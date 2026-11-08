import { Archive, FileDown, Package, Store as StoreIcon } from "lucide-react";
import { useState, type ReactNode } from "react";

import { absoluteApiUrl, ApiError } from "@/api/client";
import { adminApi } from "@/api/endpoints";
import type { ExportKind } from "@/api/types";
import { Button, InlineError } from "@/components/ui";
import { toast } from "@/components/toast";
import { isoDay } from "@/lib/format";
import { haptic, isInTelegram, nativeDownload } from "@/telegram/twa";

const MONTH_NAMES = [
  "Yanvar", "Fevral", "Mart", "Aprel", "May", "Iyun",
  "Iyul", "Avgust", "Sentabr", "Oktabr", "Noyabr", "Dekabr",
];

/** The last 12 months, newest first: [{ value: "2026-11", label: "Noyabr 2026" }]. */
function recentMonths(): Array<{ value: string; label: string }> {
  const now = new Date();
  return Array.from({ length: 12 }, (_, i) => {
    const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
    return {
      value: `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`,
      label: `${MONTH_NAMES[d.getMonth()]} ${d.getFullYear()}`,
    };
  });
}

function localFilename(kind: ExportKind, month: string): string {
  if (kind === "statements") return `statements_${month.replace("-", "_")}.zip`;
  return `${kind === "stores" ? "dokonlar-qarzlar" : "ombor-qoldiqlari"}_${isoDay(new Date())}.xlsx`;
}

/**
 * Inside Telegram the native downloader fetches a short-lived signed URL (it cannot
 * send our auth header); in a browser the file is fetched with auth and saved.
 */
async function download(kind: ExportKind, month: string): Promise<void> {
  const monthArg = kind === "statements" ? month : undefined;
  if (isInTelegram()) {
    const link = await adminApi.exportLink(kind, monthArg);
    if (nativeDownload(absoluteApiUrl(link.url), link.filename)) return;
  }
  const blob = await adminApi.exportBlob(kind, monthArg);
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = localFilename(kind, month);
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

export function ExportCard() {
  const months = recentMonths();
  const [month, setMonth] = useState(months[0]!.value);
  const [busy, setBusy] = useState<ExportKind | null>(null);
  const [error, setError] = useState<string>();

  async function run(kind: ExportKind) {
    setBusy(kind);
    setError(undefined);
    haptic.tap();
    try {
      await download(kind, month);
      haptic.success();
      if (kind === "statements") toast.success("Akt-sverkalar arxivi tayyor");
    } catch (err) {
      haptic.error();
      setError(err instanceof ApiError ? err.message : "Faylni yuklab bo'lmadi");
    } finally {
      setBusy(null);
    }
  }

  const row = (kind: ExportKind, icon: ReactNode, title: string, hint: string, extra?: ReactNode) => (
    <div className="flex flex-col gap-2 rounded-xl bg-tg-secondary p-3 sm:flex-row sm:items-center">
      <div className="flex min-w-0 flex-1 items-start gap-3">
        <div className="mt-0.5 text-tg-link [&>svg]:size-5">{icon}</div>
        <div className="min-w-0">
          <p className="font-medium">{title}</p>
          <p className="text-xs text-tg-hint">{hint}</p>
        </div>
      </div>
      <div className="flex gap-2">
        {extra}
        <Button
          className="min-h-10 flex-1 text-sm sm:flex-none"
          icon={<FileDown className="size-4" />}
          loading={busy === kind}
          disabled={busy !== null}
          onClick={() => void run(kind)}
        >
          Yuklab olish
        </Button>
      </div>
    </div>
  );

  return (
    <section className="space-y-3 rounded-2xl bg-tg-section p-4">
      <div>
        <h2 className="font-semibold">Eksport</h2>
        <p className="text-sm text-tg-hint">Hisobotlarni Excel va PDF ko'rinishida yuklab oling.</p>
      </div>
      {row("stores", <StoreIcon />, "Do'konlar hisoboti (Excel)", "Limit, joriy va muddati o'tgan qarz, oxirgi to'lov sanasi")}
      {row("stock", <Package />, "Ombor qoldiqlari (Excel)", "Optom narx, omborda bor, band qilingan, erkin qoldiq")}
      {row(
        "statements",
        <Archive />,
        "Oylik Akt-sverkalar (ZIP)",
        "Barcha faol do'konlarning rasmiy PDF akt-sverkasi bitta arxivda",
        <select
          value={month}
          onChange={(e) => setMonth(e.target.value)}
          aria-label="Oy"
          disabled={busy !== null}
          className="h-10 rounded-xl bg-tg-section px-3 text-sm outline-none"
        >
          {months.map((m) => (
            <option key={m.value} value={m.value}>
              {m.label}
            </option>
          ))}
        </select>,
      )}
      {busy === "statements" && (
        <p className="text-xs text-tg-hint">Har bir do'kon uchun PDF tayyorlanmoqda — bir necha soniya olishi mumkin…</p>
      )}
      <InlineError message={error} />
    </section>
  );
}
