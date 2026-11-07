/**
 * Thin, version-safe wrapper around the Telegram Mini App SDK.
 *
 * Every optional API is guarded with `isVersionAtLeast`, so the app also runs in an
 * ordinary browser during development (where the SDK reports version 6.0 and no
 * initData).
 */
// Side-effect import: loads Telegram's official telegram-web-app.js, which installs
// `window.Telegram.WebApp`. We read that global instead of the package's default export,
// whose CommonJS/ESM interop differs between bundlers (Vite 8 yields `default.default`).
import "@twa-dev/sdk";
import type { WebApp as TelegramWebApp } from "@twa-dev/types";
import { useEffect, useRef } from "react";

declare global {
  interface Window {
    Telegram: { WebApp: TelegramWebApp };
  }
}

const WebApp: TelegramWebApp = window.Telegram.WebApp;
export const tg = WebApp;

export function isInTelegram(): boolean {
  return Boolean(WebApp.initData);
}

function supports(version: string): boolean {
  try {
    return WebApp.isVersionAtLeast(version);
  } catch {
    return false;
  }
}

/** Raw signed initData for the API. Outside Telegram, dev builds may use a token
 * minted by `backend/scripts/dev_init_data.py`; production builds never do. */
export function getInitData(): string {
  if (WebApp.initData) return WebApp.initData;
  if (import.meta.env.DEV) return import.meta.env.VITE_DEV_INIT_DATA ?? "";
  return "";
}

const THEME_KEYS = [
  "bg_color",
  "secondary_bg_color",
  "section_bg_color",
  "text_color",
  "hint_color",
  "link_color",
  "button_color",
  "button_text_color",
  "accent_text_color",
  "destructive_text_color",
  "section_separator_color",
  "subtitle_text_color",
] as const;

function applyTheme(): void {
  const root = document.documentElement;
  const params = WebApp.themeParams ?? {};
  for (const key of THEME_KEYS) {
    const value = params[key];
    const cssVar = `--tg-theme-${key.replaceAll("_", "-")}`;
    if (value) root.style.setProperty(cssVar, value);
  }
  const scheme = isInTelegram()
    ? WebApp.colorScheme
    : window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  root.dataset.theme = scheme;
  if (isInTelegram() && supports("6.1")) {
    WebApp.setHeaderColor("secondary_bg_color");
    WebApp.setBackgroundColor("secondary_bg_color");
  }
}

export function initTelegram(): void {
  // Each step is isolated: one failing call must not skip theming or ready().
  const steps: Array<[string, () => void]> = [
    ["theme", applyTheme],
    ["themeChanged", () => WebApp.onEvent("themeChanged", applyTheme)],
    ["ready", () => WebApp.ready()],
    ["expand", () => WebApp.expand()],
    ["swipes", () => supports("7.7") && WebApp.disableVerticalSwipes()],
  ];
  for (const [name, step] of steps) {
    try {
      step();
    } catch (error) {
      console.warn(`Telegram WebApp init step "${name}" failed`, error);
    }
  }
}

export const haptic = {
  tap(): void {
    if (supports("6.1")) WebApp.HapticFeedback.selectionChanged();
  },
  success(): void {
    if (supports("6.1")) WebApp.HapticFeedback.notificationOccurred("success");
  },
  error(): void {
    if (supports("6.1")) WebApp.HapticFeedback.notificationOccurred("error");
  },
};

/** Shows Telegram's native back button while `onBack` is set. */
export function useBackButton(onBack: (() => void) | null): void {
  const handler = useRef(onBack);
  handler.current = onBack;
  const enabled = onBack !== null;

  useEffect(() => {
    if (!enabled || !isInTelegram() || !supports("6.1")) return;
    const listener = () => handler.current?.();
    WebApp.BackButton.onClick(listener);
    WebApp.BackButton.show();
    return () => {
      WebApp.BackButton.offClick(listener);
      WebApp.BackButton.hide();
    };
  }, [enabled]);
}

/**
 * Download a file by URL. Inside Telegram 8.0+ the native downloader is used (it
 * fetches the URL itself — hence signed links); older clients open it externally.
 * Returns false when the caller should fall back to an in-page download.
 */
export function nativeDownload(url: string, fileName: string): boolean {
  if (!isInTelegram()) return false;
  if (supports("8.0")) {
    WebApp.downloadFile({ url, file_name: fileName });
    return true;
  }
  WebApp.openLink(url);
  return true;
}

export async function confirm(message: string): Promise<boolean> {
  if (isInTelegram() && supports("6.2")) {
    return new Promise((resolve) => WebApp.showConfirm(message, (ok) => resolve(ok)));
  }
  return window.confirm(message);
}
