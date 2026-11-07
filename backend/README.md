# Nasiya Pro — Backend

Telegram-native B2B ulgurji buyurtma va qarz (nasiya) nazorati platformasi.
Do'konchilar va agentlar faqat Telegram Mini App orqali ishlaydi; ulgurjichi —
Mini App yoki brauzer orqali.

## Stek

Python 3.11+ · FastAPI · SQLAlchemy 2.0 (asyncio) + asyncpg · Alembic · Aiogram 3 ·
ReportLab · PostgreSQL 15+ (UUIDv7 PK, `NUMERIC(15,2)` pul, `TIMESTAMPTZ` vaqt).

## Tuzilma

```
backend/
├── alembic/versions/           # 0001 sxema + triggerlar, 0002 buyurtma/ombor/rekvizitlar
├── app/
│   ├── core/
│   │   ├── telegram_auth.py    # initData HMAC-SHA256 tekshiruvi + FastAPI dependency
│   │   ├── security.py         # OTP kodlari (HMAC)
│   │   └── config, money, uuid7, exceptions, clock, logging
│   ├── db/                     # Base, engine/session, atomic(), append-only DDL
│   ├── models/                 # SQLAlchemy modellari
│   ├── schemas/                # Pydantic v2 (pul JSON'da string)
│   ├── services/
│   │   ├── ledger_service.py   # pul: charge, FIFO to'lov, tuzatish, statement, aging
│   │   ├── order_service.py    # buyurtma holatlari + ombor zaxirasi
│   │   ├── pdf_service.py      # Akt-sverka PDF (xotirada, BytesIO)
│   │   ├── otp_service.py      # naqd pulni tasdiqlash kodi
│   │   └── locks.py            # umumiy qulflash tartibi
│   └── api/
│       ├── deps.py             # rollar bo'yicha xavfsizlik zanjiri
│       └── v1/endpoints/       # auth, store, agent, admin, health
└── tests/                      # haqiqiy PostgreSQL'da integratsion testlar
```

## Ishga tushirish

```bash
cp .env.example .env            # OTP_SECRET va TELEGRAM_BOT_TOKEN ni to'ldiring
docker compose up -d postgres
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

Testlar (alohida baza):

```bash
TEST_DATABASE_URL=postgresql+asyncpg://nasiya:nasiya@localhost:5432/nasiya_test uv run pytest
```

**PDF shrifti.** Akt-sverkada kirill va o'zbek lotin harflari chiqishi uchun Unicode TTF
kerak. Linux/Docker'da `apt-get install fonts-dejavu-core` yetarli (avtomatik topiladi),
yoki `PDF_FONT_REGULAR_PATH` / `PDF_FONT_BOLD_PATH` ni bering. Shrift topilmasa
Helvetica ishlatiladi va logda ogohlantirish chiqadi.

Lokal ishlab chiqish: `scripts/seed_demo.py` demo ma'lumot yaratadi, `scripts/dev_init_data.py`
Telegram'siz sinash uchun imzolangan `initData` beradi (frontend README'ga qarang).

## Autentifikatsiya va rollar

Har bir so'rov: `Authorization: tma <Telegram.WebApp.initData>`.

1. `core/telegram_auth.py` imzoni bot tokeni bilan tekshiradi (HMAC-SHA256, `auth_date`
   muddati, takroriy maydonlar, bot akkauntlar rad etiladi).
2. `api/deps.py` `telegram_user_id` bo'yicha rolni aniqlaydi:

| Rol | Kim | Qanday topiladi |
|---|---|---|
| `wholesaler_admin` | ulgurjichi egasi | `wholesalers.owner_telegram_user_id` |
| `sales_agent` | savdo agenti | `agents.telegram_user_id` (faol) |
| `store_owner` | do'konchi | `stores.telegram_user_id` (faol) |

Har bir rolning o'z URL prefiksi bor, shuning uchun bir kishi ham agent, ham do'konchi
bo'lsa ham chalkashlik yo'q. Bir nechta ulgurjichida ro'yxatdan o'tgan foydalanuvchi
`X-Wholesaler-Id` sarlavhasini yuboradi. Agent faqat o'ziga biriktirilgan do'konlar
bilan ishlaydi (`agent_store_assignments`); boshqalari 404 qaytaradi.

## Telegram bot (`app/bot/`)

aiogram 3, FastAPI `lifespan` ichida ishga tushadi va to'xtaganda polling hamda HTTP sessiya
toza yopiladi.

| Fayl | Vazifasi |
|---|---|
| `dispatcher.py` | Bot/Dispatcher yaratish, `BotRuntime`: polling, webhook, to'xtatish |
| `routers/start.py` | `/start` (rol bo'yicha salomlashuv + Mini App tugmasi), telefon orqali ro'yxatdan o'tish |
| `keyboards.py` | Mini App (`web_app`) va kontakt tugmalari |
| `messages.py` | Barcha matnlar (HTML, foydalanuvchi ma'lumotlari ekranlangan) |
| `notifications.py` | `BotNotifier`: OTP kodi va push xabarlar |

**Rejimlar (`BOT_MODE`):** `off` (testlar, qo'shimcha API workerlar — xabar *yuborish* baribir
ishlaydi), `polling` (development, faqat **bitta** jarayonda), `webhook` (production:
`BOT_WEBHOOK_BASE_URL` + `BOT_WEBHOOK_SECRET`, `POST /api/v1/telegram/webhook`).

**`WEBAPP_URL`** HTTPS bo'lishi shart — Telegram `http://` Mini App tugmalarini rad etadi.
Lokal sinash uchun tunnel (`cloudflared tunnel --url http://localhost:5173`) yoki deploy
qilingan frontend manzilini bering. URL bo'lmasa bot ishlaydi, faqat tugmalarsiz.

**Ro'yxatdan o'tish:** foydalanuvchi o'z kontaktini ulashadi; faqat `contact.user_id ==
from_user.id` bo'lsa (Telegram tasdiqlagan o'z raqami) qabul qilinadi. Raqam do'kon
kartasidagi raqamga mos kelsa va do'kon hali hech kimga ulanmagan bo'lsa, `telegram_user_id`
bog'lanadi. Boshqa akkauntga ulangan do'kon hech qachon qayta bog'lanmaydi.

**Xabarnomalar:**

| Hodisa | Kimga | Qachon |
|---|---|---|
| Naqd pul OTP | do'konchi | sinxron; yetkazilmasa API 502 va kod bekor qilinadi |
| Yangi buyurtma | ulgurjichi + do'konga biriktirilgan agentlar (buyurtma bergandan tashqari) | tranzaksiya commit bo'lgach, fonda |
| Buyurtma yetkazildi | do'konchi (yangi qarz balansi bilan) | commit'dan keyin, fonda |
| To'lov qabul qilindi | do'konchi (elektron kvitansiya) | commit'dan keyin, fonda |

Push xabarlar xatoga chidamli: bot bloklangan, Telegram ishlamayotgan yoki flood-limit
bo'lsa log yoziladi, biznes operatsiyasi esa hech qachon bekor bo'lmaydi. Idempotent
qayta so'rovlar xabarni ikkinchi marta yubormaydi.

**Token xavfsizligi:** `TELEGRAM_BOT_TOKEN` faqat `.env`da (git'ga tushmaydi). Token
oshkor bo'lsa, BotFather'da `/revoke` qiling va yangisini `.env`ga yozing.

## Buyurtma va ombor

```
draft → pending → approved → (shipped) → delivered
   └──────┴──────────┴───────────┴──→ cancelled
```

| Qadam | Ombor | Pul |
|---|---|---|
| yaratish / submit | qoldiq yetarliligi tekshiriladi (qulfsiz) | — |
| approve | qatorlar qulflanadi, `reserved += qty` (hammasi yoki hech biri); kredit limiti: qarz + yetkazilmagan buyurtmalar + shu buyurtma | — |
| deliver | `stock -= qty`, `reserved -= qty` | `ledger.deliver_order` — **o'sha tranzaksiyada** |
| cancel | zaxira bo'shatiladi | — |

Qulflash tartibi hamma joyda bir xil: store → order → products (id bo'yicha) → OTP → debts,
shuning uchun buyurtma va to'lov operatsiyalari bir-birini deadlock qilmaydi.
Ombor ortiqcha sotilmasligi parallel test bilan tekshirilgan.

## Ledger kafolatlari

| Kafolat | Qayerda ta'minlanadi |
|---|---|
| Bir do'kon balansi bir vaqtda bitta operatsiya bilan o'zgaradi | `SELECT ... FOR UPDATE` |
| Hammasi yoki hech narsa | har bir yozuv metodi bitta DB tranzaksiyasi (`db.uow.atomic`) |
| `balance_after = balance_before + amount` | DB `CHECK` |
| Ledger o'zgartirilmaydi/o'chirilmaydi | UPDATE/DELETE/TRUNCATE triggerlari |
| Yashirin tahrir aniqlanadi | do'kon bo'yicha SHA-256 hash-zanjir |
| Bitta buyurtma ikki marta hisoblanmaydi | idempotent deliver + partial unique index |
| `SUM(ochiq qarzlar) == max(current_debt, 0)` | har yozuvdan keyin tekshiriladi |

## API (`/api/v1`)

| Metod | Yo'l | Rol |
|---|---|---|
| GET | `/auth/me` | har qanday — Mini App qaysi ekranni ochishini biladi |
| GET | `/store/me` | do'konchi — joriy qarz, limit, muddati o'tgan qarz, oxirgi to'lov |
| GET | `/store/catalog?q&in_stock` | do'konchi |
| GET/POST | `/store/orders` | do'konchi |
| GET | `/store/orders/{id}` | do'konchi |
| POST | `/store/orders/{id}/submit`, `/cancel` | do'konchi (tasdiqlangunga qadar) |
| GET | `/store/statement.pdf?date_from&date_to` | do'konchi — Akt-sverka |
| POST | `/store/statement-link` | do'konchi — `WebApp.downloadFile` uchun 5 daqiqalik imzolangan havola |
| GET | `/store/transactions?before_sequence` | do'konchi — qarz/to'lov tarixi |
| GET | `/files/statements/{token}` | ochiq, faqat imzolangan token bilan |
| GET | `/agent/catalog` | agent |
| GET | `/agent/stores` | agent — biriktirilgan do'konlar |
| GET/POST | `/agent/orders` | agent |
| POST | `/agent/orders/{id}/deliver` | agent |
| POST | `/agent/collect-cash/request-otp` | agent — kod do'konchining Telegramiga boradi |
| POST | `/agent/collect-cash/verify-otp` | agent — kod to'g'ri bo'lsa to'lov FIFO yoziladi |
| GET | `/admin/aging-report` | admin |
| GET | `/admin/orders` | admin |
| POST | `/admin/orders/{id}/approve`, `/ship`, `/deliver`, `/cancel` | admin |
| POST | `/admin/stores/{id}/payments`, `/adjustments` | admin |
| GET | `/admin/stores/{id}/balance`, `/statement`, `/statement.pdf`, `/ledger/verify` | admin |
| PUT/DELETE | `/admin/agents/{agent_id}/stores/{store_id}` | admin — agentni biriktirish |
