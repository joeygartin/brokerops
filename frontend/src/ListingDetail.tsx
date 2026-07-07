import { useParams } from "@tanstack/react-router";
import { useState } from "react";
import { ApiError } from "./api";
import { ListingCard } from "./ListingsBoard";
import { useListing } from "./hooks/listings";
import { BackLink, CenteredMessage } from "./routeElements";

// Deep-linkable single listing (BOP-025). Resolves the key through the keyed
// GET /listings/{key} — not a find() over the board query — so any real listing
// deep link works, not just those on the default search page. An unknown key
// 404s (ApiError) into a clean not-found state; never a crash.
export default function ListingDetail() {
  const { key } = useParams({ from: "/listings/$key" });
  const { data: listing, error, isPending } = useListing(key);
  const [notice, setNotice] = useState<string | null>(null);
  const notFound = error instanceof ApiError && error.status === 404;

  return (
    <section style={{ maxWidth: 700, margin: "0 auto", textAlign: "center" }}>
      <BackLink to="/listings" label="All listings" />
      {isPending && <CenteredMessage title="Loading listing…" />}
      {notFound && (
        <CenteredMessage title={`No listing found for ${key}.`}>
          <p style={{ fontSize: "0.85rem", margin: 0 }}>
            It may have closed or the link is out of date.
          </p>
        </CenteredMessage>
      )}
      {error && !notFound && (
        <p style={{ color: "#cf222e" }}>Could not load listing: {String(error)}</p>
      )}
      {notice && (
        <p
          style={{
            color: "#1a7f37",
            background: "#dafbe1",
            borderRadius: 6,
            padding: "0.5rem",
            margin: "0 0 1rem",
          }}
        >
          {notice}
        </p>
      )}
      {listing && <ListingCard listing={listing} onStarted={setNotice} />}
    </section>
  );
}
