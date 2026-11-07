import { BarChart3, ClipboardList, Package, RefreshCw, Store as StoreIcon } from "lucide-react";
import { useState, type ReactNode } from "react";

import { adminApi } from "@/api/endpoints";
import type { AdminStore } from "@/api/types";
import { BottomTabs, ErrorState, Spinner, TopBar, cx, type TabItem } from "@/components/ui";
import { useAsync } from "@/lib/useAsync";
import { haptic } from "@/telegram/twa";

import { AgingTab } from "./AgingTab";
import { OrdersTab } from "./OrdersTab";
import { StockTab } from "./StockTab";
import { StoresTab } from "./StoresTab";

type Tab = "aging" | "orders" | "stores" | "stock";

const TITLES: Record<Tab, { title: string; subtitle: string }> = {
  aging: { title: "Debitorlik", subtitle: "Qarzlar yoshi va eng katta qarzdorlar" },
  orders: { title: "Buyurtmalar", subtitle: "Tasdiqlash, yetkazish va bekor qilish" },
  stores: { title: "Do'konlar va limitlar", subtitle: "Balans va kredit limitlari" },
  stock: { title: "Ombor", subtitle: "Qoldiqlar va kam qolgan tovarlar" },
};

/**
 * Wholesaler dashboard. One layout for both surfaces: a bottom tab bar inside the
 * Telegram Mini App (phone), a sidebar with a wide content area on desktop browsers.
 */
export default function AdminApp() {
  const [tab, setTab] = useState<Tab>("orders");
  // Bumped after any change so every tab refetches what it shows.
  const [refreshKey, setRefreshKey] = useState(0);
  const stores = useAsync(adminApi.stores, [refreshKey]);
  const [patched, setPatched] = useState<Record<string, AdminStore>>({});

  const storeList = (stores.data ?? []).map((s) => patched[s.id] ?? s);
  const pending = storeList.reduce((sum, s) => sum + s.pending_orders, 0);

  const refresh = () => {
    setPatched({});
    setRefreshKey((k) => k + 1);
  };

  const tabs: TabItem<Tab>[] = [
    { id: "orders", label: "Buyurtmalar", icon: <ClipboardList />, badge: pending || undefined },
    { id: "aging", label: "Debitorlik", icon: <BarChart3 /> },
    { id: "stores", label: "Do'konlar", icon: <StoreIcon /> },
    { id: "stock", label: "Ombor", icon: <Package /> },
  ];

  let content: ReactNode;
  if (stores.loading && !stores.data) {
    content = <Spinner />;
  } else if (stores.error && !stores.data) {
    content = <ErrorState message={stores.error.message} onRetry={stores.reload} />;
  } else if (tab === "aging") {
    content = <AgingTab refreshKey={refreshKey} />;
  } else if (tab === "orders") {
    content = <OrdersTab stores={storeList} pendingCount={pending} onChanged={() => void stores.reload()} />;
  } else if (tab === "stores") {
    content = (
      <StoresTab stores={storeList} onUpdated={(s) => setPatched((prev) => ({ ...prev, [s.id]: s }))} />
    );
  } else {
    content = <StockTab refreshKey={refreshKey} />;
  }

  return (
    <div className="min-h-dvh lg:flex">
      <aside className="sticky top-0 hidden h-dvh w-64 shrink-0 flex-col border-r border-tg-separator bg-tg-bg lg:flex">
        <div className="px-5 pt-6 pb-4">
          <p className="text-lg font-bold">Nasiya Pro</p>
          <p className="text-sm text-tg-hint">Boshqaruv paneli</p>
        </div>
        <nav aria-label="Bo'limlar" className="flex flex-col gap-1 px-3">
          {tabs.map((t) => {
            const selected = t.id === tab;
            return (
              <button
                key={t.id}
                type="button"
                aria-current={selected ? "page" : undefined}
                onClick={() => setTab(t.id)}
                className={cx(
                  "flex items-center gap-3 rounded-xl px-3 py-2.5 text-left text-[15px] font-medium transition [&>svg]:size-5",
                  selected ? "bg-tg-button text-tg-button-text" : "text-tg-text hover:bg-tg-secondary",
                )}
              >
                {t.icon}
                <span className="flex-1">{t.label}</span>
                {t.badge ? (
                  <span className="min-w-5 rounded-full bg-tg-destructive px-1.5 text-center text-xs leading-5 text-white">
                    {t.badge}
                  </span>
                ) : null}
              </button>
            );
          })}
        </nav>
      </aside>

      <div className="mx-auto flex min-h-dvh w-full max-w-lg flex-1 flex-col lg:max-w-6xl">
        <TopBar
          title={TITLES[tab].title}
          subtitle={TITLES[tab].subtitle}
          right={
            <button
              type="button"
              aria-label="Yangilash"
              onClick={() => {
                haptic.tap();
                refresh();
              }}
              className="inline-flex size-10 items-center justify-center rounded-full text-tg-link active:bg-tg-section"
            >
              <RefreshCw className={cx("size-5", stores.loading && "animate-spin")} aria-hidden />
            </button>
          }
        />
        <main className="flex-1 px-4 pt-1 pb-6 lg:px-8 lg:pt-3">{content}</main>
        <BottomTabs tabs={tabs} active={tab} onChange={setTab} className="lg:hidden" />
      </div>
    </div>
  );
}
