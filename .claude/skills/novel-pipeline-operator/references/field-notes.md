# 经验库

## 使用方式

**先检索，不要整读。** 按节点、rule id、退出码或报错片段检索整个 reference 目录：

```text
rg -n "stage6|hook_para_sep|exit 70" .claude/skills/novel-pipeline-operator/references/
```

只把命中的条目作为诊断提示。当前 finding、代码、测试和盘上原始证据优先。`observed` 不能提升为不变量；`open-gap` 不能被写成已支持。

下面的索引是**条目的投影**（标题与状态逐字取自条目本身，由校验器对账），可以只读索引就决定要不要打开某条：
「状态」告诉你它够不够格影响决策，「处置入口」很多时候就是你要的那条命令。

## 索引

| ID | 标题 | 状态 | 触发键（节点 / rule / 退出码） | 处置入口 |
|---|---|---|---|---|
| FN-001 | stage0 批准前的预期红灯 | contract-confirmed | `s0_approval`, exit 1 | `stage0.approve` |
| FN-002 | 重号与缺号章节 | contract-confirmed | `s1_chapter_labels` | 核对源文标记与 ordinal，不改正文 |
| FN-003 | `--full-extract` 部分完成 | contract-confirmed | `--full-extract` | 分别 preview 缺失的 `stage2.blueprint` / `stage3.style` |
| FN-004 | blueprint 锚点失败要先判原字节 | observed | `hinge_anchor_void`, `verbatim_failed` | `--chapters`（付费）或 finding 明列的免费 fix |
| FN-005 | 钱已调用但目标产物缺失 | contract-confirmed | exit 70, 账本有调用而产物不在盘 | 完全相同参数重跑，命中 `.llm_cache` |
| FN-006 | 语义结论按文本 sha1 绑定，不跟机械验收的轮次走 | contract-confirmed | `stage6.semantic`, `semantic_review_seq` | 文本未变就**不要**重跑；变了才重跑 |
| FN-007 | 语义泄底可能源于上游揭示章误标 | contract-confirmed | `semantic_secret_leak` | `stage2.global --apply-edit` 或 `--wontfix` 留账 |
| FN-008 | stage6 首轮只有 INVALID | contract-confirmed | `hook.INVALID.json`, `hook_para_sep` | `stage6.hook --apply-edit` 或 `--revalidate` |
| FN-009 | stage6 总报告与所选候选不是同一粒度 | observed | 落选候选 HARD, 同 sha1 两轮矛盾 | 按 label+sha1 对齐，采更严格那一轮 |
| FN-010 | stage7 人物表不是全部 surface | observed | `rn_kept_contains_mapped`, `rn_semantic_marker_changed` | `name_map_edit.json` 展开内嵌映射 |
| FN-011 | stage7 映射编辑后必须重批重拼 | contract-confirmed | `approved`, `book_txt` | 重跑 `stage7.approve` + `stage7.assemble` |
| FN-012 | `consumed_changed` 可能是诊断噪声 | observed | `consumed_changed`, stale | 逐项指纹全同才披露诊断边，否则按真 stale 处理 |
| FN-013 | 语义审判的结论恒是带证据的 `UNVER` | contract-confirmed | `stage6.semantic`, `UNVER`, `judges` | 读 `judges` 两侧再人工收口 |
| FN-014 | 超限章有一条 0 成本销账路 | contract-confirmed | `trim_audit_chNN`, verification | `--retain-pretrim --chapters` 或 `--apply-edit` |
| FN-015 | 落盘闸拦下后，finding 细节的读法逐层不同 | contract-confirmed | `.INVALID`, exit 2 | 先 `--check-only` 取逐条，再 `--revalidate` 提升 |
| FN-016 | `--apply-edit` 的路径语义逐层不同 | contract-confirmed | `edit_rel`, 产物目录之外, exit 2 | `--apply-edit`，编辑文件写进书目录内 |

### FN-001 · stage0 批准前的预期红灯

- 状态：`contract-confirmed`
- 触发：`stage0.remove` 的追跑 check 仅因 `s0_approval` 为 pending 返回 exit 1。
- 判断：这是“尚未批准”，不是剔除质量失败；仍需确认其余 HARD 全部为零。
- 处置：按 stage0 代批规则读剔除段与边界，运行 `stage0.approve`。
- 验证：批准状态落盘，后续 `state` 不再把 stage0 视为等待 approval。
- 依据：`novel/stages/stage0/checks.py::check` 的 `s0_approval` 分支（批准状态非 approved 即命中）；
  `contracts/judges/stage0.py` 那一行的 sev 与 requirement。

### FN-002 · 重号与缺号章节

- 状态：`contract-confirmed`
- 触发：`s1_chapter_labels` 报告原始章号重号或缺号。
- 判断：scanned 行是原始标签读数，不等于下游 ordinal 已损坏；低频重号可能只是源书标签毛刺。
- 处置：核对源文标记规律与 stage1 产生的 ordinal，不凭告警重写正文。
- 验证：下游 chapter ordinal 唯一、连续且恰好覆盖 `1..N`；高频重复标记另行人工审阅。
- 依据：`novel/stages/stage1/checks.py::check` 产出的 `s1_chapter_labels`（读的是**原始标签**，
  不是下游 ordinal）；成书侧由代批契约的逐字对账第 1 项复核。

### FN-003 · `--full-extract` 部分完成

- 状态：`contract-confirmed`
- 触发：`stage2.global --full-extract` 在 global 或后续落盘闸门处停止，blueprint/style 缺失。
- 判断：组合命令不是原子事务；已经正式落盘的上游不应为补齐下游而重复付费。
- 处置：按 finding 修复活动失败，再分别 preview 缺失的 `stage2.blueprint`、`stage3.style`。
- 验证：两份 follow-check 都 fresh 且无 HARD，`state` 显示 stage2/stage3 完成。
- 依据：`novel/orchestrator/nodes.py::build_steps` 的 full-extract 追跑规则与产物存在性。

### FN-004 · blueprint 锚点失败要先判原字节

- 状态：`observed`
- 触发：`hinge_anchor_void`、`verbatim_failed` 或相近 span/anchor finding。
- 判断：先在对应源章逐字查锚点。原字节不存在表示模型转述/编造；原字节存在但去标点后退化为高频短串表示 locator/归一化问题。
- 处置：模型问题使用 `stage2.blueprint --chapters <N>` 定向重生成；确定性定位问题只执行 finding 明列的免费 fix，连续同错时停止重复抽样。
- 验证：锚点在源章逐字存在且定位到正确出现位置，定向章与整表 check 均通过。
- 依据：`novel/stages/stage2/checks.py::_HINGE_RULES` 那一组锚点判据；
  判断仍需原始响应、源章字节与当前 finding 文案三方对照。

### FN-005 · 钱已调用但目标产物缺失

- 状态：`contract-confirmed`
- 触发：连接中断、宿主终止或 exit 70；账本有调用而目标产物不在盘。
- 判断：账本与产物写入不是事务；“记过调用”不能证明生成成功。
- 处置：先 `state`，再用完全相同参数重跑；不得通过换参数或删缓存强制新调用。
- 验证：目标正式产物存在、fresh，追跑 check 完成；账本将缓存回放与实付分开。
- 依据：`novel/llm/cache.py::cache_key` 决定同参数重跑能不能命中回放；
  `novel/llm/cache.py::ResponseCache` 与 CLI 的写盘顺序不是一个事务。

### FN-006 · 语义结论按文本 sha1 绑定，不跟机械验收的轮次走

- 状态：`contract-confirmed`
- 触发：跑过 `stage5.review` 或 `stage6.hook --recheck` 之后，不确定 `stage6.semantic`
  要不要跟着重跑——**它是付费节点**，判错这一步就是一次没有理由的实付。
- 判断：全链只有 `stage6.semantic` 一个语义节点，它的每条决议按**候选文本的 sha1** 绑定，
  并且只在 `stage6/hook.json` 不 fresh 时整体作废。重跑 `stage5.review` 不产生新的绑定序号、
  也不会让已有语义结论过期。⚠ 产物里的 `semantic_review_seq` 记的是“哪一轮语义审判给出了这条
  结论”，**不是**“绑定了哪一轮机械验收”，别按名字反推出一条机械→语义的耦合。
- 处置：候选文本 sha1 未变时不要重跑 `stage6.semantic`。确实改过开篇候选
  （`--apply-edit` 开新轮、`--revalidate` 提升）时 `hook.json` 会失去 fresh，
  那时才按主链重跑一次。
- 验证：`stage6/semantic_<n>.json` 里每条 review 的 `text_sha1` 覆盖当前候选集合，
  `state` 判 stage6 完成；账本这一段没有新增实付记录。
- 依据：`novel/stages/stage6/semantic.py::text_sha1` 与该模块按 sha1 比对
  `current_shas` / `judged_shas` 的过期判定；同文件对 `stage6/hook.json` 的 freshness 前置检查。

### FN-007 · 语义泄底可能源于上游揭示章误标

- 状态：`contract-confirmed`
- 触发：`semantic_secret_leak` 的证据在该章源文中本来就存在。
- 判断：若成稿只是保留源章已发生的揭示，根因可能是 Stage2 权威
  `reader_reveal_events` 时线标晚，而不是写手跨章泄底。多次/分步揭示不再被压成单点。
- 处置：逐句对照源章，不误改成稿。上游标注错误时走 typed `stage2.global --apply-edit`
  修正 secret 时线，重跑受影响的 Stage5 review/semantic；确认不修时用当前 finding
  证据键的 `stage5.review --wontfix`留账。
- 验证：记录源文章号、逐字证据、更正后的 `reader_reveal_events` 与 fresh semantic finding
  的对应关系；旧主体绑定结论必须过期。
- 依据：`novel/stages/stage2/extract.py::run_apply_edit` 与 `_apply_secret_patches` 的 typed 修订路；
  `novel/wontfix.py::mark` 与 `novel/wontfix.py::applied_to_outcomes` 的证据键收敛链。

### FN-008 · stage6 首轮只有 INVALID

- 状态：`contract-confirmed`
- 触发：首次 `stage6.hook` 被落盘闸拦下，盘上只有 `hook.INVALID.json`，随后 `--check-only` 找不到正式 `hook.json`。
- 判断：check-only 只读正式产物；候选级 finding 仍可直接从活动 INVALID 读取。
- 处置：按 INVALID 的 `candidates[].findings` 修复。`hook_para_sep` 若来自单段墙文，归一化不能凭空创建段落；在完整句边界重写为多段后走 `--apply-edit`，或跑 `stage6.hook --revalidate --actor <ACTOR> --reason <why>` 强审计提升。**判据自己会说这一句**（detail 里出现「一整段」「归一化修不掉」即此类），无须再从档位反推。
- 验证：正式 `hook.json` 落盘，mechanical check 可读且至少一条候选无 HARD；再运行 semantic。
- 依据：`novel/stages/stage6/hook.py::run_check_only` 只读正式产物；
  `novel/stages/stage6/measure.py::normalize_hook`「换得了分隔符、造不出段落边界」。

### FN-009 · stage6 总报告与所选候选不是同一粒度

- 状态：`observed`
- 触发：落选候选仍有 HARD，或同一 sha1 的两轮语义判断相互矛盾。
- 判断：总报告汇总所有候选；它可以红而所选候选合格。相同输入的矛盾判定表明语义判官非确定性，不应“采最后一次绿”。
- 处置：按 label+sha1 对齐候选级结论；采更严格判断。三候选全不合格时只人工改一条，减少新的审判面。
- 验证：approved 文本、所选 label 最新语义结论和 state 三者一致；冲突与落选残红进终审包。
- 依据：`novel/stages/stage6/semantic.py::CandidateAssessment` 是候选级状态的唯一形状；
  总报告是它的汇总。非确定性那一半仍是实跑观察。

### FN-010 · stage7 人物表不是全部 surface

- 状态：`observed`
- 触发：`rn_kept_contains_mapped`、`rn_semantic_marker_changed`、短别名或全文补扫发现旧名残留。
- 判断：人物 registry 可能漏掉拆名、家族简称、门派、府邸、外文家名或单字昵称；kept 还可能保护内嵌旧名。
- 处置：在 `name_map_edit.json` 中展开内嵌映射、保留数字/排行/关系标记；单字别名用 `old == new` 明确保留，并用带上下文的长 `extra_subs` 处理真实位置。
- 验证：所有 subs 旧键 count 为零，补集 surface 扫描无未解释旧名，新名不碰通用关系词。
- 依据：`novel/stages/stage7/subs.py::residual_scan` 的补集扫描；
  `novel/stages/stage7/rename.py::_judge` 的映射判据；成书侧另做逐字扫描。

### FN-011 · stage7 映射编辑后必须重批重拼

- 状态：`contract-confirmed`
- 触发：已批准或已 assemble 的书再次运行 `stage7.rename --apply-edit`。
- 判断：当前产物可能保留旧 `approved`/`book_txt` 元数据；这些值不能证明新映射已被批准或已进入盘上成书。
- 处置：无条件重新运行 `stage7.approve`、`stage7.assemble` 和全部成书逐字对账。
- 验证：book sha1/chars/chapters 与盘一致，旧键归零，批准记录晚于当前映射轮。
- 依据：`novel/stages/stage7/rename.py::_retire_book_txt` 退休旧成书；
  `novel/stages/stage7/assemble.py::run_assemble` 才是盘上成书的唯一产地。

### FN-012 · `consumed_changed` 可能是诊断噪声

- 状态：`observed`
- 触发：`state` 把 writer context 标为 `stale/consumed_changed`，但 provenance 的逐文件指纹与盘上输入一致。
- 判断：只有逐项指纹相同才能怀疑诊断噪声；不能仅凭历史经验忽略 stale。
- 处置：核对全部 inputs、code 与实际消费投影。证据完全一致时披露诊断边，不因此重跑付费 plan；存在任何差异时按真实 stale 处理。
- 验证：下游 check 读取的 context 与当前 plan/global/blueprint 指纹一致。
- 依据：`novel/prov.py::freshness` 判 `consumed_changed` 的那一支；
  「指纹一致却仍判 stale」这一半是实跑观察，未被提升为实现契约。

### FN-013 · 语义审判的结论恒是带证据的 `UNVER`

- 状态：`contract-confirmed`
- 触发：`stage6.semantic` 的产物 `stage6/semantic_<n>.json` 落盘，但里面每一条
  `decisions[].verdict` 都是 `UNVER`。
- 判断：**这是产品的正常终态，不是“没跑起来”。** 本仓没有把 judge 结论升级成自动
  `PASS`/`FAIL` 的装置（evaluator profile 那一套已整批下线），合并决议在类型上
  就只能是 `UNVER`。要分辨的是另一件事——读 `judges` 里两名盲判官各自的 `verdict` 与
  `evidence`：**判官给了证据**（正常，人据此核验）与**判官没给证据**（`evidence` 为空，
  那才是真有问题，要重跑或转人工细读）是两回事。合并 `reason` 会逐句说明属于哪一种，
  其中「A/B 同判且各自证据闭合」是信息量最大的一条，别把它读成“没有结论”。
- 处置：读 `judges` 两侧各自的 verdict / reason / evidence，取得带证据的 `UNVER`
  与匿名质量排序后，按
  `stage6.approve --pick N --actor <ACTOR> --reason <理由> --subject-sha256 <完整 SHA-256>`
  收口。不因结论是 `UNVER` 就停机，也不在零语义产物的情况下直接写核验结论。
- 验证：`stage6/semantic_<n>.json` 落盘且 fresh；批准后的 resolution 记
  `basis='human_verification'`；终审包记为“判官证据 + 人工核验”，不记为机器绿灯。
- 依据：`novel/quality/evaluator.py::double_blind_decision`（合并恒 `UNVER`，并给出四选一的
  人读 reason）；`novel/stages/stage6/semantic.py::require_human_resolution`。

### FN-014 · 超限章有一条 0 成本销账路

- 状态：`contract-confirmed`
- 触发：`inbox` 出现 `[verification] stage5 chNN trim_audit_chNN`，文案含
  “保留 pretrim，转 verification”。
- 判断：这不是失败，是**唯一**的出路——本仓没有任何「自动生成收缩候选并采用」的路径
  （收缩臂随 evaluator profile 一并整臂下线），超上限章一律零调用落 identity
  `TrimAudit` 并转人工。与 semantic 节点不是一回事：那一层会真的调用判官，这一层零调用。
  逐条读“超限根因附注”里的实际幅度再决定。
- 处置：接受当前原稿时跑
  `stage5.review --retain-pretrim --chapters <N> --actor <ACTOR> --reason <理由>`
  （0 次 LLM，只写 hash-bound resolution，恰好一章）。判断该章确需改写时用
  `stage5.write --apply-edit <绝对路径>` 提交该章全部 producer-owned slot 的完整
  `ChapterResponse`（同样 0 次 LLM）。**两条都不要留着不销账**——留着它们会让终审包的
  verification 余额里混进已经看过的项。
- 验证：`trim_resolution_chNN.json` 落盘且 hash 绑定当前正文；`inbox` 该条离开 verification。
- 依据：`novel/stages/stage5/trim_resolution.py::retain_pretrim`；
  `contracts/invariants.py::CONDITIONAL_REQUIRED_FLAGS['stage5.review', '--retain-pretrim']`。

### FN-015 · 落盘闸拦下后，finding 细节的读法逐层不同

- 状态：`contract-confirmed`
- 触发：任一层被落盘闸拦下返回 exit 2，需要知道“错在哪几条”而不只是终态里的 rule id。
- 判断：`--check-only` 面对活动 `.INVALID` 的取值面**逐层不同**，不要按单一心智模型推断：
  stage2 问题稿优先（有 `.INVALID` 就判它，理由是它是更新的一次尝试）；
  stage3 / stage4 正式稿优先，正式稿不在盘上时才回落判 `.INVALID`；
  两者都会在报告第一节标注「⚠ 判的这一份是 .INVALID 稿」，不会把它冒充成正式结论。
  stage6 只读正式产物，候选级 finding 在 `hook.INVALID.json` 的 `candidates[].findings` 里（见 FN-008）；
  stage7 也只读正式产物。**stage4 的 `.INVALID` 不带 findings**（schema 无此字段），
  所以那一层的逐条证据只能从报告拿，拿不到就是读法用错了。
- 处置：被拦下后**第一步就是 `--check-only`** 取逐条命中，再按 finding 的 `fix`/`how` 在
  允许写入面上修，最后 `--revalidate --actor <ACTOR> --reason <text>` 同轮重判提升。
  只有正式稿与 `.INVALID` 两份都不在盘上才是真的“先跑那一层”。
  不要因为终态只给了 rule id 就盲改、自行复算判据或停机。
- 验证：`--revalidate` 打印的完整报告里该 rule 归零，`state` 判该层完成，活动 `.INVALID` 已归档。
- 依据：`novel/stages/stage2/extract.py::_read_raw` 的问题稿优先；
  `novel/stages/stage3/style.py::run_check_only` 与 `novel/stages/stage4/plan.py::run_check_only`
  的 `judged` 回落；`novel/stages/stage6/hook.py::run_check_only` 与
  `novel/stages/stage7/rename.py::run_check_only` 只读正式产物；`novel/report/render.py` 的 invalid 标注行。

### FN-016 · `--apply-edit` 的路径语义逐层不同

- 状态：`contract-confirmed`
- 触发：`--apply-edit <路径>` 返回 exit 2，原文含“落在这本书的产物目录之外”；或反过来，
  按“一律传绝对路径”的老办法把稿子放在书目录**外**，某些层却收下了。
- 判断：**没有统一口径，不要拿一层的行为推另一层。** `stage3.style` / `stage4.plan` /
  `stage6.hook` / `stage7.rename` 共用 `edit_rel`：相对路径按**书目录**解释（不是进程 cwd），
  绝对路径只要落在书目录内也收，目录外与 `..` 穿越算平后一律关闭式拒绝。
  `stage5.write` 与 `stage0.edit` **刻意不走这条**，路径语义是“任意路径”——服务进程把人工改稿
  放在书目录之外的 `<base>/_service/edits/` 再喂给 `stage5.write --apply-edit`，那条现役 HTTP
  改稿路依赖它（`R-11.3` 要求服务进程不写产物盘）。`stage2.global --review-decisions` 与前一组同规矩。
- 处置：走 `edit_rel` 那四层时把 `--apply-edit` 的编辑文件写进书目录内（`<novel-dir>/<stage>/..._edit.json`），
  传相对书目录的路径或书目录内的绝对路径都可以。被拒时先读报错里算出来的那个绝对路径，
  它才是代码真正解释成的位置；不要靠加 `..` 或换 cwd 绕过去。
- 验证：命令 exit 0 且新一轮落盘、上一轮归档进 `_rounds/`；操作包里记的是解析后的绝对路径。
- 依据：`novel/paths.py::edit_rel` 的相对/绝对分支与目录外终态，其 docstring 记明
  `stage5.write` / `stage0.edit` 的例外与 `novel/api/routes/edits.py` 对该例外的依赖。
