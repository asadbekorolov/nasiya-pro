/** Wire types mirroring the FastAPI schemas. Money and quantities are decimal strings. */

export type Money = string;
export type Quantity = string;
export type Uuid = string;
export type IsoDateTime = string;

export type Role = "wholesaler_admin" | "sales_agent" | "store_owner";

export interface Me {
  role: Role;
  wholesaler_id: Uuid;
  agent_id: Uuid | null;
  store_id: Uuid | null;
}

export type PaymentMethod = "cash" | "card" | "bank_transfer" | "other";
export type TransactionType = "charge" | "payment" | "adjustment";
export type OrderStatus = "draft" | "pending" | "approved" | "shipped" | "delivered" | "cancelled";

export interface LastPayment {
  amount: Money;
  method: PaymentMethod;
  paid_at: IsoDateTime;
}

export interface StoreMe {
  store_id: Uuid;
  store_name: string;
  contact_name: string | null;
  wholesaler_id: Uuid;
  wholesaler_name: string;
  currency: string;
  current_debt: Money;
  credit_limit: Money;
  available_credit: Money;
  overdue_amount: Money;
  last_payment: LastPayment | null;
}

export interface Product {
  id: Uuid;
  sku: string | null;
  name: string;
  description: string | null;
  unit: string;
  price: Money;
  available_quantity: Quantity;
  image_url: string | null;
}

export interface OrderItem {
  id: Uuid;
  product_id: Uuid;
  product_name: string;
  unit: string;
  unit_price: Money;
  quantity: Quantity;
  line_total: Money;
}

export interface Order {
  id: Uuid;
  order_number: number;
  store_id: Uuid;
  agent_id: Uuid | null;
  status: OrderStatus;
  total_amount: Money;
  note: string | null;
  items: OrderItem[];
  created_at: IsoDateTime;
}

export interface OrderLineIn {
  product_id: Uuid;
  quantity: Quantity;
}

export interface Transaction {
  id: Uuid;
  store_id: Uuid;
  sequence_no: number;
  type: TransactionType;
  amount: Money;
  balance_before: Money;
  balance_after: Money;
  payment_method: PaymentMethod | null;
  order_id: Uuid | null;
  agent_id: Uuid | null;
  description: string | null;
  entry_hash: string;
  created_at: IsoDateTime;
}

export interface TransactionPage {
  items: Transaction[];
  next_before_sequence: number | null;
}

export interface StatementLink {
  url: string;
  filename: string;
  expires_at: IsoDateTime;
}

export interface AgentStore {
  id: Uuid;
  name: string;
  address: string | null;
  phone: string | null;
  current_debt: Money;
  credit_limit: Money;
  overdue_amount: Money;
}

export interface OtpIssue {
  id: Uuid;
  store_id: Uuid;
  amount: Money;
  payment_method: PaymentMethod;
  status: string;
  max_attempts: number;
  expires_at: IsoDateTime;
}

export interface Allocation {
  debt_id: Uuid;
  order_id: Uuid | null;
  amount: Money;
  remaining_after: Money;
  closed: boolean;
}

export interface PaymentResult {
  transaction: Transaction;
  allocations: Allocation[];
  store_balance: Money;
  advance_amount: Money;
  created: boolean;
}

export interface AgingRow {
  store_id: Uuid;
  store_name: string;
  buckets: Record<"current" | "1_30" | "31_60" | "61_90" | "90_plus", Money>;
  total: Money;
}

export interface AgingReport {
  as_of: string;
  rows: AgingRow[];
  grand_total: Money;
}
