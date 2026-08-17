# 经验库

## 使用方式

先按节点、rule id、退出码或报错片段检索，例如：

```text
rg -n "stage6|hook_para_sep|exit 70" .claude/skills/novel-pipeline-operator/references/field-notes.md
```

只把命中的条目作为诊断提示。当前 finding、代码、测试和盘上原始证据优先。`observed` 不能提升为不变量；`open-gap` 不能被写成已支持。

## 索引

| ID | 主题 | 关键词 |
|---|---|---|
| FN-001 | stage0 批准前的预期红灯 | `s0_approval`, pending |
| FN-002 | 重号与缺号章节 | `s1_chapter_labels`, ordinal |
| FN-003 | full-extract 部分完成 | `--full-extract`, upstream missing |
| FN-004 | blueprint 锚点分流 | `hinge_anchor_void`, `verbatim_failed` |
| FN-005 | 中断后的缓存重跑 | exit 70, cache, no artifact |
| FN-006 | review 与 semantic 绑定 | `mechanical_review_seq`, stale |
| FN-007 | 语义泄底的上游误标 | `semantic_secret_leak`, wontfix |
| FN-008 | stage6 首轮 INVALID | `hook.INVALID`, `hook_para_sep` |
| FN-009 | stage6 候选级语义 | unselected HARD, nondeterminism |
| FN-010 | stage7 映射边角 | kept, marker, one-char alias |
| FN-011 | stage7 编辑后重装配 | approved, book_txt, stale |
| FN-012 | 来源指纹诊断噪声 | `consumed_changed`, fingerprints |
| FN-013 | 语义审判：advisory 与标定的分界 | `stage6.semantic`, `--advisory`, `NOVEL_EVALUATOR_PROFILE`, exit 2 |
| FN-014 | 超限章的 0 成本销账 | `trim_audit_chNN`, `--retain-pretrim`, verification |
| FN-015 | 落盘闸拦下后 finding 细节的读法 | `.INVALID`, `--check-only`, `--revalidate`, exit 2 |

### FN-001 · stage0 批准前的预期红灯

- 状态：`contract-confirmed`
- 触发：`stage0.remove` 的追跑 check 仅因 `s0_approval` 为 pending 返回 exit 1。
- 判断：这是“尚未批准”，不是剔除质量失败；仍需确认其余 HARD 全部为零。
- 处置：按 stage0 代批规则读剔除段与边界，运行 `stage0.approve`。
- 验证：批准状态落盘，后续 `state` 不再把 stage0 视为等待 approval。
- 依据：stage0 approval 状态机与主链顺序。

### FN-002 · 重号与缺号章节

- 状态：`contract-confirmed`
- 触发：`s1_chapter_labels` 报告原始章号重号或缺号。
- 判断：scanned 行是原始标签读数，不等于下游 ordinal 已损坏；低频重号可能只是源书标签毛刺。
- 处置：核对源文标记规律与 stage1 产生的 ordinal，不凭告警重写正文。
- 验证：下游 chapter ordinal 唯一、连续且恰好覆盖 `1..N`；高频重复标记另行人工审阅。
- 依据：stage1 partition report 与成书章序对账。

### FN-003 · `--full-extract` 部分完成

- 状态：`contract-confirmed`
- 触发：`stage2.global --full-extract` 在 global 或后续落盘闸门处停止，blueprint/style 缺失。
- 判断：组合命令不是原子事务；已经正式落盘的上游不应为补齐下游而重复付费。
- 处置：按 finding 修复活动失败，再分别 preview 缺失的 `stage2.blueprint`、`stage3.style`。
- 验证：两份 follow-check 都 fresh 且无 HARD，`state` 显示 stage2/stage3 完成。
- 依据：`build_steps` 的 full-extract 追跑规则与产物存在性。

### FN-004 · blueprint 锚点失败要先判原字节

- 状态：`observed`
- 触发：`hinge_anchor_void`、`verbatim_failed` 或相近 span/anchor finding。
- 判断：先在对应源章逐字查锚点。原字节不存在表示模型转述/编造；原字节存在但去标点后退化为高频短串表示 locator/归一化问题。
- 处置：模型问题使用 `stage2.blueprint --chapters <N>` 定向重生成；确定性定位问题只执行 finding 明列的免费 fix，连续同错时停止重复抽样。
- 验证：锚点在源章逐字存在且定位到正确出现位置，定向章与整表 check 均通过。
- 依据：原始响应、源章字节与当前 finding 文案三方对照。

### FN-005 · 钱已调用但目标产物缺失

- 状态：`contract-confirmed`
- 触发：连接中断、宿主终止或 exit 70；账本有调用而目标产物不在盘。
- 判断：账本与产物写入不是事务；“记过调用”不能证明生成成功。
- 处置：先 `state`，再用完全相同参数重跑；不得通过换参数或删缓存强制新调用。
- 验证：目标正式产物存在、fresh，追跑 check 完成；账本将缓存回放与实付分开。
- 依据：持久缓存键与 CLI 写盘顺序。

### FN-006 · mechanical review 与 semantic 绑定

- 状态：`contract-confirmed`
- 触发：正文未变却重跑 `stage5.review` 后，原 semantic 结论变为 stale/unver。
- 判断：semantic 记录绑定当时最新的 `mechanical_review_seq`；新 review 会产生新序号。
- 处置：正文未变时不重跑 review。确已产生新 review 时重新运行 semantic 以绑定当前序号。
- 验证：semantic 的 `mechanical_review_seq`、正文 sha1 与最新 review 一致。
- 依据：stage5 semantic freshness 逻辑。

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
- 依据：Stage2 secret typed edit、Stage5 权威揭示时线与语义 wontfix 收敛链的现役实现。

### FN-008 · stage6 首轮只有 INVALID

- 状态：`contract-confirmed`
- 触发：首次 `stage6.hook` 被落盘闸拦下，盘上只有 `hook.INVALID.json`，随后 `--check-only` 找不到正式 `hook.json`。
- 判断：check-only 只读正式产物；候选级 finding 仍可直接从活动 INVALID 读取。
- 处置：按 INVALID 的 `candidates[].findings` 修复。`hook_para_sep` 若来自单段墙文，归一化不能凭空创建段落；在完整句边界重写为多段后走 `--apply-edit`，或跑 `stage6.hook --revalidate --actor <id> --reason <why>` 强审计提升。**判据自己会说这一句**（detail 里出现「一整段」「归一化修不掉」即此类），无须再从档位反推。
- 验证：正式 `hook.json` 落盘，mechanical check 可读且至少一条候选无 HARD；再运行 semantic。
- 依据：stage6 check-only 与 normalize_hook 的当前行为。

### FN-009 · stage6 总报告与所选候选不是同一粒度

- 状态：`observed`
- 触发：落选候选仍有 HARD，或同一 sha1 的两轮语义判断相互矛盾。
- 判断：总报告汇总所有候选；它可以红而所选候选合格。相同输入的矛盾判定表明语义判官非确定性，不应“采最后一次绿”。
- 处置：按 label+sha1 对齐候选级结论；采更严格判断。三候选全不合格时只人工改一条，减少新的审判面。
- 验证：approved 文本、所选 label 最新语义结论和 state 三者一致；冲突与落选残红进终审包。
- 依据：stage6 候选聚合形状与真实多轮语义观察。

### FN-010 · stage7 人物表不是全部 surface

- 状态：`observed`
- 触发：`rn_kept_contains_mapped`、`rn_semantic_marker_changed`、短别名或全文补扫发现旧名残留。
- 判断：人物 registry 可能漏掉拆名、家族简称、门派、府邸、外文家名或单字昵称；kept 还可能保护内嵌旧名。
- 处置：在 `name_map_edit.json` 中展开内嵌映射、保留数字/排行/关系标记；单字别名用 `old == new` 明确保留，并用带上下文的长 `extra_subs` 处理真实位置。
- 验证：所有 subs 旧键 count 为零，补集 surface 扫描无未解释旧名，新名不碰通用关系词。
- 依据：stage7 mapping schema、residual probe 与成书逐字扫描。

### FN-011 · stage7 映射编辑后必须重批重拼

- 状态：`contract-confirmed`
- 触发：已批准或已 assemble 的书再次运行 `stage7.rename --apply-edit`。
- 判断：当前产物可能保留旧 `approved`/`book_txt` 元数据；这些值不能证明新映射已被批准或已进入盘上成书。
- 处置：无条件重新运行 `stage7.approve`、`stage7.assemble` 和全部成书逐字对账。
- 验证：book sha1/chars/chapters 与盘一致，旧键归零，批准记录晚于当前映射轮。
- 依据：stage7 apply-edit 的元数据继承行为与 assemble 消费路径。

### FN-012 · `consumed_changed` 可能是诊断噪声

- 状态：`observed`
- 触发：`state` 把 writer context 标为 `stale/consumed_changed`，但 provenance 的逐文件指纹与盘上输入一致。
- 判断：只有逐项指纹相同才能怀疑诊断噪声；不能仅凭历史经验忽略 stale。
- 处置：核对全部 inputs、code 与实际消费投影。证据完全一致时披露诊断边，不因此重跑付费 plan；存在任何差异时按真实 stale 处理。
- 验证：下游 check 读取的 context 与当前 plan/global/blueprint 指纹一致。
- 依据：实跑观察；未被提升为实现契约。

### FN-013 · 语义审判：advisory 与标定的分界

- 状态：`contract-confirmed`
- 触发：`stage6.semantic` 返回 exit 2，原文为
  `NOVEL_EVALUATOR_PROFILE 未配置；设置对应本地路径后才可启用自动语义裁决`。
- 判断：这是“没有标定 profile”，**不是“语义层不可用”**。区分两件事：能不能拿到判官证据
  （能，走 `--advisory`）；能不能自动盖章（不能，要 profile）。终态原文只说 profile 那一半，
  不要据此推断整层不可跑。⚠ profile **不降低每本书的调用成本**——校准路与 advisory 调的是
  同样两名盲判官，`require_enabled` 只决定结论允不允许标 `automated=true`。
- 处置：**主链参数本来就带 `--advisory`**；若因手拼 argv 漏掉而 exit 2，带上它重跑即可。
  取得双盲判官的带证据 `UNVER` 与匿名质量排序后，按
  `actor + reason + 完整 subject SHA-256` 的人工核验路收口。
  不伪造 profile，不因缺 profile 停机，也不在零语义产物的情况下直接写核验结论。
- 验证：`stage6/semantic_<n>.json` 落盘且 fresh；产物的
  `evaluator_fingerprint.dataset_sha256` 等于 advisory 哨兵值、全部 `decisions` 为
  `automated=false` / `profile_id=null`；终审包记为“advisory 证据 + 人工核验”，不记为机器绿灯。
- 依据：`contracts/invariants.py::PAID_FLAGS['--advisory']` 与该节点 `NodeSpec.flags`；
  `novel/quality/profile.py::advisory_fingerprint`；
  `novel/stages/stage6/semantic.py::_is_advisory_artifact`。

### FN-014 · 超限章有一条 0 成本销账路

- 状态：`contract-confirmed`
- 触发：`inbox` 出现 `[verification] stage5 chNN trim_audit_chNN`，文案含
  “未运行语义裁决…未发起收缩调用或语义调用，保留 pretrim，转 verification”。
- 判断：这不是失败，是收缩臂的 fail-closed 出路——`trim_equivalence` 的 profile 缺失时，
  `_trim_to_fit` 在构造任何请求之前就退出，正文保持 pretrim 原样。**它没有 `--advisory` 对应物**，
  与两个 semantic 节点不是一回事。逐条读“超限根因附注”里的实际幅度再决定。
- 处置：接受当前原稿时跑
  `stage5.review --retain-pretrim --chapters <N> --actor <自报身份> --reason <理由>`
  （0 次 LLM，只写 hash-bound resolution，恰好一章）。判断该章确需改写时用
  `stage5.write --apply-edit <绝对路径>` 提交该章全部 producer-owned slot 的完整
  `ChapterResponse`（同样 0 次 LLM）。**两条都不要留着不销账**——留着它们会让终审包的
  verification 余额里混进已经看过的项。
- 验证：`trim_resolution_chNN.json` 落盘且 hash 绑定当前正文；`inbox` 该条离开 verification。
- 依据：`novel/stages/stage5/write.py::_trim_to_fit` 的 profile 前置检查；
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
  允许写入面上修，最后 `--revalidate --actor <id> --reason <text>` 同轮重判提升。
  只有正式稿与 `.INVALID` 两份都不在盘上才是真的“先跑那一层”。
  不要因为终态只给了 rule id 就盲改、自行复算判据或停机。
- 验证：`--revalidate` 打印的完整报告里该 rule 归零，`state` 判该层完成，活动 `.INVALID` 已归档。
- 依据：`novel/stages/stage2/extract.py::_raw` 的问题稿优先；
  `novel/stages/stage3/style.py::run_check_only` 与 `novel/stages/stage4/plan.py::run_check_only`
  的 `judged` 回落；`novel/stages/stage6/hook.py::run_check_only` 与
  `novel/stages/stage7/rename.py::run_check_only` 只读正式产物；`novel/report/render.py` 的 invalid 标注行。
