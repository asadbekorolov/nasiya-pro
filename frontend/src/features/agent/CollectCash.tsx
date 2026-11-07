import { BadgeCheck, KeyRound, Send } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { ApiError } from "@/api/client";
import { agentApi } from "@/api/endpoints";
import type { AgentStore, OtpIssue, PaymentResult } from "@/api/types";
import { Button, Card, InlineError } from "@/components/ui";
import { formatDateTime, PAYMENT_METHOD_LABEL } from "@/lib/format";
import { formatMoney, isPositiveMoneyInput, normalizeMoneyInput, toCents } from "@/lib/money";
import { haptic } from "@/telegram/twa";

type Step =
  | { kind: "amount" }
  | { kind: "code"; otp: OtpIssue }
  | { kind: "receipt"; result: PaymentResult };

function useSecondsLeft(expiresAt: string | undefined): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!expiresAt) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [expiresAt]);
  if (!expiresAt) return 0;
  return Math.max(0, Math.ceil((new Date(expiresAt).getTime() - now) / 1000));
}

function errorText(error: unknown): string {
  if (!(error instanceof ApiError)) return "Xatolik yuz berdi";
  switch (error.code) {
    case "otp_invalid_code": {
      const left = Number(error.details.attempts_left ?? 0);
      return `Kod noto'g'ri. Qolgan urinishlar: ${left}`;
    }
    case "otp_attempts_exceeded":
      return "Urinishlar tugadi. Yangi kod so'rang.";
    case "otp_expired":
      return "Kodning muddati tugadi. Yangi kod so'rang.";
    case "store_not_linked_to_telegram":
      return "Do'konchi Telegram akkauntini ulamagan — kod yuborib bo'lmaydi.";
    case "notification_delivery_failed":
      return "Kodni do'konchiga yuborib bo'lmadi. Birozdan keyin qayta urinib ko'ring.";
    default:
      return error.message;
  }
}

const NEEDS_NEW_CODE = new Set(["otp_attempts_exceeded", "otp_expired"]);

/**
 * Cash collection with store confirmation:
 *   1. agent enters the amount -> a 4-digit code goes to the store owner's Telegram
 *   2. store owner tells the code -> agent enters it -> payment is posted (FIFO)
 *   3. receipt with the new balance
 */
export function CollectCash({
  store,
  onDone,
}: {
  store: AgentStore;
  onDone: (result: PaymentResult | null) => void;
}) {
  const [step, setStep] = useState<Step>({ kind: "amount" });
  const [amount, setAmount] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [needsNewCode, setNeedsNewCode] = useState(false);
  const codeInput = useRef<HTMLInputElement>(null);
  const secondsLeft = useSecondsLeft(step.kind === "code" ? step.otp.expires_at : undefined);

  const debtCents = toCents(store.current_debt);
  const amountValid = isPositiveMoneyInput(amount);

  useEffect(() => {
    if (step.kind === "code") codeInput.current?.focus();
  }, [step.kind]);

  async function requestCode() {
    if (!amountValid) return;
    setBusy(true);
    setError(undefined);
    setNeedsNewCode(false);
    try {
      const otp = await agentApi.requestOtp(store.id, normalizeMoneyInput(amount));
      setCode("");
      setStep({ kind: "code", otp });
    } catch (err) {
      haptic.error();
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function verify(otp: OtpIssue) {
    if (!/^\d{4}$/.test(code)) return;
    setBusy(true);
    setError(undefined);
    try {
      const result = await agentApi.verifyOtp(otp.id, code);
      haptic.success();
      setStep({ kind: "receipt", result });
    } catch (err) {
      haptic.error();
      setError(errorText(err));
      if (err instanceof ApiError && NEEDS_NEW_CODE.has(err.code)) setNeedsNewCode(true);
      setCode("");
    } finally {
      setBusy(false);
    }
  }

  if (step.kind === "receipt") {
    const { result } = step;
    const paid = -toCents(result.transaction.amount);
    const closed = result.allocations.filter((a) => a.closed).length;
    return (
      <div className="space-y-3 px-4 pb-6">
        <Card className="text-center">
          <BadgeCheck className="mx-auto size-14 text-emerald-500" aria-hidden />
          <p className="mt-2 text-sm text-tg-hint">To'lov qabul qilindi</p>
          <p className="text-3xl font-bold">{formatMoney(paid)}</p>
          <p className="mt-1 text-sm text-tg-hint">{store.name}</p>
        </Card>
        <Card className="divide-y divide-tg-separator py-1 text-[15px]">
          {[
            ["Kvitansiya", `№ ${result.transaction.sequence_no} · ${result.transaction.entry_hash.slice(0, 8).toUpperCase()}`],
            ["Sana", formatDateTime(result.transaction.created_at)],
            ["To'lov turi", PAYMENT_METHOD_LABEL[result.transaction.payment_method ?? "cash"]],
            ["Yopilgan qarzlar", closed ? `${closed} ta` : "qisman"],
            ["Yangi qarz qoldig'i", formatMoney(result.store_balance)],
            ...(toCents(result.advance_amount) > 0n
              ? [["Avans", formatMoney(result.advance_amount)] as [string, string]]
              : []),
          ].map(([label, value]) => (
            <div key={label} className="flex justify-between gap-3 py-2.5">
              <span className="text-tg-hint">{label}</span>
              <span className="text-right font-medium">{value}</span>
            </div>
          ))}
        </Card>
        <Button block onClick={() => onDone(result)}>
          Tayyor
        </Button>
      </div>
    );
  }

  if (step.kind === "code") {
    const { otp } = step;
    const minutes = Math.floor(secondsLeft / 60);
    const seconds = String(secondsLeft % 60).padStart(2, "0");
    const expired = secondsLeft === 0;
    return (
      <div className="space-y-3 px-4 pb-6">
        <Card className="space-y-1 text-center">
          <KeyRound className="mx-auto size-10 text-tg-accent" aria-hidden />
          <p className="font-semibold">Kod do'konchining Telegramiga yuborildi</p>
          <p className="text-sm text-tg-hint">
            {store.name} egasidan {formatMoney(otp.amount)} uchun 4 xonali kodni so'rang.
          </p>
        </Card>
        <Card className="space-y-3">
          <label className="block">
            <span className="sr-only">Tasdiqlash kodi</span>
            <input
              ref={codeInput}
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 4))}
              onKeyDown={(e) => e.key === "Enter" && void verify(otp)}
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="\d{4}"
              maxLength={4}
              placeholder="••••"
              disabled={needsNewCode}
              className="h-16 w-full rounded-xl bg-tg-secondary text-center text-3xl font-bold tracking-[0.6em] outline-none placeholder:text-tg-hint"
            />
          </label>
          <p className={expired ? "text-center text-sm text-tg-destructive" : "text-center text-sm text-tg-hint"}>
            {expired ? "Kod muddati tugadi" : `Kod ${minutes}:${seconds} amal qiladi`}
          </p>
          <InlineError message={error} />
          {needsNewCode || expired ? (
            <Button block loading={busy} icon={<Send className="size-5" />} onClick={requestCode}>
              Yangi kod so'rash
            </Button>
          ) : (
            <Button block loading={busy} disabled={code.length !== 4} onClick={() => verify(otp)}>
              Tasdiqlash
            </Button>
          )}
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-3 px-4 pb-6">
      <Card className="flex items-center justify-between">
        <span className="text-tg-hint">Joriy qarz</span>
        <span className="text-lg font-bold">{formatMoney(store.current_debt)}</span>
      </Card>
      <Card className="space-y-3">
        <label className="block space-y-1.5">
          <span className="text-sm text-tg-hint">Qabul qilingan naqd pul (so'm)</span>
          <input
            value={amount}
            onChange={(e) => setAmount(e.target.value.replace(/[^\d\s,.]/g, ""))}
            inputMode="decimal"
            placeholder="0"
            autoFocus
            className="h-14 w-full rounded-xl bg-tg-secondary px-4 text-2xl font-bold outline-none placeholder:text-tg-hint"
          />
        </label>
        {debtCents > 0n && (
          <button
            type="button"
            onClick={() => setAmount(store.current_debt.replace(/\.00$/, ""))}
            className="text-sm font-medium text-tg-link"
          >
            To'liq qarz: {formatMoney(store.current_debt)}
          </button>
        )}
        <InlineError message={error} />
        <Button block loading={busy} disabled={!amountValid} icon={<Send className="size-5" />} onClick={requestCode}>
          Do'konchiga kod yuborish
        </Button>
        <p className="text-center text-xs text-tg-hint">
          To'lov faqat do'konchi kodni aytgandan keyin hisobga olinadi.
        </p>
      </Card>
    </div>
  );
}
