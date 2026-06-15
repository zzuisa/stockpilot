"""每日早报(说明书 §10 Workflow B + §13 模板):按 group 逐组生成。
LLM 可用时由 SiliconFlow DeepSeek 产出 Markdown;否则本地 Jinja2 模板兜底。
"""
import json
import logging
from datetime import datetime, timedelta, timezone

from jinja2 import Template
from sqlalchemy import func, select

import settings
from analysis import sentiment
from models import (AccountSnapshot, Group, IndicatorDaily, News,
                    PositionSnapshot, Signal, WatchlistItem)

log = logging.getLogger(__name__)

_TG_TEMPLATE = Template("""📋 <b>{{ g.name }}</b> · {{ date }}

① 账户: €{{ '%.0f'|format(acc.total or 0) }} · 现金 €{{ '%.0f'|format(acc.free_cash or 0) }}

② 持仓/异动
{% for s in symbols -%}
   {{ s.symbol }}  {{ '%+.1f'|format(s.price_change_pct) }}%  RSI {{ '%.0f'|format(s.rsi or 0) }}{% if s.ppl is not none %}  盈亏 €{{ '%.1f'|format(s.ppl) }}{% endif %}
{% endfor %}
③ 信号
{% if signals %}{% for sig in signals %}   • {{ sig.symbol }} {{ sig.rule }} ({{ sig.direction }})
{% endfor %}{% else %}   无
{% endif %}
④ 新闻雷达
{% for s in symbols if s.news_cnt %}   {{ s.symbol }}: 情绪 {{ '%+.1f'|format(s.news_sentiment) }} ({{ s.news_cnt }} 条)
{% else %}   无更新
{% endfor %}
⑤ 社区风向
{% for s in symbols if s.community_cnt %}   {{ s.symbol }}: {{ {'bullish':'🟢 看多','bearish':'🔴 看空','neutral':'⚪ 中性','mixed':'🟡 分歧'}[s.community_signal] }} ({{ s.community_cnt }} 帖, 均分 {{ '%+.1f'|format(s.community_sentiment) }})
{% if s.top_community_post %}   热帖: "{{ s.top_community_post[:60] }}" 👍{{ s.top_post_likes }}
{% endif %}{% else %}   无讨论
{% endfor %}""")

_EMAIL_TEMPLATE = Template("""<div style="font-family:monospace;max-width:600px;margin:0 auto">
  <h2 style="border-bottom:2px solid #E8A33D;padding-bottom:8px">
    📋 {{ g.name }} · {{ date }}
  </h2>
  <p>账户: €{{ '%.0f'|format(acc.total or 0) }} · 现金 €{{ '%.0f'|format(acc.free_cash or 0) }}</p>
  <table style="width:100%;border-collapse:collapse">
  {% for s in symbols %}
    <tr>
      <td style="color:#888;padding:4px 0">{{ s.symbol }}</td>
      <td style="text-align:right;color:{{ '#3DD68C' if s.price_change_pct >= 0 else '#E8553D' }}">
        {{ '%+.1f'|format(s.price_change_pct) }}%</td>
      <td style="padding-left:12px;color:#888">RSI {{ '%.0f'|format(s.rsi or 0) }} · 新闻 {{ '%+.1f'|format(s.news_sentiment) }} · 社区 {{ '%+.1f'|format(s.community_sentiment) }}</td>
    </tr>
  {% endfor %}
  </table>
  {% if signals %}<h3>信号</h3><p>{% for sig in signals %}{{ sig.symbol }} {{ sig.rule }} ({{ sig.direction }})<br>{% endfor %}</p>{% endif %}
  <h3>社区风向</h3>
  {% for s in symbols if s.community_cnt %}
  <p>{{ s.symbol }}: {{ s.community_signal }} · {{ s.community_cnt }} 帖 · 均分 {{ '%+.1f'|format(s.community_sentiment) }}
  {% if s.top_community_post %}<br><em>"{{ s.top_community_post[:80] }}"</em> 👍{{ s.top_post_likes }}{% endif %}</p>
  {% else %}<p>无讨论</p>{% endfor %}
  <hr>
  <p style="font-size:12px;color:#888">
    StockPilot · 本邮件由系统自动生成,不构成投资建议
  </p>
</div>""")


def _price_change_pct(db, symbol: str) -> float:
    rows = db.execute(
        select(IndicatorDaily.close).where(IndicatorDaily.symbol == symbol)
        .order_by(IndicatorDaily.ts.desc()).limit(2)).scalars().all()
    if len(rows) == 2 and rows[1]:
        return round((rows[0] - rows[1]) / rows[1] * 100, 2)
    return 0.0


def build_group_payload(db, group: Group) -> dict:
    """组装说明书 §10 Workflow B 的输入"""
    now = datetime.now(timezone.utc)
    items = db.execute(select(WatchlistItem).where(
        WatchlistItem.group_id == group.id, WatchlistItem.active)).scalars().all()
    latest_pos_ts = db.execute(select(func.max(PositionSnapshot.ts))).scalar()
    gcfg = group.config or {}

    symbols_data = []
    for w in items:
        ind = db.execute(select(IndicatorDaily)
                         .where(IndicatorDaily.symbol == w.symbol)
                         .order_by(IndicatorDaily.ts.desc()).limit(1)
                         ).scalar_one_or_none()
        agg = sentiment.symbol_aggregates(db, w.symbol)
        scfg = w.symbol_config or {}
        priority = scfg.get("community_priority",
                            gcfg.get("community_priority", "positive"))
        top = sentiment.top_community_post(db, w.symbol, priority)
        ppl = None
        if w.t212_ticker and latest_pos_ts:
            pos = db.execute(select(PositionSnapshot).where(
                PositionSnapshot.ts == latest_pos_ts,
                PositionSnapshot.ticker == w.t212_ticker)).scalar_one_or_none()
            ppl = pos.ppl if pos else None
        sigs = db.execute(select(Signal.rule).where(
            Signal.symbol == w.symbol,
            Signal.ts >= now - timedelta(hours=24))).scalars().all()
        symbols_data.append({
            "symbol": w.symbol,
            "price_change_pct": _price_change_pct(db, w.symbol),
            "rsi": ind.rsi if ind else None,
            "news_sentiment": agg["sent_avg"],
            "news_cnt": agg["news_cnt"],
            "community_sentiment": agg["comm_avg"],
            "community_cnt": agg["comm_cnt"],
            "community_signal": sentiment.community_signal_label(
                agg["comm_avg"], agg["comm_cnt"]),
            "top_community_post": (top.content or "")[:200] if top else None,
            "top_post_likes": top.likes if top else 0,
            "signals": list(sigs),
            "ppl": ppl,
        })

    acc = db.execute(select(AccountSnapshot)
                     .order_by(AccountSnapshot.ts.desc()).limit(1)
                     ).scalar_one_or_none()
    return {
        "group_id": group.id,
        "group_name": group.name,
        "symbols_data": symbols_data,
        "account_summary": {
            "total": acc.total if acc else None,
            "cash": acc.free_cash if acc else None,
            "daily_pnl": acc.ppl if acc else None,
        },
        "language": gcfg.get("language", "zh"),
    }


_REPORT_SYSTEM = (
    "你是一名专业的股票市场分析师助手。根据提供的市场数据,生成一份简洁易读的"
    "每日早报(Telegram HTML 格式,支持 <b><i> 标签)。"
    "包含账户概况、各持仓涨跌与技术指标摘要、信号提醒、新闻情绪、社区风向。"
    "语言简洁专业,控制在 400 字以内。不要有 Markdown 代码块。"
)


def _llm_report(payload: dict) -> str | None:
    """调用 SiliconFlow 生成日报文本,失败返回 None"""
    if not settings.llm_enabled:
        return None
    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=settings.SILICONFLOW_API_KEY,
            base_url=settings.SILICONFLOW_BASE_URL,
        )
        resp = client.chat.completions.create(
            model=settings.SILICONFLOW_MODEL,
            messages=[
                {"role": "system", "content": _REPORT_SYSTEM},
                {"role": "user", "content": json.dumps(
                    payload, ensure_ascii=False, default=str)},
            ],
            temperature=0.4,
            max_tokens=800,
            timeout=90,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        log.warning("llm report failed for %s: %s", payload.get("group_id"), e)
        return None


def render_group_report(db, group: Group) -> dict:
    """返回 {subject, body_md(TG HTML), body_html(email)}"""
    payload = build_group_payload(db, group)
    date = datetime.now().strftime("%Y-%m-%d %a")

    body_md = _llm_report(payload)

    ctx = {
        "g": group, "date": date,
        "acc": type("A", (), payload["account_summary"] | {
            "total": payload["account_summary"]["total"],
            "free_cash": payload["account_summary"]["cash"]})(),
        "symbols": [type("S", (), s)() for s in payload["symbols_data"]],
        "signals": [type("Sig", (), {"symbol": s["symbol"], "rule": r,
                                     "direction": ""})()
                    for s in payload["symbols_data"] for r in s["signals"]],
    }
    if not body_md:
        body_md = _TG_TEMPLATE.render(**ctx)
    body_html = _EMAIL_TEMPLATE.render(**ctx)
    return {"subject": f"StockPilot 早报 · {group.name} · {date}",
            "body_md": body_md, "body_html": body_html}


def build_all_reports(db) -> list[dict]:
    """每组一份,返回事件列表交 router 分发(event_type=daily_report)"""
    events = []
    for g in db.query(Group).order_by(Group.id).all():
        try:
            payload = render_group_report(db, g)
            events.append({"event_type": "daily_report", "symbol": None,
                           "group_id": g.id, "payload": payload})
        except Exception as e:
            log.exception("daily report for %s failed: %s", g.id, e)
    return events
