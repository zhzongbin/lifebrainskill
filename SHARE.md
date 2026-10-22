# 分发指南：把 lifebrain 分享给别的 Octop 用户

本文给**要把这个 skill 分享出去的人**看。分四种场景，选一条即可。

## 0. 先搞清楚包里有什么

```
lifebrain/                 ← 这才是「一个 skill 目录」，SKILL.md 在这一层
  SKILL.md
  references/format.md     ← 数据格式规约
  references/octop-setup.md← Octop 运行时细节（通道投递、时区、兜底）
  scripts/lifebrain.py     ← 核心 CLI（status/show/append/project/report/rebuild/mark-reminder/todo）
  scripts/rebuild_index.py ← 项目索引重算
  scripts/scope.py         ← scope 解析（多用户隔离的唯一入口）
  scripts/reminder.sh      ← Octop 版提醒入口（system cron → octop cron run-now）
  scripts/reminder_omp.sh  ← 原 omp 无头 CLI 版（其他 agent 运行时用）
  scripts/install_octop.sh ← 一键部署到 Octop 技能目录
```

关键点：**分享的是 `lifebrain/` 这一层**（SKILL.md 必须在顶层）。
路径全是自解析的，换机器/换 agent/换实例都不用改。

> **包里没有任何数据。** 数据永远在各自机器的 scope 目录里，不会随包走。
> 但"目录长什么样"取决于**谁在跑**——所以对方装完必须跑一次
> `install_octop.sh`（它会写 `scope.json`、分配数据根）。详见下面的「多用户隔离」。

---

## 场景 A：GitHub 仓库（最通用，推荐）

对方是任意 Octop 实例（或任何支持 SKILL.md 的 agent 运行时）都能装。

**对方一条命令**：

```bash
# 方式 1：Octop 自带的技能管理（推荐，会做安全检查）
python3 ~/.octop/agents/<AGENT>/.octop/_builtin_skills/skill-manager/scripts/manage_skills.py \
  install "git+https://github.com/zhzongbin/lifebrainskill" --subpath "lifebrain"

# 方式 2：直接用本仓库自带的安装脚本
git clone --depth 1 https://github.com/zhzongbin/lifebrainskill.git
bash lifebrainskill/lifebrain/scripts/install_octop.sh --agent <AGENT_ID>
```

**对方装完之后还要做一件事**（提醒任务）：

- 想让提醒推到**微信/QQ**：在聊天窗口里跟自己的 agent 说「用 lifebrain 技能建一个每天 21:30 的提醒任务」——
  agent 用 `cronjob_create` 建的任务会**自动绑定当前通道**，结果推回那里。
- 想要早上的目标提醒，再建一条（早 7:30，推今天的目标 + 最近进展 + 累计投入）：
  「用 lifebrain 技能建一个每天早上 7:30 的待办提醒任务」。
- 用 CLI 建的任务会绑到 dashboard（网页控制台），且需要 `octop service restart` 才被调度器加载：
  ```bash
  octop --agent <AGENT_ID> cron create --trigger "cron:30 21 * * *" \
    --prompt "$(bash lifebrain/scripts/install_octop.sh --print-prompt)" \
    --task-type agent --fresh-thread

  octop --agent <AGENT_ID> cron create --trigger "cron:30 7 * * *" \
    --prompt "$(bash lifebrain/scripts/install_octop.sh --print-prompt morning)" \
    --task-type agent --fresh-thread
  ```

> 时间按**实例时区**解释（Octop 配置 `default_timezone`，默认 Asia/Shanghai），
> 不是机器的系统时区——很多机器是 UTC，直接 `cron:30 21 * * *` 会差 8 小时。

---

## 多用户隔离（同一台机器上多人时必读）

> **一个微信号 ≠ 一个 agent ≠ 一个数据目录。** 这两件事是分开的。

### 同一个 Octop 实例里的其他用户

Octop 的每个用户有自己的 agent，但**所有 agent 跑在同一个 Unix 账号下、共用同一个 `$HOME`**。
所以 `~/lifebrain` **不是**天然按用户隔离的——不加区分地读它，就会读到别人的记录。

本 skill 用 **scope** 机制解决：

| 概念 | 含义 |
|------|------|
| scope | "我是谁"：一个 agent id（`$OCTOP_AGENT_ID` / `$LIFEBRAIN_SCOPE` / `$LIFEBRAIN_AGENT` / 工作目录） |
| scope 根 | 这个 scope 的数据目录 |
| `scope.json` | 装在**技能目录**里，声明"这份副本属于谁、数据放哪" |

分配规则：

- 认领了 legacy `~/lifebrain` 的那个 scope（`config.yaml` 里的 `owner_scope`）→ 用 `~/lifebrain`；
- 其他 scope → 用 `~/.lifebrain/scopes/<scope>`；
- `$LIFEBRAIN_HOME` 显式指定时优先用它。

**对方（任何新用户）装完必须做这一步**：

```bash
bash lifebrain/scripts/install_octop.sh --agent <AGENT_ID>
```

它会：① 在技能目录写 `scope.json`；② 按上面的规则分配数据根；③ 认领 legacy 家目录时写上 `owner_scope`。

**如果对方的 `~/lifebrain` 里已经有别人的数据**（同机多人时很常见），安装脚本**不会**自动认领，
而是给这个 agent 分配独立目录，并在输出里提示。要接管 legacy 目录必须显式：

```bash
bash lifebrain/scripts/install_octop.sh --agent <AGENT_ID> --home ~/lifebrain
```

### 脚本路径不能乱扫

```bash
# ❌ 绝对不要这样：会拿到别的 agent 的脚本副本
LB_SCRIPT="$(ls -d ~/.octop/agents/*/.octop/skills/lifebrain/scripts/lifebrain.py | head -1)"

# ✅ 用 Octop 给每个 agent 注入的自己的技能目录
LB_SCRIPT="$OCTOP_SKILLS_DIR/lifebrain/scripts/lifebrain.py"
```

就算路径拿错了，脚本也会按**当前进程的 scope** 解析数据目录（不会读到别人的数据），
并在 stderr 打印一条 scope 不一致的告警——但版本可能不对，所以还是别拿错。

### 通道投递

cron 任务 → 绑定会话 → 通道，这条链路由 Octop 保证隔离。
**但微信 iLink bot 是"一个微信号同一时间只属于一个 bot"**：
如果两个 agent 想都推微信，他们必须用**各自的微信号**扫码绑定。
用同一个微信号给第二个 agent 扫码，第一个 bot 会立刻失效（发送报 `ret=-2 prepare failed`）。

---

## 场景 B：ZIP 直传（对方不方便用 git）

打包好的 `dist/lifebrain-skill.zip` 里是 `lifebrain/SKILL.md` 这种结构。

```bash
# 对方：网页控制台 → 技能 → 上传 ZIP
# 或命令行：
python3 ~/.octop/agents/<AGENT>/.octop/_builtin_skills/skill-manager/scripts/manage_skills.py \
  install ./lifebrain-skill.zip
```

自己重新打包（不需要 `zip` 命令，只要 python3）：

```bash
cd lifebrainskill && python3 make_zip.py      # → dist/lifebrain-skill.zip
```

---

## 场景 C：同一个 Octop 实例里的其他用户（共享包）

Octop 有「全局技能包」机制（instance-global skill packages），实例内共享给其他用户。

**最快的做法（网页控制台）**：

1. 网页控制台 → 技能包 → 新建包（名字随意，例如 `日常记录`）。
2. 把 `lifebrain-skill.zip` 上传进这个包。
3. 包属性里选 `copy_policy`：
   - `snapshot`：别人复制到工作区后是快照，可以自己改（推荐）
   - `lock`：锁定不可改
   - `deny`：不允许复制（就只当只读分发）
4. 其他用户在控制台里从这个包**复制技能到自己的 workspace**，之后各自独立，互不影响。

**用 API 做（可脚本化）**：

```bash
BASE=http://127.0.0.1:8088
TOKEN=<管理员 access token>

# 1) 建包
curl -s -X POST "$BASE/api/skill-packages" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"日常记录","description":"lifebrain 每日记录技能","copy_policy":"snapshot"}'

# 2) 把技能文件塞进包里（content_base64 是文件内容的 base64）
python3 - <<'PY' > /tmp/payload.json
import base64, json, pathlib
files = []
for p in sorted(pathlib.Path("lifebrain").rglob("*")):
    if p.is_file() and "__pycache__" not in p.parts:
        files.append({"path": str(p.relative_to("lifebrain")),
                      "content_base64": base64.b64encode(p.read_bytes()).decode()})
print(json.dumps({"name": "lifebrain", "files": files, "overwrite": True}, ensure_ascii=False))
PY
curl -s -X POST "$BASE/api/skill-packages/<package_id>/skills" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  --data-binary @/tmp/payload.json

# 3) 让某个 agent 用上这个包
curl -s -X PATCH "$BASE/api/agents/<AGENT_ID>" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"skill_package_ids":["<package_id>"]}'
```

包里的技能挂到 agent 上后，会作为「全局共享技能」出现在所有挂了该包的 agent 的技能列表里；
用户也可以在控制台里把它复制进自己的 workspace。

> **复制 ≠ 隔离。** 复制到 workspace 的只是**代码**，数据仍然按 scope 分配。
> 对方第一次用之前必须跑一次 `install_octop.sh`（或让 agent 按 SKILL.md 的流程 `init`），
> 它的数据才会落到自己的 scope 目录里。**不要**假设"每个人有自己的 `~/lifebrain`"——
> 同一台机器上多个 Octop 用户共用一个 `$HOME`，不隔离就是互相读到对方的记录。

---

## 场景 D：公开上架 SkillHub（skillhub.cn）

想让全网用户都能搜到，就发到 SkillHub：

```bash
npm i -g @skillhub/cli   # 或官方推荐的方式装 skillhub CLI
skillhub login
# 在 lifebrain/ 目录里准备发布物料后
skillhub publish
```

对方就能直接：

```bash
python3 .../manage_skills.py install "skillhub:<namespace>/<slug>"
```

Octop 实例内也能从 SkillHub 搜到并装进共享包（控制台 → 技能包 → 从 SkillHub 添加）。

---

## 场景 E：打包成一个「专家」一起分发

如果想让技能和人设/知识一起给别人，做成 Expert 模板（Octop 的专家市场），技能随专家一起走。
参考 `octop/infra/agents/experts/` 的 manifest 与 composer 机制，或直接在控制台里把技能加入专家。

---

## 分发前必查清单

- [ ] `lifebrain/SKILL.md` 的 frontmatter 里 `name` 和 `description` 齐全（description 是触发词，删了就不好用了）
- [ ] 脚本里没有写死任何人的用户名/agent id/会话 key
- [ ] 脚本里没有 `ls -d ~/.octop/agents/*` 这种会串到别的 agent 的路径扫描
- [ ] `scripts/lifebrain.py status` 在干净机器上能跑（只需要 python3；PyYAML 有就更好，没有会自动降级）
- [ ] `scripts/__pycache__` 别打进包里；`scope.json` 是**安装时生成**的，不进包
- [ ] 对方首次使用时 agent 会先问「记录放哪儿」，确认后才建目录；不需要预置 `~/lifebrain`
- [ ] 装完跑一次 `install_octop.sh --agent <ID>`，确认 `scope.json` 里的 `scope` / `home` 正确

## 常见坑

| 现象 | 原因 | 解法 |
|------|------|------|
| 提醒在半夜/早上触发 | 实例时区不是 Asia/Shanghai | 查 `octop` 配置的 `default_timezone`；或用带偏移的 `date:` |
| 提醒发到网页控制台而不是微信 | cron 是用 CLI 建的（绑 dashboard） | 在聊天里让 agent 用 `cronjob_create` 重建任务 |
| CLI 建的任务不触发 | 写的是本地 DB，服务没重载 | `octop service restart`，或改用聊天里建 |
| 提醒来两次 | 调度器 + 兜底 crontab 都发了 | 靠 `mark-reminder` 的 marker 去重，别删兜底脚本 |
| **发不出去（`ret=-2 prepare failed`）** | 微信 bot 掉线：同一个微信号被拿去绑了别的 bot | 用自己的微信号重新扫码绑定；`getconfig` 成功但 `sendmessage` 失败就是这个症状 |
| **读到别人的记录** | 用了 `ls ~/.octop/agents/*` 拿到别人的脚本副本，或没装 `scope.json` | `$LB config` 看 `scope.scope_mismatch`；改用 `$OCTOP_SKILLS_DIR` 下那份；重跑 `install_octop.sh` |
| **两个用户抢同一个 `~/lifebrain`** | 同机共用 `$HOME`，legacy 家目录没标主人 | `config.yaml` 里写 `owner_scope`；其他 scope 用 `~/.lifebrain/scopes/<scope>` |
| agent 报「项目未登记」 | 写入前项目没进 `projects.yaml` | 先 `$LB project add <名> --category work|life --goal "…"` |
| agent 报「todo tN 不存在」 | `append --todo` 指向了不存在的目标 | 先 `$LB todo add`，或用 `todo list` 核对 id |
| 早间提醒只有标题没有进展 | 目标还没挂过工作记录 | 晚间记录时用 `append --todo <id>` 关联；旧记录不会被自动补关联 |
