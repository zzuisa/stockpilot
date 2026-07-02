"""行情采集(说明书 §8):
- 日线:每天收盘后全量补 5 天
- 分钟线:盘中每 15 分钟,只拉 watchlist;yfinance 失败时降级 Finnhub /quote
- 历史回填:首次部署拉 2 年日线供回测
"""
import logging
from datetime import datetime, timezone

import httpx
import pandas as pd
import yfinance as yf
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

import settings
from models import Price

log = logging.getLogger(__name__)


def _upsert_frame(db, symbol: str, df: pd.DataFrame, interval: str) -> int:
    """单 symbol 的 OHLCV DataFrame upsert 进 prices 表"""
    if df is None or df.empty:
        return 0
    df = df.dropna(subset=["Close"])
    n = 0
    for ts, row in df.iterrows():
        ts = pd.Timestamp(ts)
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        stmt = pg_insert(Price).values(
            symbol=symbol, interval=interval, ts=ts.to_pydatetime(),
            open=float(row["Open"]), high=float(row["High"]),
            low=float(row["Low"]), close=float(row["Close"]),
            volume=int(row.get("Volume") or 0),
        ).on_conflict_do_update(
            index_elements=["symbol", "interval", "ts"],
            set_={"open": float(row["Open"]), "high": float(row["High"]),
                  "low": float(row["Low"]), "close": float(row["Close"]),
                  "volume": int(row.get("Volume") or 0)},
        )
        db.execute(stmt)
        n += 1
    return n


def _download(symbols: list[str], yf_map: dict[str, str] | None = None,
              **kw) -> dict[str, pd.DataFrame]:
    """yf.download 多 symbol 结果拆成 {original_symbol: df}

    yf_map: {symbol → yf_ticker} 覆盖映射,用于德股等需要交易所后缀的标的。
    下载时用 yf_ticker,返回时键名还原为原始 symbol。
    """
    if not symbols:
        return {}
    yf_map = yf_map or {}
    yf_tickers = [yf_map.get(s, s) for s in symbols]
    # yf_ticker → original symbol 反向映射
    rev = {yf_map.get(s, s): s for s in symbols}

    df = yf.download(yf_tickers, group_by="ticker", auto_adjust=True,
                     threads=False, progress=False, **kw)
    if df is None or df.empty:
        return {}
    out = {}
    if len(yf_tickers) == 1:
        # group_by="ticker" 单标的时列为 MultiIndex (ticker, field)，降为扁平
        # OHLCV，否则 _upsert_frame 取 df["Close"] 会 KeyError(['Close'])
        single = df
        if isinstance(single.columns, pd.MultiIndex):
            single = single.droplevel(0, axis=1)
        out[rev[yf_tickers[0]]] = single
    else:
        for yt in yf_tickers:
            if yt in df.columns.get_level_values(0):
                out[rev[yt]] = df[yt]
    return out


def fetch_daily(symbols: list[str], db, period: str = "5d",
                yf_map: dict[str, str] | None = None) -> dict:
    frames = _download(symbols, yf_map=yf_map, period=period, interval="1d")
    counts = {s: _upsert_frame(db, s, f, "1d") for s, f in frames.items()}
    missing = [s for s in symbols if not counts.get(s)]
    if missing:
        log.warning("daily fetch missing: %s", missing)
    return {"rows": sum(counts.values()), "missing": missing}


def fetch_intraday(symbols: list[str], db,
                   yf_map: dict[str, str] | None = None) -> dict:
    frames = _download(symbols, yf_map=yf_map, period="1d", interval="5m")
    counts = {s: _upsert_frame(db, s, f, "5m") for s, f in frames.items()}
    # yfinance 缺数据的 symbol 降级 Finnhub /quote
    fallback = 0
    for s in symbols:
        if not counts.get(s) and settings.finnhub_enabled:
            fallback += _finnhub_quote(db, s)
    return {"rows": sum(counts.values()), "finnhub_fallback": fallback}


def _finnhub_quote(db, symbol: str) -> int:
    try:
        r = httpx.get("https://finnhub.io/api/v1/quote",
                      params={"symbol": symbol, "token": settings.FINNHUB_TOKEN},
                      timeout=15)
        q = r.json()
        if not q.get("c"):
            return 0
        stmt = pg_insert(Price).values(
            symbol=symbol, interval="5m",
            ts=datetime.now(timezone.utc).replace(second=0, microsecond=0),
            open=q.get("o"), high=q.get("h"), low=q.get("l"),
            close=q.get("c"), volume=0,
        ).on_conflict_do_nothing(
            index_elements=["symbol", "interval", "ts"])
        db.execute(stmt)
        return 1
    except Exception as e:
        log.warning("finnhub quote %s failed: %s", symbol, e)
        return 0


def backfill(symbols: list[str], db, period: str = "2y") -> dict:
    """首次部署:拉两年日线供回测(§8)"""
    frames = _download(symbols, period=period, interval="1d")
    counts = {s: _upsert_frame(db, s, f, "1d") for s, f in frames.items()}
    log.info("backfill done: %s", counts)
    return counts


def needs_backfill(db) -> bool:
    n = db.execute(select(func.count()).select_from(Price)
                   .where(Price.interval == "1d")).scalar() or 0
    return n < 100
