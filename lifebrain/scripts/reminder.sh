#!/usr/bin/env bash
# cron entry: daily reminder -> headless agent session.
# crontab line (adjust path & time):
#   30 21 * * * ~/.agents/skills/lifebrain/scripts/reminder.sh >> ~/.lifebrain-reminder.log 2>&1
set -euo pipefail

OMP="${OMP:-$HOME/.bun/bin/omp}"
PROMPT='[lifebrain-reminder] 请执行 lifebrain skill 的提醒模式：检查今天是否已记录，未记录则进入每日记录问答。'

"$OMP" -p "$PROMPT"
