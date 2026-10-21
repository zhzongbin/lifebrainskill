#!/usr/bin/env bash
# lifebrain 一键安装（Octop 实例）
#
#   curl -fsSL https://raw.githubusercontent.com/zhzongbin/lifebrainskill/main/install.sh | bash
#   bash install.sh --agent <AGENT_ID>
#   bash install.sh --agent <AGENT_ID> --with-cron
#
# 环境变量：
#   LIFEBRAIN_REPO    仓库地址（默认官方仓库）
#   LIFEBRAIN_BRANCH  分支（默认 main）
set -euo pipefail

REPO="${LIFEBRAIN_REPO:-https://github.com/zhzongbin/lifebrainskill.git}"
BRANCH="${LIFEBRAIN_BRANCH:-main}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

if ! command -v git >/dev/null 2>&1; then
  echo "ERROR: 需要 git" >&2
  exit 127
fi

echo "→ 拉取 $REPO ($BRANCH)"
git clone --depth 1 --branch "$BRANCH" "$REPO" "$TMP/lifebrainskill" >/dev/null 2>&1 || {
  echo "ERROR: 克隆失败（私有仓库请用 git clone 后手动跑 install_octop.sh）" >&2
  exit 1
}

bash "$TMP/lifebrainskill/lifebrain/scripts/install_octop.sh" "$@"

cat <<'EOF'

提醒任务（投递到你正在用的通道）：
  直接在聊天里跟 agent 说 ——「用 lifebrain 技能建一个每天 21:30 的提醒任务」
  这样建的任务会绑定当前通道（微信/QQ），结果推回那里。

先验证一下：
  python3 ~/.octop/agents/<你的AGENT_ID>/.octop/skills/lifebrain/scripts/lifebrain.py status
EOF
