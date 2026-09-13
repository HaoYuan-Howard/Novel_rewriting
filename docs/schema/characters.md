# Stage2 人物登记与联合首抽来源

人物登记只保存实体、称谓归属及首次公开身份。`stage2.analyze` 从 MainText 一次提取
人物与 Global 草稿，代码分别物化两份独立产物；后续修订由各自 producer 负责。
Global Commander 统一审阅两侧，人物不新增 Commander。Gate A 的实际验收范围见
`docs/REQUIREMENTS.md::TODO-49`，下游 presentation/ActorRef 的 Gate B 消费不能由本层落盘代替。

| 路径 | 模型与版本 | 用途 |
|---|---|---|
| `stage2/characters.json` | `CharacterRegistry` v2 | 独立人物正式产物 |
| `stage2/characters_edit.json` | `CharactersEdit` v2 | 人工或受托 typed 编辑输入 |
| `stage2/blueprint_character_projection.json` | `BlueprintCharacterProjection` v2 | 人物 producer 的确定性章内闭集投影 |
| `stage2/analyze_receipt.json` | `AnalyzeReceipt` v1 | 不可变联合首抽来源和首次物化去向 |
| `stage2/analyze_recovery.json` | `AnalyzeRecoveryJournal` v1 | 原 GlobalDraft 免费物化的 transient WAL |
| `stage2/analyze_owner_draft_edit.json` | `AnalyzeOwnerDraftEdit` v1 | 只替换已购但不能编译的单 owner 草稿 |
| `stage2/analyze_characters_draft_repair.json` / `stage2/analyze_global_draft_repair.json` | `AnalyzeDraftRepairJournal` v1 | 各自独立的免费草稿物化 WAL |

只读当前登记版本；v1 人物/编辑/投影不迁移，不补造原文证据或身份时点。

## 人物字段

`characters` 为非空 `char_001` 格式 ID 映射。每项包含：

| 字段 | 含义 |
|---|---|
| `canonical` | 实体的登记名，字符串 |
| `aliases[]` | 每实体每唯一称谓一项：`surface`、`identity_revealed_from`、非空 `evidence_refs` |
| `first_chapter` | 实体首次出场章，阅读序整数 |
| `first_appearance_evidence_refs` | 首次出场的非空归属证据 |
| `role` / `role_evidence_refs` | 首次公开身份及非空证据，不收后期身份变化或结局 |
| `name_distinctiveness` | 沿用 `NAME_DISTINCTIVENESS`，为改名提供专名程度 |

同一实体的 surface 唯一，`canonical` 在 aliases 中恰好出现一次。不同实体可以有相同称谓，
包括“师尊”等共同称谓或同名人物；归属必须由各自证据建立。源文异写可经语义审阅归并，
代码只修剪空白，不按模糊字符串自动合并实体。删除的正式 ID 不复用；独立修订冻结局部
句柄到既有 ID 的映射，新实体在保留及删除 ID 之后分配。

`identity_revealed_from` 表示读者得知这个称谓属于该实体的时点，**不表示称谓首次能出现的章**。
“新邻居”在第 10 章可以出现，第 11 章才揭示是某已登记人物；不能以此字段禁用第 10 章称谓，
也不能把内部 char_id 或未公开 canonical 的映射发给 Writer。章内称谓出现与表面身份的
选择由原文和 Blueprint 支持；跨章同人关系的揭示归 Global `arc_secrets` 与蓝图章级揭示字段。
registry 不保存 mention、by/context、局部动态状态或第二份披露图。

## 证据与编译

人物和 Global 共用 `CoreEvidenceAnchor`：`chapter_ordinal/start/end/text_sha256`。
start/end 是章内字符区间 `[start,end)`，原文只由 MainText 精确回取并核 SHA。
每个正式产物保有自己的 `evidence_anchors: dict[EvidenceId, CoreEvidenceAnchor]`，
相同区间得到相同稳定 `ev_…` ID，不内嵌第二份原文。证据表必须覆盖全部引用，无悬空或无用 anchor。

`CharactersResponse.characters` 与 `AnalyzeResponse.characters` 都返回局部 `character_ref`
（如 c1），不返回正式 char_id。证据只引用本轮段落候选 e000001；
`characters.compile_response` 从冻结段落表编译 anchors 和稳定 ID，并可接受冻结 identity_map。
跨人物共同称谓、源文错字和匿名功能角色的实体语义由统一审阅确认，不能用全书最早字面命中代替归属。

## 独立编辑与恢复

`CharactersEdit` 绑定正式或优先 INVALID 的原始 SHA-256，操作为 add/replace/delete。
replace/delete 只引用既有 char_id，每个 ID 只操作一次。add 使用本次唯一的 character_ref
（如 c1），不得自填 char_id；代码按局部引用数值顺序，避开当前与退休 ID 分配新身份，
并把 added_character_ids 映射冻结在本次回执。不能凭空 replace/delete 或删成空表。人物对象中的
证据可复用现有 EvidenceId，或提交 `{chapter_ordinal, locator}`，代码只接受指定章的唯一匹配。
坏 locator 在写前拒绝；不存占位 source、不把未知改成成功。归一后的 no-op 不写轮次。

免费 `--apply-edit` 须有 actor/reason，经过编译、HARD 判定、归档和 typed readback。
`characters_edit` 回执保存源 SHA、edit SHA、actor/reason、时间、本次新增映射及累计删除 ID；精确重复提交
只补尾，不重复改写。HARD 候选落 INVALID，免费 `--revalidate --actor --reason` 重验及重建投影，
不凭机械通过补语义 PASS。人物成功写回会按当前两份独立产物刷新统一 review，保留未变主体决定。
统一 C 编辑先以 `preflight_edit` 冻结候选，两侧均无 HARD 后由各 owner 的 CAS 提交；完成回执不重开。
同一 Core 决定的关系端点可引用本批新增 cN。只由 Core 编译器按本次人物回执改写为正式 ID；
原请求保留局部句柄，WAL 保存冻结后的两侧候选。独立 Global 编辑没有人物新增映射，不能接受 cN。

独立 agent run 在首 wire 前冻结请求、profile 和目标初始字节 SHA，accepted 回答先保存 checkpoint。
提交沿 prepared → 正式/INVALID → typed readback → adoption → committed。自身已写盘的尾窗
按原始 SHA 从本产物正式、INVALID 或 `_rounds` 回读冻结基线，不迁移、不取当前稿冒充原稿。
foreign prepared、第三方写回和损坏来源拒绝覆盖；付费原响应优先免费恢复，未知账不能用重买掩盖。

## Analyze v1 来源生命周期

首抽仅在人物与 Global 均无正式、INVALID、历史轮次及旧 run/调用来源时进入。
联合回执分别绑定 MainText、输入投影 `input_sha256`、真实 ledger 请求指纹 `request_sha256`、
response/schema/checkpoint SHA、原 run/attempt、冻结人物和
Global 句柄映射。`materializations` 恰好记录两个 owner，各自状态为 formal、invalid、
draft_invalid 或 awaiting_characters。人物 HARD 时保存人物候选和原 GlobalDraft，
Global 不凭失败人物映射偷渡正式产物；Global HARD 不撤销已合法物化的人物。

两份子产物继承真实 model/prompts/adopted_models，并以 `shared_analysis_source`
指向回执原字节 SHA 与原 run/attempt。子产物 `usage=null` 表示共享成本由回执和唯一 ledger
attempt 认领，不表示零费用。独立修订保留原来源并记实际新调用/编辑，不能把两份子产物的同一
Analyze E 重复计费。回执和原首次提交记录在后续独立编辑后仍不可变，不成为第三份运行知识库。

原 Analyze prepared 尾窗同时占有两侧 owner；免费 `stage2.analyze --recover-analyze-run <id>`
复验原 run/checkpoint/ledger/profile，仅在 exact-code 下以原 run 补原 `_commit`，不发 wire、
不重算本轮零 usage。已有完整 raw 而尚无 checkpoint 时，从原 custody 免费编译 checkpoint；
原 raw 形状损坏时建立既有 response-repair 工作项，封存唯一修复决定后仍运行同一免费 flag。
普通 checkpoint 沿用 v1；只有 Analyze 显式登记的修复 checkpoint 使用 v2，另存
`response_repair` 的原 rejection/封存 resolution/候选双 SHA，原 stdout 与成本身份不变。
无 raw、放弃修复或修复复验失败明确停止，不自动重购或重置已用四轮预算。代码改变时
必须先用冻结原代码排空旧 owner，不能跨 Gate 携 prepared 或重新首抽。

人物修好且原回执为 awaiting_characters 时，`stage2.global --recover-analyze` 从原
GlobalDraft 免费编译。它用只读 historical restore 验证原来源，在新的恢复 run 建立独立
AnalyzeRecoveryJournal：prepared/committed、原回执/checkpoint/run SHA、当前人物 SHA、
精确目标 SHA、冻结 Global 候选与 HARD 列表。未知、已删除或合并的原人物 ID 不按名字猜回。
prepared 冻结两侧 owner，恢复只补同一目标、review 和确定性采用审计；已 committed 不因后续
独立修订重开。最终 HARD 仍落 INVALID，交 Global 独立免费编辑，不重复购买联合回答。

如果人物证据候选或 Global 外键尚不能编译，代码仍在首次编译前冻结全部局部句柄与正式 ID。
对应 owner 的 `--emit-analyze-draft-template` 保留原坏引用，输出 AnalyzeOwnerDraftEdit v1：
owner、原 receipt SHA、原 owner 草稿 SHA、原完整冻结映射 SHA，以及恰好一侧的替换草稿。
Characters 不接受人工指定正式 ID；Global 的 `character_bindings` 默认复制原人物句柄，只有
typed 明确订正才能将漏人、删除或合并后的悬空引用绑定到当前正式人物，不能凭称谓猜回。
证据候选仍以原 MainText 闭集解析，不接受占位人物、伪造 source SHA 或放宽正式 schema。

`--repair-analyze-draft <file> --actor ... --reason ...` 在独立 owner WAL 保存原请求、编辑身份、
receipt/checkpoint/run SHA、当前 MainText 和必要人物基线，以及精确正式/INVALID 和人物投影目标。
整体 CAS 预检后才写目标；任何尾窗由同 owner `--recover-analyze-draft` 使用冻结请求免费补齐。
prepared 阻止两侧竞争写，闭合后回到独立产物编辑，不重开原联合提交或覆盖第三方修改。
API 工作包返回原草稿、诊断及原段落候选一次；读取不建目录、不追加账。草稿修复未调用模型，
原 receipt、raw、checkpoint 和唯一 ledger attempt 均不改变。

CharactersProvenance 的 `retired_character_ids` 独立于 `characters_edit` 保存删除过的持久 ID。
模型全量修订和人工 typed 编辑都继承退休集合，新增 ID 同时避让当前与退休身份；模型新产出
不会把旧的人工作用重新记成自己的人审。首次联合或独立生成明确保存空集合，局部草稿删除
也保留初始冻结 ID 的退休事实；禁止以列表重排、删除再新增复用旧身份。

## 历史首抽的只读核对边界

原 workflow_metrics 读数器（tools/ 下，已删除）随 Stage2 语义审阅一同退役；
历史首抽的证据仍以原 run 与永久 Executor request 中已验证的 system/schema 为准，
当前提示文本改变不抹除原调用来源，无法解码的旧 schema 保持 unver。
首轮/接受后 core 采用无空白 JSON **字符数**与 MainText 字符数比较；证据、审计另外计量。
候选去留按原 checkpoint 的稳定主体 ID 统计，无法证明的拆分/合并不推测。C 干预先验证
永久 Commander WorkUnit 的原请求、回答、typed 动作、会话与唯一成本来源，再关联实际提交：
Core 编辑绑定原审批、prepared 和 completed；reviewed GlobalEdit 绑定稀疏编辑 SHA 与已提交
历史 WAL；C 指派的独立 E 修订按原 review_token/owner 关联 E 的冻结请求、checkpoint、基线与
提交目标，并单列为 E 执行的变化。人工 Core 批次不会因 actor 字样被算作模型 C；缺少来源或
提交联结保持 unver，并另列可证明的小计。证据或审计字段变化不算语义主体干预。
调用累计耗时按唯一 ledger attempt 单列 Analyze E、Core C 和独立 executor；内容返工与网络重试
依工作单元账区分，未知阶段不归零。执行墙钟取 E 的成对 CLI start/end 与成功 C 原 receipt 的
返回时刻、实测耗时；另列区间并集和首尾跨度，标明秒级端点。补账时生成的 ledger.at 不作为
原调用结束时刻，E 节点窗口也不冒充单次模型往返。失败 C、缺尾、免费恢复时长、审阅失效等
没有权威区间或变更记录时保持 unver。
