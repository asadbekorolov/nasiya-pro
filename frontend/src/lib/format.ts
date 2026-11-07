import type { PaymentMethod, Transaction } from "@/api/types";

const MONTHS = [
  "yanvar",
  "fevral",
  "mart",
  "aprel",
  "may",
  "iyun",
  "iyul",
  "avgust",
  "sentabr",
  "oktabr",
  "noyabr",
  "dekabr",
];

/** "8-noyabr, 14:05" (local time of the device). */
export function formatDateTime(iso: string): string {
  const d = new Date(iso);
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${d.getDate()}-${MONTHS[d.getMonth()]}, ${hh}:${mm}`;
}

export function formatDate(iso: string): string {
  const d = new Date(iso);
  return `${d.getDate()}-${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
}

export const PAYMENT_METHOD_LABEL: Record<PaymentMethod, string> = {
  cash: "Naqd pul",
  card: "Plastik karta",
  bank_transfer: "Bank o'tkazmasi",
  other: "Boshqa",
};

const UNIT_LABEL: Record<string, string> = {
  pcs: "dona",
  kg: "kg",
  l: "l",
  m: "m",
  box: "quti",
  pack: "pachka",
  block: "blok",
};

export function unitLabel(unit: string): string {
  return UNIT_LABEL[unit] ?? unit;
}

export function transactionTitle(tx: Transaction): string {
  switch (tx.type) {
    case "charge":
      return "Tovar yetkazildi";
    case "payment":
      return `To'lov · ${PAYMENT_METHOD_LABEL[tx.payment_method ?? "other"]}`;
    case "adjustment":
      return tx.description ? `Tuzatish · ${tx.description}` : "Tuzatish";
  }
}

/** Local calendar date as YYYY-MM-DD (not UTC). */
export function isoDay(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

export function newIdempotencyKey(prefix: string): string {
  return `${prefix}-${crypto.randomUUID()}`;
}
