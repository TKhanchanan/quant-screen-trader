import { resolve } from 'node:path'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'electron-vite'

const bundledDependencies = ['@quant-screen-trader/shared-types', 'zod']

export default defineConfig({
  main: {
    build: {
      externalizeDeps: { exclude: bundledDependencies },
      rollupOptions: {
        input: resolve(import.meta.dirname, 'electron/main/index.ts'),
        output: {
          format: 'cjs',
          entryFileNames: '[name].cjs'
        }
      }
    }
  },
  preload: {
    build: {
      externalizeDeps: false,
      rollupOptions: {
        input: resolve(import.meta.dirname, 'electron/preload/index.ts'),
        output: {
          format: 'cjs',
          entryFileNames: '[name].cjs'
        }
      }
    }
  },
  renderer: {
    root: resolve(import.meta.dirname, 'src/renderer'),
    plugins: [react()]
  }
})
