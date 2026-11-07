"""Domain exceptions. Each one carries a stable machine-readable `code` and an HTTP
status, so the API layer can translate them without knowing business details."""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    code: str = "domain_error"
    status_code: int = 400

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class AuthenticationError(DomainError):
    code = "authentication_failed"
    status_code = 401


class PermissionDeniedError(DomainError):
    code = "permission_denied"
    status_code = 403


class NotFoundError(DomainError):
    code = "not_found"
    status_code = 404


class InvalidAmountError(DomainError):
    code = "invalid_amount"
    status_code = 422


class InvalidOrderStateError(DomainError):
    code = "invalid_order_state"
    status_code = 409


class InactiveEntityError(DomainError):
    code = "inactive_entity"
    status_code = 409


class CreditLimitExceededError(DomainError):
    code = "credit_limit_exceeded"
    status_code = 422


class IdempotencyConflictError(DomainError):
    code = "idempotency_conflict"
    status_code = 409


class LedgerIntegrityError(DomainError):
    """Raised when an invariant of the ledger would be violated. The surrounding
    database transaction is rolled back, so nothing inconsistent is persisted."""

    code = "ledger_integrity_error"
    status_code = 500


# --- Payment OTP ---------------------------------------------------------------


class OtpError(DomainError):
    code = "otp_error"
    status_code = 400


class OtpRequiredError(OtpError):
    code = "otp_required"
    status_code = 403


class OtpInvalidCodeError(OtpError):
    code = "otp_invalid_code"
    status_code = 400


class OtpExpiredError(OtpError):
    code = "otp_expired"
    status_code = 410


class OtpAttemptsExceededError(OtpError):
    code = "otp_attempts_exceeded"
    status_code = 429


class OtpAlreadyUsedError(OtpError):
    code = "otp_already_used"
    status_code = 409


class OtpMismatchError(OtpError):
    code = "otp_mismatch"
    status_code = 409


class StoreNotLinkedError(DomainError):
    code = "store_not_linked_to_telegram"
    status_code = 409


class NotificationDeliveryError(DomainError):
    code = "notification_delivery_failed"
    status_code = 502
