#!/usr/bin/env bash
# lifebrain 每日提醒入口 —— Octop 版。
#
# 作用：system cron → 触发 Octop 主动发起一次 agent 会话（无头）→ 结果推送到用户绑定的通道（微信/QQ/dashboard…）。
# 这是原 omp 版（reminder_omp.sh）在 Octop 上的等价替代：Octop 自带调度器 + 通道投递，
# 不需要额外再拉一个 headless CLI 进程。
#
# 用法：
#   LIFEBRAIN_AGENT=JVZG0K LIFEBRAIN_CRON_ID=cXXXXXX ./reminder.sh
#
# 幂等策略（避免重复打扰）：
#   1. 今天已有记录            → 直接退出，不打扰
#   2. 今天已发过提醒(marker)  → 直接退出
#   3. 否则触发一次提醒会话，并写入 marker
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LI=(python3 "${SKILL_DIR}/scripts/lifebrain.py")
AGENT="${LIFEBRAIN_AGENT:-JVZG0K}"
CRON_ID="${LIFEBRAIN_CRON_ID:-}"
LOG="${LIFEBRAIN_LOG:-$HOME/.lifebrain-reminder.log}"
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
if [ "$fired" = "yes" ]; then
  log "SKIP: 今天已发过提醒（marker 存在）"
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
for r in rows:
    if "lifebrain" in (r.get("prompt") or "") or "lifebrain" in (r.get("name") or ""):
        print(r.get("id") or "")
        break
' 2>/dev/null || true)"
fi

if [ -n "$CRON_ID" ]; then
  # 走 Octop 调度器同一条投递链路：结果会推到该任务绑定的通道
  if octop --agent "$AGENT" cron run-now "$CRON_ID" >>"$LOG" 2>&1; then
    "${LI[@]}" mark-reminder >/dev/null 2>&1 || true
    log "OK: 已触发提醒任务 $CRON_ID"
    exit 0
  fi
  log "WARN: cron run-now 失败，改用 chats send"
fi

# --- 3. 兜底：直接对绑定会话发一条消息（CLI 通道）---
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
