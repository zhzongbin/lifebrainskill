#!/usr/bin/env python3
"""lifebrain — 每日工作生活记录的确定性读写入口（agent 专用）。

设计原则：**用户永远不手写 Markdown，agent 永远不手改 Markdown。**
所有状态查询、条目写入、项目登记、索引重算、报告聚合都通过这里，
输出统一为 JSON（stdout），错误写 stderr 并返回非 0 退出码。

用法速查：
    lifebrain.py config
    lifebrain.py status [--date YYYY-MM-DD] [--lookback 7]
    lifebrain.py show --date YYYY-MM-DD | --week YYYY-Www | --month YYYY-MM
    lifebrain.py append --date D --time HH:MM --project P --type T --duration 2h --content "..."
    lifebrain.py append --entry "HH:MM|P|T|2h|内容"        # 一次写多条
    lifebrain.py project list | add | set
    lifebrain.py report --week YYYY-Www | --month YYYY-MM
    lifebrain.py rebuild
    lifebrain.py mark-reminder [--date YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date as date_cls
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rebuild_index import (  # noqa: E402
    DATE_HEAD,
    ENTRY,
    config_home,
    config_path,
    data_dir_source,
    default_data_dir,
    read_yaml,
    rebuild,
    to_hours,
)

TIME_RE = re.compile(r"^(\d{2}:\d{2}|--:--)$")
DUR_RE = re.compile(r"^[\d.]+[hm]$")
NO_ENTRY = "（无记录）"
CATEGORIES = ("work", "life")
STATUSES = ("active", "paused", "done")


# ---------------------------------------------------------------- basics

def _system_tz() -> str | None:
    """Best-effort system timezone name (IANA-ish)."""
    p = Path("/etc/timezone")
    if p.exists():
        try:
            name = p.read_text(encoding="utf-8").strip()
            if name:
                return name
        except OSError:
            pass
    try:
        import time

        return time.tzname[0] if time.tzname else None
    except Exception:  # noqa: BLE001
        return None


def _tz_name() -> str | None:
    """Timezone name: $LB_TIMEZONE > config.yaml:timezone > system local."""
    env = os.environ.get("LB_TIMEZONE")
    if env:
        return env
    cfg = config_path()
    if cfg.exists():
        try:
            name = read_yaml(cfg).get("timezone")
        except Exception:  # noqa: BLE001 - malformed config shouldn't crash reads
            name = None
        if name:
            return str(name)
    return None


def now_local() -> datetime:
    """Current time in the user's effective timezone (falls back to system local)."""
    name = _tz_name()
    if name:
        try:
            from zoneinfo import ZoneInfo

            return datetime.now(ZoneInfo(name))
        except Exception:  # noqa: BLE001 - unknown zone falls back to system time
            pass
    return datetime.now()


def iso_week(d: date_cls) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def week_bounds(week: str) -> tuple[date_cls, date_cls]:
    m = re.match(r"^(\d{4})-W(\d{2})$", week)
    if not m:
        raise ValueError(f"周格式应为 YYYY-Www，收到 {week!r}")
    y, w = int(m.group(1)), int(m.group(2))
    monday = date_cls.fromisocalendar(y, w, 1)
    return monday, monday + timedelta(days=6)


def parse_date(value: str) -> date_cls:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"日期格式应为 YYYY-MM-DD，收到 {value!r}") from exc


def die(msg: str, code: int = 1) -> "NoReturn":  # type: ignore[valid-type]
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def out(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------- journal io

def journal_path(root: Path, d: date_cls) -> Path:
    return root / "journals" / f"{iso_week(d)}.md"


def week_header(week: str) -> str:
    y, w = week.split("-W")
    return f"# {int(y)} 第{int(w)}周"


def parse_sections(text: str) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """Split a journal into (preamble, [(date, section_lines)])."""
    preamble: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    cur_date: str | None = None
    cur_lines: list[str] = []
    for line in text.splitlines():
        m = DATE_HEAD.match(line)
        if m:
            if cur_date is not None:
                sections.append((cur_date, cur_lines))
            cur_date, cur_lines = m.group(1), []
        elif cur_date is None:
            preamble.append(line)
        else:
            cur_lines.append(line)
    if cur_date is not None:
        sections.append((cur_date, cur_lines))
    return preamble, sections


def render_journal(preamble: list[str], sections: list[tuple[str, list[str]]]) -> str:
    head = list(preamble)
    while head and head[0].strip() == "":
        head.pop(0)
    while head and head[-1].strip() == "":
        head.pop()
    out_lines: list[str] = list(head)
    for d, lines in sections:
        body = [l.rstrip() for l in lines]
        while body and body[-1].strip() == "":
            body.pop()
        out_lines.append("")
        out_lines.append(f"## {d}")
        out_lines.extend(body)
    text = "\n".join(out_lines).strip("\n")
    return (text + "\n") if text else ""


def entry_time_key(line: str) -> str:
    m = ENTRY.match(line)
    return m.group(1) if m else "99:99"


def read_entries(root: Path) -> list[dict]:
    """All journal entries across every week file, sorted by date/time."""
    entries: list[dict] = []
    for jf in sorted((root / "journals").glob("*.md")):
        week = jf.stem
        cur = None
        for line in jf.read_text(encoding="utf-8").splitlines():
            m = DATE_HEAD.match(line)
            if m:
                cur = m.group(1)
                continue
            m = ENTRY.match(line)
            if m and cur:
                entries.append({
                    "date": cur,
                    "week": week,
                    "time": m.group(1),
                    "project": m.group(2).strip(),
                    "type": m.group(3).strip(),
                    "duration": m.group(4),
                    "hours": round(to_hours(m.group(4)), 3),
                    "content": m.group(5),
                })
    entries.sort(key=lambda e: (e["date"], e["time"]))
    return entries


def date_has_entries(root: Path, d: date_cls) -> bool:
    p = journal_path(root, d)
    if not p.exists():
        return False
    text = p.read_text(encoding="utf-8")
    _, sections = parse_sections(text)
    for sdate, lines in sections:
        if sdate == d.isoformat():
            return any(ENTRY.match(l) for l in lines)
    return False


# ---------------------------------------------------------------- projects

def registry_path(root: Path) -> Path:
    return root / "projects.yaml"


def load_registry(root: Path) -> dict:
    p = registry_path(root)
    if not p.exists():
        return {}
    data = read_yaml(p) or {}
    projects = data.get("projects") or {}
    return projects if isinstance(projects, dict) else {}


def save_registry(root: Path, projects: dict) -> None:
    p = registry_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        import yaml  # type: ignore

        p.write_text(
            yaml.safe_dump({"projects": projects}, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
    except ImportError:
        lines = ["projects:"]
        for name, meta in projects.items():
            lines.append(f"  {name}:")
            for k, v in (meta or {}).items():
                val = "" if v is None else str(v)
                lines.append(f'    {k}: "{val}"' if val else f"    {k}: ")
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- commands

def _initialized(root: Path) -> bool:
    """True when the data dir looks set up (journals dir + project registry)."""
    return (root / "journals").is_dir() and registry_path(root).exists()


def _source_for(args) -> str:
    """Describe where the effective data dir came from."""
    if getattr(args, "data_dir", None):
        return "cli:--data-dir"
    return data_dir_source()


def cmd_config(args) -> int:
    root = Path(args.data_dir).expanduser() if args.data_dir else default_data_dir()
    cfg_file = config_path()
    cfg = read_yaml(cfg_file) if cfg_file.exists() else {}
    out({
        "data_dir": str(root),
        "data_dir_source": _source_for(args),
        "config_file": str(cfg_file),
        "config": cfg,
        "reminder_time": cfg.get("reminder_time"),
        "timezone": cfg.get("timezone"),
        "initialized": _initialized(root),
        "journals_dir": str(root / "journals"),
        "projects_file": str(registry_path(root)),
        "index_dir": str(root / "projects"),
        "reports_dir": str(root / "reports"),
        "agent_id": os.environ.get("LIFEBRAIN_AGENT", "JVZG0K"),
        "skill_dir": str(Path(__file__).resolve().parent.parent),
    })
    return 0


def cmd_init(args) -> int:
    """Create the data layout and write the config (data dir is user-chosen)."""
    chosen = getattr(args, "init_data_dir", None) or args.data_dir
    target = Path(chosen).expanduser() if chosen else config_home()
    try:
        target = target.resolve()
    except OSError:
        pass
    cfg_file = config_path()
    existing = read_yaml(cfg_file).get("data_dir") if cfg_file.exists() else None
    if existing and not args.force:
        if Path(str(existing)).expanduser().resolve() != target:
            die(
                f"已经初始化在 {existing}。要换目录请加 --force（只改配置，不会搬旧数据），"
                f"或手工把 {existing} 里的文件挪过去",
                3,
            )

    created: list[str] = []
    for sub in ("", "journals", "projects", "reports"):
        d = target / sub if sub else target
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            created.append(str(d))
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    old_cfg = read_yaml(cfg_file) if cfg_file.exists() else {}
    reminder = args.reminder_time or old_cfg.get("reminder_time") or "21:30"
    tz_name = args.timezone or old_cfg.get("timezone")
    if not tz_name:
        tz_name = os.environ.get("LB_TIMEZONE") or _system_tz()
    cfg_body = {"data_dir": str(target), "reminder_time": reminder}
    if tz_name:
        cfg_body["timezone"] = str(tz_name)
    # 保留用户自己加的键，只覆盖我们管理的这几个
    merged = dict(old_cfg)
    merged.update(cfg_body)
    body = "".join(f"{k}: {json.dumps(v, ensure_ascii=False) if isinstance(v, str) else v}\n"
                   for k, v in merged.items())
    cfg_file.write_text(body, encoding="utf-8")

    reg = registry_path(target)
    if not reg.exists():
        reg.write_text("projects: {}\n", encoding="utf-8")
        created.append(str(reg))

    # 建本周周文件，把今天标成「无记录」，提醒流程才有锚点
    today = now_local().date()
    jp = journal_path(target, today)
    if not jp.exists():
        jp.write_text(
            f"{week_header(iso_week(today))}\n\n## {today.isoformat()}\n{NO_ENTRY}\n",
            encoding="utf-8",
        )
        created.append(str(jp))

    out({
        "ok": True,
        "data_dir": str(target),
        "config_file": str(cfg_file),
        "created": created,
        "reminder_time": reminder,
        "timezone": tz_name,
        "initialized": _initialized(target),
        "next_steps": [
            "登记项目：lifebrain.py project add <名称> --category work|life --goal \"一句话目标\"",
            "记第一条：lifebrain.py append --project <名称> --duration 1h --content \"做了什么\"",
            "建每日提醒任务（投递到聊天通道）：在聊天里让 agent 用 cronjob_create 建 cron:30 21 * * *",
        ],
    })
    return 0


def cmd_status(args) -> int:
    root = Path(args.data_dir).expanduser() if args.data_dir else default_data_dir()
    target = parse_date(args.date) if args.date else now_local().date()
    lookback = max(0, args.lookback)
    registry = load_registry(root)

    start = target - timedelta(days=lookback)
    missing = []
    for i in range(lookback):
        d = start + timedelta(days=i)
        if d >= target:  # today is expected to be empty until recorded
            continue
        if not date_has_entries(root, d):
            missing.append(d.isoformat())

    today = now_local().date()
    marker = root / f".reminder-{target.isoformat()}"
    out({
        "data_dir": str(root),
        "data_dir_source": _source_for(args),
        "initialized": _initialized(root),
        "date": target.isoformat(),
        "today": today.isoformat(),
        "week": iso_week(target),
        "week_file": str(journal_path(root, target)),
        "week_file_exists": journal_path(root, target).exists(),
        "recorded": date_has_entries(root, target),
        "entries": [e for e in read_entries(root) if e["date"] == target.isoformat()],
        "missing_dates": missing,
        "lookback": lookback,
        "projects": registry,
        "reminder_marker": str(marker) if marker.exists() else None,
        "reminder_fired_today": marker.exists(),
    })
    return 0


def cmd_show(args) -> int:
    root = Path(args.data_dir).expanduser() if args.data_dir else default_data_dir()
    entries = read_entries(root)
    if args.date:
        d = parse_date(args.date)
        sel = [e for e in entries if e["date"] == d.isoformat()]
    elif args.week:
        monday, sunday = week_bounds(args.week)
        sel = [e for e in entries if monday <= parse_date(e["date"]) <= sunday]
    elif args.month:
        sel = [e for e in entries if e["date"].startswith(args.month)]
    else:
        sel = entries
    out({"count": len(sel), "entries": sel})
    return 0


def _validate_entry(time_s: str, project: str, type_s: str, duration: str, content: str,
                    registry: dict) -> tuple[str, str, str, str, str]:
    if not TIME_RE.match(time_s):
        die(f"时间格式应为 HH:MM 或 --:--，收到 {time_s!r}", 2)
    if not DUR_RE.match(duration):
        die(f"时长格式应为 2h / 30m / 1.5h，收到 {duration!r}", 2)
    if "|" in content:
        die("内容里不能包含 '|'（管道符是字段分隔符）", 2)
    if not content.strip():
        die("内容不能为空", 2)
    if project not in registry:
        die(
            f"项目 {project!r} 未在 projects.yaml 登记。"
            f"先登记：lifebrain.py project add {project} --category work|life --goal \"一句话目标\"",
            3,
        )
    return time_s, project, type_s, duration, content.strip()


def cmd_append(args) -> int:
    root = Path(args.data_dir).expanduser() if args.data_dir else default_data_dir()
    target = parse_date(args.date) if args.date else now_local().date()
    registry = load_registry(root)

    raw: list[tuple[str, str, str, str, str]] = []
    if args.entry:
        for item in args.entry:
            parts = item.split("|")
            if len(parts) != 5:
                die(f"--entry 必须是 5 段 'HH:MM|项目|类型|时长|内容'，收到 {item!r}", 2)
            raw.append(tuple(p.strip() for p in parts))  # type: ignore[arg-type]
    else:
        if not (args.project and args.duration and args.content):
            die("append 需要 --entry 重复参数，或 --project/--duration/--content 三件套（--type 可选）", 2)
        time_s = args.time or now_local().strftime("%H:%M")
        type_s = args.type or "记录"
        raw.append((time_s, args.project, type_s, args.duration, args.content))

    entries = [_validate_entry(*r, registry) for r in raw]

    path = journal_path(root, target)
    path.parent.mkdir(parents=True, exist_ok=True)
    week = iso_week(target)
    if path.exists():
        text = path.read_text(encoding="utf-8")
    else:
        text = week_header(week) + "\n"
    preamble, sections = parse_sections(text)

    key = target.isoformat()
    entry_lines = [f"- {t} | {p} | {ty} | {d} | {c}" for (t, p, ty, d, c) in entries]

    found = False
    for idx, (sdate, lines) in enumerate(sections):
        if sdate == key:
            found = True
            kept = [l for l in lines if l.strip() != NO_ENTRY]
            while kept and kept[-1].strip() == "":
                kept.pop()
            merged = kept + entry_lines
            merged.sort(key=entry_time_key)
            sections[idx] = (sdate, merged)
            break
    if not found:
        new_section = (key, list(entry_lines))
        pos = len(sections)
        for i, (sdate, _) in enumerate(sections):
            if sdate < key:  # weeks are newest-first
                pos = i
                break
        sections.insert(pos, new_section)

    path.write_text(render_journal(preamble, sections), encoding="utf-8")

    result = {"ok": True, "date": key, "week": week, "file": str(path),
              "appended": len(entries),
              "entries": [{"time": t, "project": p, "type": ty, "duration": d, "content": c}
                          for (t, p, ty, d, c) in entries]}
    if not args.no_rebuild:
        rb = rebuild(root)
        result["rebuild"] = {"generated": rb.get("generated", []),
                             "dropped": rb.get("dropped", []),
                             "removed": rb.get("removed", [])}
        if rb.get("dropped"):
            result["warning"] = "有无法解析的条目行被丢弃，检查 rebuild.dropped"
    out(result)
    return 0


def cmd_project(args) -> int:
    root = Path(args.data_dir).expanduser() if args.data_dir else default_data_dir()
    projects = load_registry(root)
    name = getattr(args, "name_opt", None) or getattr(args, "name_pos", None)
    if args.action == "list":
        out({"projects": projects})
        return 0
    if args.action == "add":
        if not name:
            die("project add 需要项目名：lifebrain.py project add <名称> --category work|life --goal \"一句话目标\"", 2)
        if name in projects and not args.force:
            die(f"项目 {name!r} 已存在；用 --force 覆盖，或 project set 修改", 3)
        meta = dict(projects.get(name, {}) or {})
        meta.setdefault("category", args.category or "work")
        meta.setdefault("goal", args.goal or "")
        meta.setdefault("status", args.status or "active")
        if args.category:
            meta["category"] = args.category
        if args.goal is not None:
            meta["goal"] = args.goal
        if args.status:
            meta["status"] = args.status
        projects[name] = meta
        save_registry(root, projects)
        out({"ok": True, "action": "add", "project": name, "meta": meta})
        return 0
    if args.action == "set":
        if not name or name not in projects:
            die(f"项目未登记：{name!r}", 3)
        meta = dict(projects[name])
        if args.category:
            meta["category"] = args.category
        if args.status:
            meta["status"] = args.status
        if args.goal is not None:
            meta["goal"] = args.goal
        projects[name] = meta
        save_registry(root, projects)
        out({"ok": True, "action": "set", "project": name, "meta": meta})
        return 0
    die(f"未知 action {args.action!r}", 2)


def cmd_report(args) -> int:
    root = Path(args.data_dir).expanduser() if args.data_dir else default_data_dir()
    entries = read_entries(root)
    if args.week:
        monday, sunday = week_bounds(args.week)
        sel = [e for e in entries if monday <= parse_date(e["date"]) <= sunday]
        scope = {"week": args.week, "from": monday.isoformat(), "to": sunday.isoformat()}
    elif args.month:
        sel = [e for e in entries if e["date"].startswith(args.month)]
        y, m = args.month.split("-")
        first = date_cls(int(y), int(m), 1)
        nxt = date_cls(int(y) + (1 if int(m) == 12 else 0), 1 if int(m) == 12 else int(m) + 1, 1)
        scope = {"month": args.month, "from": first.isoformat(), "to": (nxt - timedelta(days=1)).isoformat()}
    else:
        sel = entries
        scope = {"all": True}

    by_project: dict[str, dict] = {}
    for e in sel:
        p = by_project.setdefault(e["project"], {"hours": 0.0, "count": 0, "entries": []})
        p["hours"] = round(p["hours"] + e["hours"], 3)
        p["count"] += 1
        p["entries"].append({"date": e["date"], "time": e["time"], "type": e["type"],
                             "duration": e["duration"], "content": e["content"]})
    for p in by_project.values():
        p["hours"] = round(p["hours"], 1)

    recorded_dates = sorted({e["date"] for e in sel})
    empty_dates: list[str] = []
    today_iso = now_local().date().isoformat()
    if "week" in scope:
        monday, sunday = week_bounds(scope["week"])
        d = monday
        while d <= sunday:
            if d.isoformat() <= today_iso and d.isoformat() not in recorded_dates:
                empty_dates.append(d.isoformat())
            d += timedelta(days=1)
    elif "month" in scope:
        y, m = (int(x) for x in scope["month"].split("-"))
        d = date_cls(y, m, 1)
        while d.month == m:
            if d.isoformat() <= today_iso and d.isoformat() not in recorded_dates:
                empty_dates.append(d.isoformat())
            d += timedelta(days=1)

    out({
        "scope": scope,
        "total_hours": round(sum(p["hours"] for p in by_project.values()), 1),
        "total_entries": len(sel),
        "by_project": by_project,
        "recorded_dates": recorded_dates,
        "empty_dates": empty_dates,
    })
    return 0


def cmd_rebuild(args) -> int:
    root = Path(args.data_dir).expanduser() if args.data_dir else default_data_dir()
    result = rebuild(root)
    if not result["ok"]:
        die(result["reason"], 1)
    out({"ok": True, **{k: v for k, v in result.items() if k != "ok"}})
    return 2 if result["dropped"] else 0


def cmd_mark_reminder(args) -> int:
    root = Path(args.data_dir).expanduser() if args.data_dir else default_data_dir()
    target = parse_date(args.date) if args.date else now_local().date()
    root.mkdir(parents=True, exist_ok=True)
    marker = root / f".reminder-{target.isoformat()}"
    marker.write_text(now_local().isoformat(timespec="seconds"), encoding="utf-8")
    out({"ok": True, "marker": str(marker), "date": target.isoformat()})
    return 0


# ---------------------------------------------------------------- cli

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="lifebrain", description="lifebrain 每日记录 CLI")
    ap.add_argument("--data-dir", default=None, help="覆盖数据目录（默认 $LB_DATA_DIR / config.yaml / ~/lifebrain）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("config", help="打印数据目录与配置").set_defaults(func=cmd_config)

    p = sub.add_parser("init", help="初始化数据目录（需用户确认后调用）")
    p.add_argument("--data-dir", dest="init_data_dir", default=None,
                   help="用户确认的数据目录，默认 ~/lifebrain")
    p.add_argument("--reminder-time", default=None, help="每日提醒时间 HH:MM，默认 21:30")
    p.add_argument("--timezone", default=None, help="IANA 时区，如 Asia/Shanghai；默认取系统时区")
    p.add_argument("--force", action="store_true", help="允许改写已有配置里的 data_dir")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("status", help="查询某天是否已记录、缺失日期、项目表")
    p.add_argument("--date", default=None, help="YYYY-MM-DD，默认今天")
    p.add_argument("--lookback", type=int, default=7, help="回看天数，默认 7")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("show", help="读取条目")
    p.add_argument("--date", default=None)
    p.add_argument("--week", default=None, help="YYYY-Www")
    p.add_argument("--month", default=None, help="YYYY-MM")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("append", help="写入条目（写完自动重建索引）")
    p.add_argument("--date", default=None, help="YYYY-MM-DD，默认今天")
    p.add_argument("--time", default=None, help="HH:MM，默认当前时间")
    p.add_argument("--project", default=None)
    p.add_argument("--type", default=None, help="类型短语，如 开发/会议/生活")
    p.add_argument("--duration", default=None, help="2h / 30m / 1.5h")
    p.add_argument("--content", default=None)
    p.add_argument("--entry", action="append", default=None, help="重复使用：'HH:MM|项目|类型|时长|内容'")
    p.add_argument("--no-rebuild", action="store_true")
    p.set_defaults(func=cmd_append)

    p = sub.add_parser("project", help="项目注册表")
    p.add_argument("action", choices=["list", "add", "set"])
    p.add_argument("name_pos", nargs="?", default=None, help="项目名（也可写 --name）")
    p.add_argument("--name", dest="name_opt", default=None)
    p.add_argument("--category", choices=list(CATEGORIES))
    p.add_argument("--status", choices=list(STATUSES))
    p.add_argument("--goal", default=None)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_project)

    p = sub.add_parser("report", help="按周/月聚合")
    p.add_argument("--week", default=None)
    p.add_argument("--month", default=None)
    p.set_defaults(func=cmd_report)

    sub.add_parser("rebuild", help="重建项目索引").set_defaults(func=cmd_rebuild)

    p = sub.add_parser("mark-reminder", help="写下今日提醒已触发的标记（防重复）")
    p.add_argument("--date", default=None)
    p.set_defaults(func=cmd_mark_reminder)

    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except SystemExit:
        raise
    except ValueError as exc:
        die(str(exc), 2)
    except Exception as exc:  # noqa: BLE001
        die(f"{type(exc).__name__}: {exc}", 1)


if __name__ == "__main__":
    sys.exit(main())
