import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true,
    allowedHosts: [".ngrok-free.dev", ".ngrok-free.app", ".ngrok.io", ".ngrok.app"],
    proxy: {
      "/api": {
        target: "http://localhost:8087",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
  },
});
