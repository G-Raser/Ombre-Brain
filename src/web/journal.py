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
            items = await journal_tools.journal_list(
                query=request.query_params.get("query", ""),
                entry_type=request.query_params.get("entry_type", ""),
                date_from=request.query_params.get("date_from", ""),
                date_to=request.query_params.get("date_to", ""),
                tags=request.query_params.get("tags", ""),
                domain=request.query_params.get("domain", ""),
                max_results=int(request.query_params.get("max_results", "50")),
            )
            return JSONResponse({"ok": True, "journals": items, "total": len(items)})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    @mcp.custom_route("/api/journals/trash", methods=["GET"])
    async def api_journals_trash(request: Request) -> Response:
        err = sh._require_auth(request)
        if err:
            return err
        try:
            items = await journal_tools.journal_read_trash(
                query=request.query_params.get("query", ""),
                entry_type=request.query_params.get("entry_type", ""),
                max_results=int(request.query_params.get("max_results", "50")),
                include_full=_truthy(request.query_params.get("include_full", "")),
                include_history=False,
            )
            return JSONResponse({"ok": True, "journals": items, "total": len(items)})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    @mcp.custom_route("/api/journal-detail", methods=["GET"])
    async def api_journal_detail_by_key(request: Request) -> Response:
        err = sh._require_auth(request)
        if err:
            return err
        detail_key = request.query_params.get("key", "")
        include_trash = _truthy(request.query_params.get("include_trash", ""))
        include_history = _truthy(request.query_params.get("include_history", ""))
        history_limit = int(request.query_params.get("history_limit", "10"))
        try:
            item = await journal_tools.journal_detail(
                detail_key,
                include_trash=include_trash,
                include_history=include_history,
                history_limit=history_limit,
            )
            if item:
                return JSONResponse({"ok": True, "journal": item})
            return JSONResponse({"ok": False, "error": "journal not found"}, status_code=404)
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    @mcp.custom_route("/api/journal/{journal_id}", methods=["GET"])
    async def api_journal_detail(request: Request) -> Response:
        err = sh._require_auth(request)
        if err:
            return err
        journal_id = request.path_params.get("journal_id", "")
        include_trash = _truthy(request.query_params.get("include_trash", ""))
        include_history = _truthy(request.query_params.get("include_history", ""))
        history_limit = int(request.query_params.get("history_limit", "10"))
        try:
            item = await journal_tools.journal_detail(
                journal_id,
                include_trash=include_trash,
                include_history=include_history,
                history_limit=history_limit,
            )
            if item:
                return JSONResponse({"ok": True, "journal": item})
            return JSONResponse({"ok": False, "error": "journal not found"}, status_code=404)
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    @mcp.custom_route("/api/journal/{journal_id}/history", methods=["GET"])
    async def api_journal_history(request: Request) -> Response:
        err = sh._require_auth(request)
        if err:
            return err
        journal_id = request.path_params.get("journal_id", "")
        try:
            result = await journal_tools.journal_history(
                journal_id=journal_id,
                limit=int(request.query_params.get("limit", "10")),
                include_trash=True,
            )
            return JSONResponse(result)
        except FileNotFoundError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=404)
        except ValueError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    @mcp.custom_route("/api/journal/{journal_id}/update", methods=["POST"])
    async def api_journal_update(request: Request) -> Response:
        err = sh._require_auth(request)
        if err:
            return err
        journal_id = request.path_params.get("journal_id", "")
        try:
            body = await request.json()
            if not isinstance(body, dict):
                return JSONResponse({"ok": False, "error": "request body must be a JSON object"}, status_code=400)
            kwargs = {
                "journal_id": journal_id,
                "updated_by": body.get("updated_by", "owner"),
                "update_note": body.get("update_note", ""),
            }
            if "title" in body:
                kwargs["title"] = body.get("title")
            if "content" in body:
                kwargs["content"] = body.get("content")
            if "journal_type" in body:
                kwargs["journal_type"] = body.get("journal_type")
            elif "entry_type" in body:
                kwargs["journal_type"] = body.get("entry_type")
            if "tags" in body:
                kwargs["tags"] = body.get("tags")
            if isinstance(body.get("metadata"), dict):
                kwargs["metadata"] = body.get("metadata")
            result = await journal_tools.journal_update(**kwargs)
            return JSONResponse(result)
        except FileNotFoundError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=404)
        except PermissionError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=403)
        except ValueError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    @mcp.custom_route("/api/journal/{journal_id}/delete", methods=["POST"])
    async def api_journal_delete(request: Request) -> Response:
        err = sh._require_auth(request)
        if err:
            return err
        journal_id = request.path_params.get("journal_id", "")
        try:
            body = await request.json()
            if body is None:
                body = {}
            if not isinstance(body, dict):
                return JSONResponse({"ok": False, "error": "request body must be a JSON object"}, status_code=400)
            result = await journal_tools.journal_delete(
                journal_id=journal_id,
                deleted_by=body.get("deleted_by", "owner"),
                delete_note=body.get("delete_note", ""),
            )
            return JSONResponse(result)
        except FileNotFoundError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=404)
        except PermissionError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=403)
        except ValueError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
