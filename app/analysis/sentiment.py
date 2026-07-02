"""LLM 情绪分析(说明书 §10 Workflow A):新闻 + 社区统一打分,写回
news.sentiment / t212_community.sentiment。SiliconFlow 未配置时退化为词典打分,
保证流水线可跑(打分项 llm_reason 标记 'fallback')。
"""
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select

import settings
from models import News, T212CommunityPost

log = logging.getLogger(__name__)

# ─── SiliconFlow LLM 客户端 ───

_SCORE_SYSTEM = (
    "你是一个专业的股票市场情绪分析师。"
    "你的任务是对给定的新闻和社区帖子进行情绪评分。"
    "评分范围: -2(极度负面) 到 +2(极度正面), 0 为中性。"
    "只返回 JSON,不要有任何其他内容。"
)

_SCORE_USER_TMPL = (
    "请对以下 {symbol} 相关内容进行情绪评分与解读,返回 JSON:\n\n"
    "新闻列表:\n{news_json}\n\n"
    "社区帖列表:\n{posts_json}\n\n"
    "要求:score 为 -2~+2 整数;reason 为对该条对 {symbol} 影响的分析解读"
    "(2~3 句、约 60~140 字),需说明:① 核心事件/催化是什么;② 对 {symbol} 是利好还是利空"
    "及其传导逻辑;③ 影响程度或时间维度(短期/长期、重大/有限)。不要复述标题。\n"
    "返回格式:\n"
    '{{"news_items":[{{"url":"...","score":<int>,"reason":"<解读>"}}],'
    '"community_items":[{{"post_id":"...","score":<int>,"reason":"<解读>"}}]}}'
)


def _llm_client():
    """懒加载 openai 客户端,仅在 llm_enabled 时调用"""
    from openai import OpenAI
    return OpenAI(
        api_key=settings.SILICONFLOW_API_KEY,
        base_url=settings.SILICONFLOW_BASE_URL,
        max_retries=0,   # LLM 慢/超时时失败快返，避免重试叠加拖垮工作器
    )


def _llm_score(symbol: str, news: list, posts: list) -> tuple[dict | None, int]:
    """调用 SiliconFlow 批量打分,返回 (结果dict|None, 本次消耗token数)。"""
    news_json = json.dumps(
        [{"url": n.url, "title": n.title,
          "summary": (n.summary or "")[:200]} for n in news],
        ensure_ascii=False,
    )
    posts_json = json.dumps(
        [{"post_id": p.post_id, "author": p.author,
          "content": (p.content or "")[:400],
          "likes": p.likes} for p in posts],
        ensure_ascii=False,
    )
    prompt = _SCORE_USER_TMPL.format(
        symbol=symbol, news_json=news_json, posts_json=posts_json
    )
    try:
        client = _llm_client()
        resp = client.chat.completions.create(
            model=settings.SILICONFLOW_MODEL,
            messages=[
                {"role": "system", "content": _SCORE_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=4096,        # 解读加长(60~140字/条)，留足空间避免截断
            timeout=120,
        )
        text = resp.choices[0].message.content or ""
        tokens = int(getattr(resp.usage, "total_tokens", 0) or 0)
        return json.loads(text), tokens
    except Exception as e:
        log.warning("llm score failed for %s: %s", symbol, e)
        return None, 0


# ─── 词典 fallback(无 LLM 时维持流水线) ───
_POS = ("beat", "beats", "surge", "record", "upgrade", "rally", "growth",
        "strong", "bullish", "outperform", "buy", "上调", "超预期", "增长")
_NEG = ("miss", "lawsuit", "probe", "downgrade", "ban", "restriction",
        "recall", "bearish", "sell", "fraud", "warning", "下调", "调查", "禁令")


def _fallback_score(text: str) -> int:
    t = (text or "").lower()
    score = sum(w in t for w in _POS) - sum(w in t for w in _NEG)
    return max(-2, min(2, score))


def run_sentiment_batch(on_progress=None, on_tick=None) -> dict:
    """对 3 天内未打分(或曾 LLM 失败回退)的新闻+社区帖,按 symbol 分块送 LLM
    打分 + 解读。每标的用独立短会话(及时提交+释放连接,避免长时占用连接池)。
    on_progress(text): 可选进度回调(实时进度)。
    on_tick(scored, tokens): 可选每标的完成回调(供工作器实时累计 token)。"""
    import config
    from db import get_session
    since = datetime.now(timezone.utc) - timedelta(days=3)
    scored_news = scored_posts = tokens = 0

    with get_session() as s0:
        syms = config.active_symbols(s0)

    for idx, sd in enumerate(syms, 1):
        sym = sd["symbol"]
        if on_progress:
            on_progress(f"LLM 打分 {sym} ({idx}/{len(syms)})")
        try:
            with get_session() as db:   # 每标的独立短会话
                # 逐条新闻打分只覆盖开启 news_auto 的标的(未开启的不再无差别打分)；
                # 每轮每标的只处理最近 12 条(1 次 LLM 调用)，控制单轮时长；
                # 较旧的未打分项由后续轮次增量覆盖。优先最新(用户最关心)。
                news = db.execute(select(News).where(
                    News.symbol == sym, News.fetched_at >= since,
                    or_(News.sentiment.is_(None),
                        News.llm_reason == "fallback"))
                    .order_by(News.published.desc().nullslast())
                    .limit(12)).scalars().all() if sd.get("news_auto") else []
                posts = db.execute(select(T212CommunityPost).where(
                    T212CommunityPost.symbol == sym,
                    T212CommunityPost.fetched_at >= since,
                    or_(T212CommunityPost.sentiment.is_(None),
                        T212CommunityPost.llm_summary == "fallback"))
                    .order_by(T212CommunityPost.likes.desc())
                    .limit(12)).scalars().all()
                if not news and not posts:
                    continue
                sn, sp, tk = _score_symbol(db, sym, news, posts)
                scored_news += sn
                scored_posts += sp
                tokens += tk
                if on_tick and (sn or sp or tk):
                    on_tick(sn + sp, tk)
        except Exception as e:
            log.warning("sentiment %s failed: %s", sym, e)

    log.info("sentiment scored: news=%d community=%d tokens=%d (llm=%s)",
             scored_news, scored_posts, tokens, settings.llm_enabled)
    return {"news": scored_news, "community": scored_posts,
            "tokens": tokens, "llm": settings.llm_enabled}


# ─── 持续 LLM 工作器(常驻循环,实时进度 + token 统计,不处理已分析项) ───

LLM_STATUS: dict = {
    "enabled": False,
    "running": False,
    "phase": "idle",            # idle | scoring | sleeping
    "progress": "",
    "interval": 15,
    "scored_total": 0,          # 本次进程启动以来累计打分条数
    "tokens_total": 0,          # 累计消耗 token
    "tokens_last": 0,           # 最近一轮 token
    "scored_last": 0,           # 最近一轮打分条数
    "calls": 0,                 # 已完成轮次
    "last_batch_at": None,
    "started_at": None,
    "error": None,
    "model": None,
}


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


async def news_llm_loop(interval: int = 15):
    """常驻协程：持续扫描未打分新闻/社区帖 → LLM 打分(已打分项天然跳过,不重复)。
    实时把进度/ token 写入 LLM_STATUS,供 News LLM 页展示。"""
    import asyncio
    LLM_STATUS.update(enabled=settings.llm_enabled, running=True,
                      interval=interval, started_at=_now_iso(),
                      model=settings.SILICONFLOW_MODEL)
    log.info("news LLM 持续工作器启动 (interval=%ss, llm=%s)",
             interval, settings.llm_enabled)

    def _cb(text: str):
        LLM_STATUS["progress"] = text

    _pass = {"n": 0, "tk": 0}

    def _tick(scored: int, tokens: int):
        # 每标的完成即实时累计 token / 打分数(无需等整轮结束)
        _pass["n"] += scored
        _pass["tk"] += tokens
        LLM_STATUS["scored_total"] += scored
        LLM_STATUS["tokens_total"] += tokens
        if tokens:
            LLM_STATUS["tokens_last"] = tokens
        LLM_STATUS["last_batch_at"] = _now_iso()

    while True:
        try:
            LLM_STATUS["phase"] = "scoring"
            _pass["n"] = _pass["tk"] = 0
            await asyncio.to_thread(run_sentiment_batch, _cb, _tick)
            n, tok = _pass["n"], _pass["tk"]
            LLM_STATUS["scored_last"] = n
            LLM_STATUS["calls"] += 1
            LLM_STATUS["error"] = None
            if n:
                LLM_STATUS["tokens_last"] = tok
                log.info("news LLM 轮次完成: 打分 %d 条, token %d", n, tok)
        except asyncio.CancelledError:
            LLM_STATUS.update(running=False, phase="idle", progress="已停止")
            raise
        except Exception as e:
            LLM_STATUS["error"] = str(e)
            log.warning("news LLM 工作器异常: %s", e)
        LLM_STATUS["phase"] = "sleeping"
        LLM_STATUS["progress"] = (
            "本轮无待分析项，等待新数据…" if not LLM_STATUS["scored_last"]
            else f"本轮打分 {LLM_STATUS['scored_last']} 条，休眠 {interval}s…")
        await asyncio.sleep(interval)


def _score_symbol(db, sym, news, posts) -> tuple[int, int, int]:
    """单标的：分块送 LLM(每块 ≤CHUNK 条，避免解读过长被 max_tokens 截断；
    单块失败只回退该块)。每块 flush，便于增量落库。
    返回 (打分新闻数, 打分帖子数, 消耗token数)。"""
    CHUNK = 8       # 解读加长后单块条数下调，降低截断风险
    scored_news = scored_posts = tokens = 0
    nchunks = [news[k:k + CHUNK] for k in range(0, len(news), CHUNK)]
    pchunks = [posts[k:k + CHUNK] for k in range(0, len(posts), CHUNK)]
    for r in range(max(len(nchunks), len(pchunks))):
        nb = nchunks[r] if r < len(nchunks) else []
        pb = pchunks[r] if r < len(pchunks) else []
        if not nb and not pb:
            continue
        result, tok = _llm_score(sym, nb, pb) if settings.llm_enabled else (None, 0)
        tokens += tok
        if result:
            by_url = {i.get("url"): i for i in result.get("news_items", [])}
            for n in nb:
                item = by_url.get(n.url)
                if item is not None:
                    n.sentiment = max(-2, min(2, int(item.get("score", 0))))
                    n.llm_reason = item.get("reason", "")
                    scored_news += 1
            by_pid = {str(i.get("post_id")): i
                      for i in result.get("community_items", [])}
            for p in pb:
                item = by_pid.get(str(p.post_id))
                if item is not None:
                    p.sentiment = max(-2, min(2, int(item.get("score", 0))))
                    p.llm_summary = item.get("reason", "")
                    scored_posts += 1
        else:
            for n in nb:
                n.sentiment = _fallback_score(f"{n.title} {n.summary}")
                n.llm_reason = "fallback"
                scored_news += 1
            for p in pb:
                p.sentiment = _fallback_score(p.content)
                p.llm_summary = "fallback"
                scored_posts += 1
        db.flush()
    return scored_news, scored_posts, tokens


# ─── 信号引擎 / 日报用的聚合 ───

def symbol_aggregates(db, symbol: str, days: int = 3) -> dict:
    """近 N 天:新闻【质量加权】均分/条数、社区正向帖数/均分。
    一线高相关来源的情绪权重更大，弱化 PR 通稿/低相关噪音对情绪的影响。"""
    from analysis.news_quality import quality_weight
    since = datetime.now(timezone.utc) - timedelta(days=days)
    news = db.execute(select(News.sentiment, News.source_tier, News.relevance)
                      .where(News.symbol == symbol, News.published >= since,
                             News.sentiment.isnot(None))).all()
    posts = db.execute(select(T212CommunityPost.sentiment).where(
        T212CommunityPost.symbol == symbol,
        T212CommunityPost.published >= since,
        T212CommunityPost.sentiment.isnot(None))).scalars().all()

    if news:
        wsum = sum(quality_weight(t, r) for _, t, r in news)
        sent_avg = (sum(s * quality_weight(t, r) for s, t, r in news) / wsum
                    if wsum > 0 else sum(s for s, _, _ in news) / len(news))
        # 平均来源质量(1 一线占比越高越接近 1)
        tiers = [t for _, t, _ in news if t]
        tier_avg = round(sum(tiers) / len(tiers), 2) if tiers else None
    else:
        sent_avg, tier_avg = 0.0, None
    comm_avg = sum(posts) / len(posts) if posts else 0.0
    return {
        "sent_avg": round(sent_avg, 2),
        "news_cnt": len(news),
        "news_tier_avg": tier_avg,
        "comm_avg": round(comm_avg, 2),
        "comm_cnt": len(posts),
        "comm_pos_cnt": sum(1 for p in posts if p >= 1),
        "comm_neg_cnt": sum(1 for p in posts if p <= -1),
    }


def community_signal_label(comm_avg: float, comm_cnt: int) -> str:
    if comm_cnt == 0:
        return "neutral"
    if comm_avg >= 0.5:
        return "bullish"
    if comm_avg <= -0.5:
        return "bearish"
    return "mixed" if comm_cnt >= 3 else "neutral"


def top_community_post(db, symbol: str, priority: str = "positive",
                       days: int = 3):
    """日报『社区风向』选帖:按 community_priority 过滤,高赞优先(§7 采集策略)"""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    q = select(T212CommunityPost).where(
        T212CommunityPost.symbol == symbol,
        T212CommunityPost.published >= since)
    posts = db.execute(q).scalars().all()
    if priority == "positive":
        kept = [p for p in posts if (p.sentiment or 0) >= 1
                or (p.likes or 0) >= 5]
    elif priority == "negative":
        kept = [p for p in posts if (p.sentiment or 0) <= -1
                or (p.likes or 0) >= 5]
    else:
        kept = posts
    kept.sort(key=lambda p: (p.likes or 0), reverse=True)
    return kept[0] if kept else None
