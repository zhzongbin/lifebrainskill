#!/usr/bin/env bash
# 把 lifebrain skill 部署到 Octop 实例的技能目录（幂等，可重复执行）。
#
#   ./install_octop.sh                  # 只部署技能文件
#   ./install_octop.sh --with-cron     # 顺便注册每日提醒定时任务
#   ./install_octop.sh --agent <ID>    # 指定目标 agent
#   ./install_octop.sh --print-prompt  # 只打印定时任务的 prompt 模板（路径自解析，可直接粘）
#
# 环境变量：
#   LIFEBRAIN_AGENT          agent id（默认从仓库路径推断，其次扫 ~/.octop/agents/）
#   LIFEBRAIN_SKILLS_DIR     目标技能目录（默认 ~/.octop/agents/<agent>/.octop/skills）
#   LIFEBRAIN_TRIGGER        cron 触发串（默认 cron:30 21 * * *，按实例时区解释）
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

infer_agent() {
  # 1) 从 <...>/.octop/agents/<agent-id>/... 里抠 agent id
  if [[ "$SRC" =~ \.octop/agents/([^/]+) ]]; then
    printf '%s' "${BASH_REMATCH[1]}"
    return
  fi
  # 2) 扫本机已装的 agent（只有一个就用它）
  local found
  found="$(ls -1 "$HOME/.octop/agents" 2>/dev/null | head -1 || true)"
  printf '%s' "$found"
}

AGENT=""
WITH_CRON=0
PRINT_PROMPT=0
while [ $# -gt 0 ]; do
  case "$1" in
    --with-cron) WITH_CRON=1 ;;
    --print-prompt) PRINT_PROMPT=1 ;;
    --agent) AGENT="${2:-}"; shift ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
  shift
done

PROMPT_TEMPLATE="$(cat <<'EOF'
[lifebrain-reminder] 执行 lifebrain skill 的提醒模式（无头定时会话，回复会推送到用户所在通道）。

1. 先解析脚本路径：
   LB_SCRIPT="$(ls -d ~/.octop/agents/*/.octop/skills/lifebrain/scripts/lifebrain.py 2>/dev/null | head -1)"
   [ -z "$LB_SCRIPT" ] && LB_SCRIPT="$(ls -d .octop/skills/lifebrain/scripts/lifebrain.py 2>/dev/null | head -1)"
   两条都没命中就 find ~ -path '*skills/lifebrain/scripts/lifebrain.py' | head -1
2. 运行 python3 "$LB_SCRIPT" status --lookback 3，读 JSON。
3. recorded=true → 只回一句「今天已经记过了 ✓」+ 今天的条目，不再问问题。
4. recorded=false → 1-2 句话问「今天主要做了什么？大概多久？」，用户回复后用 append 写入
   （项目要先在 projects.yaml 登记；脚本会自动重建索引）。
5. missing_dates 非空 → 末尾最多提一句漏记（只提最近一天）。
6. 全程中文、口语化，总共不超过 3 句——这是推送到通道的消息，不是长报告。
7. 结束前运行 python3 "$LB_SCRIPT" mark-reminder，防止兜底 crontab 重复打扰。
EOF
)"

if [ "$PRINT_PROMPT" = "1" ]; then
  printf '%s\n' "$PROMPT_TEMPLATE"
  exit 0
fi

AGENT="${AGENT:-${LIFEBRAIN_AGENT:-$(infer_agent)}}"
if [ -z "$AGENT" ]; then
  echo "ERROR: 认不出目标 agent，请用 --agent <ID> 指定（可先跑 'octop agent list' 看列表）" >&2
  exit 2
fi

SKILLS_DIR="${LIFEBRAIN_SKILLS_DIR:-$HOME/.octop/agents/$AGENT/.octop/skills}"
TARGET="$SKILLS_DIR/lifebrain"
TRIGGER="${LIFEBRAIN_TRIGGER:-cron:30 21 * * *}"

if [ ! -f "$SRC/SKILL.md" ]; then
  echo "ERROR: $SRC 不像 skill 目录（缺 SKILL.md）" >&2
  exit 1
fi

install -d "$SKILLS_DIR"
rm -rf "$TARGET"
install -d "$TARGET"
cp -r "$SRC/." "$TARGET/"
rm -rf "$TARGET/scripts/__pycache__"
chmod +x "$TARGET/scripts/"*.sh "$TARGET/scripts/"*.py 2>/dev/null || true
echo "✔ 技能已部署 → $TARGET"

if [ "$WITH_CRON" = "1" ]; then
  command -v octop >/dev/null || { echo "ERROR: 找不到 octop CLI" >&2; exit 127; }

  # 同一 agent 下已有同款提醒任务就先删掉，避免重复
  old_ids="$(octop --json --agent "$AGENT" cron list 2>/dev/null | python3 -c '
import json, sys
try:
    rows = json.load(sys.stdin)
except Exception:
    rows = []
for r in rows:
    if "[lifebrain-reminder]" in (r.get("prompt") or ""):
        print(r.get("id") or "")
' || true)"
  for cid in $old_ids; do
    octop --agent "$AGENT" cron delete "$cid" >/dev/null 2>&1 || true
    echo "  删除旧任务 $cid"
  done

  octop --agent "$AGENT" cron create \
    --trigger "$TRIGGER" \
    --prompt "$PROMPT_TEMPLATE" \
    --task-type agent \
    --fresh-thread
  echo "✔ 定时任务已注册（$TRIGGER）"
  echo "  注意 1：CLI 写的是本地 DB，需要 'octop service restart' 才会被运行中的服务加载。"
  echo "  注意 2：CLI 建的任务绑到 dashboard（网页控制台）。"
  echo "        想让提醒直接进微信/QQ，请在聊天里让 agent 用 cronjob_create 工具建，会自动绑当前通道。"
fi

echo
echo "下一步："
echo "  1) 让 agent 重新加载技能（新会话生效）"
echo "  2) 验证：python3 $TARGET/scripts/lifebrain.py status"
echo "  3) 兜底 crontab（可选，21:50 再补一次提醒）："
echo "     50 21 * * * $TARGET/scripts/reminder.sh >> ~/.lifebrain-reminder.log 2>&1"
echo "  4) 建定时任务（投递到当前聊天通道）：在聊天里说「用 lifebrain 技能建一个每天 21:30 的提醒任务」"
