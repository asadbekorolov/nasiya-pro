# Nasiya Pro

Telegram-native B2B platforma: ulgurji sotuvchi, do'konlar va savdo agentlari o'rtasidagi
buyurtma, ombor zaxirasi va qarz (nasiya) hisobi — hammasi Telegram Mini App ichida.

| Qism | Texnologiya | Hujjat |
|---|---|---|
| `backend/` | FastAPI · SQLAlchemy 2 · PostgreSQL · aiogram 3 | [backend/README.md](backend/README.md) |
| `frontend/` | React 19 · TypeScript · Vite · Tailwind v4 | [frontend/README.md](frontend/README.md) |
| `deploy/` | Docker Compose · Caddy (HTTPS) · nginx · PostgreSQL 16 · Redis | [deploy/README.md](deploy/README.md) |

**Production:** haqiqiy domen va avtomatik SSL bilan — [deploy/README.md](deploy/README.md)
(`docker compose --env-file deploy/.env.prod -f docker-compose.prod.yml up -d --build`).
Quyidagi `run_live.py` va vaqtinchalik tunnel faqat ishlab chiqish/sinov uchun.

## Bitta buyruq bilan jonli ishga tushirish

Kerak: [uv](https://docs.astral.sh/uv/) va [Node.js 20+](https://nodejs.org/). Docker shart emas.

```bash
python run_live.py --phone "+998 90 123 45 67"
```

`python` buyrug'i yo'q bo'lsa: Windows'da `run_live.cmd`, Linux/macOS'da `./run_live.sh`
(ikkalasi ham uv orqali Python topadi). Argumentlar bir xil.

Skript:
1. bog'liqliklarni o'rnatadi, `backend/.env` ni tayyorlaydi (token so'raladi, faqat `.env`ga yoziladi);
2. PostgreSQL'ni topadi yoki lokal embedded serverni ishga tushiradi, migratsiya va demo ma'lumot;
3. `--phone` raqamini demo do'kon "Baraka market"ga yozadi;
4. Cloudflare HTTPS tunnel ochadi, Mini App, API va botni ishga tushiradi;
5. bot menyusiga "📱 Nasiya Pro" tugmasini qo'yadi va Telegramda nima qilishni ko'rsatadi.

Telegramda: botga `/start` → "📞 Telefon raqamni yuborish" → "📱 Nasiya Pro".
`Ctrl+C` hammasini to'xtatadi, menyu tugmasini qaytaradi va `.env`ni tiklaydi.
Loglar: `.live/logs/`.

> Tunnel manzili har ishga tushirishda o'zgaradi va faqat skript ishlab turganda ochiq.
> Doimiy foydalanish uchun frontend va backendni HTTPS domenga deploy qilib,
> `BOT_MODE=webhook` ishlating.
