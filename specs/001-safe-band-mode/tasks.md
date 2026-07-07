# Tasks: 001 只赚不亏模式 + 波段适配性判断

来源：`plan.md` + `spec.md`（含 Clarifications）。按阶段执行，每阶段完成即 Conventional Commits。

## Phase 1 — 只赚不亏 safe_mode（P1，止血，本次实现）
- [ ] T101 `quant.py` DEFAULT_PARAMS 增 `safe_mode=False`、`disaster_stop_pct=25.0`
- [ ] T102 `quant.py` 加 `_loss_stop_pct()`：safe_mode→disaster_stop_pct，否则 stop_loss
- [ ] T103 `quant.py` 状态②(止盈监控中)与状态③(持仓) 的硬止损阈值改用 `_loss_stop_pct()`；safe_mode 下 reason 记为 `disaster_stop`
- [ ] T104 `quant.py` 状态③ `signal_stop` 用 `not safe_mode` 门控（safe_mode 下不触发指标止损）——**直接消除 NIO 5.02→5.01 小亏**
- [ ] T105 `quant.py` 决策解释：`_decision_context` 带 safe_mode/disaster_stop_pct；`_rule_explain` 加 `disaster_stop`；`disaster_stop` 纳入 LLM 解释白名单
- [ ] T106 `api/quant.py` StrategyStart 增 `safe_mode`、`disaster_stop_pct`
- [ ] T107 前端 `BandModal.vue` 加「只赚不亏」开关 + 灾难止损% + 风险提示；`endpoints/types` 传参；运行卡显示徽章
- [ ] T108 单测：safe_mode 下「小浮亏+RSI<35+MACD<0」序列断言无卖出；跌破 -25% 触发 disaster_stop；达止盈卖出。build + 部署 + 模拟盘验证
- [ ] T109 提交：`feat(quant): 只赚不亏 safe_mode（仅止盈卖出 + -25% 灾难止损兜底）`

## Phase 2 — 波段适配性闸门（P2，后续）
- [ ] T201 新 `analysis/suitability.py`：`band_suitability(symbol)`（波动/震荡·趋势强度·流动性 → 适合/不适合/未知 + 理由，阈值"适中"默认可调）
- [ ] T202 `api/quant.py` `GET /suitability/{symbol}`
- [ ] T203 `BandModal.vue` 启动前查适配性；不适合→二次确认→`force_unsuitable=true` 启动
- [ ] T204 `StrategyStart` 加 `force_unsuitable` 并入 params（记录强制开启）
- [ ] T205 单测 + 前端 build + 部署验证；提交 `feat(quant): 波段适配性闸门 + 强制开启确认`

## 生效范围（Clarify）
safe_mode 即时对当前持仓生效：通过表单开启即 stop→start（引擎会对账接管现有持仓并按 safe_mode 管理，不再亏损止损）。
