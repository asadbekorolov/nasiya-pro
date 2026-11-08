"""Public file downloads authorised by a signed, short-lived token (no Telegram auth)."""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.api.deps import LedgerServiceDep, PdfServiceDep, SessionDep, SettingsDep
from app.api.v1.endpoints.admin import EXPORT_LINK_PURPOSE
from app.api.v1.endpoints.common import (
    export_file_response,
    pdf_response,
    xlsx_template_response,
    zip_response,
)
from app.api.v1.endpoints.store import STATEMENT_LINK_PURPOSE
from app.core.exceptions import AuthenticationError
from app.core.signed_links import verify_link
from app.services.excel_service import ImportKind
from app.services.export_service import ExportService

router = APIRouter(prefix="/files", tags=["files"])


@router.get(
    "/statements/{token}",
    response_class=StreamingResponse,
    responses={200: {"content": {"application/pdf": {}}}},
)
async def download_statement(
    token: str, settings: SettingsDep, pdf: PdfServiceDep
) -> StreamingResponse:
    payload = verify_link(
        token, secret=settings.otp_secret.get_secret_value(), purpose=STATEMENT_LINK_PURPOSE
    )
    try:
        wholesaler_id = uuid.UUID(payload["w"])
        store_id = uuid.UUID(payload["s"])
        date_from = date.fromisoformat(payload["f"])
        date_to = date.fromisoformat(payload["t"])
    except (KeyError, ValueError, TypeError) as exc:
        raise AuthenticationError("Download link is invalid") from exc
    document = await pdf.build(
        wholesaler_id=wholesaler_id, store_id=store_id, date_from=date_from, date_to=date_to
    )
    return pdf_response(document)


@router.get(
    "/templates/{kind}.xlsx",
    response_class=StreamingResponse,
    responses={
        200: {"content": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}}}
    },
)
async def download_import_template(kind: ImportKind) -> StreamingResponse:
    """Blank import templates hold only column headers and sample rows, so they are
    public: Telegram's `downloadFile` fetches the URL itself, without auth headers."""
    return xlsx_template_response(kind)


@router.get("/exports/{token}", response_class=StreamingResponse)
async def download_export(
    token: str,
    settings: SettingsDep,
    session: SessionDep,
    ledger: LedgerServiceDep,
    pdf: PdfServiceDep,
) -> StreamingResponse:
    """Admin export behind a signed 5-minute link minted by POST /admin/export/link."""
    payload = verify_link(
        token, secret=settings.otp_secret.get_secret_value(), purpose=EXPORT_LINK_PURPOSE
    )
    try:
        wholesaler_id = uuid.UUID(payload["w"])
        kind = str(payload["k"])
        month = str(payload.get("m") or "")
    except (KeyError, ValueError, TypeError) as exc:
        raise AuthenticationError("Download link is invalid") from exc
    service = ExportService(session, ledger, pdf)
    if kind == "stores":
        return export_file_response(await service.stores_xlsx(wholesaler_id=wholesaler_id))
    if kind == "stock":
        return export_file_response(await service.stock_xlsx(wholesaler_id=wholesaler_id))
    if kind == "statements":
        archive = await service.statements_zip(wholesaler_id=wholesaler_id, month=month)
        return zip_response(archive)
    raise AuthenticationError("Download link is invalid")
