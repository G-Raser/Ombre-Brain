"""Dashboard API for journal entries."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from . import _shared as sh

try:
    from tools import journal as journal_tools
except ImportError:  # pragma: no cover
    from ..tools import journal as journal_tools  # type: ignore


def _truthy(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def register(mcp) -> None:
    @mcp.custom_route("/api/journals", methods=["GET"])
    async def api_journals(request: Request) -> Response:
        err = sh._require_auth(request)
        if err:
            return err
        try:
            items = await journal_tools.journal_read(
                query=request.query_params.get("query", ""),
                entry_type=request.query_params.get("entry_type", ""),
                date_from=request.query_params.get("date_from", ""),
                date_to=request.query_params.get("date_to", ""),
                tags=request.query_params.get("tags", ""),
                domain=request.query_params.get("domain", ""),
                max_results=int(request.query_params.get("max_results", "50")),
                include_full=_truthy(request.query_params.get("include_full", "")),
            )
            return JSONResponse({"ok": True, "journals": items, "total": len(items)})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    @mcp.custom_route("/api/journal/{journal_id}", methods=["GET"])
    async def api_journal_detail(request: Request) -> Response:
        err = sh._require_auth(request)
        if err:
            return err
        journal_id = request.path_params.get("journal_id", "")
        try:
            items = await journal_tools.journal_read(
                query=journal_id,
                max_results=10,
                include_full=True,
            )
            for item in items:
                if item.get("journal_id") == journal_id:
                    return JSONResponse({"ok": True, "journal": item})
            return JSONResponse({"ok": False, "error": "journal not found"}, status_code=404)
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
