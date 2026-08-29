# Novel rewriting

把一本中文网络小说经过 stage0、stage1、stage2、stage4～stage7 七个现役层处理为一部新书
（Stage3 编号保留为空洞）：保留情节骨架，重写文字，
替换人名，最终交付以书名命名的成书 `<书名>_rewriting.txt`。

单书七层主链、agent 驱动全链和前端真实服务主路径已经验收。当前开发重点是
**正确性、恢复能力、质量闭环与契约收口**；尚未完成、尚未验证和待产品裁决的事项统一登记在
`docs/REQUIREMENTS.md`。它是唯一活动待办，README 不重复维护任务状态。

## 产品边界

- 单人、本机、桌面浏览器；服务只绑定 `127.0.0.1`，不做登录、协作、权限、通知或移动端。
- 输入是一部中文网络小说，七个现役层依次完成清洗、分章、抽取、计划、写作、文案、
  改名与成书，最终产出 `stage7/<书名>_rewriting.txt`。
- 工具的主要风险不是程序崩溃，而是 LLM 造事实、提前泄密或漏掉内容后仍写得通顺。因此产品是
  “审校台”而不是 dashboard：主体动作是并排对照证据，再由人做决定。
- stage0 批准是全链第一个人工硬闸；Stage4 有两张不同的票：A 候选先经全书策略 + 逐章正式审阅，
  只授权 B；B 编译出的计划再经最终逐章结构批准，才打开 s4。Stage5 的付费写作授权与二者独立；
  stage6 与 stage7 的人工定稿是各自产物进入下游的语义闸。
- Stage2 称名投影可用后，人可一边复核 global、一边让 blueprint 逐章计算；两支必须在 Stage4A
  前汇合为 current 正式 global + current blueprint、联合 HARD 通过与 current blocking 面全 PASS 人审。同书的
  review / typed edit / producer 写回仍串行，不能把“人机并行”解释成并发改文件。
- 看、免费但写盘、付费 LLM 三类动作必须一眼可分。付费前由服务端下发完整 argv 并二次确认；
  不显示猜测的价格或调用次数，发起后不承诺无损取消在途调用。

## 处理链与成本

付费节点会调用 LLM；确定性部分（分章、Stage5/6 从 Stage1 即时机械测量、锚点回取、替换、组装、
全部机械校验器）不花钱。全链调用数随章节数与真实重试变化，一本小书通常在数十分钟量级。

```
stage0  非正文剔除 + 人工审阅闸门              0 成本，无 LLM，**必须人批准才能往下走**
stage1  摄入 + 分章                          0 成本，无 LLM
stage2  全书级抽取（人物/关系/规则/秘密/弧线）  1 次调用
          ├─ 逐章抽取（事实原子/因果/舞台 + 保真点） 每章 1 次、最多 32 并发
          └─ 全局事实逐项人审                         0 成本，与逐章计算重叠
        两支汇合：blueprint 联合 check-only + current blocking 面全 PASS 人审；随后才进 Stage4A
stage4  A：1 个公开付费节点/授权；内部 1 份整书策略 + 每章 1 个私有 session
           策略串行先行，逐章最多 4 并发；全部通过后才确定性聚合并原子落盘
        A 正式审阅：全书策略 + 逐章 accept/repair；拒绝章可 hash-bound 定向修订 0 成本审阅
        B：机械基线先解唯一绑定；只给真歧义章开私有 session（最多 4 并发）
           全机械书 0 wire；逐章闭合后确定性聚合并编译计划
        最终人工逐章结构批准（不自动触发重规划） 0 成本
        写作上下文（最终批准后独立确定性组装，写作的唯一输入） 0 成本
stage5  全书逐章闭卷写作；正式稿唯一落 `stage5/chNN.json`，默认补齐全部缺章
        最多 32 并发，`--chapters` 只定向重写指定章；旧 Stage5 目录报 `legacy_incompatible`
        全书机械验收（判成品）+ 修订式修复（旧称「反哺」） 0 成本 / 逐章调用
stage6  开篇文案：一次调用产三候选，人定稿       1 次调用
stage7  人名替换：一次调用产新人物表（只产决策）  1 次调用
        替换与成书组装                          0 成本
```

Stage2 逐章调用以 `run_id` 归组。通过 JSON/schema/动态判据/章节 scope 的成功章会写入书内的
验证后检查点；同一 run 中断后可在不新增 wire 的情况下重验恢复。一个并发波次的可人工修复响应
只生成一份 batch workpack、一次原子 resolution 和一次 resume；reasoning 通道里已经完整过闸的
JSON 直接采用，不再为了取得普通 content 重采。损坏、跨 run、输入或 SHA 漂移会关闭式停止，
`--resample` 与 `--chapters` 的刷新语义不会被检查点绕过。检查点不是正式 artifact 或普通 LLM 缓存，
不改变 `chapter_blueprints.json` 的 schema，也不改变 `micro_events`、`beats` 或 fact atoms。
逐章扇出的供应商前缀 warm-up 仍默认开启，未新增关闭型产品 flag；本次不另做付费 A/B，收益只在
下一次自然跑书按 `run_id` 回读 wire/章、checkpoint restore、repair batch、reasoning token 与墙钟。

Stage4A 对外仍只有一条 `stage4.design` 付费 argv 和一次 `--yes` 授权，不开放公开的
`--chapters`。内部先让一条小型整书策略请求只决定宏观重构意图与章间权重；策略过
JSON/schema/动态判据后立即写验证后 checkpoint，再按章启动互相独立的私有 session，硬上限 4 并发。
每章通过即 checkpoint，同一 `run_id` 恢复时只补缺失或失败单元，已验策略和章节不重发 wire。
这些 checkpoint 都不是正式 artifact：必须等策略与全部章节齐全，由代码确定性还原正式 alias、排序并
组装整书候选，重跑整书级 schema 与全部 HARD，最后才原子写入 `stage4/blind_candidate.json`；任何
中断或聚合失败都不得留下部分正式候选。候选落盘并不授权 B：必须先提交绑定当前候选的正式设计审阅。
已接受且主体未变的章可在下一次整书原子决定中复用；被拒章只按关闭式约束定向修订，策略被拒才整书
重规划。Stage4B 仍是另一个公开节点和另一次授权，但内部先由代码生成机械基线：唯一可解章不发
wire，只有真歧义章各开一条私有 session（硬上限 4 并发），每章验证后立即 checkpoint，再按章号
确定性聚合并走同一 compiler/HARD。单章失败不会抹掉其他已验章；模型声称 `infeasible` 只有经代码
构造性证明后才能成为正式终态，否则只回对应章 session。无法自动闭合的 unit 走绑定 run/request/code
与主体 SHA-256 的 0-wire replacement，不重发已经有效的模型输出。

Stage4 两次人审都先读服务端生成的异常优先工作包：CLI 为
`novel.cli approval workpack design|structure <SID> --base <BASE>`，HTTP 为对应的
`GET /api/novels/:sid/approvals/{design|structure}/workpack`。工作包固定给出 blocking、语义关注、
披露、metric、阶段延期与全章 coverage 六组，只缩小阅读面；它不预填决定、不自动接受，
INFO / metric / UNVER 也不会因此获得付费返工入口。

Stage5 同一 run 的正常中断继续复用原 `run_id` 恢复 checkpoint。若代码已变化、旧 run 已完成
响应与 checkpoint 却停在 foreign `prepared` 提交尾窗，默认付费写作会在 client/driver 前关闭式
拒绝；只能显式先 `--emit-prepared-recovery-workpack <SOURCE_RUN_ID>`，再在同一个新 target run
提交 hash-bound `--recover-prepared`。该桥完整复验旧 ledger/session/request/sidecar 与当前
上游/HARD/live baseline，零 driver、零 wire，source sidecar 只读；收口后仍须单独跑
`stage5.review`。命令与中断恢复细节见 `.claude/skills/novel-pipeline-operator/references/recovery.md`。

Stage5 的 canonical v2 产物与单臂工作流已完成工程回归；现役 atom / scene / material 写作契约属于
设计改进，但没有完成与此前 event-list / beat-material 契约的受控双书付费 A/B，不能据此宣称质量更高、成本更低
或速度更快。成稿还显著受 Stage1～4 上游材料影响，证据边界见 `docs/schema/stage5.md`。

**最高风险不是「出不了书」，是「错误的剧情被静默接受」。** LLM 的错误不会崩溃：它会造一个
读起来毫无破绽的假事实、会在第 1 章说出第 14 章才揭晓的秘密、会把一段该保留的关键台词整段丢掉
而成稿依然通顺。系统的全部设计张力都在这里——这决定了 `docs/BACKEND_CONTRACT.md` 里绝大多数的
`Q-` 条目。作业单元的命名体系（`stage` 与 `node` 两根正交轴）与章号恒等式见该文档 §一。

## 七个产物层的人类任务

| 阶段 | 系统动作 | 人的主要检查 |
|---|---|---|
| stage0 | 剔除番外、后记、尾声、作者有话说 | 整本审阅删除范围；可直接修正范围或正文；批准后才下行 |
| stage1 | 确定性分章 | 原文与切分并排，确认无漏章、错章、断号 |
| stage2 | 全书事实底座与逐章事实原子/舞台证据 | 称名投影可用后即可让 blueprint 计算，并与 global 复核重叠；Stage4 前 blocking 面必须全部 PASS，disclosure 未决项继续披露 |
| stage4 | 盲设计、证据绑定、计划编译与写作上下文 | 先审 A 的全书策略与逐章候选，接受或定向返修；再审 B 编译计划的入口/分组/披露读数、证据落点与 binding concerns |
| stage5 | canonical 闭卷写作与机械验收 | 材料与成稿、原文与改写、finding 与修订面 |
| stage6 | 生成三个开篇文案候选 | 三候选并排，人选一条定稿 |
| stage7 | 生成人名决策，确定性替换并组书 | 新旧名表、漏网候选和逐章替换前后；人定稿 |

界面规格（路由与屏幕职责、UI 硬规则 U-01～U-20、技术与数据缝）见 `web/DELIVERY.md`。

## 文档职责

| 文档 | 负责 | 不负责 |
|---|---|---|
| 本文件 | 项目入口、**产品边界与人的任务**、处理链与成本、运行方式、备份与恢复、接手顺序 | 需求明细、完成日志、踩坑长文 |
| `AGENTS.md`（= `CLAUDE.md` 符号链接） | agent 入口摘要：pytest 三坑、架构要点压缩版、编码风格、工作纪律；命令细节只留指针 | 详细契约与证据；与 `docs/BACKEND_CONTRACT.md` 冲突时以后者为准 |
| `docs/REQUIREMENTS.md` | 仅列未完成事项、阻塞、验收条件与待裁决问题 | 已完成能力、施工过程、界面设计细节 |
| `docs/LESSONS.md` | 编号化的可迁移工程经验与真实踩坑 | 任务状态、需求清单 |
| `web/DELIVERY.md` | **界面规格**：技术与数据缝、路由与屏幕职责、组件清单、UI 硬规则、前端开发命令 | 项目总任务清单、联调历史 |
| `docs/schema/` | 已实现后端的产物与 API 契约（现在时，不含施工注记） | 后续工作排序、schema 推导史 |
| `tests/README.md` | 测试分层、跑法与 `--require-corpus` 硬验收操作步骤 | manifest 字段契约（见 `docs/schema/corpus.md`） |
| `docs/BACKEND_CONTRACT.md` | 命名体系、冻结编号（R/Q/B）、双向覆盖率、修复路由、守卫耦合、证据基线；§十 是唯一历史落点（只留重大决策） | 产品与界面规格（在 README 与 `web/DELIVERY.md`）、活动开发指导 |

README 只保留稳定入口和当前能力；“做过什么”由 Git 历史与
`docs/BACKEND_CONTRACT.md` §十回答。可迁移经验整合进 `docs/LESSONS.md`。

## 当前工作边界

- 只从 `docs/REQUIREMENTS.md` 仍存在的条目开工，优先处理 P0；待产品裁决项先确认语义再实现。
- 条目需要跨后端、前端、契约、测试或文档时，完成整条真实消费路径，不按历史阶段边界截断。
- 不能用 mock、空数组、普通 404 或本地存储伪装尚未提供的后端能力。
 产品语义与屏幕行为以本文件的「产品边界 / 七个现役阶段的人类任务」与 `web/DELIVERY.md` 为准；
  若它与真实 API 冲突，按
  `docs/REQUIREMENTS.md` 的待裁决项处理，不静默选择一边。

## 当前已具备的主路径

- `novel.cli` 可驱动单书七层主链，最终组装成书；完整操作规程位于
  `.claude/skills/novel-pipeline-operator/SKILL.md`。
- `web/` 默认以 `VITE_USE_MOCK=false` 连接真实服务。九条读屏 GET 与三条人工写回 POST
  已接通；写操作遵循 preview → 确认 → CLI 作业执行，作业失败由 `JobRecord.state='failed'`
  表达，而不是伪装成 HTTP 错误。
- 服务进程只写 `_service/`，产物变更经 CLI 子进程完成（R-11.3）。`stage6/edit` 与
  `stage7/edit` 因此只走 CLI；浏览器仅提供对应批准入口。
- 人工闸门可委托 agent 按规则代审，依据持续写入该书目录根的
  `<书名>_review.md`。真实书全链验收和账本摘要见 `docs/BACKEND_CONTRACT.md` §十。

## 接手顺序

1. 运行 `git status --short`，确认并保留工作区已有改动。
2. 阅读 `docs/REQUIREMENTS.md`，只从仍存在的条目中选任务；待裁决项先取得产品决定。
3. 做前端时再读 `web/DELIVERY.md`（规格与三道缝）与 `docs/BACKEND_CONTRACT.md` §八（证据基线）；
   做后端缺口时按条目点名的 schema 和代码落点回查。
4. 覆盖真实消费者和失败路径；验收通过后删除对应活动条目。

## 运行与验证

**要用界面：双击 `scripts\start.cmd`。** 它按需构建前端、起服务、开浏览器，
只有一个进程一个地址（`http://127.0.0.1:8765/`），Ctrl+C 全停。
`-Base` 指定语料根——**含各书目录的那一层**，省略即仓根 `outputs`，进界面先看到它下面的
全部书、自己挑一本；`-Dev` 换成 Vite 热更形态。
界面与 API 同源：服务自己把 `web/dist` 挂在同一个端口上（见 `web/DELIVERY.md` §1）。

后端使用 conda 环境 `novel_refactor`：

```powershell
conda run -n novel_refactor python -m pytest -q
conda run -n novel_refactor python -m novel.api --base <Output目录>
conda run -n novel_refactor python -X utf8 -u -m novel.cli inbox <sid> --base <Output目录>   # 八类待办清单（0 成本）
```

pytest 默认复用仓根 `.test_runtime` 与 `.test_cache`，不会随运行次数新增目录。agent 执行时必须
绕开文件系统沙箱；不要为每次运行另造带测试名或时间戳的 `--basetemp`。若 Windows 路径预算不足，
固定使用同一个仓外短路径（如 `--basetemp=E:\_tr`），pytest 会在下一次启动时自行清空并复用它。
正常退出时测试钩子会趁创建者仍有权限恢复普通用户 ACL。若进程被强杀、钩子没机会执行，应先以
管理员权限修复并删除那一份临时目录，再回到固定路径；继续换目录名只会积累垃圾，并使代码快照
扫描撞上不可读目录。

API 只监听 `127.0.0.1:8765`。前端默认配置位于 `web/.env`；真实联调时应为
`VITE_USE_MOCK=false`。前端开发命令（`npm ci` / `typecheck` / `seams` / `build` / `dev`）
的唯一落点是 `web/DELIVERY.md`。

下面这段只服务独立保留的**显式 legacy DeepSeek 回滚路径**，不是八个 agent executor 或
Commander 主路的启动条件。若操作者明确选择该回滚，且本机代理使用 TUN/fake-IP、后台调用
DeepSeek 出现 `SSL UNEXPECTED_EOF`，在根
`.env` 显式配置 `DEEPSEEK_PROXY=http://127.0.0.1:<本地 HTTP 代理端口>`。该配置由
DeepSeek 传输层直接读取，不依赖启动服务的终端是否继承代理环境变量。
配置后先运行 `conda run -n novel_refactor python -m novel.cli doctor --llm-transport`：
它使用同一代理与超时，只探测 DNS/TCP/TLS/HTTP，不发送正文或 API key；收到 401/403 也说明
传输已打通。只有这一步成功后才值得启动一次真实付费节点。

正常 agent 路径从 `GET /api/agent/capabilities` 选择 Claude Code 或 Codex 的 Commander / executor
profile；节点 driver 在正文外发前用对应 CLI 做零模型 probe。缺 DeepSeek 配置不得阻断这条主路，
也不得因旧传输 doctor 失败而自动切换 provider。

只跑编译检查不代表联调完成。交付前还要在真实服务上覆盖正常、未运行、错误和空态，
并确认关闭 mock 后没有样本正文进入网络响应或构建产物。

## 备份与恢复

**当前验收范围只有代码灾备。** 版权语料与付费产物按 2026-08-16 裁决不备份：它们一旦丢失
需要重新取得语料并重新付费生成，这是已接受的残余风险，**不得把代码恢复能力说成数据灾备能力**。
代码的权威副本是 GitHub 私库，`gh repo view` 核验 `visibility=PRIVATE` 且远程 `main` 与本地
`HEAD` 一致才算落地；同一块磁盘上的快照只是恢复演练，不冒充异地副本。

```powershell
# 建快照：文件集来自 git ls-files --cached --others --exclude-standard，
# 只放行 .env.example 与 web/.env，硬排除其他 .env*、settings.local.json、
# outputs/、Output/、Novel_data/、Test_data/、缓存与构建目录，并拒绝跟随符号链接
conda run -n novel_refactor python tools/backup_snapshot.py create-code `
  --repo . --archive E:\offline-backup\novel-code.tar.gz

# 恢复只允许空目录：先查路径穿越/重复成员/归档哈希，落盘后再逐文件复核
conda run -n novel_refactor python tools/backup_snapshot.py restore `
  --archive E:\offline-backup\novel-code.tar.gz --destination E:\restore-drill\novel
```

只靠 `.gitignore` 不足以守版权边界，上面那道硬排除是第二道门。若将来重新裁决要备份私密数据，
`create-private` 必须显式给数据根、目标位于受控加密离线介质，并带 `--acknowledge-encrypted-offline-target`；
那个 flag 是操作者对介质的声明，工具本身不加密。

**演练判据**（每季度、依赖或契约大改后、备份目标迁移后各一次）：空目录恢复后确认
`.env.example`、`pyproject.toml`、`web/.env`、`web/package.json`、`tests/fixtures/manifest.json` 存在，
根 `.env`、`outputs/`、`Test_data/`、`.llm_cache/`、`.claude/settings.local.json` 不存在，
再在恢复目录跑 `conda run -n novel_refactor python -m pytest -q tests/test_fixture_registry.py`
（全合成数据，逐文件校验固定 SHA-256）。记录日期、归档 SHA-256、恢复文件数与测试结果——
只记"备份任务成功"不算演练。最近一次演练的读数见 `docs/BACKEND_CONTRACT.md` §十。

## 关键目录

```text
contracts/       后端 schema、判据注册与跨层常量
novel/           CLI、运行时、API、视图与 stage 实现
web/             React + Vite + TypeScript 前端
docs/schema/     后端产物/API 契约
tests/           后端与文档守卫
tools/           探针、对比与 schema 派生工具
.claude/skills/  agent 端到端操作规程
```

本仓无外部目录依赖。早期快速原型仓 `../Novel_refactoring/`（含 163 本语料与 3 本书的
历史产物）已于 2026-08-16 删除，证据一律取 `outputs/` 下的批次产物；需要原始语料的工具与
测试改为显式指路（`NOVEL_DATA` / `NOVEL_CORPUS` / `NOVEL_OUTPUT`），指不到就明说「没查」。
