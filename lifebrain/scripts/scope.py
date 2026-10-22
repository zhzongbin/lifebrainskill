#!/usr/bin/env python3
"""lifebrain 的多用户隔离：scope 解析 + 数据根目录定位。

为什么需要这个模块
------------------
一台 Octop 主机上可以同时跑多个用户（一个用户一个 agent），但它们往往共享
同一个 Unix 账号、同一个 ``$HOME``。于是 ``~/lifebrain`` **不是**按用户隔离的：
A 用户的 agent 和 B 用户的 agent 会读写同一份 config.yaml / todos.yaml /
journals/，彼此的数据直接串。

所以 lifebrain 的「家目录」必须按 scope（agent / user）显式分流：

* ``$LIFEBRAIN_HOME``  —— 直接指定家目录（最强，优先于一切）
* scope 解析（先到先得）:
  1. ``$LIFEBRAIN_SCOPE``   显式指定
  2. ``$LIFEBRAIN_AGENT``   agent id
  3. ``$OCTOP_AGENT_ID``    Octop agent 运行时注入，每个 agent 一份
  4. 当前工作目录在 ``$OCTOP_HOME/agents/<id>/`` 下 → 取 ``<id>``
  5. 都没有 → ``None``（人手 / 系统 crontab 场景，改用 skill 自带的 scope.json）

家目录规则
----------
* legacy 家 ``~/lifebrain`` 归「认领者」所有——认领信息写在
  ``~/lifebrain/config.yaml`` 的 ``owner_scope`` 键里。
* 认领者继续用 ``~/lifebrain``（老用户零迁移）。
* 其他 scope 一律用 ``~/.lifebrain/scopes/<scope>``，彼此不重叠。

原则：**运行时 scope 永远优先于 skill 声明的 scope**。哪怕 agent 错误地跑了
别的 agent 的那份脚本，数据仍然落在「当前这个 agent 自己」的名下，不会串。
声明与运行时不一致时只告警，不致命。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

SCOPE_FILE_NAME = "scope.json"
LEGACY_DIR_NAME = "lifebrain"
SCOPES_ROOT_NAME = ".lifebrain"  # ~/.lifebrain/scopes/<scope>


# ------------------------------------------------------------------ 基础工具

def skill_root() -> Path:
    """本 skill 的根目录（scripts/ 的上一级）。"""
    return Path(__file__).resolve().parent.parent


def scope_file() -> Path:
    """安装时写下的 scope 声明文件。"""
    return skill_root() / SCOPE_FILE_NAME


def read_json(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _yaml_scalar(text: str, key: str) -> str | None:
    """从一个扁平 YAML 里取标量值（只处理顶层 ``key: value``）。"""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        k, _, v = line.partition(":")
        if k.strip() != key:
            continue
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        return v or None
    return None


# ------------------------------------------------------------------ scope 解析

def declared_scope() -> str | None:
    """安装时写进 scope.json 的 scope（可能为空）。"""
    value = read_json(scope_file()).get("scope")
    return str(value).strip() if value else None


def declared_home() -> Path | None:
    """安装时写进 scope.json 的家目录（可能为空）。"""
    value = read_json(scope_file()).get("home")
    return Path(str(value)).expanduser() if value else None


def runtime_scope() -> str | None:
    """当前进程代表哪个 agent / 用户；识别不到返回 None。"""
    for var in ("LIFEBRAIN_SCOPE", "LIFEBRAIN_AGENT", "OCTOP_AGENT_ID"):
        value = os.environ.get(var)
        if value and value.strip():
            return value.strip()
    # 工作目录推断：<OCTOP_HOME>/agents/<agent-id>/
    try:
        cwd = Path.cwd()
        root = Path(os.environ.get("OCTOP_HOME", str(Path.home() / ".octop")))
        agents = root / "agents"
        if cwd == agents or agents in cwd.parents:
            rel = cwd.relative_to(agents).parts
            if rel:
                return rel[0]
    except (OSError, ValueError):  # pragma: no cover - 防御性
        pass
    return None


def effective_scope() -> str | None:
    """生效的 scope：运行时优先，其次 skill 声明。"""
    return runtime_scope() or declared_scope()


def scope_mismatch() -> tuple[str | None, str | None] | None:
    """声明 scope 与运行时 scope 不一致时返回 (declared, runtime)。"""
    declared = declared_scope()
    runtime = runtime_scope()
    if not declared or not runtime or declared == runtime:
        return None
    return declared, runtime


# ------------------------------------------------------------------ 家目录

def legacy_home() -> Path:
    """legacy 家目录：``~/lifebrain``。"""
    return Path.home() / LEGACY_DIR_NAME


def legacy_owner() -> str | None:
    """legacy 家的认领者（写在 ``~/lifebrain/config.yaml`` 的 owner_scope）。"""
    cfg = legacy_home() / "config.yaml"
    if not cfg.exists():
        return None
    try:
        value = _yaml_scalar(cfg.read_text(encoding="utf-8"), "owner_scope")
    except OSError:  # pragma: no cover - 防御性
        return None
    return value.strip() if value else None


def scopes_root() -> Path:
    """非认领 scope 的家目录根：``~/.lifebrain/scopes``。"""
    return Path.home() / SCOPES_ROOT_NAME / "scopes"


def legacy_has_data() -> bool:
    """legacy 目录里是否已经有数据（决定能不能自动认领）。"""
    root = legacy_home()
    if not root.is_dir():
        return False
    journals = root / "journals"
    if journals.is_dir() and any(journals.glob("*.md")):
        return True
    for name in ("projects.yaml", "todos.yaml"):
        f = root / name
        try:
            if f.exists() and f.stat().st_size > 0:
                return True
        except OSError:  # pragma: no cover - 防御性
            continue
    return False


def scoped_home(scope: str) -> Path:
    """某个 scope 的家目录。

    * 认领者（owner_scope == scope）→ legacy ``~/lifebrain``
    * legacy 还没被认领 → 空目录先到先得；里面已经有数据则**不碰**，走自己的目录
    * 其他 scope → ``~/.lifebrain/scopes/<scope>``
    """
    scope = (scope or "").strip()
    legacy = legacy_home()
    owner = legacy_owner()
    if owner == scope:
        return legacy
    if owner is None and not legacy_has_data():
        return legacy
    return scopes_root() / scope


def scope_home() -> Path:
    """当前生效的家目录。"""
    env_home = os.environ.get("LIFEBRAIN_HOME")
    if env_home and env_home.strip():
        return Path(env_home.strip()).expanduser()
    runtime = runtime_scope()
    if runtime:
        return scoped_home(runtime)
    declared = declared_home()
    if declared:
        return declared
    return legacy_home()


def scope_status() -> dict:
    """给 config / status 命令用的 scope 诊断信息。"""
    runtime = runtime_scope()
    declared = declared_scope()
    mismatch = scope_mismatch()
    return {
        "scope": effective_scope(),
        "runtime_scope": runtime,
        "declared_scope": declared,
        "scope_source": "runtime" if runtime else ("declared" if declared else "none"),
        "scope_home": str(scope_home()),
        "legacy_home": str(legacy_home()),
        "legacy_owner": legacy_owner(),
        "legacy_has_data": legacy_has_data(),
        "is_legacy_owner": bool(runtime and legacy_owner() == runtime),
        "scope_mismatch": bool(mismatch),
        "scope_mismatch_detail": (
            f"当前进程属于 {runtime}，但这份脚本是 {declared} 的副本；已按 {runtime} 的数据目录执行"
            if mismatch else None
        ),
    }


# ------------------------------------------------------------------ 配置 / 数据目录

def config_home() -> Path:
    """config.yaml 所在目录 = 当前 scope 的家目录。"""
    return scope_home()


def config_path() -> Path:
    return config_home() / "config.yaml"


def data_dir_source() -> str:
    """数据目录来源：env / config / scope / default。"""
    if os.environ.get("LB_DATA_DIR"):
        return "env:LB_DATA_DIR"
    cfg = config_path()
    if cfg.exists():
        try:
            from rebuild_index import read_yaml  # 局部导入，避免循环依赖

            if read_yaml(cfg).get("data_dir"):
                return "config"
        except Exception:  # noqa: BLE001 - 配置坏了也不能影响读路径
            pass
    if effective_scope():
        return f"scope:{effective_scope()}"
    return "default"


def default_data_dir() -> Path:
    """数据目录：``$LB_DATA_DIR`` > config.yaml:data_dir > 当前 scope 家目录。"""
    env = os.environ.get("LB_DATA_DIR")
    if env:
        return Path(env).expanduser()
    cfg = config_path()
    if cfg.exists():
        try:
            from rebuild_index import read_yaml

            data = read_yaml(cfg).get("data_dir")
            if data:
                return Path(str(data)).expanduser()
        except Exception:  # noqa: BLE001
            pass
    return config_home()
