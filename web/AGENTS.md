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

- 只有两种跑书模式：自动模式由确定性 RunSupervisor 推进，Executor 生成、受托 Commander 审阅，
  正常成功后用户只审最终小说；人工模式由用户在各 Stage 页面选择生成、编辑、审批与下一动作。
  两者共用后端前置、授权、质量和交付检查。工程运维手册供维护者查故障，不是普通用户的跑书教程。
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
| 产物、检查、审批、审阅工作包、作业与进度 | 后端判定，经 TanStack Query 按 sid 隔离缓存 |
| 当前书、章号、屏幕 | React Router；换书或章不携带上一页的编辑草稿 |
| 抽屉、待确认动作与瞬态提示 | `useChrome`；不存业务闸门 |
| 筛选、选中项、剧透开关、编辑草稿 | 屏内状态；不冒充已持久化决定 |
| 用户常用模型配置 | 当前 output base 的后端 settings；页面只持 revision 与未保存草稿，保存/清空走 CAS |

- screens/components → hooks → `api.*`；普通 HTTP 只由 client 发起，SSE 由 API 适配器管理。
  屏与组件不 fetch、不拼 URL/argv，不把业务判断移到 hook 后称为“后端统一判定”。写请求带 `X-Novel-UI: 1`。
- `realApi` 与 `mockApi` 共同满足 `web/src/types/api.ts` 的 `Api`。真实模式不得在缺端点时回退 mock，
  不用 localStorage、空数组或普通 404 伪装后端能力。新增字段追完整 types → endpoints → hook → 页面及 fixture。
- `web/src/mocks/` 是冻结历史证据，字节/hash 由 `web/scripts/mock-baseline.json` 与 seams 守护，不能用新输出覆盖。
  现役模拟数据放 `web/src/fixtures/`；两类材料只由 `web/src/api/mock.ts` 接入，不让组件直接消费。
  模拟延迟、作业与 localStorage 覆盖仅服务离线开发，不作为真实链路证据。
- 动作、枚举、费用、写盘语义来自后端；driver/model/effort/执行模式来自 capability，不能手抄节点数量或能力矩阵。
  `default_profiles=null` 不从数组首项代选；用户偏好与产品默认、已受理 run 分开，不保存凭据或两项授权。
  capability 的不可用/未知原因及 settings 读取失败、CAS 冲突都显式呈现；只读 CLI 探针不证明账号或模型已验证。
- 自动 Agent、普通 Job 与人工写回共用书级刷新。首次真实进度快照后复验相关读模型，并取消未返回的旧材料请求；
  后续按真实快照变化刷新，不因 elapsed 等时间噪声反复失效。run、日志与 preview 不进入读缓存失效循环。
  invalidate 不等于已经 refetch；测试须在不重挂页面时验证产物、check、审批与 inbox 更新。
- 作业抽屉只展示全部作业的最新概览，详情转本书进度页。日志只由专用消费者订阅；SSE 以 UTF-8 字节游标续读，
  终态先排空日志再结束。进程状态与盘面交付结论分别展示，不能由退出码或 beacon 猜“可下载”。

## 路由与屏幕职责

本表维护页面职责，`web/src/routes.tsx` 实现路由；变更须同步，不在其他文档复制另一张路由表。

| 路由 | 屏幕职责 |
|---|---|
| `/` | 语料库、顺序批量上传、逐书状态与批量代跑；维护/回收站入口。书名为打开入口，筛选在页头，卡片保持紧凑 |
| `/settings` | 当前 output base 的 Commander、默认 Executor 和节点覆盖偏好；只消费 capability，保存/清空走 revision CAS |
| `/n/:sid` | 重定向到 Stage0；已有产物可从左栏直达对应层 |
| `/n/:sid/run` | 全链进度、Blueprint 生成/正式提交/有效章审/跨章状态/实际问题、事件、工作单元耗时/token 与累计额度；可选择本次运行或跨运行累计，展开尝试和所属日志；节点顺序、续跑前沿、下一处理者和可用动作全部来自后端 |
| `/n/:sid/stage0` | 整本剔除审阅、编辑与批准；完整原文默认展开，Agent 入口放审批栏，不重复 Stage1 分章预览。浏览器人工动作按 `human-ui` 与具体动作理由记审计，Agent/CLI 显式自报 |
| `/n/:sid/stage1` | 校验结论与切分确认独立于正文查询；只读章节索引、切分正文和一次开篇文案，按后端诊断标出重号/缺号/倒退 |
| `/n/:sid/read/:o` | 原文与完整剧情对照，选择当前章或跨章复核；展示节点、状态、约束、证据和实际问题，五维判断与提交 token 同包。完整章编辑先预检，修改后旧预检立即失效 |
| `/n/:sid/global` | 人物、必要知识、关系及源文案按主体审阅；区分正文支持、原钩子独立支持和未决，展示证据与定位失败；钩子独立支持不要求正文重复，也不自动等于审阅接受 |
| `/n/:sid/stage4/:o` | A 候选设计审阅与 B 最终结构审阅；分别消费异常优先工作包，展示主体身份、全章 retain/reconstruct、声明/复算变化及 concerns，再展开父节点、分场呈现和明确端点。普通分场在本层完成；上游疑点可登记转交，明确不会立即调用模型。尚无 plan 时可展示后端 identity plan，Context 缺席仍显式标出 |
| `/n/:sid/stage5/:o` | canonical 正文、材料、finding、改稿与 Commander 闭合；`materials=null` 表示材料缺席，不画假任务书。正文只取当前 Draft，Review 只提供 finding 与计数 |
| `/n/:sid/inbox` | 全书待办与对应审阅入口；不建立第二套编辑面或校验结论 |
| `/n/:sid/stage6` | 文案候选与 typed 定稿；从同一终审 workpack 展开原钩子、接受主张和完整重写正文，独立区分两类事实支持 |
| `/n/:sid/stage7`、`/n/:sid/stage7/:o` | 改名表、漏网候选、成书状态与逐章替换对照 |

`stage5` 无章号时转第 1 章，非法章序也走同一守卫。旧 `review/:o` 与 `check` 仅重定向至 `stage5/:o`
和 `inbox`；不恢复独立 review/check 屏或 Stage3。

## 动作与审阅交互

- 查看、免费写盘、付费调用分别呈现。Agent 单本/批量启动使用独立的付费与正文外发授权；任一缺失只允许预览。
  外发声明展开实际可达 provider 路由；不把被节点覆盖遮蔽的默认项算入授权。配置、授权或 capability 变化使旧 preview 失效，
  start 提交最后成功 preview 的同一请求快照，不能重拼。确认框限制在视口内，正文可滚动，授权和提交始终可见；完整 argv/prompt 可展开核对。
- 批量上传顺序复用单书接口，避免同时持有多本原始字节；免费扫描后单本/全成功批次进入对应授权框。
  部分失败逐本说明、保留成功项，由用户决定是否只运行成功项。批量代跑分别显示 running/pending/rejected，
  上限、互斥和排队由服务端决定；重启后的队列状态只认后端，前端不补建任务。
- 删除项目需完整 sid 二次确认，先移入可恢复回收站；运行/排队中的书由后端拒绝，成功后刷新语料库。
  retention/restore 先 preview 后 apply，展示完整计划与扫描分母；表单变化使旧预览失效，apply 原样提交计划并显式确认。
  TTL 留空是 null，不解释为 0；浏览器和 HTTP 进程不直接修改产物目录。
- 运行页消费 `resume_frontier`、`work_units`、`allowed_controls` 和恢复投影。显示实际生成、机械处理、历史采用、
  决定继承及基础/追加/累计额度；缺失保持 unknown，空单元列表表示尚无登记，界面不计算剩余额度。
  暂停、恢复、熄火、追加额度与原配置续跑分别走对应后端入口，不能借其中一个解除其他阻断或重置累计账。
- Stage2 已读和节点接受是两件事；不能开页即全书已读。Global 未接受不能生成 Blueprint，Blueprint 未接受不能进入 Stage4。
  typed 决定与返工属主来自后端模板/枚举；分组、交付效果、计数也由后端提供，全选仍展开为可单独撤销的逐项决定。
- 各审阅面只从本次完整工作包填表，提交 opaque review token、actor/reason 与 typed 判断；不从其他查询重拼身份或在浏览器算 SHA。
  待审与继承范围、历史来源均照后端呈现，不能从摘要相似、主体删除、空组或旧绿色状态推导接受；主体漂移须重新取包。
- Stage4A 接受只授权 B，B 结构接受只打开结构闸，Stage5 写作授权仍独立。A/B 的工作包、query key 与 typed 表单分开；
  保留扫描分母、六组材料、逐章 reading、concerns、来源和允许动作，INFO/metric/UNVER 等只读项不能自行长出付费按钮。
  历史约束逐项显式保留/撤回/替代并给理由，不默认保留；引用、repair code 和最小基数来自后端模板。
  request_repair/request_replan 决定本身不自动启动付费作业；历史来源未验证时不显示可进入下一阶段。
- Stage5 改稿走专用只读 preview → 明示正式稿/工作稿 → 双 SHA apply，正文一变就废弃旧 preview；完整 CLI 仍经通用 preview 核对。
  多次改稿不自动重复 Review，顶部显式做一次最终 Review。closure 只消费 current workpack/next_step，
  batch trim 原样复制每项审计/章节身份；模板主体不一致、枚举缺失或占位未填完均禁用提交。闭合不得由 finding 数量推导。
- Stage6/7 的正式 typed 决定由后端 current 证明公开；不因来自 CLI/Agent 就要求再补 HTTP 盖章。
  缺失或过期仍进入对应审阅，不能用“补录验收链”代签。两屏使用专用审批面，不再挂通用 GateBar；
  批准不代表后续作业已发起，人物表批准不代表成书可下载。
- Stage7 下载入口消费后端 delivery；文件存在不等于可交付。运行统计区分运行历时、模型活跃区间并集
  和尝试累计耗时；只有部分已知时展示小计与缺失分母。缓存、历史采用与实际网络调用分开，reasoning 不重复加总。

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
| U-11 | 不重算 badge/passed/severity/freshness/预算阈值/结构变化/closure；后端不给结论就显示原始读数或 unknown，流程图也不补审批结论 |
| U-12 | 不用中文文案正则推导章节、修复路由或状态；证据定位用结构化材料，文本匹配失败明确说未命中 |
| U-13 | 不按 Python 字符下标在 JavaScript 切正文；消费服务端片段。`HighlightedText` 不接受 start/end 偏移 |
| U-14 | 中文优先字体，正文行高约 2.05～2.15、不 justify，段落靠间距，并排两侧共享可读列宽 |
| U-15 | 对照默认并排，不用 tab 隐藏另一侧；`ComparePanes` 长文主滚动/结构侧栏，`CompareRows` 用已验证配对行等高，不做比例滚动同步 |
| U-16 | 未对齐区域有可见边界与“对应未验证”文字，保留覆盖范围和回源，不能用空白暗示已对齐 |
| U-17 | 每屏具备 loading/error/not-run/normal；not-run 给下一步，错误按 kind 区分并分别渲染 message/hint，空 hint 不占位。单屏崩溃也明确显示 |
| U-18 | 不发明百分比、健康分或无依据建议；只展示有来源的状态、计数与读数 |
| U-19 | 运行控制只依后端 allowed_controls；暂停/恢复/熄火与取消尚未开始的队列项分开，不能把队列取消套到运行中任务 |
| U-20 | 面向 1280～2560 桌面宽屏；页面不设总宽上限，正文保留 measure。导航折叠为窄轨后主区与底部作业面板同步释放宽度，并留展开入口 |

状态色只从 `web/src/constants/vocabulary.ts` 取，其余颜色使用 `web/src/index.css` 的样式令牌。
`npm run seams` 检查数据访问、颜色字面量、mock/fixture 入口和冻结样本；不另维护“逐条已通过”表。
全局左右键只在带章号屏翻章，Esc 先关确认框再关抽屉；输入控件、contentEditable 和 Ctrl/Alt/Meta 组合键不劫持。
