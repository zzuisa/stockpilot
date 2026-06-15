import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 相对 base：使产物在 `/manage/` 直连端口 与 `/stockpilot/manage/` nginx 子路径
// 两种访问方式下都能正确解析静态资源（配合 hash 路由，无需服务端重写规则）。
export default defineConfig({
  base: './',
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  build: {
    outDir: 'dist',
    chunkSizeWarningLimit: 1200,
    sourcemap: false,
  },
  server: {
    port: 5173,
    // 本地开发时把 API 代理到集群暴露的 NodePort
    proxy: {
      '/api': { target: 'http://localhost:30810', changeOrigin: true },
      '/health': { target: 'http://localhost:30810', changeOrigin: true },
    },
  },
})
