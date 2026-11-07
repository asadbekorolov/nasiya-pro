import { BarChart3 } from "lucide-react";

import { adminApi } from "@/api/endpoints";
import type { ShortAgingBucket } from "@/api/types";
import { Card, EmptyState, ErrorState, SectionTitle, Spinner } from "@/components/ui";
import { formatDate } from "@/lib/format";
import { formatMoney, toCents } from "@/lib/money";
import { useAsync } from "@/lib/useAsync";

import { percent, StackedBar, StatTile, type BarSegment } from "./widgets";

const BUCKETS: Array<{ key: ShortAgingBucket; label: string; color: string; text: string }> = [
  { key: "current", label: "Muddati kelmagan", color: "bg-tg-button", text: "text-tg-link" },
  { key: "1_15", label: "0–15 kun o'tgan", color: "bg-amber-400", text: "text-amber-600 dark:text-amber-400" },
  { key: "16_30", label: "15–30 kun o'tgan", color: "bg-orange-500", text: "text-orange-600 dark:text-orange-400" },
  { key: "30_plus", label: "30+ kun o'tgan", color: "bg-tg-destructive", text: "text-tg-destructive" },
];

const TOP_DEBTORS = 10;

export function AgingTab({ refreshKey }: { refreshKey: number }) {
  const report = useAsync(adminApi.agingShort, [refreshKey]);

  if (report.loading && !report.data) return <Spinner />;
  if (report.error || !report.data) {
    return <ErrorState message={report.error?.message ?? "Xatolik"} onRetry={report.reload} />;
  }
  const { rows, grand_total, as_of } = report.data;
  const total = toCents(grand_total);
  const byBucket = BUCKETS.map((b) => ({
    ...b,
    cents: rows.reduce((sum, r) => sum + toCents(r.buckets[b.key]), 0n),
  }));
  const overdue = byBucket.filter((b) => b.key !== "current").reduce((sum, b) => sum + b.cents, 0n);
  const segments = (buckets: Record<ShortAgingBucket, string>): BarSegment[] =>
    BUCKETS.map((b) => ({ key: b.key, value: toCents(buckets[b.key]), className: b.color, label: b.label }));

  return (
    <div className="space-y-3 lg:grid lg:grid-cols-2 lg:items-start lg:gap-6 lg:space-y-0">
      <div className="space-y-3 lg:sticky lg:top-20">
        <div className="grid grid-cols-2 gap-3">
          <StatTile label="Jami debitorlik" value={formatMoney(grand_total)} hint={`${formatDate(as_of)} holatiga`} />
          <StatTile
            label="Muddati o'tgan"
            value={formatMoney(overdue)}
            hint={`${percent(overdue, total)}% jami qarzdan`}
            tone={overdue > 0n ? "danger" : "default"}
          />
        </div>

        <Card className="space-y-4">
          <p className="font-semibold">Qarz yoshi bo'yicha</p>
          <StackedBar
            height="h-4"
            segments={byBucket.map((b) => ({ key: b.key, value: b.cents, className: b.color, label: b.label }))}
          />
          <ul className="space-y-2.5">
            {byBucket.map((b) => (
              <li key={b.key} className="flex items-center gap-3 text-sm">
                <span className={`size-3 shrink-0 rounded-full ${b.color}`} aria-hidden />
                <span className="min-w-0 flex-1 text-tg-hint">{b.label}</span>
                <span className="text-xs text-tg-hint">{percent(b.cents, total)}%</span>
                <span className={`shrink-0 text-right font-semibold whitespace-nowrap ${b.cents > 0n && b.key !== "current" ? b.text : ""}`}>
                  {formatMoney(b.cents)}
                </span>
              </li>
            ))}
          </ul>
        </Card>
      </div>

      <div>
        <SectionTitle>Eng ko'p qarzdor do'konlar</SectionTitle>
        {rows.length === 0 ? (
          <Card>
            <EmptyState icon={<BarChart3 />} title="Ochiq qarzlar yo'q" hint="Barcha do'konlar hisob-kitobni yopgan" />
          </Card>
        ) : (
          <ol className="divide-y divide-tg-separator overflow-hidden rounded-2xl bg-tg-section">
            {rows.slice(0, TOP_DEBTORS).map((row, index) => {
              const rowOverdue =
                toCents(row.buckets["1_15"]) + toCents(row.buckets["16_30"]) + toCents(row.buckets["30_plus"]);
              return (
                <li key={row.store_id} className="space-y-2 px-4 py-3">
                  <div className="flex items-baseline gap-3">
                    <span className="w-5 shrink-0 text-sm font-semibold text-tg-hint">{index + 1}</span>
                    <p className="min-w-0 flex-1 truncate font-medium">{row.store_name}</p>
                    <p className="font-bold">{formatMoney(row.total)}</p>
                  </div>
                  <div className="pl-8">
                    <StackedBar segments={segments(row.buckets)} height="h-2" />
                    <p className="mt-1 flex justify-between text-xs text-tg-hint">
                      <span>{percent(toCents(row.total), total)}% umumiy qarzdan</span>
                      {rowOverdue > 0n && (
                        <span className="text-tg-destructive">muddati o'tgan {formatMoney(rowOverdue)}</span>
                      )}
                    </p>
                  </div>
                </li>
              );
            })}
          </ol>
        )}
        {rows.length > TOP_DEBTORS && (
          <p className="px-1 pt-2 text-xs text-tg-hint">
            Yana {rows.length - TOP_DEBTORS} ta do'kon — to'liq ro'yxat "Do'konlar" bo'limida.
          </p>
        )}
      </div>
    </div>
  );
}
