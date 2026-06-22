import { useState } from "react";
import ApprovalsInbox from "./ApprovalsInbox";
import ListingsBoard from "./ListingsBoard";
import TransactionsBoard from "./TransactionsBoard";
import { useAuth } from "./authContext";

type Tab = "listings" | "transactions" | "approvals";

export default function App() {
  const [tab, setTab] = useState<Tab>("listings");
  const { email, role, signOut } = useAuth();

  const tabStyle = (active: boolean) => ({
    padding: "0.5rem 1.2rem",
    borderRadius: 6,
    border: "1px solid #d0d7de",
    background: active ? "#24292f" : "#fff",
    color: active ? "#fff" : "#24292f",
    cursor: "pointer",
  });

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
        <button style={tabStyle(tab === "listings")} onClick={() => setTab("listings")}>
          Listings
        </button>
        <button style={tabStyle(tab === "transactions")} onClick={() => setTab("transactions")}>
          Transactions
        </button>
        <button style={tabStyle(tab === "approvals")} onClick={() => setTab("approvals")}>
          Approvals
        </button>
      </nav>
      {tab === "listings" && <ListingsBoard />}
      {tab === "transactions" && <TransactionsBoard />}
      {tab === "approvals" && <ApprovalsInbox />}
    </main>
  );
}
