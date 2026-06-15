"""★ T212 社区帖子采集(说明书 §7)
Trading212 社区论坛是 Discourse 驱动,公开 JSON API,无需认证。
"""
import logging
import re
import time
from datetime import datetime, timedelta

import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert

from models import T212CommunityPost

log = logging.getLogger(__name__)

COMMUNITY_BASE = "https://community.trading212.com"
HIGH_LIKES = 5      # 高赞帖无论情绪都保留(代表社区关注度)


class T212Community:
    """抓取 T212 Discourse 论坛的公开帖子(无需认证)"""

    def __init__(self):
        self.session = httpx.Client(timeout=30, headers={
            "Accept": "application/json",
            "User-Agent": "StockPilot/1.0 (personal research)",
        }, follow_redirects=True)
        self._last_call = 0.0

    def _throttle(self, interval=2.0):
        wait = self._last_call + interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def search_symbol(self, symbol: str, days: int = 3) -> list[dict]:
        """搜索最近 N 天包含该 symbol 关键词的帖子"""
        self._throttle()
        try:
            r = self.session.get(f"{COMMUNITY_BASE}/search.json", params={
                "q": f"{symbol} after:{_days_ago(days)}",
                "order": "latest",
            })
        except httpx.HTTPError as e:
            log.warning("community search %s failed: %s", symbol, e)
            return []
        if r.status_code != 200:
            return []
        data = r.json()
        posts = []
        for post in data.get("posts", []):
            posts.append({
                "topic_id": post["topic_id"],
                "post_id": post["id"],
                "author": post.get("username", ""),
                "content": _strip_html(post.get("blurb", "")),
                "published": post.get("created_at"),
                "likes": post.get("like_count", 0),
            })
        return posts

    def get_topic_posts(self, topic_id: int, limit: int = 20) -> list[dict]:
        """获取某个话题下的回复(用于深挖热门讨论)"""
        self._throttle()
        try:
            r = self.session.get(f"{COMMUNITY_BASE}/t/{topic_id}.json")
        except httpx.HTTPError as e:
            log.warning("community topic %s failed: %s", topic_id, e)
            return []
        if r.status_code != 200:
            return []
        topic = r.json()
        posts = []
        for p in topic.get("post_stream", {}).get("posts", [])[:limit]:
            posts.append({
                "topic_id": topic_id,
                "post_id": p["id"],
                "author": p.get("username", ""),
                "content": _strip_html(p.get("cooked", "")),
                "published": p.get("created_at"),
                "likes": p.get("like_count", 0),
            })
        return posts


def _days_ago(n: int) -> str:
    return (datetime.utcnow() - timedelta(days=n)).strftime("%Y-%m-%d")


def _strip_html(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html or "").strip()


def _parse_ts(v):
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def collect_for_watchlist(db) -> dict:
    """按 watchlist 逐 symbol 搜索,post_id 去重入库(每天收盘后跑一次)"""
    import config
    symbols = [s for s in config.active_symbols(db) if s["t212_community"]]
    if not symbols:
        return {"symbols": 0, "posts": 0}
    cli = T212Community()
    total = 0
    for s in symbols:
        for p in cli.search_symbol(s["symbol"]):
            stmt = pg_insert(T212CommunityPost).values(
                topic_id=p["topic_id"],
                post_id=p["post_id"],
                symbol=s["symbol"],
                author=p["author"],
                content=p["content"],
                published=_parse_ts(p["published"]),
                likes=p["likes"] or 0,
            ).on_conflict_do_nothing(index_elements=["post_id"])
            res = db.execute(stmt)
            total += res.rowcount or 0
    log.info("community collected: %d new posts for %d symbols",
             total, len(symbols))
    return {"symbols": len(symbols), "posts": total}
