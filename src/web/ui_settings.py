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
ALLOWED_TOP_FIELDS = {
    "theme",
    "show_mascot",
    "themes",
}
ALLOWED_THEME_FIELDS = {
    "background_enabled",
    "background_url",
    "background_dim",
    "background_blur",
    "background_position",
}
DEFAULT_THEME_SETTINGS: dict[str, Any] = {
    "background_enabled": False,
    "background_url": None,
    "background_dim": 0.72,
    "background_blur": 0,
    "background_position": "center center",
}
DEFAULT_UI_SETTINGS: dict[str, Any] = {
    "theme": "umi-purple",
    "show_mascot": False,
    "themes": {},
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


def _default_themes() -> dict[str, dict[str, Any]]:
    return {theme: dict(DEFAULT_THEME_SETTINGS) for theme in sorted(ALLOWED_THEMES)}


def _normalize_theme_settings(raw: Any) -> dict[str, Any]:
    data = dict(DEFAULT_THEME_SETTINGS)
    if isinstance(raw, dict):
        data.update({k: raw.get(k) for k in ALLOWED_THEME_FIELDS if k in raw})

    data["background_enabled"] = bool(data.get("background_enabled"))
    bg_url = data.get("background_url")
    data["background_url"] = bg_url if isinstance(bg_url, str) and bg_url.startswith("/user-assets/backgrounds/") else None
    if not data["background_url"]:
        data["background_enabled"] = False
    data["background_dim"] = _clamp_float(data.get("background_dim"), 0.72, 0.45, 0.90)
    data["background_blur"] = _clamp_float(data.get("background_blur"), 0, 0, 8)
    if data.get("background_position") not in {"center center", "top center", "bottom center"}:
        data["background_position"] = DEFAULT_THEME_SETTINGS["background_position"]
    return data


def _normalize_settings(raw: Any) -> dict[str, Any]:
    data = {
        "theme": DEFAULT_UI_SETTINGS["theme"],
        "show_mascot": DEFAULT_UI_SETTINGS["show_mascot"],
        "themes": _default_themes(),
    }
    if isinstance(raw, dict):
        data.update({k: raw.get(k) for k in ALLOWED_TOP_FIELDS if k in raw})

    if data.get("theme") not in ALLOWED_THEMES:
        data["theme"] = DEFAULT_UI_SETTINGS["theme"]
    data["show_mascot"] = bool(data.get("show_mascot"))

    themes = _default_themes()
    raw_themes = data.get("themes")
    if isinstance(raw_themes, dict):
        for theme in sorted(ALLOWED_THEMES):
            themes[theme] = _normalize_theme_settings(raw_themes.get(theme))

    # Backward-compatible migration for older ui.json files with global
    # background fields. Move those fields into the currently selected theme.
    if isinstance(raw, dict) and any(k in raw for k in ALLOWED_THEME_FIELDS):
        active_theme = data["theme"]
        legacy = {k: raw.get(k) for k in ALLOWED_THEME_FIELDS if k in raw}
        themes[active_theme] = _normalize_theme_settings({**themes[active_theme], **legacy})

    data["themes"] = themes
    return data


def _active_theme(settings: dict[str, Any]) -> str:
    theme = settings.get("theme")
    return theme if theme in ALLOWED_THEMES else DEFAULT_UI_SETTINGS["theme"]


def _update_theme_settings(settings: dict[str, Any], theme: str, patch: dict[str, Any]) -> dict[str, Any]:
    target = theme if theme in ALLOWED_THEMES else _active_theme(settings)
    themes = dict(settings.get("themes") or _default_themes())
    current = themes.get(target, dict(DEFAULT_THEME_SETTINGS))
    themes[target] = _normalize_theme_settings({**current, **patch})
    settings["themes"] = themes
    return settings


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
        data = _normalize_settings(DEFAULT_UI_SETTINGS)
        _write_settings(data)
        return data, False
    try:
        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        data = _normalize_settings(raw)
        return data, False
    except Exception as exc:
        sh.logger.warning(f"[ui-settings] failed to read ui.json, using defaults: {exc}")
        data = _normalize_settings(DEFAULT_UI_SETTINGS)
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
        next_settings = _normalize_settings(current)

        if "theme" in body:
            next_settings["theme"] = body["theme"] if body["theme"] in ALLOWED_THEMES else next_settings["theme"]
        if "show_mascot" in body:
            next_settings["show_mascot"] = bool(body.get("show_mascot"))

        theme_patch = {k: body[k] for k in ALLOWED_THEME_FIELDS if k in body}
        if theme_patch:
            target_theme = body.get("target_theme") or body.get("theme") or _active_theme(next_settings)
            _update_theme_settings(next_settings, target_theme, theme_patch)

        if isinstance(body.get("themes"), dict):
            themes = dict(next_settings.get("themes") or _default_themes())
            for theme, patch in body["themes"].items():
                if theme in ALLOWED_THEMES:
                    themes[theme] = _normalize_theme_settings({**themes.get(theme, {}), **(patch if isinstance(patch, dict) else {})})
            next_settings["themes"] = themes

        next_settings = _normalize_settings(next_settings)
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
            target_theme = form.get("theme")
            if not isinstance(target_theme, str) or target_theme not in ALLOWED_THEMES:
                target_theme = None
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
        next_settings = _normalize_settings(current)
        _update_theme_settings(
            next_settings,
            target_theme or _active_theme(next_settings),
            {
                "background_url": url,
                "background_enabled": True,
            },
        )
        _write_settings(next_settings)
        return JSONResponse({"ok": True, "url": url, "filename": filename, "settings": next_settings})

    @mcp.custom_route("/user-assets/backgrounds/{filename}", methods=["GET"])
    async def user_background_asset(request: Request) -> Response:
        return _asset_response(request.path_params.get("filename", ""))
