import type { ReactNode } from "react";

import { cx } from "@/components/ui";
import { haptic } from "@/telegram/twa";

/** Share of `part` in `total` as a percentage with one decimal, computed on BigInt. */
export function percent(part: bigint, total: bigint): number {
  if (total <= 0n || part <= 0n) return 0;
  return Number((part * 1000n) / total) / 10;
}

export function Segmented<T extends string>({
  options,
  value,
  onChange,
  label,
}: {
  options: Array<{ id: T; label: string; count?: number }>;
  value: T;
  onChange: (id: T) => void;
  label: string;
}) {
  return (
    <div role="tablist" aria-label={label} className="flex gap-0.5 overflow-x-auto rounded-xl bg-tg-section p-1 [scrollbar-width:none]">
      {options.map((o) => {
        const selected = o.id === value;
        return (
          <button
            key={o.id}
            type="button"
            role="tab"
            aria-selected={selected}
            onClick={() => {
              if (!selected) haptic.tap();
              onChange(o.id);
            }}
            className={cx(
              "flex min-h-9 flex-1 items-center justify-center gap-1 rounded-lg px-2 text-[13px] font-medium whitespace-nowrap transition sm:px-3 sm:text-sm",
              selected ? "bg-tg-button text-tg-button-text" : "text-tg-hint",
            )}
          >
            {o.label}
            {o.count ? (
              <span
                className={cx(
                  "min-w-5 rounded-full px-1.5 text-xs leading-5",
                  selected ? "bg-tg-button-text/25" : "bg-tg-destructive text-white",
                )}
              >
                {o.count}
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

export function StatTile({
  label,
  value,
  hint,
  tone = "default",
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: "default" | "danger" | "warning";
}) {
  return (
    <div className="min-w-0 rounded-2xl bg-tg-section p-4">
      <p className="truncate text-sm text-tg-hint">{label}</p>
      <p
        className={cx(
          "mt-0.5 text-lg leading-tight font-bold break-words lg:text-xl",
          tone === "danger" && "text-tg-destructive",
          tone === "warning" && "text-amber-600 dark:text-amber-400",
        )}
      >
        {value}
      </p>
      {hint && <p className="mt-0.5 truncate text-xs text-tg-hint">{hint}</p>}
    </div>
  );
}

export interface BarSegment {
  key: string;
  value: bigint;
  className: string; // background colour utility
  label: string;
}

/** Horizontal 100% stacked bar. */
export function StackedBar({ segments, height = "h-3" }: { segments: BarSegment[]; height?: string }) {
  const total = segments.reduce((sum, s) => sum + (s.value > 0n ? s.value : 0n), 0n);
  return (
    <div
      className={cx("flex w-full overflow-hidden rounded-full bg-tg-secondary", height)}
      role="img"
      aria-label={segments.map((s) => `${s.label}: ${percent(s.value, total)}%`).join(", ")}
    >
      {segments.map((s) => {
        const width = percent(s.value, total);
        return width > 0 ? <div key={s.key} className={s.className} style={{ width: `${width}%` }} /> : null;
      })}
    </div>
  );
}

/** Usage meter (e.g. debt against credit limit); turns amber at 80% and red above 100%. */
export function Meter({ used, limit }: { used: bigint; limit: bigint }) {
  const ratio = limit > 0n ? percent(used, limit) : used > 0n ? 100 : 0;
  const color = ratio > 100 ? "bg-tg-destructive" : ratio >= 80 ? "bg-amber-500" : "bg-tg-button";
  return (
    <div
      className="h-2 w-full overflow-hidden rounded-full bg-tg-secondary"
      role="meter"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.min(ratio, 100)}
      aria-label="Limitdan foydalanish"
    >
      <div className={cx("h-full rounded-full", color)} style={{ width: `${Math.min(ratio, 100)}%` }} />
    </div>
  );
}

export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "danger" | "warning" | "success" | "info" }) {
  const TONE = {
    neutral: "bg-tg-secondary text-tg-hint",
    danger: "bg-tg-destructive/12 text-tg-destructive",
    warning: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
    success: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
    info: "bg-tg-button/12 text-tg-link",
  } as const;
  return (
    <span className={cx("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap", TONE[tone])}>
      {children}
    </span>
  );
}
