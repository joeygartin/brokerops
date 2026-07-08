import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { useAuth } from "./authContext";
import { type Listing } from "./client";
import { useListings, useStartListingWorkflow, useStartOutboundCall } from "./hooks/listings";
import { PERMALINK_CLASS } from "./routeElements";

const price = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

const STATUS_VARIANT: Record<Listing["status"], BadgeProps["variant"]> = {
  active: "success",
  pending: "warning",
  closed: "secondary",
};

const DEMO_FEEDBACK_CONTACT = "101"; // Jordan Pike in the FUB stub

export function ListingCard({
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
    <Card as="article" className="text-left">
      <CardHeader>
        <strong className="text-lg">
          {listing.list_price != null ? price.format(listing.list_price) : "Price on request"}
        </strong>
        <Badge variant={STATUS_VARIANT[listing.status]} className="uppercase">
          {listing.status}
        </Badge>
      </CardHeader>
      <CardContent>
        <div className="mb-1">{listing.address}</div>
        <div className="text-sm text-muted-foreground">
          {listing.bedrooms != null && listing.bathrooms != null
            ? `${listing.bedrooms} bd · ${listing.bathrooms} ba`
            : "—"}
          {listing.living_area_sqft ? ` · ${listing.living_area_sqft.toLocaleString()} sqft` : ""}
          {listing.year_built ? ` · built ${listing.year_built}` : ""}
        </div>
        <div className="mt-1 text-xs text-muted-foreground">
          {listing.mls_id} — {listing.agent_name}
        </div>
        {listing.status === "active" && hasRole("operator") && (
          <div className="mt-3 flex flex-wrap gap-2">
            <Button variant="success" size="sm" onClick={startWorkflow} disabled={starting}>
              {starting ? "Starting…" : "Start marketing workflow"}
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="border-primary text-primary hover:bg-info-soft"
              onClick={startFeedbackCall}
              disabled={calling}
            >
              {calling ? "Calling…" : "Feedback call"}
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export default function ListingsBoard() {
  const { data: listings = [], error, isPending } = useListings();
  const [notice, setNotice] = useState<string | null>(null);

  return (
    <>
      {error && (
        <p className="text-center text-destructive">
          Could not load listings: {String(error)} — is the api running on :8000?
        </p>
      )}
      {isPending && <p className="text-center text-muted-foreground">Loading listings…</p>}
      {!isPending && !error && listings.length === 0 && (
        <p className="text-center text-muted-foreground">No listings found.</p>
      )}
      {notice && (
        <p className="mx-auto mb-4 max-w-2xl rounded-md bg-success-soft p-2 text-center text-success-soft-foreground">
          {notice}
        </p>
      )}
      <section className="mx-auto grid max-w-[1100px] grid-cols-[repeat(auto-fill,minmax(280px,1fr))] gap-4">
        {listings.map((listing) => (
          <div key={listing.mls_id} className="grid gap-1.5">
            <ListingCard listing={listing} onStarted={setNotice} />
            <Link
              to="/listings/$key"
              params={{ key: listing.mls_id }}
              className={cn(PERMALINK_CLASS, "block text-right")}
            >
              Open listing ↗
            </Link>
          </div>
        ))}
      </section>
    </>
  );
}
