import { absoluteApiUrl } from "@/api/client";
import { storeApi, type Period } from "@/api/endpoints";
import { isoDay } from "@/lib/format";
import { isInTelegram, nativeDownload } from "@/telegram/twa";

export interface PeriodOption {
  id: "month" | "prev_month" | "year";
  label: string;
  period: () => Period;
}

export const PERIOD_OPTIONS: PeriodOption[] = [
  {
    id: "month",
    label: "Shu oy",
    period: () => {
      const now = new Date();
      return { date_from: isoDay(new Date(now.getFullYear(), now.getMonth(), 1)), date_to: isoDay(now) };
    },
  },
  {
    id: "prev_month",
    label: "O'tgan oy",
    period: () => {
      const now = new Date();
      return {
        date_from: isoDay(new Date(now.getFullYear(), now.getMonth() - 1, 1)),
        date_to: isoDay(new Date(now.getFullYear(), now.getMonth(), 0)),
      };
    },
  },
  {
    id: "year",
    label: "Shu yil",
    period: () => {
      const now = new Date();
      return { date_from: isoDay(new Date(now.getFullYear(), 0, 1)), date_to: isoDay(now) };
    },
  },
];

/**
 * Inside Telegram the native downloader fetches a short-lived signed URL (it cannot
 * send our auth header). In a browser (development, Telegram Web/Desktop fallback) the
 * PDF is fetched with auth and saved through an object URL.
 */
export async function downloadStatement(period: Period): Promise<void> {
  if (isInTelegram()) {
    const link = await storeApi.statementLink(period);
    if (nativeDownload(absoluteApiUrl(link.url), link.filename)) return;
  }
  const blob = await storeApi.statementPdf(period);
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `akt-sverka_${period.date_from}_${period.date_to}.pdf`;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
}
