import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Listen on all interfaces (IPv4 + IPv6) so the dev server is reachable
    // via 127.0.0.1, localhost, or LAN IP — not just the IPv6 loopback.
    host: true,
  },
  build: {
    // Drop Vite's inline module-preload polyfill so the strict Content-Security-Policy
    // (script-src 'self', no 'unsafe-inline') in vercel.json doesn't block it.
    // Modern browsers support <link rel="modulepreload"> natively.
    modulePreload: { polyfill: false },
  },
})
