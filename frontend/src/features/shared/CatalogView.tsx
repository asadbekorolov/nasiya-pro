import { PackageSearch, Search, ShoppingCart } from "lucide-react";
import { useEffect, useState } from "react";

import type { CatalogQuery } from "@/api/endpoints";
import type { Product } from "@/api/types";
import { Button, cx, EmptyState, ErrorState, Spinner, Stepper } from "@/components/ui";
import { formatMoney, formatQuantity, toMilli } from "@/lib/money";
import { unitLabel } from "@/lib/format";
import { useAsync } from "@/lib/useAsync";
import { useCart } from "@/state/cart";

const PAGE = 50;

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), ms);
    return () => window.clearTimeout(timer);
  }, [value, ms]);
  return debounced;
}

function ProductRow({ product, scope }: { product: Product; scope: string }) {
  const cart = useCart(scope);
  const quantity = cart.quantityOf(product.id);
  const maxUnits = Number(toMilli(product.available_quantity) / 1000n);
  const outOfStock = maxUnits < 1;
  return (
    <li className="flex items-center gap-3 px-4 py-3">
      <div className="min-w-0 flex-1">
        <p className="truncate text-[15px] font-medium">{product.name}</p>
        <p className="text-[15px] font-semibold text-tg-accent">
          {formatMoney(product.price)}
          <span className="font-normal text-tg-hint"> / {unitLabel(product.unit)}</span>
        </p>
        <p className={cx("text-xs", outOfStock ? "text-tg-destructive" : "text-tg-hint")}>
          {outOfStock
            ? "Omborda qolmagan"
            : `Qoldiq: ${formatQuantity(product.available_quantity)} ${unitLabel(product.unit)}`}
        </p>
      </div>
      <Stepper
        label={product.name}
        value={quantity}
        max={maxUnits}
        onChange={(next) => cart.setQuantity(product, next)}
      />
    </li>
  );
}

/** Searchable product list with +/- steppers and a floating cart bar. */
export function CatalogView({
  scope,
  loadCatalog,
  onOpenCart,
}: {
  scope: string;
  loadCatalog: (query: CatalogQuery) => Promise<Product[]>;
  onOpenCart: () => void;
}) {
  const [search, setSearch] = useState("");
  const [inStock, setInStock] = useState(false);
  const [extra, setExtra] = useState<Product[]>([]);
  const [exhausted, setExhausted] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const q = useDebounced(search.trim(), 300);
  const cart = useCart(scope);

  const page = useAsync(async () => {
    const rows = await loadCatalog({ q, in_stock: inStock, limit: PAGE });
    setExtra([]);
    setExhausted(rows.length < PAGE);
    return rows;
  }, [q, inStock]);

  const products = [...(page.data ?? []), ...extra];

  async function loadMore() {
    setLoadingMore(true);
    try {
      const rows = await loadCatalog({ q, in_stock: inStock, limit: PAGE, offset: products.length });
      setExtra((prev) => [...prev, ...rows]);
      setExhausted(rows.length < PAGE);
    } finally {
      setLoadingMore(false);
    }
  }

  return (
    <div className="flex flex-1 flex-col">
      <div className="sticky top-0 z-10 space-y-2 bg-tg-secondary px-4 pt-1 pb-2">
        <label className="flex items-center gap-2 rounded-xl bg-tg-section px-3">
          <Search className="size-5 text-tg-hint" aria-hidden />
          <input
            type="search"
            inputMode="search"
            enterKeyHint="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Tovar nomi yoki artikul"
            className="h-11 w-full bg-transparent text-[15px] outline-none placeholder:text-tg-hint"
          />
        </label>
        <label className="flex items-center gap-2 px-1 text-sm text-tg-hint">
          <input
            type="checkbox"
            checked={inStock}
            onChange={(e) => setInStock(e.target.checked)}
            className="size-4 accent-[var(--color-tg-button)]"
          />
          Faqat omborda borlari
        </label>
      </div>

      {page.loading && !page.data ? (
        <Spinner />
      ) : page.error ? (
        <ErrorState message={page.error.message} onRetry={page.reload} />
      ) : products.length === 0 ? (
        <EmptyState icon={<PackageSearch />} title="Tovar topilmadi" hint="Qidiruvni o'zgartirib ko'ring" />
      ) : (
        <div className="px-4 pb-28">
          <ul className="divide-y divide-tg-separator overflow-hidden rounded-2xl bg-tg-section">
            {products.map((p) => (
              <ProductRow key={p.id} product={p} scope={scope} />
            ))}
          </ul>
          {!exhausted && (
            <Button variant="ghost" block loading={loadingMore} onClick={loadMore} className="mt-2">
              Ko'proq ko'rsatish
            </Button>
          )}
        </div>
      )}

      {cart.count > 0 && (
        <div className="pointer-events-none fixed inset-x-0 bottom-20 z-30 mx-auto max-w-lg px-4">
          <button
            type="button"
            onClick={onOpenCart}
            className="pointer-events-auto flex w-full items-center gap-3 rounded-2xl bg-tg-button px-4 py-3.5 text-tg-button-text shadow-lg active:scale-[0.99]"
          >
            <ShoppingCart className="size-5" aria-hidden />
            <span className="flex-1 text-left font-semibold">Savat · {cart.count}</span>
            <span className="font-bold">{formatMoney(cart.totalCents)}</span>
          </button>
        </div>
      )}
    </div>
  );
}
