import { createRouter, createWebHashHistory, type RouteRecordRaw } from 'vue-router'

// hash 路由：在直连端口与 nginx 子路径两种部署下都无需服务端重写
const routes: RouteRecordRaw[] = [
  { path: '/', redirect: '/overview' },
  {
    path: '/overview',
    name: 'overview',
    component: () => import('@/views/OverviewView.vue'),
    meta: { label: '概览' },
  },
  {
    path: '/t212',
    name: 't212',
    component: () => import('@/views/T212View.vue'),
    meta: { label: 'T212 行情' },
  },
  {
    path: '/etf-backtest',
    name: 'etf-backtest',
    component: () => import('@/views/EtfBacktestView.vue'),
    meta: { label: 'ETF 回测' },
  },
  {
    path: '/groups',
    name: 'groups',
    component: () => import('@/views/GroupsView.vue'),
    meta: { label: '分组 / 推送' },
  },
  {
    path: '/trades',
    name: 'trades',
    component: () => import('@/views/TradesView.vue'),
    meta: { label: '交易历史' },
  },
  {
    path: '/news-llm',
    name: 'news-llm',
    component: () => import('@/views/NewsLLMView.vue'),
    meta: { label: '新闻 LLM' },
  },
  {
    path: '/daily-brief',
    name: 'daily-brief',
    component: () => import('@/views/DailyBriefView.vue'),
    meta: { label: '盘前日报' },
  },
  {
    path: '/logs',
    name: 'logs',
    component: () => import('@/views/LogsView.vue'),
    meta: { label: '推送日志' },
  },
  {
    path: '/jobs',
    name: 'jobs',
    component: () => import('@/views/JobsView.vue'),
    meta: { label: '任务' },
  },
  {
    path: '/settings',
    name: 'settings',
    component: () => import('@/views/AgentSettingsView.vue'),
    meta: { label: '设置 / 托管' },
  },
  { path: '/:pathMatch(.*)*', redirect: '/overview' },
]

export const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

export const NAV_ITEMS = routes
  .filter((r) => r.meta?.label)
  .map((r) => ({ name: r.name as string, label: r.meta!.label as string }))
