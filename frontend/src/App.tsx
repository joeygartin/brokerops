import { useState } from "react";
import ApprovalsInbox from "./ApprovalsInbox";
import ListingsBoard from "./ListingsBoard";
import TransactionsBoard from "./TransactionsBoard";

type Tab = "listings" | "transactions" | "approvals";

export default function App() {
  const [tab, setTab] = useState<Tab>("listings");

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
