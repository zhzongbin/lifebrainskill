#!/usr/bin/env python3
"""Rebuild project index files from weekly journals.

Journals are the single source of truth; projects/*.md are derived.
Entry format: - HH:MM | project | type | duration | content

Usage:
    python3 rebuild_index.py [--data-dir ~/lifebrain] [--quiet]

Exit codes: 0 = ok, 1 = no journals, 2 = dropped unparseable entry lines.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

ENTRY = re.compile(r"^- (\d{2}:\d{2}|--:--) \| ([^|]+) \| ([^|]+) \| ([\d.]+[hm]) \| (.+)$")
HOURS = re.compile(r"([\d.]+)h")
DATE_HEAD = re.compile(r"^## (\d{4}-\d{2}-\d{2})\s*$")
# 待办引用：条目行可选的第 6 段，形如 `- 09:30 | web | 开发 | 2h | 做事 | t3`
TODO_REF = re.compile(r"^t\d+$")


# 家目录 / 配置 / 数据目录的定位统一走 scope.py（多用户隔离的唯一入口）。
# 这里只做转出，保持 `from rebuild_index import config_home` 这类旧用法可用。
from scope import (  # noqa: E402,F401
    config_home,
    config_path,
    data_dir_source,
    default_data_dir,
    scope_home,
    scope_status,
)


def read_yaml(path: Path) -> dict:
    """Read a small YAML file; falls back to a mini parser when PyYAML is absent."""
    text = path.read_text(encoding="utf-8")
    if yaml is not None:
        return yaml.safe_load(text) or {}
    return mini_yaml_load(text)


def mini_yaml_load(text: str) -> dict:
    """Minimal nested-mapping YAML parser (2-space indent, scalars only)."""
    root: dict = {}
    stack: list[tuple[int, dict]] = [(-1, root)]
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        line = raw.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1] if stack else root
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if value.startswith('"') and value.endswith('"') and len(value) >= 2:
            try:
                value = json.loads(value)
            except ValueError:
                value = value.strip('"').strip("'")
        else:
            value = value.strip('"').strip("'")
        if value == "":
            node: dict = {}
            parent[key] = node
            stack.append((indent, node))
        else:
            parent[key] = value
    return root


def to_hours(dur: str) -> float:
    """Parse a duration like ``2h`` / ``90m`` / ``2`` (bare number = hours)."""
    s = str(dur).strip()
    m = HOURS.match(s)
    if m:
        return float(m.group(1))
    if s.endswith("m"):
        try:
            return float(s[:-1]) / 60.0
        except ValueError:
            return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def split_entry_line(line: str) -> tuple[str, str, str, str, str, str] | None:
    """Parse one journal entry line into (time, project, type, duration, content, todo_id).

    The 6th field (todo id, e.g. ``t3``) is optional and only recognised when the
    trailing segment looks exactly like ``t<digits>`` — older 5-field lines keep
    working untouched.
    """
    m = ENTRY.match(line)
    if not m:
        return None
    time_s, project, type_s, duration, rest = (
        m.group(1), m.group(2).strip(), m.group(3).strip(), m.group(4), m.group(5)
    )
    todo_id = ""
    if " | " in rest:
        head, _, tail = rest.rpartition(" | ")
        if head and TODO_REF.match(tail.strip()):
            rest, todo_id = head, tail.strip()
    return time_s, project, type_s, duration, rest, todo_id


def parse_journal(text: str) -> tuple[dict[str, list[tuple[str, str, str, str, str, str]]], list[tuple[int, str]]]:
    """Return (date -> entries, dropped_lines) for one weekly journal."""
    by_date: dict[str, list[tuple[str, str, str, str, str, str]]] = defaultdict(list)
    dropped: list[tuple[int, str]] = []
    date = None
    for lineno, line in enumerate(text.splitlines(), start=1):
        m = DATE_HEAD.match(line)
        if m:
            date = m.group(1)
            continue
        parsed = split_entry_line(line)
        if parsed and date:
            by_date[date].append(parsed)
        elif line.startswith("- ") and "无记录" not in line:
            dropped.append((lineno, line))
    return dict(by_date), dropped


def rebuild(root: Path, *, registry: dict | None = None) -> dict:
    """Regenerate projects/<name>.md from the journals. Returns a summary dict."""
    journals = sorted((root / "journals").glob("*.md"))
    if not journals:
        return {"ok": False, "reason": "no journals found", "generated": [], "dropped": []}

    if registry is None:
        registry = {}
        reg_file = root / "projects.yaml"
        if reg_file.exists():
            registry = (read_yaml(reg_file) or {}).get("projects", {}) or {}

    # project -> week -> list of (date, time, type, duration, content)
    timeline: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    dropped: list[dict] = []
    for jf in journals:
        week = jf.stem  # e.g. 2026-W43
        by_date, bad = parse_journal(jf.read_text(encoding="utf-8"))
        for lineno, line in bad:
            dropped.append({"file": jf.name, "line": lineno, "text": line})
        for date, entries in by_date.items():
            for entry in entries:
                timeline[entry[1]][week].append((date, *entry))
    out_dir = root / "projects"
    out_dir.mkdir(parents=True, exist_ok=True)
    generated: list[str] = []
    totals: dict[str, float] = {}
    for proj, weeks in sorted(timeline.items()):
        meta = registry.get(proj, {}) or {}
        lines = [f"# {proj}", "",
                 f"- 类别: {meta.get('category', '?')}  状态: {meta.get('status', '?')}",
                 f"- 目标: {meta.get('goal', '—')}", ""]
        total = 0.0
        for week in sorted(weeks):
            lines.append(f"## {week}")
            whours = 0.0
            for date, t, _proj, typ, dur, content, todo_id in weeks[week]:
                mark = f" [{todo_id}]" if todo_id else ""
                lines.append(f"- {date} {t} | {typ} | {dur} | {content}{mark}")
                whours += to_hours(dur)
            lines += ["", f"小计: {whours:.1f}h", ""]
            total += whours
        lines.append(f"总计: {total:.1f}h")
        (out_dir / f"{proj}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        generated.append(proj)
        totals[proj] = round(total, 2)

    removed: list[str] = []
    for f in out_dir.glob("*.md"):
        if f.stem not in generated:
            f.unlink()
            removed.append(f.name)

    return {
        "ok": True,
        "generated": generated,
        "totals": totals,
        "dropped": dropped,
        "removed": removed,
        "journals": [j.name for j in journals],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Rebuild lifebrain project indexes from weekly journals.")
    ap.add_argument("--data-dir", default=None, help="Override the data dir (default: $LB_DATA_DIR / config.yaml / ~/lifebrain).")
    ap.add_argument("--quiet", action="store_true", help="Only print errors.")
    args = ap.parse_args(argv)

    root = Path(args.data_dir).expanduser() if args.data_dir else default_data_dir()
    result = rebuild(root)
    if not result["ok"]:
        print(result["reason"], file=sys.stderr)
        return 1
    for item in result["dropped"]:
        print(f"WARNING: {item['file']}:{item['line']} unparseable entry line dropped: {item['text']}", file=sys.stderr)
    for name in result["removed"]:
        print(f"removed stale index {name}", file=sys.stderr)
    if not args.quiet:
        print(f"rebuilt {len(result['generated'])} project index file(s)")
    return 2 if result["dropped"] else 0


if __name__ == "__main__":
    sys.exit(main())
