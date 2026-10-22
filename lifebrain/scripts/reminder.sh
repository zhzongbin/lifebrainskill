#!/usr/bin/env bash
# lifebrain 每日提醒兜底入口 —— Octop 版。
#
# 作用：system cron → 触发 Octop 主动发起一次 agent 会话（无头）→ 结果推送到用户绑定的通道（微信/QQ/dashboard…）。
# 这是原 omp 版（reminder_omp.sh）在 Octop 上的等价替代：Octop 自带调度器 + 通道投递，
# 不需要额外再拉一个 headless CLI 进程。
#
# 用法：
#   LIFEBRAIN_SCOPE=JVZG0K LIFEBRAIN_CRON_ID=cXXXXXX ./reminder.sh
#
# 幂等策略（避免重复打扰）：
#   1. 今天已有记录            → 直接退出，不打扰
#   2. 今天已发过提醒(marker)  → 直接退出
#   3. 否则触发一次提醒会话，并写入 marker
#
# marker 语义（v1.3.0 起）：**投递成功才算数**。
#   agent 在会话末尾会先写 marker（它只知道内容生成成功，不知道通道投递成没成功），
#   所以兜底脚本会去看 cron 的 last_status：
#     * last_status=ok     → 真的送达了，保留 marker，跳过
#     * last_status=error  → 上次投递失败，marker --clear 清掉并重试
#   重试也失败时不写 marker，下一轮兜底还能再试。
#
# 多用户隔离：scope 决定「数据读谁的 + 触发哪个 agent 的任务」。
#   scope 来自 scope.json（技能副本自带）或 $LIFEBRAIN_SCOPE / $LIFEBRAIN_AGENT / $OCTOP_AGENT_ID。
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LI=(python3 "${SKILL_DIR}/scripts/lifebrain.py")

read_scope() {
  # 1) 显式环境变量
  for var in LIFEBRAIN_SCOPE LIFEBRAIN_AGENT OCTOP_AGENT_ID; do
    if [ -n "${!var:-}" ]; then
      printf '%s' "${!var}"
      return
    fi
  done
  # 2) 技能副本自带的 scope.json
  if [ -f "${SKILL_DIR}/scope.json" ]; then
    sed -n 's/.*"scope"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "${SKILL_DIR}/scope.json" | head -1
  fi
}

AGENT="$(read_scope)"
AGENT="${AGENT:-JVZG0K}"
CRON_ID="${LIFEBRAIN_CRON_ID:-}"
LOG="${LIFEBRAIN_LOG:-$HOME/.lifebrain-reminder-$AGENT.log}"
REMINDER_PROMPT='[lifebrain-reminder] 请执行 lifebrain skill 的提醒模式：检查今天是否已记录，未记录则进入每日记录问答。'

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"$LOG"; }

if [ ! -x "$(command -v octop)" ]; then
  log "ERROR: 找不到 octop CLI，跳过提醒"
  exit 127
fi

# --- 1. 状态检查（今天是否已记录 / 今天是否已发过提醒）---
status_json="$("${LI[@]}" status --lookback 1 2>/dev/null || echo '{}')"
read -r recorded fired <<EOF
$(printf '%s' "$status_json" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    d = {}
print("yes" if d.get("recorded") else "no", "yes" if d.get("reminder_fired_today") else "no")
' 2>/dev/null || echo "no no")
EOF

if [ "$recorded" = "yes" ]; then
  log "SKIP: 今天已记录"
  exit 0
fi

# --- 2. 定位提醒用的 cron 任务 ---
if [ -z "$CRON_ID" ]; then
  CRON_ID="$(octop --json --agent "$AGENT" cron list 2>/dev/null | python3 -c '
import json, sys
try:
    rows = json.load(sys.stdin)
except Exception:
    rows = []
evening = [r for r in rows if "[lifebrain-reminder]" in (r.get("prompt") or "")]
pick = evening or [r for r in rows if "lifebrain" in (r.get("name") or "")]
if pick:
    print(pick[0].get("id") or "")
' 2>/dev/null || true)"
fi

cron_status() {
  # 输出该 cron 的 last_status（拿不到就空）
  [ -n "$CRON_ID" ] || { printf ''; return; }
  octop --json --agent "$AGENT" cron list 2>/dev/null | python3 -c '
import json, sys
cid = sys.argv[1]
try:
    rows = json.load(sys.stdin)
except Exception:
    rows = []
for r in rows:
    if r.get("id") == cid:
        print(r.get("last_status") or "")
        break
' "$CRON_ID" 2>/dev/null || printf ''
}

if [ "$fired" = "yes" ]; then
  last="$(cron_status)"
  case "$last" in
    ok)
      log "SKIP: 今天已发过提醒（marker 存在，cron 上次投递 ok）"
      exit 0
      ;;
    error)
      log "WARN: marker 存在但 cron 上次投递失败（$last），清掉 marker 重试"
      "${LI[@]}" mark-reminder --clear >/dev/null 2>&1 || true
      ;;
    *)
      log "SKIP: 今天已发过提醒（marker 存在，cron 状态未知 $last）"
      exit 0
      ;;
  esac
fi

# --- 3. 触发提醒（走 Octop 调度器同一条投递链路）---
if [ -n "$CRON_ID" ]; then
  # run-now 会等待执行结束；投递失败时命令非零退出 → 不写 marker，允许下轮重试
  if octop --agent "$AGENT" cron run-now "$CRON_ID" >>"$LOG" 2>&1; then
    "${LI[@]}" mark-reminder >/dev/null 2>&1 || true
    log "OK: 已触发提醒任务 $CRON_ID（投递成功）"
    exit 0
  fi
  log "WARN: cron run-now 失败（投递未成功），不写 marker，下轮兜底再试"
fi

# --- 4. 兜底：直接对绑定会话发一条消息（CLI 通道）---
SESSION_KEY="${LIFEBRAIN_SESSION_KEY:-}"
if [ -n "$SESSION_KEY" ]; then
  if octop --agent "$AGENT" chats send --session-key "$SESSION_KEY" "$REMINDER_PROMPT" >>"$LOG" 2>&1; then
    "${LI[@]}" mark-reminder >/dev/null 2>&1 || true
    log "OK: 已通过 chats send 触发提醒"
    exit 0
  fi
  log "ERROR: chats send 失败"
  exit 1
fi

log "ERROR: 没有可用触发方式（需要 LIFEBRAIN_CRON_ID 或 LIFEBRAIN_SESSION_KEY）"
exit 1
