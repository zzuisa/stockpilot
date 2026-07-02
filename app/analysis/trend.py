"""卡尔曼滤波趋势分析(Trend V9)：快/慢 KF + 置信带 + 多空 regime +
趋势价差(Trend Spread) + 周线共振(Resonance) + 美元主力资金 + 相对量能。

复现盘前日报「趋势证据」原型图。仅用 numpy，数据来自 prices 表日线 OHLCV。
KF 调参 R=1.5/10：以日变动方差为基准噪声 Q，快线 R=1.5Q(贴价)、慢线 R=10Q(平滑)。
"""
import numpy as np


def _kalman(z: np.ndarray, Q: float, R: float):
    """1D 局部水平卡尔曼滤波，返回 (估计序列, 估计方差序列)。"""
    n = len(z)
    x = np.zeros(n)
    P = np.zeros(n)
    x_est, P_est = float(z[0]), R
    for i in range(n):
        P_pred = P_est + Q              # 预测
        K = P_pred / (P_pred + R)       # 卡尔曼增益
        x_est = x_est + K * (z[i] - x_est)
        P_est = (1 - K) * P_pred
        x[i], P[i] = x_est, P_est
    return x, P


def _weekly_spread(dates: list[str], closes: np.ndarray, base: float) -> list[float]:
    """按 ISO 周取每周末收盘 → 周线 KF 快慢价差 → 前向填充回日线长度(共振)。"""
    week_last: dict[str, int] = {}
    for i, d in enumerate(dates):
        try:
            y, m, dd = d[:10].split("-")
            import datetime as _dt
            wk = _dt.date(int(y), int(m), int(dd)).isocalendar()
            week_last[f"{wk[0]}-{wk[1]:02d}"] = i           # 该周最后一个交易日索引
        except Exception:
            continue
    idxs = sorted(week_last.values())
    if len(idxs) < 5:
        return [0.0] * len(closes)
    wk_closes = closes[idxs]
    wb = float(np.var(np.diff(wk_closes))) or base
    wf, _ = _kalman(wk_closes, wb, 1.5 * wb)
    ws, _ = _kalman(wk_closes, wb, 10 * wb)
    wk_spread = wf - ws
    # 映射回日线：每个交易日取其所属周的周线价差
    daily = np.zeros(len(closes))
    cur = 0.0
    wk_pos = 0
    for i in range(len(closes)):
        if wk_pos < len(idxs) and i >= idxs[wk_pos]:
            cur = float(wk_spread[wk_pos])
            wk_pos += 1
        daily[i] = cur
    return [round(v, 3) for v in daily]


def trend_analysis(dates: list[str], closes, volumes,
                   highs=None, lows=None, mf_window: int = 20) -> dict | None:
    """返回趋势证据图所需全部序列与指标。closes/volumes(/highs/lows) 与 dates 等长。"""
    closes = np.asarray(closes, dtype=float)
    volumes = np.asarray(volumes, dtype=float)
    highs = np.asarray(highs, dtype=float) if highs is not None else closes
    lows = np.asarray(lows, dtype=float) if lows is not None else closes
    if len(closes) < 30:
        return None

    base = float(np.var(np.diff(closes))) or 1.0
    fast, Pf = _kalman(closes, base, 1.5 * base)
    slow, _ = _kalman(closes, base, 10.0 * base)
    band = 2.0 * np.sqrt(Pf + 1.5 * base)          # ±2σ 置信带
    spread = fast - slow
    regime = ["bull" if f >= s else "bear" for f, s in zip(fast, slow)]

    # 美元主力资金 = CMF 比率(近 window 日，无量纲 ∈[-1,1]) × 日均美元成交额。
    # 即"日均主力净流向美元"：收盘靠上=流入、靠下=流出。直接累加毛额会到十亿级
    # (高额股日成交数亿)，故用比率×日均额，量级贴近真实净流向。
    rng = np.where(highs > lows, highs - lows, np.nan)
    mult = np.nan_to_num(((closes - lows) - (highs - closes)) / rng, nan=0.0)
    w = slice(-mf_window, None)
    vol_sum = float(np.sum(volumes[w])) or 1.0
    cmf_ratio = float(np.sum(mult[w] * volumes[w])) / vol_sum
    avg_dollar_vol = float(np.mean(volumes[w] * closes[w]))
    money_flow_usd = round(cmf_ratio * avg_dollar_vol, 0)
    avg_vol = float(np.mean(volumes[-mf_window:])) or 1.0
    rel_vol = round(float(volumes[-1]) / avg_vol, 2)

    # 趋势格局标签：近端 spread 相对价格的强度 + 方向
    last_spread = float(spread[-1])
    strength = abs(last_spread) / (closes[-1] or 1) * 100
    if regime[-1] == "bull":
        trend_label = "偏强" if strength > 1.5 else "中性偏多"
    else:
        trend_label = "偏弱" if strength > 1.5 else "中性偏空"

    return {
        "dates": [d[:10] for d in dates],
        "close": [round(float(v), 2) for v in closes],
        "fast": [round(float(v), 2) for v in fast],
        "slow": [round(float(v), 2) for v in slow],
        "band_upper": [round(float(f + b), 2) for f, b in zip(fast, band)],
        "band_lower": [round(float(f - b), 2) for f, b in zip(fast, band)],
        "regime": regime,
        "spread": [round(float(v), 3) for v in spread],
        "weekly_spread": _weekly_spread(dates, closes, base),
        "money_flow_usd": round(money_flow_usd, 0),
        "relative_volume": rel_vol,
        "trend_label": trend_label,
    }
