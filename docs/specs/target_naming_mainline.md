# 目标命名前置与普通正文主链（执行文档）

本文件是「目标命名前置＋普通正文」重构的执行文档：登记目标形态、已定取舍、实施批次与验收方式。
**批次进度、兼容入口与删除条件唯一登记在 [REQUIREMENTS.md](../REQUIREMENTS.md)**，本文件不另建进度真相源；
行为依据为 [BACKEND_CONTRACT.md](../BACKEND_CONTRACT.md) 与 `contracts/`，现役 RC 与目标架构见
[schema/str06.md](../schema/str06.md)。本文件不改变任何现役行为：生产代码修改、付费运行与正文外发按批次
另行授权（付费显式 `--yes`，外发 `--allow-egress`）。

2026-10-18 立项。时点事实：版式 `str06-rc25`；现役 13 个 str06 节点
（`contracts/invariants.py::NODES`：stage2 五 owner、stage4 四节点、stage5 两节点、stage7 两节点）；
末端改名链为 `str06.stage7.naming`（产物 `stage7/naming/name_map.json`，
`contracts/schema/naming.py::NameMapV6`，wire 见 `novel/stages/chain.py::run_naming`＋
`novel/stages/common.py::NAMING_RULES`）→ `str06.stage7.assemble`（单遍替换＋装配，
末端替换证明链（已随批次 C 删除；目标字面表改由 `novel/core/name_materials.py::target_face_table` 供 compile 消费）；
模块已随节点前移迁入 stage4 包）。

## 1. 目标与已确定的取舍

将唯一主链调整为：

**源书抽取 → 确定目标人物名字 → 目标故事设计 → Stage5 直接生成最终名字的普通正文 → 核对与装配。**

本次重构的目标，是删除末端改名所派生的全量文本标注负担，让流水线能够稳定生成正文，并通过实际
阅读检验改写质量。

已确定的产品规则：

- 普通名字默认可用，不再要求所有人物逐场声明名称释放。
- 仅对有源剧情依据的隐藏身份、化名归属和揭名时点设置限制。
- 正文中的疑似旧名、疑似提前揭名，只形成定位报告，不阻断流水线、不自动触发付费返修。
- 结构、引用、产物完整性和来源绑定等确定错误仍须阻断，并提供对应恢复路径。
- 不新增付费 Judge、名称清洗节点或人工逐阶段审批。
- 接受命名前置的代价：目标名字修改后，相关目标材料和正文需要重建，不再提供末端只替换姓名的能力。

范围边界：Stage0、Stage1、Stage2 的源 schema 和正式字节保持不变；`str06.stage4.naming` 前移后
`str06.stage7.naming` 退役，节点总数保持 13 个，不增加付费语义节点；旧 Stage7 naming 的历史
工作单元转只读（§4）。

## 2. 目标架构与数据职责

### 2.1 源书抽取与目标命名

Stage2 继续拥有源人物身份、名称结构、亲属关系、事实及原始证据（`stage2/characters/characters.json`
的 `name_structure`、`contracts/schema/source.py::CharactersArtifactV7`／`BlueprintsArtifactV18`）。
目标命名不得修改这些源产物。这里的符号已同步 rc27 身份／认知边界契约；
本文件“Stage2 源字节保持不变”只描述命名前移批次范围，不授权 rc26 根跨版本沿用到 rc27。

将原命名节点迁移为 `str06.stage4.naming`，置于 Stage2 compile 之后、Preserve 之前；唯一正式产物改为：

`stage4/naming/name_map.json`

命名继续依据源人物与蓝图，决定目标姓名及独立化名；compiler 根据已登记名称结构派生完整名字和
相关称谓（沿用 `compile_name_map` 的闭合核对：名称位一致、无姓氏冲突、保留项带理由）。人物 ID、
家族关系与 alias ID 保持稳定。无需改名的纯称谓人物保留原称谓；应改名集合为空时，由确定性路径
零调用完成。

旧 Stage7 naming 节点退出现役。`apply_bound_names`／`diff_proof`／`compile_bound_replacements`
（末端替换证明链）已随装配切换删除（批次 C，2026-10-18）；目标字面表改由
`novel/core/name_materials.py::target_face_table` 供 compile 消费。

### 2.2 Preserve 与目标故事设计

Preserve（`str06.stage4.preserve`）同时接收源材料和目标名称表，成为**源材料到目标语义简报的
唯一转述 owner**。其正式产物仍为 `runtime/preserve_checkpoint.json` 与
`stage4/grounding/reconstruction_brief.json`，按目标表述闭集显式升版。

以下进入创作链的自然语言必须使用目标名字：

- 义务、满足条件和可改写细节。
- 被实际消费的命题、世界约束和表达要求。
- 事实锁说明、未回答问题及身份约束说明。

这些内容沿用 Brief 的既有职责分区；需要新增模型输出时，只覆盖实际下游消费的源描述闭集
（`contracts/schema/consumption.py::STR06_FIELD_DELIVERY` 的送达登记同批更新）。审计引文不进入
该闭集，不建立整份源产物的文本镜像。

compiler 负责引用解析、结构组装和确定性称谓构造，禁止在自由文本中全局替换姓名，也禁止缺少目标
表述时静默抄回源句（I-15／I-21：可机械推导的派生字段由 compiler 计算；compiler 不得根据自然
语言猜测缺失语义）。

Design（`str06.stage4.body_design`）和 Hook Design（`str06.stage4.hook_design`）只消费目标简报、
目标人物投影及正式约束。原始证据通过稳定引用保留在审计链中，不再次混入写手材料。

### 2.3 身份例外与普通正文

删除全人物 `initial_surfaces`（`contracts/schema/design.py`）和逐拍 `release`
（同文件 SlimDesignBeat）所承担的默认禁名机制。

隐藏身份与揭名要求复用现有 identity/reveal 义务、reader 认知变化及目标承载位。必要的结构化
关联仅连接已有的人物或名称位、源证据和揭示认知引用；不建立全人物许可矩阵。

必须区分：

- 化名字面可以使用。
- 化名对应的真实身份尚不可公开。
- 某个情节节点需要正式揭名。

Stage5 正文（`str06.stage5.body`）和 Hook（`str06.stage5.hook`）直接输出普通文本，不再生成姓名
标记、普通义标记或字符区间。批次 C（2026-10-18 完成）已退役 identity-text-v1 协议及其指令面、
`NameMention`／`NameTextSpan` 消费链与回执两类区间字段；相关模块与字段已从仓内删除（历史形状见 Git）。
疑似旧名残留与提前揭名只形成定位报告（`novel/core/name_residue.py`，不阻断、不购修）。写手只接收
当前剧情范围内的目标材料；受限身份的未来答案不进入正向材料。

纯正文是否真正遵守揭示语义保持未评，通过阅读验收确认（§5）。

### 2.4 核对与装配

Stage7（`str06.stage7.assemble`）只核对章序、正文与回执绑定、完整性及交付身份，然后直接装配
已接受的正文。删除末端姓名替换、姓名区间核对（R-10.2 的 `apply_bound_names`＋`diff_proof`
证明）和替换差异证明。保留逐章摘要、Hook 摘要、装配格式版本、TXT 与 manifest 一致性
（R-10.3～R-10.5），以及零新增调用的确定性重放。R-10 全节按新主链改写（§3 批次 A）。

## 3. 实施批次与契约闭合

| 批次 | 实施内容 | 完成条件 |
|---|---|---|
| A：冻结新契约 | 统一采用 rc26；登记新命名节点、产物、普通名称默认可用及身份例外规则；明确退役判据 | 契约、producer 和 consumer 对新规则一致 |
| B：打通目标材料 | 命名前移；Preserve 生成目标表述；关闭源自由文本直接流入 Design、Binding、正文及 Hook 的入口 | 目标名称从正式命名产物到真实写手输入完整可达 |
| C：切换正文与装配 | 删除 identity-text 协议、候选覆盖硬闸、两类区间回执及末端替换；修订普通正文回执与交付身份 | 无标记正文能够经真实入口接受并交付 |
| D：恢复与公开读面 | 闭合存量升级、历史账读取、预算、CLI、API 和监控页 | 同根升级与重启恢复可用，公开状态反映新主链 |
| E：验收与删除旧路径 | 完成工程验证及真实正文验收，删除失去消费者的代码和测试 | 唯一新主链成立，无永久新旧双路径 |

契约变更范围包括目标命名、Preserve checkpoint、Brief、Design、Graph、Binding、编译产物、写手
协议、正文与 Hook 回执以及交付身份。按受影响语义和形状显式升版（`str06-rc26`，
`contracts/schema/__init__.py::ARTIFACT_SPECS` 与 `contracts/artifacts.py::STR06_ARTIFACT_RC_VERSIONS`
同批更新），禁止旧目标稿静默采用（I-13）。疑似旧名／提前揭名的定位报告作为新披露面登记：
不阻断、不购返修；确定的结构错误维持 fail fast（I-34），工程错误保留 stage/node/item/context（I-36）。

同步更新节点注册、执行顺序、消费依赖、模型配置映射、许可、版本登记、恢复和测试清单
（`contracts/invariants.py::NODES`、`novel/stages/chain.py`、`novel/views/str06.py`、
`tests/` 分层清单）。API 命名读取路由及现有展示结构可以保留，改读新产物；监控页将「目标人物
命名」移到故事设计之前。

保留通用返修能力，并修复材料问题转交过程中丢失规则码、owner、路径和上下文的缺口。工程错误必须
准确报告，不能转成无法定位的普通消息。

## 4. 存量升级与恢复

为当前根提供集中登记的 rc25→rc26 升级（`contracts/contract_upgrades.py::REGISTERED_UPGRADES`
新增精确身份边），沿用现有 preview/apply、冻结摘要、锁和中断恢复机制
（`novel/orchestrator/contract_upgrade.py`）。

升级行为：

- 保留 Stage0～Stage2、原始证据、已购响应、历史轮次和成本账。
- 归档旧 Preserve checkpoint、Brief 及全部 Stage4～Stage7 目标产物。
- 从新目标命名节点开始重建目标链。
- 不通过移动文件、删除标记或重新序列化，把旧语义稿转换成新稿。
- 不恢复内容额度；不足时走已有不可变追加授权。
- 命名改变后，依赖它的目标简报及下游产物明确失效；源抽取继续复用。

迁移必须处理两个已发现的工程缺口：

1. 升级器当前无条件保留 Preserve checkpoint（`novel/orchestrator/contract_upgrade.py` 的
   `_files` 把 `runtime/preserve_checkpoint.json` 直接加入保留集合），需要改为服从归档集合，
   防止同一文件同时进入保留和归档清单。
2. 旧 naming 节点退出后，其历史工作单元仍须可读。设置集中的历史账只读入口
   （`novel/work_units.py` 与 `<base>/_service/ledger.jsonl` 的解码面，
   `contracts/schema/workflow.py::work_unit_ledger_of_payload` 同风格集中），禁止旧节点继续
   执行、追加授权或修改历史身份；旧成本继续计入整书账（I-17）。

历史只读适配的删除条件登记到 REQUIREMENTS，不扩展为通用旧链兼容层（S-03）。

## 5. 验证与验收

### 5.1 工程验证

重点覆盖以下场景：

- 普通人物不声明 release，也能正常设计、写作和交付。
- 「空白」「若受了委屈」等普通文字不再触发身份硬错误。
- 不含任何身份标记的正文和 Hook 能通过真实消费者完成装配。
- 同姓亲属、复姓、独立化名和纯称谓人物得到正确目标名称投影。
- 化名可用时，真实归属仍不进入揭示前的写手材料。
- 疑似姓名报告不阻断、不额外购买返修；确定的结构错误仍准确失败。
- 旧目标稿不能冒充新协议结果；Stage2 可以免费复用。
- 升级中断能够恢复；历史预算和成本仍可读取；名字变动正确使下游失效。
- 交付与已接受正文一致，重跑装配不产生新调用。

先运行定向 unit/contract、stage、恢复及真实 CLI 子进程测试；契约稳定后运行契约五文件
（`docs/BACKEND_CONTRACT.md` §9.5）。整批完成时运行一次默认非 corpus 回归，并补受影响前端验证。
退出维护的旧协议测试登记删除依据，保留仍有效的职责反例（I-50～I-52）。

### 5.2 本书实际验收

工程验证完成后，以当前《夫君不行，但我有孕啦》作为真实验收书：

1. 免费预览并执行契约升级，确认 Stage2 保留、旧目标产物归档、历史账完整。
2. 沿新主链生成目标命名、目标设计、Hook 和全部正文。
3. 到达 Stage5 后检查实际文本，记录人物混淆、旧名残留、提前揭示、因果断裂、重复与文风问题。
4. 完成 Stage7 装配，核对最终成书和正文一致。
5. 分别报告工程正确性、实际阅读质量、首答接受情况、返修原因、调用量及成本；失败与未知用量均保留。

真实验收不要求首答必过；未过时必须区分真实内容问题、结构错误和工程缺口。不得以增加额度、
过滤失败记录或继续添加未经验证的硬闸代替诊断。
