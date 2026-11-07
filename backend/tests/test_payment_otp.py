from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.exceptions import (
    OtpAlreadyUsedError,
    OtpAttemptsExceededError,
    OtpExpiredError,
    OtpInvalidCodeError,
    OtpMismatchError,
    OtpRequiredError,
)
from app.models import OtpStatus, PaymentMethod, PaymentOtpVerification
from app.services.ledger_service import LedgerService
from app.services.otp_service import OtpService
from tests.conftest import FakeClock, World, end_read


def _wrong(code: str) -> str:
    return f"{(int(code) + 1) % 10_000:04d}"


async def _otp_row(session, otp_id) -> PaymentOtpVerification:  # type: ignore[no-untyped-def]
    row = await session.scalar(
        select(PaymentOtpVerification)
        .where(PaymentOtpVerification.id == otp_id)
        .execution_options(populate_existing=True)
    )
    await end_read(session)
    return row


async def test_agent_payment_requires_otp(session, world: World) -> None:
    with pytest.raises(OtpRequiredError):
        await LedgerService(session).record_payment(
            wholesaler_id=world.wholesaler.id,
            store_id=world.store.id,
            amount="100.00",
            method=PaymentMethod.CASH,
            agent_id=world.agent.id,
        )


async def test_full_otp_payment_flow(session, world: World, make_order) -> None:
    ledger = LedgerService(session)
    order = await make_order("1000.00")
    await ledger.deliver_order(wholesaler_id=world.wholesaler.id, order_id=order.id)

    otp = OtpService(session)
    issued = await otp.issue(
        wholesaler_id=world.wholesaler.id,
        store_id=world.store.id,
        agent_id=world.agent.id,
        amount="600.00",
    )
    assert len(issued.code) == 4 and issued.code.isdigit()
    assert issued.code not in issued.verification.code_hash
    assert issued.store_telegram_user_id == world.store.telegram_user_id
    otp_id = issued.verification.id  # failures below roll back and expire ORM state

    # Cannot post before verification.
    with pytest.raises(OtpRequiredError):
        await ledger.record_payment(
            wholesaler_id=world.wholesaler.id,
            store_id=world.store.id,
            amount="600.00",
            method=PaymentMethod.CASH,
            agent_id=world.agent.id,
            otp_verification_id=otp_id,
        )

    verified = await otp.verify(
        wholesaler_id=world.wholesaler.id,
        verification_id=otp_id,
        agent_id=world.agent.id,
        code=issued.code,
    )
    assert verified.status == OtpStatus.VERIFIED

    # Amount must match what the store confirmed.
    with pytest.raises(OtpMismatchError):
        await ledger.record_payment(
            wholesaler_id=world.wholesaler.id,
            store_id=world.store.id,
            amount="6000.00",
            method=PaymentMethod.CASH,
            agent_id=world.agent.id,
            otp_verification_id=otp_id,
        )

    pay = await ledger.record_payment(
        wholesaler_id=world.wholesaler.id,
        store_id=world.store.id,
        amount="600.00",
        method=PaymentMethod.CASH,
        agent_id=world.agent.id,
        otp_verification_id=otp_id,
    )
    assert pay.created and pay.store_balance == Decimal("400.00")
    assert pay.transaction.otp_verification_id == otp_id
    assert pay.transaction.agent_id == world.agent.id
    assert (await _otp_row(session, otp_id)).status == OtpStatus.CONSUMED

    # Retrying the same confirmation returns the original payment (no double posting).
    again = await ledger.record_payment(
        wholesaler_id=world.wholesaler.id,
        store_id=world.store.id,
        amount="600.00",
        method=PaymentMethod.CASH,
        agent_id=world.agent.id,
        otp_verification_id=otp_id,
    )
    assert not again.created and again.transaction.id == pay.transaction.id

    with pytest.raises(OtpAlreadyUsedError):
        await otp.verify(
            wholesaler_id=world.wholesaler.id,
            verification_id=otp_id,
            agent_id=world.agent.id,
            code=issued.code,
        )


async def test_wrong_attempts_are_persisted_and_lock(session, world: World) -> None:
    otp = OtpService(session)
    issued = await otp.issue(
        wholesaler_id=world.wholesaler.id,
        store_id=world.store.id,
        agent_id=world.agent.id,
        amount="50.00",
    )
    kwargs = dict(
        wholesaler_id=world.wholesaler.id,
        verification_id=issued.verification.id,
        agent_id=world.agent.id,
    )
    for left in (2, 1):
        with pytest.raises(OtpInvalidCodeError) as exc:
            await otp.verify(**kwargs, code=_wrong(issued.code))
        assert exc.value.details["attempts_left"] == left
        # the failed attempt survived the error (committed before raising)
        assert (await _otp_row(session, issued.verification.id)).attempts == 3 - left

    with pytest.raises(OtpAttemptsExceededError):
        await otp.verify(**kwargs, code=_wrong(issued.code))
    row = await _otp_row(session, issued.verification.id)
    assert row.status == OtpStatus.LOCKED and row.attempts == 3

    # Even the right code is useless now.
    with pytest.raises(OtpAttemptsExceededError):
        await otp.verify(**kwargs, code=issued.code)


async def test_code_expires_after_ttl(session, world: World) -> None:
    clock = FakeClock()
    otp = OtpService(session, clock=clock)
    issued = await otp.issue(
        wholesaler_id=world.wholesaler.id,
        store_id=world.store.id,
        agent_id=world.agent.id,
        amount="50.00",
    )
    clock.advance(seconds=181)
    with pytest.raises(OtpExpiredError):
        await otp.verify(
            wholesaler_id=world.wholesaler.id,
            verification_id=issued.verification.id,
            agent_id=world.agent.id,
            code=issued.code,
        )
    assert (await _otp_row(session, issued.verification.id)).status == OtpStatus.EXPIRED


async def test_new_code_supersedes_pending_one(session, world: World) -> None:
    otp = OtpService(session)
    args = dict(
        wholesaler_id=world.wholesaler.id,
        store_id=world.store.id,
        agent_id=world.agent.id,
        amount="10.00",
    )
    first = await otp.issue(**args)
    second = await otp.issue(**args)
    assert (await _otp_row(session, first.verification.id)).status == OtpStatus.CANCELLED
    assert (await _otp_row(session, second.verification.id)).status == OtpStatus.PENDING
