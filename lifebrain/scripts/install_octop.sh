#!/usr/bin/env bash
# 把 lifebrain skill 部署到 Octop 实例的技能目录（幂等，可重复执行）。
#
#   ./install_octop.sh              # 只部署技能文件
#   ./install_octop.sh --with-cron  # 顺便注册每日提醒定时任务
#
# 环境变量：
#   LIFEBRAIN_AGENT          agent id（默认取本仓库路径推断，或 JVZG0K）
#   LIFEBRAIN_SKILLS_DIR     目标技能目录（默认 ~/.octop/agents/<agent>/.octop/skills）
#   LIFEBRAIN_TRIGGER        cron 触发串（默认 cron:30 21 * * *）
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
infer_agent() {
  # 从 <...>/.octop/agents/<agent-id>/... 里把 agent id 抠出来
  if [[ "$SRC" =~ \.octop/agents/([^/]+) ]]; then
    printf '%s' "${BASH_REMATCH[1]}"
  fi
}
AGENT="${LIFEBRAIN_AGENT:-$(infer_agent)}"
AGENT="${AGENT:-JVZG0K}"
SKILLS_DIR="${LIFEBRAIN_SKILLS_DIR:-$HOME/.octop/agents/$AGENT/.octop/skills}"
TARGET="$SKILLS_DIR/lifebrain"
TRIGGER="${LIFEBRAIN_TRIGGER:-cron:30 21 * * *}"
PROMPT='[lifebrain-reminder] 请执行 lifebrain skill 的提醒模式：检查今天是否已记录，未记录则进入每日记录问答。'
WITH_CRON=0
[ "${1:-}" = "--with-cron" ] && WITH_CRON=1

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
    --prompt "$PROMPT" \
    --task-type agent \
    --fresh-thread
  echo "✔ 定时任务已注册（$TRIGGER）"
  echo "  注意：CLI 写的是本地 DB，需要 'octop service restart' 才会被运行中的服务加载；"
  echo "        更好的做法是让 agent 用 cronjob_create 工具创建，绑定当前通道会话并立即生效。"
fi

echo
echo "下一步："
echo "  1) 让 agent 重新加载技能（新会话生效）"
echo "  2) 验证：python3 $TARGET/scripts/lifebrain.py status"
echo "  3) 兜底 crontab（可选，21:50 再补一次提醒）："
echo "     50 21 * * * $TARGET/scripts/reminder.sh >> ~/.lifebrain-reminder.log 2>&1"
