# Stage 7 当前契约

Stage 7 负责人物真名替换与最终成书。模型只产改名决定；映射展开、替换、残留扫描、恒等基线
和 `book.txt` 组装均由确定性代码完成。

| 路径 | 模型 | 角色 |
|---|---|---|
| `stage7/name_map.json` | `NameMap` | 当前正式改名产物 |
| `stage7/_rounds/name_map.rN.json` | `NameMap` | 归档轮次，与正式产物同形 |
| `stage7/name_map_edit.json` | `NameMapEdit` | 人工输入，不是节点产物 |
| `stage7/book.txt` | 纯文本 | 最终交付物 |

正式 JSON 使用当前严格 schema，不读取旧字段、旧规则行或混合契约标记。

## 一、`name_map.json`

顶层字段全部必填；可空字段也必须保留键。

| 字段 | 类型 | 内容 |
|---|---|---|
| `surname_map` | `dict[str,str]` | 原姓到新姓 |
| `characters` | `NameDecision[]` | 每个 `char_id` 的完整决定 |
| `extra_subs` | `ExtraSub[]` | 人工补充映射 |
| `subs` | `dict[str,str]` | 代码展开后的完整替换表 |
| `kept` | `str[]` | 必须保持原样的称呼 |
| `applied` | `Applied` | 正文与文案各映射的命中次数 |
| `residual` | `Residual[]` | 旧姓字残留候选 |
| `baselines` | `dict[str,StemBaseline]` | 旧姓字覆盖率基线 |
| `approved` | `ApprovedNameMap｜null` | 人工定稿状态 |
| `book_txt` | `BookTxt｜null` | 已组装交付物的指纹与规模 |
| `judged` | `dict[str,JudgedDraft]` | 实际消费的逐章成稿及投影指纹 |
| `hook_sha1` | `fp10｜null` | 文案产物文件指纹 |
| `hook_consumed` | `HookConsumed` | 实际消费的定稿文案投影 |
| `sources` | `NameMapSources` | `writer_context` 新鲜度 |
| `book` | `Finding[]` | 本层确定性发现 |
| `identity` | `IdentityBaseline｜null` | 恒等替换基线 |
| `_provenance` | `NameMapProvenance｜NameMapFreeProvenance` | 模型或纯人工来源账 |

### 1.1 人物决定

`NameDecision`：

| 字段 | 类型 | 约束 |
|---|---|---|
| `char_id` | `str` | 必须覆盖当前人物表且不多不少 |
| `old_canonical` | `str` | 代码从 Stage 2 人物表回填 |
| `new_canonical` | `str` | 新名；不改名时与旧名相同 |
| `renamed` | `bool` | 是否改真名 |
| `not_renamed_reason` | `str｜null` | `renamed=false` 时必须说明 |
| `aliases` | `Alias[]` | 每个旧称呼到新称呼的明确去向 |
| `reason` | `str` | 决策理由 |

`Alias` 只含必填 `old` 与 `new`。通用亲属、官职、排行和敬称进入 `kept`，并以恒等映射
参与正则构造，防止短称呼误伤长词。

`ExtraSub` 只含 `old`、`new`、`origin=human`。人工映射的碰撞单独进入
`rn_new_collide_human`，不与模型新名唯一性混判。

### 1.2 确定性替换账

- `Applied.chapters{relpath}.counts` 与 `Applied.hook.counts` 记录每条映射的命中次数。
- `Residual` 必填 `stem`、`where`、`hit`、`context`、`confidence=high｜low`、
  `unit`、`suggest`。
- `StemBaseline` 必填 `total`、`covered`、`residual`、`ratio`；`ratio` 可为 `null`。
- `ApprovedNameMap` 只有 `origin=human`。
- `BookTxt` 只有 `sha1`、`chars`、`chapters`；文案未定稿时不得组装成书，因此不存
  `has_hook` 双态字段。

### 1.3 恒等基线与来源

`IdentityBaseline` 必填：

- `roundtrip_ok: bool`；
- `chapters: int`；
- `mismatches: str[]`；
- `blind: str[]`。

`HookConsumed` 必填 `present` 与 `approved_text_sha1`。`NameMapSources` 只含必填
`writer_context`。成稿正文的新鲜度通过 `judged{relpath}.consumed` 判断，不退回整文件指纹。

## 二、来源账

模型生成使用 `NameMapProvenance`：继承 `LlmProvenance`，并必填
`params={max_tokens}`。`prompts` 使用 `RENAME_SYS` 与统一的 `RULES` 指纹键。

没有模型基线、由完整人工编辑直接产生时使用 `NameMapFreeProvenance`：继承
`FreeProvenance`，同样保留 `params`，但结构上没有 `model`，并写 `origin=human`。

两种来源账都属于当前契约。局部人工编辑已有模型产物时走 `partial_rewrite=--apply-edit`，
保留原模型来源并更新本次真实读取的 `inputs`。

## 三、人工编辑输入

`NameMapEdit` 是严格 schema，允许以下任选组合：

| 字段 | 类型 | 用途 |
|---|---|---|
| `characters` | `NameDecision[]` | 提交完整人物决定集 |
| `character_patches` | `CharacterPatch[]` | 按 `char_id` 局部覆盖现有决定 |
| `surname_map` | `dict[str,str]` | 覆盖姓氏映射 |
| `extra_subs` | `ExtraSub[]` | 增补人工替换 |

首次没有 `name_map.json` 或 `.INVALID` 基线时，必须提交足以构造完整决定集的内容；有基线时
可只交 patch。编辑文件经 `ArtifactStore` 读取并进入来源边，不允许读取产物目录外路径。

## 四、替换与成书

- 替换器按长度优先构造单一 alternation；`subs` 与 `kept` 同时参与，防止子串跨界。
- 正文和已定稿文案使用同一替换器；书名只作为人工不可验证项报告。
- 成书前要求每个输出章恰有一份正式成稿、没有 `.INVALID` 成稿，并且 Stage 6 文案已定稿。
- `book.txt` 写入后，其 sha1、字符数与章节数进入 `book_txt`；`rn_txt_stale` 校验盘上文件。

## 五、当前入口

| 入口 | 成本 | 行为 |
|---|---|---|
| 主路径 | 付费 | 生成新人物决定并跑完整确定性闸门 |
| `--dry-run` | 0 | 打印当前实际下发内容，不写盘 |
| `--identity` | 0 | 运行恒等替换基线并写回结论 |
| `--recheck` | 0 | 按当前代码重建派生字段并重判 |
| `--apply-edit <路径>` | 0 | 应用严格人工编辑输入。路径相对**书目录**，也接受书目录内的绝对路径；目录外（含 `..` 穿越）是显式终态（`novel/paths.py::edit_rel`，stage3/4/6/7 共用） |
| `--revalidate` | 0 | 重建 `.INVALID` 的派生字段，合格后提升 |
| `stage7.assemble` | 0 | 在前置条件满足时写 `book.txt` |
| `--check-only` | 0 | 只读复检当前正式产物 |

## 六、现行判据

当前注册表共 **33 条**：**15 hard、8 info、3 metric、7 unver**。owner 分布为
`stage7=20`、`stage5=2`、`stage2=1`、`None=10`。

| 严重度 | 当前规则 id |
|---|---|
| hard | `rn_llm_shape` · `rn_map_coverage` · `rn_new_collide` · `rn_ambiguous` · `rn_shared_surface` · `rn_surface_short` · `rn_already_present` · `rn_title_dropped` · `rn_semantic_marker_changed` · `rn_kinship_touched` · `rn_surname_split` · `rn_overlap` · `rn_residual_key` · `rn_book_incomplete` · `rn_txt_stale` |
| info | `rn_new_collide_human` · `rn_shared_surface_human` · `rn_new_in_source` · `rn_invalid_drafts` · `rn_residual_stem` · `rn_no_reason` · `rn_kept_contains_mapped` · `rn_identity_baseline` |
| metric | `rn_stem_noise` · `rn_report` · `rn_style_report` |
| unver | `rn_identity_blind` · `rn_taste_unver` · `rn_coverage_unver` · `rn_title_unver` · `rn_not_approved` · `rn_body_stale` · `rn_hook_missing` |

本层没有退役规则占位或旧规则兼容输出。权威定义在 `contracts/judges/stage7.py`；报告形状见
[`_report.md`](./_report.md)。
