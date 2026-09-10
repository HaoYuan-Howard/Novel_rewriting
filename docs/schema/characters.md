# Stage2 人物登记契约

`stage2.characters` 只生成静态人物登记，不新增 Commander。人物与必要知识的整批集成状态
记录在 `docs/REQUIREMENTS.md::TODO-49`；独立节点通过测试不能替代 Global 统一审阅和下游消费验收。

| 路径 | 模型与版本 | 归属 |
|---|---|---|
| `stage2/characters.json` | `CharacterRegistry` v1 | 人物 producer |
| `stage2/characters_edit.json` | `CharactersEdit` v1 | 显式人工或受托 typed 输入 |

`CharacterRegistry.characters` 是非空的 `char_001` 格式 ID 映射。每个人只存
`canonical`、`address[]`、`name_distinctiveness`、`role`、`role_evidence`；
`address` 是唯一称谓表，不另存 aliases。`fate`、关系、世界规则与秘密不属于此载体。
`role` 必须是人物首次出场时可公开的静态身份，不能夹入中段揭示或结局。

provider wire 为 `CharactersResponse`，人物以带显式 `char_id` 的数组返回；证据只交
本次 MainText 段落表中的 `evidence_candidate_id`，代码从同一表回取 `FactEvidence/SourceSpan`。
生成、人工编辑与重验证复用 `normalize_characters`、`checks.check_characters`；代码只验证
可机械判断的称谓、时点及证据区间，事实语义与身份是否泄底仍须由 Global Commander 核验。

`CharactersProvenance` 绑定实际 MainText 输入、代码、模型及 ledger 用量，保留
`evidence_locator_failures`。HARD 候选进入 `.INVALID`；`--revalidate --actor --reason`
免费重判，不能给原本未通过的语义判断补 PASS。人工修改沿 `--apply-edit` 路径归一、判断、
归档并继承真实模型来源，不能重新声明模型或把历史费用写成零。

`CharactersEdit` 绑定正式文件或优先的 `.INVALID` 原始 SHA-256，列出
`add / replace / delete` 操作。每个 ID 在一份输入中最多出现一次；add 不覆盖既有人物，
replace/delete 不创建不存在的目标，不能删除到空表。replacement 的证据使用 locator，
由同一算法回取；归一后的 no-op 不写轮次。CLI 必须提交非空 actor/reason。
正式或问题候选的 `characters_edit` 回执绑定输入 SHA、基线、actor/reason、时间与删除 ID，
重复操作不重复落盘；回执不能替代当前新鲜度检查，也不能把删除当作审阅接受。

agent 的首 wire 前 run 记录冻结请求、profile 和目标文件初始 SHA。每个 accepted 回答先经
通用 `agent_runs` 写不可变 checkpoint；正式提交以 `prepared → 写盘 → typed readback →
adoption → committed` 闭合。foreign prepared、原回答后发生的第三方修改、身份或来源损坏
保持 unknown；不得覆盖或再购买结果。同一已保存问题候选的恢复不制造新的 INVALID 历史轮次。
显式 legacy backend 继续使用自己的 transport/cache/工作单元来源，不能接管 agent run。

单元基础额度为 4，追加只走既有人工不可变授权。人物生成是否实际完成、模型调用是否发生、
Global Commander 是否接受是三件独立事实；节点落盘不能替代后续接受。

称名投影同属人物 producer：默认生成、typed 编辑与免费复验都发布
`blueprint_character_projection.json`，知识 producer 只读校验。人物正式登记 current 时，
缺失或损坏的投影可通过 `--revalidate --actor ... --reason ...` 免费重建。静态人物
HARD 的显式模型修复是 `stage2.characters --resample`，只接受已有 INVALID，沿 legacy
transport/cache/额度入口刷新问题回答；默认工作流定向返工由 Commander 的 review token
驱动人物 executor。两种动作都不替 Global Commander 接受，也不允许覆盖其他 run 的 prepared。
# 人物恢复模板的证据边界

人物的 `role_evidence.source` 为 null 时，编辑模板只能取同一人物、同一声明章的唯一正向
locator 失败记录，不得漏掉该人物或猜测原文。缺少唯一记录时显式返回模板不可用原因；操作者仍可
按当前 `source_sha256` 提交完整 typed replace。未知 `char_id` 引用保留给 `address_by_unresolved`
硬闸，不能归一成 `public` 后通过。
