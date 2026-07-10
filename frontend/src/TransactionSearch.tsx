import { Link } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { type TransactionSearchRow } from "./client";
import { useTransactionSearch } from "./hooks/transactions";
import { CenteredMessage, PERMALINK_CLASS } from "./routeElements";

// The viewer's home (BOP-030): find an active transaction by listing key, party
// name, or property address. Search is submitted (not per-keystroke) so a blank
// box never fires and each term caches. Results link to the transaction hub.

function ResultCard({ row }: { row: TransactionSearchRow }) {
  const { transaction, property_address } = row;
  return (
    <Card as="article" className="mx-auto mb-3 max-w-[760px] text-left">
      <CardContent className="p-4">
        <div className="flex items-baseline justify-between gap-3">
          <strong>
            {transaction.id} — {transaction.listing_key}
          </strong>
          <Badge variant="secondary" className="uppercase">
            {transaction.stage.replace(/_/g, " ")}
          </Badge>
        </div>
        {property_address && (
          <div className="mt-1 text-sm text-muted-foreground">{property_address}</div>
        )}
        <div className="mt-1 text-xs text-muted-foreground">
          {(transaction.parties ?? [])
            .map((party) => `${party.name} (${party.role.replace(/_/g, " ")})`)
            .join(" · ")}
        </div>
        <div className="mt-2">
          <Link
            to="/transactions/$id"
            params={{ id: transaction.id }}
            className={PERMALINK_CLASS}
          >
            Open ↗
          </Link>
        </div>
      </CardContent>
    </Card>
  );
}

export default function TransactionSearch() {
  const [term, setTerm] = useState("");
  const [submitted, setSubmitted] = useState("");
  const query = submitted.trim();
  const { data: rows = [], error, isPending, isFetching } = useTransactionSearch(query, {
    enabled: query.length > 0,
  });

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    setSubmitted(term);
  };

  return (
    <>
      <h2 className="mb-4 text-center text-xl font-semibold">Search transactions</h2>
      <form onSubmit={onSubmit} className="mb-5 flex justify-center gap-2">
        <Input
          value={term}
          onChange={(event) => setTerm(event.target.value)}
          placeholder="Address, listing key, or contact name"
          aria-label="Search transactions"
          className="min-w-[320px] max-w-md"
        />
        <Button type="submit">Search</Button>
      </form>
      {error && <p className="text-center text-destructive">{String(error)}</p>}
      {query.length === 0 ? (
        <CenteredMessage title="Search your active transactions.">
          <p className="m-0 text-sm">Find a deal by property address, listing key, or a party's name.</p>
        </CenteredMessage>
      ) : isPending || isFetching ? (
        <p className="text-center text-muted-foreground">Searching…</p>
      ) : rows.length === 0 ? (
        <CenteredMessage title={`No transactions match "${query}".`}>
          <p className="m-0 text-sm">Try a listing key, a property address, or a contact name.</p>
        </CenteredMessage>
      ) : (
        rows.map((row) => <ResultCard key={row.transaction.id} row={row} />)
      )}
    </>
  );
}
