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


def ensure_symbol(symbol: str, yf_symbol: str | None = None) -> dict:
    """按需为单个标的补全日线 + 计算技术指标(研究模块缺数据时调用)。
    返回 {steps:[{name,status(done|failed),detail}], ready:bool}，供前端流程可视化。
    yf_symbol 缺省时优先用 watchlist 的映射，否则用 symbol 本身(美股代码通常即可)。"""
    from sqlalchemy import func
    from collectors import prices
    from db import get_session
    from models import WatchlistItem
    sym = symbol.upper()
    steps: list[dict] = []
    with get_session() as db:
        if not yf_symbol:
            w = db.query(WatchlistItem).filter(WatchlistItem.symbol == sym).first()
            yf_symbol = ((w.symbol_config or {}).get("yf_symbol") if w else None) or sym
        npx = db.execute(select(func.count()).select_from(Price)
                         .where(Price.symbol == sym,
                                Price.interval == "1d")).scalar() or 0
        if npx < 210:
            res = prices.fetch_daily([sym], db, period="1y", yf_map={sym: yf_symbol})
            got = res.get("rows", 0)
            if got <= 0 or sym in res.get("missing", []):
                steps.append({"name": "采集日线价格", "status": "failed",
                              "detail": f"yfinance 无 {yf_symbol} 数据(代码或交易所后缀有误)"})
                return {"steps": steps, "ready": False}
            steps.append({"name": "采集日线价格", "status": "done",
                          "detail": f"新增 {got} 根日线"})
        else:
            steps.append({"name": "采集日线价格", "status": "done",
                          "detail": f"已有 {npx} 根日线"})
        r = compute_symbol(db, sym)
        if r:
            steps.append({"name": "计算技术指标", "status": "done",
                          "detail": "RSI/MACD/SMA/布林带/ATR"})
            return {"steps": steps, "ready": True}
        steps.append({"name": "计算技术指标", "status": "failed",
                      "detail": "日线不足 30 根"})
        return {"steps": steps, "ready": False}


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
