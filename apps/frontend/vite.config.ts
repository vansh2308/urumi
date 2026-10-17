import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

// In development the gateway and agent are reached through the Vite proxy, so the browser
// only talks to its own origin and neither service needs CORS enabled.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const gatewayTarget = env.GATEWAY_PROXY_TARGET || 'http://127.0.0.1:8100'
  const agentTarget = env.AGENT_PROXY_TARGET || 'http://127.0.0.1:8201'
  return {
    plugins: [react(), tailwindcss()],
    resolve: { alias: { '@': path.resolve(__dirname, './src') } },
    server: {
      port: 5173,
      proxy: {
        '/api/gateway': {
          target: gatewayTarget,
          changeOrigin: true,
          rewrite: (p) => p.replace(/^\/api\/gateway/, ''),
        },
        '/api/agent': {
          target: agentTarget,
          changeOrigin: true,
          rewrite: (p) => p.replace(/^\/api\/agent/, ''),
        },
      },
    },
  }
})
