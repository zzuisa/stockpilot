"""新闻采集(说明书 §8):Finnhub company-news + RSS 宏观源,url 去重入库。
T212 社区帖子与外部新闻统一进 LLM 情绪打分流程(§10)。
"""
import logging
from datetime import datetime, timedelta, timezone

import feedparser
import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert

import settings
from models import News

log = logging.getLogger(__name__)


def _insert(db, **kw) -> int:
    if not kw.get("url"):
        return 0
    stmt = pg_insert(News).values(**kw).on_conflict_do_nothing(
        index_elements=["url"])
    return db.execute(stmt).rowcount or 0


def fetch_finnhub(symbols: list[str], db, days: int = 3) -> int:
    if not settings.finnhub_enabled:
        log.info("FINNHUB_TOKEN 未配置,跳过 finnhub 新闻")
        return 0
    today = datetime.now(timezone.utc).date()
    frm = (today - timedelta(days=days)).isoformat()
    n = 0
    for sym in symbols:
        try:
            r = httpx.get("https://finnhub.io/api/v1/company-news",
                          params={"symbol": sym, "from": frm,
                                  "to": today.isoformat(),
                                  "token": settings.FINNHUB_TOKEN},
                          timeout=20)
            r.raise_for_status()
            for item in (r.json() or [])[:30]:
                n += _insert(
                    db, symbol=sym, source="finnhub",
                    url=item.get("url"), title=item.get("headline"),
                    summary=(item.get("summary") or "")[:2000],
                    published=datetime.fromtimestamp(
                        item.get("datetime", 0), tz=timezone.utc),
                )
        except Exception as e:
            log.warning("finnhub news %s failed: %s", sym, e)
    return n


def fetch_rss(db) -> int:
    """宏观 RSS 源,symbol 置空"""
    n = 0
    for feed_url in settings.RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for e in feed.entries[:30]:
                published = None
                if getattr(e, "published_parsed", None):
                    published = datetime(*e.published_parsed[:6],
                                         tzinfo=timezone.utc)
                n += _insert(
                    db, symbol=None, source="rss",
                    url=getattr(e, "link", None),
                    title=getattr(e, "title", ""),
                    summary=(getattr(e, "summary", "") or "")[:2000],
                    published=published,
                )
        except Exception as ex:
            log.warning("rss %s failed: %s", feed_url, ex)
    return n


def collect_all(db) -> dict:
    import config
    symbols = [s["symbol"] for s in config.active_symbols(db)]
    fn = fetch_finnhub(symbols, db)
    rs = fetch_rss(db)
    log.info("news collected: finnhub=%d rss=%d", fn, rs)
    return {"finnhub": fn, "rss": rs}
