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
| 配置文件 | `<scope 根>/config.yaml` — 位置随 scope 变（见下方「多用户隔离」） |
| 数据目录 | 默认 = scope 根；可用 `LB_DATA_DIR` 或 `config.yaml:data_dir` 覆盖 |
| 周文件 | `<数据目录>/journals/YYYY-Www.md` — **唯一真相源** |
| 项目注册表 | `<数据目录>/projects.yaml` |
| 项目索引 | `<数据目录>/projects/<name>.md` — **脚本生成，禁止手改** |
| 报告存档 | `<数据目录>/reports/YYYY-Www.md` / `YYYY-MM.md` |

> 路径解析顺序：环境变量 `LB_DATA_DIR` → `<scope 根>/config.yaml` 的 `data_dir` → scope 根。
> `$LB config` 会告诉你当前生效的是哪一个（`data_dir_source` 字段）。

### 多用户隔离（同一台机器多用户时必读）

一台 Octop 主机上可能同时跑**多个用户**（每个用户一个 agent），但它们共用同一个 Unix 账号、
同一个 `$HOME`。**`~/lifebrain` 不是按用户隔离的**——不加区分地读它，就会读到别人的记录。

脚本按 **scope** 分流（scope = 你的 agent id）：

| 环节 | 规则 |
|------|------|
| scope 判定 | `$LIFEBRAIN_SCOPE` → `$LIFEBRAIN_AGENT` → `$OCTOP_AGENT_ID` → 当前目录是不是 `~/.octop/agents/<id>/` → 都没有就用技能副本里的 `scope.json` |
| 数据根 | 认领了 `~/lifebrain` 的那个 scope（`config.yaml` 里 `owner_scope`）用 `~/lifebrain`；其他 scope 用 `~/.lifebrain/scopes/<scope>`，**互不重叠** |
| 二次校验 | 脚本比对「自己属于谁」和「当前进程属于谁」，不一致就往 stderr 打警告，并**按当前进程解析数据目录**——跑错脚本也读不到别人的数据 |
| 显式覆盖 | `LIFEBRAIN_HOME=<目录>` 可以把整个根目录钉死 |

`$LB config` 的 `scope` 字段会打印 `scope` / `runtime_scope` / `declared_scope` / `home` /
`owner_scope` / `mismatch`。**任何一次读写前先确认 `runtime_scope` 是自己的 agent id**。

### 先解析脚本路径（安装位置因实例而异，别写死）

**只取当前 agent 自己目录里的那份**——不要用 `ls ~/.octop/agents/*`（那会挑到别人的 agent）：

```bash
LB_SCRIPT=""
[ -n "${OCTOP_SKILLS_DIR:-}" ] && [ -f "$OCTOP_SKILLS_DIR/lifebrain/scripts/lifebrain.py" ] && LB_SCRIPT="$OCTOP_SKILLS_DIR/lifebrain/scripts/lifebrain.py"
[ -z "$LB_SCRIPT" ] && [ -f "$PWD/.octop/skills/lifebrain/scripts/lifebrain.py" ] && LB_SCRIPT="$PWD/.octop/skills/lifebrain/scripts/lifebrain.py"
[ -z "$LB_SCRIPT" ] && [ -n "${OCTOP_AGENT_ID:-}" ] && [ -f "$HOME/.octop/agents/$OCTOP_AGENT_ID/.octop/skills/lifebrain/scripts/lifebrain.py" ] && LB_SCRIPT="$HOME/.octop/agents/$OCTOP_AGENT_ID/.octop/skills/lifebrain/scripts/lifebrain.py"
[ -z "$LB_SCRIPT" ] && LB_SCRIPT="$(ls -d "$HOME"/.octop/skill-packages/*/skills/lifebrain/scripts/lifebrain.py 2>/dev/null | head -1)"
LB="python3 $LB_SCRIPT"
```

前三条是「装在 agent 工作区里」的情况（`$OCTOP_SKILLS_DIR` 由 Octop 注入，指向**本 agent**
的技能目录），第四条是「从实例级全局技能包挂载过来」的情况
（包目录在 `~/.octop/skill-packages/<包ID>/skills/lifebrain/`，不需要复制到工作区也能跑）。
**没有第五个"兜底全盘扫描"**——扫 `~/.octop/agents/*` 一定会挑到别人的 agent。
四条都没命中就 `$LB config` 确认技能是否装好，别硬跑。

解析一次后本轮对话复用 `$LB`；`$LB config` 会打印当前真实路径和 scope，可用它自检。

> 万一还是拿到了别的 agent 的脚本副本，**数据不会串**：脚本会按当前进程的 scope 重新解析数据目录，
> 并在 stderr 打出 mismatch 警告。

所有子命令都往 stdout 输出 JSON，错误走 stderr 并返回非 0 —— **你只管解析 JSON，不用肉眼读 Markdown**。

| 命令 | 用途 |
|------|------|
| `$LB config` | 看当前数据目录、配置、脚本路径、是否已初始化 |
| `$LB init --data-dir <目录>` | **首次使用**初始化：建目录结构 + 写 `~/lifebrain/config.yaml`（需用户确认后调用） |
| `$LB status [--date D] [--lookback N]` | 今天/指定日期是否已记录、条目、漏记日期、项目表、**带进展的待办目标** |
| `$LB show --date D \| --week YYYY-Www \| --month YYYY-MM` | 读某个日期/周/月的条目 |
| `$LB append ... [--todo t1]` | 追加条目（**唯一的写入口**，写完自动重算索引）；`--todo` 把这条记为某目标的进展 |
| `$LB project list \| add <名> \| set <名>` | 项目登记 / 修改 |
| `$LB report --week W \| --month M` | 按项目**和目标**聚合（时长、条目、空缺日期） |
| `$LB rebuild` | 手动重算项目索引 |
| `$LB todo add --content "要做的事" [--goal G] [--project P] [--estimate 16h] [--due D]` | 加一条**目标**（早间提醒会带上它） |
| `$LB todo list [--status pending\|done\|all]` | 看目标清单，**带累计投入、最近进展、逾期天数** |
| `$LB todo review [--days 7]` | 目标进展复盘：今天该聚焦哪几个、投入 vs 预计、卡住没动的 |
| `$LB todo update <id> [--goal G] [--estimate E] [--due D]` | 改目标的定义 / 预计投入 / 截止日 |
| `$LB todo done <id> [--note "…"]` / `$LB todo rm <id>` | 完成 / 删除目标 |
| `$LB mark-reminder [--clear]` | 写 / 清「今天已提醒」marker（防重复打扰；投递失败时用 `--clear` 清掉以便重试） |

### 目标 vs 待办：这是两回事

`todos.yaml` 里的一条不是"待办清单里的一行字"，而是一个**可追踪的目标**：

| 字段 | 含义 |
|------|------|
| `content` | 这件事叫什么（如"永定大排高密度电法测线"） |
| `goal` | 达成后是什么样子（如"完成测线并出解释剖面报告"） |
| `project` | 挂在哪个项目下（项目必须先在 `projects.yaml` 登记） |
| `estimate` | 预计投入，如 `16h` / `2d`（1 天按 8h 折算）/ `4`（裸数字按小时） |
| `due` | 期望完成日期；过期会被标成 `rolled_over` |
| `status` / `done_at` | `pending` / `done`，**只有显式 `todo done` 才会完成** |
| `note` | 完成备注或补充说明 |

**实际投入从周文件里来**：某条工作记录只要在行尾挂了 `| t1`，就计入该目标的累计时长。
周文件条目格式（**第 6 段可选**，旧记录 5 段完全兼容）：

```markdown
- 09:30 | web | 开发 | 2h | 完成登录联调 | t1     ← 关联到目标 t1
- 20:00 | 健身 | 生活 | 1h | 跑步 5km              ← 普通记录，无目标
```


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
2. 写配置文件 `<scope 根>/config.yaml`（里面 `data_dir` 指向刚才的目录；
   若这个 scope 认领了 `~/lifebrain`，`config.yaml` 里会多一行 `owner_scope: <你的 agent id>`）；
3. 建本周周文件，把今天标成「（无记录）」；
4. 返回 `created` 列表和 `next_steps`。

**第四步：回报 + 引导下一步**

- 把 `data_dir` 用人话告诉用户："已经建好了，记录存在 `<目录>`。"
- `initialized` 为 true 且是首次 → 告诉用户"已有记录，我直接接着用"。
- `init` 返回 `next_steps` 里的三步（登记项目 / 记第一条 / 建每日提醒任务）要**逐个带用户走**，不要一口气甩三个问题。

**安全约束**

- 已初始化的目录再跑 `init` 是幂等的，不会覆盖数据。
- 想换目录必须加 `--force`（只改配置，**不搬数据**），而且必须用户明确说"要换"才能做。
- 配置文件在 scope 根目录（`~/lifebrain` 或 `~/.lifebrain/scopes/<scope>`）里，**每个 scope 一份**，互不干扰。
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

# 一次写多条（每条 5 段，顺序：时间|项目|类型|时长|内容；第 6 段可选，为目标 id）
$LB append --date 2026-10-21 --entry "09:00|web|会议|1h|需求评审" --entry "20:00|健身|生活|1h|跑步 5km"

# 这条是目标 t1 的进展：时长会累计进 t1 的 actual_hours
$LB append --date 2026-10-21 --time 14:30 --project web --type 开发 --duration 2h --content "完成登录 API 联调" --todo t1
```

参数说明：`--time` 24 制（未知写 `--:--`）、`--duration` 形如 `2h` / `30m` / `1.5h`、`--content` 不能含 `|`。
`--todo` 指向的目标必须已存在，否则脚本报错退出（不会写出脏数据）。

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
6. 用户提到"明天/接下来要 X" → `$LB todo add --content "X" [--project P] [--goal G]` 记成**新目标**，早间提醒会带上它。
7. 用户提到的是**已有目标**的进展（"t1 那边解释剖面做完了"）→ 不要新建 todo，
   改成 `$LB append ... --todo t1` 把进展挂到那个目标上；必要时 `$LB todo update t1 --estimate ...` 修正预计投入。

> 这个会话的输出会被推送到微信/QQ等通道，**第一句就要给到人话**，不要输出思考过程、JSON 或脚本回显。

### 2.5 目标进展收集（晚间与早间共用）

目标是**要被追踪的**，不是一次性提醒。用户反馈进展时，按下面三步走：

1. **认领目标**：先 `$LB todo list --status pending` 看有没有对应的目标（按 `content` / `project` 匹配）。
   - 已有 → 用它的 id（`t1`、`t2`…）。
   - 没有 → `$LB todo add --content "…" --project P --goal "达成后是什么样"` 建一个。
2. **把进展写成条目**：`$LB append --date <今天> --time <时间> --project P --type <类型> --duration <时长> --content "<进展>" --todo <id>`。
   - 这条记录同时是"今天做了什么"和"目标 t1 的进展"，**一次写入两处都成立**。
   - 只有用户明确说"这条对不上任何目标"才不加 `--todo`。
3. **补阻塞信息（可选）**：用户说"卡住了/被挡了" → 追加一条条目，type 写"阻塞"，
   同样挂 `--todo <id>`；这样 `todo list` 的 `last_progress` 就是最新的真实状态。

**判断该不该新建目标**：只有用户说"要/打算/接下来做"这种**未来意图**才 `todo add`；
用户说"做完了/推进了/卡住了"这种**已发生的事实**一律 `append --todo`。

### 3. 早间模式（带今日目标与进展）

当消息以 `[lifebrain-morning]` 开头时，是**早上的定时会话**：把今天的目标清单和进展推给用户。

1. `$LB status --lookback 2`（昨天记了什么、有没有漏记）和 `$LB todo review --days 7`（目标进展复盘）。
2. 可选：`memory_search` 查一下跟这些项目相关的未完成事项，**最多补 1 条**，不要硬凑。
3. 输出控制在 5 行以内：
   - 第 1 行：早安 + 「今天的目标：1) … 2) …」（最多 3 条；没目标就写"今天还没排目标"）。
   - 第 2 行：**进展**——每个目标最近一次进展 + 累计投入（如"t1 测线：上次到解释剖面，累计 3.5h/16h"）。
   - 第 3 行：昨天做了什么（1 行内）+ 有漏记就提一句。
   - 第 4 行（可选）：**最多问一个**问题——"t1 那边今天想先推哪一步？"或"t2 有什么新进展/卡点？"
     只问一个目标，不要变成问卷。
4. **早间只读不写**：不要 append、不要 todo add、不要 todo done，等用户回复再动手。
5. 用户回"1"或"先做 X" → 记成今天的条目（类型写"计划"）并挂 `--todo <对应 id>`；
   用户回"完成了 X" → 记成条目挂 `--todo`，如果他明确说这个目标做完了，再 `todo done <id>`。

> 晚间会话结束后，Octop 会在会话静默约 5 分钟后自动做记忆抽取（整理）；
> 所以第二天早上 `memory_search` 拿到的就是昨晚整理过的内容。详见 `references/octop-setup.md`。

### 4. 周报 / 月报 / 项目汇总

用户说"周报 / 月报 / 项目汇总"时：

1. `$LB report --week 2026-W43`（或 `--month 2026-10`）拿聚合数据；单项目时间线用 `$LB show --week` 再人工汇总。
2. 生成结构：
   - 周报/月报：按项目分组的时间投入、关键进展、阻塞；生活类单独一节。
   - 项目汇总：单项目全部条目时间线 + 阶段总结。
   - **目标进展**（`by_todo`）：每个目标的投入时长、最近进展、逾期天数 —— 有目标的部分单独成节，
     写清"推进到哪一步 / 还差多少 / 卡在哪"。
3. **先展示给用户看**，用户认可后再写文件：`~/lifebrain/reports/2026-W43.md` 或 `2026-10.md`，写完 `$LB rebuild`。
4. `empty_dates` 里的日期要如实写"无记录"，不要粉饰。

## 定时任务（Octop 侧）

每日提醒由 Octop 自己的调度器负责，**不需要**再拉 headless CLI。

**建提醒任务的 prompt 模板**（复制去用，路径是自解析的，换机器不用改）：

```
[lifebrain-reminder] 执行 lifebrain skill 的提醒模式（无头定时会话，回复会推送到用户所在通道）。

1. 先解析脚本路径（只取本 agent 的那份，不要用 ls ~/.octop/agents/*）：
   LB_SCRIPT=""
   [ -n "${OCTOP_SKILLS_DIR:-}" ] && [ -f "$OCTOP_SKILLS_DIR/lifebrain/scripts/lifebrain.py" ] && LB_SCRIPT="$OCTOP_SKILLS_DIR/lifebrain/scripts/lifebrain.py"
   [ -z "$LB_SCRIPT" ] && [ -f "$PWD/.octop/skills/lifebrain/scripts/lifebrain.py" ] && LB_SCRIPT="$PWD/.octop/skills/lifebrain/scripts/lifebrain.py"
   [ -z "$LB_SCRIPT" ] && [ -n "${OCTOP_AGENT_ID:-}" ] && [ -f "$HOME/.octop/agents/$OCTOP_AGENT_ID/.octop/skills/lifebrain/scripts/lifebrain.py" ] && LB_SCRIPT="$HOME/.octop/agents/$OCTOP_AGENT_ID/.octop/skills/lifebrain/scripts/lifebrain.py"
   [ -z "$LB_SCRIPT" ] && LB_SCRIPT="$(ls -d "$HOME"/.octop/skill-packages/*/skills/lifebrain/scripts/lifebrain.py 2>/dev/null | head -1)"
   都还没命中就 find ~ -path '*skills/lifebrain/scripts/lifebrain.py' | head -1
1.5 跑一次 python3 "$LB_SCRIPT" config，确认 scope.runtime_scope 是本 agent（不是别人的）；
   不一致就换用 $OCTOP_SKILLS_DIR 下那份，绝不读别人的数据。
2. 运行 python3 "$LB_SCRIPT" status --lookback 3，读 JSON。
2.5 若 status 里 initialized=false（用户还没确认数据目录）→ 只回一句「还没定记录放哪儿，回我一句我就给你建好」
   然后结束；**不要自己建目录、不要写数据**。
3. recorded=true → 只回一句「今天已经记过了 ✓」+ 今天的条目，不再问问题。
4. recorded=false → 1-2 句话问「今天主要做了什么？大概多久？」，用户回复后用 append 写入
   （项目要先在 projects.yaml 登记；脚本会自动重建索引）。
5. missing_dates 非空 → 末尾最多提一句漏记（只提最近一天）。
6. 全程中文、口语化，总共不超过 3 句——这是推送到通道的消息，不是长报告。
7. 结束前运行 python3 "$LB_SCRIPT" mark-reminder，防止兜底 crontab 重复打扰。
   （marker 只表示「内容已生成」；兜底脚本会查 cron 的 last_status，投递失败时它会清掉 marker 重试。）
8. 用户提到「明天/接下来要做什么」→ 用 todo add 记成新目标（早间提醒会带上）。
9. 用户说的是「某个已有目标推进了/卡住了」→ 不要新建 todo，用 append --todo <id> 把进展挂到那个目标上。
```

**早间任务的 prompt 模板**（配合 `cron:30 7 * * *` 这类早上触发）：

```
[lifebrain-morning] 执行 lifebrain skill 的早间模式（无头定时会话，回复会推送到用户所在通道）。

1. 先解析脚本路径（只取本 agent 的那份，不要用 ls ~/.octop/agents/*）：
   LB_SCRIPT=""
   [ -n "${OCTOP_SKILLS_DIR:-}" ] && [ -f "$OCTOP_SKILLS_DIR/lifebrain/scripts/lifebrain.py" ] && LB_SCRIPT="$OCTOP_SKILLS_DIR/lifebrain/scripts/lifebrain.py"
   [ -z "$LB_SCRIPT" ] && [ -f "$PWD/.octop/skills/lifebrain/scripts/lifebrain.py" ] && LB_SCRIPT="$PWD/.octop/skills/lifebrain/scripts/lifebrain.py"
   [ -z "$LB_SCRIPT" ] && [ -n "${OCTOP_AGENT_ID:-}" ] && [ -f "$HOME/.octop/agents/$OCTOP_AGENT_ID/.octop/skills/lifebrain/scripts/lifebrain.py" ] && LB_SCRIPT="$HOME/.octop/agents/$OCTOP_AGENT_ID/.octop/skills/lifebrain/scripts/lifebrain.py"
   [ -z "$LB_SCRIPT" ] && LB_SCRIPT="$(ls -d "$HOME"/.octop/skill-packages/*/skills/lifebrain/scripts/lifebrain.py 2>/dev/null | head -1)"
1.5 跑一次 python3 "$LB_SCRIPT" config，确认 scope.runtime_scope 是本 agent（不是别人的）；
   不一致就换用 $OCTOP_SKILLS_DIR 下那份，绝不读别人的数据。
2. 运行 python3 "$LB_SCRIPT" status --lookback 2，读 JSON。
3. 运行 python3 "$LB_SCRIPT" todo review --days 7，读 JSON（目标进展复盘：今天该聚焦哪几个、投入 vs 预计）。
4. 可选：用 memory_search 查跟这些项目相关的未完成事项，最多补 1 条，不要硬凑。
5. 输出不超过 5 行：
   - 第 1 行：早安 + 「今天的目标：1) … 2) …」（最多 3 条；没有目标写「今天还没排目标」）
   - 第 2 行：进展——每个目标最近一次进展 + 累计投入（如「t1 测线：上次到解释剖面，累计 3.5h/16h」）
   - 第 3 行：昨天做了什么（1 行以内）
   - 第 4 行：有漏记就提一句；然后**最多问一个问题**——挑一个目标问「今天想先推哪一步 / 有没有新进展或卡点」
     （只问一个，不要变问卷）
6. 全程中文、口语化，第一句就是人话，不要 JSON、不要脚本回显。
7. 早间只读不写：不要 append、不要 todo add、不要 todo done，等用户回复再动手。
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

- **不串数据**：同一台机器上可能有别的用户在跑。**只用本 agent 目录下的脚本**
  （`$OCTOP_SKILLS_DIR` 指向它），读写前用 `$LB config` 确认 `scope.runtime_scope` 是自己；
  任何情况下都不要去读 `~/lifebrain` 里不属于自己的那份。
- **数据目录由用户定**：首次使用必须先提议、拿到明确确认再 `init`；没确认前不建目录、不写盘。
- 周文件是**唯一真相源**：任何数据只写进周文件，项目索引一律由脚本重算，**绝不直接编辑 `projects/*.md`**。
- 条目行必须是合法结构化行，解析失败的行会在 rebuild 时被丢弃 —— 写完即跑脚本，确认 `dropped` 为空。
- **目标只有用户明确说"这个做完了"才 `todo done`**；记了一条进展 ≠ 目标完成，不要自动结案。
- **"要做的"建目标，"做过的"记条目**：
  - 未来意图 → `todo add`（带 `--goal` 写清达成标准）
  - 已发生事实 → `append --todo <id>`，挂到对应目标上
- 别把普通小事硬塞进目标。挂 `--todo` 只在用户明确说"这是那件事的进展"时才做。
- 项目必须先登记再写入；类别（work/life）决定报告里是否单独成节，别默认全当工作。
- 用户回答模糊时追问具体项目名和时长；宁可多问一句，不留脏数据。
- 不臆造时间与时长。不知道就写 `--:--`，并在追问里问出来。
- **不替用户补历史关联**：已有周文件里的旧记录不会被自动挂上目标 id；要补也得用户确认过。
- 报告写盘前先给用户看；用户没点头不写 `reports/`。
