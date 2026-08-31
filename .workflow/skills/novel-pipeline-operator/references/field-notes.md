# 经验库

## 使用方式

**先检索，不要整读。** 按节点、rule id、退出码或报错片段检索整个 reference 目录：

```text
rg -n "stage6|hook_para_sep|exit 70" .workflow/skills/novel-pipeline-operator/references/
```

只把命中的条目作为诊断提示。当前 finding、代码、测试和盘上原始证据优先。`observed` 不能提升为不变量；`open-gap` 不能被写成已支持。

下面的索引是**条目的投影**（标题与状态逐字取自条目本身，由校验器对账），可以只读索引就决定要不要打开某条：
「状态」告诉你它够不够格影响决策，「处置入口」很多时候就是你要的那条命令。

## 索引

| ID | 标题 | 状态 | 触发键（节点 / rule / 退出码） | 处置入口 |
|---|---|---|---|---|
| FN-001 | stage0 批准前的预期红灯 | contract-confirmed | `s0_approval`, exit 1 | `stage0.approve` |
| FN-002 | 重号与缺号章节 | contract-confirmed | `s1_chapter_labels` | 核对源文标记与 ordinal，不改正文 |
| FN-003 | Stage2 部分完成盘面按 run 收口 | contract-confirmed | Stage2 下游缺失, `repair_manifest.json`, `checkpoint_restored` | 同一 `run_id` resume；Stage4 前收齐 review |
| FN-004 | blueprint 锚点失败要先判原字节 | observed | `hinge_anchor_void`, `verbatim_failed` | `--chapters`（付费）或 finding 明列的免费 fix |
| FN-005 | 钱已调用但目标产物缺失 | contract-confirmed | exit 70, 账本有调用而产物不在盘 | Stage2 同 run 恢复 checkpoint；其余完全相同参数重跑 |
| FN-006 | 语义结论按文本 sha1 绑定，不跟机械验收的轮次走 | contract-confirmed | `stage6.semantic`, `semantic_review_seq` | 文本未变就**不要**重跑；变了才重跑 |
| FN-008 | stage6 首轮只有 INVALID | contract-confirmed | `hook.INVALID.json`, `hook_para_sep` | `stage6.hook --apply-edit` 或 `--revalidate` |
| FN-009 | stage6 总报告与所选候选不是同一粒度 | observed | 落选候选 HARD, 同 sha1 两轮矛盾 | 按 label+sha1 对齐，采更严格那一轮 |
| FN-010 | stage7 人物表不是全部 surface | contract-confirmed | `rn_kept_contains_mapped`, `rn_semantic_marker_changed`, `rn_derived_alias_candidate` | `name_map_edit.json` 展开内嵌映射 |
| FN-011 | stage7 映射编辑后必须重批重拼 | contract-confirmed | `approved`, `book_txt` | 重跑 `stage7.approve` + `stage7.assemble` |
| FN-012 | `consumed_changed` 可能是诊断噪声 | observed | `consumed_changed`, stale | 逐项指纹全同才披露诊断边，否则按真 stale 处理 |
| FN-013 | 语义审判的结论恒是带证据的 `UNVER` | contract-confirmed | `stage6.semantic`, `UNVER`, `judges` | 读 `judges` 两侧再人工收口 |
| FN-014 | 超限章有一条 0 成本销账路 | contract-confirmed | `trim_audit_chNN`, verification | `--retain-pretrim --chapters` 或 `--apply-edit` |
| FN-015 | 落盘闸拦下后，finding 细节的读法逐层不同 | contract-confirmed | `.INVALID`, exit 2 | 先 `--check-only` 取逐条，再 `--revalidate` 提升 |
| FN-016 | `--apply-edit` 的路径语义逐层不同 | contract-confirmed | `edit_rel`, 产物目录之外, exit 2 | `--apply-edit`，编辑文件写进书目录内 |
| FN-017 | `--revise` 只吃 UNIQUE，FREE/NONE 转人工 | contract-confirmed | `--revise`, `repair`, 没有可自救的条目 | 按 finding 的免费路修；没有唯一修法时 `--apply-edit` 或 `--wontfix` |
| FN-018 | 宿主杀掉后台句柄后，逐章节点仍是可续的 | observed | 后台终止, `--chapters`, 账本无记录 | 前台 + `--chapters` 分批重跑 |
| FN-019 | 并行会话改仓库代码会让 CLI 崩在 import 期 | observed | `NameError`, import, 与本书无关的栈 | 等对方写完后重试同一条命令，不改仓库代码 |
| FN-020 | 审阅 info finding 要按判据实际读的字段读 | contract-confirmed | `present_continuity`, `present_char_ids`, 字段名相近 | 先定位判据实现，再照它读的那几个字段取证 |
| FN-021 | METRIC/UNVER 重复出现不是 stalled | contract-confirmed | `stalled`, `unresolved_rounds`, `metric`, `unver` | 只让 HARD/INFO 进入 stalled；不要给读数编修复任务 |
| FN-022 | 长退出序列用无损 RLE 与完整摘要双重锁定 | contract-confirmed | `exit_sequence_compact_text`, `exit_sequence_sha256`, 终审包 | 复制收集器输出，不手抄连续同项 |
| FN-023 | Stage4B 拒稿先看 owner，再决定等不等 | contract-confirmed | `typed_failures`, `repair_owner`, `_llm_rejected`, `GateBlocked` | 按 witness 的 owner 走 revalidate / A 返修 / 上游修复，不重跑 B |

### FN-001 · stage0 批准前的预期红灯

- 状态：`contract-confirmed`
- 触发：`stage0.remove` 的追跑 check 仅因 `s0_approval` 为 pending 返回 exit 1。
- 判断：这是“尚未批准”，不是剔除质量失败；仍需确认其余 HARD 全部为零。
- 处置：按 stage0 代批规则读剔除段与边界，运行 `stage0.approve`。
- 验证：批准状态落盘，后续 `state` 不再把 stage0 视为等待 approval。
- ⚠ **零剔除的书没有例外分支**：自动批准已停产，一段都没剔的书首跑同样恒
  `pending`，这条红灯照常出现，`stage0.approve` 照常要跑。剔除分母为零时代批责任掉转方向做
  **反向漏剔**抽查，判据见[代批与终审契约](delegated-review.md#stage0-代批)。
- ⚠ **不存在「历史自动批」这种状态**：记录它的取值与配套理由码都已删除。
  存量自动批产物停在旧 schema 版本上，而现役权威已收成 `stage0/revisions.json`、
  本仓又不迁移旧产物。盘上遇到读不进来的旧 stage0，
  正确处置是**免费重跑 `stage0.remove` 再人工批**，不是找一条兼容路。
- 依据：`novel/stages/stage0/checks.py::check` 的 `s0_approval` 分支（批准状态非 approved 即命中）；
  `contracts/judges/stage0.py` 那一行的 sev 与 requirement；
  执行资格的唯一落点是 `novel/stages/stage0/eligibility.py::is_stage0_executable`，
  取值面是 `contracts/invariants.py::APPROVAL_STATE`。

### FN-002 · 重号与缺号章节

- 状态：`contract-confirmed`
- 触发：`s1_chapter_labels` 报告原始章号重号或缺号。
- 判断：scanned 行是原始标签读数，不等于下游 ordinal 已损坏；低频重号可能只是源书标签毛刺。
- 处置：核对源文标记规律与 stage1 产生的 ordinal，不凭告警重写正文。
- 验证：下游 chapter ordinal 唯一、连续且恰好覆盖 `1..N`；高频重复标记另行人工审阅。
- 依据：`novel/stages/stage1/checks.py::check` 产出的 `s1_chapter_labels`（读的是**原始标签**，
  不是下游 ordinal）；成书侧由代批契约的逐字对账第 1 项复核。

### FN-003 · Stage2 部分完成盘面按 run 收口

- 状态：`contract-confirmed`
- 触发：Stage2 下游缺失，或本书出现 `repair_manifest.json` / `checkpoint_restored`；global、逐章
  checkpoint、正式 blueprint 与 review 可能只完成其中一部分。
- 判断：checkpoint 只证明一章模型响应已经通过当时的单章闸，不是正式 artifact，也不提升 Stage2
  完成度；review 的 blocking 面收口也只证明人审支路完成。Blueprint 只等待 current/fresh 称名投影：
  非称名 HARD 留下的 schema-valid Global `.INVALID` 可先物化投影并继续计算；称名 HARD 未收口则投影为
  `unknown/global_naming_blocked`，只能先 typed edit / `--revalidate`（0 LLM），不能 renormalize 或付费重采。
  只有正式 blueprint 编译并通过联合 HARD，且 current review 的 blocking 面逐项 PASS，两支才真正汇合。
  disclosure 非 PASS 保留披露但不挡汇合。已经验证并 checkpoint 的章不应因别章失败重新付费。
- 处置：先 `state` 看正式产物。manifest 点名恢复时必须使用同一 `run_id` resume，让成功章复验恢复；
  没有可恢复 sidecar 才按当前 preview 补跑缺失节点。global review 可与 blueprint 计算重叠，但必须在
  Stage4 前收齐；旧 style 不补跑、不迁移。
- 验证：恢复章出现 `checkpoint_restored` 且新增 wire 为零；`stage2.blueprint --check-only` fresh 且无
  HARD，review current 且 blocking 面逐项 PASS，`state` 显示 Stage2 完成；disclosure 未决项仍在 inbox /
  终审包披露。
- 依据：`novel/llm/stage2_runs.py::restore_checkpoint`；
  `novel/stages/stage2/extract.py::_raise_response_repair`；
  `contracts/invariants.py::stage2_review_required`。

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
- 处置：先 `state`。Stage2 blueprint 已有 run sidecar 时用原 `run_id` 恢复，已验证章从 checkpoint
  复验；其余节点用完全相同参数重跑。不得通过换身份、换参数、删 checkpoint 或删缓存强制新调用。
- 验证：目标正式产物存在、fresh，追跑 check 完成；Stage2 恢复章为 `checkpoint_restored` 且零新增
  wire，其余缓存回放与实付仍由账本分开。
- 依据：`novel/llm/cache.py::cache_key` 决定同参数重跑能不能命中回放；
  `novel/llm/cache.py::ResponseCache` 与 CLI 的写盘顺序不是一个事务；
  `novel/llm/stage2_runs.py::restore_checkpoint` 负责同 run 的验证后章节恢复。

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

- 状态：`contract-confirmed`
- 触发：`rn_kept_contains_mapped`、`rn_semantic_marker_changed`、`rn_derived_alias_candidate`、短别名或全文补扫发现旧名残留。
- 判断：人物 registry 可能漏掉拆名、家族简称、门派、府邸、外文家名或单字昵称；kept 还可能保护内嵌旧名。`rn_derived_alias_candidate` 会从已改 canonical/alias 推导“旧尾字 + 关系后缀”并给无歧义建议，但中文无词界，所以它只报 INFO、不自动替换。
- 处置：在 `name_map_edit.json` 中展开内嵌映射、保留数字/排行/关系标记；单字别名用 `old == new` 明确保留，并用带上下文的长 `extra_subs` 处理真实位置。
- 验证：所有 subs 旧键 count 为零，补集 surface 扫描无未解释旧名，新名不碰通用关系词。
- 依据：`novel/stages/stage7/subs.py::residual_scan` 的旧姓补集扫描与
  `novel/stages/stage7/subs.py::derived_alias_scan` 的尾字昵称候选；
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
  stage4 正式稿优先，正式稿不在盘上时才回落判 `.INVALID`；
  两者都会在报告第一节标注「⚠ 判的这一份是 .INVALID 稿」，不会把它冒充成正式结论。
  stage6 只读正式产物，候选级 finding 在 `hook.INVALID.json` 的 `candidates[].findings` 里（见 FN-008）；
  stage7 也只读正式产物。**stage4 的 `.INVALID` 不带 findings**（schema 无此字段），
  所以那一层的逐条证据只能从报告拿，拿不到就是读法用错了。
- 处置：被拦下后**第一步就是该 stage 注册的复检节点加 `--check-only`** 取逐条命中；
  `stage2.global` 自身不接受该 flag，要跑 `stage2.blueprint --check-only`（它会同时读取
  global 问题稿与逐章稿）。再按 finding 的 `fix`/`how` 在允许写入面上修，最后
  `--revalidate --actor <ACTOR> --reason <text>` 同轮重判提升。
  只有正式稿与 `.INVALID` 两份都不在盘上才是真的“先跑那一层”。
  不要因为终态只给了 rule id 就盲改、自行复算判据或停机。
- 验证：`--revalidate` 打印的完整报告里该 rule 归零，`state` 判该层完成，活动 `.INVALID` 已归档。
- 依据：`novel/stages/stage2/extract.py::_read_raw` 的问题稿优先；
  `novel/stages/stage4/plan.py::run_check_only`
  的 `judged` 回落；`novel/stages/stage6/hook.py::run_check_only` 与
  `novel/stages/stage7/rename.py::run_check_only` 只读正式产物；`novel/report/render.py` 的 invalid 标注行。

### FN-016 · `--apply-edit` 的路径语义逐层不同

- 状态：`contract-confirmed`
- 触发：`--apply-edit <路径>` 返回 exit 2，原文含“落在这本书的产物目录之外”；或反过来，
  按“一律传绝对路径”的老办法把稿子放在书目录**外**，某些层却收下了。
- 判断：**没有统一口径，不要拿一层的行为推另一层。** `stage4.plan` /
  `stage6.hook` / `stage7.rename` 共用 `edit_rel`：相对路径按**书目录**解释（不是进程 cwd），
  绝对路径只要落在书目录内也收，目录外与 `..` 穿越算平后一律关闭式拒绝。
  `stage5.write` 与 `stage0.edit` **刻意不走这条**，路径语义是“任意路径”——服务进程把人工改稿
  放在书目录之外的 `<base>/_service/edits/` 再喂给 `stage5.write --apply-edit`，那条现役 HTTP
  改稿路依赖它（服务进程只写 `_service/`，产物变更一律经 CLI 子进程）。
  `stage2.global --review-decisions` 与前一组同规矩。
- 处置：走 `edit_rel` 的现役层时把 `--apply-edit` 的编辑文件写进书目录内（`<novel-dir>/<stage>/..._edit.json`），
  传相对书目录的路径或书目录内的绝对路径都可以。被拒时先读报错里算出来的那个绝对路径，
  它才是代码真正解释成的位置；不要靠加 `..` 或换 cwd 绕过去。
  ⚠⚠ **反过来，`stage5.write` / `stage0.edit` 的改稿要写进 `<base>/_service/edits/`，
  不要按那四层类比放进 `<novel-dir>/stage5/`。** 它收任意路径 ⇒ 放书目录里**不会报错**，
  但那四层的编辑文件是登记在 `ARTIFACTS` 里的（所以才必须在书目录内），
  这两层**刻意没有登记** ⇒ 一份 `stage5/chNN_edit.json` 会让
  `tests/corpus.py::_classify` 关闭式失败，**这本书从此当不成语料底座**。
  真发生过：三份改稿留在 `stage5/` 下，那本书的语料验收从此全线跑不动。
- 验证：命令 exit 0 且新一轮落盘、上一轮归档进 `_rounds/`；操作包里记的是解析后的绝对路径。
- 依据：`novel/paths.py::edit_rel` 的相对/绝对分支与目录外终态，其 docstring 记明
  `stage5.write` / `stage0.edit` 的例外与 `novel/api/routes/edits.py` 对该例外的依赖。

### FN-017 · `--revise` 只吃 UNIQUE，FREE/NONE 转人工

- 状态：`contract-confirmed`
- 触发：成稿仍有 finding，想统一交给 `stage5.write --revise`，却得到 exit 2
  「这一轮没有可自救的条目」，并列出被跳过的条数。
- 判断：**这不是付费调用失败，而是修复分流在请求发出前关闭式退出。** `--revise` 只消费
  `repair == 'UNIQUE'` 且尚未超过轮次上限的条目。现役 51 条中 repair 分布为
  **12 UNIQUE、12 FREE、27 NONE**：FREE 必须按 finding 指名的 0 成本入口修，NONE 表示满足路径
  不唯一、必须人工判断。`remediation` 与 `repair` 是两格，前者说“归谁修”，后者才是付费自救
  的准入（见[修复状态机](recovery.md)）。
- 处置：先按 Review 中的 `repair` 分桶；UNIQUE 才运行 `--revise`，FREE 跑 finding 指名的
  deterministic 路径，NONE 则二选一：`--apply-edit` 提交该章完整 `ChapterResponse`
  （0 次调用，会让下游 stale 需重跑），或 `--wontfix` 记带 actor/reason 的不修裁决。
  如果误跑 `--revise` 命中“没有可自救的条目”，先确认 ledger 没有新增调用，再回到上述分流。
  ⚠ 代笔整章正文是**授权范围内**的，但这条路**没有任何守卫替你兜**：文风、保真材料与在场集
  的一致性都不在任何判据的扫描面内，改坏了不会红。所以代笔前先取三样依据——该章 blueprint
  的拍子、由 Stage1 正文即时计算的机械基线、该章在场角色集——写完逐项自查并把方法与结论写进
  终审包。取不到这三样依据时选 `--wontfix` 留一条明确裁决，而不是凭手感改正文。
- 验证旧豁免：`wontfix` 绑的是 `(章, rule, evidence_key)`，不是“这章从此免检”。
  `--apply-edit` 改正文后必须先重跑 `stage5.review`；证据键变化的新 finding 会自动重新进入
  `human_only`，只对当前仍命中的新证据重新裁决，不删除或篡改旧豁免账。
- 验证：无 UNIQUE 时这次尝试没有新增调用；选 `--wontfix` 时
  `inbox` 该项离开 `human_only` 并计入 wontfix 豁免；选 `--apply-edit` 时新一轮落盘且下游重跑通过。
- 依据：`contracts/rules.py::_validate` 的 repair 约束；
  `novel/stages/stage5/write.py` 的 `--revise` 选择与轮次守卫；完整分布由
  `contracts/judges/stage5.py` 的现役注册表机械统计。

### FN-018 · 宿主杀掉后台句柄后，逐章节点仍是可续的

- 状态：`observed`
- 触发：把付费长节点交给宿主的后台/detached 句柄，进程在中途消失。通知可能先报"完成/退出码 0"
  再改报"已终止"，两者都不可信。
- 判断：只看盘上三样，别看通知——串联脚本最后那行退出码回显在不在、目标产物在不在、
  账本有没有该节点的记录。三样全空 ⇒ 没有任何调用完成，重跑不是重复付费；
  部分产物在盘且账本有对应条数 ⇒ 那几章已完成并已计费，只需补其余章。
- 处置：改用**前台 + 分批**：逐章节点用 `--chapters` 把单次运行切到宿主超时以内。
  这条安全的根据是 `--chapters` 缺省语义就是"补写所有缺的章" ⇒ 逐章节点天然增量，
  已落盘的章不会被重写。⚠ 前台超时是硬杀，宁可多分几批，不要设一个刚好够呛的超时去赌。
- 验证：分批跑完后每章恰有一份正式稿、无活动 `.INVALID`；账本该节点的调用数等于章数。
- 依据：`contracts/invariants.py::VALUED_FLAGS` 里 `--chapters` 那一格的"不给就补写所有缺的章"；
  中断可续的另一半由 `novel/llm/cache.py::cache_key` 兜住（同参数重跑命中回放，见 FN-005）。

### FN-019 · 并行会话改仓库代码会让 CLI 崩在 import 期

- 状态：`observed`
- 触发：某条命令让 CLI **整体**崩在 import 期（例如某模块 `NameError`），
  而同一条命令几分钟前还正常，报错栈与本书、本节点、本产物全都无关。
- 判断：先分清是"仓库瞬时不一致"还是"本书的问题"。`git status` 显示 `novel/` 下有未提交改动
  且报错文件正在其中 ⇒ 是别的会话正在改这个仓库，你撞上了它写文件的中间时刻。
  ⚠ 崩在 import 期意味着**没有写盘**，本书状态干净。
- 处置：**不改仓库代码**（本 skill 的硬边界），等对方那次编辑完成后重试同一条命令。
  重试前重新取一次乐观锁 hash——代码变化有可能改变决策面；两次取值相同正好证明它没被影响。
  把这次干扰如实记进终审包，不要写成本书的产物问题。
- 验证：目标模块可单独 import 成功；重试后命令 exit 0；重取的 hash 与崩溃前逐字相同。
- 依据：原始证据类型——命令 stderr 的 import 栈、`git status` 的未提交改动清单、
  重试前后两次乐观锁取值。

### FN-020 · 审阅 info finding 要按判据实际读的字段读

- 状态：`contract-confirmed`
- 触发：要人工判定某条 info 是真问题还是假阳，于是去产物里找证据，
  却发现自己算出的命中集合与判据报的对不上（典型形状：判据点名的对象在你读的那个字段里根本不存在）。
- 判断：**产物里常有一对名字相近、取值不同的字段**，判据读的是哪一个由实现决定，凭字段名猜会得出反向结论。
  已知一例（`present_continuity`）：在场连续性判据扫的是每一拍的**事件描述**字段与**角色 id 集合**，
  而不是形近的正文片段字段与显示名集合——照后者读会算出"判据点的那个人根本没出现"，
  从而把一条真信号读成判据坏了。
- 处置：先用 rule id 在 `novel/stages/<stage>/checks.py` 里定位那段实现，看清它读哪几个键、
  命中条件怎么写，再照同样的键取证。判据的严重度与它的分辨力是一致的：
  按叫法在自由文本里做广度扫描的判据必然分不开"在场"与"被提及/被回忆/正在离场"，
  这正是它定为 info 而非 hard 的原因 ⇒ 逐条读出语义类别后保留并解释，
  比反向改产物安全（把不在场的人补进在场集，下游写作会真的把人写进场景）。
- 验证：能用判据读的那几个字段复现出与报告完全相同的命中集合；每条命中都写出语义类别与判定。
- 依据：`novel/stages/stage2/checks.py::_present` 的在场族判定（读 `event` 与 `present_char_ids`，
  按角色去重而非按名字）；严重度分级的理由在 `contracts/judges/stage2.py` 该条的 `_info` 登记。

### FN-021 · METRIC/UNVER 重复出现不是 stalled

- 状态：`contract-confirmed`
- 触发：重复运行 `stage5.review` 后，`inbox` 把 `dialogue_report`、`secret_timeline`、
  `name_surface_survey` 或 `must_hold_unver` 列成 `stalled`，并建议改正文或记 `wontfix`。
- 判断：Review 的收敛账会为所有 finding 带上 `unresolved_rounds`，包括每轮无条件出现的
  `metric` 与结构性 `unver`。它们连续两轮仍存在，只证明读数/边界仍存在，不证明修复失败；
  尤其 UNVER 按清单总纪律本就永不入列。只有 HARD/INFO 质量结论才可能升级为 stalled。
- 处置：不对 METRIC/UNVER 运行 `--apply-edit` 或 `--wontfix`。先确认当前代码的
  `novel.views.inbox::_is_stage5_stalled` 只接受 HARD/INFO，再刷新 `inbox`；若旧版本仍误列，
  这是分类器 bug，不是书稿余额。
- 验证：同一份 Review 即便上述 finding 的 `unresolved_rounds >= 2`，`stalled` 仍为 0；
  真正的 HARD/INFO 到阈值仍进入 stalled，且定向守卫两侧都覆盖。
- 依据：`novel/views/inbox.py::_is_stage5_stalled`；`tests/test_views_inbox.py` 的纯函数阈值用例。

### FN-022 · 长退出序列用无损 RLE 与完整摘要双重锁定

- 状态：`contract-confirmed`
- 触发：一本书的 `agent_progress.jsonl` 含几十个连续同名复检事件，终审包需要列完整退出顺序；
  人工复制时很难看出多一个或少一个 `stage5.review:0`。
- 判断：长重复序列不是适合手抄的证据。相邻同项 RLE（如 `stage5.review:0×52`）可无损展开，
  同时以规范 JSON 的事件数与 SHA-256 锁定完整序列；人读表示、机器摘要与原始 JSONL 三者互证。
- 处置：从仓根运行 `.workflow/skills/novel-pipeline-operator/scripts/collect_final_evidence.py`，复制 `exit_sequence_compact_text` 与
  `review_anchor_line`。不要自己计数、拼箭头或从日志截一段冒充全历史序列。
- 验证：`.workflow/skills/novel-pipeline-operator/scripts/validate_review.py` 要求终审包含当前压缩序列，并逐项比较机器锚点中的
  `exit_sequence_count` / `exit_sequence_sha256`；任一事件变化都会失败。
- 依据：`.workflow/skills/novel-pipeline-operator/scripts/collect_final_evidence.py::_compact_exits` 与
  `.workflow/skills/novel-pipeline-operator/scripts/validate_review.py::validate`。

### FN-023 · Stage4B 拒稿先看 owner，再决定等不等

- 状态：`contract-confirmed`
- 触发：`stage4.plan` 一次 wire 后即落 `GateBlocked`（`_INVALID`），日志里没有第二轮反馈；
  或同一判据只续改一轮就提前收口。
- 判断：这是 `R-5.21` 的关闭式路由在工作，不是卡死。B 的联合 HARD 拒稿先按 typed
  witness 的 `repair_owner` 路由：`stage4.design` / `stage2.blueprint` / `code` 的失败重试 B
  不可能闭合，**首轮即停**；同一 fingerprint 集连续两轮未闭合也提前终止。只有
  `stage4.plan` owner（B 自己的绑定错）才回同一 session 续改。
- 处置：完整 witness 在 `_llm_rejected/<chain>/<attempt>/rejection.json` 的 `typed_failures`
  里（含 code/章号/refs/owner），`repair_eligible=false` 是正常的——收口不走
  `--response-repair`，按 owner 走 `stage4.plan --revalidate`（判据假红）或 A 的
  decision-bound repair（真设计错）/ 上游修复。`_service/llm_audit.jsonl` 的 reject_detail
  只有无正文读数（code/chapter/fingerprint），不要在那里找判词。
- 验证：`tests/test_stage4_typed_failures.py` 的首轮停与两轮早停用例；owner 表改动的
  正反例都在 `contracts/invariants.py::STAGE4_FAILURE_OWNERS`。
- 依据：`novel/stages/stage4/plan.py::run_plan` 与 `::_keep_business_witness`；
  `novel/llm/typed_failures.py`。
