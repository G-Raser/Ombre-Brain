"""Server-side UI settings and user background assets for Dashboard."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response

from . import _shared as sh


ALLOWED_THEMES = {"ombre-original", "umi-purple", "cattea-gold-black", "cc-gold-brown"}
ALLOWED_BACKGROUND_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
MAX_BACKGROUND_BYTES = 10 * 1024 * 1024
ALLOWED_FIELDS = {
    "theme",
    "background_enabled",
    "background_url",
    "background_dim",
    "background_blur",
    "background_position",
    "show_mascot",
}
DEFAULT_UI_SETTINGS: dict[str, Any] = {
    "theme": "umi-purple",
    "background_enabled": False,
    "background_url": None,
    "background_dim": 0.72,
    "background_blur": 0,
    "background_position": "center center",
    "show_mascot": False,
}


def _buckets_dir() -> Path:
    return Path(sh.config["buckets_dir"]).resolve()


def _settings_dir() -> Path:
    return _buckets_dir() / "user_settings"


def _settings_path() -> Path:
    return _settings_dir() / "ui.json"


def _background_dir() -> Path:
    return _buckets_dir() / "user_assets" / "backgrounds"


def _clamp_float(value: Any, default: float, low: float, high: float) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, n))


def _normalize_settings(raw: Any) -> dict[str, Any]:
    data = dict(DEFAULT_UI_SETTINGS)
    if isinstance(raw, dict):
        data.update({k: raw.get(k) for k in ALLOWED_FIELDS if k in raw})

    if data.get("theme") not in ALLOWED_THEMES:
        data["theme"] = DEFAULT_UI_SETTINGS["theme"]
    data["background_enabled"] = bool(data.get("background_enabled"))
    bg_url = data.get("background_url")
    data["background_url"] = bg_url if isinstance(bg_url, str) and bg_url.startswith("/user-assets/backgrounds/") else None
    if not data["background_url"]:
        data["background_enabled"] = False
    data["background_dim"] = _clamp_float(data.get("background_dim"), 0.72, 0.45, 0.90)
    data["background_blur"] = _clamp_float(data.get("background_blur"), 0, 0, 8)
    if data.get("background_position") not in {"center center", "top center", "bottom center"}:
        data["background_position"] = DEFAULT_UI_SETTINGS["background_position"]
    data["show_mascot"] = bool(data.get("show_mascot"))
    return data


def _write_settings(settings: dict[str, Any]) -> None:
    _settings_dir().mkdir(parents=True, exist_ok=True)
    tmp = _settings_path().with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, _settings_path())


def _read_settings() -> tuple[dict[str, Any], bool]:
    path = _settings_path()
    if not path.exists():
        data = dict(DEFAULT_UI_SETTINGS)
        _write_settings(data)
        return data, False
    try:
        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        data = _normalize_settings(raw)
        return data, False
    except Exception as exc:
        sh.logger.warning(f"[ui-settings] failed to read ui.json, using defaults: {exc}")
        data = dict(DEFAULT_UI_SETTINGS)
        try:
            _write_settings(data)
        except Exception as write_exc:
            sh.logger.warning(f"[ui-settings] failed to rewrite default ui.json: {write_exc}")
        return data, True


def _safe_filename(name: str) -> str:
    stem = Path(name or "background").stem
    ext = Path(name or "").suffix.lower()
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-") or "background"
    stem = stem[:80]
    return f"bg_{time.strftime('%Y%m%d_%H%M%S')}_{stem}{ext}"


def _asset_response(filename: str) -> Response:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", filename or ""):
        return JSONResponse({"ok": False, "error": "invalid filename"}, status_code=400)
    path = (_background_dir() / filename).resolve()
    base = _background_dir().resolve()
    if base not in path.parents or path.suffix.lower() not in ALLOWED_BACKGROUND_EXTS:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    if not path.exists() or not path.is_file():
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    media_types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }
    return FileResponse(str(path), media_type=media_types.get(path.suffix.lower(), "application/octet-stream"))


def register(mcp) -> None:
    @mcp.custom_route("/api/ui-settings", methods=["GET"])
    async def api_ui_settings_get(request: Request) -> Response:
        settings, recovered = _read_settings()
        return JSONResponse({"ok": True, "settings": settings, "recovered_default": recovered})

    @mcp.custom_route("/api/ui-settings", methods=["POST"])
    async def api_ui_settings_post(request: Request) -> Response:
        err = sh._require_auth(request)
        if err:
            return err
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "invalid JSON"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "error": "body must be a JSON object"}, status_code=400)

        current, _ = _read_settings()
        update = {k: body[k] for k in ALLOWED_FIELDS if k in body}
        next_settings = _normalize_settings({**current, **update})
        _write_settings(next_settings)
        return JSONResponse({"ok": True, "settings": next_settings})

    @mcp.custom_route("/api/ui-settings/background/upload", methods=["POST"])
    async def api_ui_background_upload(request: Request) -> Response:
        err = sh._require_auth(request)
        if err:
            return err
        content_length = request.headers.get("content-length")
        try:
            if content_length and int(content_length) > MAX_BACKGROUND_BYTES + 1024 * 512:
                return JSONResponse({"ok": False, "error": "background image must be 10MB or smaller"}, status_code=413)
        except ValueError:
            pass

        try:
            form = await request.form()
            upload = form.get("file")
            if not upload or isinstance(upload, str):
                return JSONResponse({"ok": False, "error": "missing file field"}, status_code=400)
            original = getattr(upload, "filename", "background")
            ext = Path(original or "").suffix.lower()
            if ext not in ALLOWED_BACKGROUND_EXTS:
                return JSONResponse({"ok": False, "error": "allowed image types: png, jpg, jpeg, webp"}, status_code=400)
            raw = await upload.read()
        except Exception as exc:
            return JSONResponse({"ok": False, "error": f"failed to read upload: {exc}"}, status_code=400)

        if len(raw) > MAX_BACKGROUND_BYTES:
            return JSONResponse({"ok": False, "error": "background image must be 10MB or smaller"}, status_code=413)
        _background_dir().mkdir(parents=True, exist_ok=True)
        filename = _safe_filename(original)
        path = (_background_dir() / filename).resolve()
        if _background_dir().resolve() not in path.parents:
            return JSONResponse({"ok": False, "error": "invalid upload path"}, status_code=400)
        with path.open("wb") as f:
            f.write(raw)
        url = f"/user-assets/backgrounds/{filename}"

        current, _ = _read_settings()
        next_settings = _normalize_settings({
            **current,
            "background_url": url,
            "background_enabled": True,
        })
        _write_settings(next_settings)
        return JSONResponse({"ok": True, "url": url, "filename": filename, "settings": next_settings})

    @mcp.custom_route("/user-assets/backgrounds/{filename}", methods=["GET"])
    async def user_background_asset(request: Request) -> Response:
        return _asset_response(request.path_params.get("filename", ""))
