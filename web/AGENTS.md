# 前端开发与界面规范

本目录是 React/Vite/TypeScript 审校台。本文件统一维护前端开发方法、产品边界、页面职责和 UI 规则；
共同工程纪律见根 `AGENTS.md`，响应字段与取值域见 `contracts/schema/api.py` 和 `docs/schema/_api.md`。
只保留影响开发决策的规则，不重复字段手册、组件目录树、历史实现过程或验收成绩。

## 开发与验证

在 `web` 目录执行以下命令；缺依赖时先 `npm ci`。TypeScript 使用 2 空格、单引号、无分号，
组件 PascalCase，hook 命名 `useXxx`。

```bash
npm run dev                         # 127.0.0.1:5173；开发代理默认连接 8765
npm run test -- <相关测试文件>       # 定向交互与数据消费测试
npm run typecheck                   # mock/real 与 Api 类型一致
npm run seams                       # 数据访问、颜色令牌与冻结样本守卫
npm run build                       # web/dist，供后端同源托管
```

检查选择与整批门槛遵循根指南的测试纪律，说明修改不自动触发前端四件套。
跨层联调验证页面动作 → HTTP → 服务 → 最终产物及刷新、重启后的读回；UI 改动附截图。
类型检查或 mock 通过不能代替真实消费者验收。

| 变更 | 入口 |
|---|---|
| 页面、路由与可复用交互 | `web/src/routes.tsx`、`web/src/screens/`、`web/src/components/` |
| 查询、动作与书级刷新 | `web/src/hooks/`、`web/src/api/queryKeys.ts` |
| HTTP 与错误适配 | `web/src/api/client.ts`、`web/src/api/endpoints.ts` |
| 响应映射与后端枚举 | `web/src/types/`、`contracts/schema/api.py` |
| 状态色、排版与样式令牌 | `web/src/constants/vocabulary.ts`、`web/src/index.css` |

## 产品边界

- 产品现役为**全自动单模式**（确定性 orchestrator 推进）。现役链是 14 个
  `str06.` 节点（B2 五节点重建，2026-09-27 D9：Stage2 book／chapters／names／
  associate／compile → Stage4 目标故事与确定性编译 → Stage5 按章正文与 Hook →
  Stage7 命名与交付）；Stage6 整层与三候选改选面已退役，
  `/n/:sid/stage6` 路由与 `approveHook` 写面随 F8b 删除。F8c 起旧链读屏
  （read / global / stage4 / stage5 / stage7）、待办屏（inbox）与人工闸整族
  （approvals / workpack / reset）连同路由、数据缝、类型与 mock 一并退役：
  上传前确认付费/正文外发授权与 profiles，并带客户端幂等键自动开跑。Stage 页只读监控，
  不提供任何人工编辑/批准写按钮；人工逐 Stage 模式已退役。工程运维手册供维护者查故障，不是普通
  用户的跑书教程。
- **术语**：界面与类型里的 Agent / `/agent/*` 仅指**整书代跑编排**；模型侧配置文案为
  「模型执行配置」（wire `kind:"legacy"`）。不要把 Agent 写成 CLI driver / session。
- 最终交付是小说文件，运行统计与披露在前端读取权威账；不要求生成或阅读 Markdown 终审报告。
- 面向单人、本机、桌面浏览器。服务仅绑定 loopback；产品不包含登录、多人协作、通知和移动端。
- 用户负责提出需求、验收结果和人工兜底。界面提供证据、可审阅差异与明确待决事项，不用分数代替审阅。
- 新书保留源文情节骨架，全文重写并替换人名；章号与源章恒等，不合并、不拆分、不删除、不重排。
- 前端与 API 同源同端口，运行入口见 `docs/OPERATIONS.md`。开发代理只服务热更新，不要求运行用户另起前端进程。
  `novel/api/app.py::_mount_ui` 保证深链接刷新返回 SPA；未知 `/api/*` 仍返回结构化 404，不能吞成 HTML。
  未构建时 UI 返回带操作提示的 503，API 继续可用。

## 数据与状态归属

| 状态 | 唯一归属 |
|---|---|
| 产物、层级复检、作业与进度 | 后端判定，经 TanStack Query 按 sid 隔离缓存 |
| 当前书、屏 | React Router；换书不携带上一页的编辑草稿 |
| 抽屉、待确认动作与瞬态提示 | `useChrome`；不存业务闸门 |
| 筛选、选中项、剧透开关 | 屏内状态；不冒充已持久化决定 |
| 用户常用模型配置 | 当前 output base 的后端 settings；页面只持 revision 与未保存草稿，保存/清空走 CAS |

- screens/components → hooks → `api.*`；普通 HTTP 只由 client 发起，SSE 由 API 适配器管理。
  屏与组件不 fetch、不拼 URL/argv，不把业务判断移到 hook 后称为“后端统一判定”。写请求带 `X-Novel-UI: 1`。
- `realApi` 与 `mockApi` 共同满足 `web/src/types/api.ts` 的 `Api`。真实模式不得在缺端点时回退 mock，
  不用 localStorage、空数组或普通 404 伪装后端能力。新增字段追完整 types → endpoints → hook → 页面及 fixture。
- `web/src/mocks/` 是冻结历史证据，字节/hash 由 `web/scripts/mock-baseline.json` 与 seams 守护，不能用新输出覆盖。
  F8c 后只剩 7 份（目录/概览/Stage0 输入与 Stage0/1 作业样例）；`web/src/fixtures/backend_v1/` 是现役
  数据源（enums / stage1 / agent_capabilities / str06_candidate），旧链读屏抓包与 `plot_v1` 完整剧情链
  夹具已随旧链整层退役，恢复来源为 Git 历史。两类材料只由 `web/src/api/mock.ts` 接入，不让组件直接消费。
  模拟延迟、作业与 localStorage 覆盖仅服务离线开发，不作为真实链路证据。
- 动作、枚举、费用、写盘语义来自后端；Provider/Model/Effort/Thinking 来自 capability，不能手抄节点数量或能力矩阵。
  `default_profiles=null` 不从数组首项代选；用户偏好与产品默认、已受理 run 分开，不保存凭据或两项授权。
  capability 的不可用/未知原因及 settings 读取失败、CAS 冲突都显式呈现；只读探针不证明账号或模型已验证。
- 自动 Agent、普通 Job 与人工写回共用书级刷新。首次真实进度快照后复验相关读模型，并取消未返回的旧材料请求；
  后续按真实快照变化刷新，不因 elapsed 等时间噪声反复失效。run、日志与 preview 不进入读缓存失效循环。
  invalidate 不等于已经 refetch；测试须在不重挂页面时验证产物与 check 更新。
- 作业抽屉只展示全部作业的最新概览，详情转本书进度页。日志只由专用消费者订阅；SSE 以 UTF-8 字节游标续读，
  终态先排空日志再结束。进程状态与盘面交付结论分别展示，不能由退出码或 beacon 猜“可下载”。

## 路由与屏幕职责

本表维护页面职责，`web/src/routes.tsx` 实现路由；变更须同步，不在其他文档复制另一张路由表。

| 路由 | 屏幕职责 |
|---|---|
| `/` | 语料库、顺序批量上传（上传前授权并自动开跑）、逐书状态与批量代跑；维护/回收站入口。书名为打开入口，筛选在页头，卡片保持紧凑 |
| `/settings` | 当前 output base 的默认模型执行配置与节点覆盖偏好；只消费 capability，保存/清空走 revision CAS |
| `/n/:sid` | 重定向到 Stage0；`/n/:sid` 下只剩 run / stage0 / stage1 三个可达屏 |
| `/n/:sid/run` | 全链进度（stage0.remove、stage1.partition 与 14 个 `str06.` 节点顺序）、事件、工作单元耗时/token 与累计额度；可选择本次运行或跨运行累计，展开尝试和所属日志；节点顺序、续跑前沿、下一处理者和可用动作全部来自后端。层级复检不在这屏：`/check` 取值面只剩 stage0/stage1，结论归那两屏的 CheckPanel |
| `/n/:sid/stage0` | 整本剔除只读监控；完整原文默认展开。上传已自动开跑时通常不需在此发起作业 |
| `/n/:sid/stage1` | 校验结论与切分只读展示；章节索引、切分正文和一次开篇文案，按后端诊断标出重号/缺号/倒退 |
| `/str06/:candidate` | STR-06 链只读监控：workflow 状态、节点盘面事实、ledger 成本、Characters／Blueprint／Design／WriterBookIR 导航、章正文与章回执、Hook、NameMap 与成书 manifest，以及成书全文下载。不进左导航、无 check 面板、无写按钮。真实书的 run 目录里也有 `runtime/candidate_workflow.json`，书名 sid 同样可作为 candidate 名监控真实跑书 |

F8c 起旧链读屏（`read/:o`、`global`、`stage4/:o`、`stage5/:o`、`stage7`、`stage7/:o`）与待办屏
（`inbox`）整层退役，老路由重定向一并取消：`review/:o → stage5/:o` 与 `check → inbox` 的目标屏都已删，
未匹配路径由兜底 `* → /` 处理，不保留指向空气的重定向。新链 Stage2/4/5/7 的只读监控统一在
`/str06/:candidate`，不在 `/n/:sid` 下另开屏。

## 动作与审阅交互

- 查看、免费写盘、付费调用分别呈现。上传与 Agent 启动使用独立的付费与正文外发授权；任一缺失只允许预览。
  外发声明展开实际可达 provider 路由；不把被节点覆盖遮蔽的默认项算入授权。配置、授权或 capability 变化使旧 preview 失效，
  start 提交最后成功 preview 的同一请求快照，不能重拼。确认框限制在视口内，正文可滚动，授权和提交始终可见；完整 argv/prompt 可展开核对。
- 批量上传顺序复用单书接口，避免同时持有多本原始字节；上传前统一确认授权与 profiles，每本独立幂等键，成功后自动开跑。
  部分失败逐本说明、保留成功项。批量代跑分别显示 running/pending/rejected，
  上限、互斥和排队由服务端决定；重启后的队列状态只认后端，前端不补建任务。
- 删除项目需完整 sid 二次确认，先移入可恢复回收站；运行/排队中的书由后端拒绝，成功后刷新语料库。
  retention/restore 先 preview 后 apply，展示完整计划与扫描分母；表单变化使旧预览失效，apply 原样提交计划并显式确认。
  TTL 留空是 null，不解释为 0；浏览器和 HTTP 进程不直接修改产物目录。
- 运行页消费 `resume_frontier`、`work_units`、`allowed_controls` 和恢复投影。显示实际生成、机械处理、历史采用、
  决定继承及基础/追加/累计额度；缺失保持 unknown，空单元列表表示尚无登记，界面不计算剩余额度。
  暂停、恢复、熄火与追加额度分别走对应后端入口；不再提供「按原配置继续」的 human-gate 续跑对话框。
- Stage 页只读监控。不要在 Stage 屏恢复人工编辑、批准闸门、补录验收链或改稿提交按钮；
  Stage6 三候选改选面已随 Stage6 整层退役（F8a），`approveHook` 写面与前端 Stage6 消费面
  （屏、路由、端点、query key、mock 与 fixture）已随 F8b 一并删除；F8c 起旧链读屏与人工闸
  整族（approvals / workpack / reset）连同端点、query key、类型与 mock 全部退役，新链没有
  待办面也没有第二套编辑面。
  现役判据（Guard M1–M6、章级机械验、命名残留、交付回执链）的结论由后端公开，
  不因来自 CLI/Agent 就要求再补 HTTP 盖章。
- 成书下载入口消费 `/api/str06/candidates/{candidate}/book`；文件存在不等于可交付。
  运行统计区分运行历时、模型活跃区间并集和尝试累计耗时；只有部分已知时展示小计与缺失分母。
  缓存、历史采用与实际网络调用分开，reasoning 不重复加总。

## UI 硬规则

以下 U 编号用于稳定引用，不记录“已实现”勾选和历史测试成绩。

| 编号 | 规则 |
|---|---|
| U-00 | 不知道、未运行、缺失、合法空值和没问题分别表达；`EmptyIsSuspicious` 与 `EmptyIsGood` 不合并 |
| U-01 | freshness 保留 fresh/stale/unknown，missing 独立显示；问号与空集符号不能冒充 fresh。状态兼有文字和形状，紧凑矩阵也要同屏图例及 title/aria-label |
| U-02 | pass/amber/fail 分开；amber 明示存在未验证项，不表示已证明没问题 |
| U-03 | hard/info/metric/unver 分开；metric 是读数，不使用警告色或警告语义 |
| U-04 | 作业 running/ok/landed_red/failed/unknown 原样适配；landed_red 引导看报告。浏览器刷新不等于服务重启；恢复后的未知状态只认后端，不能把历史 running 自行改成 ok |
| U-05 | 空数组按字段解释，不统一写“没问题”；样本缺失不等于产物未运行 |
| U-06 | 部分剧透遮蔽的披露在 loading/empty/404/error/normal 都常驻；说明完整数据仍已下发，不宣传为安全边界 |
| U-07 | 费用只认服务端 paid；调用方不能用请求字段自报免费 |
| U-08 | 免费写盘有明确标识；缺 writes_disk 显示未标注，不能当作只读 |
| U-09 | 确认框呈现服务端完整命令；取不到有效 preview 就不能执行，专用编辑/批量输入也不前端拼 argv |
| U-10 | 不显示预估费用或预估调用次数；实际用量与累计额度单独展示 |
| U-11 | 不重算 badge/passed/severity/freshness/预算阈值/结构变化；后端不给结论就显示原始读数或 unknown，流程图也不补审批结论 |
| U-12 | 不用中文文案正则推导章节、修复路由或状态；证据定位用结构化材料，文本匹配失败明确说未命中 |
| U-13 | 不按 Python 字符下标在 JavaScript 切正文；消费服务端片段。任何高亮组件都不接受 start/end 偏移 |
| U-14 | 中文优先字体，正文行高约 2.05～2.15、不 justify，段落靠间距，并排两侧共享可读列宽 |
| U-15 | 对照默认并排，不用 tab 隐藏另一侧；长文主滚动/结构侧栏，配对行等高，不做比例滚动同步 |
| U-16 | 未对齐区域有可见边界与“对应未验证”文字，保留覆盖范围和回源，不能用空白暗示已对齐 |
| U-17 | 每屏具备 loading/error/not-run/normal；not-run 给下一步，错误按 kind 区分并分别渲染 message/hint，空 hint 不占位。单屏崩溃也明确显示 |
| U-18 | 不发明百分比、健康分或无依据建议；只展示有来源的状态、计数与读数 |
| U-19 | 运行控制只依后端 allowed_controls；暂停/恢复/熄火与取消尚未开始的队列项分开，不能把队列取消套到运行中任务 |
| U-20 | 面向 1280～2560 桌面宽屏；页面不设总宽上限，正文保留 measure。导航折叠为窄轨后主区与底部作业面板同步释放宽度，并留展开入口 |

状态色只从 `web/src/constants/vocabulary.ts` 取，其余颜色使用 `web/src/index.css` 的样式令牌。
`npm run seams` 检查数据访问、颜色字面量、mock/fixture 入口和冻结样本；不另维护“逐条已通过”表。
Esc 先关确认框再关抽屉；输入控件、contentEditable 和 Ctrl/Alt/Meta 组合键不劫持。
（F8c 后 `/n/:sid` 下没有带章号的屏，全局左右键翻章随旧链读屏一起退役。）
