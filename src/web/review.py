"""
Dashboard API wrappers for the Review Mode queue.

These routes keep the browser UI thin and delegate the actual review behavior to
tools.review_gate, so the MCP tools and Dashboard share one approval path.
"""

from starlette.requests import Request
from starlette.responses import Response
from starlette.responses import JSONResponse

from . import _shared as sh

try:
    from tools import review_gate
except ImportError:  # pragma: no cover
    from ..tools import review_gate  # type: ignore


def _ok(**payload) -> JSONResponse:
    data = {"ok": True}
    data.update(payload)
    return JSONResponse(data)


def register(mcp) -> None:
    @mcp.custom_route("/api/review/pending", methods=["GET"])
    async def api_review_pending(request: Request) -> Response:
        err = sh._require_auth(request)
        if err:
            return err
        try:
            limit = int(request.query_params.get("limit", "200"))
            items = await review_gate.list_pending_records(limit=limit)
            return _ok(candidates=items, total=len(items))
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    @mcp.custom_route("/api/review/pending/{candidate_id}", methods=["GET"])
    async def api_review_pending_detail(request: Request) -> Response:
        err = sh._require_auth(request)
        if err:
            return err
        candidate_id = request.path_params.get("candidate_id", "")
        try:
            item = await review_gate.read_pending_record(candidate_id)
            if not item:
                return JSONResponse({"ok": False, "error": "Pending candidate not found"}, status_code=404)
            return _ok(candidate=item)
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    @mcp.custom_route("/api/review/pending/{candidate_id}/reject", methods=["POST"])
    async def api_review_pending_reject(request: Request) -> Response:
        err = sh._require_auth(request)
        if err:
            return err
        candidate_id = request.path_params.get("candidate_id", "")
        reason = ""
        try:
            if (request.headers.get("content-type") or "").lower().startswith("application/json"):
                body = await request.json()
                reason = str(body.get("reason") or "")
        except Exception:
            reason = ""
        try:
            result = await review_gate.reject_pending_memory(candidate_id, reason)
            not_found = "未找到" in result or "not found" in result.lower()
            return JSONResponse(
                {"ok": not not_found, "result": result},
                status_code=404 if not_found else 200,
            )
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    @mcp.custom_route("/api/review/pending/{candidate_id}/approve", methods=["POST"])
    async def api_review_pending_approve(request: Request) -> Response:
        err = sh._require_auth(request)
        if err:
            return err
        candidate_id = request.path_params.get("candidate_id", "")
        dry_raw = str(request.query_params.get("dry_run", "true")).strip().lower()
        dry_run = dry_raw not in {"0", "false", "no", "off"}
        try:
            result = await review_gate.approve_pending_memory(candidate_id, dry_run=dry_run)
            not_found = "未找到" in result or "not found" in result.lower()
            blocked = "阻止正式批准" in result or "未修改正式记忆库" in result and not dry_run
            approved = "写入正式" in result or "approved" in result.lower()
            status = 404 if not_found else (409 if blocked and not dry_run else 200)
            return JSONResponse(
                {
                    "ok": (not not_found) and (dry_run or approved or not blocked),
                    "dry_run": dry_run,
                    "blocked": blocked,
                    "result": result,
                },
                status_code=status,
            )
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
