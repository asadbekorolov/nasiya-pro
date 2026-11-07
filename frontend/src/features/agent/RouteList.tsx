import { ChevronRight, MapPin, Route, Search } from "lucide-react";
import { useState } from "react";

import type { AgentStore } from "@/api/types";
import { Card, EmptyState } from "@/components/ui";
import { formatMoney, fromCents, toCents } from "@/lib/money";

export function RouteList({ stores, onSelect }: { stores: AgentStore[]; onSelect: (store: AgentStore) => void }) {
  const [query, setQuery] = useState("");
  const needle = query.trim().toLowerCase();
  const visible = stores.filter(
    (s) => !needle || s.name.toLowerCase().includes(needle) || (s.address ?? "").toLowerCase().includes(needle),
  );
  const totalDebt = stores.reduce((sum, s) => sum + (toCents(s.current_debt) > 0n ? toCents(s.current_debt) : 0n), 0n);
  const totalOverdue = stores.reduce((sum, s) => sum + toCents(s.overdue_amount), 0n);

  if (stores.length === 0) {
    return <EmptyState icon={<Route />} title="Sizga do'kon biriktirilmagan" hint="Ulgurji sotuvchi do'konlarni biriktirgach shu yerda ko'rinadi" />;
  }

  return (
    <div className="space-y-3 px-4 pb-6">
      <Card className="grid grid-cols-2 gap-3">
        <div>
          <p className="text-sm text-tg-hint">Jami qarz</p>
          <p className="text-lg font-bold">{formatMoney(fromCents(totalDebt))}</p>
        </div>
        <div>
          <p className="text-sm text-tg-hint">Muddati o'tgan</p>
          <p className={totalOverdue > 0n ? "text-lg font-bold text-tg-destructive" : "text-lg font-bold"}>
            {formatMoney(fromCents(totalOverdue))}
          </p>
        </div>
      </Card>

      <label className="flex items-center gap-2 rounded-xl bg-tg-section px-3">
        <Search className="size-5 text-tg-hint" aria-hidden />
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Do'kon nomi yoki manzil"
          className="h-11 w-full bg-transparent text-[15px] outline-none placeholder:text-tg-hint"
        />
      </label>

      <ul className="divide-y divide-tg-separator overflow-hidden rounded-2xl bg-tg-section">
        {visible.map((store) => {
          const overdue = toCents(store.overdue_amount) > 0n;
          return (
            <li key={store.id}>
              <button
                type="button"
                onClick={() => onSelect(store)}
                className="flex w-full items-center gap-3 px-4 py-3 text-left active:bg-tg-secondary"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium">{store.name}</p>
                  {store.address && (
                    <p className="flex items-center gap-1 truncate text-sm text-tg-hint">
                      <MapPin className="size-3.5 shrink-0" aria-hidden />
                      {store.address}
                    </p>
                  )}
                </div>
                <div className="text-right">
                  <p className="font-semibold">{formatMoney(store.current_debt)}</p>
                  {overdue && (
                    <p className="text-xs text-tg-destructive">muddati o'tgan {formatMoney(store.overdue_amount)}</p>
                  )}
                </div>
                <ChevronRight className="size-5 text-tg-hint" aria-hidden />
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
