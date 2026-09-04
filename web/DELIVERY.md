# 审校台前端 · 交付说明

产品与界面规格的现役落点就是本文件（§1 技术与数据缝 · §3 路由与屏幕职责 · §6 UI 硬规则），
产品边界与七个现役阶段的人类任务见仓根 `README.md`。**当前处于真实模式**：
`web/.env` 为 `VITE_USE_MOCK=false`，数据走 `127.0.0.1:8765` 的真实服务。
`src/mocks/` 那 20 份是冻结的证据基线（字节与哈希登记在 `scripts/mock-baseline.json`，
来历见统一契约 §8.1，`npm run seams` 钉着），mock 路径仅作离线开发用。

只想用界面见仓根 `README.md`。下面是**开发**前端时需要的：

```bash
cd web
npm install
npm run dev          # http://127.0.0.1:5173（热更；经 dev proxy 打到 8765）
npm run build        # 产物落 web/dist，就是服务托管的那一份
npm run typecheck    # tsc --noEmit
npm run seams        # 数据缝 / 设计缝红线（见下）
```

**批量开书**：上传口接受一次选择 / 拖入 N 个 `.txt` / `.md`。浏览器复用单书
`POST /api/upload` **逐本顺序**上传并执行免费 Stage0 扫描，不把 N 本原始字节同时压进内存；
单本成功立即打开 agent 授权框（返回即落到该书 Stage0 人工审校），多本全部成功直接打开整批
agent 授权框。部分失败不回滚成功项，
先逐本列出失败原因，再允许只把成功项交给 agent；人工路径始终可从语料库逐本进入 Stage0。

**批量代跑**：语料库屏每本书标题前有勾选框，勾几本后打开批量确认框。付费授权与正文外发
授权任一缺失时只允许 0 成本预览配置并明确写「不会启动任何一本」；两项同时授予且 profile
完整有效后才显示「授权并开始跑这 N 本」。
闸内的立刻起 agent，其余排队，一本结束自动带起下一本；`running`/`pending`/`rejected`
三堆在确认框里逐本摆开（**部分失败不回滚**）。同时在跑的上限由服务端
`GET /api/agent/runs/queue` 下发，前端不写死。排队只在服务进程内存里，重启不保留。
要逐字核对某一本的 argv，仍走那本书自己的「agent 跑书」按钮（那条有 0 成本 preview）。

**语料库信息密度**：不展示完整流水线说明、服务地址或“尚未打开书”的解释。筛选框放在
页头；书名本身是打开入口，卡片右侧只保留紧凑的 Agent / 删除操作；总览矩阵的阶段状态与
产物新鲜度只画符号，完整文字由上方图例、悬停 title 与无障碍名称承载。

**删除项目**：语料库每本书提供「删除项目」，二次确认必须输入完整 sid。删除不是前端
隐藏卡片，也不是立即永久销毁：后端零成本子进程把整本书目录及该书审批状态移入
`<base>/_service/trash/<trash_id>/`；上传原文、ledger、作业与 agent 审计继续保留。
正在运行或位于 agent 队列中的书拒绝删除，成功后自动刷新语料库查询。

**服务维护**：语料库页头「维护」打开 retention / 回收站恢复面板。Retention 四类 TTL 默认都关闭；
留空就是 `null`，界面不得解释成 0 秒。两类动作都先调用只读 preview，展示扫描分母、候选或恢复的
sid/title 及完整 token；表单任何一格变化都作废旧 preview。Apply 必须勾选显式确认并把服务端返回的
完整计划原样交回，不能只搬 token 或在前端重拼候选。Retention apply 只写 `_service`；trash restore
的 book/approval/service run 移动仍由受控 CLI 子进程完成，浏览器和 HTTP 服务进程不直接改产物树。
端点为 `POST /api/maintenance/retention/{preview,apply}` 与
`POST /api/maintenance/trash/restore/{preview,apply}`。

**跑书进度屏**：`/n/:sid/run`（左导航「进度」按钮）。agent 的 `novel.cli mark`
与 UI / 持久队列共用 `<base>/_service/agent_progress.jsonl`：作业执行器在每个节点开始、
结束时自动打点，界面按 `NODES` 的真实顺序折成进度 + 事件时间线；有步在跑时 2.5s 轮询，
闲置 10s。节点没有 beacon 时显示「无运行记录」，只表示该条命令没有独立打点；可选节点未执行、
上传或人工流程从别的入口完成，都不能据此改写所属阶段的完成状态。它是**观察屏不是判定屏**——
「这层过没过」仍归各层屏的 badge（R-13.6）。后端路由：`GET /api/novels/:sid/run`
（`novel/api/routes/run.py`，书目录不存在也 200）。同一响应另带由 operator 当前快照直接投影的
`resume_frontier`：明确显示真实续跑节点、下一处理者是 code / Commander / Executor / human、
贵贱与 blocker；planner 已确认无需再跑的前序节点默认折叠、可展开审计。前端不得从 beacon、
Stage badge 或步骤位置自行推导该前沿。

**agent 一键跑书**：语料库屏每本书的「Agent」，或 Stage0 审批操作栏的
「Agent 代跑」→ 确认框摊开
将拉起的完整 argv 与提示词全文。付费授权与正文外发授权使用两个独立复选框：
付费默认不勾，正文外发默认勾选，任一变化都会使旧 preview 失效并重新 preview；两项值和声明原文分别留档，
不会因已获付费授权而推定正文外发同意。正文外发声明须把已选 Commander 与逐节点 executor
profile 展开成实际可达的具体 provider 路由；被 override 完全遮蔽的默认项不得扩入授权。
完整命令与 prompt 默认折叠、按需展开；弹框限制在
视口内，中段独立滚动，底部授权与发起动作始终可见。任一授权缺失时当前只可 preview、不会启动
Commander；两项同时授予后才显示「授权并开始跑书」。确认后启动
（`POST /api/agent/runs`，后端按已选 profile 以 Claude Code 或 Codex Commander driver 拉起
headless agent；长 prompt 只经 stdin 传入，不进 argv/env/log。产品写入边界仍只允许 skill 明列的
typed 人工输入与唯一终审包，正式产物一律经 CLI）。进度在 `/n/:sid/run` 实时跟踪，
那里有 agent 进程状态、日志尾部与「熄火」按钮（`POST /api/agent/runs/:id/stop`）。
进程状态与交付终验是两条正交状态：exit 0 只显示“进程完成”；服务端另以启动/结束代码指纹、
Stage2 typed 闸、当前成书、终审包和 inbox 阻塞轴判 `ready` / `ready_with_disclosures` /
`stopped` / `failed` / `unknown`，界面不得从退出码补推“可下载”。
互斥两个方向都拦：agent 在跑时起任何 API 作业 409，反之亦然。

**Agent capability / executor / Commander 边界**：后端提供动态 `GET /api/agent/capabilities`，发布
Agent driver/provider 元数据、角色、model id 与 label、各模型实际 effort / execution mode、本机 CLI/版本
探针结果、Legacy provider/model/thinking/effort/node 能力、executor 节点和 `default_profiles`。该响应不归
`/api/enums`，前端不得另写供应商能力表。
`AgentRunDialog` / `AgentBatchDialog` 现已消费这个端点，动态呈现 Commander、默认 executor 与逐节点
override。每个 executor 先显式选择 `kind=agent|legacy`，再分别呈现 AgentProfile 或 LegacyLlmProfile；
上游选择改动会清空失效的下游字段。`default_profiles=null` 明确表示尚无经整书验收的产品预置配置，
不是 backend 开关，也不能取任何 capability 数组首项自动代选。`/settings` 另经
`GET/POST /api/settings/agent-profiles` 管理当前 output base 的用户常用配置：保存后单本与批量表单优先
载入，表单内仍可只为本次临时覆盖。用户偏好不改产品默认，不改已受理 run；unset/configured/unreadable、
读取失败与 CAS 冲突都显式呈现。设置不保存 key、CLI 登录态或两项授权，付费与正文外发仍逐次确认。
`ready / missing / unknown / unauthenticated` 四态和服务端 reason 原样显示，非 ready 不能选；CLI/版本
探针不发模型请求，`ready` 也不等于账号或具体模型已验证。

直接 `POST /api/jobs/preview` / `POST /api/jobs` 把同一 resolved `executor_backend` 经 Step / JobStart /
argv / CLI / RunContext 传到节点 executor；自动 0 成本 `--check-only` Step 不继承 backend。旧请求里的
`executor_profile` 只读迁移为 `kind=agent`，新 UI 与响应都使用判别联合。

Phase 4 增加 `stage6.semantic + flags=[]`，并为 direct executor 纵切接通独立正文外发授权：
请求默认 false，preview 可回显 backend / egress 快照及没有 `--allow-egress` 的不可执行 argv；真 start
只有显式 true 才可在 job/driver/probe 前越过授权闸。它与 `--yes` 付费确认正交，无 backend 时不能
伪造 egress。Stage6 A/B 使用两个独立 driver/session/cwd/provider session，缺失角色并发；逐角色
checkpoint 可完整或单边恢复，代码以 prepared journal → 正式写盘 → typed readback → 双 adoption →
committed 收口并做最终校验，不由 agent 自述补判，也不回退 DeepSeek。

Agent backend 的 direct 面收口为 **8 节点 / 13 种 flag-token 组合**：

- Stage2：`stage2.global []`、`stage2.blueprint []`；
- Stage4：`stage4.design [] / [--review-token] / [--replan,--review-token]`、`stage4.plan []`；
- Stage5：`stage5.write [] / [--chapters] / [--revise] / [--revise,--chapters]`；
- Stage6：`stage6.hook []`、`stage6.semantic []`；
- Stage7：`stage7.rename []`。

`--chapters` 的章号是值，不另算 token。Stage4 design/plan、Stage5、Hook、Rename 均由 agent 交固定
typed 完整候选，代码负责 HARD、结构化 feedback、checkpoint、正式 journal/readback/adoption 与终验；
显式 Agent backend 不回退 Legacy API；矩阵外 Agent 组合仍关闭式拒绝。Legacy backend 由服务端 provider
能力表校验，DeepSeek Flash 在三个钉死 Pro 的节点必须改用 Pro 或逐节点覆盖；任一 variant 失败都不自动
切到另一种。

Phase 6 同时接通 outer Commander：preview/start/batch 共用后端唯一 planner，页面将完整 profiles 与
两项授权组成请求，并把最后一次成功 preview 的**同一个请求快照**保存到确认态；start 不从当前表单
重拼。表单、授权、服务端回显或 capability 刷新任一漂移都会关闭启动。batch 受理后保存整批快照；
历史 run 的 `profiles=null` 显示 unknown，不按今天的选择倒推。direct Job adapter/确认框/抽屉仍独立
展示该次 executor backend（Agent + AgentProfile 或 Legacy + LegacyLlmProfile）与 egress，不把它和 outer
Commander 合并。后端 started 还冻结 base、代码
指纹与 resolved profiles，child 通过 contract SHA 复核后才给节点 executor；进程状态与盘面 delivery
终验继续正交。Phase 1～6 验证使用 fake driver、零模型 CLI 检查与非 corpus 回归，**真实模型请求数
仍为 0，没有真实 Claude / Codex 调用、质量/速度/entitlement 或 current-schema corpus 实证**。

---

## 1. 三道缝落在哪

总则：

- React 18、TypeScript、Vite、Tailwind CSS、React Router、TanStack Query；不引入无需求支撑的 UI
  组件库、图表库、全局状态库、i18n、鉴权或 SSR。
- **界面与 API 同源同端口**：服务把 `web/dist` 静态挂在自己那个端口上
  （`novel/api/app.py::_mount_ui`，默认落点 `UI_DIST`，可由 `create_app(..., ui_dist=)` 注入）。
  跑书的人只起一个进程、只记一个地址；`web/vite.config.ts` 那条指向 8765 的 dev proxy
  只服务于前端开发。三条边界由 `tests/test_api_ui.py` 钉住：
  非 `/api` 的 GET 一律回 `index.html`（`BrowserRouter` 深链接刷新必需）·
  `/api/*` 的漏网请求仍是 `ErrorBody` 形状的 404，**不许吞成 200 + HTML** ·
  catch-all 必须是路由表最后一条。未构建时界面地址答 503 并给出下一步，API 不受影响。
- 屏与组件只经 hooks → `web/src/api/endpoints.ts` → Api 接口取数，不直接 fetch。
- real 与 mock 适配器满足同一 TypeScript 接口；真实模式不允许缺端点时回退 mock。
- 写请求携带 `X-Novel-UI: 1`；服务进程只写 `_service/`，产物变更经 CLI 子进程。
- action、枚举、付费与写盘语义由服务端 `/api/enums` 和 preview 返回；前端不保存第二份判定表。
- agent driver / model / effort / execution mode 由 `/api/agent/capabilities` 发布；`useAgentCapabilities` 使用 no-store 语义，页面按响应动态构造 Commander / 默认 executor / 节点 override，null 不代选。用户常用 profile 由 `/api/settings/agent-profiles` 按三态 + CAS 单独持久化，不能混成 capability 产品默认或浏览器 localStorage。后端 8 节点 / 13 组合 direct job 纵切及独立 egress 快照仍原样保留。
- 底部作业抽屉只消费 `/api/jobs`，逐条展示全部作业的最新状态，不订阅或展示命令、stdout/stderr；
  详情统一跳到本书运行进度页。日志能力仍由 `/api/jobs/:id/stream` SSE 提供给专用详情消费者：
  事件 `id` 等于 UTF-8 `next_byte`，断线用 `Last-Event-ID` 续读，终态先排空日志、再发唯一 `end`。

### 数据缝

`src/types/api.ts` 声明 `interface Api`。`src/api/mock.ts`（假后端）和
`src/api/endpoints.ts` 里的 `realApi`（真 HTTP）**都被声明为 `Api`**，
所以「两条路径必须完全同型」是 `tsc` 强制的，不是口头约定。

```ts
// src/api/endpoints.ts 最后一行
export const api: Api = USE_MOCK ? mockApi : realApi
```

屏与组件从不 fetch，只经 `src/hooks/use*` → `api.*`。
`src/mocks/*.json` **只被 `src/api/mock.ts` import**（`npm run seams` 查这条）。

`src/api/mock.ts` 不是 fixture 加载器，是一个假服务端：

- 20 份 JSON 原始字节（`npm run seams` 会和 `scripts/mock-baseline.json` 的 bytes + SHA-256 比对）
- 一层 localStorage 覆盖层：验收记录、作业、改稿
- 120ms 模拟延迟，所以加载态是真的会看到
- 作业引擎：起一个 running 作业、定时往日志里追加行、最后翻 `ok`
- `previewJob()` 下发命令行 —— 前端不拼 argv
- `NoSampleError`：sid ≠ 样本那本、章号 ≠ 3 时抛，界面走「没有样本」屏

### 静态 / 动态分离

`src/constants/vocabulary.ts` 是**全应用唯一的状态色出口**：
`FRESH` / `SEV` / `BADGE` / `JOB` / `GATE` / `STAGES` / `RAIL`，值逐字抄自设计稿。
其余颜色是 `src/index.css` 里 `@theme` 的令牌。
非核心枚举（hinge_kinds / voice_keys / actions …）运行时从 `/api/enums` 取，不硬编码。

### 渲染缝

一屏一个纯函数。数据形状复杂的四屏拆了 `useXxxScreen.ts`
（corpus / read / stage4 / stage5），其余屏的选择逻辑短，直接在屏文件里。
组件只摆放，不做判断。

---

## 2. 状态归属（没有引入状态管理库）

| 状态 | 家 |
|---|---|
| 各屏响应 | TanStack Query |
| Agent 跑书用户常用 profiles | 当前 output base 的 `_service/agent_profile_settings.json`；页面只持读取 revision 与未保存草稿，POST 以 CAS 提交 |
| **`gate` / `seen` / `design_review` / `structure` / `writing` / `writing_approval` / `closure`** | 一份公开 v6 `ApprovalDoc`，也走 Query（`['approvals', sid]`）；`seen.global` 不再“打开页面即已读”：全局设定页只在服务端 `GlobalReviewView.review_token` 非 null 时提供“确认已通读当前全局复核”，提交 `Stage2GlobalSeenRequest`（token + 确认人），服务端重算 token 拒绝过期主体；`design_review` 保存绑定当前 Stage4A 主体的策略级与逐章 typed 裁决，只授权 B；`structure` 保存绑定完整 Stage4 plan + binding concerns 主体的最终逐章裁决；`writing_approval` 是当前 Stage4 主体的 typed 写作授权；`closure` 是绑定 current canonical 全章 + fresh Review + finding/trim 集的 Stage5 Commander 回执，只有 current `closed` 才打开 s5 |
| Stage2 Blueprint 审阅工作包 | 独立 Query（`['stage2', sid, 'blueprint-review-workpack']`），读取 `/stage2/blueprint-review-workpack`；服务端绑定 current Blueprint/MainText/完整 check/code/rules，异常章优先且全章保留。工作包到达后正文分区与骨架只从该包渲染，确认提交同一包的完整 binding，页面不从另一份 ReadingResponse 或 rows 重拼主体/判定，不写新 approval 或 artifact |
| Stage4 A/B 审阅工作包 | 两条独立 Query（`['approvals', sid, 'design|structure', 'workpack']`）；分别读取 `/approvals/design/workpack` 与 `/approvals/structure/workpack`，绑定当前 A 候选或 B 结构主体，不从 `ApprovalDoc`、页面 rows 或历史决定重拼；v2 每个 coverage 行携 `reading`（服务端渲染的逐章裁决材料），页面只作折叠呈现、不自算 |
| `job` / `jobLog` | Query + `refetchInterval`（由服务端下发的 `running` 决定） |
| 确认框里的 argv | Query（`api.previewJob`） |
| `sid` / 章号 / 当前屏 | React Router 路径 |
| `drawerOpen` / 待确认动作 | 全应用**唯一一个** Context（`useChrome`） |
| 选中的判据 / 剧透开关 / tab / 编辑中的文本 | 屏内 `useState` |

**gate 走 Query 而不是 reducer**：它在真产品里是服务端的人工验收记录。
普通闸门经 `approveGate`；Stage4 刻意不走通用 bool 路由，而有两道彼此独立的 typed 人审。
Stage4A 表单一次提交当前完整 subject、策略 `accept|request_replan` 与逐章
`accept|request_repair`；只有全接受才授权付费 B，拒绝决定本身不自动发起付费作业。B 编译并通过
机械闸后，最终结构表单再对绑定 Plan 与 concerns 的完整 subject 逐章提交
`accept|request_replan`，只有全接受才打开 s4。换实现仍只改 `src/api/` 这一道传输缝，屏与组件
不直接 fetch。

**「闸门不能是全局的」是结构性保证**，不是约定：
`sid` 来自 `useParams()` → 进 query key → 进持久化 key。
换书就是换一条缓存记录，不存在 A 书的 ✓ 能漏到 B 书的代码路径。

---

## 3. 路由

**按审校任务建屏，老路由保留为重定向。** 下表就是当前**权威**路由表，
执行落点是 `web/src/routes.tsx`。

| 路由 | 屏幕职责 |
|---|---|
| `/` | 语料库、上传与逐书阶段状态；勾选多本一次交给 agent 批量代跑；项目可在 sid 二次确认后移入可恢复回收站（闸与排队归服务端，界面不写死上限） |
| `/settings` | 管理当前 output base 的 Commander、默认 Executor 与逐节点 override 常用 profiles；选项只来自 capability，保存/清空走 revision CAS，不保存凭据或逐次授权 |
| `/n/:sid` | 进入该书并重定向到 stage0（新书先审非正文；已有产物仍可从左栏直达各层） |
| `/n/:sid/run` | agent / UI / 持久队列共用的全链运行进度与事件时间线；步骤从 `NODES` 派生，`resume_frontier` 从 operator 同快照投影，前序已确认跳过项默认折叠；界面**只观察不判定** |
| `/n/:sid/stage0` | 整本剔除审阅、编辑与批准；完整原文默认展开、可收起，不重复展示属于 Stage1 的分章预览。Agent 入口放在审批操作栏；浏览器人工动作不要求填写 actor/reason，统一按 `human-ui` 和具体动作理由记审计；Agent/CLI 仍显式自报 |
| `/n/:sid/stage1` | 顶部合并展示校验结论与切分确认，且不依赖正文查询成功；下方是章节索引和原文切分结果，只读，开篇文案只展示一次，重号/缺号/倒退按服务端章号诊断逐章显式提示 |
| `/n/:sid/read/:o` | 原文与 stage2 骨架对照；书级工作包先给异常章与完整检查摘要，默认隐藏已由代码校验的 beat/fact/hinge 结构 ID 与字符坐标，仍保留章功能、覆盖原文、事实参与人物、因果、hinge、状态与**全部**叙述声音；打开/切换单章不自动标整本已读，只有正式工作包成功加载后才可显式确认。页面所见与待签主体来自同一工作包，提交时携带整包 binding 及 Blueprint/MainText/code/rules 身份，任一漂移均零写入，后续整包变化使旧 seen 自动过期 |
| `/n/:sid/global` | stage2 全局人物、关系、规则、秘密 |
| `/n/:sid/stage4/:o` | Stage4A 盲候选先经策略级与逐章 typed 人审；A/B 各自先显示服务端异常优先工作包（`blocking → semantic_attention → disclosure → metric_only → phase_deferred → coverage`），再由人填写完整 typed 裁决。全接受后才呈现 B/最终结构工作面。常驻展示完整结构 subject SHA-256、全章 `retain/reconstruct`、声明/复算变化与 B 的 nonblocking concerns，选中章再展示计划/上下文细节，并经第二道 typed 表单原子提交最终逐章结构裁决。最终结构批准不等于 Stage5 写作授权 |
| `/n/:sid/stage5/:o` | canonical 成稿、材料、验收 finding 与修订的合并工作面；顶部常驻服务端 Stage5 Commander 闭合卡：`review_required` 只给一次最终 Review，blocked 工作包可按服务端 `unresolved_trim_items` 批量销账，ready/expired 经 `/approvals/stage5/template` 逐项形成 typed 回执；空状态在付费确认前先写入全书闭卷授权。单章改稿严格走只读 typed preview → 明示 `formal/working_draft` → 双 SHA apply，每次 apply 不自动 Review；fresh Review 的章级/书级 finding 仍可按完整 evidence identity 提交 wontfix |
| `/n/:sid/inbox` | 八类待办的全书路由器，不提供第二个编辑面（校验下沉到各阶段屏的面板） |
| `/n/:sid/stage6` | 三候选文案与人工定稿 |
| `/n/:sid/stage7`、`/n/:sid/stage7/:o` | 人名表、漏网候选、成书状态和逐章替换对照 |

旧 `/n/:sid/review/:o` 重定向到 `stage5/:o`，旧 `/n/:sid/check` 重定向到 `inbox`；
总览、独立 review 和独立 check 的信息已由当前工作面承接，**不再作为第二个真相来源**。

---

## 4. 组件清单

```
components/
  app/    AppShell（含右下角 notice 槽）  Rail  BookLayout  ScreenFrame  ScreenBoundary
  vocab/  FreshnessDot/Tag/Badge/Legend  SevMark/Chip/Badge/Counts
          CheckBadge  CheckMark  JobStateChip  GateChip  GateMark
  ui/     Panel  PaneHeader  SectionTitle  ActionButton  LinkButton
          Mono  Prose  HighlightedText  ChapterStrip
          ActorReasonFields（actor/reason 两连输入，actor 经 sessionStorage 记住上一次）
          ChapterGrid（全书章格，stage4 / stage5 共用）
  compare/ ComparePanes（粘性侧栏）  PaneScroll  CompareRows（行配对）
  state/  LoadingPane  LoadFailed  NotRunYet  MissingSample
          EmptyIsSuspicious  EmptyIsGood  HatchNote  QueryStates
  gate/   GateBar        check/  CheckPanel        job/  JobDrawer  ConfirmDialog
  agent/  AgentBatchDialog  AgentRunDialog
  screens/settings/  SettingsScreen（用户常用 Agent profiles；三态 + CAS）
```

Stage4 屏另有两块专用审批面板。`screens/stage4/DesignReviewPanel` 对当前 Stage4A 候选提交
hash-bound 的策略级与逐章裁决；候选、证据、允许的 repair code 及约束最小基数都来自后端，默认
关闭式拒绝，前端不替人补拒绝理由。只有后端返回 `design_review.state=accepted`，才呈现
`screens/stage4/StructureApprovalPanel`。后者替代通用 `GateBar(s4)`，对完整 Plan + concerns 主体
逐章裁决；`request_replan` 不顺手拉起重规划，写作授权仍只在 Stage5 的付费确认流中单独授予。
两面共用 `screens/stage4/ApprovalWorkpackPanel`，但各走自己的 GET 与 query key：六组按服务端顺序
逐项展示 `scanned / sev / status / effect / detail / refs / scene_refs / allowed_actions`，并原样标出
`changed / historical_accept_reusable / source_decision_sha256`。面板不按 severity 或文案重判、不据
空组宣称通过、不预填 accept；`allowed_actions` 只作为 `accept|request_repair|request_replan` 的 typed
入口披露，INFO、metric、UNVER、skipped 等只读项不会因此长出付费按钮。
Design workpack 的 `disposition=suspended` 专门呈现 exact frozen-v6 的 raw all-accept：页面明确显示
“已记录但缺 v7 relation/preflight 验证权威”，与 `already_current` 一样禁用重复模板入口，但绝不显示
“可进入 B”，也不渲染 repair/replan/B 动作。只有真实 `request_repair` 的章行可携 `s4er1` 零 wire
完整章 edit token；`suspended` 本身不携 action/edit/execution token。Stage6/7 workpack 不接受该处置态。

Stage5 屏顶部另有 `screens/stage5/Stage5ClosurePanel`。它只消费 `ApprovalDoc.closure` 的
`state/workpack/next_step`，不会拿旧 Review、历史回执或 finding 数量自行补成 ready/closed。
workpack 的 `chapter_subjects` 是全章 canonical 身份展示面；批量 trim 请求逐项原样复制
`unresolved_trim_items` 的 audit/chapter 两枚 SHA，只补共享 actor/reason 与可选单项 reason；发专用 API
前仍以「服务端一次性 manifest」占位走通用 jobs preview，确认框核过 argv 后才真正提交。
回执模板与提交分别真实走 `GET/POST /api/novels/:sid/approvals/stage5(/template)`；模板身份与页面
current workpack 不一致时关闭式禁用，placeholder/token 必须由操作者逐项消掉。模板 GET 标为只读，
正式回执 POST 会写审批账并打开 s5，按钮按 U-08 明示为免费写盘。

全局屏的 `screens/global/GlobalReviewPanel` 按**主体**成卡：人读这一面的方式是
「这个人物 / 这条关系 / 这条秘密的时间线」，不是一张 145 行的表。收敛键
`group_key` 与交付效果 `effect` **都由服务端随行下发**（`R-2.22`），屏幕侧只负责摆
——本地复制一份分类表就是第二个判官，某天新增一类高影响事实，界面仍会把它画成
「只披露」而没有任何东西出声。能挡住 Stage4 的卡排在前面，只披露的卡**不因此消失**；
卡片全选只是把逐项选择一次勾上，提交时仍展开成逐项 hash-bound decision，
允许单项撤销，且任何路径都不会自动 `PASS`。顶部计数直接读
`effect_counts`，不在前端从 `items` 重算一份。

notice 槽与快捷键不属于任何一屏：`useChrome().notify(text)` 推一条瞬态通知
（写回成功 / 已复制之类的一句话反馈），AppShell 在右下角渲染，约 3s 自消、最多堆 3 条；
`useHotkeys` 挂在 AppShell，管全局 ←/→ 翻章与 Esc（见下「快捷键」）。
`ScreenBoundary` 兜住单屏崩溃：崩了要说「这一屏崩了」，白板会被当成「已经看过」。

两个并排壳覆盖全部六屏对照需求：

- **`ComparePanes`**：一栏正常流 + 一栏粘性侧栏。页面唯一的滚动条归长文那一栏
  （Ctrl-F / PageDown / 滚动条即进度不能坏），结构化那栏钉在旁边内滚。
  **不做滚动同步** —— 两侧长度无关，线性比例是错的，按偏移量算又撞 U-13。
- **`CompareRows`**：两侧有已知对应关系时，做一个栅格、每个对齐单位是横跨两列的一行。
  两格自动等高，对齐精确且与文本长度无关，零 JS。

stage5 的「三栏」= `ComparePanes(sticky=left)` 的右栏里装 `CompareRows`，不是第三种版式。

### 快捷键

- `← / →`：带章号的屏（read / stage4 / stage5 / stage7）翻章，屏名保留、只动章号；
  下界钳在第 1 章，上界各屏数据层自己管（跳过头会看到「没样本」或「还没跑」）
- `Esc`：确认框开着就关确认框，否则关作业抽屉
- 焦点在输入框 / textarea / select / contentEditable 里时一律不劫持（左右键是光标）；
  Ctrl / Alt / Meta 组合键也不碰

---

## 5. 数据形状现状

- **动作分类以服务端下发的 `writes_disk: boolean` 为准。** `constants/actions.ts` 的精确集合
  只给不带该字段的历史 mock 兜底。
- **Stage4 计划/上下文按场景槽对齐**：`plan_chapter.scenes[]`
  恒非空；`ctx_chapter=null` 表示上下文缺席，非空时再按 `scenes[].slot` 对齐。
- **Stage4 view 不照抄派生字段**：计划不持久化 `semantic_contract` 或
  `computed_change_kinds`；API 用当前 blueprint 与实际计划现场派生后再返回。Context 的
  `characters` 是带 `char_id` 的数组，界面不显示内部 id，也不重复展示舞台已有的 participants。
- **Stage4A 人审与最终 Plan 人审是两份主体**：`ApprovalDoc.design_review` 只决定当前盲候选能否
  进入 B；逐章 `request_repair` 必须带关闭式 repair code 和所需 scene/opaque-alias 引用。
  `ApprovalDoc.structure` 只在 B 编译后对完整 Plan + `binding_concerns` 生效，二者不能互相代签。
  concerns 是需人核对的非阻断披露，前端逐条展示，但不把它自动翻译成拒绝或通过。
- **Stage4 A/B 工作包是两份只读 façade v1**：各自携带当前 `subject_sha256` 与固定六组全集；空组仍
  保留服务端的 `scanned` 口径。`phase_deferred` 只说明要等 Context 才可判，不进入 B 结构主体；
  `coverage` 是全章核对导航，不是自动批准清单。界面只显示服务端 `effect` 与 `allowed_actions`，
  不从 INFO / metric / UNVER 造 repair 或付费入口。
- **Stage5 `materials` 是对象或 `null`**：非空时以 `blocks[]` 为唯一下发面。前端分别处理
  对象缺席与对象内列表，不能把 `null` 洗成空列表。
- **Stage5 正文只取 Draft `segments[]`**：Review 只发 finding 与计数，不重复正文、轮次、篇幅、
  基线或来源账。阅读、编辑与证据高亮都拼接并行 Stage5 响应里的当前 segments，不能退回 Review
  的历史主体或在前端重判 stale。
- **Stage5 改稿有独立只读 preview 主体**：`POST /review/:o/edit/preview` 返回服务端归一后的
  `normalized_segments`、全部 finding、current/introduced/resolved HARD、current chapter SHA、
  preview subject SHA 与唯一 `required_confirmation`。页面不从 rule/count 推确认语义；正文一改就让
  旧 preview 失效。正式 `POST /review/:o/edit` 原样提交归一稿、actor/reason、两枚 SHA 和该 confirmation，
  并在发起前仍用通用 jobs preview 摊开完整 CLI 形状；完成多次改稿后由顶部显式跑一次最终 Review。
- **Stage5 closure/batch trim 的身份只来自服务端工作包**：`review_required` 明确区别于 missing；
  `chapter_subjects` 与 `unresolved_trim_items` 都是必填投影，浏览器不从正文、reason/context 或 Review
  重新算 SHA。Commander ruling/group/state 三张取值面是 `/api/enums` 的必填字段，整份 enums 未到才 loading，
  不把缺键降成空枚举继续提交。
- **Stage4 结构决定与最终人工裁决仍是两层**：`stage4.index[].structure_decision` 展示计划自己的
  `retain/reconstruct + declared/computed_change_kinds`；`ApprovalDoc.structure` 展示人对当前
  完整 Plan + concerns subject 的 `accept/request_replan`。前端不把前者自动翻译成后者。
- **`/api/jobs` 的元素是嵌套 `JobRecord`**。`api/endpoints.ts` 在唯一数据缝把它适配成抽屉
  使用的扁平 `Job`，五态（`running / ok / landed_red / failed / unknown`）从服务端原样适配；
  `landed_red` 表示产物已落盘但报告有阻断红行，不折进 `ok` 或 `failed`。抽屉只画概览，
  不因“顺手看日志”建立第二个详情页。

---

## 6. UI 硬规则 U-01～U-20 与逐条自查

规则本身：

1. freshness 保留 fresh / stale / unknown，missing 作为明确显示态；unknown 用问号，missing 用空集符号，不得画成 fresh，也不得彼此复用近似轮廓。
2. badge 保留 pass / amber / fail；amber 显示为「通过 · 有未验证项」，表示存在 unver，不是较弱的通过；进度页可悬停问号查看服务端给出的具体原因。
3. severity 保留 hard / info / metric / unver；metric 是读数，不使用警告语义。
4. job state 保留 running / ok / landed_red / failed / unknown 五态；`landed_red` 表示产物已落盘但报告有阻断红行（exit 1），界面必须引导人看报告，不能画成成功或“命令没跑成”。它在批量队列里仍会停住同书 lane；服务重启后的 running 一律改判 unknown。
5. 空数组按字段解释，不得统一翻译成“没问题”；not-run、合法空与数据缺失分别呈现。
6. 部分遮蔽必须常驻标注“部分”，不能收进 tooltip 或宣传为完整安全边界。
7. 付费、免费只读、免费写盘在按钮与确认流上可辨；费用和写盘语义只由服务端给出。
8. 付费确认显示服务端下发的完整命令；取不到预览就不允许发起。
9. 不显示预估花费、预估调用次数或伪精度进度。
10. 前端不重算 badge、passed、severity、freshness、paid 或业务阈值；缺结论时显示读数/unknown。
11. 不从中文文案正则抠结构；修复路由、章节、证据、状态全部消费结构化字段。
12. 服务端切好的片段直接渲染；不按 Python 下标在 JavaScript 中二次切中文字符串。
13. 中文正文不 justify，使用适合长读的字体与行高，段落靠间距而不是缩进堆叠。
14. 对照任务默认并排，不用 tab 隐藏另一侧；两侧共享可读列宽。
15. 未对齐块必须带可见边界和“未验证对应”的文字，不得用空白暗示已对齐。
16. loading / error / not-run / normal 四态每屏齐备；not-run 说明下一步，不写“暂无数据”。
17. 不发明健康分、AI 建议卡、项目百分比；只显示有来源的状态与读数。
18. 不提供运行中取消按钮；持久队列只允许取消尚未开始的 `pending/blocked` 项，运行中返回 409。
19. 状态不能只靠颜色：完整判定面同时用形状与文字；紧凑总览矩阵可只画形状，但必须在同屏
    提供文字图例，并通过 title / aria-label 保留逐项文字语义。
20. 目标视口 1280～2560 桌面宽屏；不要求移动端和响应式重排。左侧导航可折叠为窄轨，折叠时主内容与底部作业面板同步释放宽度，并常驻展开入口。

逐条自查（U-00 是本文件自加的一条，规格里没有）：

| 条 | 状态 | 落在哪 |
|---|---|---|
| U-00 「不知道」≠「没问题」 | ✅ | `components/state/` 整层；`EmptyIsSuspicious` 与 `EmptyIsGood` **刻意不合并** |
| U-01 新鲜度三态 | ✅ | fresh 实心圆 / stale 实心方块 / unknown 问号；`missing` 作为第四种显示态使用空集符号。完整屏常驻文字，总览矩阵由同屏图例 + title / aria-label 承载文字 |
| U-02 徽章三态，amber 不是绿 | ✅ | `CheckBadge`；amber 的解释文案写明「查不了不等于没问题」 |
| U-03 sev 四级，metric 不用警告色 | ✅ | `SevMark` 的 metric 是一条底线不是点，色 `#57534c` |
| U-04 作业五态含 unknown / landed_red | ✅ | 刷新页面 = 服务重启，原本 running 的一律改判 `unknown`（不是 ok）；exit 1 显示 `landed_red` 并引导查看报告，不冒充成功或命令崩溃 |
| U-05 空数组 ≠ 没问题 | ✅ | `secrets: []` / `problems: []` / `voice_extra: []` / 漏网候选为空，各有各的说法 |
| U-06 非完整遮蔽边界必须标注 | ✅ | `/api/enums` 的结构化 `spoiler_disclosure` 在 loading / empty / 404 / error / normal 全部状态常驻；界面明确服务端仍下发完整数据、该开关不是安全边界 |
| U-07 花钱一眼可分，依据只有服务端 `paid` | ✅ | `previewJob` 下发 `paid`；`JobRequest` 里**没有** `paid` 字段，调用方带不进来 |
| U-08 第三类：免费但写盘 | ✅ | 深色 + `写盘` 角标 + 第四种「未标注」。分类**只查表不判定**：`writes_disk` 由服务端 `novel/api/enums.py::would_write` 逐动作下发，`constants/actions.ts` 缺格即画 `unclassified`——「不知道它会不会写盘」和「它只读」是两回事 |
| U-09 确认框显示完整命令 | ✅ | argv 来自 `POST /api/jobs/preview`；取不到就不让发起。Stage5 typed edit 先做专用只读正文 preview，apply 前仍把一次性稿占位、actor/reason、双 SHA 与 `--edit-confirmation` 组成同一合法 `(node, flags)` 交给通用 preview；批量 trim 同样以服务端一次性 manifest 占位预览 `--retain-pretrim-batch`。两者都由确认框的 `run` 才调用专用 API，不用前端自拼展示命令 |
| U-10 不显示预估花费 / 次数 | ✅ | 全屏搜不到 |
| U-11 前端不做判定 | ✅ | `badge`/`paid`/`freshness`/`sev`/`passed` 全部照画。锚点覆盖率由服务端下发 `coverage_threshold` + `coverage_state`，前端只消费 state；Stage4 场景不再展示无独立下游作用的逐场源字数比较，章级预算是否出档只读 `/check/stage4` 的 `budget_band`。派生结构向量由 API view 给出，前端不复算、不写回计划。Stage5 closure state/workpack、edit `required_confirmation` 与 batch trim 双 SHA 全部原样消费服务端投影，不从 finding/正文重判或重算。唯一例外是闸门 DAG——它是 brief 自己画的流程图，不是对服务端结论的再判定 |
| U-12 不从文案正则抠结构 | ✅ | `chapters` / `fix` 全部读服务端字段；证据高亮只做 `indexOf`，且一处没命中时明说 |
| U-13 不按下标切字符串 | ✅ | `HighlightedText` 的 props 里**没有 start/end**，类型上就踩不进去 |
| U-14 中文阅读界面 | ✅ | 中文优先字体栈；行高 2.05–2.15；不 justify；段落用间距分隔；并排两栏共用一个 `measure` |
| U-15 并排是主视图 | ✅ | 两个 shell；`src/components/` 下没有在对照两侧之间切换的 tab |
| U-16 对不齐要显式标出 | ✅ | 未锚定的行有虚线左边框 + 「盖了第 N 拍…这一块的对应没有验证过」；`锚 3/8 拍` 常驻 |
| U-17 空态说「还没跑」并给下一步 | ✅ | `NotRunYet`；语料库空、待办清空各有专门文案。**错误态同理**：后端把「发生了什么」与「下一条命令」分成 `ErrorBody.message` / `.hint` 两格，界面用 `ErrorHint` 单独渲染第二格（`components/state/States.tsx`）——糊回正文，用户就得从一段中文里自己找那条命令。`hint` 为空时**什么都不画**，不占位：那一格空着说的是「后端说不出下一步」，不是「忘了写」。每一处渲染 `errorBody()` 的地方都要带上它，吞掉一条出路比不给更坏（人会以为只能干等）|
| U-18 不发明进度百分比 / 健康分 | ✅ | 没有。唯一的对比条是「预算 / 源文」两个服务端数字之比 |
| U-19 没有取消按钮 | ✅ | 没有 |
| U-20 1280~2560 桌面 | ✅ | `min-w-[1280px]`，页面**没有 max-width**，多出来的宽度流进并排两栏；正文块仍有 measure 上限。左导航可折叠为 58px 窄轨，主网格和底部作业面板共用 `--rail-current-w` 同步释放宽度 |

---

## 7. 机器可查的红线

`npm run seams` 逐条查：

```
src/screens、src/components 里 0 处 fetch(
src/screens、src/components 里 0 处颜色字面量（#xxx / oklch() / rgb()）
src/mocks/*.json 只被 src/api/mock.ts import
src/mocks/ 与 scripts/mock-baseline.json 的 20 份 bytes + SHA-256 一致
```

`npm run typecheck`：mock 与真实实现都满足 `Api`。
