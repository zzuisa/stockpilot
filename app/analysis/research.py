"""财报下载 + LLM 泡沫分析 + 投资策略深度分析"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

REPORT_DIR = Path("/appHome/application/StockPilot/report")


def _llm_client():
    from openai import OpenAI
    import settings
    # max_retries=0：失败立即返回，避免 openai 默认重试 2 次叠加超时(可达 3×timeout)
    # 把研究/日报请求拖到 >120s。单次超时由各 create() 的 timeout 控制。
    return OpenAI(
        api_key=settings.SILICONFLOW_API_KEY,
        base_url=settings.SILICONFLOW_BASE_URL,
        max_retries=0,
        timeout=95,   # 各 create() 仍可按需用更短 timeout 覆盖
    )


def _fmt_val(v) -> str:
    """格式化财务数值"""
    if v is None:
        return "N/A"
    if isinstance(v, float) and v != v:  # NaN
        return "N/A"
    if isinstance(v, (int, float)):
        av = abs(v)
        if av >= 1e9:
            return f"{v/1e9:.2f}B"
        if av >= 1e6:
            return f"{v/1e6:.2f}M"
        return f"{v:.4f}"
    return str(v)


def download_quarterly_report(symbol: str, period: str | None = None) -> dict:
    """
    使用 yfinance 下载指定股票最近季度财报数据，保存为结构化文本。
    period: "2024Q4" 格式，None = 最新季度。
    返回 {period, filename, path, size, report_text}
    """
    import yfinance as yf

    ticker = yf.Ticker(symbol)
    try:
        income = ticker.quarterly_income_stmt
        balance = ticker.quarterly_balance_sheet
        cashflow = ticker.quarterly_cashflow
        info = ticker.info or {}
    except Exception as e:
        raise ValueError(f"yfinance 获取 {symbol} 失败: {e}")

    # 确定目标期次列
    target_col = None
    period_str = "Unknown"
    if income is not None and not income.empty:
        cols = list(income.columns)
        if period:
            for c in cols:
                try:
                    q = (c.month - 1) // 3 + 1
                    ps = f"{c.year}Q{q}"
                except Exception:
                    ps = str(c)[:7]
                if period in ps or period == ps:
                    target_col = c
                    period_str = ps
                    break
        if target_col is None:
            target_col = cols[0]
        if hasattr(target_col, "month"):
            q = (target_col.month - 1) // 3 + 1
            period_str = f"{target_col.year}Q{q}"
        else:
            period_str = str(target_col)[:7].replace("-", "Q")

    lines = [
        f"# {symbol} 季度财务报告 ({period_str})",
        f"生成时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "## 公司基本信息",
    ]

    info_keys = [
        ("longName", "公司名称"), ("sector", "行业"), ("industry", "细分行业"),
        ("country", "国家"), ("marketCap", "市值"), ("enterpriseValue", "企业价值"),
        ("forwardPE", "预期市盈率"), ("trailingPE", "历史市盈率"),
        ("priceToBook", "市净率"), ("priceToSalesTrailing12Months", "市销率"),
        ("enterpriseToEbitda", "EV/EBITDA"), ("debtToEquity", "债务/股权比"),
        ("returnOnEquity", "ROE"), ("returnOnAssets", "ROA"),
        ("grossMargins", "毛利率"), ("operatingMargins", "营业利润率"),
        ("profitMargins", "净利率"), ("revenueGrowth", "收入增速(YoY)"),
        ("earningsGrowth", "盈利增速(YoY)"), ("currentRatio", "流动比率"),
        ("quickRatio", "速动比率"), ("totalDebt", "总债务"),
        ("totalRevenue", "总收入(TTM)"), ("freeCashflow", "自由现金流(TTM)"),
        ("currentPrice", "当前股价"), ("fiftyTwoWeekHigh", "52周高点"),
        ("fiftyTwoWeekLow", "52周低点"), ("targetMeanPrice", "分析师目标均价"),
        ("targetHighPrice", "分析师目标高价"), ("targetLowPrice", "分析师目标低价"),
        ("recommendationMean", "分析师评级均值(1=强买5=强卖)"),
        ("numberOfAnalystOpinions", "跟踪分析师数"),
        ("beta", "Beta系数"), ("shortRatio", "做空比率"),
    ]
    for key, label in info_keys:
        val = info.get(key)
        if val is not None:
            lines.append(f"- {label}: {_fmt_val(val)}")

    # 季度损益表
    if income is not None and not income.empty:
        lines += ["", "## 季度损益表 (最近4期，从新到旧)"]
        inc_keys = [
            "Total Revenue", "Gross Profit", "Operating Income",
            "Net Income", "EBITDA", "Basic EPS", "Diluted EPS",
            "Operating Expense", "Research And Development",
        ]
        for item in inc_keys:
            if item in income.index:
                row = income.loc[item]
                vals = [_fmt_val(row.get(c)) for c in list(income.columns)[:4]]
                lines.append(f"- {item}: {' | '.join(vals)}")

    # 季度资产负债表
    if balance is not None and not balance.empty:
        lines += ["", "## 季度资产负债表 (最近2期)"]
        bal_keys = [
            "Total Assets", "Total Liabilities Net Minority Interest",
            "Total Equity Gross Minority Interest", "Cash And Cash Equivalents",
            "Long Term Debt", "Current Assets", "Current Liabilities",
            "Stockholders Equity", "Retained Earnings",
        ]
        for item in bal_keys:
            if item in balance.index:
                row = balance.loc[item]
                vals = [_fmt_val(row.get(c)) for c in list(balance.columns)[:2]]
                lines.append(f"- {item}: {' | '.join(vals)}")

    # 季度现金流
    if cashflow is not None and not cashflow.empty:
        lines += ["", "## 季度现金流 (最近4期)"]
        cf_keys = [
            "Operating Cash Flow", "Investing Cash Flow",
            "Financing Cash Flow", "Free Cash Flow",
            "Capital Expenditure", "Dividends Paid",
        ]
        for item in cf_keys:
            if item in cashflow.index:
                row = cashflow.loc[item]
                vals = [_fmt_val(row.get(c)) for c in list(cashflow.columns)[:4]]
                lines.append(f"- {item}: {' | '.join(vals)}")

    report_text = "\n".join(lines)

    sym_dir = REPORT_DIR / symbol
    sym_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{symbol}_{period_str}_quarterly.txt"
    filepath = sym_dir / filename
    filepath.write_text(report_text, encoding="utf-8")

    return {
        "period": period_str,
        "filename": filename,
        "path": str(filepath),
        "size": filepath.stat().st_size,
        "report_text": report_text,
    }


# ─── LLM 泡沫分析 ───────────────────────────────────────────────────────────

_BUBBLE_SYSTEM = (
    "你是顶级投资银行的资深基本面分析师，擅长股票估值与泡沫识别。"
    "你的任务是评估股票当前股价是否超出公司基本面合理区间，给出泡沫幅度判断。"
    "只返回 JSON，不含任何其他内容。"
)

_BUBBLE_USER_TMPL = """## 分析标的: {symbol}  当前股价: {current_price}

### 技术指标（最新日线）
{indicators_text}

### 近7天新闻情绪汇总
{news_text}

### 基本面数据（季报）
{report_text}

---
请从5个维度综合评估当前股价与基本面的偏离程度：
1. 估值：市盈率/市净率/EV-EBITDA 与同业和历史中枢对比
2. 盈利质量：收入增速、利润率趋势、自由现金流健康度
3. 债务与流动性：资产负债结构、偿债能力
4. 市场预期：分析师目标价距离、分析师评级与情绪面
5. 技术面：RSI超买超卖程度、MACD背离信号、布林带位置

泡沫幅度定义：
- normal（0-10%）：股价在基本面合理区间，无明显高估
- slight（10-30%）：轻微高估，基本面支撑较强但有一定溢价
- moderate（30-60%）：中度高估，基本面难以完全支撑
- severe（60-100%）：严重高估，估值泡沫明显，下行风险大
- extreme（>100%）：极度高估，股价远超基本面

返回 JSON（所有字段必填）：
{{"bubble_level":"normal|slight|moderate|severe|extreme","bubble_pct":<0-200数字>,"fundamental_value":<估算合理价格>,"summary":"泡沫判断的核心依据，100-150字，需提及估值倍数与对比基准","key_factors":[{{"factor":"估值","signal":"偏高|合理|偏低","detail":"具体数据与分析，30-50字"}},{{"factor":"盈利质量","signal":"...","detail":"..."}},{{"factor":"债务结构","signal":"...","detail":"..."}},{{"factor":"市场预期","signal":"...","detail":"..."}},{{"factor":"技术面","signal":"...","detail":"..."}}],"risk_warning":"主要风险，50字以内"}}
"""


def llm_bubble_analysis(symbol: str, report_text: str, news_agg: dict,
                        current_price: float, indicators: dict) -> tuple[dict | None, int]:
    """LLM 分析财报+新闻 → 泡沫幅度。返回 (result_dict|None, tokens)"""
    import settings
    if not settings.llm_enabled:
        return None, 0

    def fv(v, fmt=".2f"):
        return f"{v:{fmt}}" if isinstance(v, (int, float)) and v == v else "N/A"

    indicators_text = (
        f"RSI: {fv(indicators.get('rsi'))}\n"
        f"MACD/信号线/柱: {fv(indicators.get('macd'), '.4f')} / "
        f"{fv(indicators.get('macd_signal'), '.4f')} / "
        f"{fv(indicators.get('macd_hist'), '.4f')}\n"
        f"SMA20/50/200: {fv(indicators.get('sma20'))} / "
        f"{fv(indicators.get('sma50'))} / {fv(indicators.get('sma200'))}\n"
        f"布林带 上轨/下轨: {fv(indicators.get('bb_upper'))} / {fv(indicators.get('bb_lower'))}\n"
        f"ATR: {fv(indicators.get('atr'), '.4f')}  成交量比率: {fv(indicators.get('vol_ratio'))}"
    )

    news_text = (
        f"新闻平均情绪分: {fv(news_agg.get('sent_avg', 0))} (±2范围)\n"
        f"新闻数量/来源质量层级均值: {news_agg.get('news_cnt', 0)} 条 / "
        f"{fv(news_agg.get('news_tier_avg'))}\n"
        f"社区帖情绪均分: {fv(news_agg.get('comm_avg', 0))} ({news_agg.get('comm_cnt', 0)} 帖)"
    )

    truncated = report_text[:3500] if len(report_text) > 3500 else report_text

    prompt = _BUBBLE_USER_TMPL.format(
        symbol=symbol, current_price=current_price,
        indicators_text=indicators_text,
        news_text=news_text,
        report_text=truncated,
    )

    try:
        client = _llm_client()
        resp = client.chat.completions.create(
            model=settings.SILICONFLOW_MODEL,
            messages=[
                {"role": "system", "content": _BUBBLE_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=2048,
            timeout=120,
        )
        text = resp.choices[0].message.content or ""
        tokens = int(getattr(resp.usage, "total_tokens", 0) or 0)
        return json.loads(text), tokens
    except Exception as e:
        log.warning("llm bubble analysis failed for %s: %s", symbol, e)
        return None, 0


# ─── LLM 投资策略 ─────────────────────────────────────────────────────────────

_STRATEGY_SYSTEM = (
    "你是顶级对冲基金的首席策略师，擅长多因子量化分析与基本面结合的投资决策。"
    "给出一份深度、专业、可执行的投资策略分析报告。"
    "只返回 JSON，不含任何其他内容。"
)

_STRATEGY_USER_TMPL = """## 深度投资策略分析: {symbol}  当前价格: {current_price}

### 技术指标快照（最新日线）
RSI: {rsi}
MACD: {macd} | 信号线: {macd_signal} | 柱: {macd_hist} | 交叉信号: {macd_cross}
SMA20/50/200: {sma20} / {sma50} / {sma200}
布林带 上轨/下轨: {bb_upper} / {bb_lower}
ATR(波动率): {atr}  成交量比率: {vol_ratio}

### 近7天新闻情绪
平均情绪分: {sent_avg} (±2)  新闻 {news_cnt} 条  来源质量层级均值: {tier_avg}
社区情绪均分: {comm_avg} ({comm_cnt} 帖)

### 泡沫评估
泡沫级别: {bubble_level}  幅度: {bubble_pct}%
基本面合理价: {fundamental_value}
摘要: {bubble_summary}

### 近5日技术指标趋势
{history_text}

---
请从5个角度深度分析，给出专业投资策略：
1. 趋势判断：当前价格结构（上升/横盘/下降）、均线多空排列、趋势强度
2. 进出场信号：RSI超买超卖 + MACD金叉/死叉 + 布林带位置综合判断
3. 估值与情绪：泡沫评估结合新闻情绪，当前风险收益比
4. 风险评估：主要下行风险、上行催化剂、ATR波动率对应的风险
5. 操作建议：具体建议（买入/持有/减持/卖出）、目标价区间、止损参考

返回 JSON（所有字段必填，价格为数字）：
{{"recommendation":"buy|hold|reduce|sell","confidence":0.0-1.0,"target_price_low":<数字>,"target_price_high":<数字>,"stop_loss":<数字>,"holding_period":"短线(1-5天)|中线(1-4周)|长线(1-3月)","trend_phase":"上升趋势|横盘整理|下降趋势","trend_strength":"强|中|弱","technical_signal":"多头信号|中性|空头信号","rsi_status":"超买(>70)|正常|超卖(<30)","macd_status":"金叉上行|顶背离|死叉下行|底背离|中性","summary":"策略核心摘要，100-150字，需涵盖技术面+估值+情绪三个维度","catalysts":[{{"type":"利好|利空","description":"..."}},{{"type":"...","description":"..."}}],"key_levels":{{"support1":<价格>,"support2":<价格>,"resistance1":<价格>,"resistance2":<价格>}},"risk_factors":["风险1，20字以内","风险2","风险3"]}}
"""


def llm_investment_strategy(symbol: str, indicators: dict, indicators_history: list,
                            news_agg: dict, bubble: dict,
                            current_price: float) -> tuple[dict | None, int]:
    """LLM 综合分析 → 投资策略。返回 (result_dict|None, tokens)"""
    import settings
    if not settings.llm_enabled:
        return None, 0

    def fv(v, fmt=".2f"):
        return f"{v:{fmt}}" if isinstance(v, (int, float)) and v == v else "N/A"

    cross_map = {1: "金叉(看涨)", -1: "死叉(看跌)", 0: "无交叉"}
    history_lines = []
    for row in indicators_history[-5:]:
        rsi = row.get("rsi")
        macd = row.get("macd")
        close = row.get("close")
        history_lines.append(
            f"  {str(row.get('ts', ''))[:10]}: "
            f"Close={fv(close)} RSI={fv(rsi)} MACD={fv(macd, '.4f')}"
        )

    prompt = _STRATEGY_USER_TMPL.format(
        symbol=symbol, current_price=current_price,
        rsi=fv(indicators.get("rsi")),
        macd=fv(indicators.get("macd"), ".4f"),
        macd_signal=fv(indicators.get("macd_signal"), ".4f"),
        macd_hist=fv(indicators.get("macd_hist"), ".4f"),
        macd_cross=cross_map.get(indicators.get("macd_cross", 0), "N/A"),
        sma20=fv(indicators.get("sma20")), sma50=fv(indicators.get("sma50")),
        sma200=fv(indicators.get("sma200")),
        bb_upper=fv(indicators.get("bb_upper")), bb_lower=fv(indicators.get("bb_lower")),
        atr=fv(indicators.get("atr"), ".4f"), vol_ratio=fv(indicators.get("vol_ratio")),
        sent_avg=fv(news_agg.get("sent_avg", 0)),
        news_cnt=news_agg.get("news_cnt", 0),
        tier_avg=fv(news_agg.get("news_tier_avg")),
        comm_avg=fv(news_agg.get("comm_avg", 0)),
        comm_cnt=news_agg.get("comm_cnt", 0),
        bubble_level=bubble.get("bubble_level", "未评估"),
        bubble_pct=bubble.get("bubble_pct", 0),
        fundamental_value=bubble.get("fundamental_value", "N/A"),
        bubble_summary=bubble.get("summary", "暂无泡沫分析"),
        history_text="\n".join(history_lines) if history_lines else "暂无历史数据",
    )

    try:
        client = _llm_client()
        resp = client.chat.completions.create(
            model=settings.SILICONFLOW_MODEL,
            messages=[
                {"role": "system", "content": _STRATEGY_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=3000,
            timeout=180,
        )
        text = resp.choices[0].message.content or ""
        tokens = int(getattr(resp.usage, "total_tokens", 0) or 0)
        return json.loads(text), tokens
    except Exception as e:
        log.warning("llm strategy failed for %s: %s", symbol, e)
        return None, 0
