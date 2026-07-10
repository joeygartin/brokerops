import {
  Link,
  Navigate,
  Outlet,
  createRootRoute,
  createRoute,
  createRouter,
  redirect,
} from "@tanstack/react-router";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import ApprovalDetail from "./ApprovalDetail";
import ApprovalsInbox from "./ApprovalsInbox";
import AuditTrail from "./AuditTrail";
import { useAuth } from "./authContext";
import { takePostLoginRedirect } from "./auth";
import DeadlineQueue from "./DeadlineQueue";
import ListingDetail from "./ListingDetail";
import ListingsBoard from "./ListingsBoard";
import { homeFor } from "./roles";
import { BackLink, CenteredMessage } from "./routeElements";
import TransactionDetailPage from "./TransactionDetailPage";
import TransactionSearch from "./TransactionSearch";
import TransactionsBoard from "./TransactionsBoard";

// URL-addressable app shell (BOP-025). The four flat tabs became top-level routes
// so every entity is linkable (a notification email can point at one approval)
// and a refresh keeps context. The route tree is code-based (no file-based
// codegen step) and fully typed; the router is created per-mount in App so it
// reads the URL *after* the auth bootstrap has scrubbed any sign-in token.

const NAV = [
  { to: "/listings", label: "Listings" },
  { to: "/transactions", label: "Transactions" },
  { to: "/deadlines", label: "Deadlines" },
  { to: "/approvals", label: "Approvals" },
  { to: "/search", label: "Search" },
  { to: "/audit", label: "Audit trail" },
] as const;

// TanStack Router concatenates `className` + the active/inactive `className`
// verbatim (no tailwind-merge), so colour utilities live entirely in the
// active/inactive sets — never the shared base — to keep exactly one `bg-*`
// applied per state. The base carries only non-conflicting layout + focus.
const NAV_BASE_CLASS =
  "rounded-md border px-4 py-2 text-sm font-medium no-underline transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background";
const NAV_INACTIVE_CLASS = "border-border bg-card text-foreground hover:bg-muted";
const NAV_ACTIVE_CLASS = "border-strong bg-strong text-strong-foreground";

function NavTab({ to, label }: { to: string; label: string }) {
  return (
    <Link
      to={to}
      className={NAV_BASE_CLASS}
      activeProps={{ className: NAV_ACTIVE_CLASS }}
      inactiveProps={{ className: NAV_INACTIVE_CLASS }}
    >
      {label}
    </Link>
  );
}

// The app shell: header (identity + sign-out) and nav, with the active view in
// the outlet. Role-based access is a UI mirror of the API's require_role, which
// gates only *writes* — every read is viewer-open (ADR-0009 keeps all tabs
// visible to all roles, hiding only controls). BOP-030 added genuinely
// role-shaped *home* surfaces — "/" lands each role on their work (see
// HomeRedirect) — but kept every route reachable: no route-level page lock, so
// manual navigation to any tab is unchanged. The write mirror still lives at the
// control level, gated in-card by `hasRole` (approve = admin, start-workflow /
// outbound-call = operator).
function RootLayout() {
  const { email, role, signOut } = useAuth();

  return (
    <main className="min-h-screen p-8">
      {email && (
        <div className="flex items-center justify-end gap-2 text-sm text-muted-foreground">
          <span>{email}</span>
          {role && (
            <Badge variant="secondary" className="uppercase tracking-wide">
              {role}
            </Badge>
          )}
          <Button variant="outline" size="sm" onClick={signOut}>
            Sign out
          </Button>
        </div>
      )}
      <h1 className="text-center text-3xl font-semibold tracking-tight">brokerops</h1>
      <nav className="my-6 flex flex-wrap justify-center gap-2">
        {NAV.map((item) => (
          <NavTab key={item.to} to={item.to} label={item.label} />
        ))}
      </nav>
      <Outlet />
    </main>
  );
}

// Rendered inside the shell (it is the root route's notFoundComponent), so the
// nav stays put and the user can recover without a reload.
function NotFound() {
  return (
    <div className="text-center">
      <BackLink to="/listings" label="Back to listings" />
      <CenteredMessage title="Page not found.">
        <p className="m-0 text-sm">That link doesn't point anywhere here.</p>
      </CenteredMessage>
    </div>
  );
}

const rootRoute = createRootRoute({ component: RootLayout, notFoundComponent: NotFound });

// "/" lands each role on their work (BOP-030): the broker (admin) in the approval
// inbox, the coordinator (operator) on the deadline queue, the viewer on search.
// A deep link saved before login (BOP-025) still wins over the role default.
function HomeRedirect() {
  const { role } = useAuth();
  return <Navigate to={homeFor(role)} replace />;
}

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  beforeLoad: () => {
    const dest = takePostLoginRedirect();
    if (dest) throw redirect({ href: dest });
  },
  component: HomeRedirect,
});

const listingsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/listings",
  component: ListingsBoard,
});
const listingDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/listings/$key",
  component: ListingDetail,
});
const transactionsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/transactions",
  component: TransactionsBoard,
});
const transactionDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/transactions/$id",
  component: TransactionDetailPage,
});
const deadlinesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/deadlines",
  component: DeadlineQueue,
});
const searchRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/search",
  component: TransactionSearch,
});
const approvalsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/approvals",
  component: ApprovalsInbox,
});
const approvalDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/approvals/$id",
  component: ApprovalDetail,
});
const auditRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/audit",
  component: AuditTrail,
});

export const routeTree = rootRoute.addChildren([
  indexRoute,
  listingsRoute,
  listingDetailRoute,
  transactionsRoute,
  transactionDetailRoute,
  deadlinesRoute,
  searchRoute,
  approvalsRoute,
  approvalDetailRoute,
  auditRoute,
]);

export function createAppRouter() {
  return createRouter({ routeTree });
}

declare module "@tanstack/react-router" {
  interface Register {
    router: ReturnType<typeof createAppRouter>;
  }
}
