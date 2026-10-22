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
50 21 * * * LIFEBRAIN_SCOPE=JVZG0K /home/zhzongbin/.octop/agents/JVZG0K/.octop/skills/lifebrain/scripts/reminder.sh >> ~/.lifebrain-reminder-JVZG0K.log 2>&1
```

> **每个 scope 一行**，并且把 `LIFEBRAIN_SCOPE` 钉死。共享主机上多个用户的兜底脚本
> 会共存于同一个系统 crontab，但各自只读各自的数据、各自触发各自 agent 的 cron。
> 不钉 `LIFEBRAIN_SCOPE` 时，脚本会退化成"扫第一个 agent"，在多用户机器上必串。

兜底脚本自己做幂等：

1. 今天已记录 → 退出（不打扰）
2. 今天已有 `.reminder-YYYY-MM-DD` marker → 查该 cron 的 `last_status`：
   - `ok` → 投递成功过，退出
   - `error` → **投递失败过** → 清掉 marker，继续往下重试（这是"发失败了还能补发"的关键）
3. 否则 `octop cron run-now <job_id>` 触发同一条投递链路
   - 成功（退出码 0，含通道投递成功）→ 写 marker
   - 失败 → **不写 marker**，下次兜底还能再试

环境变量：`LIFEBRAIN_SCOPE`（推荐）、`LIFEBRAIN_AGENT`、`LIFEBRAIN_HOME`、
`LIFEBRAIN_CRON_ID`、`LIFEBRAIN_SESSION_KEY`、`LIFEBRAIN_LOG`。

## 2. 路径映射

| 概念 | Octop 里的实际路径 |
|------|--------------------|
| 技能目录 | `/home/zhzongbin/.octop/agents/JVZG0K/.octop/skills/lifebrain/` |
| 脚本 | `<技能目录>/scripts/lifebrain.py` |
| 数据目录 | `~/lifebrain`（**只有认领了它的那个 scope 能用**）；别的 scope 是 `~/.lifebrain/scopes/<scope>` |
| Agent ID | `JVZG0K`（技能目录名就是 agent id） |
| scope 声明 | `<技能目录>/scope.json`（安装时写入，`{"scope": "JVZG0K", "home": "…"}`） |

**同一台机器上多个用户时，`~/lifebrain` 不再天然隔离。** 共享主机上每个 agent 必须
有自己的一份数据目录，靠 `scope` 机制分流：认领了 `~/lifebrain` 的那个 scope 用 legacy
目录，其余 scope 用 `~/.lifebrain/scopes/<scope>`。判定依据是 `$OCTOP_AGENT_ID`
（Octop 给每个 agent 注入）→ `$LIFEBRAIN_SCOPE` → `$LIFEBRAIN_AGENT` → 工作目录。

`lifebrain.py config` 的 `scope` 字段随时可以自检当前解析到哪一份。

## 3. 通道投递的写作约束

定时提醒的输出会推到微信/QQ这类通道，所以：

- 第一句就是人话，不要 JSON、不要脚本回显、不要思考过程。
- 一次最多问 2 个问题，别把问答拆成 5 轮。
- 没有新话题时也要给一句回应（"今天已经记过了 ✓"），不要静默。

## 3.5 Octop 记忆整理：什么时候跑

**结论：不是固定在晚上，而是"会话一安静就整理"。**

Octop 的记忆抽取（atom 抽取 + entity page 重建）由 harness 的 memory middleware 驱动，触发方式两种（互斥）：

| 模式 | 配置项 | 行为 | 默认 |
|---|---|---|---|
| idle | `memory_extract_trigger_mode = "idle"` | 每个会话在**最后一次对话后静默 300 秒**（5 分钟）触发抽取 | ✅ 默认 |
| interval | `memory_extract_trigger_mode = "interval"` | 每 `memory_extract_interval_seconds`（默认 6 小时）扫一遍近期活跃会话 | 可选 |

所以实践中的效果是：**晚上 21:30 的记录会话聊完、5 分钟没说话，那一刻记忆就整理好了**；
第二天早上 `memory_search` 拿到的就是昨晚沉淀过的内容。这也解释了"感觉是晚上整理的"的印象 —— 整理确实发生在晚间，只是由"静默"触发而不是由"夜间定时"触发。

其他相关操作：

- `octop memory slim` —— 手动备份 + 瘦身（运维动作，不是自动跑的）。
- 记忆抽取用主模型还是辅助模型由 `memory_aux_model` 决定；辅助模型不可用时会降级，不阻塞主对话。

早间任务用 `memory_search` 补充待办时，就是读这一晚整理出来的结果；
权威清单仍然是 `todos.yaml`（确定性、可校验），记忆只作为补充（最多加 1 条）。

## 4.5 目标进展怎么进早间提醒

数据模型分两层，各管一件事：

| 层 | 存哪 | 存什么 |
|---|---|---|
| 目标定义 | `<数据目录>/todos.yaml` | 叫什么、达成标准、挂在哪个项目、预计投入、截止日、状态 |
| 实际投入 | `<数据目录>/journals/YYYY-Www.md` | 条目行尾的 `\| t1` 引用 + 该条时长 |

于是三件事各归各位：

- **清单**来自 `todos.yaml`（`todo list` / `todo review`）；
- **进展和投入**来自周文件的引用（`actual_hours` / `last_progress` / `last_activity`）；
- **完成度**是两者相除的结果（`planned_hours` vs `actual_hours`，`hours_gap`）。

晚间会话里用户报"某目标推进了"→ `append --todo <id>`；
用户说"明天要 X"→ `todo add`（这才是新目标）。
早间用 `todo review --days 7` 一次拿到「该聚焦哪几个 + 投入 vs 预计 + 卡住没动的」。

## 5. 放进实例级全局技能包（技能共享池）

Octop 的「全局技能包」是实例级的共享池：包里的技能可以挂给多个 agent，也可以让别的用户复制走。

本机现状：包 `工作记录`（id `N9ZQ2T`），里面装着 `lifebrain`；`copy_policy=snapshot`，
别人复制到自己的 workspace 后是独立快照。

包内技能目录（挂载后 agent 直接可读，不需要复制到工作区）：

```
~/.octop/skill-packages/<包ID>/skills/lifebrain/SKILL.md
~/.octop/skill-packages/<包ID>/skills/lifebrain/scripts/lifebrain.py
```

所以脚本路径解析里有一条例外：

```bash
[ -z "$LB_SCRIPT" ] && LB_SCRIPT="$(ls -d ~/.octop/skill-packages/*/skills/lifebrain/scripts/lifebrain.py 2>/dev/null | head -1)"
```

**用 API 更新包内技能**（本地管理员）：

```bash
TOKEN=<admin token>
python3 - <<'PY' > /tmp/payload.json   # 把 lifebrain/ 下的文件打包成 API 需要的 base64 列表
import base64, json, pathlib
root = pathlib.Path("lifebrain")
files = [{"path": str(p.relative_to(root)),
          "content_base64": base64.b64encode(p.read_bytes()).decode()}
         for p in sorted(root.rglob("*"))
         if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"]
print(json.dumps({"name": "lifebrain", "files": files, "overwrite": True}, ensure_ascii=False))
PY
curl -s -X POST "http://127.0.0.1:8088/api/skill-packages/<包ID>/skills" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  --data-binary @/tmp/payload.json
```

**把包挂给某个 agent**：

```bash
curl -s -X PATCH "http://127.0.0.1:8088/api/agents/<AGENT_ID>" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"skill_package_ids":["<包ID>"]}'
```

注意：包内技能和 agent 工作区里的技能**同名时，包里的版本优先**。
所以工作区那份是"给兜底脚本用的本地安装"，包里的那份是"全实例共用的最新版"——两边内容一致时没有歧义。

**从包复制到别的用户时**：包里的 `lifebrain/` **不含任何数据**，数据永远在各自机器上的
scope 目录里。别人复制走之后必须自己跑一次 `install_octop.sh`（它会写 `scope.json`、
认领或分配数据目录），否则那份脚本没有 scope 声明，会退回到"扫第一个 agent"的旧行为。

**重要**：`~/.octop/agents/*` 下有多个 agent 时，**绝不能**用
`ls -d ~/.octop/agents/*/... | head -1` 找脚本——那会拿到别人的副本。
用 `$OCTOP_SKILLS_DIR`（Octop 给每个 agent 注入的自己的技能目录）：

```bash
[ -n "${OCTOP_SKILLS_DIR:-}" ] && [ -f "$OCTOP_SKILLS_DIR/lifebrain/scripts/lifebrain.py" ] && LB_SCRIPT="$OCTOP_SKILLS_DIR/lifebrain/scripts/lifebrain.py"
```

## 6. 排错

```bash
octop --agent JVZG0K cron list                 # 看定时任务
octop --agent JVZG0K cron run-now <job_id>    # 手动跑一次
tail -50 ~/.lifebrain-reminder-JVZG0K.log     # 兜底脚本日志
python3 <技能目录>/scripts/lifebrain.py status   # 看今天记没记
```

- 提醒没到 → 先确认任务 `enabled`，再确认绑定通道会话还在（`octop --agent JVZG0K chats list`）。
- 提醒没到且 `last_status=error` → 看 `last_error`：多半是**通道投递失败**（微信掉线、通道凭据过期），
  不是 lifebrain 的问题。marker 会被兜底脚本清掉并重试。
- 索引对不上 → `lifebrain.py rebuild`，看 `dropped` 是否为空。
- 数据目录不对 → `lifebrain.py config` 看实际生效的 `data_dir` 和 `scope`。
- 读到别人的数据 → `lifebrain.py config` 看 `scope.mismatch`：为 true 说明用的是别人的脚本副本，
  改用 `$OCTOP_SKILLS_DIR` 下那份。
