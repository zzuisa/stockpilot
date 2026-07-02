"""ETF 全天候回测 API (All Weather Lab)
- POST /run            跑回测，返回 KPI / 多策略对比 / 每资产贡献 / 漂移 / 记录 / 序列
- GET  /data-status    各标的价格覆盖区间/行数
- POST /update-data    从 yfinance 回填全天候标的历史日线(period=max)
- POST /report.html    HTML 报告
- POST /export.csv     完整日级净值 CSV
- POST /export-drift.csv  每资产最大漂移 / 利润贡献 CSV
"""
import csv
import io
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select

from analysis import portfolio
from collectors import prices as price_collector
from db import get_db, get_session
from models import Price

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/backtest", tags=["backtest"])


class Tier(BaseModel):
    dd: float
    amount: float


class OppItem(BaseModel):
    symbol: str
    tiers: list[Tier]


class BacktestConfig(BaseModel):
    weights: dict[str, float] | None = None
    monthly_dca: float = 1400
    initial: float = 0
    rebalance_months: int = 3
    rebalance_set: list[int] | None = None
    opportunity: list[OppItem] | None = None
    opp_cap: float | None = None
    benchmark: str = "SPY"
    start: str | None = "2016-05-01"
    end: str | None = None


def _cfg(body: BacktestConfig) -> dict:
    d = body.model_dump()
    for k in ("opportunity", "rebalance_set", "weights"):
        if d.get(k) is None:            # None → 用引擎默认
            d.pop(k, None)
    return d


@router.post("/run")
async def run(body: BacktestConfig):
    return portfolio.run_backtest(_cfg(body))


@router.get("/data-status")
async def data_status(db=Depends(get_db)):
    out = []
    for sym in portfolio.ALL_WEATHER_SYMBOLS:
        row = db.execute(
            select(func.count(), func.min(Price.ts), func.max(Price.ts))
            .where(Price.symbol == sym, Price.interval == "1d")).one()
        out.append({"symbol": sym, "label": portfolio.ASSET_LABEL.get(sym, sym),
                    "rows": int(row[0] or 0),
                    "start": row[1].strftime("%Y-%m-%d") if row[1] else None,
                    "end": row[2].strftime("%Y-%m-%d") if row[2] else None})
    ready = sum(1 for s in out if s["rows"] > 100)
    return {"symbols": out, "ready": ready, "total": len(out)}


@router.post("/update-data")
async def update_data():
    """从 yfinance 回填全天候标的历史日线(period=max，复权=总回报)。"""
    with get_session() as db:
        res = price_collector.fetch_daily(
            portfolio.ALL_WEATHER_SYMBOLS, db, period="max", yf_map=portfolio.YF_MAP)
    log.info("backtest update-data: %s", res)
    return {"ok": True, **res}


@router.post("/report.html", response_class=HTMLResponse)
async def report_html(body: BacktestConfig):
    return HTMLResponse(_render_html(portfolio.run_backtest(_cfg(body))))


@router.post("/export.csv")
async def export_csv(body: BacktestConfig):
    r = portfolio.run_backtest(_cfg(body))
    buf = io.StringIO()
    w = csv.writer(buf)
    if r.get("error"):
        w.writerow(["error", r["error"]])
    else:
        s = r["series"]
        w.writerow(["date", "equity", "drawdown_pct", "cumulative_pct", "annualized_pct"])
        for i in range(len(s["dates"])):
            w.writerow([s["dates"][i], s["equity"][i], s["drawdown"][i],
                        s["cumulative"][i], s["annualized"][i]])
    return _csv_response(buf, "allweather_equity.csv")


@router.post("/export-drift.csv")
async def export_drift(body: BacktestConfig):
    r = portfolio.run_backtest(_cfg(body))
    buf = io.StringIO()
    w = csv.writer(buf)
    if r.get("error"):
        w.writerow(["error", r["error"]])
    else:
        w.writerow(["symbol", "label", "target_weight_pct", "max_drift_pct",
                    "net_invested", "final_value", "profit"])
        for a in r["per_asset"]:
            w.writerow([a["symbol"], a["label"], a["target_weight"], a["max_drift"],
                        a["net_invested"], a["final_value"], a["profit"]])
    return _csv_response(buf, "allweather_drift.csv")


def _csv_response(buf: io.StringIO, filename: str) -> StreamingResponse:
    buf.seek(0)
    return StreamingResponse(
        iter(["﻿" + buf.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"})


# ── HTML 报告 ────────────────────────────────────────────────────────────────
def _render_html(r: dict) -> str:
    if r.get("error"):
        return f"<html><body style='font-family:sans-serif;padding:40px'><h2>无法生成报告</h2><p>{r['error']}</p></body></html>"
    k = r["kpi"]
    rng = r["effective_range"]

    def money(v):
        return f"${v:,.0f}"

    kpis = [("年化复合收益", f"{k['cagr']}%"), ("年化波动", f"{k['vol']}%"),
            ("最大回撤", f"{k['maxdd']}%"), ("Sharpe", k["sharpe"]),
            ("期末总资产", money(k["final"])), ("累计月定投", money(k["dca_total"])),
            ("机会仓流入", money(k["opp_total"])), ("有效区间", f"{rng[0]} → {rng[1]}")]
    kpi_html = "".join(
        f"<div class='kpi'><div class='k'>{n}</div><div class='v'>{v}</div></div>" for n, v in kpis)

    def cmp_rows():
        out = ""
        for c in r["comparison"]:
            out += (f"<tr><td>{c['name']}</td><td>{c['type']}</td><td>{c['cagr']}%</td>"
                    f"<td>{c['vol']}%</td><td>{c['maxdd']}%</td><td>{c['sharpe']}</td>"
                    f"<td>{money(c['final'])}</td><td>{money(c['total_invested'])}</td>"
                    f"<td>{money(c['net_profit'])}</td></tr>")
        return out

    def asset_rows():
        out = ""
        for a in r["per_asset"]:
            out += (f"<tr><td>{a['symbol']} · {a['label']}</td><td>{a['target_weight']}%</td>"
                    f"<td>{money(a['net_invested'])}</td><td>{money(a['final_value'])}</td>"
                    f"<td>{money(a['profit'])}</td><td>{a['max_drift']}%</td></tr>")
        return out

    return f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>All Weather Lab 回测报告</title><style>
body{{font-family:'Segoe UI',system-ui,sans-serif;background:#0C1117;color:#D9E0E8;margin:0;padding:28px}}
h1{{font-size:22px}} h2{{font-size:15px;color:#E8A33D;margin:24px 0 10px}}
.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}
.kpi{{background:#121922;border:1px solid #1E2933;border-radius:8px;padding:12px 14px}}
.kpi .k{{font-size:11px;color:#7C8896}} .kpi .v{{font-family:monospace;font-size:20px;margin-top:4px}}
table{{width:100%;border-collapse:collapse;font-size:12.5px;font-family:monospace}}
th,td{{text-align:right;padding:7px 8px;border-bottom:1px solid #1E2933}}
th:first-child,td:first-child{{text-align:left}} th{{color:#7C8896;font-weight:500}}
.muted{{color:#7C8896;font-size:12px}}</style></head><body>
<h1>All Weather Lab · 全天候回测报告</h1>
<p class="muted">有效区间 {rng[0]} → {rng[1]} · 主再平衡周期 {r['primary_rebalance']} 个月</p>
<div class="kpis">{kpi_html}</div>
<h2>多策略核心指标对比</h2>
<table><thead><tr><th>方案</th><th>类型</th><th>年化复合收益</th><th>年化波动</th><th>最大回撤</th>
<th>Sharpe</th><th>期末资产</th><th>总投入</th><th>净盈利</th></tr></thead><tbody>{cmp_rows()}</tbody></table>
<h2>每资产利润贡献 / 最大漂移</h2>
<table><thead><tr><th>资产</th><th>目标权重</th><th>净投入</th><th>期末市值</th><th>净利润</th><th>最大漂移</th></tr></thead>
<tbody>{asset_rows()}</tbody></table>
<p class="muted" style="margin-top:24px">注：历史数据可能会骗人——过去表现不代表未来，回测仅用于验证策略逻辑能否长期执行，非投资建议。</p>
</body></html>"""
