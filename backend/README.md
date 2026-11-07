# Nasiya Pro — Backend (1-bosqich)

Telegram-native B2B ulgurji buyurtma va qarz (nasiya) nazorati platformasining
yadrosi: ma'lumotlar bazasi, ledger va to'lovni OTP orqali tasdiqlash.

## Stek

Python 3.11+ · FastAPI · SQLAlchemy 2.0 (asyncio) + asyncpg · Alembic · Aiogram 3 ·
PostgreSQL 15+ (UUIDv7 PK, `NUMERIC(15,2)` pul, `TIMESTAMPTZ` vaqt).

## Tuzilma

```
backend/
├── alembic/                    # migratsiyalar (0001 — boshlang'ich sxema + triggerlar)
├── app/
│   ├── main.py                 # FastAPI ilovasi, xatolarni JSON'ga o'girish
│   ├── core/                   # config, uuid7, money, security (OTP, initData), exceptions
│   ├── db/                     # Base + naming convention, engine/session, atomic(), raw DDL
│   ├── models/                 # SQLAlchemy modellari
│   ├── schemas/                # Pydantic v2 sxemalari (pul JSON'da string)
│   ├── services/
│   │   ├── ledger_service.py   # yetkazish, to'lov (FIFO), tuzatish, akt-sverka, aging
│   │   ├── otp_service.py      # 4 xonali kod: issue / verify (3 urinish, 3 daqiqa)
│   │   ├── ledger_hash.py      # tranzaksiyalar hash-zanjiri
│   │   └── notifications.py    # kodni do'konchiga Telegram orqali yuborish
│   └── api/                    # deps (Telegram initData auth) va v1 endpointlar
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

Testlar (alohida baza kerak):

```bash
TEST_DATABASE_URL=postgresql+asyncpg://nasiya:nasiya@localhost:5432/nasiya_test uv run pytest
```

## Ledger kafolatlari

| Kafolat | Qayerda ta'minlanadi |
|---|---|
| Bir do'kon balansi bir vaqtda faqat bitta operatsiya bilan o'zgaradi | `SELECT ... FOR UPDATE` (stores), qulflash tartibi: store → order/OTP → debts |
| Hammasi yoki hech narsa | har bir yozuv metodi bitta DB tranzaksiyasi (`db.uow.atomic`) |
| `balance_after = balance_before + amount` | DB `CHECK` |
| Ledger o'zgartirilmaydi/o'chirilmaydi | `UPDATE/DELETE/TRUNCATE` triggerlari (`transactions`, `debt_allocations`) |
| Yashirin tahrir aniqlanadi | har do'kon uchun SHA-256 hash-zanjir, `GET /stores/{id}/ledger/verify` |
| Bitta buyurtma ikki marta hisoblanmaydi | `deliver_order` idempotent + partial unique index |
| To'lov ikki marta yozilmaydi | `idempotency_key` / OTP bo'yicha qayta javob |
| `SUM(ochiq qarzlar) == max(current_debt, 0)` | har yozuvdan keyin tekshiriladi, buzilsa rollback |

Belgilar: `amount` — do'kon qarzining o'zgarishi (`charge` > 0, `payment` < 0,
`adjustment` ≠ 0). Manfiy `current_debt` — avans; keyingi yetkazishdan avtomatik
ushlanadi.

## API (v1)

Auth: `Authorization: tma <Telegram.WebApp.initData>`; agent bir nechta ulgurjichida
ishlasa — `X-Wholesaler-Id`.

| Metod | Yo'l | Kim |
|---|---|---|
| POST | `/orders/{id}/deliver` | ega, agent |
| POST | `/stores/{id}/payments` | ega |
| POST | `/payments/otp` | agent — kod do'konchining Telegramiga boradi |
| POST | `/payments/otp/{id}/confirm` | agent — kod to'g'ri bo'lsa to'lov yoziladi |
| POST | `/stores/{id}/adjustments` | ega (boshlang'ich qoldiq, qaytarilgan tovar) |
| GET | `/stores/{id}/balance` | ega, agent |
| GET | `/stores/{id}/statement?date_from&date_to` | ega (akt-sverka uchun ma'lumot) |
| GET | `/reports/aging` | ega |
| GET | `/stores/{id}/ledger/verify` | ega |
