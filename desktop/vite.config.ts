import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";
import { execSync } from "child_process";
import pkg from "./package.json";

const host = process.env.TAURI_DEV_HOST;

// Injected into every build so the user can verify an installed .app
// is really the one they just built. Format: 1.0.0+YYYYMMDD-HHMM.<short-sha>.
// Falls back to "local" when not in a git worktree.
function resolveBuildVersion(): string {
  const now = new Date();
  const pad = (n: number) => n.toString().padStart(2, "0");
  const stamp =
    `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-` +
    `${pad(now.getHours())}${pad(now.getMinutes())}`;
  let sha = "local";
  try {
    sha = execSync("git rev-parse --short HEAD", { stdio: ["ignore", "pipe", "ignore"] })
      .toString()
      .trim() || sha;
  } catch {
    // Not in a git repo — leave "local".
  }
  let dirty = "";
  try {
    const diff = execSync("git status --porcelain", { stdio: ["ignore", "pipe", "ignore"] })
      .toString()
      .trim();
    if (diff.length > 0) dirty = "-dirty";
  } catch {
    // ignore
  }
  return `${pkg.version}+${stamp}.${sha}${dirty}`;
}

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@shared": path.resolve(__dirname, "../frontend/src"),
    },
  },
  define: {
    __BUILD_VERSION__: JSON.stringify(resolveBuildVersion()),
  },
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    host: host || false,
    hmr: host ? { protocol: "ws", host, port: 1421 } : undefined,
    watch: { ignored: ["**/src-tauri/**"] },
    proxy: {
      "/api": {
        target: "http://localhost:8087",
        changeOrigin: true,
      },
    },
  },
  envPrefix: ["VITE_", "TAURI_"],
});
