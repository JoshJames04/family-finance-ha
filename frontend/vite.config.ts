import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// base "./" keeps asset URLs relative so the app works under a Home
// Assistant ingress path prefix without knowing it at build time.
export default defineConfig({
  base: "./",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Inside docker-compose the backend is the `backend` service
      // hostname — localhost only works for a same-machine dev run.
      "/api": {
        target:
          process.env.VITE_API_PROXY_TARGET ?? "http://localhost:8099",
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    restoreMocks: true,
    exclude: ["e2e/**", "**/node_modules/**"],
  },
});
