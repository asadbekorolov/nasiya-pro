/**
 * Shopping carts, one per scope:
 *   "store"          — the store owner's own cart
 *   "agent:<storeId>" — an agent taking an order on the spot for a store
 *
 * Persisted to localStorage (survives the Mini App being closed). Each cart keeps an
 * idempotency key: retrying an unchanged cart reuses it (a lost response can never
 * create the order twice), and any change to the cart rotates it.
 */
import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

import type { Product } from "@/api/types";
import { newIdempotencyKey } from "@/lib/format";
import { fromCents, lineTotalCents, toMilli } from "@/lib/money";

export interface CartLine {
  product: Pick<Product, "id" | "name" | "price" | "unit" | "available_quantity">;
  quantity: number; // whole units; the UI uses +/- steppers
}

interface Cart {
  lines: Record<string, CartLine>;
  idempotencyKey: string;
}

interface CartState {
  carts: Record<string, Cart>;
  setQuantity: (scope: string, product: CartLine["product"], quantity: number) => void;
  clear: (scope: string) => void;
}

const emptyCart = (): Cart => ({ lines: {}, idempotencyKey: newIdempotencyKey("order") });

function maxUnits(product: CartLine["product"]): number {
  // available_quantity is a decimal string with 3 fraction digits
  return Number(toMilli(product.available_quantity) / 1000n);
}

const safeStorage = createJSONStorage(() => {
  try {
    const probe = "__nasiya_probe__";
    window.localStorage.setItem(probe, "1");
    window.localStorage.removeItem(probe);
    return window.localStorage;
  } catch {
    const memory = new Map<string, string>();
    return {
      getItem: (k: string) => memory.get(k) ?? null,
      setItem: (k: string, v: string) => void memory.set(k, v),
      removeItem: (k: string) => void memory.delete(k),
    };
  }
});

export const useCartStore = create<CartState>()(
  persist(
    (set) => ({
      carts: {},
      setQuantity: (scope, product, quantity) =>
        set((state) => {
          const cart = state.carts[scope] ?? emptyCart();
          const lines = { ...cart.lines };
          const clamped = Math.max(0, Math.min(Math.floor(quantity), maxUnits(product)));
          if (clamped === 0) delete lines[product.id];
          else lines[product.id] = { product, quantity: clamped };
          // A different cart is a different order: never reuse the old retry key for it.
          return {
            carts: {
              ...state.carts,
              [scope]: { lines, idempotencyKey: newIdempotencyKey("order") },
            },
          };
        }),
      clear: (scope) =>
        set((state) => ({ carts: { ...state.carts, [scope]: emptyCart() } })),
    }),
    { name: "nasiya-pro-carts", version: 1, storage: safeStorage },
  ),
);

export function useCart(scope: string) {
  const cart = useCartStore((s) => s.carts[scope]);
  const setQuantity = useCartStore((s) => s.setQuantity);
  const clear = useCartStore((s) => s.clear);
  const lines = Object.values(cart?.lines ?? {});
  const totalCents = lines.reduce(
    (sum, line) => sum + lineTotalCents(line.product.price, String(line.quantity)),
    0n,
  );
  return {
    lines,
    count: lines.reduce((n, l) => n + l.quantity, 0),
    totalCents,
    total: fromCents(totalCents),
    idempotencyKey: cart?.idempotencyKey ?? "",
    quantityOf: (productId: string) => cart?.lines[productId]?.quantity ?? 0,
    setQuantity: (product: CartLine["product"], quantity: number) =>
      setQuantity(scope, product, quantity),
    clear: () => clear(scope),
  };
}
