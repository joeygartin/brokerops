import { useState } from "react";
import { useAuth } from "./authContext";
import { type Listing } from "./client";
import { useListings, useStartListingWorkflow, useStartOutboundCall } from "./hooks/listings";

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

const DEMO_FEEDBACK_CONTACT = "101"; // Jordan Pike in the FUB stub

function ListingCard({
  listing,
  onStarted,
}: {
  listing: Listing;
  onStarted: (message: string) => void;
}) {
  const { hasRole } = useAuth();
  const startWorkflowMutation = useStartListingWorkflow();
  const startCallMutation = useStartOutboundCall();
  const starting = startWorkflowMutation.isPending;
  const calling = startCallMutation.isPending;

  const startWorkflow = async () => {
    try {
      const result = await startWorkflowMutation.mutateAsync(listing.mls_id);
      onStarted(
        result.status === "awaiting_approval"
          ? `${listing.mls_id}: marketing draft is waiting in the Approvals inbox.`
          : `${listing.mls_id}: workflow ended with status "${result.status}".`,
      );
    } catch (cause) {
      onStarted(`${listing.mls_id}: failed to start workflow — ${String(cause)}`);
    }
  };

  const startFeedbackCall = async () => {
    try {
      const result = await startCallMutation.mutateAsync({
        listingKey: listing.mls_id,
        contactId: DEMO_FEEDBACK_CONTACT,
      });
      onStarted(
        `${listing.mls_id}: feedback call ${result.call_id} placed — the transcript lands as a ` +
          `CRM note (check Approvals if the buyer is hot).`,
      );
    } catch (cause) {
      onStarted(`${listing.mls_id}: failed to place call — ${String(cause)}`);
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
        <strong style={{ fontSize: "1.15rem" }}>{listing.list_price != null ? price.format(listing.list_price) : "Price on request"}</strong>
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
        {listing.bedrooms != null && listing.bathrooms != null
          ? `${listing.bedrooms} bd · ${listing.bathrooms} ba`
          : "—"}
        {listing.living_area_sqft ? ` · ${listing.living_area_sqft.toLocaleString()} sqft` : ""}
        {listing.year_built ? ` · built ${listing.year_built}` : ""}
      </div>
      <div style={{ color: "#57606a", fontSize: "0.8rem", marginTop: "0.4rem" }}>
        {listing.mls_id} — {listing.agent_name}
      </div>
      {listing.status === "active" && hasRole("operator") && (
        <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.75rem", flexWrap: "wrap" }}>
          <button
            onClick={startWorkflow}
            disabled={starting}
            style={{
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
          <button
            onClick={startFeedbackCall}
            disabled={calling}
            style={{
              padding: "0.4rem 0.9rem",
              borderRadius: 6,
              border: "1px solid #0969da",
              background: "#fff",
              color: "#0969da",
              cursor: calling ? "wait" : "pointer",
            }}
          >
            {calling ? "Calling…" : "Feedback call"}
          </button>
        </div>
      )}
    </article>
  );
}

export default function ListingsBoard() {
  const { data: listings = [], error, isPending } = useListings();
  const [notice, setNotice] = useState<string | null>(null);

  return (
    <>
      {error && (
        <p style={{ textAlign: "center", color: "#cf222e" }}>
          Could not load listings: {String(error)} — is the api running on :8000?
        </p>
      )}
      {isPending && <p style={{ textAlign: "center", color: "#57606a" }}>Loading listings…</p>}
      {!isPending && !error && listings.length === 0 && (
        <p style={{ textAlign: "center", color: "#57606a" }}>No listings found.</p>
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
