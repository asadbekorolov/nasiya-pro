import { fileURLToPath, URL } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The Mini App is served from the same origin as the API in production (reverse
// proxy), so API calls use relative `/api/v1` URLs. In development Vite proxies them
// to the FastAPI server.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: {
    host: true,
    port: 5173,
    strictPort: true,
    // Vite rejects unknown Host headers; allow the HTTPS tunnel used by run_live.py
    // (Cloudflare quick tunnels) plus anything listed in VITE_ALLOWED_HOSTS.
    allowedHosts: [
      ".trycloudflare.com",
      ...(process.env.VITE_ALLOWED_HOSTS ?? "").split(",").map((h) => h.trim()).filter(Boolean),
    ],
    proxy: {
      "/api": { target: process.env.VITE_API_PROXY_TARGET ?? "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
  build: {
    target: "es2022",
    sourcemap: true,
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
