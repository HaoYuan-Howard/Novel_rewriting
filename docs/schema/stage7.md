# Stage 7 当前契约

Stage 7 负责人物真名替换与最终成书。模型只产改名决定；映射展开、替换、残留扫描、恒等基线
和成书组装均由确定性代码完成。

| 路径 | 模型 | 角色 |
|---|---|---|
| `stage7/name_map.json` | `NameMap` v4 | 当前正式改名产物 |
| `stage7/_rounds/name_map.rN.json` | `NameMap` v3 | 归档轮次，与正式产物同形 |
| `stage7/name_map_edit.json` | `NameMapEdit` v1 | 人工输入，不是节点产物 |
| `stage7/{title}_rewriting.txt` | 纯文本 | 最终交付物。**书级参数化路径**（`contracts/artifacts.py::BOOK_SCOPED_PATHS`）：占位符由「这本书是谁」决定，每本书恰好解析出一份具名产物，落点唯一在 `novel/paths.py::book_txt_rel` |

正式 JSON 使用当前严格 schema，不读取旧字段、旧规则行或混合契约标记。

`stage7.rename` 的 agent wire 为兼容 Claude/Codex 公共严格 JSON Schema，把动态姓氏表表达为
`surname_map: [{old_surname, new_surname}]`；代码拒绝重复 `old_surname` 后还原为下表所列的
`dict[str,str]`。该数组只属于 provider 传输，`name_map.json`、轮次归档、人工编辑与新 checkpoint
payload 继续使用正式 dict 形状；本批 NameMap v3 改为独立 Characters v2 / Global v7 来源，旧来源不得继承审批；旧 checkpoint 不改字节，但旧 schema SHA 不再
满足 current restore，必须由新 run 重建。

## 一、`name_map.json`

顶层字段全部必填；可空字段也必须保留键。

| 字段 | 类型 | 内容 |
|---|---|---|
| `surname_map` | `dict[str,str]` | 原姓到新姓。口径是**本书全部改名角色的姓**；【姓氏族群】先按完整字面排除纯关系/官职/位号，再以有据同姓且 ≥2 人聚类，只是其中必须共姓的子集，单人映射合法 |
| `characters` | `NameDecision[]` | 每个 `char_id` 恰好一条的完整决定；逐角色 `aliases[].old` 必须与实际下发 alias 精确对应，不漏、不多、不重复、不串角色。⚠ `renamed=true` 时 `not_renamed_reason` **只准是 `null`**，且 visible canonical 或该角色实际收到的至少一条 alias 必须真实变化；描述 canonical 恒等、带姓 alias 真改合法，全恒等的账面假改名由 `rn_llm_shape` 阻断。`renamed=false` 时 canonical/alias 必须全恒等 |
| `extra_subs` | `ExtraSub[]` | 补充映射。`origin` 分 `human`（人手写）与 `adopted`（机械安全采纳或 `--adopt-residual` 重放），**判定同权，分开只为审计** |
| `subs` | `dict[str,str]` | 代码展开后的完整替换表 |
| `kept` | `str[]` | 必须保持原样的称呼 |
| `applied` | `Applied` | 正文与文案各映射的命中次数 |
| `residual` | `Residual[]` | 旧姓字残留候选 |
| `baselines` | `dict[str,StemBaseline]` | 旧姓字覆盖率基线 |
| `approved` | `ApprovedNameMap｜null` | 定稿状态；现役写 `origin=auto`，`human` 仅存量 |
| `book_txt` | `BookTxt｜null` | 已组装交付物的指纹与规模 |
| `judged` | `dict[str,JudgedDraft]` | 实际消费的逐章成稿及投影指纹 |
| `hook_sha1` | `fp10｜null` | 文案产物文件指纹 |
| `hook_consumed` | `HookConsumed` | 实际消费的定稿文案投影 |
| `sources` | `NameMapSources` | `writer_context` 新鲜度 |
| `book` | `Finding[]` | 本层确定性发现 |
| `identity` | `IdentityBaseline｜null` | 恒等替换基线 |
| `_provenance` | `NameMapProvenance｜NameMapFreeProvenance` | 模型或纯人工来源账 |

`stage7.rename` 的人物材料由独立 `stage2/characters.json` 提供：canonical、唯一 aliases、
首次公开 role；确定性姓名检测另消费 name_distinctiveness。Global 只提供有向关系端点、
predicate 与 setting.era。人物 role / alias 归属及时代、关系变化使实际消费失效；证据坐标、
别名身份披露章和未进入改名材料的规则、秘密不扩大这条付费依赖。共同称谓继续保留多owner，
不按同字面合并人物；排行、头衔、最长词屏蔽、一旧名两新名与新名碰撞守卫保持。

旧 NameMap v2、旧来源 prepared 与旧批准保留历史，不手补当前版本或伪造通过；当前版本
仍经原有完整响应恢复、typed 编辑、复验、定稿和成书重建入口。下载通过
`delivery.read_deliverable_book` 与 `assemble.read_current_book` 核对当前映射、正式章稿、
文案接受和最终文件字节；重启读回不绕过这些门。

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
- `ApprovedNameMap` 现役写 `origin=auto`（rename producer 机械 seal）；`origin=human` 仅存量可读。必填自报 `actor`、非空 `reason` 与服务端解析得到的
  `subject_sha256`；新决定另存 `review_identity_sha256` 和刚读工作包的 `workpack_sha256`。
  调用方不手搬这些 SHA，只提交工作包给出的 `review_token`。长期适用性只绑定人实际看见的
  `surname_map`、`characters`、`extra_subs`、可见 finding 与显式审阅契约；代码、规则、
  provenance 和免费机械复检结果属于 `validation_seal`，不再级联撤销内容未变的人工决定。
  提交时服务端仍在同书锁内重建 exact workpack，旧 token 关闭式拒绝。
- 当前审阅契约是 `stage7-final-review-v4`，公开工作包为 v3，二者分别版本化。
  工作包固定 `check_basis=name_map_without_approval_or_delivery`：`check` 对去掉 `approved`
  和 `book_txt` 的人物表进行核验，批准或正常组书后仍保留同一基准报告。
  当前批准由 `disposition/current_approval` 证明，可下载由独立 `delivery` 判定；
  页面不能用基准里的“尚未批准”提示覆盖这两者。基准说明绑定本次 exact token，
  不改变已有报告的长期 `review_identity_sha256`。
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

Legacy Rename 在首次正式写盘前保存完整提交证明：原 run/parent、请求策略、原接受回答与
全部成本行、候选 typed 值及正式渲染 SHA-256，并绑定人物表、INVALID、动态成书的写前字节。
证明保存在该书 `stage7.rename` 的 `book` 工作单元；纯缓存候选同样保存证明，内容调用计数仍为 0。
`inspect_prepared_recovery` 只读投影精确来源，CLI 使用
`--recover-prepared <source_run_id> --subject-sha256 <proof_sha256>` 进入 0 模型恢复，
不构造 LLM client 或 Agent session，也不重新结算原模型费用。

未完成提交必须重新通过原输入、当前 HARD 与三路径 CAS，然后只提交原候选并补原 run 的采纳。
原采纳已存在时，以封存的原 typed 值、渲染字节摘要、原费用、原回答与精确采纳核验完成事实；
合法人工定稿或编辑可以改变当前人物表，恢复只补预算与提交标记，不覆盖后继，也不授予当前主体
任何接受。证明损坏、原费用缺失或未完成目标漂移
保持 unknown，不提供可执行恢复动作，也不自动发起替代模型调用。

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
- 成书只能在当前 `name_map` 已定稿（`ApprovedNameMap`，批次 B4 后由 rename auto seal 写入 `origin=auto`）、批准的 subject 仍精确等于当前决策面、provenance fresh、正文/文案消费指纹均未漂移时写入；写入后，其 sha1、字符数与章节数进入 `book_txt`。映射编辑、`--identity` 与重判都会清空旧 `book_txt`，并将旧 txt 移到内容寻址的 `_rounds/` 归档。映射编辑始终清空旧批准；`--identity` / 重判只有在重写 provenance **之前**证明旧模型消费投影仍 fresh、且决策面未变时才保留批准，输入 stale / unknown 时清空批准并由 rename 再次机械 seal（`stage7.approve` 已退役）。
- 预览/下载端点经 `novel/orchestrator/delivery.py::read_deliverable_book` 共用后端终验，复核当前审批、Stage2 generation-safe、阻塞待办、成书指纹/规模与确定性逐字重建，前后 surface 或代码漂移拒绝。自动收尾和 Stage7 页面消费同一结论；没有 Markdown 报告要求。

## 五、当前入口

| 入口 | 成本 | 行为 |
|---|---|---|
| 主路径 | 付费 | 生成新人物决定并跑完整确定性闸门 |
| `--dry-run` | 0 | 打印当前实际下发内容，不写盘 |
| `--identity` | 0 | 运行恒等替换基线并写回结论；若已有映射的旧模型消费投影漂移或不可证明，清空旧批准并退休旧交付物 |
| `--recheck` | 0 | 按当前代码重建派生字段并重判；若旧模型消费投影漂移或不可证明，清空旧批准后等待人工重批 |
| `--emit-edit-template` | 0 | stdout-only 输出 registry 绑定的 `NameMapEdit` v1 骨架，不写盘 |
| `--apply-edit <路径>` | 0 | 应用严格人工编辑输入并开新人工修订轮。路径相对**书目录**，也接受书目录内的绝对路径；目录外（含 `..` 穿越）是显式终态（`novel/paths.py::edit_rel`，stage2/4/6/7 共用） |
| `--adopt-residual high` | 0 | 把当前高置信残留作为新决策输入重判；其他参数关闭式拒绝 |
| `--revalidate --actor system:invalid-revalidate --reason <text>` | 0 | 对 `.INVALID` 做强审计，记录 hash 差异并在合格后提升；批次 C 起由队列自动排入，Stage 页不再发布用户入口；不开新生成轮 |
| `stage7.rename` 正式成功后 auto seal | 0 | 批次 B4：机械校验通过后第二次写 `ApprovedNameMap(origin=auto)`；`stage7.approve` 已退役 |
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
