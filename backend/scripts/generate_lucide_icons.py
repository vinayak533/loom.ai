"""Regenerate `app/agents/data/lucide_icons.json` from the installed lucide-react.

Agent 3 is told to name Lucide icons, and an icon that does not exist in the
*installed build* renders as nothing. So the reference it consults is generated
from `frontend/node_modules/lucide-react` rather than typed out by hand — a
hand-written list is a list that is wrong the next time the package is upgraded.

Run it after changing the lucide-react version:

    python scripts/generate_lucide_icons.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
NODE_MODULES = BACKEND.parent / "frontend" / "node_modules" / "lucide-react"
OUT = BACKEND / "app" / "agents" / "data" / "lucide_icons.json"

#: Not icons. `lucide-react` also exports its base component, an alias helper
#: and the icon-node map; shipping those to the model as valid icon names would
#: be exactly the kind of near-miss that renders blank.
NOT_ICONS = {"Icon", "createLucideIcon", "icons", "default", "IconNode", "LucideProps"}

#: Deprecated aliases are exported for compatibility and still render, but they
#: double the list without adding a single new glyph. Dropped so the model
#: picks from canonical names.
ALIAS_SUFFIXES = ("Icon",)


def main() -> int:
    entry = _find_types()
    if entry is None:
        print(
            f"lucide-react not found under {NODE_MODULES}. "
            "Run `npm install` in frontend/ first.",
            file=sys.stderr,
        )
        return 1

    source = entry.read_text(encoding="utf-8", errors="replace")
    names = set(re.findall(r"declare const (\w+)\s*:", source))
    names |= set(re.findall(r"^\s*(\w+) as (\w+)", source, re.MULTILINE))  # noqa: F841
    names = {n for n in names if isinstance(n, str)}

    if not names:
        # The bundled .d.ts layout changes between majors; fall back to the
        # export list, which has been stable for far longer.
        names = set(re.findall(r"\b([A-Z][A-Za-z0-9]+) as ", source))

    icons = sorted(
        n
        for n in names
        if n not in NOT_ICONS
        and n[0].isupper()
        and not any(n.endswith(s) and n[: -len(s)] in names for s in ALIAS_SUFFIXES)
    )

    if len(icons) < 500:
        print(
            f"Only found {len(icons)} icons — that is too few to be right. "
            "The lucide-react type layout has probably changed; fix the regex "
            "above rather than shipping a truncated list.",
            file=sys.stderr,
        )
        return 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps({"version": _version(), "icons": icons}, indent=1),
        encoding="utf-8",
    )
    print(f"Wrote {len(icons)} icon names to {OUT.relative_to(BACKEND)}")
    return 0


def _find_types() -> Path | None:
    for candidate in (
        NODE_MODULES / "dist" / "lucide-react.d.ts",
        NODE_MODULES / "dist" / "lucide-react.suffixed.d.ts",
        NODE_MODULES / "dist" / "cjs" / "lucide-react.d.ts",
    ):
        if candidate.exists():
            return candidate
    matches = sorted((NODE_MODULES / "dist").glob("*.d.ts")) if NODE_MODULES.exists() else []
    return matches[0] if matches else None


def _version() -> str:
    pkg = NODE_MODULES / "package.json"
    if not pkg.exists():
        return "unknown"
    try:
        return json.loads(pkg.read_text(encoding="utf-8")).get("version", "unknown")
    except Exception:  # noqa: BLE001
        return "unknown"


if __name__ == "__main__":
    raise SystemExit(main())
