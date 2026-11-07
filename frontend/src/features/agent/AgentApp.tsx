import { Banknote, Phone, ShoppingCart } from "lucide-react";
import { useCallback, useState } from "react";

import { agentApi } from "@/api/endpoints";
import type { AgentStore, PaymentResult } from "@/api/types";
import { Button, Card, ErrorState, Screen, Spinner, TopBar } from "@/components/ui";
import { CartSheet } from "@/features/shared/CartSheet";
import { CatalogView } from "@/features/shared/CatalogView";
import { formatMoney, toCents } from "@/lib/money";
import { useAsync } from "@/lib/useAsync";
import { useBackButton } from "@/telegram/twa";

import { CollectCash } from "./CollectCash";
import { RouteList } from "./RouteList";

type View =
  | { kind: "route" }
  | { kind: "store"; storeId: string }
  | { kind: "collect"; storeId: string }
  | { kind: "order"; storeId: string };

export default function AgentApp() {
  const [view, setView] = useState<View>({ kind: "route" });
  const [cartOpen, setCartOpen] = useState(false);
  const stores = useAsync(agentApi.stores);
  // Optimistic balance right after a payment, until the reload lands.
  const [balanceOverride, setBalanceOverride] = useState<Record<string, string>>({});

  const list = (stores.data ?? []).map((s) =>
    balanceOverride[s.id] ? { ...s, current_debt: balanceOverride[s.id]! } : s,
  );
  const current = "storeId" in view ? list.find((s) => s.id === view.storeId) : undefined;

  const back = useCallback(() => {
    setCartOpen(false);
    setView((v) => (v.kind === "collect" || v.kind === "order" ? { kind: "store", storeId: v.storeId } : { kind: "route" }));
  }, []);
  useBackButton(view.kind === "route" ? null : back);

  function paymentDone(store: AgentStore, result: PaymentResult | null) {
    if (result) {
      setBalanceOverride((prev) => ({ ...prev, [store.id]: result.store_balance }));
      void stores.reload().then(() => setBalanceOverride({}));
    }
    setView({ kind: "store", storeId: store.id });
  }

  if (stores.loading && !stores.data) return <Spinner />;
  if (stores.error) return <ErrorState message={stores.error.message} onRetry={stores.reload} />;

  if (view.kind === "route" || !current) {
    return (
      <Screen>
        <TopBar title="Marshrut" subtitle={`${list.length} ta do'kon`} />
        <RouteList stores={list} onSelect={(s) => setView({ kind: "store", storeId: s.id })} />
      </Screen>
    );
  }

  if (view.kind === "collect") {
    return (
      <Screen>
        <TopBar title="Naqd pul qabul qilish" subtitle={current.name} />
        <CollectCash store={current} onDone={(result) => paymentDone(current, result)} />
      </Screen>
    );
  }

  if (view.kind === "order") {
    const scope = `agent:${current.id}`;
    return (
      <Screen>
        <TopBar title="Buyurtma" subtitle={current.name} />
        <CatalogView scope={scope} loadCatalog={agentApi.catalog} onOpenCart={() => setCartOpen(true)} />
        <CartSheet
          scope={scope}
          title={`Buyurtma · ${current.name}`}
          open={cartOpen}
          onClose={() => setCartOpen(false)}
          submit={(lines, key) => agentApi.createOrder(current.id, lines, key)}
          onOrdered={() => setView({ kind: "store", storeId: current.id })}
        />
      </Screen>
    );
  }

  const overdue = toCents(current.overdue_amount) > 0n;
  return (
    <Screen>
      <TopBar title={current.name} subtitle={current.address ?? undefined} />
      <div className="space-y-3 px-4 pb-6">
        <Card>
          <p className="text-sm text-tg-hint">Joriy qarz</p>
          <p className="text-3xl font-bold">{formatMoney(current.current_debt)}</p>
          {overdue && (
            <p className="mt-1 text-sm text-tg-destructive">
              Muddati o'tgan: {formatMoney(current.overdue_amount)}
            </p>
          )}
          <p className="mt-2 text-sm text-tg-hint">Kredit limiti: {formatMoney(current.credit_limit)}</p>
        </Card>
        <Button block icon={<Banknote className="size-5" />} onClick={() => setView({ kind: "collect", storeId: current.id })}>
          Naqd pul qabul qilish
        </Button>
        <Button
          block
          variant="secondary"
          icon={<ShoppingCart className="size-5" />}
          onClick={() => setView({ kind: "order", storeId: current.id })}
        >
          Joyida buyurtma kiritish
        </Button>
        {current.phone && (
          <a
            href={`tel:${current.phone}`}
            className="flex min-h-12 items-center justify-center gap-2 rounded-xl text-[15px] font-semibold text-tg-link"
          >
            <Phone className="size-5" aria-hidden />
            {current.phone}
          </a>
        )}
        <Button block variant="ghost" onClick={() => setView({ kind: "route" })}>
          Marshrutga qaytish
        </Button>
      </div>
    </Screen>
  );
}
