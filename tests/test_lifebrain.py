#!/usr/bin/env python3
"""lifebrain 回归测试：目标进展模型（todo + 周文件引用）。

跑法：
    python3 lifebrainskill/tests/test_lifebrain.py

全程只碰临时目录，不动用户真实数据。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "lifebrain" / "scripts"

PASS = 0
FAIL = 0
FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        FAILURES.append(f"{name} :: {detail}")
        print(f"  FAIL {name} :: {detail}")


class LB:
    """在指定数据目录里跑 lifebrain.py。"""

    def __init__(self, root: Path, no_yaml: bool = False):
        self.root = root
        self.no_yaml = no_yaml

    def env(self) -> dict:
        e = dict(os.environ)
        e["LB_DATA_DIR"] = str(self.root)
        e["LB_TIMEZONE"] = "Asia/Shanghai"
        e["PYTHONPATH"] = str(self.root / "_shim") if self.no_yaml else e.get("PYTHONPATH", "")
        return e

    def run(self, *args: str) -> tuple[int, dict | str, str]:
        p = subprocess.run(
            [sys.executable, str(SCRIPTS / "lifebrain.py"), *args],
            capture_output=True, text=True, env=self.env(),
        )
        if p.returncode == 0:
            try:
                return p.returncode, json.loads(p.stdout), p.stderr
            except json.JSONDecodeError:
                return p.returncode, p.stdout, p.stderr
        return p.returncode, "", p.stderr

    def raw(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "lifebrain.py"), *args],
            capture_output=True, text=True, env=self.env(),
        )


def make_root(no_yaml: bool = False) -> LB:
    root = Path(tempfile.mkdtemp(prefix="lbregress-"))
    (root / "journals").mkdir()
    (root / "projects").mkdir()
    (root / "reports").mkdir()
    (root / "projects.yaml").write_text("projects: {}\n", encoding="utf-8")
    (root / "todos.yaml").write_text("todos:\n", encoding="utf-8")
    if no_yaml:
        shim = root / "_shim"
        shim.mkdir()
        (shim / "yaml.py").write_text("raise ImportError('no PyYAML')\n", encoding="utf-8")
    return LB(root, no_yaml=no_yaml)


def test_parse_backcompat():
    print("\n[1] 解析：旧 5 字段必须完全不变")
    sys.path.insert(0, str(SCRIPTS))
    from rebuild_index import split_entry_line  # noqa: PLC0415

    old = "- 08:00 | 永定大排 | 测线 | 8h | 完成测线第3段"
    got = split_entry_line(old)
    check("旧格式 5 段解析", got == ("08:00", "永定大排", "测线", "8h", "完成测线第3段", ""), str(got))

    new = "- 09:30 | 永定大排 | 测线 | 2h | 数据采集 | t1"
    got = split_entry_line(new)
    check("新格式 6 段解析", got == ("09:30", "永定大排", "测线", "2h", "数据采集", "t1"), str(got))

    tricky = "- 10:00 | P | T | 2h | 内容里有 t3 字样"
    got = split_entry_line(tricky)
    check("内容里的 t3 不误判", got[4] == "内容里有 t3 字样" and got[5] == "", str(got))

    weird = "- 11:00 | P | T | 1h | 内容 | T1"
    got = split_entry_line(weird)
    check("大写 T1 不算引用", got[5] == "", str(got))


def test_append_and_aggregate():
    print("\n[2] 写入 + 目标聚合")
    lb = make_root()
    try:
        lb.run("project", "add", "永定大排", "--category", "work", "--goal", "完成测线")
        rc, out, err = lb.run("todo", "add", "--content", "高密度电法测线",
                              "--project", "永定大排", "--goal", "完成测线并出剖面报告",
                              "--estimate", "16h", "--due", "2026-11-15")
        check("todo add", rc == 0 and out["id"] == "t1", f"rc={rc} err={err}")

        rc, out, err = lb.run("append", "--date", "2026-10-21", "--time", "08:00",
                              "--project", "永定大排", "--type", "测线", "--duration", "8h",
                              "--content", "完成测线第3段")
        check("append 普通条目", rc == 0 and out["entries"][0]["todo"] is None, err)

        rc, out, err = lb.run("append", "--date", "2026-10-21", "--time", "09:30",
                              "--project", "永定大排", "--type", "测线", "--duration", "2h",
                              "--content", "数据采集与整理", "--todo", "t1")
        check("append --todo 关联目标", rc == 0 and out["entries"][0]["todo"] == "t1", err)

        rc, out, err = lb.run("append", "--date", "2026-10-22",
                              "--entry", "09:00|永定大排|测线|1.5h|解释剖面|t1",
                              "--entry", "10:00|永定大排|测线|0.5h|整理图件")
        check("--entry 6 段与 5 段混写", rc == 0 and len(out["entries"]) == 2, err)

        rc, out, err = lb.run("append", "--date", "2026-10-22", "--time", "11:00",
                              "--project", "永定大排", "--type", "测线", "--duration", "1h",
                              "--content", "x", "--todo", "t99")
        check("不存在的 todo 被拒绝", rc != 0 and "t99" in err, f"rc={rc} err={err}")

        week = (lb.root / "journals" / "2026-W43.md").read_text(encoding="utf-8")
        check("周文件写入 6 段", "| 数据采集与整理 | t1" in week, week)
        check("周文件保留 5 段", "- 10:00 | 永定大排 | 测线 | 0.5h | 整理图件" in week, week)

        rc, out, _ = lb.run("todo", "list")
        t = out["todos"][0]
        check("累计投入只算挂目标的部分", t["actual_hours"] == 3.5, str(t["actual_hours"]))
        check("记录条数", t["logged_entries"] == 2, str(t["logged_entries"]))
        check("最近进展", t["last_progress"] == "解释剖面", str(t["last_progress"]))
        check("最近活动日期", t["last_activity"] == "2026-10-22", str(t["last_activity"]))
        check("预计投入解析", t["planned_hours"] == 16.0, str(t["planned_hours"]))
        check("还差多少", t["hours_gap_note"] == "还差 12.5h", str(t["hours_gap_note"]))

        rc, out, _ = lb.run("report", "--week", "2026-W43")
        check("report 按目标聚合", out["by_todo"]["t1"]["hours"] == 3.5, str(out["by_todo"]))
        check("report 项目总时长", out["total_hours"] == 12.0, str(out["total_hours"]))

        rc, out, _ = lb.run("status")
        check("status 带目标进展", out["pending_todos"][0]["last_progress"] == "解释剖面",
              str(out["pending_todos"][0]))

        rc, out, _ = lb.run("todo", "review", "--days", "7")
        check("review 有 today_focus", out["today_focus"] == ["t1"], str(out["today_focus"]))
        check("review 有 notes", isinstance(out["notes"], list), str(out["notes"]))

        rc, out, err = lb.run("rebuild")
        check("rebuild 无 dropped", rc == 0 and out["dropped"] == [], f"rc={rc} {out}")
    finally:
        shutil.rmtree(lb.root, ignore_errors=True)


def test_todo_lifecycle():
    print("\n[3] 目标生命周期")
    lb = make_root()
    try:
        lb.run("project", "add", "P1", "--category", "work", "--goal", "g")
        lb.run("todo", "add", "--content", "目标一", "--project", "P1", "--estimate", "4")
        lb.run("todo", "add", "--content", "目标二", "--project", "P1", "--estimate", "2d")

        rc, out, err = lb.run("todo", "add", "--content", "坏估时", "--estimate", "abc")
        check("非法 estimate 被拒绝", rc != 0, f"rc={rc} {err}")
        rc, out, err = lb.run("todo", "add", "--content", "未登记项目", "--project", "不存在")
        check("未登记项目被拒绝", rc != 0, f"rc={rc} {err}")

        rc, out, _ = lb.run("todo", "list", "--status", "all")
        est = {t["id"]: t["planned_hours"] for t in out["todos"]}
        check("estimate 4 → 4h", est["t1"] == 4.0, str(est))
        check("estimate 2d → 16h", est["t2"] == 16.0, str(est))

        rc, out, _ = lb.run("todo", "update", "t1", "--estimate", "8h", "--note", "范围扩大")
        check("todo update 改预计", out["todo"]["planned_hours"] == 8.0, str(out["todo"]))

        rc, out, _ = lb.run("todo", "done", "t1", "--note", "搞定")
        check("todo done 标完成", out["todo"]["status"] == "done" and out["todo"]["done_at"], str(out["todo"]))

        rc, out, _ = lb.run("todo", "list", "--status", "pending")
        check("done 后不在 pending", [t["id"] for t in out["todos"]] == ["t2"], str(out["todos"]))

        rc, out, _ = lb.run("todo", "rm", "t2")
        check("todo rm", out["removed"]["id"] == "t2", str(out))
    finally:
        shutil.rmtree(lb.root, ignore_errors=True)


def test_progress_does_not_autocomplete():
    print("\n[4] 记了进展 ≠ 目标完成")
    lb = make_root()
    try:
        lb.run("project", "add", "P1", "--category", "work", "--goal", "g")
        lb.run("todo", "add", "--content", "目标", "--project", "P1")
        lb.run("append", "--date", "2026-10-21", "--time", "09:00", "--project", "P1",
               "--type", "开发", "--duration", "2h", "--content", "推进一步", "--todo", "t1")
        rc, out, _ = lb.run("todo", "list")
        check("挂进展后仍是 pending", out["todos"][0]["status"] == "pending", str(out["todos"][0]))
        check("但投入已累计", out["todos"][0]["actual_hours"] == 2.0, str(out["todos"][0]))
    finally:
        shutil.rmtree(lb.root, ignore_errors=True)


def test_no_pyyaml():
    print("\n[5] 无 PyYAML 环境")
    lb = make_root(no_yaml=True)
    try:
        lb.run("project", "add", "P1", "--category", "work", "--goal", "测试目标")
        lb.run("todo", "add", "--content", "带冒号的目标: 内容", "--goal", "目标定义",
               "--project", "P1", "--estimate", "8h", "--due", "2026-11-01")
        rc, out, err = lb.run("append", "--date", "2026-10-21", "--time", "09:00",
                              "--project", "P1", "--type", "开发", "--duration", "2h",
                              "--content", "做了点事: 带冒号", "--todo", "t1")
        check("无 yaml 也能 append --todo", rc == 0, f"rc={rc} {err}")
        rc, out, _ = lb.run("todo", "list")
        t = out["todos"][0]
        check("无 yaml 读回目标", t["content"] == "带冒号的目标: 内容", str(t["content"]))
        check("无 yaml 读回进展", t["last_progress"] == "做了点事: 带冒号", str(t["last_progress"]))
        check("无 yaml 读回估时", t["planned_hours"] == 8.0, str(t["planned_hours"]))
        rc, out, _ = lb.run("rebuild")
        check("无 yaml rebuild 无 dropped", rc == 0 and out["dropped"] == [], str(out))
    finally:
        shutil.rmtree(lb.root, ignore_errors=True)


def main() -> int:
    test_parse_backcompat()
    test_append_and_aggregate()
    test_todo_lifecycle()
    test_progress_does_not_autocomplete()
    test_no_pyyaml()
    print(f"\n{'=' * 46}\n通过 {PASS} / 失败 {FAIL}")
    for f in FAILURES:
        print("  ✗", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
