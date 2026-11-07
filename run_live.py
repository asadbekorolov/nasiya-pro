#!/usr/bin/env python3
"""Nasiya Pro — one-command live runner (Windows / Linux / macOS).

    python run_live.py                      # asks for your phone (optional)
    python run_live.py --phone "+998 90 123 45 67"
    python run_live.py --no-phone

What it does:
  1. checks tools (uv, npm), installs dependencies when missing
  2. backend/.env: creates it if missing (generates OTP_SECRET, asks for the bot token)
  3. PostgreSQL: uses DATABASE_URL if reachable, otherwise starts a local embedded server
  4. runs migrations and seeds the demo tenant; puts YOUR phone on the demo store
  5. opens a Cloudflare quick tunnel (HTTPS) to the Mini App
  6. starts the Mini App (Vite :5173) and the API + Telegram bot (uvicorn :8000)
  7. points the bot's menu button at the tunnel and prints what to do in Telegram
Ctrl+C stops everything, resets the bot menu button and restores backend/.env.

Standard library only, so it runs on any Python >= 3.9.
"""

# This is a developer runner: every subprocess command is fixed by this file and every
# URL is http(s) built here, so the subprocess/urlopen audit rules do not apply.
# ruff: noqa: S603, S607, S310

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from getpass import getpass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
ENV_FILE = BACKEND / ".env"
ENV_EXAMPLE = BACKEND / ".env.example"
LOG_DIR = ROOT / ".live" / "logs"

API_PORT = 8000
WEB_PORT = 5173
MENU_TEXT = "📱 Nasiya Pro"
TUNNEL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
TOKEN_RE = re.compile(r"^\d{5,16}:[A-Za-z0-9_-]{30,}$")
IS_WINDOWS = os.name == "nt"

if hasattr(sys.stdout, "reconfigure"):  # emoji on Windows consoles
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


# --- output ----------------------------------------------------------------------------


class Failure(Exception):
    """A step failed; the message tells the user what to do."""


def step(text: str) -> None:
    print(f"\n▶ {text}", flush=True)


def ok(text: str) -> None:
    print(f"  ✓ {text}", flush=True)


def warn(text: str) -> None:
    print(f"  ! {text}", flush=True)


# --- .env helpers -----------------------------------------------------------------------


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def set_env_values(path: Path, updates: dict[str, str]) -> None:
    """Update or append KEY=value lines, keeping comments and order."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pending = dict(updates)
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key in pending and not line.lstrip().startswith("#"):
            lines[i] = f"{key}={pending.pop(key)}"
    lines.extend(f"{key}={value}" for key, value in pending.items())
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --- process helpers -------------------------------------------------------------------


def which(name: str) -> str | None:
    return shutil.which(name)


def run(cmd: list[str], cwd: Path, *, env: dict[str, str] | None = None, timeout: int = 900) -> str:
    """Run to completion; raise Failure with the tail of the output on error."""
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise Failure(f"`{' '.join(cmd)}` timed out after {timeout}s") from exc
    if proc.returncode != 0:
        tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-15:])
        raise Failure(f"`{' '.join(cmd)}` failed (exit {proc.returncode}):\n{tail}")
    return proc.stdout


class Service:
    """A long-running child process with its output in .live/logs/<name>.log."""

    def __init__(self, name: str, cmd: list[str], cwd: Path, env: dict[str, str]) -> None:
        self.name = name
        self.log_path = LOG_DIR / f"{name}.log"
        self.lines: list[str] = []
        self._log = self.log_path.open("w", encoding="utf-8")
        kwargs: dict[str, object] = {}
        if IS_WINDOWS:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        else:
            kwargs["start_new_session"] = True
        self.proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            **kwargs,  # type: ignore[arg-type]
        )
        self._pump_thread = threading.Thread(target=self._pump, daemon=True)
        self._pump_thread.start()

    def _pump(self) -> None:
        """Owns the log file: writes every line and closes it at end of output."""
        assert self.proc.stdout is not None
        try:
            for line in self.proc.stdout:
                self.lines.append(line.rstrip())
                self._log.write(line)
                self._log.flush()
        finally:
            self._log.close()

    def alive(self) -> bool:
        return self.proc.poll() is None

    def tail(self, n: int = 12) -> str:
        return "\n".join(self.lines[-n:])

    def stop(self, graceful_timeout: float = 12.0) -> None:
        if not self.alive():
            return
        try:
            if IS_WINDOWS:
                self.proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
            else:
                os.killpg(self.proc.pid, signal.SIGINT)
            self.proc.wait(timeout=graceful_timeout)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            pass
        if self.alive():
            if IS_WINDOWS:
                subprocess.run(
                    ["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                    capture_output=True,
                    check=False,
                )
            else:
                with contextlib.suppress(OSError):
                    os.killpg(self.proc.pid, signal.SIGKILL)
        self._pump_thread.join(timeout=5)


def http_get(url: str, timeout: float = 5.0) -> tuple[int, str]:
    request = urllib.request.Request(url, headers={"User-Agent": "nasiya-pro-run-live"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status, resp.read(4096).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except (urllib.error.URLError, OSError, TimeoutError):
        return 0, ""


def wait_for(description: str, check, timeout: float, services: list[Service]) -> None:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        for svc in services:
            if not svc.alive():
                raise Failure(f"{svc.name} exited while waiting for {description}:\n{svc.tail()}")
        time.sleep(1)
    raise Failure(f"timed out waiting for {description}")


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


# --- Telegram Bot API (stdlib) ----------------------------------------------------------


def telegram(token: str, method: str, payload: dict[str, object] | None = None) -> object:
    data = json.dumps(payload or {}).encode()
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = json.loads(exc.read() or b"{}")
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise Failure(f"cannot reach api.telegram.org: {exc}") from exc
    if not body.get("ok"):
        raise Failure(f"Telegram {method} failed: {body.get('description', body)}")
    return body["result"]


# --- steps -----------------------------------------------------------------------------


def check_tools() -> tuple[str, str]:
    step("Vositalarni tekshirish")
    uv, npm = which("uv"), which("npm")
    if not uv:
        raise Failure(
            "`uv` topilmadi. O'rnating: https://docs.astral.sh/uv/getting-started/installation/"
        )
    if not npm:
        raise Failure("`npm` (Node.js 20+) topilmadi. O'rnating: https://nodejs.org/")
    ok(f"uv: {uv}")
    ok(f"npm: {npm}")
    return uv, npm


def install_dependencies(uv: str, npm: str) -> None:
    step("Bog'liqliklarni o'rnatish")
    run([uv, "sync"], BACKEND)
    ok("backend (uv sync)")
    if not (FRONTEND / "node_modules").exists():
        run([npm, "install"], FRONTEND)
    ok("frontend (npm)")


def ensure_env_file(token_arg: str | None) -> dict[str, str]:
    step("backend/.env")
    if not ENV_FILE.exists():
        shutil.copyfile(ENV_EXAMPLE, ENV_FILE)
        ok(".env.example dan nusxa olindi")
    env = read_env(ENV_FILE)
    updates: dict[str, str] = {}

    if len(env.get("OTP_SECRET", "")) < 32 or env.get("OTP_SECRET", "").startswith("replace"):
        updates["OTP_SECRET"] = secrets.token_urlsafe(48)
        ok("OTP_SECRET yaratildi")

    token = token_arg or env.get("TELEGRAM_BOT_TOKEN", "")
    if not TOKEN_RE.match(token):
        if not sys.stdin.isatty():
            raise Failure(
                "TELEGRAM_BOT_TOKEN yo'q: --token bilan bering yoki backend/.env ga yozing"
            )
        token = getpass("  BotFather bergan bot tokenini kiriting (ko'rinmaydi): ").strip()
        if not TOKEN_RE.match(token):
            raise Failure("token formati noto'g'ri")
    if token != env.get("TELEGRAM_BOT_TOKEN"):
        updates["TELEGRAM_BOT_TOKEN"] = token

    for key, value in {"ENVIRONMENT": "local", "OTP_DELIVERY": "telegram"}.items():
        if env.get(key) != value:
            updates[key] = value
    if updates:
        set_env_values(ENV_FILE, updates)
    ok("sozlamalar tayyor (token faqat .env da, git'ga tushmaydi)")
    return read_env(ENV_FILE)


def ensure_database(uv: str, env: dict[str, str]) -> None:
    step("PostgreSQL")
    url = env.get("DATABASE_URL", "")
    parsed = urllib.parse.urlparse(url.replace("+asyncpg", ""))
    host, port = parsed.hostname or "", parsed.port or 5432
    if url and host and port_open(port, host):
        ok(f"mavjud baza ishlayapti ({host}:{port})")
    else:
        if host not in ("", "127.0.0.1", "localhost"):
            raise Failure(f"DATABASE_URL dagi server ({host}:{port}) javob bermayapti")
        out = run([uv, "run", "python", "scripts/local_db.py"], BACKEND, timeout=300)
        match = re.search(r"DATABASE_URL=(\S+)", out)
        if not match:
            raise Failure(f"local_db.py natijasi tushunarsiz:\n{out}")
        # Embedded Windows builds ship without tz data: keep the server default zone.
        set_env_values(ENV_FILE, {"DATABASE_URL": match.group(1), "DB_SESSION_TIMEZONE": ""})
        ok(f"lokal baza ishga tushdi ({match.group(1).rsplit('@', 1)[-1]})")
    run([uv, "run", "alembic", "upgrade", "head"], BACKEND)
    ok("migratsiyalar qo'llandi")


def seed(uv: str, phone: str | None) -> None:
    step("Demo ma'lumotlar")
    cmd = [uv, "run", "python", "scripts/seed_demo.py"]
    if phone:
        cmd += ["--store-phone", phone]
    for line in run(cmd, BACKEND).strip().splitlines():
        ok(line)


def ask_phone(args: argparse.Namespace) -> str | None:
    if args.no_phone:
        return None
    if args.phone:
        return args.phone
    if not sys.stdin.isatty():
        return None
    print(
        "\n  Telegram'da do'kon egasi sifatida kirish uchun telefon raqamingizni kiriting.\n"
        "  (Bu raqam demo do'kon \"Baraka market\"ga yoziladi; Enter — o'tkazib yuborish)"
    )
    value = input("  Telefon (+998 ...): ").strip()
    return value or None


def find_cloudflared(npm: str) -> str:
    found = which("cloudflared")
    if found:
        return found
    exe = "cloudflared.exe" if IS_WINDOWS else "cloudflared"
    local = FRONTEND / "node_modules" / "cloudflared" / "bin" / exe
    if not local.exists():
        node = which("node") or "node"
        # The npm package downloads the official binary from Cloudflare's GitHub releases.
        run(
            [
                node,
                "-e",
                "const c=require('cloudflared');c.install(c.bin).then(()=>console.log(c.bin))",
            ],
            FRONTEND,
            timeout=300,
        )
    if not local.exists():
        raise Failure("cloudflared o'rnatilmadi (frontend/node_modules/cloudflared)")
    return str(local)


def start_tunnel(npm: str) -> tuple[Service, str]:
    step("HTTPS tunnel (Cloudflare)")
    binary = find_cloudflared(npm)
    tunnel = Service(
        "tunnel",
        [binary, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{WEB_PORT}"],
        ROOT,
        dict(os.environ),
    )
    url: list[str] = []

    def has_url() -> bool:
        for line in tunnel.lines:
            match = TUNNEL_RE.search(line)
            if match:
                url.append(match.group(0))
                return True
        return False

    wait_for("tunnel URL", has_url, 60, [tunnel])
    ok(url[0])
    return tunnel, url[0]


def start_frontend(npm: str) -> Service:
    env = dict(os.environ)
    # The Mini App is now public: never ship the dev identity from .env.local through
    # the tunnel (anyone opening the URL in a browser would be logged in as that user).
    env["VITE_DEV_INIT_DATA"] = ""
    env["VITE_DEV_WHOLESALER_ID"] = ""
    env["VITE_API_PROXY_TARGET"] = f"http://127.0.0.1:{API_PORT}"
    return Service(
        "web",
        [npm, "run", "dev", "--", "--host", "127.0.0.1", "--port", str(WEB_PORT)],
        FRONTEND,
        env,
    )


def start_backend(uv: str, webapp_url: str) -> Service:
    env = dict(os.environ)
    env.update({"BOT_MODE": "polling", "WEBAPP_URL": webapp_url, "OTP_DELIVERY": "telegram"})
    return Service(
        "api",
        [uv, "run", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(API_PORT)],
        BACKEND,
        env,
    )


def configure_menu_button(token: str, webapp_url: str) -> str:
    step("Bot menyu tugmasi")
    me = telegram(token, "getMe")
    username = str(me["username"])  # type: ignore[index]
    telegram(
        token,
        "setChatMenuButton",
        {"menu_button": {"type": "web_app", "text": MENU_TEXT, "web_app": {"url": webapp_url}}},
    )
    ok(f'@{username}: "{MENU_TEXT}" -> {webapp_url}')
    current = telegram(token, "getChatMenuButton")
    if current.get("type") != "web_app":  # type: ignore[union-attr]
        # Observed with the live Bot API: the *default* button reads back as "commands"
        # right after a successful set. The bot also sets the button per chat on /start,
        # so every user who presses /start gets it regardless.
        warn(
            "Telegram standart tugmani 'commands' deb qaytardi; /start uni har bir chatga o'rnatadi"
        )
    return username


def reset_menu_button(token: str) -> None:
    try:
        telegram(token, "setChatMenuButton", {"menu_button": {"type": "default"}})
        ok("bot menyu tugmasi standart holatga qaytarildi")
    except Failure as exc:
        warn(str(exc))


def print_instructions(username: str, webapp_url: str, phone: str | None) -> None:
    line = "═" * 66
    claim = (
        '  3. "📞 Telefon raqamni yuborish" tugmasini bosing —\n'
        "     raqamingiz \"Baraka market\" do'koniga bog'lanadi.\n"
        '  4. Pastdagi "📱 Nasiya Pro" tugmasini bosing: qarz, katalog, Akt-sverka.\n'
        if phone
        else '  3. Pastdagi "📱 Nasiya Pro" tugmasini bosing.\n'
        "     (Do'kon egasi bo'lish uchun keyingi safar --phone bilan ishga tushiring.)\n"
    )
    print(
        f"\n{line}\n"
        "  ✅ NASIYA PRO JONLI ISHLAYAPTI\n"
        f"{line}\n"
        f"  Telegram:  https://t.me/{username}\n"
        f"  Mini App:  {webapp_url}\n"
        f"  API:       http://127.0.0.1:{API_PORT}/docs\n"
        f"  Loglar:    {LOG_DIR}\n\n"
        "  Telegramda:\n"
        f"  1. @{username} botini oching.\n"
        "  2. /start ni bosing.\n"
        f"{claim}"
        "\n  To'xtatish: Ctrl+C\n"
        f"{line}",
        flush=True,
    )


# --- main ------------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Nasiya Pro live runner")
    parser.add_argument("--phone", help="your phone; claims the demo store via the bot")
    parser.add_argument("--no-phone", action="store_true", help="do not ask for a phone")
    parser.add_argument("--token", help="bot token (saved to backend/.env only)")
    parser.add_argument("--skip-install", action="store_true", help="skip uv sync / npm install")
    parser.add_argument(
        "--exit-after",
        type=float,
        metavar="SECONDS",
        help="smoke test: shut down cleanly this many seconds after everything is up",
    )
    args = parser.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    services: list[Service] = []
    token = ""
    previous_webapp_url: str | None = None
    menu_set = False

    try:
        uv, npm = check_tools()
        if not args.skip_install:
            install_dependencies(uv, npm)
        env = ensure_env_file(args.token)
        token = env["TELEGRAM_BOT_TOKEN"]
        previous_webapp_url = env.get("WEBAPP_URL", "")

        for port in (API_PORT, WEB_PORT):
            if port_open(port):
                raise Failure(f"port {port} band — boshqa nusxa ishlayaptimi? Uni to'xtating.")

        ensure_database(uv, read_env(ENV_FILE))
        phone = ask_phone(args)
        seed(uv, phone)

        tunnel, webapp_url = start_tunnel(npm)
        services.append(tunnel)
        set_env_values(ENV_FILE, {"WEBAPP_URL": webapp_url})
        ok("backend/.env: WEBAPP_URL yangilandi")

        step("Mini App (Vite) va API + bot (uvicorn)")
        web = start_frontend(npm)
        services.append(web)
        api = start_backend(uv, webapp_url)
        services.append(api)
        wait_for(
            "API",
            lambda: http_get(f"http://127.0.0.1:{API_PORT}/api/v1/health/ready")[0] == 200,
            90,
            services,
        )
        ok(f"API http://127.0.0.1:{API_PORT}")
        wait_for(
            "Mini App", lambda: http_get(f"http://127.0.0.1:{WEB_PORT}/")[0] == 200, 90, services
        )
        ok(f"Mini App http://127.0.0.1:{WEB_PORT}")
        wait_for(
            "bot polling",
            lambda: any("Run polling for bot" in line for line in api.lines),
            60,
            services,
        )
        ok("Telegram bot polling ishlayapti")
        wait_for(
            "tunnel -> Mini App -> API",
            lambda: http_get(f"{webapp_url}/api/v1/health/live", timeout=10)[0] == 200,
            120,
            services,
        )
        ok("tunnel orqali butun zanjir javob berdi")

        username = configure_menu_button(token, webapp_url)
        menu_set = True
        print_instructions(username, webapp_url, phone)

        started = time.monotonic()
        while args.exit_after is None or time.monotonic() - started < args.exit_after:
            for svc in services:
                if not svc.alive():
                    raise Failure(f"{svc.name} to'xtab qoldi:\n{svc.tail()}")
            time.sleep(2)
        raise KeyboardInterrupt
    except KeyboardInterrupt:
        print("\nTo'xtatilmoqda…", flush=True)
        code = 0
    except Failure as exc:
        print(f"\n✗ {exc}", file=sys.stderr, flush=True)
        code = 1
    finally:
        if menu_set and token:
            reset_menu_button(token)
        for svc in reversed(services):
            svc.stop()
        if previous_webapp_url is not None and ENV_FILE.exists():
            set_env_values(ENV_FILE, {"WEBAPP_URL": previous_webapp_url})
    return code


if __name__ == "__main__":
    sys.exit(main())
