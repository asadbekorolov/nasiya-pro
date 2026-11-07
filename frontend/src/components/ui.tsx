import { AlertTriangle, Loader2, Minus, Plus, RefreshCw } from "lucide-react";
import { useEffect, type ButtonHTMLAttributes, type ReactNode } from "react";

import { haptic } from "@/telegram/twa";

export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

// --- layout ------------------------------------------------------------------------------

export function Screen({ children }: { children: ReactNode }) {
  return <div className="mx-auto flex min-h-dvh w-full max-w-lg flex-col">{children}</div>;
}

export function Card({
  children,
  className,
  tone = "default",
}: {
  children: ReactNode;
  className?: string;
  tone?: "default" | "accent";
}) {
  // Background lives in exactly one class so callers never fight over `bg-*`.
  const surface = tone === "accent" ? "bg-tg-button text-tg-button-text" : "bg-tg-section";
  return <section className={cx("rounded-2xl p-4", surface, className)}>{children}</section>;
}

export function SectionTitle({ children }: { children: ReactNode }) {
  return (
    <h2 className="px-1 pt-2 pb-1.5 text-xs font-semibold tracking-wide text-tg-hint uppercase">
      {children}
    </h2>
  );
}

// --- buttons -----------------------------------------------------------------------------

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  loading?: boolean;
  icon?: ReactNode;
  block?: boolean;
}

const VARIANT: Record<ButtonVariant, string> = {
  primary: "bg-tg-button text-tg-button-text",
  secondary: "bg-tg-secondary text-tg-link",
  ghost: "bg-transparent text-tg-link",
  danger: "bg-tg-secondary text-tg-destructive",
};

export function Button({
  variant = "primary",
  loading = false,
  icon,
  block = false,
  className,
  children,
  disabled,
  onClick,
  ...rest
}: ButtonProps) {
  return (
    <button
      type="button"
      {...rest}
      disabled={disabled || loading}
      onClick={(event) => {
        haptic.tap();
        onClick?.(event);
      }}
      className={cx(
        "inline-flex min-h-12 items-center justify-center gap-2 rounded-xl px-4 text-[15px] font-semibold transition active:scale-[0.98] disabled:opacity-50",
        VARIANT[variant],
        block && "w-full",
        className,
      )}
    >
      {loading ? <Loader2 className="size-5 animate-spin" aria-hidden /> : icon}
      {children}
    </button>
  );
}

// --- states ------------------------------------------------------------------------------

export function Spinner({ label = "Yuklanmoqda…" }: { label?: string }) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-3 py-16 text-tg-hint">
      <Loader2 className="size-7 animate-spin" aria-hidden />
      <span className="text-sm">{label}</span>
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-4 px-6 py-16 text-center">
      <AlertTriangle className="size-9 text-tg-destructive" aria-hidden />
      <p className="text-[15px]">{message}</p>
      {onRetry && (
        <Button variant="secondary" icon={<RefreshCw className="size-4" />} onClick={onRetry}>
          Qayta urinish
        </Button>
      )}
    </div>
  );
}

export function EmptyState({ icon, title, hint }: { icon: ReactNode; title: string; hint?: string }) {
  return (
    <div className="flex flex-col items-center gap-2 px-6 py-14 text-center text-tg-hint">
      <div className="mb-1 [&>svg]:size-10">{icon}</div>
      <p className="text-[15px] font-medium text-tg-text">{title}</p>
      {hint && <p className="text-sm">{hint}</p>}
    </div>
  );
}

export function InlineError({ message }: { message: string | undefined }) {
  if (!message) return null;
  return (
    <p role="alert" className="rounded-xl bg-tg-destructive/10 px-3 py-2.5 text-sm text-tg-destructive">
      {message}
    </p>
  );
}

// --- inputs ------------------------------------------------------------------------------

export function Stepper({
  value,
  max,
  onChange,
  label,
}: {
  value: number;
  max: number;
  onChange: (value: number) => void;
  label: string;
}) {
  if (value === 0) {
    return (
      <button
        type="button"
        aria-label={`${label}: savatga qo'shish`}
        disabled={max < 1}
        onClick={() => {
          haptic.tap();
          onChange(1);
        }}
        className="inline-flex size-10 items-center justify-center rounded-full bg-tg-button text-tg-button-text transition active:scale-95 disabled:opacity-40"
      >
        <Plus className="size-5" aria-hidden />
      </button>
    );
  }
  return (
    <div className="inline-flex items-center gap-1 rounded-full bg-tg-secondary p-1">
      <button
        type="button"
        aria-label={`${label}: kamaytirish`}
        onClick={() => {
          haptic.tap();
          onChange(value - 1);
        }}
        className="inline-flex size-8 items-center justify-center rounded-full bg-tg-section text-tg-link active:scale-95"
      >
        <Minus className="size-4" aria-hidden />
      </button>
      <span className="min-w-8 text-center text-[15px] font-semibold" aria-live="polite">
        {value}
      </span>
      <button
        type="button"
        aria-label={`${label}: ko'paytirish`}
        disabled={value >= max}
        onClick={() => {
          haptic.tap();
          onChange(value + 1);
        }}
        className="inline-flex size-8 items-center justify-center rounded-full bg-tg-button text-tg-button-text active:scale-95 disabled:opacity-40"
      >
        <Plus className="size-4" aria-hidden />
      </button>
    </div>
  );
}

// --- navigation ----------------------------------------------------------------------------

export interface TabItem<T extends string> {
  id: T;
  label: string;
  icon: ReactNode;
  badge?: number;
}

export function BottomTabs<T extends string>({
  tabs,
  active,
  onChange,
  className,
}: {
  tabs: TabItem<T>[];
  active: T;
  onChange: (id: T) => void;
  className?: string;
}) {
  return (
    <nav
      aria-label="Bo'limlar"
      className={cx(
        "safe-bottom sticky bottom-0 z-20 mt-auto border-t border-tg-separator bg-tg-bg/95 backdrop-blur",
        className,
      )}
    >
      <ul className="mx-auto flex max-w-lg">
        {tabs.map((tab) => {
          const selected = tab.id === active;
          return (
            <li key={tab.id} className="flex-1">
              <button
                type="button"
                aria-current={selected ? "page" : undefined}
                onClick={() => {
                  if (!selected) haptic.tap();
                  onChange(tab.id);
                }}
                className={cx(
                  "relative flex w-full flex-col items-center gap-0.5 pt-2 pb-1 text-[11px] font-medium",
                  selected ? "text-tg-button" : "text-tg-hint",
                )}
              >
                <span className="[&>svg]:size-6">{tab.icon}</span>
                {tab.label}
                {tab.badge ? (
                  <span className="absolute top-1 left-1/2 ml-2 min-w-5 rounded-full bg-tg-destructive px-1.5 text-[11px] leading-5 text-white">
                    {tab.badge}
                  </span>
                ) : null}
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

export function TopBar({ title, subtitle, right }: { title: string; subtitle?: string; right?: ReactNode }) {
  return (
    <header className="sticky top-0 z-10 flex items-center gap-3 bg-tg-secondary/95 px-4 pt-3 pb-2 backdrop-blur">
      <div className="min-w-0 flex-1">
        <h1 className="truncate text-xl font-bold">{title}</h1>
        {subtitle && <p className="truncate text-sm text-tg-hint">{subtitle}</p>}
      </div>
      {right}
    </header>
  );
}

/** Bottom sheet modal. */
export function Sheet({
  open,
  title,
  onClose,
  children,
  footer,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-40 flex items-end justify-center lg:items-center lg:p-6"
      role="dialog"
      aria-modal
      aria-label={title}
    >
      <button type="button" aria-label="Yopish" className="absolute inset-0 bg-black/40" onClick={onClose} />
      {/* Bottom sheet on phones, centred dialog on desktop. */}
      <div className="relative flex max-h-[88dvh] w-full max-w-lg flex-col rounded-t-3xl bg-tg-bg lg:max-h-[80dvh] lg:rounded-3xl lg:pb-2 lg:shadow-2xl">
        <div className="mx-auto mt-2 h-1 w-10 rounded-full bg-tg-separator lg:hidden" aria-hidden />
        <h2 className="px-4 pt-3 pb-2 text-lg font-bold">{title}</h2>
        <div className="flex-1 overflow-y-auto px-4">{children}</div>
        {footer && <div className="safe-bottom border-t border-tg-separator px-4 pt-3">{footer}</div>}
      </div>
    </div>
  );
}
