import { CheckCircle2, XCircle } from "lucide-react";
import { useEffect } from "react";
import { create } from "zustand";

interface Toast {
  id: number;
  kind: "success" | "error";
  message: string;
}

interface ToastState {
  toasts: Toast[];
  push: (kind: Toast["kind"], message: string) => void;
  dismiss: (id: number) => void;
}

let nextId = 1;

const useToastStore = create<ToastState>((set) => ({
  toasts: [],
  push: (kind, message) =>
    set((s) => ({ toasts: [...s.toasts.slice(-2), { id: nextId++, kind, message }] })),
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));

export const toast = {
  success: (message: string) => useToastStore.getState().push("success", message),
  error: (message: string) => useToastStore.getState().push("error", message),
};

function ToastItem({ toast: t }: { toast: Toast }) {
  const dismiss = useToastStore((s) => s.dismiss);
  useEffect(() => {
    const timer = window.setTimeout(() => dismiss(t.id), 3500);
    return () => window.clearTimeout(timer);
  }, [t.id, dismiss]);
  const Icon = t.kind === "success" ? CheckCircle2 : XCircle;
  return (
    <button
      type="button"
      onClick={() => dismiss(t.id)}
      className="flex w-full items-center gap-2 rounded-2xl bg-tg-text/90 px-4 py-3 text-left text-sm text-tg-bg shadow-lg"
    >
      <Icon className="size-5 shrink-0" aria-hidden />
      {t.message}
    </button>
  );
}

export function ToastHost() {
  const toasts = useToastStore((s) => s.toasts);
  return (
    <div
      aria-live="polite"
      className="pointer-events-none fixed inset-x-0 top-3 z-50 mx-auto flex max-w-lg flex-col gap-2 px-4 [&>*]:pointer-events-auto"
    >
      {toasts.map((t) => (
        <ToastItem key={t.id} toast={t} />
      ))}
    </div>
  );
}
