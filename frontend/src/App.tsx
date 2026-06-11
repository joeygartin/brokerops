import { useEffect, useState } from "react";

type ListingMedia = {
  media_key: string;
  listing_key: string;
  url: string;
  order: number;
  description: string;
};

type Listing = {
  mls_id: string;
  status: "active" | "pending" | "closed";
  address: string;
  city: string;
  state: string;
  postal_code: string;
  list_price: number;
  bedrooms: number;
  bathrooms: number;
  living_area_sqft: number | null;
  year_built: number | null;
  agent_id: string;
  agent_name: string;
  remarks: string;
  modified_at: string;
  media: ListingMedia[];
};

const API_BASE = "http://localhost:8000";

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

function ListingCard({ listing }: { listing: Listing }) {
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
    </article>
  );
}

export default function App() {
  const [listings, setListings] = useState<Listing[]>([]);
  const [error, setError] = useState<string | null>(null);

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
    <main style={{ fontFamily: "system-ui", padding: "2rem", background: "#f6f8fa", minHeight: "100vh" }}>
      <h1 style={{ textAlign: "center" }}>brokerops</h1>
      <p style={{ textAlign: "center", color: "#57606a" }}>
        Listings from the mock RESO Web API, served through the MLS port.
      </p>
      {error && (
        <p style={{ textAlign: "center", color: "#cf222e" }}>
          Could not load listings: {error} — is the api running on :8000?
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
          <ListingCard key={listing.mls_id} listing={listing} />
        ))}
      </section>
    </main>
  );
}
