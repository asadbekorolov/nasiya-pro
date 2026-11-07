import { Lock, Smartphone } from "lucide-react";
import { lazy, Suspense } from "react";

import { ApiError } from "@/api/client";
import { authApi } from "@/api/endpoints";
import type { Me, Role } from "@/api/types";
import { EmptyState, ErrorState, Screen, Spinner } from "@/components/ui";
import { useAsync } from "@/lib/useAsync";
import { getInitData, tg } from "@/telegram/twa";

// Each role only downloads its own screens.
const StoreApp = lazy(() => import("@/features/store/StoreApp"));
const AgentApp = lazy(() => import("@/features/agent/AgentApp"));
const AdminApp = lazy(() => import("@/features/admin/AdminApp"));

// The bot opens the Mini App at /admin, /agent or /store (the person's own section).
const ROLE_BY_PATH: Record<string, Role> = {
  admin: "wholesaler_admin",
  agent: "sales_agent",
  store: "store_owner",
};

function requestedRole(): Role | undefined {
  return ROLE_BY_PATH[window.location.pathname.split("/")[1] ?? ""];
}

/** A path is only a hint: without that role the user simply gets their own cabinet. */
async function loadMe(): Promise<Me> {
  const role = requestedRole();
  if (!role) return authApi.me();
  try {
    return await authApi.me(role);
  } catch (error) {
    if (error instanceof ApiError && error.status === 403) return authApi.me();
    throw error;
  }
}

/**
 * Asks the API who the Telegram user is (`GET /auth/me`) and opens the matching
 * cabinet. The backend is the only authority: the role is never inferred locally.
 */
export function RoleGuard() {
  const me = useAsync(loadMe);

  if (!getInitData()) {
    return (
      <Screen>
        <EmptyState
          icon={<Smartphone />}
          title="Ilovani Telegram orqali oching"
          hint="Nasiya Pro faqat Telegram bot ichidagi Mini App sifatida ishlaydi."
        />
      </Screen>
    );
  }

  if (me.loading && !me.data) return <Spinner label="Kirilmoqda…" />;

  if (me.error) {
    if (me.error.status === 403) {
      const id = tg.initDataUnsafe?.user?.id;
      return (
        <Screen>
          <EmptyState
            icon={<Lock />}
            title="Sizga hali ruxsat berilmagan"
            hint={`Ulgurji sotuvchingizdan sizni tizimga qo'shishni so'rang.${id ? ` Telegram ID: ${id}` : ""}`}
          />
        </Screen>
      );
    }
    return <ErrorState message={me.error.message} onRetry={me.reload} />;
  }

  const role = me.data?.role;
  return (
    <Suspense fallback={<Spinner />}>
      {role === "store_owner" && <StoreApp />}
      {role === "sales_agent" && <AgentApp />}
      {role === "wholesaler_admin" && <AdminApp />}
    </Suspense>
  );
}
