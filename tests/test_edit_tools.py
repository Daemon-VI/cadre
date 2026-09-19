import json
from pathlib import Path

import pytest

from cadre.providers import estimate_tokens
from cadre.repomap import repo_map
from cadre.tools import MAX_OBSERVATION, TOOLS
from cadre.types import ToolSpec
from cadre.workspace import Workspace, WorkspaceError

from .conftest import agent_of, by_agent, make_engine, two_family_router


def fixture_module(lines: int = 400) -> str:
    """A realistic ~400-line Python module: small functions with docstrings and bodies."""
    out = ['"""Inventory helpers (fixture)."""', "", "import math", ""]
    i = 0
    while len(out) < lines - 6:
        out += [f"def item_{i}(qty: int, price: float) -> float:",
                f'    """Total for line item {i}, with a {i % 7}% discount."""',
                "    subtotal = qty * price",
                f"    return round(subtotal * (1 - {i % 7} / 100), 2)",
                ""]
        i += 1
    out += ["", "def grand_total(rows):", "    return math.fsum(rows)", ""]
    return "\n".join(out[:lines]) + "\n"


def tokens(obj) -> int:
    text = obj if isinstance(obj, str) else json.dumps(obj)
    return len(text) // 4 + 1


def measure_edit_saving() -> dict[str, int]:
    """ADR-021: tokens to change one line of a 400-line file, whole-file rewrite vs targeted edit."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        ws = Workspace(Path(tmp))
        text = fixture_module()
        ws.write("inventory.py", text, "fixture")
        old = "    return round(subtotal * (1 - 3 / 100), 2)"
        assert text.count(old) > 1  # a naive `old` is ambiguous; a real edit includes context
        target = "def item_52(qty: int, price: float) -> float:"
        new_text = text.replace("Total for line item 52, with a 3% discount.",
                                "Total for line item 52, with a 4% discount.")
        # whole-file route: read every line (observations are cut at 4 KB, so several reads) and
        # send the complete new file back as the tool call's arguments
        reads = -(-len(text) // MAX_OBSERVATION)
        write_in = tokens(text)
        write_out = tokens({"path": "inventory.py", "content": new_text})
        # targeted route: search for the function, read a small range, send only old/new
        found = ws.search("def item_52\\(")
        line_no = int(found.split(":")[1])
        snippet = ws.read("inventory.py", start_line=line_no, end_line=line_no + 4)
        edit_in = tokens(found) + tokens(snippet)
        edit_args = {"path": "inventory.py",
                     "old": target + '\n    """Total for line item 52, with a 3% discount."""',
                     "new": target + '\n    """Total for line item 52, with a 4% discount."""'}
        edit_out = tokens(edit_args)
        ws.edit("inventory.py", edit_args["old"], edit_args["new"], "fixture")
        assert ws.read("inventory.py", max_chars=10**6) == new_text

    old_read = ToolSpec(name="read_file", description="Read a text file from the shared workspace.",
                        parameters={"type": "object", "required": ["path"], "properties": {
                            "path": {"type": "string", "description": "path relative to the workspace"}}})
    schema = {name: estimate_tokens([], [TOOLS[name].spec]) for name in ("edit_file", "search", "read_file")}
    schema["read_file_delta"] = schema["read_file"] - estimate_tokens([], [old_read])
    return {
        "file_lines": 400, "file_tokens": tokens(text), "whole_file_reads": reads,
        "write_route_in": write_in, "write_route_out": write_out,
        "edit_route_in": edit_in, "edit_route_out": edit_out,
        "saving_per_edit": (write_in + write_out) - (edit_in + edit_out),
        "schema_edit_file": schema["edit_file"], "schema_search": schema["search"],
        "schema_read_range_delta": schema["read_file_delta"],
        "schema_cost_per_call": schema["edit_file"] + schema["search"] + schema["read_file_delta"],
    }


def test_edit_saves_more_than_its_schema_costs():
    m = measure_edit_saving()
    # keep rule (ADR-021): one targeted edit per 10 calls must more than pay for the schemas
    assert m["saving_per_edit"] > 10 * m["schema_cost_per_call"], m
    assert m["edit_route_out"] * 20 < m["write_route_out"], m
    assert m["whole_file_reads"] >= 3  # a 400-line file cannot even be read in one observation


def test_edit_replaces_exactly_one_match(tmp_path):
    ws = Workspace(tmp_path / "run")
    ws.write("a.py", "x = 1\ny = 2\n", "eng")
    info = ws.edit("a.py", "y = 2", "y = 3", "eng")
    assert ws.read("a.py") == "x = 1\ny = 3\n" and info["version"] == 2


def test_zero_or_many_matches_report_the_count(tmp_path):
    ws = Workspace(tmp_path / "run")
    ws.write("a.py", "pass\npass\n", "eng")
    with pytest.raises(WorkspaceError, match="matches 2 times"):
        ws.edit("a.py", "pass", "return", "eng")
    with pytest.raises(WorkspaceError, match="matches 0 times"):
        ws.edit("a.py", "missing", "x", "eng")
    with pytest.raises(WorkspaceError, match="non-empty"):
        ws.edit("a.py", "", "x", "eng")
    assert ws.read("a.py") == "pass\npass\n"


STUBS = ('def top_words(text):\n    """Most common words."""\n    raise NotImplementedError\n\n\n'
         'def reading_time(text):\n    """Minutes to read."""\n    raise NotImplementedError\n')


def test_repeated_text_names_its_lines_and_line_picks_one(tmp_path):
    # the GitHub Action demo (2026-09-19): two identical stub lines, and the engineer burned its
    # turns on "matches 2 times" without knowing where the matches were
    ws = Workspace(tmp_path / "run")
    ws.write("core.py", STUBS, "eng")
    with pytest.raises(WorkspaceError, match=r"matches 2 times, at lines 3 and 8.*`line`"):
        ws.edit("core.py", "    raise NotImplementedError", "    return 0", "eng")
    ws.edit("core.py", "    raise NotImplementedError", "    return 0", "eng", line=8)
    second = STUBS.rindex("    raise NotImplementedError")
    assert ws.read("core.py") == STUBS[:second] + "    return 0\n"
    ws.edit("core.py", "    raise NotImplementedError", "    return []", "eng", line=2)  # nearest wins
    assert "raise" not in ws.read("core.py")


def test_line_halfway_between_two_matches_is_refused(tmp_path):
    ws = Workspace(tmp_path / "run")
    ws.write("a.py", "pass\nx\npass\n", "eng")
    with pytest.raises(WorkspaceError, match="as near"):
        ws.edit("a.py", "pass", "return", "eng", line=2)
    assert ws.read("a.py") == "pass\nx\npass\n"


def test_no_match_points_at_where_its_first_line_is(tmp_path):
    ws = Workspace(tmp_path / "run")
    ws.write("core.py", STUBS, "eng")
    # right lines, wrong indentation: say where the first line is so the model can re-read it
    with pytest.raises(WorkspaceError, match=r"matches 0 times.*first line.*line 6"):
        ws.edit("core.py", "def reading_time(text):\n  raise NotImplementedError", "x", "eng")


async def test_edit_file_tool_passes_line(tmp_path):
    from types import SimpleNamespace

    ws = Workspace(tmp_path / "run")
    ws.write("core.py", STUBS, "eng")
    ctx = SimpleNamespace(workspace=ws, note_file=lambda *a: None)
    out = await TOOLS["edit_file"].handler(ctx, "eng", "w/0", {
        "path": "core.py", "old": "    raise NotImplementedError", "new": "    return 0", "line": "8"})
    assert "edited core.py" in out and ws.read("core.py").count("NotImplementedError") == 1
    assert "line" in TOOLS["edit_file"].spec.parameters["properties"]


def test_crlf_files_accept_plain_newline_edits(tmp_path):
    ws = Workspace(tmp_path / "run")
    (ws.root / "w.txt").write_bytes(b"one\r\ntwo\r\nthree\r\n")
    ws.edit("w.txt", "one\ntwo", "one\n2", "eng")
    assert (ws.root / "w.txt").read_bytes() == b"one\r\n2\r\nthree\r\n"


async def test_edits_are_versioned(tmp_path, store):
    org = "name: t\nagents: [{id: eng, role: e, tools: [write_file, edit_file]}]\nworkflow: {agent: eng, task: go}\n"
    from cadre.types import ChatResponse, ToolCall

    def call(name, **args):
        return ChatResponse(tool_calls=[ToolCall(id=name, name=name, arguments=args)])

    script = by_agent({"eng": [call("write_file", path="m.py", content="A = 1\n"),
                               call("edit_file", path="m.py", old="A = 1", new="A = 2"),
                               call("edit_file", path="m.py", old="nope", new="x"),
                               "done"]})
    router, *_ = two_family_router(script)
    engine, ctx = make_engine(tmp_path, store, org, router)
    out = await engine.run()
    assert ctx.workspace.read("m.py") == "A = 2\n"
    assert store.files(ctx.run_id)[0]["versions"] == 2
    tools = [e["data"] for e in store.events(ctx.run_id, kinds=("agent.tool",))]
    assert [t["ok"] for t in tools] == [True, True, False]
    assert "matches 0 times" in tools[2]["result"]
    assert out.data["files"] == ["m.py"]


def test_read_line_range(tmp_path):
    ws = Workspace(tmp_path / "run")
    ws.write("f.txt", "".join(f"line {i}\n" for i in range(1, 11)), "a")
    assert ws.read("f.txt", start_line=3, end_line=4) == "[f.txt: lines 3-4 of 10]\nline 3\nline 4\n"
    assert ws.read("f.txt", start_line=9).endswith("line 9\nline 10\n")
    with pytest.raises(WorkspaceError, match="has 10 lines"):
        ws.read("f.txt", start_line=11)


def test_search_is_capped_and_confined(tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("needle outside\n")
    ws = Workspace(tmp_path / "run")
    ws.write("src/a.py", "".join(f"needle {i}\n" for i in range(60)), "a")
    ws.write("b.md", "needle in docs\n", "a")
    hits = ws.search("needle")
    assert hits.count("\n") == 40  # 40 hits + the "more matches" line
    assert "61 total" in hits and "outside" not in hits
    only_md = ws.search("needle", "*.md")
    assert only_md == "b.md:1: needle in docs"
    assert ws.search("needle[").startswith("no matches")  # invalid regex falls back to literal
    assert ws.search("zzz", "*.py") == "no matches for 'zzz' in *.py"


def test_repo_map_lists_defs_and_respects_the_cap(tmp_path):
    ws = Workspace(tmp_path / "run")
    ws.write("pkg/core.py", "class Engine:\n    pass\n\n\ndef run():\n    pass\n", "a")
    ws.write("pkg/broken.py", "def (:\n", "a")
    ws.write("README.md", "# hi\n", "a")
    m = repo_map(ws)
    assert "pkg/core.py (6 lines): class Engine, def run" in m
    assert "pkg/broken.py (1 lines): does not parse" in m and "README.md (1 lines)" in m
    for i in range(290):
        ws.write(f"many/f{i:03}.py", f"def f{i}():\n    return {i}\n", "a")
    capped = repo_map(ws, cap_tokens=200)
    with pytest.raises(WorkspaceError, match="written 300 different files"):
        for i in range(301):
            ws.write(f"new/n{i:03}.txt", "x", "a")
    assert len(capped) <= 200 * 4 + 80 and "more files" in capped


def test_repo_map_of_cadre_itself_stays_under_the_default_cap(tmp_path):
    src = Path(__file__).resolve().parents[1] / "src" / "cadre"
    ws = Workspace(tmp_path / "run")
    for f in sorted(src.glob("*.py")):
        ws.write(f.name, f.read_text(encoding="utf-8"), "copy")
    m = repo_map(ws)
    assert "engine.py" in m and "class Engine" in m
    assert len(m) // 4 <= 1200


async def test_agents_with_file_tools_get_the_map_others_get_the_listing(tmp_path, store):
    org = """
name: t
agents:
  - {id: eng, role: e, tools: [read_file]}
  - {id: pm, role: p}
workflow:
  - {agent: eng, task: look}
  - {agent: pm, task: decide}
"""
    router, a, _ = two_family_router(by_agent({"eng": ["seen"], "pm": ["ok"]}))
    engine, ctx = make_engine(tmp_path, store, org, router)
    ctx.workspace.write("app.py", "def main():\n    pass\n", "seed")
    await engine.run()
    prompts = {agent_of(c["messages"]): c["messages"][1].content for c in a.calls}
    assert "REPO MAP" in prompts["eng"] and "app.py (2 lines): def main" in prompts["eng"]
    assert "WORKSPACE FILES" in prompts["pm"] and "REPO MAP" not in prompts["pm"]
