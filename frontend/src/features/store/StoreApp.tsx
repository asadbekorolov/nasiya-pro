import { History as HistoryIcon, LayoutDashboard, ShoppingBag } from "lucide-react";
import { useState } from "react";

import { storeApi } from "@/api/endpoints";
import { BottomTabs, ErrorState, Screen, Spinner, TopBar, type TabItem } from "@/components/ui";
import { CartSheet } from "@/features/shared/CartSheet";
import { CatalogView } from "@/features/shared/CatalogView";
import { useAsync } from "@/lib/useAsync";
import { useCart } from "@/state/cart";

import { Dashboard } from "./Dashboard";
import { History } from "./History";

type Tab = "dashboard" | "catalog" | "history";
const CART_SCOPE = "store";

export default function StoreApp() {
  const [tab, setTab] = useState<Tab>("dashboard");
  const [cartOpen, setCartOpen] = useState(false);
  const me = useAsync(storeApi.me);
  const cart = useCart(CART_SCOPE);

  const tabs: TabItem<Tab>[] = [
    { id: "dashboard", label: "Asosiy", icon: <LayoutDashboard /> },
    { id: "catalog", label: "Katalog", icon: <ShoppingBag />, badge: cart.count || undefined },
    { id: "history", label: "Tarix", icon: <HistoryIcon /> },
  ];

  if (me.loading && !me.data) return <Spinner />;
  if (me.error || !me.data) {
    return <ErrorState message={me.error?.message ?? "Xatolik"} onRetry={me.reload} />;
  }
  const info = me.data;

  return (
    <Screen>
      <TopBar
        title={tab === "dashboard" ? info.store_name : tab === "catalog" ? "Katalog" : "Qarz va to'lovlar"}
        subtitle={tab === "dashboard" ? info.wholesaler_name : undefined}
      />
      <main className="flex flex-1 flex-col">
        {tab === "dashboard" && <Dashboard me={info} />}
        {tab === "catalog" && (
          <CatalogView scope={CART_SCOPE} loadCatalog={storeApi.catalog} onOpenCart={() => setCartOpen(true)} />
        )}
        {tab === "history" && <History currency={info.currency} />}
      </main>
      <BottomTabs tabs={tabs} active={tab} onChange={setTab} />
      <CartSheet
        scope={CART_SCOPE}
        open={cartOpen}
        onClose={() => setCartOpen(false)}
        availableCredit={info.available_credit}
        submit={(lines, key) => storeApi.createOrder(lines, key)}
        onOrdered={() => void me.reload()}
      />
    </Screen>
  );
}
