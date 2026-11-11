import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import electron from 'vite-plugin-electron/simple'
import path from 'path'
import { createHash } from 'node:crypto'

export default defineConfig({
  plugins: [
    react(),
    {
      name: 'firekb-content-security-policy',
      apply: 'build',
      transformIndexHtml(html) {
        const hashes = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)]
          .map(([, script]) => `'sha256-${createHash('sha256').update(script).digest('base64')}'`)
        const policy = ["default-src 'self'", `script-src 'self' ${hashes.join(' ')}`,
          "style-src 'self' 'unsafe-inline'", "img-src 'self' data:", "connect-src 'none'",
          "object-src 'none'", "frame-src 'none'", "base-uri 'none'", "form-action 'none'"].join('; ')
        return [{ tag: 'meta', attrs: { 'http-equiv': 'Content-Security-Policy', content: policy }, injectTo: 'head-prepend' }]
      },
    },
    // 主进程与预加载脚本的构建入口。
    // 原先只有 renderer 被构建，package.json 里 "main": "dist/main/main.js" 指向的文件
    // 从未生成（tsconfig 是 noEmit），Electron 因此无法启动。产物与 main 字段对齐。
    electron({
      main: {
        entry: 'src/main/main.ts',
        vite: {
          build: {
            outDir: 'dist/main',
            rollupOptions: { external: ['electron'] }
          }
        }
      },
      preload: {
        input: 'src/main/preload.ts',
        vite: {
          build: {
            outDir: 'dist/main',
            rollupOptions: { external: ['electron'] }
          }
        }
      }
    })
  ],
  base: './',
  build: {
    outDir: 'dist/renderer',
    emptyOutDir: true
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src')
    }
  },
  server: {
    port: 5173
  }
})
