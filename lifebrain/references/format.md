# 数据格式

> 本文是格式规约（真相源约定）。日常读写**不要手写 Markdown**，用 `scripts/lifebrain.py`，
> 它按本文格式落盘并自动重算索引。本文只在需要手工核对/修复时看。

## 目录
```
~/lifebrain/                # 配置壳（位置固定）
  config.yaml               # data_dir / reminder_time / timezone
<数据目录>/                  # 默认 ~/lifebrain，可被 config.yaml:data_dir 指到别处
  projects.yaml             # 项目注册表
  todos.yaml                # 待办清单（早间提醒带的就是它）
  journals/2026-W43.md      # 周文件（唯一真相源，ISO 周命名）
  projects/<name>.md        # 项目索引（脚本重算，只读）
  reports/                  # 周报月报存档
  .reminder-YYYY-MM-DD      # 当天提醒已发 marker（防止重复打扰）
```

## todos.yaml（目标清单）

早间提醒推送的清单就是它；晚间会话里用户说"明天要 X"时用 `todo add` 记进来，
说"某个目标推进了"时用 `append --todo <id>` 把进展挂上去。

```yaml
todos:
  t1:
    content: "把高密度电法测线数据反演完"   # 必填，这件事叫什么
    goal: "完成反演并出解释剖面报告"          # 可选，达成后是什么样子
    project: 永定大排铅锌矿物探               # 可选，关联项目
    estimate: "16h"                           # 可选，预计投入 16h / 2d / 4
    due: "2026-10-22"                         # 可选
    created: "2026-10-21"
    status: pending                           # pending | done
    done_at: "2026-10-22"                     # 完成后写入
    note: "范围扩大到 3 号异常体"              # 可选，完成备注/补充说明
```

- id 自增（`t1`、`t2`…），`done` 只是改 `status` 并写 `done_at`，记录留在文件里。
- `estimate` 写法：`16h` / `90m` / `2d`（1 天按 8h 折算）/ `16`（裸数字按小时）。
- **目标状态不因记了一条进展而改变** —— 只有显式 `todo done <id>` 才算完成。
- 格式是普通 YAML 双层映射，没有 PyYAML 也能读（脚本自带降级解析）。

## config.yaml

**位置固定在 `~/lifebrain/config.yaml`**（不随数据目录移动），里面的 `data_dir` 指向真正的数据目录。

```yaml
data_dir: ~/lifebrain      # 数据目录，可指向任意本机可写路径
reminder_time: "21:30"     # 仅供人读；实际调度在 Octop cron / crontab
timezone: "Asia/Shanghai"  # 可选；决定"今天"是哪天（默认取系统时区）
```

优先级：环境变量 `LB_DATA_DIR` > `config.yaml:data_dir` > `~/lifebrain`。
时区优先级：`LB_TIMEZONE` > `config.yaml:timezone` > 系统时区。

首次使用必须由用户确认数据目录后，用 `lifebrain.py init --data-dir <目录>` 生成，
不要手工建目录、也不要手工写这个配置文件。

## projects.yaml
```yaml
projects:
  web:
    category: work      # work | life
    goal: 一句话目标
    status: active      # active | paused | done
```

## 周文件格式
文件名 `YYYY-Www.md`（ISO 周，`date +%G-W%V`）。

```markdown
# 2026 第43周

## 2026-10-21
- 09:00 | web | 会议 | 1h | 需求评审
- 14:30 | web | 开发 | 2h | 完成登录 API 联调 | t1
- 20:00 | 健身 | 生活 | 1h | 跑步 5km

## 2026-10-20
（无记录）
```

条目行（pipe 分隔，**5 字段 + 可选第 6 段**）：
`- HH:MM | 项目名 | 类型 | 时长 | 内容 [| 目标id]`
- 时间：24h制，未知写 `--:--`
- 项目名：必须在 projects.yaml 中登记（生活类项目也登记，如 健身/阅读）
- 类型：自由短语（开发/会议/学习/生活…），聚合只按项目，类型仅展示
- 时长：`2h` / `30m` / `1.5h`
- 内容：一句话，不含 `|`
- **目标id（可选）**：形如 `t1`，指向 `todos.yaml` 里的目标。
  带了这个 id 的条目，**实际时长会计入该目标的 `actual_hours`**，
  并作为该目标的"最近进展"。不带就是普通记录。
  只有最后一段严格匹配 `t<数字>` 才会被当目标引用 —— 内容里偶然出现 `t3` 字样不会误判。
- 第 6 段是**后加的**，旧记录 5 字段格式完全兼容，不需要迁移。

日期小节无条目时必须显式写 `（无记录）`，供补录检测。

约定（脚本已保证）：
- 同一周文件内，日期小节**倒序**（最新在上）。
- 同一天内的条目按**时间正序**排列。
- 写入某天时若该天原为 `（无记录）`，该标记会被自动去掉。
- 追加到不存在的日期时自动新建小节。

## 项目索引（脚本生成，勿手改）
`projects/<name>.md`：状态头（来自 projects.yaml）+ 全部条目时间线 + 按周合计时长。
带目标引用的条目在行尾标 `[t1]`。
由 `lifebrain.py rebuild` 或 `scripts/rebuild_index.py` 重新生成；周文件改了必须重算。
