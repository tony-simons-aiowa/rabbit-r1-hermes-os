"""DeviceConfig defaults and normalization (SPEC 3.2).

``normalize_config`` never trusts its input: it deep-fills defaults, drops unknown
keys, coerces types, clamps numbers and validates enums and colours. The result is
always a complete, valid DeviceConfig.
"""

from __future__ import annotations

import copy
import math
import re
import secrets
from typing import Any

DEFAULT_CONFIG: dict[str, Any] = {
    "theme": {"preset": "nous", "overrides": {}},
    "font_scale": 1,
    "home": {"greeting": "", "clock": True},
    "roster": {"order": [], "hidden": [], "show_pets": True, "active_strip": True},
    "avatar_overrides": {},
    "quick_prompts": [
        {"id": "q1", "label": "Status",
         "text": "Give me a 2-sentence status update on what you're working on.", "bot": ""},
        {"id": "q2", "label": "What's next?",
         "text": "What should I look at next? Keep it short.", "bot": ""},
    ],
    "voice": {"input": "auto", "auto_speak": False},
    "buttons": {"side_click": "select", "double_click": "back", "long_press": "talk"},
    "chat": {"show_tools": True, "show_notes": True},
}

PRESETS = (
    "nous", "nous-light", "github", "catppuccin", "everforest", "solarized",
    "midnight", "ember", "mono", "slate", "cyberpunk", "classic", "rabbit",
)
THEME_TOKENS = ("bg", "surface", "border", "fg", "muted", "primary", "accent", "ok", "warn", "err")
COLOR_RE = re.compile(r"^[#a-zA-Z0-9(),.%\s-]{1,40}$")

MAX_QUICK_PROMPTS = 12
MAX_LIST_ITEMS = 100
MAX_AVATAR_OVERRIDES = 200
MAX_GREETING = 60
FONT_MIN, FONT_MAX = 0.85, 1.3


def _str(value: Any, default: str, max_len: int) -> str:
    if not isinstance(value, str):
        return default
    return value[:max_len]


def _bool(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _enum(value: Any, options: tuple[str, ...], default: str) -> str:
    return value if isinstance(value, str) and value in options else default


def _font_scale(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return 1.0
    return min(FONT_MAX, max(FONT_MIN, float(value)))


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item[:100] for item in value if isinstance(item, str)][:MAX_LIST_ITEMS]


def _theme(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    overrides: dict[str, str] = {}
    raw_overrides = raw.get("overrides")
    if isinstance(raw_overrides, dict):
        for token, color in raw_overrides.items():
            if token in THEME_TOKENS and isinstance(color, str) and COLOR_RE.match(color):
                overrides[token] = color
    return {
        "preset": _enum(raw.get("preset"), PRESETS, "nous"),
        "overrides": overrides,
    }


def _home(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    return {
        "greeting": _str(raw.get("greeting"), "", MAX_GREETING),
        "clock": _bool(raw.get("clock"), True),
    }


def _roster(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    return {
        "order": _str_list(raw.get("order")),
        "hidden": _str_list(raw.get("hidden")),
        "show_pets": _bool(raw.get("show_pets"), True),
        "active_strip": _bool(raw.get("active_strip"), True),
    }


def _avatar_overrides(value: Any) -> dict[str, dict[str, str]]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for bot_id, override in value.items():
        if len(out) >= MAX_AVATAR_OVERRIDES:
            break
        if not isinstance(bot_id, str) or not bot_id or len(bot_id) > 100 or not isinstance(override, dict):
            continue
        entry: dict[str, str] = {}
        shape = override.get("shape")
        if isinstance(shape, str) and shape:
            entry["shape"] = shape[:64]
        color = override.get("color")
        if isinstance(color, str) and COLOR_RE.match(color):
            entry["color"] = color
        if entry:
            out[bot_id] = entry
    return out


def _quick_prompts(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in value:
        if len(out) >= MAX_QUICK_PROMPTS:
            break
        if not isinstance(item, dict):
            continue
        label = _str(item.get("label"), "", 24)
        text = _str(item.get("text"), "", 500)
        if not label.strip() or not text.strip():
            continue
        qid = _str(item.get("id"), "", 32)
        if not qid or qid in seen:
            qid = "q" + secrets.token_hex(3)
        seen.add(qid)
        out.append({"id": qid, "label": label, "text": text, "bot": _str(item.get("bot"), "", 100)})
    return out


def _voice(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    return {
        "input": _enum(raw.get("input"), ("auto", "mediarecorder", "native", "off"), "auto"),
        "auto_speak": _bool(raw.get("auto_speak"), False),
    }


def _buttons(value: Any) -> dict[str, str]:
    raw = value if isinstance(value, dict) else {}
    return {
        "side_click": _enum(raw.get("side_click"), ("select",), "select"),
        "double_click": _enum(raw.get("double_click"), ("back", "menu"), "back"),
        "long_press": _enum(raw.get("long_press"), ("talk", "menu"), "talk"),
    }


def _chat(value: Any) -> dict[str, bool]:
    raw = value if isinstance(value, dict) else {}
    return {
        "show_tools": _bool(raw.get("show_tools"), True),
        "show_notes": _bool(raw.get("show_notes"), True),
    }


def normalize_config(raw: Any) -> dict[str, Any]:
    """Return a complete, valid DeviceConfig built from ``raw``.

    Raises ``ValueError`` only when ``raw`` is not a dict.
    """
    if not isinstance(raw, dict):
        raise ValueError("config must be a JSON object")
    return {
        "theme": _theme(raw.get("theme")),
        "font_scale": _font_scale(raw.get("font_scale")),
        "home": _home(raw.get("home")),
        "roster": _roster(raw.get("roster")),
        "avatar_overrides": _avatar_overrides(raw.get("avatar_overrides")),
        "quick_prompts": (
            _quick_prompts(raw["quick_prompts"])
            if "quick_prompts" in raw
            else copy.deepcopy(DEFAULT_CONFIG["quick_prompts"])
        ),
        "voice": _voice(raw.get("voice")),
        "buttons": _buttons(raw.get("buttons")),
        "chat": _chat(raw.get("chat")),
    }
