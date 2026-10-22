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
    TODO_REF,
    config_home,
    config_path,
    data_dir_source,
    default_data_dir,
    read_yaml,
    rebuild,
    split_entry_line,
    to_hours,
)
from scope import (  # noqa: E402
    declared_scope,
    effective_scope,
    legacy_home,
    legacy_owner,
    runtime_scope,
    scope_home,
    scope_mismatch,
    scope_status,
)

TIME_RE = re.compile(r"^(\d{2}:\d{2}|--:--)$")
DUR_RE = re.compile(r"^[\d.]+[hm]$")
ESTIMATE_RE = re.compile(r"^(\d+(?:\.\d+)?)([hmd])?$", re.IGNORECASE)
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
    parsed = split_entry_line(line)
    return parsed[0] if parsed else "99:99"


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
            parsed = split_entry_line(line)
            if parsed and cur:
                time_s, project, type_s, duration, content, todo_id = parsed
                entries.append({
                    "date": cur,
                    "week": week,
                    "time": time_s,
                    "project": project,
                    "type": type_s,
                    "duration": duration,
                    "hours": round(to_hours(duration), 3),
                    "content": content,
                    "todo": todo_id or None,
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
            return any(split_entry_line(l) for l in lines)
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


# ------------------------------------------------------------------ todos

def todos_path(root: Path) -> Path:
    return root / "todos.yaml"


def load_todos(root: Path) -> dict:
    p = todos_path(root)
    if not p.exists():
        return {}
    data = read_yaml(p) or {}
    todos = data.get("todos") or {}
    return todos if isinstance(todos, dict) else {}


def save_todos(root: Path, todos: dict) -> None:
    """Write todos.yaml.

    Layout is a mapping keyed by id (not a YAML list) so both PyYAML and the
    dependency-free mini parser can read it back.
    """
    p = todos_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = ["todos:"]
    fields = ("content", "goal", "project", "estimate", "due", "created",
              "status", "done_at", "note")
    for tid, todo in todos.items():
        lines.append(f"  {tid}:")
        for k in fields:
            v = (todo or {}).get(k)
            if v in (None, ""):
                continue
            lines.append(f"    {k}: {json.dumps(str(v), ensure_ascii=False)}")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _next_todo_id(todos: dict) -> str:
    n = 0
    for tid in todos:
        m = re.fullmatch(r"t(\d+)", str(tid))
        if m:
            n = max(n, int(m.group(1)))
    return f"t{n + 1}"


def _todo_public(tid: str, todo: dict) -> dict:
    t = dict(todo or {})
    t["id"] = tid
    t.setdefault("status", "pending")
    t.setdefault("created", now_local().date().isoformat())
    return t


def _todo_hours(root: Path, entries: list[dict]) -> dict[str, float]:
    """Actual hours per todo id, summed from journal entries tagged with it."""
    out_hours: dict[str, float] = {}
    for e in entries:
        tid = e.get("todo")
        if not tid:
            continue
        out_hours[tid] = round(out_hours.get(tid, 0.0) + float(e.get("hours") or 0.0), 3)
    return out_hours


def _estimate_hours(value) -> float | None:
    """Parse an effort estimate into hours. Supports ``8h`` / ``90m`` / ``2d`` / ``2`` (hours).

    Returns None when the value is not a valid estimate so callers can reject it
    instead of silently treating garbage as 0h.
    """
    if value in (None, ""):
        return None
    s = str(value).strip()
    m = ESTIMATE_RE.match(s)
    if not m:
        return None
    num = float(m.group(1))
    unit = (m.group(2) or "h").lower()
    if unit == "h":
        return round(num, 2)
    if unit == "m":
        return round(num / 60.0, 2)
    return round(num * 8.0, 2)  # d = 天，按 8h/天折算


def _todo_stats(tid: str, todo: dict, entries: list[dict], today: date_cls) -> dict:
    """One todo with goal-aware analysis: actual vs planned, overdue, roll-over."""
    t = _todo_public(tid, todo)
    actual = 0.0
    logged = 0
    mine: list[dict] = []
    for e in entries:
        if e.get("todo") == tid:
            actual += float(e.get("hours") or 0.0)
            logged += 1
            mine.append(e)
    t["actual_hours"] = round(actual, 2)
    t["logged_entries"] = logged
    t["last_activity"] = mine[-1]["date"] if mine else None
    t["last_progress"] = mine[-1]["content"] if mine else None
    t["last_progress_time"] = mine[-1]["time"] if mine else None
    t["recent_entries"] = [
        {"date": e["date"], "time": e["time"], "duration": e["duration"], "content": e["content"]}
        for e in mine[-3:]
    ]
    t["active_dates"] = sorted({e["date"] for e in mine})[-3:] if mine else []
    est = (todo or {}).get("estimate")
    t["planned_hours"] = _estimate_hours(est)
    t["estimate"] = est or None
    if t.get("planned_hours") is not None:
        gap = round(t["planned_hours"] - t["actual_hours"], 2)
        t["hours_gap"] = gap
        t["hours_gap_note"] = ("还差 %.1fh" % gap) if gap > 0 else ("超了 %.1fh" % -gap)
    else:
        t["hours_gap"] = None
        t["hours_gap_note"] = ""
    due = (todo or {}).get("due")
    t["overdue_days"] = 0
    if due and t.get("status") == "pending":
        try:
            d = parse_date(str(due))
            t["overdue_days"] = max(0, (today - d).days)
        except ValueError:
            pass
    created = (todo or {}).get("created")
    t["age_days"] = 0
    if created:
        try:
            t["age_days"] = max(0, (today - parse_date(str(created))).days)
        except ValueError:
            pass
    t["rolled_over"] = t["overdue_days"] > 0
    return t


def cmd_todo(args) -> int:
    """Manage the todo list that drives the morning reminder."""
    root = Path(args.data_dir).expanduser() if args.data_dir else default_data_dir()
    todos = load_todos(root)
    entries = read_entries(root)
    action = args.action
    todo_id = args.id or getattr(args, "id_pos", None)
    today = now_local().date()

    if action == "add":
        content = (args.content or "").strip()
        if not content:
            die("todo add 需要 --content \"要做的事\"", 2)
        tid = todo_id or _next_todo_id(todos)
        if tid in todos:
            die(f"todo id {tid} 已存在", 2)
        todo = {
            "content": content,
            "status": "pending",
            "created": today.isoformat(),
        }
        if args.goal:
            todo["goal"] = args.goal.strip()
        if args.project:
            registry = load_registry(root)
            if registry and args.project not in registry:
                die(
                    f"项目 {args.project!r} 未在 projects.yaml 登记。"
                    f"先登记：lifebrain.py project add {args.project} --category work|life --goal \"一句话目标\"",
                    3,
                )
            todo["project"] = args.project
        if args.estimate:
            if _estimate_hours(args.estimate) is None:
                die(f"--estimate 格式应为 8h / 2d / 4，收到 {args.estimate!r}", 2)
            todo["estimate"] = args.estimate
        if args.due:
            todo["due"] = parse_date(args.due).isoformat()
        todos[tid] = todo
        save_todos(root, todos)
        out({"ok": True, "action": "add", "id": tid, "todo": _todo_public(tid, todo)})
        return 0

    if action == "list":
        want = args.status or "pending"
        items = []
        for tid, todo in todos.items():
            t = _todo_stats(tid, todo, entries, today)
            if want != "all" and t.get("status") != want:
                continue
            if args.project and t.get("project") != args.project:
                continue
            items.append(t)
        items.sort(key=lambda t: (t.get("status") != "pending",
                                  -(t.get("overdue_days") or 0),
                                  t.get("due") or "9999",
                                  t.get("created") or ""))
        out({
            "todos": items,
            "count": len(items),
            "pending_count": sum(1 for t in todos.values() if (t or {}).get("status", "pending") == "pending"),
            "done_count": sum(1 for t in todos.values() if (t or {}).get("status") == "done"),
            "file": str(todos_path(root)),
        })
        return 0

    if action == "done":
        tid = todo_id
        if tid not in todos:
            die(f"没有这个 todo：{tid}", 2)
        todos[tid]["status"] = "done"
        todos[tid]["done_at"] = today.isoformat()
        if args.note:
            todos[tid]["note"] = args.note
        save_todos(root, todos)
        stat = _todo_stats(tid, todos[tid], entries, today)
        out({"ok": True, "action": "done", "id": tid, "todo": stat})
        return 0

    if action == "update":
        tid = todo_id
        if not tid or tid not in todos:
            die(f"没有这个 todo：{tid}", 2)
        todo = dict(todos[tid] or {})
        if args.content:
            todo["content"] = args.content.strip()
        if args.goal is not None:
            todo["goal"] = args.goal.strip()
        if args.project:
            todo["project"] = args.project
        if args.estimate is not None:
            if _estimate_hours(args.estimate) is None:
                die(f"--estimate 格式应为 8h / 2d / 4，收到 {args.estimate!r}", 2)
            todo["estimate"] = args.estimate
        if args.due:
            todo["due"] = parse_date(args.due).isoformat()
        if args.note is not None:
            todo["note"] = args.note
        todos[tid] = todo
        save_todos(root, todos)
        out({"ok": True, "action": "update", "id": tid,
             "todo": _todo_stats(tid, todo, entries, today)})
        return 0

    if action == "rm":
        tid = todo_id
        if tid not in todos:
            die(f"没有这个 todo：{tid}", 2)
        removed = todos.pop(tid)
        save_todos(root, todos)
        out({"ok": True, "action": "rm", "id": tid, "removed": _todo_public(tid, removed)})
        return 0

    if action == "review":
        days = max(1, int(args.days or 7))
        since = today - timedelta(days=days - 1)
        stats = [_todo_stats(tid, todo, entries, today) for tid, todo in todos.items()]
        pending = [t for t in stats if t.get("status") == "pending"]
        done = [t for t in stats if t.get("status") == "done"]
        pending.sort(key=lambda t: (-(t.get("overdue_days") or 0),
                                     t.get("due") or "9999",
                                     t.get("created") or ""))
        done.sort(key=lambda t: (t.get("done_at") or "", t.get("created") or ""), reverse=True)

        planned = sum(t["planned_hours"] for t in pending if t.get("planned_hours"))
        actual = sum(t["actual_hours"] for t in pending)
        by_project: dict[str, dict] = {}
        for t in pending:
            key = t.get("project") or "（未归类）"
            slot = by_project.setdefault(key, {"pending": 0, "planned_hours": 0.0, "actual_hours": 0.0, "todos": []})
            slot["pending"] += 1
            slot["planned_hours"] = round(slot["planned_hours"] + (t.get("planned_hours") or 0.0), 2)
            slot["actual_hours"] = round(slot["actual_hours"] + (t.get("actual_hours") or 0.0), 2)
            slot["todos"].append(t["id"])

        recent_entries = [e for e in entries if e["date"] >= since.isoformat()]
        recent_hours = round(sum(float(e.get("hours") or 0.0) for e in recent_entries), 2)

        notes: list[str] = []
        overdue = [t for t in pending if t.get("overdue_days")]
        if overdue:
            worst = overdue[0]
            notes.append(f"{worst['id']}「{worst['content']}」已逾期 {worst['overdue_days']} 天"
                         f"（建于 {worst.get('created')}）")
        stale = [t for t in pending if (t.get("age_days") or 0) >= 3 and not t.get("logged_entries")]
        if stale:
            notes.append(f"{len(stale)} 条待办建了 3 天以上还没动过："
                         + "、".join(t["id"] for t in stale[:3]))
        if planned and actual < planned * 0.5:
            notes.append(f"待办预计共 {planned:.1f}h，实际只投入 {actual:.1f}h")
        done_today = [t for t in done if t.get("done_at") == today.isoformat()]
        if done_today:
            notes.append("今天已完成：" + "、".join(t["id"] for t in done_today[:3]))

        out({
            "scope": {"days": days, "from": since.isoformat(), "to": today.isoformat()},
            "today": today.isoformat(),
            "summary": {
                "total": len(stats),
                "pending": len(pending),
                "done": len(done),
                "overdue": len(overdue),
                "completion_rate": round(len(done) / len(stats), 3) if stats else 0.0,
                "planned_hours": round(planned, 2),
                "actual_hours": round(actual, 2),
                "recent_logged_hours": recent_hours,
                "recent_entries": len(recent_entries),
            },
            "today_focus": [t["id"] for t in pending[:3]],
            "focus_detail": pending[:3],
            "pending": pending,
            "recent_done": done[:5],
            "by_project": by_project,
            "notes": notes,
        })
        return 0

    die(f"未知 todo 动作：{action}", 2)
    return 2


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
    info = scope_status()
    mismatch = scope_mismatch()
    if mismatch:
        # 不致命，但必须让人看见：跑的是别的 agent 的脚本副本
        print(
            f"[lifebrain] ⚠ scope 不一致：当前进程属于 {mismatch[1]}，"
            f"这份脚本属于 {mismatch[0]}；数据目录已按 {mismatch[1]} 解析。",
            file=sys.stderr,
        )
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
        "agent_id": effective_scope() or "JVZG0K",
        "skill_dir": str(Path(__file__).resolve().parent.parent),
        "scope": info,
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
    # 认领 legacy 家目录：~/lifebrain 归第一个用它的 scope 所有，
    # 其他 scope 各自用 ~/.lifebrain/scopes/<scope>，绝不共享数据。
    scope = effective_scope()
    try:
        is_legacy_cfg = cfg_file.resolve() == (legacy_home() / "config.yaml").resolve()
    except OSError:  # pragma: no cover - 防御性
        is_legacy_cfg = False
    if is_legacy_cfg and scope and not old_cfg.get("owner_scope"):
        cfg_body["owner_scope"] = scope
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

    tp = todos_path(target)
    if not tp.exists():
        tp.write_text("todos:\n", encoding="utf-8")
        created.append(str(tp))

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
        "scope": scope_status(),
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
    todos = load_todos(root)
    all_entries = read_entries(root)
    pending = [
        _todo_stats(tid, t, all_entries, today) for tid, t in todos.items()
        if (t or {}).get("status", "pending") == "pending"
    ]
    pending.sort(key=lambda t: (-(t.get("overdue_days") or 0),
                                t.get("due") or "9999",
                                t.get("created") or ""))
    out({
        "data_dir": str(root),
        "data_dir_source": _source_for(args),
        "scope": scope_status(),
        "initialized": _initialized(root),
        "date": target.isoformat(),
        "today": today.isoformat(),
        "week": iso_week(target),
        "week_file": str(journal_path(root, target)),
        "week_file_exists": journal_path(root, target).exists(),
        "recorded": date_has_entries(root, target),
        "entries": [e for e in all_entries if e["date"] == target.isoformat()],
        "missing_dates": missing,
        "lookback": lookback,
        "projects": registry,
        "pending_todos": pending,
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
                    registry: dict, todo_id: str = "") -> tuple[str, str, str, str, str, str]:
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
    if todo_id and not TODO_REF.match(todo_id):
        die(f"todo 引用应为 t<数字>，收到 {todo_id!r}", 2)
    return time_s, project, type_s, duration, content.strip(), todo_id


def cmd_append(args) -> int:
    root = Path(args.data_dir).expanduser() if args.data_dir else default_data_dir()
    target = parse_date(args.date) if args.date else now_local().date()
    registry = load_registry(root)
    todos = load_todos(root)

    raw: list[tuple[str, str, str, str, str, str]] = []
    if args.entry:
        for item in args.entry:
            parts = item.split("|")
            if len(parts) == 5:
                raw.append((*(p.strip() for p in parts), ""))  # type: ignore[arg-type]
            elif len(parts) == 6:
                todo_ref = parts[5].strip()
                if not TODO_REF.match(todo_ref):
                    die(f"--entry 第 6 段必须是 t<数字> 的 todo 引用，收到 {todo_ref!r}", 2)
                raw.append((*(p.strip() for p in parts[:5]), todo_ref))
            else:
                die(f"--entry 必须是 5 段 'HH:MM|项目|类型|时长|内容'（可选第 6 段 todo 引用），收到 {item!r}", 2)
    else:
        if not (args.project and args.duration and args.content):
            die("append 需要 --entry 重复参数，或 --project/--duration/--content 三件套（--type 可选）", 2)
        time_s = args.time or now_local().strftime("%H:%M")
        type_s = args.type or "记录"
        raw.append((time_s, args.project, type_s, args.duration, args.content, (args.todo or "").strip()))

    # 注意：不能写成 _validate_entry(*r, registry) —— CPython 会把 registry
    # 绑定到下一个位置参数（todo_id），导致项目校验拿着 todo id 当注册表。
    entries = [_validate_entry(r[0], r[1], r[2], r[3], r[4], registry, r[5]) for r in raw]

    for (_t, _p, _ty, _d, _c, tid) in entries:
        if tid and tid not in todos:
            die(f"todo {tid} 不存在；先建：lifebrain.py todo add --content \"…\"", 3)

    path = journal_path(root, target)
    path.parent.mkdir(parents=True, exist_ok=True)
    week = iso_week(target)
    if path.exists():
        text = path.read_text(encoding="utf-8")
    else:
        text = week_header(week) + "\n"
    preamble, sections = parse_sections(text)

    key = target.isoformat()
    entry_lines = []
    for (t, p, ty, d, c, tid) in entries:
        line = f"- {t} | {p} | {ty} | {d} | {c}"
        if tid:
            line += f" | {tid}"
        entry_lines.append(line)

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
              "entries": [{"time": t, "project": p, "type": ty, "duration": d, "content": c,
                           "todo": tid or None}
                          for (t, p, ty, d, c, tid) in entries]}
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
                             "duration": e["duration"], "content": e["content"],
                             "todo": e.get("todo")})
    for p in by_project.values():
        p["hours"] = round(p["hours"], 1)

    by_todo: dict[str, dict] = {}
    for e in sel:
        tid = e.get("todo")
        if not tid:
            continue
        slot = by_todo.setdefault(tid, {"hours": 0.0, "count": 0, "entries": [],
                                        "project": None, "content": None,
                                        "goal": None, "last_date": None, "last_progress": None})
        slot["hours"] = round(slot["hours"] + e["hours"], 3)
        slot["count"] += 1
        slot["entries"].append({"date": e["date"], "time": e["time"], "type": e["type"],
                                 "duration": e["duration"], "content": e["content"]})
        slot["project"] = e["project"]
        slot["last_date"] = e["date"]
        slot["last_progress"] = e["content"]

    todos = load_todos(root)
    today = now_local().date()
    for tid, slot in by_todo.items():
        todo = todos.get(tid) or {}
        slot["content"] = todo.get("content")
        slot["goal"] = todo.get("goal")
        slot["status"] = todo.get("status", "pending")
        slot["due"] = todo.get("due")
        slot["overdue_days"] = 0
        if slot["due"] and slot["status"] == "pending":
            try:
                slot["overdue_days"] = max(0, (today - parse_date(str(slot["due"]))).days)
            except ValueError:
                pass
        slot["hours"] = round(slot["hours"], 1)

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
        "by_todo": by_todo,
        "todo_hours": round(sum(t["hours"] for t in by_todo.values()), 1),
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
    """写/清「今天已提醒」标记。

    标记语义：**投递成功才算数**。
    agent 侧写标记只是为了防止同一天重复打扰；真正的兜底脚本会在发现
    cron 上次投递失败时用 ``--clear`` 清掉它并重试。
    """
    root = Path(args.data_dir).expanduser() if args.data_dir else default_data_dir()
    target = parse_date(args.date) if args.date else now_local().date()
    marker = root / f".reminder-{target.isoformat()}"
    if getattr(args, "clear", False):
        existed = marker.exists()
        if existed:
            marker.unlink()
        out({"ok": True, "cleared": existed, "marker": str(marker), "date": target.isoformat()})
        return 0
    root.mkdir(parents=True, exist_ok=True)
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
    p.add_argument("--todo", default=None, help="关联目标 id（t1）；该条即视为此目标的进展记录")
    p.add_argument("--entry", action="append", default=None,
                   help="重复使用：'HH:MM|项目|类型|时长|内容[|t1]'")
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

    p = sub.add_parser("todo", help="目标清单（早间提醒用）")
    p.add_argument("action", choices=["add", "list", "done", "rm", "update", "review"])
    p.add_argument("id_pos", nargs="?", default=None, help="待办 id（done/rm/update 时必填）")
    p.add_argument("--content", default=None, help="待办内容（add 时必填）")
    p.add_argument("--id", dest="id", default=None, help="待办 id")
    p.add_argument("--project", default=None, help="关联项目")
    p.add_argument("--goal", default=None, help="目标定义：达成后是什么样子")
    p.add_argument("--estimate", default=None, help="预计投入时长，如 8h / 2d")
    p.add_argument("--due", default=None, help="期望完成日期 YYYY-MM-DD")
    p.add_argument("--note", default=None, help="完成/更新备注")
    p.add_argument("--status", default=None, choices=["pending", "done", "all"],
                   help="list 时筛选，默认 pending")
    p.add_argument("--days", type=int, default=7, help="review 时回看天数，默认 7")
    p.set_defaults(func=cmd_todo)

    p = sub.add_parser("mark-reminder", help="写下今日提醒已触发的标记（防重复）；--clear 可清掉")
    p.add_argument("--date", default=None)
    p.add_argument("--clear", action="store_true", help="删除标记（投递失败时用来允许重试）")
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
