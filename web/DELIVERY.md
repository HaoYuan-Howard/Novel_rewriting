# 审校台前端 · 交付说明

产品与界面规格的现役落点就是本文件（§1 技术与数据缝 · §3 路由与屏幕职责 · §6 UI 硬规则），
产品边界与八阶段的人类任务见仓根 `README.md`。**当前处于真实模式**：
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

**批量代跑**：语料库屏每本书标题前有勾选框，勾几本后打开批量确认框。未授予流水线
付费权限时主动作显示「批量生成诊断与恢复方案」；授予后显示「授权并开始跑这 N 本」。
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

**跑书进度屏**：`/n/:sid/run`（左导航「进度」按钮）。agent 的 `novel.cli mark`
与 UI / 持久队列共用 `<base>/_service/agent_progress.jsonl`：作业执行器在每个节点开始、
结束时自动打点，界面按 `NODES` 的真实顺序折成进度 + 事件时间线；有步在跑时 2.5s 轮询，
闲置 10s。它是**观察屏不是判定屏**——
「这层过没过」仍归各层屏的 badge（R-13.6）。后端路由：`GET /api/novels/:sid/run`
（`novel/api/routes/run.py`，书目录不存在也 200）。

**agent 一键跑书**：语料库屏每本书的「agent 跑书」，或新书 Stage0 屏的
「agent 从 Stage0 开始跑」→ 确认框摊开
将拉起的完整 argv 与提示词全文。付费授权与正文外发授权使用两个独立复选框：
付费默认不勾，正文外发默认勾选，任一变化都会重新 preview；两项值和声明原文分别留档，
不会因已获付费授权而推定正文外发同意。完整命令与 prompt 默认折叠、按需展开；弹框限制在
视口内，中段独立滚动，底部授权与发起动作始终可见。未授予付费权限时主动作是
「生成诊断与恢复方案」，只取 state / inbox / 下一节点 preview；授予后才显示
「授权并开始跑书」。确认后启动
（`POST /api/agent/runs`，后端以 `claude -p` 子进程唤起 headless agent，
权限白名单：Bash 仅 conda python 前缀、Edit 上界为 base；产品写入边界仍只允许 skill 明列的
typed 人工输入与唯一终审包，正式产物一律经 CLI——见
`novel/orchestrator/agent.py` 模块头的 Spike 结论）。进度在 `/n/:sid/run` 实时跟踪，
那里有 agent 进程状态、日志尾部与「熄火」按钮（`POST /api/agent/runs/:id/stop`）。
进程状态与交付终验是两条正交状态：exit 0 只显示“进程完成”；服务端另以启动/结束代码指纹、
Stage2 typed 闸、当前成书、终审包和 inbox 阻塞轴判 `ready` / `ready_with_disclosures` /
`stopped` / `failed` / `unknown`，界面不得从退出码补推“可下载”。
互斥两个方向都拦：agent 在跑时起任何 API 作业 409，反之亦然。

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
- 作业日志主通道是 `/api/jobs/:id/stream` SSE；事件 `id` 等于 UTF-8 `next_byte`，断线用
  `Last-Event-ID` 续读，终态先排空日志、再发唯一 `end` 并释放连接。旧 `/log?offset=` 保留兼容，
  复用同一字节读取语义；前端不再轮询日志。

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
| 20 份响应 | TanStack Query |
| **`gate` / `seen` / `deep` / `writing`** | 一份 `ApprovalDoc`，也走 Query（`['approvals', sid]`） |
| `job` / `jobLog` | Query + `refetchInterval`（由服务端下发的 `running` 决定） |
| 确认框里的 argv | Query（`api.previewJob`） |
| `sid` / 章号 / 当前屏 | React Router 路径 |
| `drawerOpen` / 待确认动作 | 全应用**唯一一个** Context（`useChrome`） |
| 选中的判据 / 剧透开关 / tab / 编辑中的文本 | 屏内 `useState` |

**gate 走 Query 而不是 reducer**：它在真产品里是服务端的人工验收记录。
`approveGate` 是「POST …/approve」，换实现只改 `src/api/` 一个目录，
`src/screens/**` 与 `src/components/**` 不感知。

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
| `/n/:sid` | 进入该书并重定向到 stage0（新书先审非正文；已有产物仍可从左栏直达各层） |
| `/n/:sid/run` | agent / UI / 持久队列共用的全链运行进度与事件时间线；步骤从 `NODES` 派生，**只观察不判定** |
| `/n/:sid/stage0` | 整本剔除审阅、编辑与批准；上传后可在此直接交给 agent 从当前盘面跑全链，或继续人工审校 |
| `/n/:sid/stage1` | 原文与分章对照，只读 |
| `/n/:sid/read/:o` | 原文与 stage2 骨架对照 |
| `/n/:sid/global` | stage2 全局人物、关系、规则、秘密 |
| `/n/:sid/style` | stage3 文风实测与下发档位 |
| `/n/:sid/stage4/:o` | 计划与写作上下文逐拍对照 |
| `/n/:sid/depth` | 逐章深/浅重构选择与写作启动前确认 |
| `/n/:sid/stage5/:o` | 成稿、材料、验收 finding 与修订的合并工作面；`writing=false` 时守卫跳 `depth` |
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
```

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
- **`null` 与 `[]` 在两层之间不一致，前端要写两套判空**：
  - `stage4.chapter.beats[]`：`keep_value` / `must_appear` / `must_hold` 缺省是 `[]`；
    但 `time_layer` / `place` 会是 `null`
  - `stage5.chapter.materials.beats[]`：**同名字段缺省是 `null`**，
    而且 `time_layer` / `place` / `present` 三个也会是 `null`
- **`/api/jobs` 的元素是嵌套 `JobRecord`**。`api/endpoints.ts` 在唯一数据缝把它适配成抽屉
  使用的扁平 `Job`，五态（`running / ok / landed_red / failed / unknown`）从服务端原样适配；
  日志用字节偏移读取。`landed_red` 表示产物已落盘但报告有阻断红行，不折进 `ok` 或 `failed`。
- 前端日志拉取按 `getJobLog(id, offset) → {lines, offset, done}` 的签名；
  换到 SSE（§1）时签名不用变。

---

## 6. UI 硬规则 U-01～U-20 与逐条自查

规则本身：

1. freshness 保留 fresh / stale / unknown，missing 作为明确显示态；unknown 用问号，missing 用空集符号，不得画成 fresh，也不得彼此复用近似轮廓。
2. badge 保留 pass / amber / fail；amber 表示存在 unver，不是较弱的通过。
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
| U-09 确认框显示完整命令 | ✅ | argv 来自 `POST /api/jobs/preview`；取不到就不让发起 |
| U-10 不显示预估花费 / 次数 | ✅ | 全屏搜不到 |
| U-11 前端不做判定 | ✅ | `badge`/`paid`/`freshness`/`sev`/`passed` 全部照画。设计稿原有的两处显示阈值已经收干净，而**两处的收法不同型**：<br>· 锚点覆盖率 —— 服务端下发 `coverage_threshold` + `coverage_state`（阈值来自 `ANCHOR_COVERAGE_WARN`，state 由 validator 反推），前端只消费 state；<br>· 拍级 `预算/源文` —— **服务端刻意没有拍级判定**：出档是章级判据（`sum(budget_chars)` vs `sum(span)`，`/check/stage4` 的 `budget_band`）。前端因此只显示比值读数与对比条，判定指向本屏底部的 stage4 校验。⚠ 章级的 `in_band` 服务端**也没有**（`contracts/schema/api.py::Stage4IndexRow` 明写）——那是判据结论，要并排显示就去 join 报告。<br>两条各有守卫：`tests/test_api_discipline.py` 的 `test_frontend_does_not_rejudge_anchor_coverage` 与 `test_frontend_does_not_rejudge_beat_budget_band`。<br>唯一剩下的例外是闸门 DAG——它是 brief 自己画的**流程图**，不是对服务端结论的再判定 |
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
