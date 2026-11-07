import { CalendarClock, FileDown, Store, Wallet } from "lucide-react";
import { useState } from "react";

import { ApiError } from "@/api/client";
import type { StoreMe } from "@/api/types";
import { toast } from "@/components/toast";
import { Button, Card, cx, SectionTitle } from "@/components/ui";
import { formatDate, PAYMENT_METHOD_LABEL } from "@/lib/format";
import { formatMoney, toCents } from "@/lib/money";
import { haptic } from "@/telegram/twa";

import { downloadStatement, PERIOD_OPTIONS, type PeriodOption } from "./statement";

function InfoRow({ label, value, tone }: { label: string; value: string; tone?: "danger" }) {
  return (
    <div className="flex items-center justify-between gap-3 py-2.5">
      <span className="text-tg-hint">{label}</span>
      <span className={cx("text-right font-medium", tone === "danger" && "text-tg-destructive")}>{value}</span>
    </div>
  );
}

export function Dashboard({ me }: { me: StoreMe }) {
  const [periodId, setPeriodId] = useState<PeriodOption["id"]>("month");
  const [downloading, setDownloading] = useState(false);
  const debtCents = toCents(me.current_debt);
  const overdue = toCents(me.overdue_amount) > 0n;

  async function download() {
    const option = PERIOD_OPTIONS.find((p) => p.id === periodId) ?? PERIOD_OPTIONS[0]!;
    setDownloading(true);
    try {
      await downloadStatement(option.period());
      haptic.success();
    } catch (error) {
      haptic.error();
      toast.error(error instanceof ApiError ? error.message : "PDF yuklab bo'lmadi");
    } finally {
      setDownloading(false);
    }
  }

  return (
    <div className="space-y-3 px-4 pb-6">
      <div className="grid grid-cols-1 gap-3 min-[380px]:grid-cols-2">
        <Card tone="accent">
          <div className="flex items-center gap-2 text-sm opacity-85">
            <Wallet className="size-4" aria-hidden />
            {debtCents < 0n ? "Oldindan to'lovim" : "Mening joriy qarzim"}
          </div>
          <p className="mt-2 text-2xl leading-tight font-bold break-words">
            {formatMoney(debtCents < 0n ? -debtCents : debtCents, me.currency)}
          </p>
          {overdue && (
            <p className="mt-1.5 rounded-lg bg-black/15 px-2 py-1 text-xs">
              Muddati o'tgan: {formatMoney(me.overdue_amount, me.currency)}
            </p>
          )}
        </Card>
        <Card>
          <div className="flex items-center gap-2 text-sm text-tg-hint">
            <CalendarClock className="size-4" aria-hidden />
            Oxirgi to'lov
          </div>
          {me.last_payment ? (
            <>
              <p className="mt-2 text-2xl leading-tight font-bold break-words">
                {formatMoney(me.last_payment.amount, me.currency)}
              </p>
              <p className="mt-1 text-sm text-tg-hint">
                {formatDate(me.last_payment.paid_at)} · {PAYMENT_METHOD_LABEL[me.last_payment.method]}
              </p>
            </>
          ) : (
            <p className="mt-2 text-[15px] text-tg-hint">Hali to'lov qilinmagan</p>
          )}
        </Card>
      </div>

      <SectionTitle>Do'kon</SectionTitle>
      <Card className="divide-y divide-tg-separator py-1">
        <div className="flex items-center gap-3 py-2.5">
          <span className="inline-flex size-10 items-center justify-center rounded-full bg-tg-secondary text-tg-accent">
            <Store className="size-5" aria-hidden />
          </span>
          <div className="min-w-0">
            <p className="truncate font-semibold">{me.store_name}</p>
            <p className="truncate text-sm text-tg-hint">{me.contact_name ?? "Egasi ko'rsatilmagan"}</p>
          </div>
        </div>
        <InfoRow label="Yetkazib beruvchi" value={me.wholesaler_name} />
        <InfoRow label="Kredit limiti" value={formatMoney(me.credit_limit, me.currency)} />
        <InfoRow label="Bo'sh limit" value={formatMoney(me.available_credit, me.currency)} />
      </Card>

      <SectionTitle>Akt-sverka</SectionTitle>
      <Card className="space-y-3">
        <div className="grid grid-cols-3 gap-1 rounded-xl bg-tg-secondary p-1" role="radiogroup" aria-label="Davr">
          {PERIOD_OPTIONS.map((option) => (
            <button
              key={option.id}
              type="button"
              role="radio"
              aria-checked={periodId === option.id}
              onClick={() => setPeriodId(option.id)}
              className={cx(
                "rounded-lg py-2 text-sm font-medium",
                periodId === option.id ? "bg-tg-section text-tg-text shadow-sm" : "text-tg-hint",
              )}
            >
              {option.label}
            </button>
          ))}
        </div>
        <Button block loading={downloading} icon={<FileDown className="size-5" />} onClick={download}>
          Akt-sverka yuklab olish (PDF)
        </Button>
      </Card>
    </div>
  );
}
