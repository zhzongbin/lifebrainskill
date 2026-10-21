---
name: lifebrain
description: >-
  用 Markdown 记录每日工作生活：问答式引导写当日日志、追踪项目进度、每日定时提醒与漏记补录、
  周报月报与项目汇总。当用户说"记一下/记录一下/记今天/今天做了什么/周报/月报/项目汇总/项目进度"，
  或收到以 [lifebrain-reminder] 开头的定时提醒消息时使用。
  数据目录 ~/lifebrain（可在 config.yaml 修改）。所有读写必须走 scripts/lifebrain.py，不要手写 Markdown。
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
| 脚本入口 | `/home/zhzongbin/.octop/agents/JVZG0K/.octop/skills/lifebrain/scripts/lifebrain.py` |
| 数据目录 | `~/lifebrain`（可用环境变量 `LB_DATA_DIR` 或 `config.yaml:data_dir` 覆盖） |
| 周文件 | `~/lifebrain/journals/YYYY-Www.md` — **唯一真相源** |
| 项目注册表 | `~/lifebrain/projects.yaml` |
| 项目索引 | `~/lifebrain/projects/<name>.md` — **脚本生成，禁止手改** |
| 报告存档 | `~/lifebrain/reports/YYYY-Www.md` / `YYYY-MM.md` |

下面统一用这个别名（换成实际安装路径）：

```bash
LB="python3 /home/zhzongbin/.octop/agents/JVZG0K/.octop/skills/lifebrain/scripts/lifebrain.py"
```

所有子命令都往 stdout 输出 JSON，错误走 stderr 并返回非 0 —— **你只管解析 JSON，不用肉眼读 Markdown**。

| 命令 | 用途 |
|------|------|
| `$LB config` | 看当前数据目录、配置、脚本路径 |
| `$LB status [--date D] [--lookback N]` | 今天/指定日期是否已记录、条目、漏记日期、项目表、提醒 marker |
| `$LB show --date D \| --week YYYY-Www \| --month YYYY-MM` | 读某个日期/周/月的条目 |
| `$LB append ...` | 追加条目（**唯一的写入口**，写完自动重算索引） |
| `$LB project list \| add <名> \| set <名>` | 项目登记 / 修改 |
| `$LB report --week W \| --month M` | 按项目聚合（时長、条目、空缺日期） |
| `$LB rebuild` | 手动重算项目索引 |
| `$LB mark-reminder` | 写「今天已提醒」marker（防重复打扰） |

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

### 2. 提醒模式（cron 主动发起会话）

当消息以 `[lifebrain-reminder]` 开头时，说明这是一次**定时任务无头拉起**的会话（Octop cron → 主动发起 → 推送到用户通道）：

1. `$LB status --lookback 7`。
2. 今天**已记录** → 只回一句"今天已经记过了 ✓"，然后 `$LB mark-reminder`，结束。
3. 今天**没记录** → 简短问 1-2 个问题（通道消息要短，别一口气甩 5 个问题）："今天主要做了什么？"（附上已知的项目名提示）。
4. 用户回复后正常走模式 1 落盘，写完 `$LB mark-reminder`。
5. 有漏记日期 → 在最后一句里带一句"另外 10-19、10-20 还没记"，别单独再开一轮。

> 这个会话的输出会被推送到微信/QQ等通道，**第一句就要给到人话**，不要输出思考过程、JSON 或脚本回显。

### 3. 周报 / 月报 / 项目汇总

用户说"周报 / 月报 / 项目汇总"时：

1. `$LB report --week 2026-W43`（或 `--month 2026-10`）拿聚合数据；单项目时间线用 `$LB show --week` 再人工汇总。
2. 生成结构：
   - 周报/月报：按项目分组的时间投入、关键进展、阻塞；生活类单独一节。
   - 项目汇总：单项目全部条目时间线 + 阶段总结。
3. **先展示给用户看**，用户认可后再写文件：`~/lifebrain/reports/2026-W43.md` 或 `2026-10.md`，写完 `$LB rebuild`。
4. `empty_dates` 里的日期要如实写"无记录"，不要粉饰。

## 定时任务（Octop 侧）

每日提醒由 Octop 自己的调度器负责，**不需要**再拉 headless CLI：

```bash
octop --agent JVZG0K cron list
octop --agent JVZG0K cron create --trigger "cron:30 21 * * *" \
  --prompt "[lifebrain-reminder] 请执行 lifebrain skill 的提醒模式：检查今天是否已记录，未记录则进入每日记录问答。" \
  --task-type agent --fresh-thread
```

- `--task-type agent` 才会真正跑一次 agent 会话（`text` 只是把 prompt 原样推出去）。
- cron 任务绑定到创建它的那个通道会话，结果会推回该通道（微信 / QQ / dashboard）。
- 想知道更多（通道投递、兜底触发、时区）：见 `references/octop-setup.md`。

## 硬性规则

- 周文件是**唯一真相源**：任何数据只写进周文件，项目索引一律由脚本重算，**绝不直接编辑 `projects/*.md`**。
- 条目行必须是合法结构化行，解析失败的行会在 rebuild 时被丢弃 —— 写完即跑脚本，确认 `dropped` 为空。
- 项目必须先登记再写入；类别（work/life）决定报告里是否单独成节，别默认全当工作。
- 用户回答模糊时追问具体项目名和时长；宁可多问一句，不留脏数据。
- 不臆造时间与时长。不知道就写 `--:--`，并在追问里问出来。
- 报告写盘前先给用户看；用户没点头不写 `reports/`。
