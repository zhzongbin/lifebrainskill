# 数据格式

> 本文是格式规约（真相源约定）。日常读写**不要手写 Markdown**，用 `scripts/lifebrain.py`，
> 它按本文格式落盘并自动重算索引。本文只在需要手工核对/修复时看。

## 目录
```
~/lifebrain/
  config.yaml          # data_dir, reminder_time
  projects.yaml        # 项目注册表
  journals/2026-W43.md # 周文件（唯一真相源，ISO 周命名）
  projects/<name>.md   # 项目索引（脚本重算，只读）
  reports/             # 周报月报存档
  .reminder-YYYY-MM-DD # 当天提醒已发 marker（防止重复打扰）
```

## config.yaml
```yaml
data_dir: ~/lifebrain
reminder_time: "21:30"   # 仅供人读；实际调度在 Octop cron / crontab
```

优先级：环境变量 `LB_DATA_DIR` > `config.yaml:data_dir` > `~/lifebrain`。

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
- 14:30 | web | 开发 | 2h | 完成登录 API 联调
- 20:00 | 健身 | 生活 | 1h | 跑步 5km

## 2026-10-20
（无记录）
```

条目行（pipe 分隔，5 字段）：
`- HH:MM | 项目名 | 类型 | 时长 | 内容`
- 时间：24h制，未知写 `--:--`
- 项目名：必须在 projects.yaml 中登记（生活类项目也登记，如 健身/阅读）
- 类型：自由短语（开发/会议/学习/生活…），聚合只按项目，类型仅展示
- 时长：`2h` / `30m` / `1.5h`
- 内容：一句话，不含 `|`

日期小节无条目时必须显式写 `（无记录）`，供补录检测。

约定（脚本已保证）：
- 同一周文件内，日期小节**倒序**（最新在上）。
- 同一天内的条目按**时间正序**排列。
- 写入某天时若该天原为 `（无记录）`，该标记会被自动去掉。
- 追加到不存在的日期时自动新建小节。

## 项目索引（脚本生成，勿手改）
`projects/<name>.md`：状态头（来自 projects.yaml）+ 全部条目时间线 + 按周合计时长。
由 `lifebrain.py rebuild` 或 `scripts/rebuild_index.py` 重新生成；周文件改了必须重算。
