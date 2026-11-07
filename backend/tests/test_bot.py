"""Telegram bot: routers through a real aiogram Dispatcher with a recording (offline)
session, the notifier's error handling, and the message templates."""

from __future__ import annotations

import secrets
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.methods import SendMessage, TelegramMethod
from aiogram.types import Chat, Contact, Message, ReplyKeyboardMarkup, Update, User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot import messages
from app.bot.dispatcher import BotRuntime, create_bot, create_dispatcher
from app.bot.notifications import BotNotifier
from app.core.config import Settings, get_settings
from app.core.exceptions import NotificationDeliveryError
from app.models import PaymentMethod, Store
from app.services.directory_service import normalize_phone
from app.services.ledger_service import LedgerService
from app.services.order_service import OrderLine, OrderService
from tests.conftest import World, end_read, next_tg_id
from tests.helpers import BOT_TOKEN, auth

D = Decimal


# --- offline Telegram ------------------------------------------------------------------


class RecordingSession(BaseSession):
    """Answers every Bot API call locally and records it."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[TelegramMethod[Any]] = []

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[Any],
        timeout: int | None = None,  # noqa: ASYNC109 - signature fixed by aiogram BaseSession
    ) -> Any:
        self.requests.append(method)
        if isinstance(method, SendMessage):
            return Message(
                message_id=len(self.requests),
                date=datetime.now(UTC),
                chat=Chat(id=int(method.chat_id), type="private"),
                text=method.text,
            )
        return True

    async def stream_content(self, *args: Any, **kwargs: Any) -> AsyncGenerator[bytes, None]:
        yield b""

    async def close(self) -> None:
        return None

    @property
    def sent(self) -> list[SendMessage]:
        return [r for r in self.requests if isinstance(r, SendMessage)]


def make_bot() -> tuple[Bot, RecordingSession]:
    session = RecordingSession()
    bot = Bot(BOT_TOKEN, session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    return bot, session


def message_update(
    user_id: int, *, text: str | None = None, contact: Contact | None = None
) -> Update:
    return Update(
        update_id=secrets.randbelow(10**9),
        message=Message(
            message_id=secrets.randbelow(10**6),
            date=datetime.now(UTC),
            chat=Chat(id=user_id, type="private"),
            from_user=User(id=user_id, is_bot=False, first_name="Test"),
            text=text,
            contact=contact,
        ),
    )


@pytest.fixture
def settings() -> Settings:
    return get_settings()


@pytest.fixture
def bot_env(sessionmaker: async_sessionmaker[AsyncSession], settings: Settings):  # type: ignore[no-untyped-def]
    bot, recorder = make_bot()
    dp = create_dispatcher(sessionmaker, settings)

    async def feed(update: Update) -> list[SendMessage]:
        before = len(recorder.sent)
        await dp.feed_update(bot, update)
        return recorder.sent[before:]

    return feed


# --- message templates -----------------------------------------------------------------


def test_payment_otp_message_matches_spec() -> None:
    text = messages.payment_otp(
        agent_name="Ali <script>", amount=D("1500000.00"), code="0427", ttl_seconds=180
    )
    assert text.startswith("⚠️ <b>Naqd pul qabul qilish so'rovi</b>")
    assert "Savdo agenti: <b>Ali &lt;script&gt;</b>" in text  # user input is escaped
    assert "Qabul qilinayotgan summa: <b>1 500 000 so'm</b>" in text
    assert "🔢 <code>0427</code> <i>(nusxalash uchun bosing)</i>" in text
    assert "Kod 3 daqiqa davomida amal qiladi" in text
    assert "kodni unga aytmang!" in text


def test_receipt_and_delivery_messages() -> None:
    receipt = messages.payment_received(
        amount=D("1200.50"),
        current_debt=D("-80.00"),
        method=PaymentMethod.CASH,
        receipt_no=7,
        receipt_hash="abcdef0123456789",
        paid_at="08.11.2026 14:05",
        agent_name="Agent",
    )
    assert "✅ <b>To'lov qabul qilindi: 1 200.50 so'm</b>" in receipt
    assert "Oldindan to'lov (avans): <b>80 so'm</b>" in receipt
    assert "№ 7 · <code>ABCDEF01</code>" in receipt

    delivered = messages.order_delivered(order_number=10001, total=D("300"), new_debt=D("4130000"))
    assert "#10001 yetkazildi" in delivered
    assert "Yangi qarz balansingiz: <b>4 130 000 so'm</b>" in delivered


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+998 (90) 111-22-33", "998901112233"),
        ("90 111 22 33", "998901112233"),
        ("998901112233", "998901112233"),
        ("+7 912 345 67 89", "79123456789"),
        ("123", None),
        (None, None),
    ],
)
def test_normalize_phone(raw: str | None, expected: str | None) -> None:
    assert normalize_phone(raw) == expected


# --- /start through the real dispatcher ---------------------------------------------------


async def test_start_for_store_owner(bot_env, world: World) -> None:
    sent = await bot_env(message_update(world.store.telegram_user_id, text="/start"))
    assert len(sent) == 1
    assert "Xush kelibsiz! Shaxsiy qarz balansingiz va tovar katalogini" in sent[0].text
    assert "Do&#x27;kon A" in sent[0].text or "Do'kon A" in sent[0].text
    button = sent[0].reply_markup.inline_keyboard[0][0]  # type: ignore[union-attr]
    assert button.text == "📱 Nasiya Pro Kabineti"
    assert button.web_app is not None and button.web_app.url == "https://app.test.example/store"


async def test_start_for_agent_and_admin(bot_env, world: World) -> None:
    sent = await bot_env(message_update(world.agent.telegram_user_id, text="/start"))
    assert "Xush kelibsiz, Savdo Agenti!" in sent[0].text
    assert sent[0].reply_markup.inline_keyboard[0][0].text == "🧭 Agent kabineti"  # type: ignore[union-attr]

    sent = await bot_env(message_update(world.wholesaler.owner_telegram_user_id, text="/start"))
    assert sent[0].text.startswith(
        "👑 Xush kelibsiz, Boshqaruvchi Test! Nasiya Pro boshqaruv panelini"
    )
    assert sent[0].reply_markup.inline_keyboard[0][0].text == "👑 Boshqaruv paneli"  # type: ignore[union-attr]


async def test_start_unregistered_asks_for_phone(bot_env) -> None:
    sent = await bot_env(message_update(next_tg_id(), text="/start"))
    assert "telefon raqamingizni" in sent[0].text
    markup = sent[0].reply_markup
    assert isinstance(markup, ReplyKeyboardMarkup)
    button = markup.keyboard[0][0]
    assert button.text == "📞 Telefon raqamni yuborish" and button.request_contact is True


async def _store_with_phone(session: AsyncSession, world: World, phone: str) -> uuid.UUID:
    async with session.begin():
        store = Store(wholesaler_id=world.wholesaler.id, name="Telefonli do'kon", phone=phone)
        session.add(store)
        await session.flush()
        return store.id


def _random_phone() -> tuple[str, str]:
    digits = f"{secrets.randbelow(10**7):07d}"
    return f"+998 (93) {digits[:3]}-{digits[3:5]}-{digits[5:]}", f"99893{digits}"


async def test_own_contact_links_store(bot_env, session, world: World) -> None:
    stored, shared = _random_phone()
    store_id = await _store_with_phone(session, world, stored)
    tg_id = next_tg_id()

    contact = Contact(phone_number=f"+{shared}", first_name="Owner", user_id=tg_id)
    sent = await bot_env(message_update(tg_id, contact=contact))
    assert "Telefon raqamingiz tasdiqlandi" in sent[0].text
    assert sent[1].reply_markup.inline_keyboard[0][0].web_app is not None  # type: ignore[union-attr]

    linked = await session.scalar(select(Store.telegram_user_id).where(Store.id == store_id))
    await end_read(session)
    assert linked == tg_id

    # Now /start greets them as a store owner.
    sent = await bot_env(message_update(tg_id, text="/start"))
    assert "Telefonli do" in sent[0].text


async def test_foreign_contact_is_rejected(bot_env, session, world: World) -> None:
    stored, shared = _random_phone()
    store_id = await _store_with_phone(session, world, stored)
    attacker = next_tg_id()
    contact = Contact(phone_number=shared, first_name="Victim", user_id=next_tg_id())
    sent = await bot_env(message_update(attacker, contact=contact))
    assert "o'zingizning" in sent[0].text
    linked = await session.scalar(select(Store.telegram_user_id).where(Store.id == store_id))
    await end_read(session)
    assert linked is None


async def test_store_bound_to_someone_else_is_not_taken_over(
    bot_env, session, world: World
) -> None:
    stored, shared = _random_phone()
    store_id = await _store_with_phone(session, world, stored)
    first, second = next_tg_id(), next_tg_id()
    await bot_env(
        message_update(first, contact=Contact(phone_number=shared, first_name="A", user_id=first))
    )
    sent = await bot_env(
        message_update(second, contact=Contact(phone_number=shared, first_name="B", user_id=second))
    )
    assert "boshqa Telegram akkauntga ulangan" in sent[0].text
    linked = await session.scalar(select(Store.telegram_user_id).where(Store.id == store_id))
    await end_read(session)
    assert linked == first


async def test_unknown_phone(bot_env) -> None:
    tg_id = next_tg_id()
    sent = await bot_env(
        message_update(
            tg_id, contact=Contact(phone_number="+998001112233", first_name="X", user_id=tg_id)
        )
    )
    assert "do'kon topilmadi" in sent[0].text


# --- notifier --------------------------------------------------------------------------


async def test_otp_is_sent_protected_and_failures_raise(
    sessionmaker: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    bot, recorder = make_bot()
    notifier = BotNotifier(bot, sessionmaker, settings)
    message_id = await notifier.send_payment_otp(
        chat_id=42, code="1234", amount=D("5000"), currency="UZS", agent_name="A", ttl_seconds=180
    )
    assert message_id == 1
    assert recorder.sent[0].protect_content is True
    assert "<code>1234</code>" in recorder.sent[0].text

    blocked = AsyncMock(
        side_effect=TelegramForbiddenError(method=recorder.sent[0], message="blocked")
    )
    bot.send_message = blocked  # type: ignore[method-assign]
    with pytest.raises(NotificationDeliveryError):
        await notifier.send_payment_otp(
            chat_id=42, code="1234", amount=D("1"), currency="UZS", agent_name="A", ttl_seconds=180
        )
    # Informational messages never raise.
    assert await notifier.send(42, "hi") is None


async def test_notifier_retries_once_on_flood_control(
    sessionmaker: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    bot, _ = make_bot()
    real = bot.send_message
    flood = TelegramRetryAfter(
        method=SendMessage(chat_id=1, text="x"), message="flood", retry_after=0
    )
    bot.send_message = AsyncMock(side_effect=[flood, await real(chat_id=1, text="ok")])  # type: ignore[method-assign]
    assert await BotNotifier(bot, sessionmaker, settings).send(1, "ok") is not None
    assert bot.send_message.await_count == 2  # type: ignore[attr-defined]


async def test_notifier_without_bot(
    sessionmaker: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    notifier = BotNotifier(None, sessionmaker, settings)
    assert await notifier.send(1, "x") is None
    with pytest.raises(NotificationDeliveryError):
        await notifier.send(1, "x", critical=True)


async def test_order_and_payment_notifications(
    session, sessionmaker: async_sessionmaker[AsyncSession], settings: Settings, world: World
) -> None:
    orders = OrderService(session)
    order = await orders.create_order(
        wholesaler_id=world.wholesaler.id,
        store_id=world.store.id,
        lines=[OrderLine(world.product.id, D("3"))],
        actor_telegram_user_id=world.store.telegram_user_id,
    )
    bot, recorder = make_bot()
    notifier = BotNotifier(bot, sessionmaker, settings)

    await notifier.order_submitted(order.id)
    recipients = {int(m.chat_id) for m in recorder.sent}
    assert recipients == {world.wholesaler.owner_telegram_user_id, world.agent.telegram_user_id}
    assert f"Yangi buyurtma #{order.order_number}" in recorder.sent[0].text
    assert "Buyurtma berdi: do'kon egasi" in recorder.sent[0].text

    await orders.approve_order(wholesaler_id=world.wholesaler.id, order_id=order.id)
    await orders.deliver_order(wholesaler_id=world.wholesaler.id, order_id=order.id)
    await notifier.order_delivered(order.id)
    delivered = recorder.sent[-1]
    assert int(delivered.chat_id) == world.store.telegram_user_id
    assert "Yangi qarz balansingiz: <b>300 so'm</b>" in delivered.text

    pay = await LedgerService(session).record_payment(
        wholesaler_id=world.wholesaler.id,
        store_id=world.store.id,
        amount="100.00",
        method=PaymentMethod.CASH,
    )
    await notifier.payment_received(pay.transaction.id)
    receipt = recorder.sent[-1]
    assert int(receipt.chat_id) == world.store.telegram_user_id
    assert "To'lov qabul qilindi: 100 so'm" in receipt.text
    assert "Joriy qarz qoldig'i: <b>200 so'm</b>" in receipt.text


async def test_notifications_never_raise(
    sessionmaker: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    bot, _ = make_bot()
    bot.send_message = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
    notifier = BotNotifier(bot, sessionmaker, settings)
    await notifier.order_submitted(uuid.uuid4())  # unknown order: silently ignored
    await notifier.payment_received(uuid.uuid4())


# --- runtime & webhook ------------------------------------------------------------------


def test_invalid_token_disables_bot(settings: Settings) -> None:
    broken = settings.model_copy(
        update={"telegram_bot_token": settings.telegram_bot_token.__class__("nope")}
    )
    assert create_bot(broken) is None


async def test_runtime_off_mode_never_calls_telegram(
    sessionmaker: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    bot, recorder = make_bot()
    runtime = BotRuntime(settings, sessionmaker, bot=bot)
    await runtime.start()
    await runtime.stop()
    assert recorder.requests == []


async def test_webhook_endpoint(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession], world: World
) -> None:
    from app.api.deps import get_bot_runtime
    from app.api.deps import get_settings as settings_dep

    secret = "w" * 40
    webhook_settings = get_settings().model_copy(
        update={
            "bot_mode": "webhook",
            "bot_webhook_secret": get_settings().otp_secret.__class__(secret),
        }
    )
    bot, recorder = make_bot()
    runtime = BotRuntime(webhook_settings, sessionmaker, bot=bot)
    app = client._transport.app  # type: ignore[attr-defined]
    app.dependency_overrides[get_bot_runtime] = lambda: runtime
    app.dependency_overrides[settings_dep] = lambda: webhook_settings

    update = message_update(world.store.telegram_user_id, text="/start")
    payload = update.model_dump(mode="json", exclude_none=True)

    r = await client.post(
        "/telegram/webhook", json=payload, headers={"X-Telegram-Bot-Api-Secret-Token": "x"}
    )
    assert r.status_code == 401
    r = await client.post(
        "/telegram/webhook", json=payload, headers={"X-Telegram-Bot-Api-Secret-Token": secret}
    )
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert "Xush kelibsiz" in recorder.sent[-1].text


async def test_webhook_is_404_outside_webhook_mode(client: httpx.AsyncClient) -> None:
    r = await client.post("/telegram/webhook", json={"update_id": 1})
    assert r.status_code == 404


# --- API triggers notifications after commit --------------------------------------------


class RecordingEvents:
    def __init__(self) -> None:
        self.calls: list[tuple[str, uuid.UUID]] = []

    async def order_submitted(self, order_id: uuid.UUID) -> None:
        self.calls.append(("order_submitted", order_id))

    async def order_delivered(self, order_id: uuid.UUID) -> None:
        self.calls.append(("order_delivered", order_id))

    async def payment_received(self, transaction_id: uuid.UUID) -> None:
        self.calls.append(("payment_received", transaction_id))


async def test_api_emits_events_once(client: httpx.AsyncClient, world: World, notifier) -> None:
    from app.api.deps import get_event_notifier

    events = RecordingEvents()
    client._transport.app.dependency_overrides[get_event_notifier] = lambda: events  # type: ignore[attr-defined]
    store, agent, admin = (
        auth(world.store.telegram_user_id),
        auth(world.agent.telegram_user_id),
        auth(world.wholesaler.owner_telegram_user_id),
    )
    body = {
        "lines": [{"product_id": str(world.product.id), "quantity": "2"}],
        "idempotency_key": f"evt-{uuid.uuid4().hex}",
    }
    order = (await client.post("/store/orders", json=body, headers=store)).json()
    await client.post("/store/orders", json=body, headers=store)  # idempotent replay
    assert events.calls == [("order_submitted", uuid.UUID(order["id"]))]

    await client.post(f"/admin/orders/{order['id']}/approve", json={}, headers=admin)
    await client.post(f"/agent/orders/{order['id']}/deliver", headers=agent)
    await client.post(f"/agent/orders/{order['id']}/deliver", headers=agent)  # replay
    assert events.calls[1:] == [("order_delivered", uuid.UUID(order["id"]))]

    r = await client.post(
        "/agent/collect-cash/request-otp",
        json={"store_id": str(world.store.id), "amount": "50.00"},
        headers=agent,
    )
    code = notifier.sent[-1]["code"]
    paid = await client.post(
        "/agent/collect-cash/verify-otp",
        json={"verification_id": r.json()["id"], "code": code},
        headers=agent,
    )
    assert events.calls[-1] == ("payment_received", uuid.UUID(paid.json()["transaction"]["id"]))

    await client.post(
        f"/admin/stores/{world.store.id}/payments",
        json={"amount": "10.00", "method": "card"},
        headers=admin,
    )
    assert [c[0] for c in events.calls].count("payment_received") == 2


async def test_start_sets_menu_button_for_the_chat(
    sessionmaker: async_sessionmaker[AsyncSession], settings: Settings, world: World
) -> None:
    from aiogram.methods import SetChatMenuButton
    from aiogram.types import MenuButtonWebApp

    bot, recorder = make_bot()
    dp = create_dispatcher(sessionmaker, settings)
    await dp.feed_update(bot, message_update(world.store.telegram_user_id, text="/start"))
    calls = [r for r in recorder.requests if isinstance(r, SetChatMenuButton)]
    assert len(calls) == 1
    assert calls[0].chat_id == world.store.telegram_user_id
    button = calls[0].menu_button
    assert isinstance(button, MenuButtonWebApp)
    assert button.text == "📱 Nasiya Pro"
    assert button.web_app.url == "https://app.test.example/store"


async def test_owner_greeting_and_admin_menu(
    sessionmaker: async_sessionmaker[AsyncSession], settings: Settings, session, world: World
) -> None:
    from aiogram.methods import SetChatMenuButton

    from app.services.owner_service import assign_owner

    await assign_owner(
        session,
        wholesaler_id=world.wholesaler.id,
        telegram_user_id=world.wholesaler.owner_telegram_user_id,
        owner_name="Asadbek O'rolov",
    )
    bot, recorder = make_bot()
    dp = create_dispatcher(sessionmaker, settings)
    await dp.feed_update(
        bot, message_update(world.wholesaler.owner_telegram_user_id, text="/start")
    )

    greeting = recorder.sent[0]
    assert greeting.text == (
        "👑 Xush kelibsiz, Boshqaruvchi Asadbek! Nasiya Pro boshqaruv panelini ochish uchun "
        "quyidagi tugmani bosing."
    )
    button = greeting.reply_markup.inline_keyboard[0][0]  # type: ignore[union-attr]
    assert button.web_app.url == "https://app.test.example/admin"  # type: ignore[union-attr]
    menu = next(r for r in recorder.requests if isinstance(r, SetChatMenuButton))
    assert menu.chat_id == world.wholesaler.owner_telegram_user_id
    assert menu.menu_button.web_app.url == "https://app.test.example/admin"  # type: ignore[union-attr]
