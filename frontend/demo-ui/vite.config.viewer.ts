import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'
import { copyFileSync, existsSync, mkdirSync } from 'fs'

/**
 * Vite config for standalone V5 Pipeline Viewer
 * Builds to /viewer path with minimal bundle
 */
export default defineConfig({
  plugins: [
    react(),
    // Custom plugin to rename output HTML and copy to static folder
    {
      name: 'rename-and-copy',
      closeBundle() {
        const distPath = path.resolve(__dirname, 'dist-viewer')
        const viewerHtml = path.resolve(distPath, 'viewer.html')
        const indexHtml = path.resolve(distPath, 'index.html')

        // Rename viewer.html to index.html
        if (existsSync(viewerHtml)) {
          copyFileSync(viewerHtml, indexHtml)
        }

        // Copy to static folder for local development
        const staticPath = path.resolve(__dirname, '../../static/v5-viewer')
        if (!existsSync(staticPath)) {
          mkdirSync(staticPath, { recursive: true })
        }
      },
    },
  ],
  // Serve from /viewer/ in production
  base: '/viewer/',
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  build: {
    outDir: 'dist-viewer',
    emptyOutDir: true,
    rollupOptions: {
      input: {
        main: path.resolve(__dirname, 'viewer.html'),
      },
      output: {
        entryFileNames: 'assets/[name]-[hash].js',
        chunkFileNames: 'assets/[name]-[hash].js',
        assetFileNames: 'assets/[name]-[hash].[ext]',
      },
    },
  },
  server: {
    host: '0.0.0.0',
    port: 5174,
    strictPort: true,
  },
})
