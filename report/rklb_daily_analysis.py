"""
RKLB 日线拐点识别 —— 复现论文 (Li et al., IEEE TKDE 2024) 的方法。
读取本地 RKLB.csv (yfinance 多级表头格式), 输出拐点图 + CSV。
方法 = PIP (Algorithm 1) + 适应度函数 UN_PIP (式4) + 邻居扩展 (Algorithm 2)。

依赖: pip install numpy pandas matplotlib
用法: python rklb_daily_analysis.py
"""
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


# ---------- PIP (Algorithm 1, 式1-2) ----------
def _pip_dist(k, a, b, t, c):
    return (np.sqrt((t[k]-t[a])**2 + (c[k]-c[a])**2) +
            np.sqrt((t[b]-t[k])**2 + (c[b]-c[k])**2))

def calculate_pips(t, c, m):
    n = len(c); m = min(m, n)
    pip = [0, n-1]; sc = {0: 0.0, n-1: 0.0}
    while len(pip) < m:
        order = sorted(pip); best_d, best_p = -1.0, None
        for s in range(len(order)-1):
            a, b = order[s], order[s+1]
            for k in range(a+1, b):
                d = _pip_dist(k, a, b, t, c)
                if d > best_d: best_d, best_p = d, k
        if best_p is None: break
        pip.append(best_p); sc[best_p] = best_d
    return sorted(pip), sc


# ---------- 适应度函数 UN_PIP (式4) ----------
# 注: 论文 alpha=0.1 在字面公式下会退化为"选中全部点", 故取 alpha>=1 让惩罚生效。
def un_pip(pip, t, alpha=1.0, beta=15):
    pen = sum(np.sign(max(beta - (t[pip[i]] - t[pip[i-1]]), 0)) for i in range(1, len(pip)))
    return len(pip) - alpha * pen

def find_optimal_pips(t, c, alpha=1.0, beta=15):
    n = len(c); best_un, best = -np.inf, [0, n-1]
    for m in range(2, n+1):
        pip, _ = calculate_pips(t, c, m)
        u = un_pip(pip, t, alpha, beta)
        if u > best_un: best_un, best = u, pip
    return best


# ---------- 邻居扩展 + 整体流程 (Algorithm 2) ----------
def balance_tps(close, sub_len=120, NS=1, alpha=1.0, beta=15):
    close = np.asarray(close, float); N = len(close); pips = []
    for s in range(0, N, sub_len):
        seg = close[s:s+sub_len]
        if len(seg) < 3: continue
        pips += [s + p for p in find_optimal_pips(np.arange(len(seg)), seg, alpha, beta)]
    pips = sorted(set(pips))
    tps = set(pips)
    for p in pips:
        if p-1 >= 0: tps.add(p-1)
        if NS >= 2 and p-2 >= 0: tps.add(p-2)
    label = {}
    for p in pips:
        lo, hi = max(0, p-1), min(N-1, p+1)
        if close[p] >= close[lo] and close[p] >= close[hi]: label[p] = 'peak'
        elif close[p] <= close[lo] and close[p] <= close[hi]: label[p] = 'valley'
        else: label[p] = 'mid'
    return sorted(tps), pips, label


def main():
    # yfinance 多级表头: 第1行字段名, 第2行 Ticker, 第3行 Date, 之后是数据
    df = pd.read_csv("RKLB.csv", skiprows=[1, 2], index_col=0, parse_dates=True)
    df.index.name = "Date"
    close = df["Close"].to_numpy(float); dates = df.index

    tps, pips, label = balance_tps(close, sub_len=120, NS=1, alpha=1.0, beta=15)
    pk = [p for p, l in label.items() if l == 'peak']
    vl = [p for p, l in label.items() if l == 'valley']
    print(f"{len(close)} 根K线 | PIP {len(pips)} | TP {len(tps)} ({len(tps)/len(close):.1%}) | "
          f"峰/卖 {len(pk)} | 谷/买 {len(vl)}")

    # 导出
    out = df[["Close"]].copy(); out["is_TP"] = 0
    out.iloc[tps, out.columns.get_loc("is_TP")] = 1
    out["signal"] = ""
    out.iloc[pk, out.columns.get_loc("signal")] = "SELL"
    out.iloc[vl, out.columns.get_loc("signal")] = "BUY"
    out.to_csv("RKLB_turning_points.csv")

    # 画图
    fig, ax = plt.subplots(2, 1, figsize=(14, 8), sharex=True,
                           gridspec_kw={'height_ratios': [3, 1]})
    ax[0].plot(dates, close, color='#777', lw=1)
    ax[0].scatter(dates[pk], close[pk], c='black', s=24, zorder=3, label='Peak / SELL')
    ax[0].scatter(dates[vl], close[vl], c='#d62728', s=24, zorder=3, label='Valley / BUY')
    ax[0].set_yscale('log'); ax[0].set_ylabel('Price (log)'); ax[0].legend(); ax[0].grid(alpha=.25)
    ax[1].bar(dates, df["Volume"].to_numpy(), color='#ccc', width=1); ax[1].set_ylabel('Volume')
    plt.tight_layout(); plt.savefig("RKLB_turning_points.png", dpi=130)
    print("已保存 RKLB_turning_points.csv 和 .png")


if __name__ == "__main__":
    main()
