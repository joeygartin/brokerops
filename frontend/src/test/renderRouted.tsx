import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { render, type RenderOptions } from "@testing-library/react";
import type { ReactElement } from "react";

// Renders a routed view (a board that links into its detail routes, BOP-025)
// under a fresh QueryClient and a memory router. The view mounts at "/"; the
// detail routes are registered as no-op stubs so a <Link>'s typed target and
// param interpolation resolve. RouterProvider mounts asynchronously, so callers
// assert with findBy*/waitFor (as the board specs already do).
export function renderRouted(ui: ReactElement, options?: Omit<RenderOptions, "wrapper">) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  const rootRoute = createRootRoute();
  const indexRoute = createRoute({ getParentRoute: () => rootRoute, path: "/", component: () => ui });
  const stub = (path: string) =>
    createRoute({ getParentRoute: () => rootRoute, path, component: () => null });
  const router = createRouter({
    routeTree: rootRoute.addChildren([
      indexRoute,
      stub("/listings/$key"),
      stub("/transactions/$id"),
      stub("/approvals/$id"),
    ]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  return render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
    options,
  );
}
