import { initApiAdapter, getLang } from "./api-adapter";
import { initDesktopI18n } from "./i18n";

async function boot() {
  // Patch fetch BEFORE React loads any API client
  await initApiAdapter();
  initDesktopI18n(getLang());

  // Dynamic import so @shared/api/client sees the patched fetch
  const React = await import("react");
  const ReactDOM = await import("react-dom/client");
  const { QueryClient, QueryClientProvider } = await import(
    "@tanstack/react-query"
  );
  const { default: DesktopApp } = await import("./DesktopApp");

  const qc = new QueryClient({
    defaultOptions: {
      queries: { refetchOnWindowFocus: false, retry: 1 },
    },
  });

  ReactDOM.createRoot(document.getElementById("root")!).render(
    React.createElement(
      QueryClientProvider,
      { client: qc },
      React.createElement(DesktopApp),
    ),
  );
}

boot();
