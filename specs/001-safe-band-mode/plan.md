# Implementation Plan: 只赚不亏止损模式 + 波段选股适配性判断

**Branch**: `001-safe-band-mode` | **Date**: 2026-07-06 | **Spec**: `specs/001-safe-band-mode/spec.md`

## Summary
两块：**(US1) 只赚不亏模式**——策略级开关，开启后持仓不因浮亏卖出，仅在止盈成交（根因是现有 `signal_stop`：任何小浮亏 + RSI<35 + MACD<0 就市价卖，正是 NIO 5.02→5.01 的元凶）；**(US2/US3) 波段适配性闸门**——用已采集指标判断标的是否适合波段，不适合则二次确认可强制开启。默认关闭、向后兼容。

## Technical Context
**Language**: Python 3.12（后端）/ TypeScript+Vue3（前端）
**Primary Deps**: FastAPI、SQLAlchemy、现有 `quant.py` 引擎、`analysis/{indicators,trend}.py`、naive-ui（复用，不新增依赖/数据源）
**Storage**: Postgres（tsdb）；复用 `IndicatorDaily`/`Price`，策略参数存 `QuantStrategy.params`
**Testing**: 纯 Python 单测（止损分支门控、适配性判据）+ 模拟盘实跑
**Project Type**: web-service（k8s 部署，`./deploy.sh code`）
**Constraints**: 向后兼容（默认关闭行为不变）；不阻塞每 5s 策略主循环

## Constitution Check
*GATE：Phase 0 前必过，设计后复查。*
- **I 实钱安全（重点）**：只赚不亏降低小亏，但引入「长期套牢/资金占用」风险 → 必须①默认关闭②状态与通知显性风险提示③保留可选「灾难止损」上限兜底（见待澄清）。仅对模拟盘/验证期推荐。✅ 有对策
- **II 完成前必验证**：单测 + 模拟盘回放零亏损卖出 + 适配性方向正确。✅
- **III 复用优先**：复用 indicators/trend/引擎，零新依赖零新数据源。✅
- **IV 韧性**：适配性接口失败要降级为「未知」并走 US3 确认，不拖垮开仓。✅
- **VII 提交纪律**：按 Conventional Commits 分阶段提交。✅

## 设计

### US1 只赚不亏（P1，先做，直接止血）
- 参数：`safe_mode: bool=False`（`DEFAULT_PARAMS` + `api/quant.py:StrategyStart` + 表单开关）；可选 `disaster_stop_pct`（默认 0=关，见待澄清）。
- 引擎 `app/quant.py` 主循环「状态③ 持仓」分支门控：
  - `safe_mode` 开 → **跳过** `hard_stop`（`gain<=-stop_loss`）与 `signal_stop`（`gain<0 & rsi<35 & macd<0`）两条亏损平仓路径；只保留 `profit_limit` 挂止盈。
  - 若 `disaster_stop_pct>0` 且浮亏 ≤ -disaster_stop_pct → 仍市价止损兜底（防退市/黑天鹅）。
- `status()` + `_notify` 增「只赚不亏(safe_mode)」标示与风险提示（FR-003）。

### US2 适配性判断（P2）
- 新 `app/analysis/suitability.py`：`band_suitability(symbol) -> {suitable, score, reasons[]}`，取 `IndicatorDaily` + `trend_analysis()` + `Price`：
  - 震荡/波动度（利于波段）：`ATR% = atr/close`、近 N 日振幅、布林带宽；
  - 趋势强度（强单边→不利波段）：trend `regime` + `spread` 强度；
  - 流动性：`vol_ratio` / 日均额下限。
  - 加权打分 + 阈值 → 适合/不适合/未知（数据不足）。阈值见待澄清。
- API：`GET /api/v1/quant/suitability/{symbol}`（加到 `api/quant.py`）。

### US3 提醒 + 强制（P2）
- `BandModal.vue` 启动前调 suitability；「不适合」→ naive-ui `dialog` 二次确认；确认 → 带 `force_unsuitable=true` 启动。
- `StrategyStart` 加 `force_unsuitable: bool`，落入 `params`（FR-008）。

## 关键文件
- 改：`app/quant.py`（safe_mode 门控 + status/notify）、`app/api/quant.py`（safe_mode/force_unsuitable/suitability 路由）、`app/frontend/src/components/BandModal.vue`（开关 + 适配性检查 + 确认）、`app/frontend/src/api/{endpoints,types}.ts`
- 新：`app/analysis/suitability.py`
- 复用：`app/analysis/{indicators,trend}.py`、`app/models.py`

## Phasing
1. **Phase 1（P1）**：US1 safe_mode（引擎门控 + 参数 + 表单开关 + 单测）→ 立刻消除无谓小亏。
2. **Phase 2（P2）**：US2 suitability 引擎 + API → US3 前端确认闸门。

## Open Questions（→ `/speckit-clarify` 澄清后再 `/speckit-tasks`）
- 适配性指标与阈值（ATR%、振幅、regime、量比下限）具体取值。
- 是否保留「灾难止损」上限及其数值（严格零止损 vs 兜底 -X%）。
- safe_mode 对**已运行**策略即时生效还是仅新开仓。
- 是否加「持仓滞留时长」软提醒。

## Verification
- 单测：构造 safe_mode 下「小浮亏 + RSI<35 + MACD<0」序列 → 断言**无卖出**；达止盈 → 卖出。
- 适配性：强单边样本判「不适合」、区间震荡样本判「适合」。
- 前端 `npm run build`；模拟盘对一标的开 safe_mode 实跑，验证零亏损卖出 + 不适合确认弹窗。
- 分阶段 Conventional Commits（`feat(quant): safe_mode…` / `feat(quant): band suitability gate…`）。
