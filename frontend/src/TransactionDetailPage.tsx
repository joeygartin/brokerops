import { useParams } from "@tanstack/react-router";
import { ApiError } from "./api";
import { TransactionCard } from "./TransactionsBoard";
import { useTransaction } from "./hooks/transactions";
import { BackLink, CenteredMessage } from "./routeElements";

// Deep-linkable single transaction (BOP-025). Resolves through the keyed GET
// /transactions/{id}, so a transaction in any stage deep-links (the board query
// returns only active ones); an unknown id 404s into a clean not-found state.
export default function TransactionDetailPage() {
  const { id } = useParams({ from: "/transactions/$id" });
  const { data: detail, error, isPending } = useTransaction(id);
  const notFound = error instanceof ApiError && error.status === 404;

  return (
    <section>
      <div style={{ textAlign: "center" }}>
        <BackLink to="/transactions" label="All transactions" />
      </div>
      {isPending && <CenteredMessage title="Loading transaction…" />}
      {notFound && (
        <CenteredMessage title={`No transaction found for ${id}.`}>
          <p style={{ fontSize: "0.85rem", margin: 0 }}>The link may be out of date.</p>
        </CenteredMessage>
      )}
      {error && !notFound && (
        <p style={{ textAlign: "center", color: "#cf222e" }}>{String(error)}</p>
      )}
      {detail && <TransactionCard detail={detail} />}
    </section>
  );
}
