/**
 * Money arrives from the API as decimal strings ("1250000.00") and is never converted
 * to floating point. Arithmetic runs on BigInt minor units:
 *   money    -> tiyin (1/100)
 *   quantity -> thousandths (NUMERIC(15,3))
 */

const DECIMAL_RE = /^(-)?(\d+)(?:\.(\d+))?$/;

function toScaled(value: string, scale: number): bigint {
  const match = DECIMAL_RE.exec(value.trim());
  if (!match) throw new Error(`Invalid decimal: ${value}`);
  const [, sign, whole = "0", fraction = ""] = match;
  if (fraction.length > scale) throw new Error(`Too many decimals: ${value}`);
  const units = BigInt(whole + fraction.padEnd(scale, "0"));
  return sign ? -units : units;
}

function fromScaled(units: bigint, scale: number): string {
  const negative = units < 0n;
  const digits = (negative ? -units : units).toString().padStart(scale + 1, "0");
  const whole = digits.slice(0, digits.length - scale);
  const fraction = digits.slice(digits.length - scale);
  return `${negative ? "-" : ""}${whole}.${fraction}`;
}

export const toCents = (value: string): bigint => toScaled(value, 2);
export const fromCents = (cents: bigint): string => fromScaled(cents, 2);
export const toMilli = (value: string): bigint => toScaled(value, 3);
export const fromMilli = (milli: bigint): string => fromScaled(milli, 3);

/** round(price * quantity, 2) with ROUND_HALF_UP — mirrors the backend exactly. */
export function lineTotalCents(price: string, quantity: string): bigint {
  const product = toCents(price) * toMilli(quantity); // scale 10^5
  const negative = product < 0n;
  const abs = negative ? -product : product;
  const rounded = (abs + 500n) / 1000n;
  return negative ? -rounded : rounded;
}

function groupThousands(whole: string): string {
  return whole.replace(/\B(?=(\d{3})+(?!\d))/g, " ");
}

/** "1 250 000 so'm" — fraction shown only when it is not zero. */
export function formatMoney(value: string | bigint, currency = "UZS"): string {
  const cents = typeof value === "bigint" ? value : toCents(value);
  const negative = cents < 0n;
  const abs = negative ? -cents : cents;
  const whole = groupThousands((abs / 100n).toString());
  const fraction = abs % 100n;
  const amount = fraction === 0n ? whole : `${whole},${fraction.toString().padStart(2, "0")}`;
  const unit = currency === "UZS" ? "so'm" : currency;
  return `${negative ? "−" : ""}${amount} ${unit}`;
}

/** "12.5" -> "12,5"; integers without decimals. */
export function formatQuantity(value: string): string {
  const milli = toMilli(value);
  const whole = groupThousands((milli / 1000n).toString());
  const fraction = (milli % 1000n).toString().padStart(3, "0").replace(/0+$/, "");
  return fraction ? `${whole},${fraction}` : whole;
}

export function isPositiveMoneyInput(value: string): boolean {
  const normalized = value.replace(/\s/g, "").replace(",", ".");
  return /^\d{1,13}(\.\d{1,2})?$/.test(normalized) && toCents(normalized) > 0n;
}

/** User input ("1 200 000,5") -> API decimal string ("1200000.50"). */
export function normalizeMoneyInput(value: string): string {
  return fromCents(toCents(value.replace(/\s/g, "").replace(",", ".")));
}
