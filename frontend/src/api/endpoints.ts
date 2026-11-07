import { apiClient } from "./client";
import type {
  AgentStore,
  AgingReport,
  Me,
  Order,
  OrderLineIn,
  OtpIssue,
  PaymentMethod,
  PaymentResult,
  Product,
  Role,
  StatementLink,
  StoreMe,
  TransactionPage,
} from "./types";

export interface Period {
  date_from: string; // YYYY-MM-DD
  date_to: string;
}

export interface CatalogQuery {
  q?: string;
  in_stock?: boolean;
  limit?: number;
  offset?: number;
}

export const authApi = {
  /** Without `role`: wholesaler_admin > sales_agent > store_owner. */
  me: (role?: Role) => apiClient.get<Me>("auth/me", { role }),
};

export const storeApi = {
  me: () => apiClient.get<StoreMe>("store/me"),
  catalog: (query: CatalogQuery = {}) => apiClient.get<Product[]>("store/catalog", { ...query }),
  createOrder: (lines: OrderLineIn[], idempotencyKey: string, note?: string) =>
    apiClient.post<Order>("store/orders", { lines, note, idempotency_key: idempotencyKey }),
  orders: () => apiClient.get<Order[]>("store/orders"),
  transactions: (beforeSequence?: number | null, limit = 30) =>
    apiClient.get<TransactionPage>("store/transactions", {
      limit,
      before_sequence: beforeSequence ?? undefined,
    }),
  statementLink: (period: Period) =>
    apiClient.post<StatementLink>("store/statement-link", undefined, { ...period }),
  statementPdf: (period: Period) => apiClient.blob("store/statement.pdf", { ...period }),
};

export const agentApi = {
  stores: () => apiClient.get<AgentStore[]>("agent/stores"),
  catalog: (query: CatalogQuery = {}) => apiClient.get<Product[]>("agent/catalog", { ...query }),
  createOrder: (storeId: string, lines: OrderLineIn[], idempotencyKey: string, note?: string) =>
    apiClient.post<Order>("agent/orders", {
      store_id: storeId,
      lines,
      note,
      idempotency_key: idempotencyKey,
    }),
  requestOtp: (storeId: string, amount: string, method: PaymentMethod = "cash") =>
    apiClient.post<OtpIssue>("agent/collect-cash/request-otp", {
      store_id: storeId,
      amount,
      method,
    }),
  verifyOtp: (verificationId: string, code: string) =>
    apiClient.post<PaymentResult>("agent/collect-cash/verify-otp", {
      verification_id: verificationId,
      code,
      idempotency_key: `otp-${verificationId}`,
    }),
};

export const adminApi = {
  aging: () => apiClient.get<AgingReport>("admin/aging-report"),
};
