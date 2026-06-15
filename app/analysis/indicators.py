"""技术指标(说明书 §9):pandas-ta 计算 RSI(14)、MACD、SMA20/50/200、
ATR、布林带、量比,收盘后对每个 watchlist 标的执行,结果存 indicators_daily。
"""
import logging

import pandas as pd
import pandas_ta as ta
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from models import IndicatorDaily, Price

log = logging.getLogger(__name__)


def _load_daily(db, symbol: str, limit: int = 320) -> pd.DataFrame:
    rows = db.execute(
        select(Price.ts, Price.open, Price.high, Price.low,
               Price.close, Price.volume)
        .where(Price.symbol == symbol, Price.interval == "1d")
        .order_by(Price.ts.desc()).limit(limit)
    ).all()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low",
                                     "close", "volume"])
    return df.sort_values("ts").set_index("ts")


def compute_symbol(db, symbol: str) -> dict | None:
    df = _load_daily(db, symbol)
    if len(df) < 30:
        log.info("indicators: %s 数据不足(%d 根)", symbol, len(df))
        return None

    df["rsi"] = ta.rsi(df["close"], length=14)
    macd = ta.macd(df["close"])                       # MACD_12_26_9 / h / s
    if macd is not None:
        df["macd"] = macd.iloc[:, 0]
        df["macd_hist"] = macd.iloc[:, 1]
        df["macd_signal"] = macd.iloc[:, 2]
    df["sma20"] = ta.sma(df["close"], length=20)
    df["sma50"] = ta.sma(df["close"], length=50)
    df["sma200"] = ta.sma(df["close"], length=200)
    df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=14)
    bb = ta.bbands(df["close"], length=20)
    if bb is not None:
        df["bb_lower"] = bb.iloc[:, 0]
        df["bb_upper"] = bb.iloc[:, 2]
    df["vol_ratio"] = df["volume"] / df["volume"].rolling(20).mean()

    # 金叉/死叉:macd-signal 差值符号变化
    diff = (df["macd"] - df["macd_signal"]).fillna(0)
    cross = ((diff > 0) & (diff.shift(1) <= 0)).astype(int) \
        - ((diff < 0) & (diff.shift(1) >= 0)).astype(int)
    df["macd_cross"] = cross

    # 最近 5 个交易日 upsert(容忍补数)
    cols = ["close", "rsi", "macd", "macd_signal", "macd_hist", "macd_cross",
            "sma20", "sma50", "sma200", "atr", "bb_upper", "bb_lower",
            "vol_ratio"]
    last = None
    for ts, row in df.tail(5).iterrows():
        vals = {}
        for c in cols:
            v = row.get(c)
            vals[c] = None if pd.isna(v) else (
                int(v) if c == "macd_cross" else float(v))
        stmt = pg_insert(IndicatorDaily).values(
            symbol=symbol, ts=ts.date(), **vals,
        ).on_conflict_do_update(index_elements=["symbol", "ts"], set_=vals)
        db.execute(stmt)
        last = {"symbol": symbol, "ts": str(ts.date()), **vals}
    return last


def compute_all(db) -> dict:
    import config
    out = {}
    for s in config.active_symbols(db):
        try:
            r = compute_symbol(db, s["symbol"])
            if r:
                out[s["symbol"]] = r
        except Exception as e:
            log.warning("indicators %s failed: %s", s["symbol"], e)
    log.info("indicators computed for %d symbols", len(out))
    return {"symbols": len(out)}
