import { AlertTriangle, PackageSearch, PackageX } from "lucide-react";
import { useState } from "react";

import { adminApi } from "@/api/endpoints";
import type { StockItem } from "@/api/types";
import { EmptyState, ErrorState, Spinner, cx } from "@/components/ui";
import { unitLabel } from "@/lib/format";
import { formatMoney, formatQuantity, toMilli } from "@/lib/money";
import { useAsync } from "@/lib/useAsync";

import { Badge, Segmented, StatTile, percent } from "./widgets";

const THRESHOLDS = ["10", "20", "50", "100"] as const;
type Threshold = (typeof THRESHOLDS)[number];
type View = "low" | "all";

function level(item: StockItem): "out" | "low" | "ok" {
  if (toMilli(item.available_quantity) <= 0n) return "out";
  return item.is_low ? "low" : "ok";
}

export function StockTab({ refreshKey }: { refreshKey: number }) {
  const [threshold, setThreshold] = useState<Threshold>("20");
  const [view, setView] = useState<View>("low");
  const report = useAsync(() => adminApi.stock(threshold), [threshold, refreshKey]);

  if (report.loading && !report.data) return <Spinner />;
  if (report.error || !report.data) {
    return <ErrorState message={report.error?.message ?? "Xatolik"} onRetry={report.reload} />;
  }
  const data = report.data;
  const items = view === "low" ? data.items.filter((i) => i.is_low) : data.items;

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="Tovarlar" value={data.total_products} hint="faol pozitsiya" />
        <StatTile
          label="Kam qolgan"
          value={data.low_count}
          hint={`sotuvga ≤ ${formatQuantity(data.threshold)}`}
          tone={data.low_count ? "warning" : "default"}
        />
        <StatTile label="Tugagan" value={data.out_of_stock_count} tone={data.out_of_stock_count ? "danger" : "default"} />
        <StatTile label="Ombor qiymati" value={formatMoney(data.stock_value)} hint="sotuv narxida" />
      </div>

      {data.low_count > 0 && (
        <div role="alert" className="flex items-start gap-3 rounded-2xl bg-amber-500/15 p-4 text-sm text-amber-800 dark:text-amber-200">
          <AlertTriangle className="mt-0.5 size-5 shrink-0" aria-hidden />
          <p>
            <b>{data.low_count} ta tovar</b> kam qoldi
            {data.out_of_stock_count > 0 && <>, shundan <b>{data.out_of_stock_count} tasi</b> tugagan</>}. Buyurtmalar
            tasdiqlanishi to'xtab qolmasligi uchun omborni to'ldiring.
          </p>
        </div>
      )}

      <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
        <div className="sm:w-72">
          <Segmented
            label="Ko'rinish"
            value={view}
            onChange={setView}
            options={[
              { id: "low", label: "Kam qolganlar", count: data.low_count },
              { id: "all", label: "Barchasi" },
            ]}
          />
        </div>
        <label className="flex items-center gap-2 text-sm text-tg-hint sm:ml-auto">
          Chegara:
          <select
            value={threshold}
            onChange={(e) => setThreshold(e.target.value as Threshold)}
            className="h-10 rounded-xl bg-tg-section px-3 text-[15px] text-tg-text outline-none"
          >
            {THRESHOLDS.map((t) => (
              <option key={t} value={t}>
                ≤ {t}
              </option>
            ))}
          </select>
        </label>
      </div>

      {items.length === 0 ? (
        <EmptyState
          icon={<PackageSearch />}
          title={view === "low" ? "Kam qolgan tovar yo'q" : "Katalog bo'sh"}
          hint={view === "low" ? "Barcha tovarlar yetarli miqdorda" : undefined}
        />
      ) : (
        <ul className="grid gap-2 lg:grid-cols-2">
          {items.map((item) => {
            const state = level(item);
            const stock = toMilli(item.stock_quantity);
            const reserved = toMilli(item.reserved_quantity);
            const unit = unitLabel(item.unit);
            return (
              <li key={item.id} className="flex items-center gap-3 rounded-2xl bg-tg-section px-4 py-3">
                <div
                  className={cx(
                    "flex size-10 shrink-0 items-center justify-center rounded-xl",
                    state === "out" && "bg-tg-destructive/12 text-tg-destructive",
                    state === "low" && "bg-amber-500/15 text-amber-600 dark:text-amber-400",
                    state === "ok" && "bg-tg-secondary text-tg-hint",
                  )}
                >
                  {state === "out" ? <PackageX className="size-5" aria-hidden /> : <AlertTriangle className={cx("size-5", state === "ok" && "opacity-0")} aria-hidden />}
                </div>
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium">{item.name}</p>
                  <p className="truncate text-xs text-tg-hint">
                    {item.sku ? `${item.sku} · ` : ""}omborda {formatQuantity(item.stock_quantity)} · band {formatQuantity(item.reserved_quantity)}
                    {stock > 0n && reserved > 0n ? ` (${percent(reserved, stock)}%)` : ""}
                  </p>
                </div>
                <div className="text-right">
                  <p
                    className={cx(
                      "text-lg font-bold",
                      state === "out" && "text-tg-destructive",
                      state === "low" && "text-amber-600 dark:text-amber-400",
                    )}
                  >
                    {formatQuantity(item.available_quantity)} <span className="text-sm font-medium">{unit}</span>
                  </p>
                  {state === "out" ? <Badge tone="danger">Tugagan</Badge> : state === "low" ? <Badge tone="warning">Kam qoldi</Badge> : <p className="text-xs text-tg-hint">sotuvga</p>}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
