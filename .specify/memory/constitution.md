# StockPilot Constitution

个人量化交易与投研平台（Vue3 + naive-ui 前端 / FastAPI + Postgres 后端 / microk8s 部署）的
开发宪法。所有 spec / plan / tasks / 实现都必须遵守以下原则。

## Core Principles

### I. 实钱安全优先（NON-NEGOTIABLE）
本平台会对接 T212 真实账户下单，任何涉及**下单 / 撤单 / 资金 / 仓位**逻辑的改动：
- 必须先在 **demo 账户**验证行为，再上 live；
- 风控项（止损 stop_loss、每日成交上限 max_trades_day、并发/限流）为强制项，不得移除；
- 破坏性或不可逆操作（真实下单、删数据、改集群 DNS/CoreDNS 等）**先确认再执行**，绝不自动替用户完成。

### II. 完成前必实跑验证（NON-NEGOTIABLE）
「能编译」不等于「能用」。每个改动在声明完成前必须**真实运行验证**：
- 后端：curl 打接口 / 单测关键状态机（如机会仓、拐点、归因图）/ 触发一次真实 LLM·T212·yfinance 调用；
- 前端：`npm run build`（vue-tsc 通过）；部署后核对 rollout + `/health` + 页面实际表现；
- 失败如实上报（贴错误、说明跳过项），不得含糊称「已完成」。

### III. 复用优先、贴合现有模式
先找现有实现再写新代码：复用 `analysis/*`、`api/*` 路由范式、`components/*`、`db.get_session`、
`streamApi`/SSE 范式、主题 token 等；新代码的命名/注释/风格与周边一致；新增依赖需明确理由。

### IV. 韧性：外部依赖失败不得拖垮系统
Telegram / LLM(SiliconFlow) / T212 / yfinance / DNS 等外部依赖不可用时必须**优雅降级**：
- 启动期外部调用失败**非致命**（App 照常起）；
- 多路 LLM/接口调用要**限并发 + 退避重试**，失败给干净兜底、不把报错串塞进后续分析；
- 主循环（量化策略、SSE）中的单点异常只影响该次，不崩整体。

### V. 结果尽量落库、可缓存可回看
分析/归因/回测类结果尽量**持久化到 Postgres**，按稳定键（如 标的+时间窗）去重复用、支持历史回看；
避免同输入重复消耗 LLM/算力。

### VI. 中文优先、范围克制
面向用户的文案与 UI 使用中文；改动**聚焦当次目标**，把「nice-to-have」明确列为后续，不夹带扩张。

### VII. 版本控制纪律（每次改动必提交，NON-NEGOTIABLE）
每次有意义的改动**实跑验证通过后立即用 git 记录**，遵循 **Conventional Commits** 标准格式：
```
<type>(<scope>): <主题，祈使句，中/英均可，≤72 字>

<正文：为什么改 + 改了什么 + 影响/验证，可换行>

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
```
- `type` ∈ feat | fix | refactor | perf | chore | docs | test | build | ci | revert；`scope` 用模块名（quant / backtest / attribution / tsdb / api / frontend / deploy…）。
- **原子提交**：一次提交只做一件事；跑不过/半成品不提交。
- StockPilot 三连：**commit → push GitHub(origin/master) → `./deploy.sh code`**。
- 密钥 / 运行态 / 构建产物不入库（见 `.gitignore`）；本机个人配置（`.claude/settings.local.json`）不提交。

## 技术与安全约束
- 密钥/令牌只走环境变量 / `host-agent.env` / k8s Secret，**绝不入代码或前端**。
- 后端只监听内网（127.0.0.1:9900 等），对外经 nginx；写操作需鉴权。
- 数据源口径固定（如回测用 yfinance auto_adjust 总回报），改动需说明口径影响。

## 开发与部署流程
1. 用 spec-kit 流程推进非平凡功能：`/speckit-constitution → /speckit-specify → /speckit-clarify(可选) → /speckit-plan → /speckit-tasks → /speckit-implement`。
2. 部署：改动 → 前端 build + 后端 compile → `./deploy.sh code`（改了 requirements 会自动重装 pip 层；构建走 `--network=host` 以绕过桥接网 DNS）→ 核对滚动更新与健康。
3. 提交/部署仅在明确需要时；涉及真钱系统的重启要挑好时机、先说明影响。

## Governance
本宪法优先于临时习惯。修订需在本文件记录并更新版本与日期；每个 plan/实现应能对照本宪法自检
（尤其原则 I、II、IV）。复杂度与新增依赖必须有正当理由，否则从简。

**Version**: 1.1.0 | **Ratified**: 2026-07-02 | **Last Amended**: 2026-07-06
<!-- 1.1.0: 新增原则 VII 版本控制纪律（每次改动按 Conventional Commits 标准提交） -->
