# Novel rewriting

把一本中文网络小说经过 stage0～stage7 处理为一部新书：保留情节骨架，重写文字，
替换人名，最终交付以书名命名的成书 `<书名>_rewriting.txt`。

单书 stage0～stage7、agent 驱动全链和前端真实服务主路径已经验收。当前开发重点是
**正确性、恢复能力、质量闭环与契约收口**；尚未完成、尚未验证和待产品裁决的事项统一登记在
`docs/REQUIREMENTS.md`。它是唯一活动待办，README 不重复维护任务状态。

## 产品边界

- 单人、本机、桌面浏览器；服务只绑定 `127.0.0.1`，不做登录、协作、权限、通知或移动端。
- 输入是一部中文网络小说，stage0～stage7 依次完成清洗、分章、抽取、文风、计划、写作、文案、
  改名与成书，最终产出 `stage7/<书名>_rewriting.txt`。
- 工具的主要风险不是程序崩溃，而是 LLM 造事实、提前泄密或漏掉内容后仍写得通顺。因此产品是
  “审校台”而不是 dashboard：主体动作是并排对照证据，再由人做决定。
- stage0 批准是全链唯一的前置人工硬闸；stage6 与 stage7 的人工定稿是各自产物进入下游的语义闸。
- 看、免费但写盘、付费 LLM 三类动作必须一眼可分。付费前由服务端下发完整 argv 并二次确认；
  不显示猜测的价格或调用次数，发起后不承诺无损取消在途调用。

## 处理链与成本

每个阶段的核心是一次或多次 LLM 调用，**花真钱**；确定性部分（分章、锚点回取、替换、组装、
全部校验器）不花钱。一次全链约 40~50 次调用，一本 8 章的小书跑完在数十分钟量级。

```
stage0  非正文剔除 + 人工审阅闸门              0 成本，无 LLM，**必须人批准才能往下走**
stage1  摄入 + 分章                          0 成本，无 LLM
stage2  全书级抽取（人物/关系/规则/秘密/弧线）  1 次调用 ┐
        逐章抽取（事件拍 + 必须保真的原文点）    每章 1 次、最多 32 并发 ├ 同时起跑
stage3  文风画像（描述 + 代码实测的形态档位）    1~2 次调用 ┘
stage4  重构计划（只存决策，一个字内容都不经它） 1 次调用
        写作上下文（确定性组装，写作的唯一输入） 0 成本
stage5  人工选深度章（默认第 1~3 章）后，两条臂并发逐章写作；每臂最多 32 并发
        全书验收（判成品）+ 修订式修复（旧称「反哺」）   0 成本 / 逐章调用
stage6  开篇文案：一次调用产三候选，人定稿       1 次调用
stage7  人名替换：一次调用产新人物表（只产决策）  1 次调用
        替换与成书组装                          0 成本
```

**最高风险不是「出不了书」，是「错误的剧情被静默接受」。** LLM 的错误不会崩溃：它会造一个
读起来毫无破绽的假事实、会在第 1 章说出第 14 章才揭晓的秘密、会把一段该保留的关键台词整段丢掉
而成稿依然通顺。系统的全部设计张力都在这里——这决定了 `docs/BACKEND_CONTRACT.md` 里绝大多数的
`Q-` 条目。作业单元的命名体系（`stage` 与 `node` 两根正交轴）与章号恒等式见该文档 §一。

## 八阶段的人类任务

| 阶段 | 系统动作 | 人的主要检查 |
|---|---|---|
| stage0 | 剔除番外、后记、尾声、作者有话说 | 整本审阅删除范围；可直接修正范围或正文；批准后才下行 |
| stage1 | 确定性分章 | 原文与切分并排，确认无漏章、错章、断号 |
| stage2 | 全书事实底座与逐章事件拍 | 原文与骨架逐段对照；全局人物、关系、规则、秘密是否有据 |
| stage3 | 代码实测加 LLM 文风画像 | 实测数字与真正下发给写手的档位词是否一致 |
| stage4 | 重构计划与写作上下文 | 计划决定与写手实际收到的材料逐拍对照 |
| stage5 | 深/浅两臂写作、机械验收和语义审判 | 材料与成稿、原文与改写、finding 与修订面 |
| stage6 | 生成三个开篇文案候选 | 三候选并排，人选一条定稿 |
| stage7 | 生成人名决策，确定性替换并组书 | 新旧名表、漏网候选和逐章替换前后；人定稿 |

界面规格（路由与屏幕职责、UI 硬规则 U-01～U-20、技术与数据缝）见 `web/DELIVERY.md`。

## 文档职责

| 文档 | 负责 | 不负责 |
|---|---|---|
| 本文件 | 项目入口、**产品边界与人的任务**、处理链与成本、运行方式、备份与恢复、接手顺序 | 需求明细、完成日志、踩坑长文 |
| `AGENTS.md`（= `CLAUDE.md` 符号链接） | agent 入口摘要：pytest 三坑、架构要点压缩版、编码风格、工作纪律；命令细节只留指针 | 详细契约与证据；与 `docs/BACKEND_CONTRACT.md` 冲突时以后者为准 |
| `docs/REQUIREMENTS.md` | 仅列未完成事项、阻塞、验收条件与待裁决问题 | 已完成能力、施工过程、界面设计细节 |
| `docs/LESSONS.md` | 可迁移的工程经验与真实踩坑，最多 21 条 | 任务状态、需求清单 |
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
- 产品语义与屏幕行为以本文件的「产品边界 / 八阶段的人类任务」与 `web/DELIVERY.md` 为准；
  若它与真实 API 冲突，按
  `docs/REQUIREMENTS.md` 的待裁决项处理，不静默选择一边。

## 当前已具备的主路径

- `novel.cli` 可驱动单书 stage0～stage7，最终组装成书；完整操作规程位于
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

API 只监听 `127.0.0.1:8765`。前端默认配置位于 `web/.env`；真实联调时应为
`VITE_USE_MOCK=false`。前端开发命令（`npm ci` / `typecheck` / `seams` / `build` / `dev`）
的唯一落点是 `web/DELIVERY.md`。

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
