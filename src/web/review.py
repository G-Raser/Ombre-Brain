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


def _truthy(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _review_area(value: str) -> str:
    area = str(value or "").strip().lower()
    if area not in {"pending", "approved", "rejected"}:
        raise ValueError("review status must be pending, approved, or rejected")
    return area


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

    @mcp.custom_route("/api/review/approved", methods=["GET"])
    async def api_review_approved(request: Request) -> Response:
        err = sh._require_auth(request)
        if err:
            return err
        try:
            limit = int(request.query_params.get("limit", "200"))
            items = await review_gate.list_review_records("approved", limit=limit)
            return _ok(candidates=items, total=len(items), status="approved")
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    @mcp.custom_route("/api/review/rejected", methods=["GET"])
    async def api_review_rejected(request: Request) -> Response:
        err = sh._require_auth(request)
        if err:
            return err
        try:
            limit = int(request.query_params.get("limit", "200"))
            items = await review_gate.list_review_records("rejected", limit=limit)
            return _ok(candidates=items, total=len(items), status="rejected")
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    @mcp.custom_route("/api/review/pending/{candidate_id}", methods=["GET"])
    async def api_review_pending_detail(request: Request) -> Response:
        err = sh._require_auth(request)
        if err:
            return err
        candidate_id = request.path_params.get("candidate_id", "")
        try:
            item = await review_gate.read_pending_record(
                candidate_id,
                include_content=_truthy(request.query_params.get("include_content", "true")),
                include_raw=_truthy(request.query_params.get("include_raw", "")),
                include_history=_truthy(request.query_params.get("include_history", "")),
                history_limit=int(request.query_params.get("history_limit", "10")),
            )
            if not item:
                return JSONResponse({"ok": False, "error": "Pending candidate not found"}, status_code=404)
            return _ok(candidate=item)
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    @mcp.custom_route("/api/review/{status}/{candidate_id}", methods=["GET"])
    async def api_review_candidate_detail(request: Request) -> Response:
        err = sh._require_auth(request)
        if err:
            return err
        candidate_id = request.path_params.get("candidate_id", "")
        try:
            area = _review_area(request.path_params.get("status", ""))
            item = await review_gate.read_review_record(
                area,
                candidate_id,
                include_content=_truthy(request.query_params.get("include_content", "true")),
                include_raw=_truthy(request.query_params.get("include_raw", "")),
                include_history=_truthy(request.query_params.get("include_history", "")),
                history_limit=int(request.query_params.get("history_limit", "10")),
            )
            if not item:
                return JSONResponse({"ok": False, "error": f"{area} candidate not found"}, status_code=404)
            return _ok(candidate=item, status=area)
        except ValueError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    @mcp.custom_route("/api/review/pending/{candidate_id}/update", methods=["POST"])
    async def api_review_pending_update(request: Request) -> Response:
        err = sh._require_auth(request)
        if err:
            return err
        candidate_id = request.path_params.get("candidate_id", "")
        try:
            body = await request.json()
            if not isinstance(body, dict):
                return JSONResponse({"ok": False, "error": "request body must be JSON object"}, status_code=400)
            flat_fields = {
                key: body.get(key)
                for key in (
                    "title",
                    "content",
                    "tags",
                    "domain",
                    "importance",
                    "pinned",
                    "feel",
                    "valence",
                    "arousal",
                    "why_remembered",
                    "notes",
                )
                if key in body
            }
            overrides = review_gate.coerce_review_overrides(
                overrides=body.get("overrides"),
                overrides_json=body.get("overrides_json"),
                flat_fields=flat_fields,
            )
            result = await review_gate.review_candidate_update(
                candidate_id=candidate_id,
                overrides=overrides,
                updated_by=str(body.get("updated_by") or "owner"),
                update_note=str(body.get("update_note") or ""),
            )
            return JSONResponse(result)
        except FileNotFoundError:
            return JSONResponse({"ok": False, "error": "Pending candidate not found"}, status_code=404)
        except ValueError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    @mcp.custom_route("/api/review/rejected/{candidate_id}/resubmit", methods=["POST"])
    async def api_review_rejected_resubmit(request: Request) -> Response:
        err = sh._require_auth(request)
        if err:
            return err
        candidate_id = request.path_params.get("candidate_id", "")
        try:
            body = await request.json()
            if body is None:
                body = {}
            if not isinstance(body, dict):
                return JSONResponse({"ok": False, "error": "request body must be JSON object"}, status_code=400)
            result = await review_gate.review_candidate_resubmit(
                candidate_id=candidate_id,
                overrides=body.get("overrides") if isinstance(body.get("overrides"), dict) else {},
                resubmitted_by=str(body.get("resubmitted_by") or "owner"),
                resubmit_note=str(body.get("resubmit_note") or ""),
            )
            return JSONResponse(result)
        except FileNotFoundError:
            return JSONResponse({"ok": False, "error": "Rejected candidate not found"}, status_code=404)
        except ValueError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
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

    @mcp.custom_route("/api/review/rejected/{candidate_id}/restore", methods=["POST"])
    async def api_review_rejected_restore(request: Request) -> Response:
        err = sh._require_auth(request)
        if err:
            return err
        candidate_id = request.path_params.get("candidate_id", "")
        try:
            result = await review_gate.restore_rejected_memory(candidate_id)
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
        confirmed = _truthy(request.query_params.get("confirmed", ""))
        if not confirmed:
            try:
                if (request.headers.get("content-type") or "").lower().startswith("application/json"):
                    body = await request.json()
                    confirmed = _truthy(body.get("confirmed"))
            except Exception:
                confirmed = False
        if not dry_run and not confirmed:
            return JSONResponse(
                {
                    "ok": False,
                    "dry_run": dry_run,
                    "confirmed": False,
                    "blocked": True,
                    "result": "正式批准需要 confirmed=true；未修改正式记忆库。",
                },
                status_code=409,
            )
        try:
            result = await review_gate.approve_pending_memory(
                candidate_id,
                dry_run=dry_run,
                confirmed=confirmed,
            )
            not_found = "未找到" in result or "not found" in result.lower()
            blocked = "阻止正式批准" in result or "未修改正式记忆库" in result and not dry_run
            approved = "bucket_id=" in result or "写入正式" in result or "approved" in result.lower()
            status = 404 if not_found else (409 if blocked and not dry_run else 200)
            return JSONResponse(
                {
                    "ok": (not not_found) and (dry_run or approved or not blocked),
                    "dry_run": dry_run,
                    "confirmed": confirmed,
                    "blocked": blocked,
                    "result": result,
                },
                status_code=status,
            )
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
