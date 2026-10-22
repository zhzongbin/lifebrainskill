#!/usr/bin/env bash
# 把 lifebrain skill 部署到 Octop 实例的技能目录（幂等，可重复执行）。
#
#   ./install_octop.sh                  # 只部署技能文件
#   ./install_octop.sh --with-cron     # 顺便注册每日提醒定时任务
#   ./install_octop.sh --agent <ID>    # 指定目标 agent
#   ./install_octop.sh --home <目录>   # 指定该 agent 的数据根（默认按 scope 自动分配）
#   ./install_octop.sh --print-prompt  # 只打印晚间任务的 prompt 模板（路径自解析，可直接粘）
#   ./install_octop.sh --print-prompt morning  # 只打印早间（带目标进展）任务的 prompt 模板
#
# 环境变量：
#   LIFEBRAIN_AGENT          agent id（默认取 OCTOP_AGENT_ID，再从仓库路径推断）
#   LIFEBRAIN_SKILLS_DIR     目标技能目录（默认 ~/.octop/agents/<agent>/.octop/skills）
#   LIFEBRAIN_HOME           该 agent 的数据根（默认按 scope 自动分配，见下）
#   LIFEBRAIN_TRIGGER        cron 触发串（默认 cron:30 21 * * *，按实例时区解释）
#   LIFEBRAIN_MORNING_TRIGGER 早间任务触发串（默认 cron:30 7 * * *）
#
# 多用户隔离（重要）：
#   一台 Octop 主机上可能同时跑多个用户（每个用户一个 agent），但它们共用同一个
#   Unix 账号和同一个 $HOME。**~/lifebrain 不是按用户隔离的**，所以本脚本会给每个
#   agent 分配一个独立的数据根，并把归属写进技能目录下的 scope.json：
#     * 认领了 ~/lifebrain 的那个 agent（config.yaml 里的 owner_scope）→ 用 ~/lifebrain
#     * 其他 agent                                    → 用 ~/.lifebrain/scopes/<agent>
#   技能脚本运行时再按「当前进程属于谁」二次校验，跑错脚本也不会读到别人的数据。
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

infer_agent() {
  # 1) Octop agent 运行时注入（每个 agent 一份，最可靠）
  if [ -n "${OCTOP_AGENT_ID:-}" ]; then
    printf '%s' "$OCTOP_AGENT_ID"
    return
  fi
  # 2) 显式环境变量
  if [ -n "${LIFEBRAIN_AGENT:-}" ]; then
    printf '%s' "$LIFEBRAIN_AGENT"
    return
  fi
  # 3) 从 <...>/.octop/agents/<agent-id>/... 里抠 agent id
  if [[ "$SRC" =~ \.octop/agents/([^/]+) ]]; then
    printf '%s' "${BASH_REMATCH[1]}"
    return
  fi
  # 4) 本机只装了一个 agent 时用它；装了多个就拒绝猜（猜错=数据串用户）
  local found count
  found="$(ls -1 "$HOME/.octop/agents" 2>/dev/null || true)"
  count="$(printf '%s\n' "$found" | grep -c . || true)"
  if [ "$count" = "1" ]; then
    printf '%s' "$found"
    return
  fi
  printf ''
}

# 读 legacy 配置里的 owner_scope（认领者）
legacy_owner() {
  local cfg="$HOME/lifebrain/config.yaml"
  [ -f "$cfg" ] || return 0
  sed -n 's/^owner_scope:[[:space:]]*"\{0,1\}\([^"]*\)"\{0,1\}[[:space:]]*$/\1/p' "$cfg" | head -1
}

# legacy 目录里是否已经有数据（有数据就不能被别人自动认领）
legacy_has_data() {
  local root="$HOME/lifebrain"
  [ -d "$root" ] || return 1
  if [ -d "$root/journals" ] && ls -1 "$root"/journals/*.md >/dev/null 2>&1; then return 0; fi
  [ -s "$root/projects.yaml" ] && return 0
  [ -s "$root/todos.yaml" ] && return 0
  return 1
}

# 按 scope 分配数据根
resolve_home() {
  local scope="$1" owner
  if [ -n "${LIFEBRAIN_HOME:-}" ]; then
    printf '%s' "$LIFEBRAIN_HOME"
    return
  fi
  owner="$(legacy_owner)"
  if [ -n "$owner" ]; then
    if [ "$owner" = "$scope" ]; then
      printf '%s' "$HOME/lifebrain"
    else
      printf '%s' "$HOME/.lifebrain/scopes/$scope"
    fi
    return
  fi
  # 还没人认领：空的先到先得；里面已有数据则不碰，走自己的目录
  if legacy_has_data; then
    printf '%s' "$HOME/.lifebrain/scopes/$scope"
  else
    printf '%s' "$HOME/lifebrain"
  fi
}

AGENT=""
HOME_OVERRIDE=""
WITH_CRON=0
PRINT_PROMPT=0
PROMPT_KIND="evening"
while [ $# -gt 0 ]; do
  case "$1" in
    --with-cron) WITH_CRON=1 ;;
    --print-prompt)
      PRINT_PROMPT=1
      if [ "${2:-}" = "morning" ]; then PROMPT_KIND="morning"; shift; fi
      ;;
    --agent) AGENT="${2:-}"; shift ;;
    --home) HOME_OVERRIDE="${2:-}"; shift ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
  shift
done

PROMPT_TEMPLATE="$(cat <<'EOF'
[lifebrain-reminder] 执行 lifebrain skill 的提醒模式（无头定时会话，回复会推送到用户所在通道）。

1. 先解析脚本路径（**只取当前 agent 自己目录里的那份**，不要扫 ~/.octop/agents/*）：
   LB_SCRIPT=""
   [ -n "${OCTOP_SKILLS_DIR:-}" ] && [ -f "$OCTOP_SKILLS_DIR/lifebrain/scripts/lifebrain.py" ] && LB_SCRIPT="$OCTOP_SKILLS_DIR/lifebrain/scripts/lifebrain.py"
   [ -z "$LB_SCRIPT" ] && [ -f "$PWD/.octop/skills/lifebrain/scripts/lifebrain.py" ] && LB_SCRIPT="$PWD/.octop/skills/lifebrain/scripts/lifebrain.py"
   [ -z "$LB_SCRIPT" ] && [ -n "${OCTOP_AGENT_ID:-}" ] && [ -f "$HOME/.octop/agents/$OCTOP_AGENT_ID/.octop/skills/lifebrain/scripts/lifebrain.py" ] && LB_SCRIPT="$HOME/.octop/agents/$OCTOP_AGENT_ID/.octop/skills/lifebrain/scripts/lifebrain.py"
   [ -z "$LB_SCRIPT" ] && LB_SCRIPT="$(ls -d "$HOME"/.octop/agents/*/.octop/skills/lifebrain/scripts/lifebrain.py 2>/dev/null | head -1)"
   [ -z "$LB_SCRIPT" ] && { echo "找不到 lifebrain.py，停止"; exit 1; }
2. 运行 python3 "$LB_SCRIPT" config，读 JSON。先确认 scope.runtime_scope 就是当前 agent
   （应该是 ${OCTOP_AGENT_ID:-当前agent}）——不是的话说明脚本定位错了，用
   "$OCTOP_SKILLS_DIR/lifebrain/scripts/lifebrain.py" 重来一次，别用别人的数据。
3. 运行 python3 "$LB_SCRIPT" status --lookback 3，读 JSON。
3.5 若 status 里 initialized=false（用户还没确认数据目录）→ 只回一句「还没定记录放哪儿，回我一句我就给你建好」
   然后结束；不要自己建目录、不要写数据。
4. recorded=true → 只回一句「今天已经记过了 ✓」+ 今天的条目，不再问问题。
5. recorded=false → 1-2 句话问「今天主要做了什么？大概多久？」，用户回复后用 append 写入
   （项目要先在 projects.yaml 登记；脚本会自动重建索引）。
6. missing_dates 非空 → 末尾最多提一句漏记（只提最近一天）。
7. 全程中文、口语化，总共不超过 3 句——这是推送到通道的消息，不是长报告。
8. 结束前运行 python3 "$LB_SCRIPT" mark-reminder，防止兜底 crontab 重复打扰。
   （兜底脚本发现投递失败时会用 mark-reminder --clear 清掉并重试，所以这里写不写都不影响兜底。）
9. 用户提到「明天/接下来要做什么」→ 用 todo add 记成新目标（早间提醒会带上）。
10. 用户说的是「某个已有目标推进了/卡住了」→ 不要新建 todo，
   用 append --todo <id> 把进展挂到那个目标上（时长会计入该目标）。
EOF
)"

MORNING_PROMPT_TEMPLATE="$(cat <<'EOF'
[lifebrain-morning] 执行 lifebrain skill 的早间模式（无头定时会话，回复会推送到用户所在通道）。

1. 先解析脚本路径（**只取当前 agent 自己目录里的那份**，不要扫 ~/.octop/agents/*）：
   LB_SCRIPT=""
   [ -n "${OCTOP_SKILLS_DIR:-}" ] && [ -f "$OCTOP_SKILLS_DIR/lifebrain/scripts/lifebrain.py" ] && LB_SCRIPT="$OCTOP_SKILLS_DIR/lifebrain/scripts/lifebrain.py"
   [ -z "$LB_SCRIPT" ] && [ -f "$PWD/.octop/skills/lifebrain/scripts/lifebrain.py" ] && LB_SCRIPT="$PWD/.octop/skills/lifebrain/scripts/lifebrain.py"
   [ -z "$LB_SCRIPT" ] && [ -n "${OCTOP_AGENT_ID:-}" ] && [ -f "$HOME/.octop/agents/$OCTOP_AGENT_ID/.octop/skills/lifebrain/scripts/lifebrain.py" ] && LB_SCRIPT="$HOME/.octop/agents/$OCTOP_AGENT_ID/.octop/skills/lifebrain/scripts/lifebrain.py"
   [ -z "$LB_SCRIPT" ] && LB_SCRIPT="$(ls -d "$HOME"/.octop/agents/*/.octop/skills/lifebrain/scripts/lifebrain.py 2>/dev/null | head -1)"
   [ -z "$LB_SCRIPT" ] && { echo "找不到 lifebrain.py，停止"; exit 1; }
2. 运行 python3 "$LB_SCRIPT" config，读 JSON。先确认 scope.runtime_scope 就是当前 agent
   （应该是 ${OCTOP_AGENT_ID:-当前agent}）——不是的话说明脚本定位错了，换成
   "$OCTOP_SKILLS_DIR/lifebrain/scripts/lifebrain.py" 再来，别用别人的数据。
3. 运行 python3 "$LB_SCRIPT" status --lookback 2，读 JSON。
4. 运行 python3 "$LB_SCRIPT" todo review --days 7，读 JSON（目标进展复盘：今天该聚焦哪几个、投入 vs 预计）。
5. 可选：用 memory_search 查跟这些项目相关的未完成事项，最多补 1 条，不要硬凑。
6. 输出不超过 5 行：
   - 第 1 行：早安 + 「今天的目标：1) … 2) …」（最多 3 条；没有目标写「今天还没排目标」）
   - 第 2 行：进展——每个目标最近一次进展 + 累计投入（如「t1 测线：上次到解释剖面，累计 3.5h/16h」）
   - 第 3 行：昨天做了什么（1 行以内）
   - 第 4 行：有漏记就提一句；然后最多问一个问题——挑一个目标问「今天想先推哪一步 / 有没有新进展或卡点」
     （只问一个，不要变问卷）
7. 全程中文、口语化，第一句就是人话，不要 JSON、不要脚本回显。
8. 早间只读不写：不要 append、不要 todo add、不要 todo done，等用户回复再动手。
EOF
)"

if [ "$PRINT_PROMPT" = "1" ]; then
  if [ "$PROMPT_KIND" = "morning" ]; then
    printf '%s\n' "$MORNING_PROMPT_TEMPLATE"
  else
    printf '%s\n' "$PROMPT_TEMPLATE"
  fi
  exit 0
fi

AGENT="${AGENT:-${LIFEBRAIN_AGENT:-$(infer_agent)}}"
if [ -z "$AGENT" ]; then
  echo "ERROR: 认不出目标 agent，请用 --agent <ID> 指定（可先跑 'octop agent list' 看列表）" >&2
  echo "      多个 agent 共用一台机器时不要靠猜——猜错就会读到别人的数据。" >&2
  exit 2
fi

SKILLS_DIR="${LIFEBRAIN_SKILLS_DIR:-$HOME/.octop/agents/$AGENT/.octop/skills}"
TARGET="$SKILLS_DIR/lifebrain"
TRIGGER="${LIFEBRAIN_TRIGGER:-cron:30 21 * * *}"

if [ ! -f "$SRC/SKILL.md" ]; then
  echo "ERROR: $SRC 不像 skill 目录（缺 SKILL.md）" >&2
  exit 1
fi

# --- 分配数据根 & 认领 legacy 家目录 ---
if [ -n "$HOME_OVERRIDE" ]; then
  export LIFEBRAIN_HOME="$HOME_OVERRIDE"
fi
DATA_HOME="$(resolve_home "$AGENT")"
if [ -z "${LIFEBRAIN_HOME:-}" ] && [ "$DATA_HOME" = "$HOME/lifebrain" ]; then
  # legacy 家目录归当前 agent（写进 config.yaml 的 owner_scope）
  LEGACY_CFG="$HOME/lifebrain/config.yaml"
  if [ -z "$(legacy_owner)" ]; then
    if [ -f "$LEGACY_CFG" ]; then
      if ! grep -q '^owner_scope:' "$LEGACY_CFG"; then
        printf 'owner_scope: "%s"\n' "$AGENT" >> "$LEGACY_CFG"
        echo "  认领 legacy 数据目录 ~/lifebrain → owner_scope=$AGENT"
      fi
    else
      mkdir -p "$HOME/lifebrain"
      printf 'owner_scope: "%s"\n' "$AGENT" > "$LEGACY_CFG"
      echo "  认领 legacy 数据目录 ~/lifebrain → owner_scope=$AGENT"
    fi
  fi
elif [ -z "${LIFEBRAIN_HOME:-}" ] && legacy_has_data && [ -z "$(legacy_owner)" ]; then
  echo "  ⚠ ~/lifebrain 里已有数据但没有 owner_scope，本 agent 不认领它。"
  echo "    如需接管：--home ~/lifebrain（会写 owner_scope）"
fi

install -d "$SKILLS_DIR"
rm -rf "$TARGET"
install -d "$TARGET"
cp -r "$SRC/." "$TARGET/"
rm -rf "$TARGET/scripts/__pycache__"
chmod +x "$TARGET/scripts/"*.sh "$TARGET/scripts/"*.py 2>/dev/null || true

# scope.json：这份技能副本属于谁、数据放哪（脚本运行时读它做二次校验）
cat > "$TARGET/scope.json" <<EOF
{
  "scope": "$AGENT",
  "agent_id": "$AGENT",
  "home": "$DATA_HOME",
  "installed_at": "$(date -Iseconds)"
}
EOF
echo "✔ 技能已部署 → $TARGET"
echo "  scope=$AGENT  数据根=$DATA_HOME"

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

  # 早间带 todo 的那条
  MORNING_TRIGGER="${LIFEBRAIN_MORNING_TRIGGER:-cron:30 7 * * *}"
  octop --agent "$AGENT" cron create \
    --trigger "$MORNING_TRIGGER" \
    --prompt "$MORNING_PROMPT_TEMPLATE" \
    --task-type agent \
    --fresh-thread
  echo "✔ 早间待办提醒已注册（$MORNING_TRIGGER）"
  echo "  注意 1：CLI 写的是本地 DB，需要 'octop service restart' 才会被运行中的服务加载。"
  echo "  注意 2：CLI 建的任务绑到 dashboard（网页控制台）。"
  echo "        想让提醒直接进微信/QQ，请在聊天里让 agent 用 cronjob_create 工具建，会自动绑当前通道。"
fi

echo
echo "下一步："
echo "  1) 让 agent 重新加载技能（新会话生效）"
echo "  2) 验证：python3 $TARGET/scripts/lifebrain.py config"
echo "     （确认 scope.runtime_scope = $AGENT、data_dir = $DATA_HOME）"
echo "  3) 兜底 crontab（可选，21:50 再补一次提醒；投递失败会自动重试）："
echo "     50 21 * * * LIFEBRAIN_SCOPE=$AGENT $TARGET/scripts/reminder.sh >> ~/.lifebrain-reminder-$AGENT.log 2>&1"
echo "  4) 建定时任务（投递到当前聊天通道）：在聊天里说「用 lifebrain 技能建一个每天 21:30 的提醒任务」"
