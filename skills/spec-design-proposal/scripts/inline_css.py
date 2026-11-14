#!/usr/bin/env python3
"""proposal HTML 内の <!-- SUI_CSS --> を assets/smarthr-ui-like.css の中身に置き換える。

使い方:
    python3 inline_css.py <input.html> [<output.html>]

output を省略すると input を上書きする。
"""
import sys
from pathlib import Path

MARKER = "<!-- SUI_CSS -->"
CSS_PATH = Path(__file__).resolve().parent.parent / "assets" / "smarthr-ui-like.css"


def main() -> int:
    if len(sys.argv) not in (2, 3):
        print(__doc__, file=sys.stderr)
        return 1
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2]) if len(sys.argv) == 3 else src
    html = src.read_text(encoding="utf-8")
    if MARKER not in html:
        print(f"{MARKER} が見つからない: {src}", file=sys.stderr)
        return 1
    css = CSS_PATH.read_text(encoding="utf-8")
    dst.write_text(html.replace(MARKER, f"<style>\n{css}\n</style>", 1), encoding="utf-8")
    print(f"wrote {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
