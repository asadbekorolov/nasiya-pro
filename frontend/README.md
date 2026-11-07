# Nasiya Pro — Telegram Mini App

React 19 · TypeScript · Vite · Tailwind CSS v4 · `@twa-dev/sdk` · ky · zustand · lucide-react.

Bitta ilova, uchta kabinet. Rolni frontend o'zi taxmin qilmaydi — `GET /api/v1/auth/me`
javobiga qarab `RoleGuard` tegishli ekranni ochadi:

| Rol | Ekranlar |
|---|---|
| Do'konchi | Asosiy (joriy qarz, oxirgi to'lov, limit, Akt-sverka PDF) · Katalog + savat · Qarz va to'lovlar tarixi |
| Savdo agenti | Marshrut (biriktirilgan do'konlar) → do'kon → naqd pul (OTP) yoki joyida buyurtma |
| Ulgurjichi | Debitorlik (aging) — to'liq admin panel keyingi bosqichda |

## Ishga tushirish

```bash
npm install
npm run dev        # http://localhost:5173, /api -> http://127.0.0.1:8000
npm run build      # tsc + vite build -> dist/
npm test           # vitest (pul hisob-kitobi)
```

### Telegram'dan tashqarida ishlab chiqish

Backend har bir `initData` imzosini bot tokeni bilan tekshiradi, shuning uchun soxta
qiymat ishlamaydi. Lokal bot token bilan imzolangan qiymatni yarating:

```bash
cd ../backend
uv run python scripts/seed_demo.py                       # demo ulgurjichi/do'kon/agent
uv run python scripts/dev_init_data.py --tg-id 2000001   # 2000001 do'konchi, 3000001 agent, 1000001 admin
```

Natijani `frontend/.env.local` ga `VITE_DEV_INIT_DATA=...` qilib yozing va `npm run dev` ni
qayta ishga tushiring. Bu qiymat faqat dev build'da o'qiladi. Agent kodi Telegram'siz
sinalishi uchun backend `.env` da `OTP_DELIVERY=log` qo'ying — kod server logiga chiqadi.

## Muhim qarorlar

- **Auth sarlavhasi:** `Authorization: tma <initData>` (backend kontrakti).
- **Pul:** API'dan decimal string keladi; hisob-kitob BigInt'da (tiyin), float yo'q.
  Qator summasi backend bilan bir xil yaxlitlanadi (ROUND_HALF_UP).
- **Takroriy buyurtma yo'q:** har bir savatning idempotency kaliti bor; savat o'zgarsa
  kalit yangilanadi, qayta urinishda esa o'sha kalit ishlatiladi.
- **Akt-sverka PDF:** Telegram 8.0+ da `WebApp.downloadFile` 5 daqiqalik imzolangan
  havolani yuklaydi (Telegram bizning auth sarlavhamizni yubora olmaydi); eski
  klientlarda havola tashqi brauzerda ochiladi; oddiy brauzerda blob orqali saqlanadi.
- **Mavzu:** Telegram `themeParams` CSS o'zgaruvchilariga bog'langan, `themeChanged`
  hodisasida yangilanadi; yorug'/qorong'i rejim ikkalasi ham ishlaydi.
- **SDK:** `@twa-dev/sdk` faqat side-effect sifatida import qilinadi va
  `window.Telegram.WebApp` ishlatiladi — paketning default eksporti Vite 8'da
  `default.default` bo'lib qolib, `WebApp.ready()` ishlamay qolardi.

## Deploy

`dist/` ni API bilan bir domenda (reverse proxy orqali `/api` → FastAPI) joylashtiring
va BotFather'da Mini App URL sifatida HTTPS manzilni bering. Boshqa domenda bo'lsa,
`VITE_API_BASE_URL` ni o'rnating va backend `CORS_ORIGINS` ga frontend domenini qo'shing.
