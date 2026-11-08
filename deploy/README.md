# Nasiya Pro — production deploy (Docker Compose + Caddy)

Bitta server, bitta domen, avtomatik HTTPS (Let's Encrypt). Vaqtinchalik Cloudflare
tunnel (`run_live.py`) faqat ishlab chiqish uchun; productionda shu andoza ishlatiladi.

```
Internet ──443──▶ caddy (TLS, HTTP/3) ──/api/*──▶ backend (FastAPI + Telegram bot) ──▶ postgres, redis
                                      └──/*─────▶ frontend (nginx, Mini App)
```

| Servis     | Image                         | Vazifa                                                   |
|------------|-------------------------------|----------------------------------------------------------|
| `caddy`    | `caddy:2.8-alpine`            | 80/443, sertifikat olish/yangilash, marshrutlash          |
| `frontend` | `Dockerfile.frontend`         | Vite build → unprivileged nginx (SPA, kesh sarlavhalari)  |
| `backend`  | `Dockerfile`                  | Migratsiya + uvicorn; bot webhook rejimida shu jarayonda  |
| `postgres` | `postgres:16-alpine`          | Ma'lumotlar (volume `pgdata`)                             |
| `redis`    | `redis:7.4-alpine`            | Bot holati (aiogram FSM), barcha workerlar uchun umumiy   |

Faqat `caddy` port ochadi. `postgres` va `redis` internetga chiqishi yo'q `internal`
tarmoqda.

## 1. Server tayyorlash

- Ubuntu 22.04/24.04, kamida 2 vCPU / 2 GB RAM.
- Docker Engine + Compose plugin: <https://docs.docker.com/engine/install/>
- Domen DNS: `A` yozuvi (`app.example.uz`) server IP manziliga.
- Firewall: 22, 80, 443/tcp va 443/udp ochiq.

## 2. Sozlash

```bash
git clone https://github.com/asadbekorolov/nasiya-pro.git
cd nasiya-pro
cp deploy/.env.prod.example deploy/.env.prod
nano deploy/.env.prod     # DOMAIN, ACME_EMAIL, POSTGRES_PASSWORD, TELEGRAM_BOT_TOKEN,
                          # BOT_WEBHOOK_SECRET, OTP_SECRET
chmod 600 deploy/.env.prod
```

Maxfiy qiymatlarni generatsiya qilish:

```bash
python3 -c "import secrets; print(secrets.token_hex(24))"        # POSTGRES_PASSWORD
python3 -c "import secrets; print(secrets.token_urlsafe(32))"    # BOT_WEBHOOK_SECRET
python3 -c "import secrets; print(secrets.token_urlsafe(48))"    # OTP_SECRET
```

## 3. Ishga tushirish

```bash
docker compose --env-file deploy/.env.prod -f docker-compose.prod.yml up -d --build
docker compose --env-file deploy/.env.prod -f docker-compose.prod.yml ps
docker compose --env-file deploy/.env.prod -f docker-compose.prod.yml logs -f backend caddy
```

Backend ishga tushganda migratsiyalarni o'zi qo'llaydi (`RUN_MIGRATIONS=1`), keyin
`BOT_MODE=webhook` bo'lsa Telegramga `https://DOMAIN/api/v1/telegram/webhook` manzilini
va "📱 Nasiya Pro" menyu tugmasini o'rnatadi.

Tekshirish:

```bash
curl -fsS https://app.example.uz/api/v1/health/ready
curl -fsSI https://app.example.uz/admin | head -1
```

## 4. Birinchi ulgurji sotuvchi (tenant)

Bazada hali ulgurjichi yo'q (demo ma'lumotlar productionga yuklanmaydi). Kompaniyani va
uning egasini (Telegram ID) yarating — rekvizitlar akt-sverkada chiqadi:

```bash
docker compose --env-file deploy/.env.prod -f docker-compose.prod.yml run --rm backend \
  python -m app.cli.create_wholesaler \
    --name "Samarqand Optom" --legal-name '"Samarqand Optom" MChJ' --tin 305112233 \
    --owner-telegram-id <TELEGRAM_ID> --owner-name "Ism Familiya" \
    --phone "+998 90 123 45 67" --address "Samarqand sh., ..." \
    --bank-name "..." --bank-account "..." --bank-mfo "..." --payment-term-days 14
```

Ega botda `/start` bosganda "👑 Boshqaruv paneli" chiqadi. Tovar va do'konlarni Mini App →
**Import** bo'limidan Excel orqali kiriting.

## 5. Yangilash

```bash
git pull
docker compose --env-file deploy/.env.prod -f docker-compose.prod.yml up -d --build
docker image prune -f
```

## 6. Zaxira nusxa (backup)

```bash
# Kunlik dump (cron'ga qo'ying):
docker compose --env-file deploy/.env.prod -f docker-compose.prod.yml exec -T postgres \
  pg_dump -U nasiya -d nasiya_pro --format=custom > "backup_$(date +%F).dump"

# Tiklash:
docker compose --env-file deploy/.env.prod -f docker-compose.prod.yml exec -T postgres \
  pg_restore -U nasiya -d nasiya_pro --clean --if-exists < backup_2026-11-08.dump
```

`caddy_data` volume'ini o'chirmang: unda TLS sertifikatlari saqlanadi (aks holda Let's
Encrypt limitiga tushib qolish mumkin).

## Eslatmalar

- **Bot rejimi.** `BOT_MODE=webhook` (standart) har qancha `WEB_CONCURRENCY` bilan
  ishlaydi. `polling` faqat bitta jarayonda: `WEB_CONCURRENCY=1` (aks holda konteyner
  ishga tushmaydi — ikki poller bir tokenda to'qnashadi).
- **Bitta token — bitta muhit.** Productionda webhook o'rnatilgach, xuddi shu token bilan
  lokal `run_live.py` (polling) ishga tushirilsa, u webhookni o'chiradi. Dev uchun alohida
  bot (BotFather) oching.
- **Fayl hajmi.** Excel importi 5 MB gacha; Caddy 8 MB dan katta so'rovni rad etadi.
  Oylik akt-sverka ZIP so'rov paytida yaratiladi, Caddy uni 300 soniyagacha kutadi.
- **Iframe.** `X-Frame-Options` qo'yilmagan: Telegram Web/Desktop Mini App'ni iframe
  ichida ochadi.
