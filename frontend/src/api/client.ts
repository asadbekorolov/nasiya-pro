/**
 * HTTP client. Every request carries the Telegram identity:
 *
 *   Authorization: tma <Telegram.WebApp.initData>
 *
 * (`tma` is the scheme the backend's `core/telegram_auth.py` expects.) Errors are
 * normalised into `ApiError` with the backend's stable error `code`.
 */
import ky, { HTTPError, TimeoutError } from "ky";

import { getInitData } from "@/telegram/twa";

const API_ORIGIN = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/+$/, "");
export const API_PREFIX = `${API_ORIGIN}/api/v1`;

let wholesalerId: string | null =
  (import.meta.env.DEV ? import.meta.env.VITE_DEV_WHOLESALER_ID : undefined) || null;

/** Tenant selector for people registered at several wholesalers. */
export function setWholesalerId(id: string | null): void {
  wholesalerId = id;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown>;

  constructor(status: number, code: string, message: string, details: Record<string, unknown> = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

interface BackendErrorBody {
  error?: { code?: string; message?: string; details?: Record<string, unknown> };
  detail?: unknown; // FastAPI request-validation errors
}

function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error;
  if (error instanceof HTTPError) {
    const body = (typeof error.data === "object" ? error.data : undefined) as
      | BackendErrorBody
      | undefined;
    if (body?.error) {
      return new ApiError(
        error.response.status,
        body.error.code ?? "error",
        body.error.message ?? "Xatolik",
        body.error.details ?? {},
      );
    }
    if (error.response.status === 422) {
      return new ApiError(422, "validation_error", "Ma'lumotlar noto'g'ri kiritilgan", {
        detail: body?.detail,
      });
    }
    return new ApiError(error.response.status, "http_error", `Server xatosi (${error.response.status})`);
  }
  if (error instanceof TimeoutError) {
    return new ApiError(0, "timeout", "Server javob bermadi. Internetni tekshiring.");
  }
  return new ApiError(0, "network", "Tarmoq xatosi. Internetni tekshiring.");
}

const http = ky.create({
  prefix: API_PREFIX,
  timeout: 20_000,
  retry: { limit: 2, methods: ["get"] },
  hooks: {
    beforeRequest: [
      ({ request }) => {
        const initData = getInitData();
        if (initData) request.headers.set("Authorization", `tma ${initData}`);
        if (wholesalerId) request.headers.set("X-Wholesaler-Id", wholesalerId);
      },
    ],
  },
});

type Query = Record<string, string | number | boolean | undefined | null>;

function searchParams(query?: Query): Record<string, string> | undefined {
  if (!query) return undefined;
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== "") out[key] = String(value);
  }
  return out;
}

async function run<T>(request: () => Promise<T>): Promise<T> {
  try {
    return await request();
  } catch (error) {
    throw toApiError(error);
  }
}

export const apiClient = {
  get: <T>(path: string, query?: Query) =>
    run(() => http.get(path, { searchParams: searchParams(query) }).json<T>()),
  post: <T>(path: string, body?: unknown, query?: Query) =>
    run(() =>
      http
        .post(path, { json: body ?? {}, searchParams: searchParams(query), retry: 0 })
        .json<T>(),
    ),
  blob: (path: string, query?: Query) =>
    run(() => http.get(path, { searchParams: searchParams(query), timeout: 60_000 }).blob()),
};

/** Absolute URL for a backend path such as `/api/v1/files/...`. */
export function absoluteApiUrl(path: string): string {
  return new URL(path, API_ORIGIN || window.location.origin).toString();
}
