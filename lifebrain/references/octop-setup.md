# Octop 适配说明（lifebrain × Octop）

这份文档记录 lifebrain 在 **Octop**（不是 omp / Claude Code）里怎么跑、跟原版差在哪。

## 差异总览

| 能力 | 原版（omp） | Octop 版 |
|------|--------------|----------|
| 触发提醒 | system crontab → `omp -p "[lifebrain-reminder] ..."` 拉 headless 进程 | Octop 定时任务 `task_type=agent` → 主动发起会话 → 推送到绑定通道 |
| 结果去向 | omp 的 stdout / 终端 | 通道会话（微信 / QQ / dashboard），用户可直接回 |
| 数据读写 | agent 手写 Markdown | `scripts/lifebrain.py` 统一 CLI（确定性、可校验） |
| 索引重算 | `python3 scripts/rebuild_index.py` | `lifebrain.py append` / `rebuild` 自动调用 |
| 技能目录 | `~/.agents/skills/lifebrain/` | `<agent 工作区>/.octop/skills/lifebrain/` |

## 1. 无头会话 → 定时任务

Octop 里有两套等价机制，都不需要外部 headless CLI：

**A. Octop 定时任务（推荐，主力）**

```bash
octop --agent JVZG0K cron create \
  --trigger "cron:30 21 * * *" \
  --prompt "[lifebrain-reminder] 请执行 lifebrain skill 的提醒模式：检查今天是否已记录，未记录则进入每日记录问答。" \
  --task-type agent --fresh-thread
```

- `task_type=agent` 才会跑一次真正的 agent 会话；`text` 只是把 prompt 原样推给用户。
- 任务绑定到**创建它的那个通道会话**，跑完的回复推回该通道 → 用户在微信里直接接着答。
- `--fresh-thread` 让每次提醒开一个新会话，不带上一晚的上下文。
- 通过 API/工具创建的定时任务**立刻生效**；通过 CLI `cron create` 写的是本地 DB，需要 `octop service restart` 才会被运行中的服务加载。

**B. system crontab 兜底（本仓库 `scripts/reminder.sh`）**

```cron
50 21 * * * /home/zhzongbin/.octop/agents/JVZG0K/.octop/skills/lifebrain/scripts/reminder.sh >> ~/.lifebrain-reminder.log 2>&1
```

兜底脚本自己做幂等：

1. 今天已记录 → 退出（不打扰）
2. 今天已有 `.reminder-YYYY-MM-DD` marker → 退出（Octop 定时任务已经提醒过）
3. 否则 `octop cron run-now <job_id>` 触发同一条投递链路，成功后写 marker

环境变量：`LIFEBRAIN_AGENT`、`LIFEBRAIN_CRON_ID`、`LIFEBRAIN_SESSION_KEY`、`LIFEBRAIN_LOG`。

## 2. 路径映射

| 概念 | Octop 里的实际路径 |
|------|--------------------|
| 技能目录 | `/home/zhzongbin/.octop/agents/JVZG0K/.octop/skills/lifebrain/` |
| 脚本 | `<技能目录>/scripts/lifebrain.py` |
| 数据目录 | `~/lifebrain`（与 omp 版**共用同一份数据**，两个 agent 看到的内容一致） |
| Agent ID | `JVZG0K`（技能目录名就是 agent id） |

数据目录留在 `~/lifebrain` 是刻意的：omp 版和 Octop 版共用一份周文件，谁记的对方都看得见。

## 3. 通道投递的写作约束

定时提醒的输出会推到微信/QQ这类通道，所以：

- 第一句就是人话，不要 JSON、不要脚本回显、不要思考过程。
- 一次最多问 2 个问题，别把问答拆成 5 轮。
- 没有新话题时也要给一句回应（"今天已经记过了 ✓"），不要静默。

## 4. 排错

```bash
octop --agent JVZG0K cron list                 # 看定时任务
octop --agent JVZG0K cron run-now <job_id>    # 手动跑一次
tail -50 ~/.lifebrain-reminder.log            # 兜底脚本日志
python3 <技能目录>/scripts/lifebrain.py status   # 看今天记没记
```

- 提醒没到 → 先确认任务 `enabled`，再确认绑定通道会话还在（`octop --agent JVZG0K chats list`）。
- 索引对不上 → `lifebrain.py rebuild`，看 `dropped` 是否为空。
- 数据目录不对 → `lifebrain.py config` 看实际生效的 `data_dir`。
