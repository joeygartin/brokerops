import { useEffect, useState } from "react";
import { API_BASE, Listing, WorkflowRunResult } from "./types";

const price = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

const STATUS_COLORS: Record<Listing["status"], string> = {
  active: "#1a7f37",
  pending: "#9a6700",
  closed: "#57606a",
};

function ListingCard({
  listing,
  onStarted,
}: {
  listing: Listing;
  onStarted: (message: string) => void;
}) {
  const [starting, setStarting] = useState(false);

  const startWorkflow = async () => {
    setStarting(true);
    try {
      const response = await fetch(`${API_BASE}/workflows/listing-to-contract/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ listing_key: listing.mls_id }),
      });
      if (!response.ok) throw new Error(`api returned ${response.status}`);
      const result = (await response.json()) as WorkflowRunResult;
      onStarted(
        result.status === "awaiting_approval"
          ? `${listing.mls_id}: marketing draft is waiting in the Approvals inbox.`
          : `${listing.mls_id}: workflow ended with status "${result.status}".`,
      );
    } catch (cause) {
      onStarted(`${listing.mls_id}: failed to start workflow — ${String(cause)}`);
    } finally {
      setStarting(false);
    }
  };

  return (
    <article
      style={{
        border: "1px solid #d0d7de",
        borderRadius: 8,
        padding: "1rem",
        textAlign: "left",
        background: "#fff",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <strong style={{ fontSize: "1.15rem" }}>{price.format(listing.list_price)}</strong>
        <span
          style={{
            color: "#fff",
            background: STATUS_COLORS[listing.status],
            borderRadius: 999,
            padding: "0.1rem 0.6rem",
            fontSize: "0.75rem",
            textTransform: "uppercase",
          }}
        >
          {listing.status}
        </span>
      </div>
      <div style={{ margin: "0.4rem 0" }}>{listing.address}</div>
      <div style={{ color: "#57606a", fontSize: "0.9rem" }}>
        {listing.bedrooms} bd · {listing.bathrooms} ba
        {listing.living_area_sqft ? ` · ${listing.living_area_sqft.toLocaleString()} sqft` : ""}
        {listing.year_built ? ` · built ${listing.year_built}` : ""}
      </div>
      <div style={{ color: "#57606a", fontSize: "0.8rem", marginTop: "0.4rem" }}>
        {listing.mls_id} — {listing.agent_name}
      </div>
      {listing.status === "active" && (
        <button
          onClick={startWorkflow}
          disabled={starting}
          style={{
            marginTop: "0.75rem",
            padding: "0.4rem 0.9rem",
            borderRadius: 6,
            border: "1px solid #1a7f37",
            background: starting ? "#f6f8fa" : "#2da44e",
            color: starting ? "#57606a" : "#fff",
            cursor: starting ? "wait" : "pointer",
          }}
        >
          {starting ? "Starting…" : "Start marketing workflow"}
        </button>
      )}
    </article>
  );
}

export default function ListingsBoard() {
  const [listings, setListings] = useState<Listing[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API_BASE}/listings`)
      .then((response) => {
        if (!response.ok) throw new Error(`api returned ${response.status}`);
        return response.json() as Promise<Listing[]>;
      })
      .then(setListings)
      .catch((cause) => setError(String(cause)));
  }, []);

  return (
    <>
      {error && (
        <p style={{ textAlign: "center", color: "#cf222e" }}>
          Could not load listings: {error} — is the api running on :8000?
        </p>
      )}
      {notice && (
        <p
          style={{
            textAlign: "center",
            color: "#1a7f37",
            background: "#dafbe1",
            borderRadius: 6,
            padding: "0.5rem",
            maxWidth: 700,
            margin: "0 auto 1rem",
          }}
        >
          {notice}
        </p>
      )}
      <section
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
          gap: "1rem",
          maxWidth: 1100,
          margin: "0 auto",
        }}
      >
        {listings.map((listing) => (
          <ListingCard key={listing.mls_id} listing={listing} onStarted={setNotice} />
        ))}
      </section>
    </>
  );
}
