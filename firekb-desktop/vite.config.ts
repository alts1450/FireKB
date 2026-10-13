import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import electron from 'vite-plugin-electron/simple'
import path from 'path'

export default defineConfig({
  plugins: [
    react(),
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
