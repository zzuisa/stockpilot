"""新闻采集(说明书 §8)：Finnhub company-news + 优质 RSS + Alpha Vantage 新闻情绪，
url 去重入库。入库即评估来源质量(source_name/source_tier/relevance)，供推送过滤、
详情展示、情绪加权使用。情绪分仍由统一 LLM 流程(§10)填充。
"""
import logging
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import feedparser
import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert

import settings
from analysis.news_quality import classify_source
from models import News

log = logging.getLogger(__name__)


def _insert(db, *, source_name=None, relevance=None, **kw) -> int:
    if not kw.get("url"):
        return 0
    kw["source_name"] = source_name
    kw["source_tier"] = classify_source(source_name)
    kw["relevance"] = relevance
    stmt = pg_insert(News).values(**kw).on_conflict_do_nothing(
        index_elements=["url"])
    # ON CONFLICT DO NOTHING 跳过时 rowcount 可能为 -1，clamp 成 0/1 计数
    return max(0, db.execute(stmt).rowcount or 0)


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
                    source_name=item.get("source"),
                    relevance=0.7,                       # company-news 已按标的检索
                    url=item.get("url"), title=item.get("headline"),
                    summary=(item.get("summary") or "")[:2000],
                    published=datetime.fromtimestamp(
                        item.get("datetime", 0), tz=timezone.utc),
                )
        except Exception as e:
            log.warning("finnhub news %s failed: %s", sym, e)
    return n


def fetch_rss(db) -> int:
    """宏观 RSS 源,symbol 置空。source_name 取 feed 频道标题(用于分级)。"""
    n = 0
    for feed_url in settings.RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            feed_name = (getattr(feed.feed, "title", None)
                         or urlparse(feed_url).netloc)
            for e in feed.entries[:30]:
                published = None
                if getattr(e, "published_parsed", None):
                    published = datetime(*e.published_parsed[:6],
                                         tzinfo=timezone.utc)
                n += _insert(
                    db, symbol=None, source="rss",
                    source_name=feed_name,
                    relevance=0.4,                       # 宏观面，相关度较低
                    url=getattr(e, "link", None),
                    title=getattr(e, "title", ""),
                    summary=(getattr(e, "summary", "") or "")[:2000],
                    published=published,
                )
        except Exception as ex:
            log.warning("rss %s failed: %s", feed_url, ex)
    return n


def _parse_av_ts(v):
    try:
        return datetime.strptime(str(v), "%Y%m%dT%H%M%S").replace(
            tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def fetch_alphavantage(symbols: list[str], db, limit: int = 50,
                       max_tickers: int = 12) -> int:
    """Alpha Vantage NEWS_SENTIMENT：专业源 + 内置相关度/情绪。免费档 25 req/day。

    **逐 ticker 查询**：AV 多 ticker 批量时只要含一个它不认识的代码(如欧股 VUAA、
    带数字的 2DG)就整批返回空，故按单 ticker 查询，互不影响。过滤明显非美股代码、
    节流 1.5s/次、命中限频提示即停，控制在免费配额内。情绪仍交 LLM 统一打分。"""
    if not settings.alphavantage_enabled or not symbols:
        return 0
    # AV 仅覆盖美股，过滤掉带数字/过长的代码(欧股 2DG/2DGD 等)
    us = [s.upper() for s in symbols if s.isalpha() and len(s) <= 5][:max_tickers]
    n = 0
    for i, sym in enumerate(us):
        if i:
            time.sleep(1.5)            # 遵守 1 req/s 突发限制
        try:
            r = httpx.get("https://www.alphavantage.co/query", params={
                "function": "NEWS_SENTIMENT", "tickers": sym,
                "limit": limit, "sort": "LATEST",
                "apikey": settings.ALPHAVANTAGE_API_KEY,
            }, timeout=25)
            r.raise_for_status()
            data = r.json() or {}
        except Exception as e:
            log.warning("alphavantage %s failed: %s", sym, e)
            continue
        feed = data.get("feed")
        if not isinstance(feed, list):
            # 限频/配额耗尽返回 {"Information"/"Note": ...}，停止本轮
            log.info("alphavantage 限频/无数据，停止: %s", str(data)[:120])
            break
        for art in feed:
            rel = 0.0
            for ts in (art.get("ticker_sentiment") or []):
                if (ts.get("ticker") or "").upper() == sym:
                    try:
                        rel = float(ts.get("relevance_score") or 0)
                    except (ValueError, TypeError):
                        rel = 0.0
                    break
            n += _insert(
                db, symbol=sym, source="alphavantage",
                source_name=art.get("source"),
                relevance=round(max(0.0, min(1.0, rel)), 3),
                url=art.get("url"), title=art.get("title"),
                summary=(art.get("summary") or "")[:2000],
                published=_parse_av_ts(art.get("time_published")),
            )
    return n


def collect_all(db) -> dict:
    """仅为开启了 news_auto 的标的、且仅其各自配置的来源采集新闻(不再无差别全量)。
    RSS 宏观源由 defaults.rss_macro 全局开关控制(默认开)。"""
    import config
    syms = config.news_symbols(db)
    # 按来源分桶:只把该来源出现在标的 news_sources 里的标的纳入对应来源
    finnhub_syms = [s["symbol"] for s in syms if "finnhub" in s["news_sources"]]
    av_syms = [s["symbol"] for s in syms if "alphavantage" in s["news_sources"]]

    fn = fetch_finnhub(finnhub_syms, db) if finnhub_syms else 0
    av = fetch_alphavantage(av_syms, db) if av_syms else 0
    rs = fetch_rss(db) if config.rss_macro_enabled(db) else 0
    log.info("news collected: symbols=%d finnhub=%d rss=%d alphavantage=%d",
             len(syms), fn, rs, av)
    return {"finnhub": fn, "rss": rs, "alphavantage": av, "symbols": len(syms)}
