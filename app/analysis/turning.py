"""股价拐点识别(复现 Li et al., IEEE TKDE 2024 的 PIP + UN_PIP + 邻居扩展)。
来源参考脚本 report/rklb_daily_analysis.py，此处去掉绘图/IO，只保留算法 +
一个面向实盘的 turning_signal()：基于近期『已确认』的谷/峰给出 买入/卖出/观望 信号。

峰(peak)=高点→卖；谷(valley)=低点→买。用于量化『拐点低买高卖』模式的择时。
"""
import numpy as np


# ---------- PIP (Algorithm 1, 式1-2) ----------
def _pip_dist(k, a, b, t, c):
    return (np.sqrt((t[k] - t[a]) ** 2 + (c[k] - c[a]) ** 2) +
            np.sqrt((t[b] - t[k]) ** 2 + (c[b] - c[k]) ** 2))


def calculate_pips(t, c, m):
    n = len(c)
    m = min(m, n)
    pip = [0, n - 1]
    while len(pip) < m:
        order = sorted(pip)
        best_d, best_p = -1.0, None
        for s in range(len(order) - 1):
            a, b = order[s], order[s + 1]
            for k in range(a + 1, b):
                d = _pip_dist(k, a, b, t, c)
                if d > best_d:
                    best_d, best_p = d, k
        if best_p is None:
            break
        pip.append(best_p)
    return sorted(pip)


# ---------- 适应度函数 UN_PIP (式4) ----------
def un_pip(pip, t, alpha=1.0, beta=15):
    pen = sum(np.sign(max(beta - (t[pip[i]] - t[pip[i - 1]]), 0))
              for i in range(1, len(pip)))
    return len(pip) - alpha * pen


def find_optimal_pips(t, c, alpha=1.0, beta=15):
    n = len(c)
    best_un, best = -np.inf, [0, n - 1]
    for m in range(2, n + 1):
        pip = calculate_pips(t, c, m)
        u = un_pip(pip, t, alpha, beta)
        if u > best_un:
            best_un, best = u, pip
    return best


# ---------- 邻居扩展 + 标注 (Algorithm 2) ----------
def balance_tps(close, sub_len=120, NS=1, alpha=1.0, beta=15):
    close = np.asarray(close, float)
    N = len(close)
    pips = []
    for s in range(0, N, sub_len):
        seg = close[s:s + sub_len]
        if len(seg) < 3:
            continue
        pips += [s + p for p in find_optimal_pips(np.arange(len(seg)), seg, alpha, beta)]
    pips = sorted(set(pips))
    label = {}
    for p in pips:
        lo, hi = max(0, p - 1), min(N - 1, p + 1)
        if close[p] >= close[lo] and close[p] >= close[hi]:
            label[p] = 'peak'
        elif close[p] <= close[lo] and close[p] <= close[hi]:
            label[p] = 'valley'
        else:
            label[p] = 'mid'
    return pips, label


def turning_signal(closes, recent_days: int = 8, sub_len: int = 120,
                   alpha: float = 1.0, beta: float = 15) -> dict:
    """基于近期『已确认』拐点给出实盘信号。
    - 只看 index 在 (0, n-1) 之间的内部拐点(末根 K 线无法确认转折，排除)。
    - 最近确认的拐点若为谷且在 recent_days 内 → 'buy'(刚到低位,适合低买)。
    - 最近确认的拐点若为峰且在 recent_days 内 → 'sell'(刚到高位)。
    - 否则 'hold'(无新鲜拐点信号)。
    返回 {signal, kind, days_since, valley_px, peak_px, n}。
    """
    closes = [float(x) for x in closes if x is not None]
    n = len(closes)
    if n < 30:
        return {"signal": "hold", "kind": None, "days_since": None,
                "valley_px": None, "peak_px": None, "n": n, "reason": "数据不足"}

    pips, label = balance_tps(closes, sub_len=sub_len, NS=1, alpha=alpha, beta=beta)
    valleys = [p for p, l in label.items() if l == 'valley' and 0 < p < n - 1]
    peaks = [p for p, l in label.items() if l == 'peak' and 0 < p < n - 1]
    last_valley = max(valleys) if valleys else None
    last_peak = max(peaks) if peaks else None

    cands = [i for i in (last_valley, last_peak) if i is not None]
    last_idx = max(cands) if cands else None

    sig, kind, days_since = "hold", None, None
    if last_idx is not None:
        days_since = (n - 1) - last_idx
        if last_idx == last_valley and days_since <= recent_days:
            sig, kind = "buy", "valley"
        elif last_idx == last_peak and days_since <= recent_days:
            sig, kind = "sell", "peak"

    return {
        "signal": sig, "kind": kind, "days_since": days_since,
        "valley_px": round(closes[last_valley], 4) if last_valley is not None else None,
        "peak_px": round(closes[last_peak], 4) if last_peak is not None else None,
        "n": n,
    }


# ---------- 日内高频拐点(swing low/high) ----------
def intraday_turning_signal(prices, beta: int = 4, rebound_pct: float = 0.2,
                            recent: int = 3) -> dict:
    """面向日内高频波段的轻量拐点识别(O(n·beta)，可每轮重算)。

    在最近的采样价序列上找『已确认』局部极值：
      - 谷(swing low): prices[i] 是 [i-beta, i+beta] 的最小值，且现价已自谷反弹
        ≥ rebound_pct% → 'buy'(低点刚转向，低买)。
      - 峰(swing high): prices[i] 是窗口最大值，且现价已自峰回落 ≥ rebound_pct% → 'sell'。
    需要极值右侧有 beta 根采样才算『确认』(末端 beta 根不判定)；极值须落在
    最后 recent+beta 根内才算『新鲜』，避免老拐点反复触发。

    参数(以采样根为单位，配合 ~1 分钟采样 → beta≈分钟)：
      beta        swing 半径(两侧根数)，越大波段越大、越稀疏
      rebound_pct 自极值反弹/回落的最小确认幅度 %，过滤噪声
      recent      极值确认后多少根采样内仍触发
    返回 {signal, kind, valley_px, peak_px, days_since(=根数), n}。
    """
    p = [float(x) for x in prices if x is not None and x > 0]
    n = len(p)
    if n < 2 * beta + 2:
        return {"signal": "hold", "kind": None, "days_since": None,
                "valley_px": None, "peak_px": None, "n": n, "reason": "采样不足"}

    last_valley = last_peak = None
    valley_px = peak_px = None
    # 只在 [beta, n-1-beta] 上判定(右侧需 beta 根确认)
    for i in range(beta, n - beta):
        seg = p[i - beta:i + beta + 1]
        if p[i] <= min(seg):
            last_valley, valley_px = i, p[i]
        if p[i] >= max(seg):
            last_peak, peak_px = i, p[i]

    cur = p[-1]
    sig, kind, idx = "hold", None, None
    fresh = lambda j: (n - 1 - j) <= recent + beta

    if last_valley is not None and fresh(last_valley) and valley_px > 0 \
            and (cur - valley_px) / valley_px * 100 >= rebound_pct:
        sig, kind, idx = "buy", "valley", last_valley
    # 峰更靠后(更新鲜)时优先卖信号
    if last_peak is not None and fresh(last_peak) and peak_px > 0 \
            and (peak_px - cur) / peak_px * 100 >= rebound_pct \
            and (idx is None or last_peak > idx):
        sig, kind, idx = "sell", "peak", last_peak

    return {
        "signal": sig, "kind": kind,
        "days_since": (n - 1 - idx) if idx is not None else None,
        "valley_px": round(valley_px, 4) if valley_px is not None else None,
        "peak_px": round(peak_px, 4) if peak_px is not None else None,
        "n": n,
    }
