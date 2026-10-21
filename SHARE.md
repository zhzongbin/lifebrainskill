# 分发指南：把 lifebrain 分享给别的 Octop 用户

本文给**要把这个 skill 分享出去的人**看。分四种场景，选一条即可。

## 0. 先搞清楚包里有什么

```
lifebrain/                 ← 这才是「一个 skill 目录」，SKILL.md 在这一层
  SKILL.md
  references/format.md     ← 数据格式规约
  references/octop-setup.md← Octop 运行时细节（通道投递、时区、兜底）
  scripts/lifebrain.py     ← 核心 CLI（status/show/append/project/report/rebuild/mark-reminder）
  scripts/rebuild_index.py ← 项目索引重算
  scripts/reminder.sh      ← Octop 版提醒入口（system cron → octop cron run-now）
  scripts/reminder_omp.sh  ← 原 omp 无头 CLI 版（其他 agent 运行时用）
  scripts/install_octop.sh ← 一键部署到 Octop 技能目录
```

关键点：**分享的是 `lifebrain/` 这一层**（SKILL.md 必须在顶层）。
路径全是自解析的，换机器/换 agent/换实例都不用改。

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
- 用 CLI 建的任务会绑到 dashboard（网页控制台），且需要 `octop service restart` 才被调度器加载：
  ```bash
  octop --agent <AGENT_ID> cron create --trigger "cron:30 21 * * *" \
    --prompt "$(bash lifebrain/scripts/install_octop.sh --print-prompt)" \
    --task-type agent --fresh-thread
  ```

> 时间按**实例时区**解释（Octop 配置 `default_timezone`，默认 Asia/Shanghai），
> 不是机器的系统时区——很多机器是 UTC，直接 `cron:30 21 * * *` 会差 8 小时。

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

> 每个用户的数据目录都是自己的 `~/lifebrain`，不会串；首次使用由各自的 agent 单独问过用户再初始化。

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
- [ ] `scripts/lifebrain.py status` 在干净机器上能跑（只需要 python3；PyYAML 有就更好，没有会自动降级）
- [ ] `scripts/__pycache__` 别打进包里
- [ ] 对方首次使用时 agent 会先问「记录放哪儿」，确认后才建目录；不需要预置 `~/lifebrain`

## 常见坑

| 现象 | 原因 | 解法 |
|------|------|------|
| 提醒在半夜/早上触发 | 实例时区不是 Asia/Shanghai | 查 `octop` 配置的 `default_timezone`；或用带偏移的 `date:` |
| 提醒发到网页控制台而不是微信 | cron 是用 CLI 建的（绑 dashboard） | 在聊天里让 agent 用 `cronjob_create` 重建任务 |
| CLI 建的任务不触发 | 写的是本地 DB，服务没重载 | `octop service restart`，或改用聊天里建 |
| 提醒来两次 | 调度器 + 兜底 crontab 都发了 | 靠 `mark-reminder` 的 marker 去重，别删兜底脚本 |
| agent 报「项目未登记」 | 写入前项目没进 `projects.yaml` | 先 `$LB project add <名> --category work|life --goal "…"` |
