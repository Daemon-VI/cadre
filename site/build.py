"""Build the docs site: site/content/*.md -> site/_build/*.html through one template (FR-20).

    uv run python site/build.py          # then: uv run python site/check_links.py

No framework. markdown-it-py (installed with rich) renders CommonMark plus tables. Two things are
filled in at build time so they cannot drift from their sources:

  <!-- project-state: M5 -->   the first table of that milestone's section in docs/PROJECT_STATE.md
  <!-- replay:facts -->        facts about the replayed run, from site/assets/replay-*.json
  <!-- replay:calls -->        one row per model call in that run

Nothing here opens Cadre's database; the replay JSON is exported once by site/export_replay.py.
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

from markdown_it import MarkdownIt

SITE = Path(__file__).resolve().parent
ROOT = SITE.parent
CONTENT = SITE / "content"
ASSETS = SITE / "assets"
OUT = SITE / "_build"
TEMPLATE = SITE / "template.html"
PROJECT_STATE = ROOT / "docs" / "PROJECT_STATE.md"
REPLAY = ASSETS / "replay-decision-board.json"
REPO = "https://github.com/Daemon-VI/cadre"

# (content file stem, navigation label, navigation group) in navigation order
PAGES = [
    ("index", "Home and quick start", "Start"),
    ("cli", "Command line", "Set up"),
    ("mcp", "AI editors (MCP)", "Set up"),
    ("github-action", "GitHub Action", "Set up"),
    ("vscode", "VS Code extension", "Set up"),
    ("containers", "Containers and downloads", "Set up"),
    ("memory", "Memory across runs", "Set up"),
    ("security", "Security model", "Trust"),
    ("numbers", "Measured numbers", "Trust"),
    ("replay", "Replay of a real run", "Trust"),
]


class BuildError(Exception):
    pass


def front_matter(text: str) -> tuple[dict[str, str], str]:
    """`---` / `key: value` lines / `---` at the top of a page."""
    if not text.startswith("---\n"):
        return {}, text
    head, sep, body = text[4:].partition("\n---\n")
    if not sep:
        raise BuildError("front matter is not closed with ---")
    meta = {}
    for line in head.splitlines():
        if line.strip():
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta, body


def slug(text: str) -> str:
    text = re.sub(r"\]\([^)]*\)|[`*\[\]]", "", text).lower()
    text = re.sub(r"[^\w\s-]", "", text).strip()
    return re.sub(r"[\s_-]+", "-", text) or "section"


# ------------------------------------------------------------------ includes
def project_state_table(milestone: str) -> str:
    """The first Markdown table in the `### <milestone> ...` section that has one."""
    lines = PROJECT_STATE.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if not re.match(rf"### {re.escape(milestone)}\b", line):
            continue
        table: list[str] = []
        for nxt in lines[i + 1:]:
            if nxt.startswith("#"):
                break
            if nxt.startswith("|"):
                table.append(nxt)
            elif table:
                break
        if table:
            return "\n".join(table)
    raise BuildError(f"no table under a '### {milestone}' heading in {PROJECT_STATE.name}")


def replay_data() -> dict:
    return json.loads(REPLAY.read_text(encoding="utf-8"))


def replay_facts(data: dict) -> str:
    r = data["run"]
    models = ", ".join(f"{m} ({n})" for m, n in r["models_used"].items())
    rows = [
        ("Run", f"{r['id']} ({r['short']})"),
        ("Template", f"{r['org']} (a council: propose, critique, options, vote, memo)"),
        ("Goal", r["goal"]),
        ("Started", f"{r['started_utc'].replace('T', ' ').rstrip('Z')} UTC ({r['date']} in IST)"),
        ("Status", r["status"]),
        ("Length", f"{r['duration_s']:.1f} s from the first event to the last"),
        ("Events", f"{r['events']}"),
        ("Model calls", f"{r['calls']}, {r['tokens_in']:,} tokens in + {r['tokens_out']:,} out"),
        ("Models that answered", models),
    ]
    items = "".join(f"<dt>{html.escape(k)}</dt><dd>{html.escape(v)}</dd>" for k, v in rows)
    return f'<dl class="facts">{items}</dl>'


def replay_calls(data: dict) -> str:
    head = ["#", "t (s)", "Agent", "Step", "Model", "Tokens in", "Tokens out", "Waited (s)",
            "Independent"]
    body = []
    for n, e in enumerate((e for e in data["events"] if e["kind"] == "agent.call"), 1):
        ind = {True: "yes", False: "no", None: "n/a"}[e.get("independent")]
        cells = [str(n), f"{e['t']:.1f}", e.get("agent", ""), e.get("step", ""), e["model"],
                 f"{e['tokens_in']:,}", f"{e['tokens_out']:,}", f"{e.get('waited') or 0:g}", ind]
        body.append("<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in cells) + "</tr>")
    thead = "".join(f"<th>{html.escape(h)}</th>" for h in head)
    return f"<table><thead><tr>{thead}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def expand_markdown_includes(body: str) -> str:
    return re.sub(r"<!-- project-state: (\S+) -->",
                  lambda m: project_state_table(m.group(1)), body)


def expand_html_includes(rendered: str) -> str:
    if "<!-- replay:" not in rendered:
        return rendered
    data = replay_data()
    rendered = rendered.replace("<!-- replay:facts -->", replay_facts(data))
    return rendered.replace("<!-- replay:calls -->", replay_calls(data))


# ------------------------------------------------------------------ rendering
def markdown() -> MarkdownIt:
    return MarkdownIt("commonmark", {"html": True}).enable("table")


def render(md: MarkdownIt, body: str) -> tuple[str, list[tuple[str, str]]]:
    tokens = md.parse(body)
    seen: dict[str, int] = {}
    toc = []
    for i, tok in enumerate(tokens):
        if tok.type == "heading_open" and tok.tag in ("h2", "h3"):
            text = tokens[i + 1].content
            base = slug(text)
            n = seen.get(base, 0)
            seen[base] = n + 1
            anchor = base if n == 0 else f"{base}-{n}"
            tok.attrSet("id", anchor)
            if tok.tag == "h2":
                toc.append((anchor, re.sub(r"\]\([^)]*\)|[`*\[\]]", "", text)))
    out = md.renderer.render(tokens, md.options, {})
    out = out.replace("<table>", '<div class="table-wrap"><table>').replace("</table>", "</table></div>")
    return out, toc


def nav_html(current: str) -> str:
    parts, group = [], None
    for stem, label, grp in PAGES:
        if grp != group:
            if group is not None:
                parts.append("</ul>")
            parts.append(f'<p class="nav-group">{html.escape(grp)}</p><ul>')
            group = grp
        here = ' aria-current="page"' if stem == current else ""
        parts.append(f'<li><a href="{stem}.html"{here}>{html.escape(label)}</a></li>')
    parts.append("</ul>")
    return "".join(parts)


def toc_html(toc: list[tuple[str, str]]) -> str:
    if len(toc) < 4:
        return ""
    items = "".join(f'<li><a href="#{a}">{html.escape(t)}</a></li>' for a, t in toc)
    return f'<nav class="toc" aria-label="On this page"><p>On this page</p><ul>{items}</ul></nav>'


def check_csp_safe(stem: str, page: str) -> None:
    """The CSP forbids inline scripts, inline styles and handlers; fail the build instead."""
    for pattern, what in ((r"<script(?![^>]*\bsrc=)", "an inline <script>"),
                          (r"<style", "a <style> block"),
                          (r"<[^>]+\sstyle=", "a style attribute"),
                          (r"<[^>]+\son[a-z]+\s*=", "an inline event handler"),
                          (r"<(?:script|img|link|iframe|source)\b[^>]*\s(?:src|href)=\"(?:https?:)?//",
                           "a resource from another origin")):
        if re.search(pattern, page):
            raise BuildError(f"{stem}: {what} would be blocked by the CSP")


def build() -> list[Path]:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    template = TEMPLATE.read_text(encoding="utf-8")
    stems = {p.stem for p in CONTENT.glob("*.md")}
    listed = {s for s, _, _ in PAGES}
    if stems != listed:
        raise BuildError(f"pages without a nav entry: {sorted(stems - listed)}; "
                         f"nav entries without a page: {sorted(listed - stems)}")
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    built = (datetime.fromtimestamp(int(epoch), UTC) if epoch else datetime.now(UTC)).strftime("%Y-%m-%d")
    md = markdown()
    written = []
    for stem, label, _ in PAGES:
        meta, body = front_matter((CONTENT / f"{stem}.md").read_text(encoding="utf-8"))
        title = meta.get("title") or label
        content, toc = render(md, expand_markdown_includes(body))
        content = expand_html_includes(content).replace("</h1>", "</h1>\n" + toc_html(toc), 1)
        scripts = "".join(f'<script src="assets/{html.escape(s.strip())}" defer></script>'
                          for s in meta.get("script", "").split(",") if s.strip())
        values = {
            "title": html.escape(title if stem == "index" else f"{title} · Cadre"),
            "description": html.escape(meta.get("description", "")),
            "body_class": f"page-{stem}" + (" wide" if meta.get("wide") == "true" else ""),
            "nav": nav_html(stem),
            "content": content,
            "scripts": scripts,
            "built": built,
            "source": f"{REPO}/blob/main/site/content/{stem}.md",
        }

        def fill(m: re.Match, stem: str = stem, values: dict = values) -> str:
            if m.group(1) not in values:
                raise BuildError(f"{stem}: the template has an unknown placeholder {m.group(0)}")
            return values[m.group(1)]

        page = re.sub(r"\{\{(\w+)\}\}", fill, template)  # one pass: page text is never re-scanned
        check_csp_safe(stem, page)
        target = OUT / f"{stem}.html"
        target.write_text(page, encoding="utf-8", newline="\n")
        written.append(target)
    shutil.copytree(ASSETS, OUT / "assets")
    return written


def main() -> int:
    try:
        build()
    except BuildError as e:
        print(f"build failed: {e}", file=sys.stderr)
        return 1
    total = 0
    for path in sorted(OUT.rglob("*")):
        if path.is_file():
            size = path.stat().st_size
            total += size
            print(f"{size:>9,}  {path.relative_to(OUT).as_posix()}")
    print(f"{total:>9,}  total in {OUT.relative_to(ROOT).as_posix()}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
