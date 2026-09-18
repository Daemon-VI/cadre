"""Check that every internal link in the built site resolves (FR-20).

    uv run python site/check_links.py [BUILD_DIR]

Checks href, src and data-src in every HTML file and url(...) in every CSS file: the target file
must exist inside the build, and a #fragment must name an id on the target page. External links
are counted, not fetched (the check runs offline). Exit status 1 on any broken link.
"""

from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

OUT = Path(__file__).resolve().parent / "_build"


class Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name == "id" and value:
                self.ids.add(value)
            elif name in ("href", "src", "data-src") and value is not None:
                self.links.append((f"<{tag} {name}>", value))


def main(argv: list[str]) -> int:
    root = Path(argv[1]).resolve() if len(argv) > 1 else OUT
    if not root.is_dir():
        print(f"no build at {root}; run site/build.py first", file=sys.stderr)
        return 1
    pages: dict[Path, Page] = {}
    for f in sorted(root.rglob("*.html")):
        p = Page()
        p.feed(f.read_text(encoding="utf-8"))
        pages[f.resolve()] = p
    refs: list[tuple[Path, str, str]] = []
    for f, p in pages.items():
        refs += [(f, where, link) for where, link in p.links]
    for css in sorted(root.rglob("*.css")):
        refs += [(css.resolve(), "url()", m.group(2))
                 for m in re.finditer(r"url\((['\"]?)([^'\")]+)\1\)", css.read_text(encoding="utf-8"))]

    broken, internal, external = [], 0, 0
    for source, where, link in refs:
        parts = urlsplit(link)
        if parts.scheme in ("http", "https", "mailto"):
            external += 1
            if parts.scheme == "http":
                broken.append(f"{source.name}: {where} {link} is plain http")
            continue
        if parts.scheme == "data":
            continue
        if parts.scheme or link.startswith("//"):
            broken.append(f"{source.name}: {where} {link} has an unexpected scheme")
            continue
        internal += 1
        target = (source.parent / unquote(parts.path)).resolve() if parts.path else source
        if root not in target.parents and target != root:
            broken.append(f"{source.name}: {where} {link} leaves the site")
            continue
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            broken.append(f"{source.name}: {where} {link} -> missing {target.relative_to(root).as_posix()}")
            continue
        if parts.fragment:
            page = pages.get(target)
            if page is None or unquote(parts.fragment) not in page.ids:
                broken.append(f"{source.name}: {where} {link} -> no id '{parts.fragment}' on "
                              f"{target.relative_to(root).as_posix()}")

    print(f"{len(pages)} pages, {internal} internal links checked, {external} external links not fetched")
    for b in broken:
        print(f"BROKEN {b}")
    print("ok: every internal link resolves" if not broken else f"{len(broken)} broken")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
