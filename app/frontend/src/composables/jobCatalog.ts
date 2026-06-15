// 任务中文名映射 + 任务目录（与 app/main.py 的调度表保持一致）

export const JOB_LABELS: Record<string, string> = {
  t212_sync: 'T212账户同步',
  intraday: '盘中行情采集',
  daily_prices: '日线行情采集',
  community: 'T212社区采集',
  news: '新闻快讯采集',
  sentiment: 'LLM情绪分析',
  signals: '技术指标信号',
  daily_report: '生成并推送早报',
  backfill: '历史数据补全',
  expire_intents: '过期意向单清理',
}

export interface JobCatalogEntry {
  id: string
  label: string
  manual: boolean
  desc: string
  schedule: string
}

export const JOB_CATALOG: JobCatalogEntry[] = [
  {
    id: 't212_sync',
    label: 'T212账户同步',
    manual: true,
    desc: '同步 Trading212 账户持仓快照和现金余额到数据库',
    schedule: '每 30 分钟',
  },
  {
    id: 'intraday',
    label: '盘中行情采集',
    manual: true,
    desc: '获取 watchlist 股票分钟级价格 + Finnhub 实时快讯',
    schedule: '周一至周五 15:30–22:00 每15分钟',
  },
  {
    id: 'daily_prices',
    label: '日线行情采集',
    manual: true,
    desc: '收盘后拉取全部标的日线 OHLCV，补全历史缺口',
    schedule: '周一至周五 22:40',
  },
  {
    id: 'community',
    label: 'T212社区采集',
    manual: true,
    desc: '抓取 T212 社区论坛中与 watchlist 相关帖子，供 LLM 情绪分析使用',
    schedule: '周一至周五 22:50',
  },
  {
    id: 'news',
    label: '新闻快讯采集',
    manual: true,
    desc: '从 Finnhub 和 RSS 源获取 watchlist 股票相关新闻',
    schedule: '周一至周五 22:55',
  },
  {
    id: 'sentiment',
    label: 'LLM情绪分析',
    manual: true,
    desc: '调用 SiliconFlow DeepSeek 对当日新闻与社区帖子批量打分（-2 极负 → +2 极正）',
    schedule: '周一至周五 23:00',
  },
  {
    id: 'signals',
    label: '技术指标信号',
    manual: true,
    desc: '计算 RSI / MACD / 布林带等指标，生成买入/卖出信号并即时推送',
    schedule: '周一至周五 23:10',
  },
  {
    id: 'daily_report',
    label: '生成并推送早报',
    manual: true,
    desc: '按分组汇总信号、情绪、持仓数据，调用 LLM 生成中文早报推送到 Telegram/Email',
    schedule: '周一至周五 08:00',
  },
  {
    id: 'backfill',
    label: '历史数据补全',
    manual: true,
    desc: '初次启动或新增标的时，回填近 2 年日线历史数据（跳过已有数据）',
    schedule: '应用启动时自动触发',
  },
  {
    id: 'expire_intents',
    label: '过期意向单清理',
    manual: false,
    desc: '自动清理超时未确认的交易意向单（默认 TTL 30 分钟）',
    schedule: '每 5 分钟（自动）',
  },
]
