"""Helpers shared by the role routers."""

from __future__ import annotations

import io
import uuid
from datetime import date
from urllib.parse import quote

from fastapi.responses import StreamingResponse

from app.services.excel_service import ImportKind, build_template, template_filename
from app.services.ledger_service import LedgerService
from app.services.pdf_service import PdfDocument


async def default_period(
    ledger: LedgerService,
    wholesaler_id: uuid.UUID,
    date_from: date | None,
    date_to: date | None,
) -> tuple[date, date]:
    """Missing bounds default to "start of the current month .. today" in the
    wholesaler's timezone."""
    today = await ledger.local_today(wholesaler_id)
    end = date_to or today
    start = date_from or end.replace(day=1)
    return start, end


def pdf_response(document: PdfDocument) -> StreamingResponse:
    disposition = (
        f"attachment; filename=\"{document.filename}\"; filename*=UTF-8''{quote(document.filename)}"
    )
    return StreamingResponse(
        io.BytesIO(document.content),
        media_type="application/pdf",
        headers={
            "Content-Disposition": disposition,
            "Content-Length": str(len(document.content)),
            "Cache-Control": "private, no-store",
            "X-Document-Code": document.document_code,
        },
    )


XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def xlsx_template_response(kind: ImportKind) -> StreamingResponse:
    content = build_template(kind)
    filename = template_filename(kind)
    return StreamingResponse(
        io.BytesIO(content),
        media_type=XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(content)),
            "Cache-Control": "no-cache",
        },
    )
