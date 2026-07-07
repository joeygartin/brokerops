import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderOptions } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";

// Renders a component under a fresh QueryClient so hooks that use TanStack Query
// (BOP-024) have a provider. A new client per call keeps tests isolated (no
// cross-test cache leakage); retries are off so an error case fails fast instead
// of stalling the test on backoff. For views that render routed <Link>s use
// renderRouted (BOP-025) instead — this helper deliberately stays router-free so
// components that render synchronously can be queried without an await.
export function renderWithClient(ui: ReactElement, options?: Omit<RenderOptions, "wrapper">) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  }
  return render(ui, { wrapper: Wrapper, ...options });
}
