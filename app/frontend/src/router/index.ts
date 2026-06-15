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
    path: '/groups',
    name: 'groups',
    component: () => import('@/views/GroupsView.vue'),
    meta: { label: '分组管理' },
  },
  {
    path: '/routes',
    name: 'routes',
    component: () => import('@/views/RoutesView.vue'),
    meta: { label: '推送路由' },
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
  { path: '/:pathMatch(.*)*', redirect: '/overview' },
]

export const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

export const NAV_ITEMS = routes
  .filter((r) => r.meta?.label)
  .map((r) => ({ name: r.name as string, label: r.meta!.label as string }))
