/**
 * Normal mode = decorated translucent floating window with the full web
 * UI inside (sidebar + every route). The frontend's `App` component is
 * imported as-is via the @shared alias, so any new route added on the
 * web side automatically shows up here too — no parallel route table to
 * maintain. We wrap it in HashRouter rather than BrowserRouter because
 * Tauri serves the bundle from `tauri://localhost/` and history-based
 * routing doesn't survive a window reload there.
 *
 * The sidecar URL has already been negotiated by DesktopApp's mode-detection
 * + start_sidecar dance before this component mounts, so every fetch
 * goes through the patched `api-adapter` automatically.
 */
import { HashRouter } from "react-router-dom";
import App from "@shared/App";
import "@shared/styles/tokens.css";

export default function NormalModeApp() {
  return (
    <HashRouter
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <App />
    </HashRouter>
  );
}
