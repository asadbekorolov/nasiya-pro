import { BarChart3 } from "lucide-react";

import { adminApi } from "@/api/endpoints";
import { Card, EmptyState, ErrorState, Screen, SectionTitle, Spinner, TopBar } from "@/components/ui";
import { formatMoney } from "@/lib/money";
import { useAsync } from "@/lib/useAsync";

const BUCKETS = [
  ["current", "Muddati kelmagan"],
  ["1_30", "1–30 kun"],
  ["31_60", "31–60 kun"],
  ["61_90", "61–90 kun"],
  ["90_plus", "90+ kun"],
] as const;

/** Wholesaler admin in the Mini App: receivables at a glance (aging report). */
export default function AdminApp() {
  const report = useAsync(adminApi.aging);

  if (report.loading && !report.data) return <Spinner />;
  if (report.error || !report.data) {
    return <ErrorState message={report.error?.message ?? "Xatolik"} onRetry={report.reload} />;
  }
  const { rows, grand_total } = report.data;

  return (
    <Screen>
      <TopBar title="Debitorlik" subtitle={`Holat: ${report.data.as_of}`} />
      <div className="space-y-3 px-4 pb-6">
        <Card>
          <p className="text-sm text-tg-hint">Jami qarzdorlik</p>
          <p className="text-3xl font-bold">{formatMoney(grand_total)}</p>
        </Card>
        {rows.length === 0 ? (
          <EmptyState icon={<BarChart3 />} title="Ochiq qarzlar yo'q" />
        ) : (
          <>
            <SectionTitle>Do'konlar bo'yicha</SectionTitle>
            {rows.map((row) => (
              <Card key={row.store_id} className="space-y-2">
                <div className="flex items-baseline justify-between gap-3">
                  <p className="truncate font-semibold">{row.store_name}</p>
                  <p className="font-bold">{formatMoney(row.total)}</p>
                </div>
                <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-sm">
                  {BUCKETS.filter(([key]) => row.buckets[key] !== "0.00").map(([key, label]) => (
                    <div key={key} className="contents">
                      <dt className="text-tg-hint">{label}</dt>
                      <dd className={key === "current" ? "text-right" : "text-right text-tg-destructive"}>
                        {formatMoney(row.buckets[key])}
                      </dd>
                    </div>
                  ))}
                </dl>
              </Card>
            ))}
          </>
        )}
      </div>
    </Screen>
  );
}
