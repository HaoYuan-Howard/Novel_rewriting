# Stage2 人物登记（Characters v3）

人物登记保存实体身份字面、book-level 称谓与逐次 mention 绑定。公开身份描述**不**进
Characters（无自由文本 `role`），由 Blueprint State（经 `public_state_ref`）承担。

新书人物唯一 writer 是 `stage2.characters`（稀疏 Extract → 确定性字面回填 → lexicon
Resolve → Compiler）。`stage2.analyze` 联合首抽**不再**为新书写 Characters；cold-start
编排为 `characters → global`。存量 `CharactersResponse` / analyze receipt 仅服务历史
草稿解码，删除条件见 `docs/REQUIREMENTS.md` STR-03-S2-CH。

| 路径 | 模型与版本 | 用途 |
|---|---|---|
| `stage2/characters.json` | `CharacterRegistry` **v3** | 独立人物正式产物 |
| `stage2/characters_edit.json` | `CharactersEdit` | 人工 typed 编辑输入 |
| `stage2/blueprint_character_projection.json` | `BlueprintCharacterProjection` | 人物 producer 的确定性章内闭集投影 |
| `stage2/analyze_receipt.json` 等 | Analyze v1 | **存量**联合首抽来源 / draft repair；新书不写 |

只读当前登记版本；v2 人物拒读、不迁移。

## 人物字段（RegisteredCharacter）

`characters` 为非空 `char_001` 格式 ID 映射。每项包含：

| 字段 | 含义 |
|---|---|
| `canonical_surface` | 实体登记身份字面 |
| `status` | 身份状态（闭集） |
| `first_mention_chapter` | 首次提及章（阅读序）；须有该章 bound mention |
| `name_usable_from` | 姓名类称谓何时可对读者使用；可为 null |
| `surfaces[]` | book-level 称谓：`surface`、`kind`、`identity_revealed_from` |
| `name_distinctiveness` | compiler 由 kind 派生，供 Stage7；非 LLM 填写 |

**已删除**：`role` / `role_evidence_refs`、`first_chapter`、`aliases`。

三投影互不替代：`first_mention_chapter` ≠ `identity_revealed_from` ≠ `name_usable_from`。
`identity_revealed_from` 表示读者得知该称谓属于该实体的时点，**不**表示称谓字面何时可出现。

同一实体的 surface 唯一。不同实体可以有相同称谓；归属必须由 mention / 证据建立。
源文 typo 仅 occurrence 绑定，不得升 book-level surface。

## Mentions 与证据

`mentions` 为正式真相的一部分（R-2.4 已翻转）：每条含章号、坐标、surface、kind、
disposition、可选 `char_id` / `reveals_identity` 与 `evidence_refs`。

人物与 Global 共用 `CoreEvidenceAnchor`：`chapter_ordinal/start/end/text_sha256`。
每个正式产物保有自己的 `evidence_anchors`；相同区间得到相同稳定 `ev_…` ID。

机械检查规则见 `novel/stages/stage2/characters/checks.py::CHARACTER_RULES`：
`character_unattested`、`address_*`、`hedge_words`、`global_evidence_unlocated`，以及
常驻 UNVER `character_semantics_unverifiable`。

## 独立编辑与恢复

`CharactersEdit` 绑定正式或优先 INVALID 的原始 SHA-256。免费 `--apply-edit` /
`--revalidate` 走人物 owner；HARD 候选落 INVALID，不凭机械通过补语义 PASS。
称名投影只由人物 owner 写入与免费复验恢复。

## 下游消费

- Stage4 ActorCatalog / 盲投影：读 `surfaces` 与 `first_mention_chapter`；无 presentation
  绑定时 `role=None`（身份只来自 Blueprint State）。
- `SourceCharacter`：`surfaces`、`first_mention_chapter`、`name_usable_from`、
  `identity_revealed_from`；无 `role` / `first_chapter`。
- `PlannerCharacter`：仅 `alias` + `surfaces`（可合法空）。
