import { defineConfig } from 'vite'
import solid from 'vite-plugin-solid'

export default defineConfig({
  plugins: [solid()],
  publicDir: 'public',
  server: {
    port: 3000,
    allowedHosts: [
      'talking-realize-inspector-wiki.trycloudflare.com'
    ],
    proxy: {
      '/api': 'http://localhost:3001',
    },
    fs: {
      allow: ['.', 'public', 'src', '../../media']
    }
  },
  preview: {
    port: 3000,
    allowedHosts: [
      'talking-realize-inspector-wiki.trycloudflare.com'
    ]
  }
})
