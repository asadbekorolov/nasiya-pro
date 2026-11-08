from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.schemas.common import Money
from app.services.excel_service import ImportResult


class ImportRowErrorRead(BaseModel):
    row: int
    column: str | None
    message: str


class ImportResultResponse(BaseModel):
    kind: Literal["products", "stores"]
    dry_run: bool
    total_rows: int
    imported: int
    created: int
    updated: int
    failed_rows: int
    opening_debt_total: Money
    errors: list[ImportRowErrorRead]


def import_response(result: ImportResult) -> ImportResultResponse:
    return ImportResultResponse(
        kind=result.kind,
        dry_run=result.dry_run,
        total_rows=result.total_rows,
        imported=result.imported,
        created=result.created,
        updated=result.updated,
        failed_rows=result.failed_rows,
        opening_debt_total=result.opening_debt_total,
        errors=[
            ImportRowErrorRead(row=e.row, column=e.column, message=e.message) for e in result.errors
        ],
    )
