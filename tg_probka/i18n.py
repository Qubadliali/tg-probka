from __future__ import annotations

import json
from pathlib import Path

_DIR = Path(__file__).parent / "locales"
LANGS: dict[str, dict[str, str]] = {
    f.stem: json.loads(f.read_text(encoding="utf-8")) for f in _DIR.glob("*.json")
}


def t(lang: str, key: str) -> str:
    return (
        LANGS.get(lang, LANGS.get("ru", {})).get(key)
        or LANGS.get("ru", {}).get(key, key)
    )


def btn_filter(key: str) -> tuple[str, ...]:
    return tuple(v[key] for v in LANGS.values() if key in v)