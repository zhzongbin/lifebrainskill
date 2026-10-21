---
name: lifebrain
description: >-
  用 Markdown 记录每日工作生活：问答式引导写当日日志、追踪项目进度、每日定时提醒与漏记补录、
  周报月报与项目汇总。当用户说"记一下/记录一下/记今天/今天做了什么/周报/月报/项目汇总/项目进度"，
  或收到以 [lifebrain-reminder] 开头的定时提醒消息时使用。
  数据目录默认 ~/lifebrain；首次使用时必须先向用户提议数据目录、得到确认后再初始化，
  之后所有读写必须走 scripts/lifebrain.py，不要手写 Markdown。
metadata:
  octop:
    emoji: "🧠"
    label:
      zh: "每日记录"
      en: "Life Brain"
    summary:
      zh: "问答式记录每天做了什么，追踪项目进度，生成周报月报。"
      en: "Guided daily journaling, project tracking, weekly and monthly reports."
---

# lifebrain — 每日记录 / 项目汇总

一句话：**用户永远不手写 Markdown。你负责问，他负责答；落盘一律走脚本。**

## 0. 环境速查（先看这段）

| 东西 | 位置 |
|------|------|
| 脚本入口 | **本 skill 目录**下的 `scripts/lifebrain.py`（安装路径因实例而异，见下方解析） |
| 配置文件 | `~/lifebrain/config.yaml` — **位置固定**，里面 `data_dir` 指向真正的数据目录 |
| 数据目录 | 默认 `~/lifebrain`；可用环境变量 `LB_DATA_DIR` 或 `config.yaml:data_dir` 覆盖 |
| 周文件 | `<数据目录>/journals/YYYY-Www.md` — **唯一真相源** |
| 项目注册表 | `<数据目录>/projects.yaml` |
| 项目索引 | `<数据目录>/projects/<name>.md` — **脚本生成，禁止手改** |
| 报告存档 | `<数据目录>/reports/YYYY-Www.md` / `YYYY-MM.md` |

> 路径解析顺序：环境变量 `LB_DATA_DIR` → `~/lifebrain/config.yaml` 的 `data_dir` → 默认 `~/lifebrain`。
> `$LB config` 会告诉你当前生效的是哪一个（`data_dir_source` 字段）。

### 先解析脚本路径（安装位置因实例而异，别写死）

```bash
LB_SCRIPT="$(ls -d ~/.octop/agents/*/.octop/skills/lifebrain/scripts/lifebrain.py 2>/dev/null | head -1)"
[ -z "$LB_SCRIPT" ] && LB_SCRIPT="$(ls -d .octop/skills/lifebrain/scripts/lifebrain.py 2>/dev/null | head -1)"
[ -z "$LB_SCRIPT" ] && LB_SCRIPT="$(ls -d ~/.octop/skills/lifebrain/scripts/lifebrain.py 2>/dev/null | head -1)"
[ -z "$LB_SCRIPT" ] && LB_SCRIPT="$(ls -d ~/.octop/skill-packages/*/skills/lifebrain/scripts/lifebrain.py 2>/dev/null | head -1)"
LB="python3 $LB_SCRIPT"
```

前三条是「装在 agent 工作区里」的情况，第四条是「从实例级全局技能包挂载过来」的情况
（包目录在 `~/.octop/skill-packages/<包ID>/skills/lifebrain/`，不需要复制到工作区也能跑）。
三条都没命中就 `find ~ -path '*skills/lifebrain/scripts/lifebrain.py' 2>/dev/null | head -1`。
解析一次后本轮对话复用 `$LB`；`$LB config` 会打印当前真实路径，可用它自检。

所有子命令都往 stdout 输出 JSON，错误走 stderr 并返回非 0 —— **你只管解析 JSON，不用肉眼读 Markdown**。

| 命令 | 用途 |
|------|------|
| `$LB config` | 看当前数据目录、配置、脚本路径、是否已初始化 |
| `$LB init --data-dir <目录>` | **首次使用**初始化：建目录结构 + 写 `~/lifebrain/config.yaml`（需用户确认后调用） |
| `$LB status [--date D] [--lookback N]` | 今天/指定日期是否已记录、条目、漏记日期、项目表、提醒 marker |
| `$LB show --date D \| --week YYYY-Www \| --month YYYY-MM` | 读某个日期/周/月的条目 |
| `$LB append ...` | 追加条目（**唯一的写入口**，写完自动重算索引） |
| `$LB project list \| add <名> \| set <名>` | 项目登记 / 修改 |
| `$LB report --week W \| --month M` | 按项目聚合（时長、条目、空缺日期） |
| `$LB rebuild` | 手动重算项目索引 |
| `$LB todo add --content "要做的事" [--project P] [--due 2026-10-22]` | 加一条待办（早间提醒会带上它） |
| `$LB todo list [--status pending\|done\|all]` | 看待办清单 |
| `$LB todo done <id>` / `$LB todo rm <id>` | 完成 / 删除待办 |
| `$LB mark-reminder` | 写「今天已提醒」marker（防重复打扰） |

## 首次使用：先和用户确认数据目录（硬性流程）

数据目录决定用户几十年的记录落在哪，**不能替用户默认决定，更不能默默建目录**。

**第一步：查状态**

```bash
$LB config     # 看 initialized 与 data_dir_source
```

- `initialized: true` → 已有数据，**直接用现有目录**，不要问要不要换，也不要擅自迁移。
- `initialized: false` → 走下面的协商流程。

**第二步：提议 + 等待用户确认**（这一轮只问，不写盘）

用一句人话把选项摆出来，然后**停下来等用户回话**：

> "lifebrain 要把每天的记录写到一个本地目录。默认用 `~/lifebrain`（纯 Markdown，随时能带走或同步）。
> 想换个地方吗？比如 iCloud/坚果云同步目录、某个代码仓库，或者你的笔记目录。你说一个，我就用它。"

要点：
- 默认值是**提议**，不是决定。用户没有明确回应就**不要**调 `init`、不要 mkdir、不要写任何文件。
- 用户给了别的路径就用用户的路径；用户说"随便/你定"才用默认 `~/lifebrain`。
- 路径必须是本机可写的绝对路径或 `~` 开头的路径。

**第三步：确认后才初始化**

```bash
$LB init --data-dir "<用户确认的目录>"        # 不传就是 ~/lifebrain
$LB init --data-dir "<目录>" --timezone Asia/Shanghai   # 可选：指定时区
```

`init` 会做四件事，输出 JSON 告诉你结果：
1. 建目录结构 `journals/ projects/ reports/` 和 `projects.yaml`；
2. 写固定配置文件 `~/lifebrain/config.yaml`（里面 `data_dir` 指向刚才的目录）；
3. 建本周周文件，把今天标成「（无记录）」；
4. 返回 `created` 列表和 `next_steps`。

**第四步：回报 + 引导下一步**

- 把 `data_dir` 用人话告诉用户："已经建好了，记录存在 `<目录>`。"
- `initialized` 为 true 且是首次 → 告诉用户"已有记录，我直接接着用"。
- `init` 返回 `next_steps` 里的三步（登记项目 / 记第一条 / 建每日提醒任务）要**逐个带用户走**，不要一口气甩三个问题。

**安全约束**

- 已初始化的目录再跑 `init` 是幂等的，不会覆盖数据。
- 想换目录必须加 `--force`（只改配置，**不搬数据**），而且必须用户明确说"要换"才能做。
- 配置文件位置永远是 `~/lifebrain/config.yaml`（固定"壳"），数据目录可以是任何地方。
- 用户说"删掉重来"前不要动已有文件。

## 模式

### 1. 每日记录（默认模式）

用户说"记一下 / 记录今天 / 今天做了什么"时：

1. `$LB status` 看今天记了没、有没有漏记的日子。
2. 如果有漏记日期，**先补录**："你 10-19 没记录，先补一下那天做了什么？"
3. 问答式引导，**每轮 2-3 个问题，最多 3 轮**：
   - "今天主要做了什么？"（按项目追问）
   - 涉及未登记项目 → 先问清类别（work/life）和一句话目标，登记后再继续
   - "有进展/阻塞值得记吗？"
   - 时间/时长不明确就追问："大概几点开始？做了多久？"
4. 每拿到一条完整信息就 `$LB append` 落盘（见下面的写法），不要攒到最后一次性写。
5. 全部记完，简短回一句"记好了，今天 3 条"。

**append 用法**（时间默认当前时刻，项目必须已在 `projects.yaml` 登记）：

```bash
$LB append --date 2026-10-21 --time 14:30 --project web --type 开发 --duration 2h --content "完成登录 API 联调"

# 一次写多条（每条 5 段，顺序：时间|项目|类型|时长|内容）
$LB append --date 2026-10-21 --entry "09:00|web|会议|1h|需求评审" --entry "20:00|健身|生活|1h|跑步 5km"
```

参数说明：`--time` 24 制（未知写 `--:--`）、`--duration` 形如 `2h` / `30m` / `1.5h`、`--content` 不能含 `|`。

**登记项目**（用户第一次提到的新项目）：

```bash
$LB project add web --category work --goal "一句话目标"
$LB project add 健身 --category life --goal "保持体能"
```

### 2. 晚间提醒模式（cron 主动发起会话）

当消息以 `[lifebrain-reminder]` 开头时，说明这是一次**定时任务无头拉起**的会话（Octop cron → 主动发起 → 推送到用户通道）：

1. `$LB status --lookback 7`。
2. 今天**已记录** → 只回一句"今天已经记过了 ✓"，然后 `$LB mark-reminder`，结束。
3. 今天**没记录** → 简短问 1-2 个问题（通道消息要短，别一口气甩 5 个问题）："今天主要做了什么？"（附上已知的项目名提示）。
4. 用户回复后正常走模式 1 落盘，写完 `$LB mark-reminder`。
5. 有漏记日期 → 在最后一句里带一句"另外 10-19、10-20 还没记"，别单独再开一轮。
6. 用户提到"明天/接下来要 X" → `$LB todo add --content "X" [--project P]` 记成待办，早间提醒会带上它。

> 这个会话的输出会被推送到微信/QQ等通道，**第一句就要给到人话**，不要输出思考过程、JSON 或脚本回显。

### 3. 早间模式（带今日 todo）

当消息以 `[lifebrain-morning]` 开头时，是**早上的定时会话**：把今天的待办清单推给用户。

1. `$LB status --lookback 2`（昨天记了什么、有没有漏记）和 `$LB todo list --status pending`（待办）。
2. 可选：`memory_search` 查一下跟这些项目相关的未完成事项，**最多补 1 条**，不要硬凑。
3. 输出控制在 5 行以内：
   - 第 1 行：早安 + 「今天的 todo：1) … 2) …」（最多 3 条；没待办就写"今天还没排待办"）。
   - 第 2 行：昨天做了什么（1 行内）。
   - 第 3 行：昨天有漏记就提一句。
4. **早间只读不写**：不要 append、不要 todo add，等用户回复再动手。
5. 用户回"1"或"先做 X" → 记成今天的条目（类型写"计划"），并把对应待办 `todo done <id>`。

> 晚间会话结束后，Octop 会在会话静默约 5 分钟后自动做记忆抽取（整理）；
> 所以第二天早上 `memory_search` 拿到的就是昨晚整理过的内容。详见 `references/octop-setup.md`。

### 4. 周报 / 月报 / 项目汇总

用户说"周报 / 月报 / 项目汇总"时：

1. `$LB report --week 2026-W43`（或 `--month 2026-10`）拿聚合数据；单项目时间线用 `$LB show --week` 再人工汇总。
2. 生成结构：
   - 周报/月报：按项目分组的时间投入、关键进展、阻塞；生活类单独一节。
   - 项目汇总：单项目全部条目时间线 + 阶段总结。
3. **先展示给用户看**，用户认可后再写文件：`~/lifebrain/reports/2026-W43.md` 或 `2026-10.md`，写完 `$LB rebuild`。
4. `empty_dates` 里的日期要如实写"无记录"，不要粉饰。

## 定时任务（Octop 侧）

每日提醒由 Octop 自己的调度器负责，**不需要**再拉 headless CLI。

**建提醒任务的 prompt 模板**（复制去用，路径是自解析的，换机器不用改）：

```
[lifebrain-reminder] 执行 lifebrain skill 的提醒模式（无头定时会话，回复会推送到用户所在通道）。

1. 先解析脚本路径：
   LB_SCRIPT="$(ls -d ~/.octop/agents/*/.octop/skills/lifebrain/scripts/lifebrain.py 2>/dev/null | head -1)"
   [ -z "$LB_SCRIPT" ] && LB_SCRIPT="$(ls -d .octop/skills/lifebrain/scripts/lifebrain.py 2>/dev/null | head -1)"
   [ -z "$LB_SCRIPT" ] && LB_SCRIPT="$(ls -d ~/.octop/skill-packages/*/skills/lifebrain/scripts/lifebrain.py 2>/dev/null | head -1)"
   两条都没命中就 find ~ -path '*skills/lifebrain/scripts/lifebrain.py' | head -1
2. 运行 python3 "$LB_SCRIPT" status --lookback 3，读 JSON。
2.5 若 status 里 initialized=false（用户还没确认数据目录）→ 只回一句「还没定记录放哪儿，回我一句我就给你建好」
   然后结束；**不要自己建目录、不要写数据**。
3. recorded=true → 只回一句「今天已经记过了 ✓」+ 今天的条目，不再问问题。
4. recorded=false → 1-2 句话问「今天主要做了什么？大概多久？」，用户回复后用 append 写入
   （项目要先在 projects.yaml 登记；脚本会自动重建索引）。
5. missing_dates 非空 → 末尾最多提一句漏记（只提最近一天）。
6. 全程中文、口语化，总共不超过 3 句——这是推送到通道的消息，不是长报告。
7. 结束前运行 python3 "$LB_SCRIPT" mark-reminder，防止兜底 crontab 重复打扰。
8. 用户提到「明天/接下来要做什么」→ 用 todo add 记成待办（早间提醒会带上）。
```

**早间任务的 prompt 模板**（配合 `cron:30 7 * * *` 这类早上触发）：

```
[lifebrain-morning] 执行 lifebrain skill 的早间模式（无头定时会话，回复会推送到用户所在通道）。

1. 先解析脚本路径（四个候选，取第一个存在的）：
   LB_SCRIPT="$(ls -d ~/.octop/agents/*/.octop/skills/lifebrain/scripts/lifebrain.py 2>/dev/null | head -1)"
   [ -z "$LB_SCRIPT" ] && LB_SCRIPT="$(ls -d .octop/skills/lifebrain/scripts/lifebrain.py 2>/dev/null | head -1)"
   [ -z "$LB_SCRIPT" ] && LB_SCRIPT="$(ls -d ~/.octop/skills/lifebrain/scripts/lifebrain.py 2>/dev/null | head -1)"
   [ -z "$LB_SCRIPT" ] && LB_SCRIPT="$(ls -d ~/.octop/skill-packages/*/skills/lifebrain/scripts/lifebrain.py 2>/dev/null | head -1)"
2. 运行 python3 "$LB_SCRIPT" status --lookback 2，读 JSON。
3. 运行 python3 "$LB_SCRIPT" todo list --status pending，读 JSON。
4. 可选：用 memory_search 查跟这些项目相关的未完成事项，最多补 1 条，不要硬凑。
5. 输出不超过 5 行：
   - 第 1 行：早安 + 「今天的 todo：1) … 2) …」（最多 3 条；没有待办写「今天还没排待办」）
   - 第 2 行：昨天做了什么（1 行以内）
   - 第 3 行：昨天有漏记就提一句
6. 全程中文、口语化，第一句就是人话，不要 JSON、不要脚本回显。
7. 早间只读不写：不要 append、不要 todo add，等用户回复再动手。
```

**建任务**（`--agent` 换成自己的 agent id）：

```bash
octop --agent <AGENT_ID> cron list
octop --agent <AGENT_ID> cron create --trigger "cron:30 21 * * *" \
  --prompt "<上面的模板>" \
  --task-type agent --fresh-thread

# 早间带 todo 的那条
octop --agent <AGENT_ID> cron create --trigger "cron:30 7 * * *" \
  --prompt "<早间模板>" \
  --task-type agent --fresh-thread
```

- `--task-type agent` 才会真正跑一次 agent 会话（`text` 只是把 prompt 原样推出去）。
- **通道绑定**：在聊天窗口里让 agent 用 `cronjob_create` 建任务，会自动绑到当前通道（微信/QQ），结果推回那里；
  用上面的 CLI 建则会绑到 dashboard（网页控制台）。CLI 建完需要 `octop service restart` 才被调度器加载。
- 时区跟随实例配置 `default_timezone`（默认 Asia/Shanghai），不是机器系统时区。
- 想知道更多（通道投递、兜底触发、实例内共享包）：见 `references/octop-setup.md`。

## 硬性规则

- **数据目录由用户定**：首次使用必须先提议、拿到明确确认再 `init`；没确认前不建目录、不写盘。
- 周文件是**唯一真相源**：任何数据只写进周文件，项目索引一律由脚本重算，**绝不直接编辑 `projects/*.md`**。
- 条目行必须是合法结构化行，解析失败的行会在 rebuild 时被丢弃 —— 写完即跑脚本，确认 `dropped` 为空。
- 项目必须先登记再写入；类别（work/life）决定报告里是否单独成节，别默认全当工作。
- 用户回答模糊时追问具体项目名和时长；宁可多问一句，不留脏数据。
- 不臆造时间与时长。不知道就写 `--:--`，并在追问里问出来。
- 报告写盘前先给用户看；用户没点头不写 `reports/`。
