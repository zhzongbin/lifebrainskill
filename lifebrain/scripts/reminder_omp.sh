#!/usr/bin/env bash
# lifebrain 每日提醒入口 —— omp（oh-my-pi / pi-coding-agent）版，原样保留。
#
# 作用：system cron → 无头拉起一个 headless agent 会话（omp -p）→ 该 agent 执行 lifebrain 提醒模式。
# Octop 实例请改用同目录下的 reminder.sh（走 Octop 调度器 + 通道投递）。
#
# crontab 行：
#   30 21 * * * ~/.agents/skills/lifebrain/scripts/reminder_omp.sh >> ~/.lifebrain-reminder.log 2>&1
set -euo pipefail

OMP="${OMP:-$HOME/.bun/bin/omp}"
PROMPT='[lifebrain-reminder] 请执行 lifebrain skill 的提醒模式：检查今天是否已记录，未记录则进入每日记录问答。'

"$OMP" -p "$PROMPT"
