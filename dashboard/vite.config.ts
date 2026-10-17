/// <reference types="vitest/config" />
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

// `npm run dev` proxies the API to a gateway on :8080 (`jarvis-gateway --fake --scenario` is enough).
const gateway = process.env.JARVIS_GATEWAY ?? "http://127.0.0.1:8080";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  server: {
    proxy: {
      "/api": { target: gateway, ws: true, changeOrigin: true },
      "/metrics": { target: gateway },
    },
  },
  build: {
    outDir: "dist",
    chunkSizeWarningLimit: 1500, // ECharts
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["src/test-setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
