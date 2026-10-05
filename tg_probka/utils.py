from __future__ import annotations

import html


def esc(s) -> str:
    return html.escape(str(s), quote=False)