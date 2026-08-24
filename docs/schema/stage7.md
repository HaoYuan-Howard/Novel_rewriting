# Stage 7 当前契约

Stage 7 负责人物真名替换与最终成书。模型只产改名决定；映射展开、替换、残留扫描、恒等基线
和成书组装均由确定性代码完成。

| 路径 | 模型 | 角色 |
|---|---|---|
| `stage7/name_map.json` | `NameMap` v2 | 当前正式改名产物 |
| `stage7/_rounds/name_map.rN.json` | `NameMap` v2 | 归档轮次，与正式产物同形 |
| `stage7/name_map_edit.json` | `NameMapEdit` v1 | 人工输入，不是节点产物 |
| `stage7/{title}_rewriting.txt` | 纯文本 | 最终交付物。**书级参数化路径**（`contracts/artifacts.py::BOOK_SCOPED_PATHS`）：占位符由「这本书是谁」决定，每本书恰好解析出一份具名产物，落点唯一在 `novel/paths.py::book_txt_rel` |

正式 JSON 使用当前严格 schema，不读取旧字段、旧规则行或混合契约标记。

## 一、`name_map.json`

顶层字段全部必填；可空字段也必须保留键。

| 字段 | 类型 | 内容 |
|---|---|---|
| `surname_map` | `dict[str,str]` | 原姓到新姓。口径是**本书全部改名角色的姓**；【姓氏族群】（字面同姓且 ≥2 人）只是其中必须共姓的子集，单人映射合法 |
| `characters` | `NameDecision[]` | 每个 `char_id` 的完整决定。⚠ `renamed=true` 时 `not_renamed_reason` **只准是 `null`**（载体强制） |
| `extra_subs` | `ExtraSub[]` | 补充映射。`origin` 分 `human`（人手写）与 `adopted`（`--adopt-residual` 批量采纳），**判定同权，分开只为审计** |
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
- `ApprovedNameMap` 必填 `origin=human`、自报 `actor`、非空 `reason` 与完整
  `subject_sha256`。hash 只绑定审批时看到的决策面：`surname_map`、`characters`、
  `extra_subs` 的排序键紧凑 canonical JSON；不绑定 residual、派生替换计数或
  成书字节。旧页面的 hash 与当前决策面不同时关闭式拒批。
- 服务检查单的 `s7` proof 绑定上述完整决策面和完整 typed `ApprovedNameMap`
  （含 actor/reason/origin/subject），并在每次读取时复核 `subject_sha256` 仍等于当前
  决策面。它不绑定 `book_txt`、residual、book findings 等确定性派生字段：正常
  `stage7.assemble` 只补交付账，不能反过来撤销授权它的同一份批准；决策面或 typed
  审批凭据任何一项变化仍会使 proof 失效。
- `BookTxt` 只有 `sha1`、`chars`、`chapters`；文案未定稿时不得组装成书，因此不存
  `has_hook` 双态字段。
- `book[]` 中的 finding 是书级事实，`output_ordinal=null`。

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

两种来源账都属于当前契约。`--apply-edit` 产生新的人工修订轮：有模型基线时记
`partial_rewrite=--apply-edit`，没有可用基线时使用 `NameMapFreeProvenance`。映射决策发生变化
必须清空旧 `approved` 与 `book_txt`。相反，`--revalidate` 只是对当前 `.INVALID`
做强审计：重建派生字段、记录 actor/reason 及 raw/canonical hash，但保留原生成轮次与
来源账，审计通过不伪装成新的模型或人工改写轮。

## 三、人工编辑输入

`NameMapEdit` 是当前注册的 v1 严格 schema，允许以下任选组合：

| 字段 | 类型 | 用途 |
|---|---|---|
| `characters` | `NameDecision[]` | 提交完整人物决定集 |
| `character_patches` | `CharacterPatch[]` | 按 `char_id` 局部覆盖现有决定 |
| `surname_map` | `dict[str,str]` | 覆盖姓氏映射 |
| `extra_subs` | `ExtraSub[]` | 增补人工替换 |

首次没有 `name_map.json` 或 `.INVALID` 基线时，必须提交足以构造完整决定集的内容；有基线时
可只交 patch。编辑文件经 `ArtifactStore` 读取并进入来源边，不允许读取产物目录外路径。

不手抄版本号或字段。以下命令只向 stdout 输出当前 registry 版本的可复制模板：

```text
python -X utf8 -u -m novel.cli run stage7.rename <sid> --emit-edit-template --base <base>
```

v1 骨架为：

```json
{
  "_schema_version": 1,
  "character_patches": null,
  "characters": null,
  "extra_subs": null,
  "surname_map": null
}
```

提交前至少替换一个决策字段；空骨架不是有效修订。

## 四、替换与成书

- 替换器按长度优先构造单一 alternation；`subs` 与 `kept` 同时参与，防止子串跨界。
- 正文和已定稿文案使用同一替换器；书名只作为人工不可验证项报告。
- 成书前要求每个输出章恰有一份正式成稿、没有 `.INVALID` 成稿，并且 Stage 6 文案已定稿。
- 成书只能在当前 `name_map` 已人工批准、批准的 subject 仍精确等于当前决策面、provenance fresh、正文/文案消费指纹均未漂移时写入；写入后，其 sha1、字符数与章节数进入 `book_txt`。映射编辑/重判会清空旧 `book_txt`（映射编辑同时清空旧批准），并将旧 txt 移到内容寻址的 `_rounds/` 归档。
- 预览/下载端点复用组装前的 approved/current/fresh 闸并复核 `book_txt` 指纹与规模；只有文件存在而账或批准不成立时拒绝返回。

## 五、当前入口

| 入口 | 成本 | 行为 |
|---|---|---|
| 主路径 | 付费 | 生成新人物决定并跑完整确定性闸门 |
| `--dry-run` | 0 | 打印当前实际下发内容，不写盘 |
| `--identity` | 0 | 运行恒等替换基线并写回结论 |
| `--recheck` | 0 | 按当前代码重建派生字段并重判 |
| `--emit-edit-template` | 0 | stdout-only 输出 registry 绑定的 `NameMapEdit` v1 骨架，不写盘 |
| `--apply-edit <路径>` | 0 | 应用严格人工编辑输入并开新人工修订轮。路径相对**书目录**，也接受书目录内的绝对路径；目录外（含 `..` 穿越）是显式终态（`novel/paths.py::edit_rel`，stage2/4/6/7 共用） |
| `--adopt-residual high` | 0 | 把当前高置信残留作为新决策输入重判；其他参数关闭式拒绝 |
| `--revalidate --actor <id> --reason <text>` | 0 | 对 `.INVALID` 做强审计，记录 hash 差异并在合格后提升；不开新生成轮 |
| `stage7.approve --actor <id> --reason <text> --subject-sha256 <64hex>` | 0 | 人工定稿；hash 必须与当前决策面完全一致 |
| `stage7.assemble` | 0 | 在前置条件满足时写成书 |
| `--check-only` | 0 | 只读复检当前正式产物 |

## 六、现行判据

当前注册表共 **35 条**：**15 hard、10 info、3 metric、7 unver**。owner 分布为
`stage7=20`、`stage5=2`、`stage2=3`、`None=10`。

| 严重度 | 当前规则 id |
|---|---|
| hard | `rn_llm_shape` · `rn_map_coverage` · `rn_new_collide` · `rn_ambiguous` · `rn_shared_surface` · `rn_surface_short` · `rn_already_present` · `rn_title_dropped` · `rn_semantic_marker_changed` · `rn_kinship_touched` · `rn_surname_split` · `rn_overlap` · `rn_residual_key` · `rn_book_incomplete` · `rn_txt_stale` |
| info | `rn_new_collide_human` · `rn_shared_surface_human` · `rn_new_in_source` · `rn_invalid_drafts` · `rn_residual_stem` · `rn_derived_alias_candidate` · `rn_no_reason` · `rn_kept_contains_mapped` · `rn_upstream_stale` · `rn_identity_baseline` |
| metric | `rn_stem_noise` · `rn_report` · `rn_style_report` |
| unver | `rn_identity_blind` · `rn_taste_unver` · `rn_coverage_unver` · `rn_title_unver` · `rn_not_approved` · `rn_body_stale` · `rn_hook_missing` |

本层没有退役规则占位或旧规则兼容输出。权威定义在 `contracts/judges/stage7.py`；报告形状见
[`_report.md`](./_report.md)。
