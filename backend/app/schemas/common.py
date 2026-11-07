from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

# JSON-serialised as a string ("125000.00") so clients never lose precision.
Money = Annotated[Decimal, Field(max_digits=15, decimal_places=2)]
PositiveMoney = Annotated[Decimal, Field(gt=0, max_digits=15, decimal_places=2)]
IdempotencyKey = Annotated[str, Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_\-:.]+$")]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorBody
