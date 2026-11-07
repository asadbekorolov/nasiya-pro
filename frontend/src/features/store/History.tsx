import { ArrowDownLeft, ArrowUpRight, History as HistoryIcon, SlidersHorizontal } from "lucide-react";
import { useState } from "react";

import { storeApi } from "@/api/endpoints";
import type { Transaction } from "@/api/types";
import { Button, cx, EmptyState, ErrorState, Spinner } from "@/components/ui";
import { formatDateTime, transactionTitle } from "@/lib/format";
import { formatMoney, toCents } from "@/lib/money";
import { useAsync } from "@/lib/useAsync";

function TransactionRow({ tx, currency }: { tx: Transaction; currency: string }) {
  const amount = toCents(tx.amount);
  const increasesDebt = amount > 0n;
  const Icon = tx.type === "adjustment" ? SlidersHorizontal : increasesDebt ? ArrowUpRight : ArrowDownLeft;
  return (
    <li className="flex items-center gap-3 px-4 py-3">
      <span
        className={cx(
          "inline-flex size-10 shrink-0 items-center justify-center rounded-full",
          increasesDebt ? "bg-tg-destructive/12 text-tg-destructive" : "bg-emerald-500/12 text-emerald-600",
        )}
      >
        <Icon className="size-5" aria-hidden />
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate font-medium">{transactionTitle(tx)}</p>
        <p className="text-sm text-tg-hint">{formatDateTime(tx.created_at)}</p>
      </div>
      <div className="text-right">
        <p className={cx("font-semibold", increasesDebt ? "text-tg-destructive" : "text-emerald-600")}>
          {increasesDebt ? "+" : "−"}
          {formatMoney(increasesDebt ? amount : -amount, currency)}
        </p>
        <p className="text-xs text-tg-hint">Qoldiq: {formatMoney(tx.balance_after, currency)}</p>
      </div>
    </li>
  );
}

/** Ledger history, newest first. "+" = debt grew (delivery), "−" = paid. */
export function History({ currency }: { currency: string }) {
  const first = useAsync(() => storeApi.transactions());
  const [older, setOlder] = useState<Transaction[]>([]);
  const [cursor, setCursor] = useState<number | null | undefined>(undefined);
  const [loadingMore, setLoadingMore] = useState(false);

  const nextCursor = cursor === undefined ? first.data?.next_before_sequence : cursor;
  const items = [...(first.data?.items ?? []), ...older];

  async function loadMore() {
    if (!nextCursor) return;
    setLoadingMore(true);
    try {
      const page = await storeApi.transactions(nextCursor);
      setOlder((prev) => [...prev, ...page.items]);
      setCursor(page.next_before_sequence);
    } finally {
      setLoadingMore(false);
    }
  }

  if (first.loading && !first.data) return <Spinner />;
  if (first.error) return <ErrorState message={first.error.message} onRetry={first.reload} />;
  if (items.length === 0) {
    return <EmptyState icon={<HistoryIcon />} title="Hali operatsiyalar yo'q" hint="Yetkazishlar va to'lovlar shu yerda ko'rinadi" />;
  }

  return (
    <div className="px-4 pb-6">
      <ul className="divide-y divide-tg-separator overflow-hidden rounded-2xl bg-tg-section">
        {items.map((tx) => (
          <TransactionRow key={tx.id} tx={tx} currency={currency} />
        ))}
      </ul>
      {nextCursor ? (
        <Button variant="ghost" block loading={loadingMore} onClick={loadMore} className="mt-2">
          Oldingi operatsiyalar
        </Button>
      ) : null}
    </div>
  );
}
