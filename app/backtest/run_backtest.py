"""回测(说明书 §18,vectorbt)。在 app 容器内运行:

    microk8s kubectl exec -it deploy/stockpilot-app -n stockpilot -- \
        python -m backtest.run_backtest NVDA --strategy rsi

策略先回测、再模拟盘(demo)、最后小仓位实盘(§0 原则)。
"""
import argparse
import sys

import pandas as pd
from sqlalchemy import select


def load_close(symbol: str) -> pd.Series:
    from db import get_session
    from models import Price
    with get_session() as s:
        rows = s.execute(
            select(Price.ts, Price.close)
            .where(Price.symbol == symbol, Price.interval == "1d")
            .order_by(Price.ts)).all()
    if len(rows) < 200:
        sys.exit(f"{symbol} 日线不足 200 根({len(rows)}),"
                 "先跑 POST /api/v1/jobs/backfill/run")
    df = pd.DataFrame(rows, columns=["ts", "close"]).set_index("ts")
    return df["close"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol")
    ap.add_argument("--strategy", default="rsi", choices=["rsi", "macd"])
    ap.add_argument("--cash", type=float, default=1000)
    args = ap.parse_args()

    import vectorbt as vbt   # 重依赖,只在回测时加载

    close = load_close(args.symbol.upper())

    if args.strategy == "rsi":
        rsi = vbt.RSI.run(close, window=14).rsi
        entries = rsi < 30
        exits = rsi > 55
    else:
        macd = vbt.MACD.run(close)
        entries = macd.macd_crossed_above(macd.signal)
        exits = macd.macd_crossed_below(macd.signal)

    pf = vbt.Portfolio.from_signals(
        close, entries, exits,
        init_cash=args.cash, fees=0.0, freq="1D")

    print(f"\n══ {args.symbol.upper()} · {args.strategy} 策略回测 ══")
    print(pf.stats())
    print("\n样本外提醒:回测好看 ≠ 实盘赚钱;先 demo 跑一个月(§21)")


if __name__ == "__main__":
    main()
