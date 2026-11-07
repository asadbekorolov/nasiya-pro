import { Ban, CheckCircle2, ChevronRight, Inbox, PackageCheck, User } from "lucide-react";
import { useState } from "react";

import { ApiError } from "@/api/client";
import { adminApi } from "@/api/endpoints";
import type { AdminStore, Order, OrderStatus } from "@/api/types";
import { Button, EmptyState, ErrorState, InlineError, Sheet, Spinner } from "@/components/ui";
import { toast } from "@/components/toast";
import { formatDateTime, unitLabel } from "@/lib/format";
import { formatMoney, formatQuantity } from "@/lib/money";
import { useAsync } from "@/lib/useAsync";
import { confirm, haptic } from "@/telegram/twa";

import { Badge, Segmented } from "./widgets";

type Filter = "pending" | "approved" | "delivered" | "cancelled";

const FILTER_STATUSES: Record<Filter, OrderStatus[]> = {
  pending: ["pending"],
  approved: ["approved", "shipped"],
  delivered: ["delivered"],
  cancelled: ["cancelled"],
};

const STATUS_BADGE: Record<OrderStatus, { label: string; tone: "info" | "warning" | "success" | "danger" | "neutral" }> = {
  draft: { label: "Qoralama", tone: "neutral" },
  pending: { label: "Yangi", tone: "warning" },
  approved: { label: "Tasdiqlangan", tone: "info" },
  shipped: { label: "Yo'lda", tone: "info" },
  delivered: { label: "Yetkazilgan", tone: "success" },
  cancelled: { label: "Bekor qilingan", tone: "neutral" },
};

type Action = "approve" | "deliver" | "cancel";

/** Human message for the domain errors an admin action can hit. */
function describe(error: unknown): string {
  if (!(error instanceof ApiError)) return "Xatolik yuz berdi";
  if (error.code === "insufficient_stock") {
    const shortages = (error.details.shortages ?? []) as Array<{ product_name: string; available: string; requested: string }>;
    const list = shortages
      .map((s) => `${s.product_name}: bor ${formatQuantity(s.available)}, kerak ${formatQuantity(s.requested)}`)
      .join("; ");
    return `Omborda yetarli tovar yo'q. ${list}`;
  }
  if (error.code === "invalid_order_state") return "Buyurtma holati o'zgargan. Ro'yxat yangilandi.";
  return error.message;
}

export function OrdersTab({
  stores,
  pendingCount,
  onChanged,
}: {
  stores: AdminStore[];
  pendingCount: number;
  onChanged: () => void;
}) {
  const [filter, setFilter] = useState<Filter>("pending");
  const orders = useAsync(() => adminApi.orders(FILTER_STATUSES[filter]), [filter]);
  const [openId, setOpenId] = useState<string | null>(null);
  const [busy, setBusy] = useState<{ id: string; action: Action } | null>(null);
  const [sheetError, setSheetError] = useState<string>();
  const [reason, setReason] = useState("");

  const storeName = (id: string) => stores.find((s) => s.id === id)?.name ?? "Do'kon";
  const open = orders.data?.find((o) => o.id === openId);

  function closeSheet() {
    setOpenId(null);
    setSheetError(undefined);
    setReason("");
  }

  async function run(order: Order, action: Action) {
    setSheetError(undefined);
    const store = storeName(order.store_id);
    try {
      if (action === "approve") {
        setBusy({ id: order.id, action });
        try {
          await adminApi.approveOrder(order.id);
        } catch (error) {
          if (!(error instanceof ApiError) || error.code !== "credit_limit_exceeded") throw error;
          const d = error.details as Record<string, string>;
          setBusy(null);
          const ok = await confirm(
            `${store} kredit limitidan oshadi.\nLimit: ${formatMoney(d.credit_limit ?? "0")}\n` +
              `Joriy qarz: ${formatMoney(d.current_debt ?? "0")}\nBuyurtma: ${formatMoney(order.total_amount)}\n\n` +
              "Limitdan qat'i nazar tasdiqlaysizmi?",
          );
          if (!ok) return;
          setBusy({ id: order.id, action });
          await adminApi.approveOrder(order.id, false);
        }
        toast.success(`#${order.order_number} tasdiqlandi, tovar band qilindi`);
      } else if (action === "deliver") {
        const ok = await confirm(
          `#${order.order_number} yetkazildi deb belgilansinmi?\n${store} hisobiga ${formatMoney(order.total_amount)} qarz yoziladi.`,
        );
        if (!ok) return;
        setBusy({ id: order.id, action });
        const result = await adminApi.deliverOrder(order.id);
        toast.success(`Yetkazildi. ${store} qarzi: ${formatMoney(result.charge.store_balance)}`);
      } else {
        const ok = await confirm(`#${order.order_number} buyurtmani bekor qilasizmi?`);
        if (!ok) return;
        setBusy({ id: order.id, action });
        await adminApi.cancelOrder(order.id, reason.trim());
        toast.success(`#${order.order_number} bekor qilindi`);
      }
      haptic.success();
      closeSheet();
      onChanged();
      await orders.reload();
    } catch (error) {
      haptic.error();
      const message = describe(error);
      if (openId === order.id) setSheetError(message);
      else toast.error(message);
      if (error instanceof ApiError && error.code === "invalid_order_state") await orders.reload();
    } finally {
      setBusy(null);
    }
  }

  const actionsFor = (order: Order, compact: boolean) => {
    const loading = (a: Action) => busy?.id === order.id && busy.action === a;
    const disabled = busy !== null;
    const size = compact ? "min-h-10 flex-1 text-sm" : "flex-1";
    if (order.status === "pending") {
      return (
        <>
          <Button className={size} icon={<CheckCircle2 className="size-4" />} loading={loading("approve")} disabled={disabled} onClick={() => void run(order, "approve")}>
            Tasdiqlash
          </Button>
          <Button className={size} variant="danger" icon={<Ban className="size-4" />} loading={loading("cancel")} disabled={disabled} onClick={() => void run(order, "cancel")}>
            Bekor qilish
          </Button>
        </>
      );
    }
    if (order.status === "approved" || order.status === "shipped") {
      return (
        <>
          <Button className={size} icon={<PackageCheck className="size-4" />} loading={loading("deliver")} disabled={disabled} onClick={() => void run(order, "deliver")}>
            Yetkazildi
          </Button>
          <Button className={size} variant="danger" icon={<Ban className="size-4" />} loading={loading("cancel")} disabled={disabled} onClick={() => void run(order, "cancel")}>
            Bekor qilish
          </Button>
        </>
      );
    }
    return null;
  };

  return (
    <div className="space-y-3">
      <Segmented
        label="Buyurtma holati"
        value={filter}
        onChange={(f) => {
          closeSheet();
          setFilter(f);
        }}
        options={[
          { id: "pending", label: "Yangi", count: pendingCount },
          { id: "approved", label: "Tasdiqlangan" },
          { id: "delivered", label: "Yetkazilgan" },
          { id: "cancelled", label: "Bekor" },
        ]}
      />

      {orders.loading && !orders.data ? (
        <Spinner />
      ) : orders.error ? (
        <ErrorState message={orders.error.message} onRetry={orders.reload} />
      ) : (orders.data ?? []).length === 0 ? (
        <EmptyState
          icon={<Inbox />}
          title={filter === "pending" ? "Yangi buyurtmalar yo'q" : "Bu holatda buyurtma yo'q"}
          hint={filter === "pending" ? "Do'konlar yoki agentlar buyurtma berganda shu yerda paydo bo'ladi" : undefined}
        />
      ) : (
        <ul className="grid gap-3 lg:grid-cols-2">
          {(orders.data ?? []).map((order) => {
            const badge = STATUS_BADGE[order.status];
            const actions = actionsFor(order, true);
            return (
              <li key={order.id} className="flex flex-col overflow-hidden rounded-2xl bg-tg-section">
                <button
                  type="button"
                  onClick={() => setOpenId(order.id)}
                  className="flex w-full items-start gap-3 px-4 pt-3 pb-2 text-left active:bg-tg-secondary"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="font-semibold">#{order.order_number}</span>
                      <Badge tone={badge.tone}>{badge.label}</Badge>
                      {order.agent_id && (
                        <Badge>
                          <User className="size-3" aria-hidden />
                          agent
                        </Badge>
                      )}
                    </div>
                    <p className="mt-1 truncate font-medium">{storeName(order.store_id)}</p>
                    <p className="text-sm text-tg-hint">
                      {formatDateTime(order.created_at)} · {order.items.length} xil tovar
                    </p>
                  </div>
                  <div className="flex items-center gap-1">
                    <p className="text-lg font-bold">{formatMoney(order.total_amount)}</p>
                    <ChevronRight className="size-5 text-tg-hint" aria-hidden />
                  </div>
                </button>
                {actions && <div className="mt-auto flex gap-2 px-4 pb-3">{actions}</div>}
              </li>
            );
          })}
        </ul>
      )}

      <Sheet
        open={open !== undefined}
        title={open ? `Buyurtma #${open.order_number}` : ""}
        onClose={closeSheet}
        footer={open && actionsFor(open, false) ? <div className="flex gap-2 pb-1">{actionsFor(open, false)}</div> : undefined}
      >
        {open && (
          <div className="space-y-3 pb-4">
            <div className="flex items-center justify-between gap-3">
              <div className="min-w-0">
                <p className="truncate font-semibold">{storeName(open.store_id)}</p>
                <p className="text-sm text-tg-hint">{formatDateTime(open.created_at)}</p>
              </div>
              <Badge tone={STATUS_BADGE[open.status].tone}>{STATUS_BADGE[open.status].label}</Badge>
            </div>

            <ul className="divide-y divide-tg-separator rounded-2xl bg-tg-secondary">
              {open.items.map((item) => (
                <li key={item.id} className="flex items-center gap-3 px-3 py-2.5 text-sm">
                  <div className="min-w-0 flex-1">
                    <p className="truncate font-medium">{item.product_name}</p>
                    <p className="text-tg-hint">
                      {formatQuantity(item.quantity)} {unitLabel(item.unit)} × {formatMoney(item.unit_price)}
                    </p>
                  </div>
                  <p className="font-semibold">{formatMoney(item.line_total)}</p>
                </li>
              ))}
            </ul>
            <div className="flex items-baseline justify-between px-1">
              <span className="text-tg-hint">Jami</span>
              <span className="text-xl font-bold">{formatMoney(open.total_amount)}</span>
            </div>

            {open.note && (
              <p className="rounded-xl bg-tg-secondary px-3 py-2 text-sm">
                <span className="text-tg-hint">Izoh: </span>
                {open.note}
              </p>
            )}
            {open.cancel_reason && (
              <p className="text-sm text-tg-hint">Bekor qilish sababi: {open.cancel_reason}</p>
            )}

            {(open.status === "pending" || open.status === "approved" || open.status === "shipped") && (
              <label className="block">
                <span className="px-1 text-xs text-tg-hint">Bekor qilish sababi (ixtiyoriy)</span>
                <input
                  value={reason}
                  maxLength={500}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder="Masalan: do'kon rad etdi"
                  className="mt-1 h-11 w-full rounded-xl bg-tg-secondary px-3 text-[15px] outline-none placeholder:text-tg-hint"
                />
              </label>
            )}
            <InlineError message={sheetError} />
          </div>
        )}
      </Sheet>
    </div>
  );
}
