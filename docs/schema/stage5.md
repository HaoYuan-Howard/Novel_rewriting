# Stage5：写作执行层

body按章顺序写作，输入仅为本章ChapterPacket及已接受前章的有限短尾。短尾默认最多800个非空白字符，按完整自然段取；整段过长时明确无可用短尾，不截断句子。hook独立消费HookPacket。

模型输出prose或needs_design；表达与节奏可以调整，关键动机、决策、沟通和剧情桥梁缺失必须回到Stage4。开篇窄修只允许前8个现有自然段的keep/replace，禁止增加段落、越界修改或借窄修整章重写。

送达材料逐项带`[section:序号]`。needs_design逐问题必填section、item_no与detail；item_no引用真实送达项，由compiler映射到ChapterPlan/HookPlan的自然字段。对应场景行动可补齐必要的即时决定和剧情桥梁，不能改变冻结跨章事实。无法定位时以story_structure和显式null报告范围不足，停止且不购买模糊修订。

模型、请求、正文与前章短尾的真实摘要由代码签入ProseReceipt；补丁保留初始生成来源，另记实际窄修请求和结果。机械通过与文学质量unver分别披露。模型在 `contracts/schema/story_prose.py`，纯请求及验证在 `novel/core/story_prose.py`，公开入口在 `novel/stages/stage5/story.py`。详细交接见 [三层规格](str06.md)。

## 当前版本与判据

本层正式故事产物**都是 schema story-v3**，旧根只读归档，须在新根由当前producer重建。

当前注册表共 **5 条**：**4 hard、1 unver**。

| 判据 | 级别 | 职责 |
|---|---|---|
| `story_prose_complete` | hard | 回答协议、finish_reason、非空与篇幅带通过机械校验 |
| `story_prose_receipt` | hard | 章回执绑定真实调用来源、章包、前章短尾、正文及修订链摘要 |
| `story_opening_patch_scope` | hard | 窄修仅替换前八个非空自然段，句柄闭合且未授权字节不变 |
| `story_hook_receipt` | hard | Hook 正文与回执绑定当前 HookPacket、请求与正文摘要 |
| `body_semantics_unver` | unver | 逻辑、人物转变、场景衔接、兑现与文学质量未经语义验收 |
