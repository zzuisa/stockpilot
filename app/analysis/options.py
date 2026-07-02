"""期权指标：从 yfinance 期权链计算 GEX / PCR / IV / Put-Call Wall / Gamma by Strike。

盘前日报「期权结论 / 观察条件」两块的数据来源。无 scipy 依赖，用 numpy/math 自算
Black-Scholes gamma。GEX 采用做市商敞口惯例：call gamma 记正、put gamma 记负，
单位为「标的每变动 1% 的美元 gamma 敞口」。
"""
import logging
import math
from datetime import datetime, timezone

log = logging.getLogger(__name__)

_R = 0.045          # 无风险利率近似
_SQRT2PI = math.sqrt(2 * math.pi)


def _num(x) -> float:
    """转 float，None/NaN/inf 归 0（yfinance 期权字段常含 NaN）。"""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    return v if math.isfinite(v) else 0.0


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / _SQRT2PI


def _bs_gamma(S: float, K: float, T: float, sigma: float) -> float:
    """Black-Scholes gamma（call/put 相同）。无效输入返回 0。"""
    if S <= 0 or K <= 0 or T <= 0 or sigma <= 0:
        return 0.0
    try:
        d1 = (math.log(S / K) + (_R + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
        return _norm_pdf(d1) / (S * sigma * math.sqrt(T))
    except (ValueError, ZeroDivisionError):
        return 0.0


def _spot(t) -> float:
    """现价：fast_info 优先，回退最近收盘。"""
    try:
        p = float(t.fast_info.get("lastPrice") or 0)
        if p > 0:
            return p
    except Exception:
        pass
    try:
        h = t.history(period="1d", prepost=True, auto_adjust=True)
        c = h["Close"].dropna()
        if not c.empty:
            return float(c.iloc[-1])
    except Exception:
        pass
    return 0.0


def option_metrics(symbol: str, yf_symbol: str | None = None,
                   max_expiries: int = 3, strike_window: int = 12) -> dict | None:
    """计算期权综合指标。返回 None 表示该标的无期权或拉取失败。

    返回: {spot, gex, pcr_oi, pcr_vol, iv_atm, call_wall, put_wall,
           expected_move_pct, gamma_by_strike:[{strike, gex, call_oi, put_oi}], expiries}
    """
    import yfinance as yf
    yf_symbol = yf_symbol or symbol
    t = yf.Ticker(yf_symbol)
    try:
        expiries = list(t.options or [])
    except Exception as e:
        log.warning("options %s: 取到期日失败 %s", symbol, e)
        return None
    if not expiries:
        return None

    spot = _spot(t)
    if spot <= 0:
        return None
    now = datetime.now(timezone.utc)

    gex_by_strike: dict[float, float] = {}
    call_oi: dict[float, float] = {}
    put_oi: dict[float, float] = {}
    tot_call_oi = tot_put_oi = tot_call_vol = tot_put_vol = 0.0
    atm_ivs: list[float] = []
    t_nearest = None

    for exp in expiries[:max_expiries]:
        try:
            chain = t.option_chain(exp)
        except Exception as e:
            log.warning("options %s %s: %s", symbol, exp, e)
            continue
        try:
            exp_dt = datetime.strptime(exp, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        days = max((exp_dt - now).total_seconds() / 86400.0, 0.5)
        T = days / 365.0
        if t_nearest is None:
            t_nearest = T

        for df, sign, oi_acc in ((chain.calls, 1.0, call_oi),
                                 (chain.puts, -1.0, put_oi)):
            if df is None or df.empty:
                continue
            for _, row in df.iterrows():
                K = _num(row.get("strike"))
                oi = _num(row.get("openInterest"))
                vol = _num(row.get("volume"))
                iv = _num(row.get("impliedVolatility"))
                if K <= 0:
                    continue
                gamma = _bs_gamma(spot, K, T, iv)
                # 每 1% 现价变动的美元 gamma 敞口
                gex = sign * gamma * oi * 100 * spot * spot * 0.0001
                gex_by_strike[K] = gex_by_strike.get(K, 0.0) + gex
                oi_acc[K] = oi_acc.get(K, 0.0) + oi
                if sign > 0:
                    tot_call_oi += oi
                    tot_call_vol += vol
                else:
                    tot_put_oi += oi
                    tot_put_vol += vol
                # ATM(现价 ±5%)的 IV 收集
                if iv > 0 and abs(K - spot) / spot <= 0.05:
                    atm_ivs.append(iv)

    if not gex_by_strike:
        return None

    total_gex = sum(gex_by_strike.values())
    pcr_oi = round(tot_put_oi / tot_call_oi, 2) if tot_call_oi else None
    pcr_vol = round(tot_put_vol / tot_call_vol, 2) if tot_call_vol else None
    iv_atm = round(sum(atm_ivs) / len(atm_ivs), 4) if atm_ivs else None
    # 按 gamma 敞口定墙(标准 gamma-wall 定义，自然偏向近价高 gamma 行权价)：
    # Call Wall = 现价上方净 GEX 最大(最正)行权价(阻力)；
    # Put Wall = 下方净 GEX 最小(最负)行权价(支撑)。
    gex_above = {k: v for k, v in gex_by_strike.items() if k >= spot}
    gex_below = {k: v for k, v in gex_by_strike.items() if k <= spot}
    call_wall = max(gex_above, key=gex_above.get) if gex_above else None
    put_wall = min(gex_below, key=gex_below.get) if gex_below else None
    expected_move_pct = (round(iv_atm * math.sqrt(t_nearest) * 100, 1)
                         if iv_atm and t_nearest else None)

    # Gamma by Strike（现价附近 ±strike_window 个行权价，供柱状图）
    strikes = sorted(gex_by_strike.keys())
    near = sorted(strikes, key=lambda k: abs(k - spot))[:strike_window * 2]
    gamma_by_strike = [
        {"strike": k,
         "gex": round(gex_by_strike[k], 2),
         "call_oi": int(call_oi.get(k, 0)),
         "put_oi": int(put_oi.get(k, 0))}
        for k in sorted(near)
    ]

    return {
        "spot": round(spot, 2),
        "gex": round(total_gex, 2),
        "pcr_oi": pcr_oi,
        "pcr_vol": pcr_vol,
        "iv_atm": iv_atm,
        "call_wall": round(float(call_wall), 2) if call_wall is not None else None,
        "put_wall": round(float(put_wall), 2) if put_wall is not None else None,
        "expected_move_pct": expected_move_pct,
        "gamma_by_strike": [
            {"strike": round(float(g["strike"]), 2), "gex": g["gex"],
             "call_oi": g["call_oi"], "put_oi": g["put_oi"]}
            for g in gamma_by_strike
        ],
        "expiries": expiries[:max_expiries],
    }
