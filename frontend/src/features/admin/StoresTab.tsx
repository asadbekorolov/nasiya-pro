import { Clock, Link2, Link2Off, MapPin, Pencil, Phone, Search, Store as StoreIcon } from "lucide-react";
import { useState } from "react";

import { adminApi } from "@/api/endpoints";
import type { AdminStore } from "@/api/types";
import { Button, EmptyState, InlineError, Sheet } from "@/components/ui";
import { toast } from "@/components/toast";
import { formatMoney, fromCents, normalizeMoneyInput, toCents } from "@/lib/money";
import { haptic } from "@/telegram/twa";

import { Badge, Meter, percent, StatTile } from "./widgets";

const LIMIT_INPUT_RE = /^\d{1,13}([.,]\d{1,2})?$/;
const QUICK_STEPS = [1_000_000n, 5_000_000n, 10_000_000n]; // so'm

type Sort = "debt" | "usage" | "name";

function usage(store: AdminStore): number {
  const limit = toCents(store.credit_limit);
  const debt = toCents(store.current_debt);
  return limit > 0n ? percent(debt, limit) : debt > 0n ? Infinity : 0;
}

export function StoresTab({
  stores,
  onUpdated,
}: {
  stores: AdminStore[];
  onUpdated: (store: AdminStore) => void;
}) {
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<Sort>("debt");
  const [editing, setEditing] = useState<AdminStore | null>(null);

  const needle = query.trim().toLowerCase();
  const visible = stores
    .filter((s) => !needle || s.name.toLowerCase().includes(needle) || (s.address ?? "").toLowerCase().includes(needle))
    .sort((a, b) => {
      if (sort === "name") return a.name.localeCompare(b.name);
      if (sort === "usage") return usage(b) - usage(a);
      const diff = toCents(b.current_debt) - toCents(a.current_debt);
      return diff > 0n ? 1 : diff < 0n ? -1 : 0;
    });

  const totalDebt = stores.reduce((sum, s) => sum + (toCents(s.current_debt) > 0n ? toCents(s.current_debt) : 0n), 0n);
  const totalLimit = stores.reduce((sum, s) => sum + toCents(s.credit_limit), 0n);
  const overLimit = stores.filter((s) => toCents(s.current_debt) > toCents(s.credit_limit)).length;

  if (stores.length === 0) {
    return <EmptyState icon={<StoreIcon />} title="Hali do'konlar yo'q" />;
  }

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="Do'konlar" value={stores.length} hint={`${stores.filter((s) => s.is_linked).length} tasi Telegramga ulangan`} />
        <StatTile label="Jami qarz" value={formatMoney(fromCents(totalDebt))} />
        <StatTile label="Jami limit" value={formatMoney(fromCents(totalLimit))} hint={`${percent(totalDebt, totalLimit)}% band`} />
        <StatTile label="Limitdan oshgan" value={overLimit} tone={overLimit ? "danger" : "default"} hint="do'kon" />
      </div>

      <div className="flex flex-col gap-2 sm:flex-row">
        <label className="flex flex-1 items-center gap-2 rounded-xl bg-tg-section px-3">
          <Search className="size-5 text-tg-hint" aria-hidden />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Do'kon nomi yoki manzil"
            className="h-11 w-full bg-transparent text-[15px] outline-none placeholder:text-tg-hint"
          />
        </label>
        <select
          value={sort}
          onChange={(e) => setSort(e.target.value as Sort)}
          aria-label="Saralash"
          className="h-11 rounded-xl bg-tg-section px-3 text-[15px] outline-none"
        >
          <option value="debt">Qarz bo'yicha</option>
          <option value="usage">Limit bandligi bo'yicha</option>
          <option value="name">Nomi bo'yicha</option>
        </select>
      </div>

      <ul className="grid gap-3 lg:grid-cols-2">
        {visible.map((store) => {
          const debt = toCents(store.current_debt);
          const limit = toCents(store.credit_limit);
          const overdue = toCents(store.overdue_amount);
          return (
            <li key={store.id} className="space-y-3 rounded-2xl bg-tg-section p-4">
              <div className="flex items-start gap-3">
                <div className="min-w-0 flex-1">
                  <p className="truncate font-semibold">{store.name}</p>
                  {store.address && (
                    <p className="flex items-center gap-1 truncate text-sm text-tg-hint">
                      <MapPin className="size-3.5 shrink-0" aria-hidden />
                      {store.address}
                    </p>
                  )}
                </div>
                <div className="flex flex-wrap justify-end gap-1">
                  {!store.is_active && <Badge tone="danger">Faol emas</Badge>}
                  {store.pending_orders > 0 && (
                    <Badge tone="warning">
                      <Clock className="size-3" aria-hidden />
                      {store.pending_orders} yangi
                    </Badge>
                  )}
                  {store.is_linked ? (
                    <Badge tone="success">
                      <Link2 className="size-3" aria-hidden />
                      Telegram
                    </Badge>
                  ) : (
                    <Badge>
                      <Link2Off className="size-3" aria-hidden />
                      ulanmagan
                    </Badge>
                  )}
                </div>
              </div>

              <div className="space-y-1.5">
                <div className="flex items-baseline justify-between gap-2 text-sm">
                  <span className="text-tg-hint">Qarz / limit</span>
                  <span>
                    <span className={debt > limit ? "font-bold text-tg-destructive" : "font-bold"}>
                      {formatMoney(store.current_debt)}
                    </span>
                    <span className="text-tg-hint"> / {formatMoney(store.credit_limit)}</span>
                  </span>
                </div>
                <Meter used={debt > 0n ? debt : 0n} limit={limit} />
                <div className="flex justify-between text-xs text-tg-hint">
                  <span>Bo'sh limit: {formatMoney(store.available_credit)}</span>
                  {overdue > 0n && <span className="text-tg-destructive">Muddati o'tgan: {formatMoney(overdue)}</span>}
                  {debt < 0n && <span>Avans: {formatMoney(-debt)}</span>}
                </div>
              </div>

              <div className="flex gap-2">
                <Button variant="secondary" className="min-h-10 flex-1 text-sm" icon={<Pencil className="size-4" />} onClick={() => setEditing(store)}>
                  Limitni o'zgartirish
                </Button>
                {store.phone && (
                  <a
                    href={`tel:${store.phone}`}
                    aria-label={`Qo'ng'iroq: ${store.phone}`}
                    className="inline-flex min-h-10 items-center justify-center rounded-xl bg-tg-secondary px-4 text-tg-link"
                  >
                    <Phone className="size-4" aria-hidden />
                  </a>
                )}
              </div>
            </li>
          );
        })}
      </ul>
      {visible.length === 0 && <p className="py-8 text-center text-sm text-tg-hint">Hech narsa topilmadi</p>}

      {editing && (
        <CreditLimitSheet
          store={editing}
          onClose={() => setEditing(null)}
          onSaved={(updated) => {
            onUpdated(updated);
            setEditing(null);
          }}
        />
      )}
    </div>
  );
}

function CreditLimitSheet({
  store,
  onClose,
  onSaved,
}: {
  store: AdminStore;
  onClose: () => void;
  onSaved: (store: AdminStore) => void;
}) {
  const [value, setValue] = useState(() => (toCents(store.credit_limit) / 100n).toString());
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string>();

  const cleaned = value.replace(/\s/g, "");
  const valid = LIMIT_INPUT_RE.test(cleaned);
  const next = valid ? normalizeMoneyInput(cleaned) : null;
  const debt = toCents(store.current_debt);
  const unchanged = next !== null && toCents(next) === toCents(store.credit_limit);

  function step(delta: bigint) {
    const base = next ? toCents(next) : 0n;
    const result = base + delta * 100n;
    setValue((result > 0n ? result / 100n : 0n).toString());
  }

  async function save() {
    if (!next) return;
    setSaving(true);
    setError(undefined);
    try {
      const updated = await adminApi.setCreditLimit(store.id, next);
      haptic.success();
      toast.success(`${store.name}: yangi limit ${formatMoney(updated.credit_limit)}`);
      onSaved(updated);
    } catch (err) {
      haptic.error();
      setError(err instanceof Error ? err.message : "Saqlab bo'lmadi");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Sheet
      open
      title="Kredit limiti"
      onClose={onClose}
      footer={
        <Button block loading={saving} disabled={!valid || unchanged} onClick={() => void save()}>
          Saqlash
        </Button>
      }
    >
      <div className="space-y-3 pb-4">
        <div>
          <p className="font-semibold">{store.name}</p>
          <p className="text-sm text-tg-hint">
            Joriy qarz: {formatMoney(store.current_debt)} · hozirgi limit: {formatMoney(store.credit_limit)}
          </p>
        </div>
        <label className="block">
          <span className="px-1 text-xs text-tg-hint">Yangi limit (so'm)</span>
          <input
            inputMode="decimal"
            autoFocus
            value={value}
            onChange={(e) => setValue(e.target.value)}
            className="mt-1 h-14 w-full rounded-xl bg-tg-secondary px-4 text-2xl font-bold outline-none"
            aria-invalid={!valid}
          />
        </label>
        {next && <p className="px-1 text-sm text-tg-hint">{formatMoney(next)}</p>}
        <div className="flex flex-wrap gap-2">
          {QUICK_STEPS.map((d) => (
            <button key={`+${d}`} type="button" onClick={() => step(d)} className="rounded-full bg-tg-secondary px-3 py-1.5 text-sm text-tg-link">
              +{formatMoney(d * 100n)}
            </button>
          ))}
          <button type="button" onClick={() => step(-1_000_000n)} className="rounded-full bg-tg-secondary px-3 py-1.5 text-sm text-tg-link">
            −{formatMoney(100_000_000n)}
          </button>
        </div>
        {!valid && <InlineError message="Summani to'g'ri kiriting (masalan 15000000)" />}
        {next && toCents(next) < debt && (
          <p className="rounded-xl bg-amber-500/15 px-3 py-2.5 text-sm text-amber-700 dark:text-amber-300">
            Limit joriy qarzdan past: do'kon qarzini kamaytirmaguncha yangi buyurtmalar tasdiqlanmaydi.
          </p>
        )}
        {next && toCents(next) === 0n && (
          <p className="px-1 text-sm text-tg-hint">0 — do'kon faqat oldindan to'lov bilan savdo qiladi.</p>
        )}
        <InlineError message={error} />
      </div>
    </Sheet>
  );
}
