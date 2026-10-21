# lifebrain skill

用 Markdown 记录每日工作生活：**问答式引导写当日日志 → 追踪项目进度 → 每日定时提醒 → 周报/月报/项目汇总**。

用户永远不手写 Markdown，也永远不手写格式。agent 负责问，用户负责答，落盘由脚本完成。

```
~/lifebrain/               # 配置壳（位置固定）
  config.yaml              # data_dir / reminder_time / timezone
<数据目录>/                 # 默认 ~/lifebrain，可指到别处
  projects.yaml            # 项目注册表（名称 / 类别 work|life / 目标 / 状态）
  journals/2026-W43.md     # 周文件 —— 唯一真相源
  projects/<name>.md       # 项目索引 —— 脚本重算，只读
  reports/                 # 周报月报存档
```

## 首次使用：数据目录由用户定

agent **不会**默默把数据写进默认位置。第一次用的时候它会：

1. 跑 `lifebrain.py config`，看 `initialized` 是不是 false；
2. 提议默认目录 `~/lifebrain`，同时问用户要不要换成同步盘 / 笔记仓库 / 工作区路径；
3. **等用户明确点头**，才执行 `lifebrain.py init --data-dir "<用户选的目录>"`；
4. 建好目录结构、写 `~/lifebrain/config.yaml`、建本周周文件，再逐个带用户走
   「登记项目 → 记第一条 → 建每日提醒任务」。

已经有数据时直接接着用，不会擅自迁移或切换目录。

## 两种运行形态

| | omp 版（原始） | Octop 版 |
|---|---|---|
| 提醒触发 | system crontab → `omp -p "[lifebrain-reminder] ..."` headless 进程 | Octop 定时任务（`task_type=agent`）主动发起会话 → 推送到通道（微信/QQ/dashboard） |
| 数据读写 | agent 手写 Markdown | `scripts/lifebrain.py` 统一 CLI |
| 兜底脚本 | `scripts/reminder_omp.sh` | `scripts/reminder.sh` |

两边**共用同一份 `~/lifebrain` 数据**，谁记的对方都看得见。

## Octop 快速上手

```bash
# 1. 部署技能（幂等）
./lifebrain/scripts/install_octop.sh

# 2. 注册每日提醒定时任务（让 agent 用 cronjob_create 工具创建最好，会绑定当前通道会话并立即生效）
octop --agent JVZG0K cron create \
  --trigger "cron:30 21 * * *" \
  --prompt "[lifebrain-reminder] 请执行 lifebrain skill 的提醒模式：检查今天是否已记录，未记录则进入每日记录问答。" \
  --task-type agent --fresh-thread

# 3.（可选）system crontab 兜底，21:50 补一次，脚本自带去重
50 21 * * * ~/.octop/agents/JVZG0K/.octop/skills/lifebrain/scripts/reminder.sh >> ~/.lifebrain-reminder.log 2>&1
```

细节见 `lifebrain/references/octop-setup.md`。

## CLI

所有读写都走 `scripts/lifebrain.py`（输出 JSON）：

```bash
LB="python3 lifebrain/scripts/lifebrain.py"

$LB config                               # 当前生效的数据目录 / 是否已初始化
$LB init --data-dir ~/lifebrain          # 首次使用（用户确认目录后才跑）
$LB status --lookback 7                  # 今天记没记、漏记日期、项目表
$LB show --date 2026-10-21                # 读某天
$LB append --date 2026-10-21 --time 14:30 --project web --type 开发 --duration 2h --content "完成登录 API 联调"
$LB append --entry "09:00|web|会议|1h|需求评审" --entry "20:00|健身|生活|1h|跑步 5km"
$LB project add 健身 --category life --goal "保持体能"
$LB report --week 2026-W43                # 按项目聚合
$LB rebuild                               # 重算项目索引
$LB mark-reminder                         # 写"今天已提醒"marker
```

数据目录优先级：环境变量 `LB_DATA_DIR` > `config.yaml:data_dir` > `~/lifebrain`。
"今天是哪天"按 `LB_TIMEZONE` > `config.yaml:timezone` > 系统时区算。

## 铁律

- 周文件是唯一真相源；项目索引一律脚本重算，**绝不手改** `projects/*.md`。
- 项目必须先在 `projects.yaml` 登记再写入；类别决定报告里是否单独成节。
- 不臆造时间与时长：不知道写 `--:--`，并在追问里问出来。
- 报告写盘前先给用户看，没点头不写 `reports/`。
- 无记录的日子如实标注，不粉饰。

格式规约见 `lifebrain/references/format.md`。
