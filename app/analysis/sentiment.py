"""LLM 情绪分析(说明书 §10 Workflow A):新闻 + 社区统一打分,写回
news.sentiment / t212_community.sentiment。SiliconFlow 未配置时退化为词典打分,
保证流水线可跑(打分项 llm_reason 标记 'fallback')。
"""
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

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
    "请对以下 {symbol} 相关内容进行情绪评分,返回 JSON:\n\n"
    "新闻列表:\n{news_json}\n\n"
    "社区帖列表:\n{posts_json}\n\n"
    "返回格式:\n"
    '{{"news_items":[{{"url":"...","score":<int>,"reason":"<15字内>"}}],'
    '"community_items":[{{"post_id":"...","score":<int>,"reason":"<15字内>"}}]}}'
)


def _llm_client():
    """懒加载 openai 客户端,仅在 llm_enabled 时调用"""
    from openai import OpenAI
    return OpenAI(
        api_key=settings.SILICONFLOW_API_KEY,
        base_url=settings.SILICONFLOW_BASE_URL,
    )


def _llm_score(symbol: str, news: list, posts: list) -> dict | None:
    """调用 SiliconFlow 批量打分,返回 {"news_items":[], "community_items":[]}"""
    news_json = json.dumps(
        [{"url": n.url, "title": n.title,
          "summary": (n.summary or "")[:400]} for n in news],
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
            max_tokens=1024,
            timeout=60,
        )
        text = resp.choices[0].message.content or ""
        return json.loads(text)
    except Exception as e:
        log.warning("llm score failed for %s: %s", symbol, e)
        return None


# ─── 词典 fallback(无 LLM 时维持流水线) ───
_POS = ("beat", "beats", "surge", "record", "upgrade", "rally", "growth",
        "strong", "bullish", "outperform", "buy", "上调", "超预期", "增长")
_NEG = ("miss", "lawsuit", "probe", "downgrade", "ban", "restriction",
        "recall", "bearish", "sell", "fraud", "warning", "下调", "调查", "禁令")


def _fallback_score(text: str) -> int:
    t = (text or "").lower()
    score = sum(w in t for w in _POS) - sum(w in t for w in _NEG)
    return max(-2, min(2, score))


def run_sentiment_batch(db) -> dict:
    """对 3 天内未打分的新闻+社区帖,按 symbol 分批送 LLM 打分"""
    import config
    since = datetime.now(timezone.utc) - timedelta(days=3)
    scored_news = scored_posts = 0

    for s in config.active_symbols(db):
        sym = s["symbol"]
        news = db.execute(select(News).where(
            News.symbol == sym, News.sentiment.is_(None),
            News.fetched_at >= since)).scalars().all()
        posts = db.execute(select(T212CommunityPost).where(
            T212CommunityPost.symbol == sym,
            T212CommunityPost.sentiment.is_(None),
            T212CommunityPost.fetched_at >= since)).scalars().all()
        if not news and not posts:
            continue

        result = None
        if settings.llm_enabled and (news or posts):
            result = _llm_score(sym, news, posts)

        if result:
            by_url = {i.get("url"): i for i in result.get("news_items", [])}
            for n in news:
                item = by_url.get(n.url)
                if item is not None:
                    n.sentiment = max(-2, min(2, int(item.get("score", 0))))
                    n.llm_reason = item.get("reason", "")
                    scored_news += 1
            by_pid = {str(i.get("post_id")): i
                      for i in result.get("community_items", [])}
            for p in posts:
                item = by_pid.get(str(p.post_id))
                if item is not None:
                    p.sentiment = max(-2, min(2, int(item.get("score", 0))))
                    p.llm_summary = item.get("reason", "")
                    scored_posts += 1
        else:
            for n in news:
                n.sentiment = _fallback_score(f"{n.title} {n.summary}")
                n.llm_reason = "fallback"
                scored_news += 1
            for p in posts:
                p.sentiment = _fallback_score(p.content)
                p.llm_summary = "fallback"
                scored_posts += 1
        db.flush()

    log.info("sentiment scored: news=%d community=%d (llm=%s)",
             scored_news, scored_posts, settings.llm_enabled)
    return {"news": scored_news, "community": scored_posts,
            "llm": settings.llm_enabled}


# ─── 信号引擎 / 日报用的聚合 ───

def symbol_aggregates(db, symbol: str, days: int = 3) -> dict:
    """近 N 天:新闻均分/条数、社区正向帖数/均分"""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    news = db.execute(select(News.sentiment).where(
        News.symbol == symbol, News.published >= since,
        News.sentiment.isnot(None))).scalars().all()
    posts = db.execute(select(T212CommunityPost.sentiment).where(
        T212CommunityPost.symbol == symbol,
        T212CommunityPost.published >= since,
        T212CommunityPost.sentiment.isnot(None))).scalars().all()
    sent_avg = sum(news) / len(news) if news else 0.0
    comm_avg = sum(posts) / len(posts) if posts else 0.0
    return {
        "sent_avg": round(sent_avg, 2),
        "news_cnt": len(news),
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
