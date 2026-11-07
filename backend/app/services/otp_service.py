"""Payment OTP: the store confirms, from its own Telegram, that it handed cash to an
agent. Flow:

    issue()   -> HMAC of a fresh 4-digit code is stored; plaintext is returned ONCE so
                 the caller can send it to the store's Telegram chat.
    verify()  -> agent submits the code; max N attempts within the TTL.
    LedgerService.record_payment(otp_verification_id=...) consumes a VERIFIED code in
                 the same DB transaction that posts the payment.

Failed attempts are committed BEFORE the error is raised; otherwise rolling back the
request would also roll back the attempt counter and make brute force possible.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock, utc_now
from app.core.config import Settings, get_settings
from app.core.exceptions import (
    DomainError,
    InactiveEntityError,
    NotFoundError,
    OtpAlreadyUsedError,
    OtpAttemptsExceededError,
    OtpExpiredError,
    OtpInvalidCodeError,
    StoreNotLinkedError,
)
from app.core.money import to_positive_money
from app.core.security import generate_otp_code, hash_otp_code, verify_otp_code
from app.core.uuid7 import uuid7
from app.db.uow import atomic
from app.models import Agent, OtpStatus, PaymentMethod, PaymentOtpVerification, Store


@dataclass(frozen=True, slots=True)
class IssuedOtp:
    verification: PaymentOtpVerification
    code: str  # plaintext — send to the store, never persist or log
    store_telegram_user_id: int
    store_name: str
    agent_name: str


class OtpService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings | None = None,
        clock: Clock = utc_now,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self._clock = clock

    @property
    def _secret(self) -> str:
        return self.settings.otp_secret.get_secret_value()

    async def issue(
        self,
        *,
        wholesaler_id: uuid.UUID,
        store_id: uuid.UUID,
        agent_id: uuid.UUID,
        amount: Decimal | str | int,
        method: PaymentMethod = PaymentMethod.CASH,
    ) -> IssuedOtp:
        value = to_positive_money(amount)
        async with atomic(self.session):
            store = await self.session.scalar(
                select(Store).where(Store.id == store_id, Store.wholesaler_id == wholesaler_id)
            )
            if store is None:
                raise NotFoundError("Store not found", store_id=str(store_id))
            if store.telegram_user_id is None:
                raise StoreNotLinkedError("Store has not connected its Telegram account yet")

            agent = await self.session.scalar(
                select(Agent).where(Agent.id == agent_id, Agent.wholesaler_id == wholesaler_id)
            )
            if agent is None:
                raise NotFoundError("Agent not found", agent_id=str(agent_id))
            if not agent.is_active:
                raise InactiveEntityError("Agent is deactivated")

            # A new request supersedes any live code for the same agent/store pair.
            await self.session.execute(
                update(PaymentOtpVerification)
                .where(
                    PaymentOtpVerification.store_id == store.id,
                    PaymentOtpVerification.agent_id == agent.id,
                    PaymentOtpVerification.status == OtpStatus.PENDING,
                )
                .values(status=OtpStatus.CANCELLED)
                .execution_options(synchronize_session=False)
            )

            now = self._clock()
            verification_id = uuid7()
            code = generate_otp_code(self.settings.otp_code_length)
            verification = PaymentOtpVerification(
                id=verification_id,
                wholesaler_id=wholesaler_id,
                store_id=store.id,
                agent_id=agent.id,
                amount=value,
                payment_method=PaymentMethod(method),
                code_hash=hash_otp_code(code, verification_id=verification_id, secret=self._secret),
                attempts=0,
                max_attempts=self.settings.otp_max_attempts,
                status=OtpStatus.PENDING,
                expires_at=now + timedelta(seconds=self.settings.otp_ttl_seconds),
            )
            self.session.add(verification)
            await self.session.flush()

        return IssuedOtp(
            verification=verification,
            code=code,
            store_telegram_user_id=store.telegram_user_id,
            store_name=store.name,
            agent_name=agent.display_name,
        )

    async def attach_message_id(self, verification_id: uuid.UUID, message_id: int) -> None:
        async with atomic(self.session):
            await self.session.execute(
                update(PaymentOtpVerification)
                .where(PaymentOtpVerification.id == verification_id)
                .values(telegram_message_id=message_id)
                .execution_options(synchronize_session=False)
            )

    async def cancel(self, verification_id: uuid.UUID) -> None:
        async with atomic(self.session):
            await self.session.execute(
                update(PaymentOtpVerification)
                .where(
                    PaymentOtpVerification.id == verification_id,
                    PaymentOtpVerification.status == OtpStatus.PENDING,
                )
                .values(status=OtpStatus.CANCELLED)
                .execution_options(synchronize_session=False)
            )

    async def verify(
        self,
        *,
        wholesaler_id: uuid.UUID,
        verification_id: uuid.UUID,
        agent_id: uuid.UUID,
        code: str,
    ) -> PaymentOtpVerification:
        failure: DomainError | None = None

        async with atomic(self.session):
            otp = await self.session.scalar(
                select(PaymentOtpVerification)
                .where(
                    PaymentOtpVerification.id == verification_id,
                    PaymentOtpVerification.wholesaler_id == wholesaler_id,
                    PaymentOtpVerification.agent_id == agent_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if otp is None:
                raise NotFoundError("Payment confirmation not found")

            if otp.status == OtpStatus.VERIFIED:
                return otp  # idempotent: retry after a network error
            if otp.status == OtpStatus.CONSUMED:
                raise OtpAlreadyUsedError("This confirmation code was already used")
            if otp.status == OtpStatus.LOCKED:
                raise OtpAttemptsExceededError("Too many wrong attempts; request a new code")
            if otp.status in (OtpStatus.EXPIRED, OtpStatus.CANCELLED):
                raise OtpExpiredError("Confirmation code is no longer valid; request a new code")

            now = self._clock()
            if now >= otp.expires_at:
                otp.status = OtpStatus.EXPIRED
                failure = OtpExpiredError("Confirmation code has expired; request a new code")
            elif verify_otp_code(
                code, verification_id=otp.id, secret=self._secret, expected=otp.code_hash
            ):
                otp.status = OtpStatus.VERIFIED
                otp.verified_at = now
            else:
                otp.attempts += 1
                attempts_left = otp.max_attempts - otp.attempts
                if attempts_left <= 0:
                    otp.status = OtpStatus.LOCKED
                    failure = OtpAttemptsExceededError(
                        "Too many wrong attempts; request a new code"
                    )
                else:
                    failure = OtpInvalidCodeError(
                        "Wrong confirmation code", attempts_left=attempts_left
                    )
            await self.session.flush()

        # The state change above is committed; only now report the failure.
        if failure is not None:
            raise failure
        return otp
