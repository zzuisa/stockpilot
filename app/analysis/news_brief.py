"""单股新闻精华(高信号筛选 + 投资判断)。

按每只自选股的配置(news_types 聚焦类别、采集窗口)汇总近期新闻,交 LLM 按
prompt.md 的高信号筛选规则提炼成「精华总结 + 投资判断」,落库 news_briefs。
只把此结果推送给用户(不再推原始标题)。LLM 未配置时降级为质量分排序的事实清单。

复用:analysis.research._llm_client(LLM 客户端)、analysis.news_quality.quality_score
(质量排序)、与 analysis/brief.py 同范式(response_format=json_object)。
"""
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select

import config
import settings
from analysis.news_quality import quality_score
from models import News, NewsBrief

log = logging.getLogger(__name__)

# prompt.md 的分析师人设 + 高信号筛选规则(嵌入为常量;Docker 构建上下文为 app/,
# 仓库根 prompt.md 不在镜像内,故以此为准,settings.NEWS_PROMPT_PATH 可选覆盖)。
_SYSTEM = (
    "你是一位专业的股票新闻分析师和短线交易策略师,专门帮助交易者过滤高信号新闻。"
    "你充当高精度新闻筛选器,只保留对股价短期(未来1-14天)走势有实质影响的内容。"
    "筛选优先级(影响从高到低):①财报/业绩指引/财报电话会议 ②公司重大公告(产品/合作/"
    "并购/资本支出/订单) ③行业趋势/供需/同业竞争 ④宏观经济(利率/政策/通胀/衰退) "
    "⑤监管/法律/地缘政治/政策(贸易/出口管制/反垄断) ⑥分析师评级/目标价/机构观点 "
    "⑦供应链/运营/生产 ⑧公司治理/公司行动(股息/回购/高管变动)。"
    "保持客观中立、基于事实,高度选择性,优先时效性与直接相关性。"
    "近期无重大进展时如实说明,并指出交易者应重点监控的方向。只返回 JSON,不含其他内容。"
)

_USER_TMPL = """今天是 {today}。请对 {symbol}（股票代码：{symbol}）做短期高影响力新闻精华。
窗口：过去 {days} 天。重点关注类别：{types}。

候选新闻（已按质量预排序，JSON）：
{news_json}

请严格筛选出最多 5-8 条对短期股价有实质影响的新闻，剔除低相关/噪音，返回 JSON：
{{"included_items":[{{"category":"<上述8类之一的中文类别名>","title":"<标题>",
"source":"<来源>","date":"<日期/时间>","summary":"<2-4句事实总结>",
"impact":"<利好|利空|中性，含可能幅度与传导逻辑>",
"nuance":"<重要背景或注意事项，如利好出尽/已定价/估值压力/不对称机会>",
"importance":"<高|中>","url":"<原文url，必须取自候选列表>"}}],
"overall_sentiment":"<bullish|bearish|neutral>",
"investment_judgment":"<2-4句明确的短期投资判断：方向、关注点、风险/机会>",
"watch_points":"<未来几天需重点关注的潜在事件或数据，一句话>",
"info_gaps":"<当前明显的信息空白或建议进一步跟踪的方向，可为空>"}}

规则：included_items 按重要性从高到低；若候选中无实质高影响新闻，included_items 返回空数组，
并在 investment_judgment 说明当前应监控的方向。全部用中文。"""

_SENT_LABEL = {"bullish": "偏多 🟢", "bearish": "偏空 🔴", "neutral": "中性 ⚪"}


def _system_prompt() -> str:
    """优先用外部覆盖文件(settings.NEWS_PROMPT_PATH),否则用嵌入常量。"""
    path = getattr(settings, "NEWS_PROMPT_PATH", "")
    if path:
        try:
            with open(path, encoding="utf-8") as f:
                txt = f.read().strip()
            if txt:
                return txt + "\n\n只返回 JSON,不含其他内容。"
        except Exception as e:
            log.debug("NEWS_PROMPT_PATH 读取失败,改用内置提示词: %s", e)
    return _SYSTEM


def _recent_news(db, symbol: str, since) -> list[News]:
    """该股近窗口内新闻,按质量分降序取 top 25(控制单次提示词体量)。"""
    rows = db.execute(
        select(News).where(
            News.symbol == symbol,
            or_(News.published >= since, News.fetched_at >= since),
        )
    ).scalars().all()
    rows.sort(
        key=lambda n: quality_score(n.source_tier, n.relevance, n.published),
        reverse=True,
    )
    return rows[:25]


def _llm_brief(symbol: str, news: list[News], news_types: list[str],
               days: int) -> tuple[dict | None, int]:
    """调用 LLM 做高信号筛选 + 投资判断,返回 (结果dict|None, token数)。"""
    type_labels = "、".join(config.NEWS_TYPE_LABELS.get(t, t)
                            for t in (news_types or config.NEWS_TYPES))
    news_json = json.dumps(
        [{"url": n.url, "title": n.title,
          "summary": (n.summary or "")[:300],
          "source": n.source_name or n.source,
          "tier": n.source_tier,
          "date": n.published.isoformat() if n.published else None}
         for n in news],
        ensure_ascii=False,
    )
    prompt = _USER_TMPL.format(
        today=datetime.now(timezone.utc).strftime("%Y年%m月%d日"),
        symbol=symbol, days=days, types=type_labels, news_json=news_json,
    )
    try:
        from analysis.research import _llm_client
        client = _llm_client()
        resp = client.chat.completions.create(
            model=settings.SILICONFLOW_MODEL,
            messages=[{"role": "system", "content": _system_prompt()},
                      {"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.2, max_tokens=2500, timeout=120,
        )
        text = resp.choices[0].message.content or "{}"
        tokens = int(getattr(resp.usage, "total_tokens", 0) or 0)
        return json.loads(text), tokens
    except Exception as e:
        log.warning("news_brief llm failed %s: %s", symbol, e)
        return None, 0


def _fallback_brief(news: list[News]) -> dict:
    """LLM 未配置/失败时:用质量分取前几条做事实清单,标注未走 LLM。"""
    top = news[:6]
    items = [{
        "category": "未分类", "title": n.title or "(无标题)",
        "source": n.source_name or n.source,
        "date": n.published.isoformat() if n.published else "",
        "summary": (n.summary or "")[:160],
        "impact": "中性", "nuance": "", "importance": "中", "url": n.url,
    } for n in top]
    return {
        "included_items": items, "overall_sentiment": "neutral",
        "investment_judgment": "（未走 LLM，仅按来源质量列出近期要闻，请人工研判。）",
        "watch_points": "", "info_gaps": "",
    }


def _render_md(symbol: str, data: dict) -> str:
    """精华结果 → Telegram/邮件可读的 markdown 文案。"""
    items = data.get("included_items") or []
    sent = data.get("overall_sentiment") or "neutral"
    lines = [f"📰 <b>{symbol} 新闻精华</b> · 短期情绪 {_SENT_LABEL.get(sent, sent)}"]
    judg = (data.get("investment_judgment") or "").strip()
    if judg:
        lines.append(f"\n<b>投资判断：</b>{judg}")
    if items:
        lines.append("\n<b>高影响力新闻：</b>")
        for i, it in enumerate(items[:8], 1):
            imp = it.get("importance") or ""
            mark = "🔴" if "高" in imp else "🟡"
            head = f"{mark} {i}. {it.get('title') or '(无标题)'}"
            meta = " · ".join(x for x in (it.get("source"), it.get("date")) if x)
            lines.append(head + (f"\n   <i>{meta}</i>" if meta else ""))
            if it.get("impact"):
                lines.append(f"   影响：{it['impact']}")
            if it.get("nuance"):
                lines.append(f"   注意：{it['nuance']}")
    else:
        lines.append("\n近期无实质高影响新闻。")
    wp = (data.get("watch_points") or "").strip()
    if wp:
        lines.append(f"\n<b>后续关注：</b>{wp}")
    gaps = (data.get("info_gaps") or "").strip()
    if gaps:
        lines.append(f"<b>信息空白：</b>{gaps}")
    return "\n".join(lines)


def build_symbol_brief(db, sym_cfg: dict, window_hours: int = 48) -> dict | None:
    """为单只股票生成新闻精华并落库 news_briefs。
    sym_cfg 来自 config.news_symbols 的元素(含 symbol/news_types)。
    无近期新闻则返回 None(不落库、不推送)。"""
    symbol = sym_cfg["symbol"]
    since = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    news = _recent_news(db, symbol, since)
    if not news:
        return None

    days = max(1, round(window_hours / 24))
    if settings.llm_enabled:
        data, tokens = _llm_brief(symbol, news, sym_cfg.get("news_types") or [], days)
        if data is None:
            data, tokens = _fallback_brief(news), 0
    else:
        data, tokens = _fallback_brief(news), 0

    items = data.get("included_items") or []
    body_md = _render_md(symbol, data)
    brief = NewsBrief(
        symbol=symbol, window_hours=window_hours,
        headline=(items[0]["title"] if items else
                  (data.get("investment_judgment") or "")[:200]),
        sentiment=data.get("overall_sentiment") or "neutral",
        judgment=data.get("investment_judgment") or "",
        summary_md=body_md,
        watch_points=data.get("watch_points") or "",
        item_count=len(items),
        news_ids=[n.id for n in news],
        tokens=tokens, pushed=False,
    )
    db.add(brief)
    db.flush()
    return {
        "id": brief.id, "symbol": symbol, "item_count": len(items),
        "sentiment": brief.sentiment, "judgment": brief.judgment,
        "body_md": body_md, "tokens": tokens,
        "groups": sym_cfg.get("groups") or [],
    }
