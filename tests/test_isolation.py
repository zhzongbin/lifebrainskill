#!/usr/bin/env python3
"""lifebrain 多用户隔离回归测试。

一台 Octop 主机上可能同时跑着多个用户（每个用户一个 agent），但它们共用同一个
Unix 账号、同一个 ``$HOME``。这些测试锁死三件事：

1. 不同 agent 的数据目录**不重叠**（认领者用 legacy，其余用 ``~/.lifebrain/scopes/<scope>``）；
2. 跑错脚本副本（scope 不一致）时，**数据仍然按当前 agent 解析**，不会读到别人的记录；
3. 兜底/提醒的 marker 语义正确（投递失败时可以清掉重试）。

跑法：
    python3 lifebrainskill/tests/test_isolation.py

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
INSTALLER = SCRIPTS / "install_octop.sh"

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


def run(script: Path, args: list[str], env: dict, cwd: str | None = None):
    p = subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True, text=True, env=env, cwd=cwd,
    )
    return p


def run_json(script: Path, args: list[str], env: dict, cwd: str | None = None):
    p = run(script, args, env, cwd)
    if p.returncode != 0:
        return p.returncode, {}, p.stderr
    try:
        return p.returncode, json.loads(p.stdout), p.stderr
    except json.JSONDecodeError:
        return p.returncode, {}, p.stdout + p.stderr


def base_env(home: Path, agent: str | None = None, **extra) -> dict:
    e = dict(os.environ)
    e["HOME"] = str(home)
    e["LB_TIMEZONE"] = "Asia/Shanghai"
    e.pop("LB_DATA_DIR", None)
    e.pop("LIFEBRAIN_HOME", None)
    e.pop("LIFEBRAIN_SCOPE", None)
    e.pop("LIFEBRAIN_AGENT", None)
    e.pop("OCTOP_AGENT_ID", None)
    if agent:
        e["OCTOP_AGENT_ID"] = agent
    e.update(extra)
    return e


def test_scope_dirs_dont_overlap():
    print("\n[1] 数据目录按 scope 隔离")
    home = Path(tempfile.mkdtemp(prefix="lbscope-"))
    try:
        env_a = base_env(home, "AAAA1111")
        env_b = base_env(home, "BBBB2222")

        # 全新机器：第一个用的 agent 认领 legacy 家目录
        rc, out, err = run_json(SCRIPTS / "lifebrain.py", ["init"], env_a)
        check("A init 成功", rc == 0, f"rc={rc} err={err}")
        cfg = (home / "lifebrain" / "config.yaml")
        cfg_text = cfg.read_text(encoding="utf-8") if cfg.exists() else ""
        check("legacy config 写上 owner_scope",
              'owner_scope: "AAAA1111"' in cfg_text or "owner_scope: AAAA1111" in cfg_text,
              cfg_text or "(缺 config)")
        check("A 的数据目录是 legacy", out.get("data_dir") == str(home / "lifebrain"), str(out.get("data_dir")))

        # B 必须被分到自己的目录
        rc, out, err = run_json(SCRIPTS / "lifebrain.py", ["config"], env_b)
        check("B 的数据目录 ≠ legacy", out.get("data_dir") != str(home / "lifebrain"), str(out.get("data_dir")))
        check("B 的数据目录在 scopes 下",
              out.get("data_dir") == str(home / ".lifebrain" / "scopes" / "BBBB2222"), str(out.get("data_dir")))
        check("B 认不出 legacy 主人", out["scope"]["legacy_owner"] == "AAAA1111", json.dumps(out["scope"], ensure_ascii=False))
        check("B 的 config 文件独立", out.get("config_file") == str(home / ".lifebrain" / "scopes" / "BBBB2222" / "config.yaml"),
              str(out.get("config_file")))

        # A 再看一次，还是 legacy（认领稳定）
        rc, out, err = run_json(SCRIPTS / "lifebrain.py", ["config"], env_a)
        check("A 仍然用 legacy", out.get("data_dir") == str(home / "lifebrain"), str(out.get("data_dir")))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_no_cross_read():
    print("\n[2] 一个用户看不到另一个用户的数据")
    home = Path(tempfile.mkdtemp(prefix="lbscope-"))
    try:
        env_a = base_env(home, "AAAA1111")
        env_b = base_env(home, "BBBB2222")
        run_json(SCRIPTS / "lifebrain.py", ["init"], env_a)
        run_json(SCRIPTS / "lifebrain.py", ["init"], env_b)

        run_json(SCRIPTS / "lifebrain.py", ["project", "add", "A的项目", "--category", "work"], env_a)
        rc, out, err = run_json(SCRIPTS / "lifebrain.py",
                                 ["todo", "add", "--content", "A的机密目标", "--project", "A的项目"], env_a)
        check("A 建目标成功", rc == 0, f"rc={rc} err={err}")
        run_json(SCRIPTS / "lifebrain.py", ["append", "--date", "2026-10-21", "--time", "08:00",
                                           "--project", "A的项目", "--type", "干活", "--duration", "3h",
                                           "--content", "A 的私密记录"], env_a)

        rc, out, err = run_json(SCRIPTS / "lifebrain.py", ["todo", "list", "--status", "all"], env_b)
        blob = json.dumps(out, ensure_ascii=False)
        check("B 看不到 A 的目标", "A的机密目标" not in blob, blob[:200])
        rc, out, err = run_json(SCRIPTS / "lifebrain.py", ["status", "--lookback", "30"], env_b)
        blob = json.dumps(out, ensure_ascii=False)
        check("B 看不到 A 的条目", "A 的私密记录" not in blob, blob[:200])
        check("B 看不到 A 的项目", "A的项目" not in blob, blob[:200])

        # 反向：A 也看不到 B 的
        run_json(SCRIPTS / "lifebrain.py", ["project", "add", "B的项目", "--category", "work"], env_b)
        run_json(SCRIPTS / "lifebrain.py", ["todo", "add", "--content", "B的目标", "--project", "B的项目"], env_b)
        rc, out, err = run_json(SCRIPTS / "lifebrain.py", ["todo", "list", "--status", "all"], env_a)
        blob = json.dumps(out, ensure_ascii=False)
        check("A 看不到 B 的目标", "B的目标" not in blob, blob[:200])
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_wrong_script_copy_still_isolated():
    print("\n[3] 跑错脚本副本（scope 不一致）也不能串数据")
    home = Path(tempfile.mkdtemp(prefix="lbscope-"))
    try:
        env_a = base_env(home, "AAAA1111")
        env_b = base_env(home, "BBBB2222")
        run_json(SCRIPTS / "lifebrain.py", ["init"], env_a)
        run_json(SCRIPTS / "lifebrain.py", ["init"], env_b)

        # 假装 A 的 agent 目录里装了一份 skill（带 scope.json）
        skill_a = home / ".octop" / "agents" / "AAAA1111" / ".octop" / "skills" / "lifebrain"
        (skill_a / "scripts").mkdir(parents=True)
        for f in ("lifebrain.py", "rebuild_index.py", "scope.py"):
            shutil.copy2(SCRIPTS / f, skill_a / "scripts" / f)
        (skill_a / "scope.json").write_text(
            json.dumps({"scope": "AAAA1111", "home": str(home / "lifebrain")}), encoding="utf-8")

        # B 的进程里误用了 A 的脚本副本
        p = run(skill_a / "scripts" / "lifebrain.py", ["config"], env_b)
        check("B 用 A 的脚本仍解析到 B 的数据目录",
              str(home / ".lifebrain" / "scopes" / "BBBB2222") in p.stdout,
              p.stdout[:300] + p.stderr[:300])
        check("stderr 有 scope 不一致告警", "scope 不一致" in p.stderr, p.stderr[:300])
        out = json.loads(p.stdout)
        check("config.scope 标出 mismatch", out["scope"]["scope_mismatch"] is True, json.dumps(out["scope"], ensure_ascii=False))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_marker_semantics():
    print("\n[4] 提醒 marker：可写、可清")
    home = Path(tempfile.mkdtemp(prefix="lbscope-"))
    try:
        env = base_env(home, "AAAA1111")
        run_json(SCRIPTS / "lifebrain.py", ["init"], env)
        rc, out, err = run_json(SCRIPTS / "lifebrain.py", ["mark-reminder"], env)
        check("mark-reminder 写 marker", rc == 0, f"rc={rc} err={err}")
        rc, out, err = run_json(SCRIPTS / "lifebrain.py", ["status"], env)
        check("status 报 fired", out.get("reminder_fired_today") is True, str(out.get("reminder_fired_today")))
        rc, out, err = run_json(SCRIPTS / "lifebrain.py", ["mark-reminder", "--clear"], env)
        check("mark-reminder --clear 清掉 marker", rc == 0 and out.get("cleared") is True, f"rc={rc} out={out}")
        rc, out, err = run_json(SCRIPTS / "lifebrain.py", ["status"], env)
        check("清完 fired=False", out.get("reminder_fired_today") is False, str(out.get("reminder_fired_today")))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_installer_picks_own_agent():
    print("\n[5] 安装脚本只认自己的 agent")
    home = Path(tempfile.mkdtemp(prefix="lbscope-"))
    try:
        # 把 skill 复制到一个**不含 agent id 的路径**（模拟从全局技能包安装）
        src = home / "pkg" / "lifebrain"
        shutil.copytree(REPO / "lifebrain", src)
        installer = src / "scripts" / "install_octop.sh"

        # 造两个 agent 目录：不给任何提示时，安装脚本必须**报错**而不是瞎猜
        for a in ("AAAA1111", "BBBB2222"):
            (home / ".octop" / "agents" / a / ".octop" / "skills").mkdir(parents=True)
        env = base_env(home, None)
        p = subprocess.run(["bash", str(installer)], capture_output=True, text=True, env=env)
        check("多 agent 且无提示 → 报错不瞎猜", p.returncode != 0 and "认不出目标 agent" in (p.stdout + p.stderr),
              f"rc={p.returncode} out={p.stdout[:200]} err={p.stderr[:200]}")

        # 给了 OCTOP_AGENT_ID → 只装到自己名下，并写 scope.json
        env = base_env(home, "BBBB2222")
        p = subprocess.run(["bash", str(installer)], capture_output=True, text=True, env=env)
        target = home / ".octop" / "agents" / "BBBB2222" / ".octop" / "skills" / "lifebrain"
        check("装到指定 agent 名下", p.returncode == 0 and target.exists(),
              f"rc={p.returncode} out={p.stdout[:300]} err={p.stderr[:300]}")
        scope_file = target / "scope.json"
        check("写了 scope.json", scope_file.exists(), str(scope_file))
        home_b = None
        if scope_file.exists():
            data = json.loads(scope_file.read_text(encoding="utf-8"))
            home_b = data.get("home")
            check("scope.json 里的 scope 是本 agent", data.get("scope") == "BBBB2222", str(data))

        # A 名下不该被装
        check("没往别的 agent 目录装",
              not (home / ".octop" / "agents" / "AAAA1111" / ".octop" / "skills" / "lifebrain").exists(),
              "AAAA1111 下出现了 lifebrain")

        # 再装 A：两个 scope 绝不能拿到同一个数据根
        env = base_env(home, "AAAA1111")
        p = subprocess.run(["bash", str(installer)], capture_output=True, text=True, env=env)
        scope_a_file = home / ".octop" / "agents" / "AAAA1111" / ".octop" / "skills" / "lifebrain" / "scope.json"
        check("第二个 agent 也装上了", p.returncode == 0 and scope_a_file.exists(),
              f"rc={p.returncode} out={p.stdout[:200]} err={p.stderr[:200]}")
        if scope_a_file.exists():
            home_a = json.loads(scope_a_file.read_text(encoding="utf-8")).get("home")
            check("两个 scope 的数据根不重叠", bool(home_a) and home_a != home_b,
                  f"A={home_a} B={home_b}")
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_prompt_templates_are_safe():
    """cron prompt 模板本身不能引入跨 agent 串用，且必须放得进 Octop 的 2000 字上限。"""
    home = Path(tempfile.mkdtemp(prefix="lb-prompt-"))
    try:
        env = base_env(home, "AAAA1111")
        for kind, label in (("evening", "晚间"), ("morning", "早间")):
            args = [str(INSTALLER), "--print-prompt"]
            if kind == "morning":
                args.append("morning")
            p = subprocess.run(["bash", *args], capture_output=True, text=True, env=env)
            check(f"{label}模板能打印出来", p.returncode == 0, p.stderr[:200])
            tpl = p.stdout.strip()
            check(f"{label}模板不超过 2000 字（Octop 硬上限）", len(tpl) <= 2000, f"实际 {len(tpl)}")
            check(f"{label}模板不含 agents/* 通配扫描",
                  "ls -d ~/.octop/agents/*" not in tpl and "agents/*/.octop" not in tpl, tpl[:200])
            check(f"{label}模板不含 find ~ 全盘扫描", "find ~ -path" not in tpl, tpl[:200])
            check(f"{label}模板校验 scope.runtime_scope", "scope.runtime_scope" in tpl, tpl[:200])
            check(f"{label}模板校验 scope.scope_mismatch", "scope.scope_mismatch" in tpl, tpl[:200])
            check(f"{label}模板找不到脚本就停", "exit 1" in tpl, tpl[:200])

        # SKILL.md 里给用户手抄的模板必须和 installer 的一致，避免两边漂移
        skill_md = (REPO / "lifebrain" / "SKILL.md").read_text(encoding="utf-8")
        for kind in ("evening", "morning"):
            args = [str(INSTALLER), "--print-prompt"] + (["morning"] if kind == "morning" else [])
            tpl = subprocess.run(["bash", *args], capture_output=True, text=True, env=env).stdout.strip()
            check(f"SKILL.md 里的{kind}模板和 installer 一致", tpl in skill_md, "SKILL.md 模板已漂移")
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_reminder_uses_live_api():
    """兜底脚本必须优先打主服务 live API，嵌入式 CLI 只能当主服务不在线时的退路。

    原因：`octop cron run-now` 会另起一套嵌入式 OctopServer。主服务在跑的时候，
    这等于开第二个 gateway 去轮询同一个微信 bot token —— 重复轮询、日志交错，
    cron 的执行环境也和真正服务用户的不是一套。
    """
    reminder = (SCRIPTS / "reminder.sh").read_text(encoding="utf-8")
    api = (SCRIPTS / "octop_api.py").read_text(encoding="utf-8")

    check("reminder.sh 存在 live API 助手", (SCRIPTS / "octop_api.py").exists())
    check("reminder.sh 调 octop_api.py run-now", "octop_api.py" in reminder and "run-now" in reminder)
    check("reminder.sh 调 octop_api.py health", "health" in reminder)

    # 嵌入式 CLI 必须排在健康检查之后
    health_at = reminder.find("octop_api.py")
    cli_at = reminder.find("cron run-now")
    check("嵌入式 CLI 在健康检查之后才可能出现", 0 <= health_at < cli_at,
          f"health@{health_at} cli@{cli_at}")
    check("主服务在线时不会再走嵌入式 CLI",
          'if "${API[@]}" health' in reminder and "exit 1" in reminder.split("退回嵌入式 CLI")[0][-400:],
          "在线分支应当直接退出，不落到 CLI")

    # live API 走 run-now 这个 HTTP 端点
    check("octop_api.py 用 live API run-now 端点",
          "/api/agents/{}/cron/{}/run-now" in api, api[-600:])
    check("octop_api.py 用 live API 读 cron 状态",
          "/api/agents/{}/cron/{}" in api, api[-600:])

    # 认证：本地签短期 token，只走 HTTP 头，不进 argv / 日志 / 仓库
    check("octop_api.py 不 import octop 包（纯标准库）",
          "import octop" not in api and "from octop" not in api, "helper 应当只依赖标准库")
    check("octop_api.py token 只放 Authorization 头",
          "Authorization" in api and "bearer" in api.lower(), "token 必须走请求头")
    check("octop_api.py 不把 token 打到 stdout",
          'print(token' not in api and 'print(token)' not in api, "token 不能进 stdout/日志")
    check("octop_api.py 用 HS256 签短期 token",
          "HS256" in api and "ttl_seconds: int = 300" in api, api[:400])


def test_octop_api_token_signing():
    """本地签出来的 access token 必须能被 Octop 的 HS256 校验通过。"""
    import base64
    import hashlib
    import hmac
    import importlib.util
    import json as _json

    spec = importlib.util.spec_from_file_location("lb_octop_api", SCRIPTS / "octop_api.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    secret = b"0123456789abcdef0123456789abcdef"
    token = mod.sign_access_token(secret, sub=1, uname="admin", role="admin", ttl_seconds=300)

    header, payload, signature = token.split(".")
    pad = lambda s: s + "=" * (-len(s) % 4)
    check("token 是三段 JWT", token.count(".") == 2, token[:40])
    check("header 是 HS256", _json.loads(base64.urlsafe_b64decode(pad(header)))["alg"] == "HS256", header)
    claims = _json.loads(base64.urlsafe_b64decode(pad(payload)))
    check("claims 带 sub/uname/role", claims["sub"] == "1" and claims["uname"] == "admin"
          and claims["role"] == "admin", str(claims))
    check("claims 有 exp 且是短期的", 0 < claims["exp"] - claims["iat"] <= 900, str(claims))

    expected = base64.urlsafe_b64encode(
        hmac.new(secret, f"{header}.{payload}".encode(), hashlib.sha256).digest()
    ).rstrip(b"=").decode()
    check("签名和 Octop 的 HS256 一致", hmac.compare_digest(expected, signature), signature[:20])


def main() -> int:
    test_scope_dirs_dont_overlap()
    test_no_cross_read()
    test_wrong_script_copy_still_isolated()
    test_marker_semantics()
    test_installer_picks_own_agent()
    test_prompt_templates_are_safe()
    test_reminder_uses_live_api()
    test_octop_api_token_signing()
    print("\n" + "=" * 46)
    print(f"通过 {PASS} / 失败 {FAIL}")
    for f in FAILURES:
        print("  - " + f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
