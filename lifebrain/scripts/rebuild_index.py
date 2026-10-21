#!/usr/bin/env python3
"""Rebuild project index files from weekly journals.

Journals are the single source of truth; projects/*.md are derived.
Line format: - HH:MM | project | type | duration | content
"""
import re
import sys
from collections import defaultdict
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

def data_dir() -> Path:
    cfg = Path.home() / "lifebrain" / "config.yaml"
    if cfg.exists() and yaml:
        d = yaml.safe_load(cfg.read_text()).get("data_dir")
        if d:
            return Path(d).expanduser()
    return Path.home() / "lifebrain"

ENTRY = re.compile(r"^- (\d{2}:\d{2}|--:--) \| ([^|]+) \| ([^|]+) \| ([\d.]+[hm]) \| (.+)$")
HOURS = re.compile(r"([\d.]+)h")

def to_hours(dur: str) -> float:
    m = HOURS.match(dur)
    if m:
        return float(m.group(1))
    return float(dur.rstrip("m")) / 60.0

def main() -> int:
    root = data_dir()
    journals = sorted((root / "journals").glob("*.md"))
    if not journals:
        print("no journals found", file=sys.stderr)
        return 1

    registry = {}
    reg_file = root / "projects.yaml"
    if reg_file.exists() and yaml:
        registry = (yaml.safe_load(reg_file.read_text()) or {}).get("projects", {})

    # project -> week -> list of (date, time, type, duration, content)
    timeline: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    dropped = 0
    for jf in journals:
        week = jf.stem  # e.g. 2026-W43
        date = None
        for line in jf.read_text().splitlines():
            m = re.match(r"^## (\d{4}-\d{2}-\d{2})$", line)
            if m:
                date = m.group(1)
                continue
            m = ENTRY.match(line)
            if m and date:
                timeline[m.group(2).strip()][week].append((date, *m.groups()[0:1], m.group(3), m.group(4), m.group(5)))
            elif line.startswith("- ") and not line.startswith("- [") and "无记录" not in line:
                dropped += 1

    if dropped:
        print(f"WARNING: {dropped} unparseable entry line(s) dropped", file=sys.stderr)

    out_dir = root / "projects"
    out_dir.mkdir(exist_ok=True)
    generated = set()
    for proj, weeks in sorted(timeline.items()):
        meta = registry.get(proj, {})
        lines = [f"# {proj}", "",
                 f"- 类别: {meta.get('category', '?')}  状态: {meta.get('status', '?')}",
                 f"- 目标: {meta.get('goal', '—')}", ""]
        total = 0.0
        for week in sorted(weeks):
            lines.append(f"## {week}")
            whours = 0.0
            for date, t, typ, dur, content in weeks[week]:
                lines.append(f"- {date} {t} | {typ} | {dur} | {content}")
                whours += to_hours(dur)
            lines += ["", f"小计: {whours:.1f}h", ""]
            total += whours
        lines.append(f"总计: {total:.1f}h")
        (out_dir / f"{proj}.md").write_text("\n".join(lines) + "\n")
        generated.add(proj)

    # Remove stale index files for unregistered/renamed projects
    for f in out_dir.glob("*.md"):
        if f.stem not in generated:
            f.unlink()
            print(f"removed stale index {f.name}", file=sys.stderr)

    print(f"rebuilt {len(generated)} project index file(s)")
    return 0 if not dropped else 2

if __name__ == "__main__":
    sys.exit(main())
