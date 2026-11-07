import { AlertTriangle, Trash2 } from "lucide-react";
import { useState } from "react";

import { ApiError } from "@/api/client";
import type { Order, OrderLineIn } from "@/api/types";
import { toast } from "@/components/toast";
import { Button, InlineError, Sheet, Stepper } from "@/components/ui";
import { unitLabel } from "@/lib/format";
import { formatMoney, fromCents, lineTotalCents, toCents, toMilli } from "@/lib/money";
import { useCart } from "@/state/cart";
import { haptic } from "@/telegram/twa";

export function describeOrderError(error: unknown): string {
  if (!(error instanceof ApiError)) return "Buyurtmani yuborib bo'lmadi";
  if (error.code === "insufficient_stock") {
    const shortages = (error.details.shortages ?? []) as Array<{ product_name: string; available: string }>;
    const names = shortages.map((s) => `${s.product_name} (bor: ${s.available})`).join(", ");
    return `Omborda yetarli emas: ${names}`;
  }
  if (error.code === "product_unavailable") return "Ba'zi tovarlar endi sotilmaydi. Savatni yangilang.";
  return error.message;
}

/**
 * Cart review + checkout. `availableCredit` (store owner only) shows a warning when the
 * order exceeds the remaining limit; the binding check is done by the wholesaler's
 * approval on the server.
 */
export function CartSheet({
  scope,
  open,
  onClose,
  title = "Savat",
  availableCredit,
  submit,
  onOrdered,
}: {
  scope: string;
  open: boolean;
  onClose: () => void;
  title?: string;
  availableCredit?: string;
  submit: (lines: OrderLineIn[], idempotencyKey: string) => Promise<Order>;
  onOrdered?: (order: Order) => void;
}) {
  const cart = useCart(scope);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string>();

  const overLimit =
    availableCredit !== undefined && cart.totalCents > toCents(availableCredit)
      ? cart.totalCents - toCents(availableCredit)
      : 0n;

  async function placeOrder() {
    setSending(true);
    setError(undefined);
    try {
      const order = await submit(
        cart.lines.map((l) => ({ product_id: l.product.id, quantity: String(l.quantity) })),
        cart.idempotencyKey,
      );
      haptic.success();
      cart.clear();
      toast.success(`Buyurtma #${order.order_number} yuborildi`);
      onOrdered?.(order);
      onClose();
    } catch (err) {
      haptic.error();
      setError(describeOrderError(err));
    } finally {
      setSending(false);
    }
  }

  return (
    <Sheet
      open={open}
      title={title}
      onClose={onClose}
      footer={
        <div className="space-y-3 pb-1">
          {overLimit > 0n && (
            <p className="flex gap-2 rounded-xl bg-amber-500/12 px-3 py-2.5 text-sm text-amber-700 dark:text-amber-400">
              <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
              Kredit limitidan {formatMoney(overLimit)} oshadi. Ulgurji sotuvchi buyurtmani
              tasdiqlamasligi mumkin.
            </p>
          )}
          <InlineError message={error} />
          <div className="flex items-baseline justify-between">
            <span className="text-tg-hint">Jami</span>
            <span className="text-xl font-bold">{formatMoney(cart.totalCents)}</span>
          </div>
          <Button block loading={sending} disabled={cart.count === 0} onClick={placeOrder}>
            Buyurtma berish
          </Button>
        </div>
      }
    >
      {cart.lines.length === 0 ? (
        <p className="py-10 text-center text-tg-hint">Savat bo'sh</p>
      ) : (
        <ul className="divide-y divide-tg-separator">
          {cart.lines.map((line) => (
            <li key={line.product.id} className="flex items-center gap-3 py-3">
              <div className="min-w-0 flex-1">
                <p className="truncate font-medium">{line.product.name}</p>
                <p className="text-sm text-tg-hint">
                  {line.quantity} {unitLabel(line.product.unit)} × {formatMoney(line.product.price)}
                </p>
                <p className="text-sm font-semibold">
                  {formatMoney(fromCents(lineTotalCents(line.product.price, String(line.quantity))))}
                </p>
              </div>
              <Stepper
                label={line.product.name}
                value={line.quantity}
                max={Number(toMilli(line.product.available_quantity) / 1000n)}
                onChange={(q) => cart.setQuantity(line.product, q)}
              />
            </li>
          ))}
        </ul>
      )}
      {cart.lines.length > 0 && (
        <Button
          variant="danger"
          className="my-2"
          icon={<Trash2 className="size-4" />}
          onClick={cart.clear}
        >
          Savatni tozalash
        </Button>
      )}
    </Sheet>
  );
}
