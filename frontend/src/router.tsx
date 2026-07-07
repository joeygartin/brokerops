import {
  Link,
  Outlet,
  createRootRoute,
  createRoute,
  createRouter,
  redirect,
} from "@tanstack/react-router";
import ApprovalDetail from "./ApprovalDetail";
import ApprovalsInbox from "./ApprovalsInbox";
import AuditTrail from "./AuditTrail";
import { useAuth } from "./authContext";
import { takePostLoginRedirect } from "./auth";
import ListingDetail from "./ListingDetail";
import ListingsBoard from "./ListingsBoard";
import { BackLink, CenteredMessage } from "./routeElements";
import TransactionDetailPage from "./TransactionDetailPage";
import TransactionsBoard from "./TransactionsBoard";

// URL-addressable app shell (BOP-025). The four flat tabs became top-level routes
// so every entity is linkable (a notification email can point at one approval)
// and a refresh keeps context. The route tree is code-based (no file-based
// codegen step) and fully typed; the router is created per-mount in App so it
// reads the URL *after* the auth bootstrap has scrubbed any sign-in token.

const NAV = [
  { to: "/listings", label: "Listings" },
  { to: "/transactions", label: "Transactions" },
  { to: "/approvals", label: "Approvals" },
  { to: "/audit", label: "Audit trail" },
] as const;

const NAV_BASE_STYLE = {
  padding: "0.5rem 1.2rem",
  borderRadius: 6,
  border: "1px solid #d0d7de",
  background: "#fff",
  color: "#24292f",
  textDecoration: "none",
  cursor: "pointer",
} as const;

const NAV_ACTIVE_STYLE = { ...NAV_BASE_STYLE, background: "#24292f", color: "#fff" } as const;

function NavTab({ to, label }: { to: string; label: string }) {
  return (
    <Link to={to} style={NAV_BASE_STYLE} activeProps={{ style: NAV_ACTIVE_STYLE }}>
      {label}
    </Link>
  );
}

// The app shell: header (identity + sign-out) and nav, with the active view in
// the outlet. Role-based access is a UI mirror of the API's require_role, which
// gates only *writes* — every read is viewer-open (ADR-0009 keeps all tabs
// visible to all roles, hiding only controls). So there is deliberately no
// route-level page lock here; the mirror lives at the control level, gated
// in-card by `hasRole` (approve = admin, start-workflow / outbound-call =
// operator). Route-level guards arrive with BOP-030's genuinely role-shaped
// surfaces (and any accompanying server-side read gate), not before.
function RootLayout() {
  const { email, role, signOut } = useAuth();

  return (
    <main
      style={{
        fontFamily: "system-ui",
        padding: "2rem",
        background: "#f6f8fa",
        minHeight: "100vh",
      }}
    >
      {email && (
        <div
          style={{
            display: "flex",
            justifyContent: "flex-end",
            alignItems: "center",
            gap: "0.6rem",
            fontSize: "0.85rem",
            color: "#57606a",
          }}
        >
          <span>{email}</span>
          {role && (
            <span
              style={{
                textTransform: "uppercase",
                fontSize: "0.7rem",
                fontWeight: 600,
                letterSpacing: "0.03em",
                padding: "0.1rem 0.5rem",
                borderRadius: 999,
                background: "#eaeef2",
                color: "#57606a",
              }}
            >
              {role}
            </span>
          )}
          <button
            onClick={signOut}
            style={{
              padding: "0.2rem 0.7rem",
              borderRadius: 6,
              border: "1px solid #d0d7de",
              background: "#fff",
              color: "#24292f",
              cursor: "pointer",
            }}
          >
            Sign out
          </button>
        </div>
      )}
      <h1 style={{ textAlign: "center" }}>brokerops</h1>
      <nav
        style={{ display: "flex", gap: "0.6rem", justifyContent: "center", margin: "1rem 0 2rem" }}
      >
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
    <div style={{ textAlign: "center" }}>
      <BackLink to="/listings" label="Back to listings" />
      <CenteredMessage title="Page not found.">
        <p style={{ fontSize: "0.85rem", margin: 0 }}>That link doesn't point anywhere here.</p>
      </CenteredMessage>
    </div>
  );
}

const rootRoute = createRootRoute({ component: RootLayout, notFoundComponent: NotFound });

// "/" is never a destination: it restores the deep link saved before login
// (BOP-025), falling back to the listings board.
const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  beforeLoad: () => {
    const dest = takePostLoginRedirect();
    if (dest) throw redirect({ href: dest });
    throw redirect({ to: "/listings" });
  },
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
