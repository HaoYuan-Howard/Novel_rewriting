# novel-pipeline-operator skill 复盘 · 2026-08-17

从一次完整跑书（`女_Alpha_的军校生活_2049864442305892545`，18 章，`outputs/e2e_20260817_alpha`，
终态 `ready_with_disclosures`）里长出来的 **skill 文档**意见。评的是
`.claude/skills/novel-pipeline-operator/` 这套文档本身，不是流水线代码——凡是根因在代码的，
只写"文档该怎么说"，代码怎么改留给另一份。

**每一条都带这次跑出来的读数、退出码或文件位置**；没有证据的猜测不写进来。

结论先说：主链、闸门语义、代批契约、终审包模板这四样在这次跑书里都真的起了作用——
四次闸门拦截（`present_names_invented` / `goal_landing` / `rn_surface_short` / `rn_overlap`）
全部按文档给的 0-LLM 人工修订路收口，成书逐字对账七项全过。问题集中在**三类**：

1. **文档描述的行为与 CLI 实际行为已经漂移**（4 条，其中 1 条整段漏掉了一个注册在案的主路径 flag，
   直接导致每本书都白丢语义证据）；
2. **判据在退化工况下会塌成空**（语义层不可用时，stage6 的候选筛选链失去全部机器信号）；
3. **执行者侧的成本纪律没有写**（一份 check 报告 36 KB，全链读三十几次）。

分级口径：`P0` = 会让执行者错误停机、走进明令禁止的路，或把没验过的说成验过；
`P1` = 会让执行者做无用功或做错判断；`P2` = 摩擦。

本次跑书的账：实付 22 次调用 / 缓存命中 38 次 / prompt 239733 · completion 255977 ·
reasoning 223189 tokens / 付费节点合计约 886 秒。全部读数见
`outputs/e2e_20260817_alpha/final_review_2049864442305892545.md`。

---

## 逐条清单

| # | 级别 | 一句话 | 落点 | 状态 |
|---:|---|---|---|---|
| S-01 | P0 | skill 整段漏掉 `--advisory`：语义层不是跑不了，是没人跑 | `workflow.md` + `delegated-review.md` + `field-notes.md` | ☑ 已落地（见 S-13） |
| S-02 | P1 | 语义缺席时 stage6 候选筛选链塌成空 | `delegated-review.md` | ☑ 已落地（见 S-13） |
| S-03 | P1 | 成书对账不查上游 info 是否漏进成书 | `delegated-review.md` | ☑ 已落地（见 S-13） |
| S-04 | P1 | `extra_subs` 之间的 `rn_overlap` 陷阱没警告 | `delegated-review.md` | ☑ 已落地 |
| S-05 | P1 | check 报告 36 KB，且"避免管道"那句挡住了过滤读法 | `workflow.md` | ☑ 已落地 |
| S-06 | P2 | `mark` 的成本在往返次数，没写批量做法 | `runtime-profiles.md` | ☑ 已落地 |
| S-07 | P1 | 上游改一个词 = 下游缓存全废，这笔账没写 | `recovery.md` | ☑ 已落地 |
| S-08 | P2 | 自动追检与 run 自身报告逐字重复 | `workflow.md` | ☑ 已落地 |
| S-09 | P2 | `--deep 1,2,3` 是写死默认，没写依据与覆盖条件 | `workflow.md` | ☑ 已落地 |
| S-10 | P0 | `inbox` 是八类，文档只写六类；完成定义漏两类 | `recovery.md` + `SKILL.md` | ☑ 已落地 |
| S-11 | P0 | `--check-only` 对 `.INVALID` 的行为分层不一致，stage4 无入口读 finding | `field-notes.md` | ☑ 已落地（根因已在代码修掉） |
| S-12 | P1 | preview 打出的 argv 没有 shell 引号，逐字执行必错 | `workflow.md` | ☑ 已落地（根因已在代码修掉） |
| S-13 | — | 语义审判层的成本分层裁决：`stage5.semantic` 下线、`stage6.semantic --advisory` 常开、不建 profile、`DRAFT_BAND` 放宽 | **已实施**（代码 + 契约 + 文档 + skill） | ☑ |

`scripts/validate_skill.py` 复盘当时运行 **PASS**，而上面 12 条它一条都测不出来——它查的是结构、
链接、渐进披露、节点顺序、经验格式与模板章节，**测不了"文档声称的行为与 CLI 实际行为是否一致"**。
S-01、S-10、S-11、S-12 正好是这一类。**文末那三条行为断言已经加进校验器**（用户裁决），
其中 flag 双向对账当场又捞出三条同形漏项（`--resample` / `--purge-role` / `--repair-surface-habits`
注册在案却全 skill 一次没提），已一并补进 `recovery.md`。

---

## S-01 · P0 · skill 整段漏掉 `--advisory`：语义层不是跑不了，是没人跑

### 现象

`stage5.semantic` 与 `stage6.semantic` 是 `PIPELINE_STEPS` 的第 12、14 个节点。
按 `workflow.md` 主链表给的主跑参数（两行都是"无"）执行，两次都是：

```text
✗ 显式终态（勿静默）：NOVEL_EVALUATOR_PROFILE 未配置；设置对应本地路径后才可启用自动语义裁决
RUN_EXITCODE=2
```

已确认 `$env:NOVEL_EVALUATOR_PROFILE` 为空、`.env` 无该键、盘上无任何标定 profile
（只有 `tools/build_evaluator_profile.py` 这个构建器）。

**但这两个节点各注册了一个 `--advisory` 付费 flag**，`contracts/invariants.py:810` 的登记原文：

> `--advisory`：未配置匹配 evaluator profile 时仍运行双盲判官；只产带证据的 UNVER，绝不自动 PASS/FAIL

`contracts/invariants.py:1155` 与 `:1172` 把它列进两个节点的 `flags`；
`contracts/schema/corpus.py:270-283` 更进一步——**语料生产命令在这两个节点上必须含
`--advisory`，否则契约直接拒收**。也就是说仓库的标准生产路径本来就是 advisory。

而 `--advisory` 在整个 `.claude/skills/novel-pipeline-operator/` 里**一次都没出现**
（grep `advisory` 命中 0）。主链表第 12、14 行的"主跑参数"列写的是"无"。

### 为什么是 P0

按现在的文档跑，每本书都会在这两个节点白丢一整层证据，而执行者不会意识到自己丢了东西：

- 文档给的参数是"无" ⇒ 必然 exit 2；
- `recovery.md` 把 exit 2 定义为"照原文补上游、参数或批准，不盲重试"，
  而终态原文只提 profile，不提 `--advisory` ⇒ 执行者按文档只能得出"这层跑不了"；
- 于是只剩两条错路：判成 `stopped` 停机（但成书其实跑得完，本次就跑完了），
  或去造一个 profile（`delegated-review.md`「代批权限不允许」明令禁止的同族行为）。

**真正的第三条路一直存在，只是文档没写。** 后果是 stage6 定稿的人工核验退化成
"agent 自己读了一遍"，而本可以垫着两名盲评判官的带证据结论——见 S-02。

⚠ 本条与"要不要建 evaluator profile"是两件事。profile 只买一样东西：把 `UNVER`
升级成 `automated=True` 的 PASS/FAIL。**没有 profile 不等于没有语义证据。**
全仓真正消费 ability 的只有三个点：`stage6.semantic`→`reference_boundary`、
`stage5.semantic`→`secret_timeline`、`stage5.write` 收缩臂→`trim_equivalence`；
其余五个 ability 无消费点，profile 里留 `n=0 disabled` 即合法。标定门槛与人力评估
另见 `contracts/schema/evaluation.py` 的 `_held_out_floor` 与 `threshold_passed`，
不在本文件展开。

### 落点与建议改法

**① `references/workflow.md` 主链表第 12、14 行**——把主跑参数从"无"改掉，这是主修：

```markdown
| 12 | `stage5.semantic` | `--advisory`（无标定 profile 时的现役主路径） | 双盲判官落盘，结论为带证据的 `UNVER`；`semantic_secret_leak` 由此获得可复核证据而非空缺 |
| 14 | `stage6.semantic` | `--advisory`（同上） | 每个去重文本都有两名盲评判官的带证据结论；仍不得当作自动 PASS/FAIL |
```

并在表下补一段：

```markdown
`stage5.semantic` / `stage6.semantic` 不带 `--advisory` 时，在未配置匹配 evaluator profile
的宿主上必然 exit 2 且**不发起任何模型调用**。`--advisory` 是注册在案的付费 flag，
用它仍运行双盲判官，只是结论恒为带证据的 `UNVER`、`automated=False`——
它不能替代标定，但它是"有证据"与"没有证据"的分界。两个节点都不接 `--check-only`。
```

**② `references/delegated-review.md`「stage6 代批」**——人工核验必须以判官证据为底：

```markdown
语义结论为 `UNVER` 时，先确认这份 `UNVER` 是**advisory 双盲判官产出的带证据结论**
还是**根本没有 semantic 产物**。两者在报告上长得像，处置完全不同：后者应先补跑
`stage6.semantic --advisory`，再据判官逐候选的证据做人工核验；
直接在"没有任何语义产物"的情况下写人工核验结论，等于把审美偏好写成客观通过。
```

**③ `references/field-notes.md`**——索引加一行，正文加一条：

```markdown
| FN-013 | 语义审判层：advisory 与标定的分界 | `stage5.semantic`, `stage6.semantic`, `--advisory`, `NOVEL_EVALUATOR_PROFILE`, exit 2 |
```

```markdown
### FN-013 · 语义审判层：advisory 与标定的分界

- 状态：`contract-confirmed`
- 触发：`stage5.semantic` 或 `stage6.semantic` 返回 exit 2，原文为
  `NOVEL_EVALUATOR_PROFILE 未配置；设置对应本地路径后才可启用自动语义裁决`。
- 判断：这是"没有标定 profile"，**不是"语义层不可用"**。区分两件事：
  能不能拿到判官证据（能，走 `--advisory`）；能不能自动盖章（不能，要 profile）。
  终态原文只说 profile 那一半，不要据此推断整层不可跑。
- 处置：**第一步就带 `--advisory` 重跑**，取得双盲判官的带证据 `UNVER`；
  再用它做 stage6 的 `actor + reason + 完整 subject SHA-256` 人工核验收口。
  不伪造 profile，不因缺 profile 停机，也不在零语义产物的情况下直接写核验结论。
- 验证：`stage5/semantic_<n>.json`、`stage6/semantic_<n>.json` 落盘且 fresh；
  产物的 `evaluator_fingerprint.dataset_sha256` 等于 advisory 哨兵值、
  全部 `decisions` 为 `automated=False` / `profile_id=null`（`_is_advisory_artifact` 的判别面）；
  终审包把它记为"advisory 证据 + 人工核验"，不记为机器绿灯。
- 依据：`contracts/invariants.py::PAID_FLAGS['--advisory']` 与两个 `NodeSpec.flags`；
  `contracts/schema/corpus.py` 对生产命令的强制要求；`novel/quality/profile.py::advisory_fingerprint`；
  `novel/stages/stage6/semantic.py::_is_advisory_artifact`。
```

⚠ 同步：`SKILL.md` 的「完成定义」现在没有把"某个主链节点结构性不可跑"排除在
`ready` 之外。缺标定 profile 的宿主上，即使跑了 advisory，两条 semantic HARD 仍是
`unver`，只能走 `ready_with_disclosures`——这一点与 S-10 的完成定义修订一起做。

---

## S-02 · P1 · 语义缺席时 stage6 候选筛选链塌成空

### 现象

`delegated-review.md`「stage6 代批」的筛选链第 4 步是：

```markdown
4. 在可定稿候选中优先选择 info 命中较少、质量排名较高者。
```

本次三个候选的机械读数**完全打平**：各 0 HARD、0 info、2 条 metric，总报告
`合计 ok 16 · 命中 0 · 够不着 6`。而"质量排名"（`hook_quality_rank`）要 `stage6.semantic`
才有——它 exit 2 了。于是第 4 步没有任何可用信号，筛选链直接落到第 5 步"写审美理由"。

文档在这里等于把定稿交给了纯审美，而「代批权限不允许」明写着
"把没有证据的审美偏好写成客观通过"。

⚠ **本次那个"没有质量排名"是 S-01 造成的，不是结构必然**：`hook_quality_rank` 与
`hook_candidate_contradiction` 由 `run_semantic` 产出，跟标定与否无关——跑了
`stage6.semantic --advisory` 就有匿名质量排序与逐候选矛盾清单。所以修复顺序是
**先修 S-01**（让排名真的存在），本条只负责"排名仍然并列 / 判官全体 UNVER"
这个残余工况；两条不可互相替代。

### 本次实际用的替代判据（可复制、有分母）

把每个候选的**每一条事实性断言回原文逐字检索**，统计无据意象密度：

| 候选 | 字数/段数 | 段均 | 事实断言回查 | 无据意象 |
|---|---|---|---|---|
| 1 `source_architecture` | 267 / 14 | 19.1 | 主体是原作文案的扩写，未新增兑现 | 「反手扣门」「面朝墙」原文 count=0 |
| 2 `payoff_preview` | 222 / 8 | 27.8 | 6/6 命中（「和他断掉」「我和你一起对他负责」为原文逐字） | 0 |
| 3 `scene_punchline` | 122 / 6 | 20.3 | 只给一个场面，穿书设定/女 alpha 前提/双男主收束全缺 | 「水龙头」「像女鬼」「踩在我命门上」原文 count=0（122 字里 3 处） |

原作文案基准：180 码点 / 7 段 = 25.7 字每段。候选 2 的 27.8 最贴。

### 落点与建议改法

`references/delegated-review.md`「stage6 代批」筛选链，把第 4 步改成两段式，
并补一段"语义缺席时"的替代判据：

```markdown
4. 在可定稿候选中优先选择 info 命中较少、质量排名较高者。

   已跑 `--advisory`（见 [经验库](field-notes.md) FN-013）后判官全体 `UNVER`、
   排名并列，或候选机械读数完全打平时，这一步没有可用信号，
   **不得直接跳到审美理由**。改用可复制的事实密度判据：
   把每个候选的每一条事实性断言回源文逐字检索，记录命中数/分母与无据意象条数，
   优先选择无据意象为零、事实密度最高者。检索结果与分母写进终审包。
```

---

## S-03 · P1 · 成书对账不查上游 info 是否漏进成书

### 现象

`delegated-review.md`「成书逐字对账」七项全是结构性的：体量与章序、批准文案前缀、
旧键归零、新旧混名、正式章来源、最终状态、指纹一致。

本次 stage2 报了 `event_early_name`（info）：

```text
· 2 处 event/summary 里写出了本章尚未指名的角色名：['ch5 「安和」(原文首现=ch8)', 'ch7 「安和」(原文首现=ch8)']
```

判据自述"下游堵不住 ⇒ 只能从这一层修"，但严重度是 info。**它到底有没有传导进成书，
七项对账一条都不查。** 我额外按章切分 `book.txt` 查了该角色新名的首现章：

```text
「苏宁」出现章: [12, 14]
```

未泄。这一步把它从"未知风险"降成"已核实无害"——而这一步现在不在文档里。

### 落点与建议改法

`references/delegated-review.md`「成书逐字对账」加第 8 项：

```markdown
8. **上游 info 的下游投影**：把上游全部"剧透 / 提前指名 / 后见之明"类 info finding
   逐条投影到成书章序核实（按章切分 `book.txt`，查涉事角色新名或涉事事实的首现章）。
   核实为未传导时写"已核实无害 + 首现章"；确已传导时回最上游正式修复入口，
   不在成书上做文本修补。
```

---

## S-04 · P1 · `extra_subs` 之间的 `rn_overlap` 陷阱没警告

### 现象

`delegated-review.md`「stage7 代批」第 4 条现在是：

```markdown
4. 单字别名不要全局替换。把不改的单字明确登记为 `old == new`，用长度至少 2 且有上下文的 `extra_subs` 处理真实昵称。
```

按这条做了，第一次 `--apply-edit` 仍被驳回：

```text
[!!] rn_overlap（hard · mechanical/human）人工补充的映射之间没有跨界重叠
     · 人工补充的「安安景景」与「景，你说我」首尾重叠 '景'——单次扫描会切出半截残串
APPLY_EXITCODE=1
```

判据只查人工补充那一侧（registry 来的键都是完整称呼，不会有这个形状），
所以这是**只有走 `extra_subs` 才会踩到的坑**，而 `extra_subs` 正是这一条推荐的做法。
代价是白跑一轮 `--apply-edit`——它会开新的人工修订轮并把上一轮归档进 `_rounds/`。

解法是给键左扩一个边界字符：`景，你说我` → `「景，你说我`，重跑即 exit 0。

### 落点与建议改法

`references/delegated-review.md`「stage7 代批」第 4 条补半句：

```markdown
4. 单字别名不要全局替换。把不改的单字明确登记为 `old == new`，用长度至少 2 且有上下文的
   `extra_subs` 处理真实昵称。多条 `extra_subs` 之间不得有"a 的真后缀 == b 的真前缀"
   （`rn_overlap`，单次扫描会切出半截残串）；给其中一个键左扩或右扩一个边界字符
   （引号、标点）即可解开。
```

---

## S-05 · P1 · check 报告 36 KB，且"避免管道"那句挡住了过滤读法

### 现象

`stage2.blueprint --check-only` 一次输出实测 **35.9 KB**（71 条判据，每条都印完整设计说明
与历史盘面基线），耗时 1.75 秒。这一份在 stage2 至少要读三次：首跑追检、`--revalidate`、
提升后复检。全链算下来是几十万字符，其中与本次判定相关的只有命中行。

对 agent 执行者，这是实打实的 token 成本与注意力稀释——本次读完整报告两次之后就改成了
重定向 + 过滤，**36 KB 压到 5 行**，判定信息一条不少：

```powershell
& $PY -X utf8 -u -m novel.cli run <node> $S --check-only --base $B > "$SP\out.txt"
$rc=$LASTEXITCODE
Get-Content "$SP\out.txt" -Encoding UTF8 | Select-String -Pattern '^\s*\[!!\]|^\s*\[\?\?\]|^合计|→\s+(PASS|AMBER|FAIL)|revalidated'
```

### 前置矛盾

`workflow.md`「命令协议」现在写的是：

```markdown
2. 直接运行命令，避免通过管道消费输出；保存真实子进程退出码。
```

本意是防"管道覆盖退出码"，但字面读起来像"不许过滤输出"——正好挡住上面这条优化。
文件重定向 `>` 不影响 `$LASTEXITCODE`，与管道不是一回事，文档应当把两者分开说。

### 落点与建议改法

`references/workflow.md`「命令协议」第 2 条改写，并在「结果判定」补一段读法：

```markdown
2. 直接运行命令并保存真实子进程退出码。**不要让管道消费退出码**；
   把 stdout 重定向到文件、取到退出码之后再过滤该文件是允许的，也是长报告的推荐读法。
```

```markdown
check 报告按设计逐条印出判据的设计说明与历史基线（单份可达 36 KB），
其中与本次判定相关的只有命中行。**先按 `[!!]`（命中）、`[??]`（够不着）、`合计` 与
badge 行过滤，只在需要展开某条命中时读全文**。过滤不改变任何判定，只改变读法；
分母、freshness 与命中数仍以报告原文为准。
```

---

## S-06 · P2 · `mark` 的成本在往返次数，没写批量做法

### 现象

实测单次 `mark` 约 1.1 秒（Python 冷启动）。全链 28 条实际命令 × 2 次打点 ≈ 62 秒，
相对 886 秒付费调用不算大。

**真正的成本是往返**：`SKILL.md` 主循环第 5 条与 `workflow.md`「命令协议」都要求
"对每条实际命令，包括自动追跑的 check 与修复命令"打 start/end 点。执行者若老实地把
mark / run / mark 拆成三次工具调用，全链就是 **84 次往返**——每次往返都是一轮上下文与延迟。
本次是把三条串进同一次 shell 调用跑的，往返数回到 28。

### 落点与建议改法

`references/runtime-profiles.md`「共同规则」加一条：

```markdown
- `mark start` / 真跑 / `mark end` 应在**同一次 shell 调用内串联**，取到真实退出码后再打
  end 点；不要拆成三次独立往返。打点本身约 1 秒一次，成本在往返次数不在耗时。
```

---

## S-07 · P1 · 上游改一个词 = 下游缓存全废，这笔账没写

### 现象

本次 stage2 + stage3 的 **38 次调用全部命中 `.llm_cache`**（该书此前跑过），
`--full-extract` 5 秒跑完 18 章蓝图 + 全书抽取 + 文风画像。

随后我为修 `present_names_invented` 改了 blueprint 里一个字段的一个词
（`负责为演员化妆的 omega` → `负责为演员化妆的是 omega`），`stage4.plan` 的 prompt 随之变化、
缓存键失效，**实付一次 233 秒 / prompt 46758 + completion 20567 + reasoning 13032 tokens 的调用**。
它是全链唯一因修复而重新付费的节点。

`recovery.md`「付费修复与缓存」现在只从反面说了"不为确保重跑修改参数、删缓存"，
**没有从正面说"上游正式产物的任何变更都会让下游全部缓存失效"**。

### 落点与建议改法

`references/recovery.md`「付费修复与缓存」补一条成本纪律：

```markdown
6. 上游正式产物一旦变更（含 `--revalidate` 提升、`--apply-edit` 新轮），
   所有以它为输入的下游节点缓存键随之失效，下一次运行是**实付**而非回放。
   选择修复层前先估算下游重算面：同一问题若在下游有等价的正式修复入口，优先在下游修；
   只能在上游修时（如 `present_names_invented` 只有 stage2 有入口），在终审包里
   把这笔重算记成修复的直接代价，不要读成重复付费。
```

---

## S-08 · P2 · 自动追检与 run 自身报告逐字重复

### 现象

`stage1.partition` 的真跑打印了一份完整 check 报告，preview 追加的 `--check-only`
又打印了一份**逐字相同**的报告（8 条判据全 OK，两份 `合计 ok 8 · 命中 0 · 够不着 0 · 没跑成 0`）。

追检的设计意义是 preview 自己写的那句"⚠ 自动追跑：退出码 0 也可能是残缺产物"——
它守的是**产物完整性**，不是让人把判据再读一遍。

### 落点与建议改法

`references/workflow.md`「命令协议」在"执行 preview 输出的全部步骤"那段后面补一句：

```markdown
真跑已打印同一份 fresh 报告时，追检的价值是复核产物完整性，不是重读判据：
仍必须执行并打点，但只取它的退出码与合计行即可，不必重读全文。
```

（与 S-05 的过滤读法是同一条纪律的两面，可以合并成一段。）

---

## S-09 · P2 · `--deep 1,2,3` 是写死默认，没写依据与覆盖条件

### 现象

`workflow.md` 主链表第 10 行：

```markdown
| 10 | `stage5.write` | `--deep 1,2,3 --shallow-rest`；不足三章时截到 `N` | ... |
```

没有写"为什么是前三章"，也没有写"什么时候该调整"。本次 18 章书按 3 深 15 浅跑，
`stage5.write` 实付 19 次调用（18 章 + 1 次内部重试/收缩）、430 秒，是全链最大的一笔。
deep 与 shallow 的单章成本不同，这是一个有成本含义的默认值，而
`SKILL.md`「接口契约」的"可选约束"里写了用户可以指定"章节深浅分配"——两处没有连起来。

### 落点与建议改法

`references/workflow.md` 主链表第 10 行的"主跑参数"列脚注，或紧随其后的一段：

```markdown
`--deep 1,2,3` 的依据是开篇三章决定读者留存，值得更高的单章成本；其余章走 shallow。
用户在可选约束里指定了章节深浅分配时以用户为准，并在终审包记录实际分配与依据。
不要为"提高质量"自行扩大 deep 集合——那是未经授权的成本变更。
```

---

## S-10 · P0 · `inbox` 是八类，文档只写六类；完成定义漏两类

### 现象

CLI 实际输出八类：

```text
── 待人工 ── 共 127 项（invalid 0 · rejected 0 · stalled 0 · repairable 0 · human_only 26 · verification 101 · upstream 0 · approval 0）
```

而 `recovery.md` 的「`inbox` 六类的读法」只有 `invalid` / `rejected` / `stalled` /
`human_only` / `upstream` / `approval`——**缺 `repairable` 与 `verification`**。

`SKILL.md`「完成定义」同样只说：

```markdown
- `inbox` 的 `invalid`、`rejected`、`stalled`、`upstream`、`approval` 均为零；`human_only` 已逐类审阅并记录。
```

### 为什么是 P0

本次跑出 **`verification` 101 条**（stage2 的 `address_context` 等逐项人工复核余额）。
文档既没说它算不算阻断、也没说要不要披露——执行者只能自己猜。猜"不算"就会把一本
带 101 条未收口余额的书标成 `ready`，而完成定义的字面允许这么做。这正是
"把没验过的说成验过"。

### 落点与建议改法

`references/recovery.md`：标题改为「`inbox` 八类的读法」，补两条：

```markdown
- `repairable`：有确定性修复入口的失败，按 finding 的 `fix` 收敛。
- `verification`：待人工复核的决策项（如 stage2 `address_context`），按
  `actor + 理由 + subject_sha256` 提交 PASS/FAIL/UNVER；hash 变更后旧决策会被拒绝。
  它不阻断成书，但**必须在终审包按类计数披露**，不得并进 `human_only` 或算作已解决。
```

`SKILL.md`「完成定义」对应改为：

```markdown
- `inbox` 的 `invalid`、`rejected`、`stalled`、`repairable`、`upstream`、`approval` 均为零；
  `human_only` 与 `verification` 已逐类审阅并按类计数记录。
```

---

## S-11 · P0 · `--check-only` 对 `.INVALID` 的行为分层不一致，stage4 无入口读 finding

### 现象

同一个 flag，三层三种行为：

| 层 | 落盘闸拦下后 `--check-only` 的行为 | 文档现状 |
|---|---|---|
| stage2.blueprint | **会**判活动 `.INVALID`，报告顶部写「⚠ 判的这一份是 .INVALID 稿」，命中行完整 | 无记载 |
| stage6.hook | 只读正式产物；候选级 finding 可从 `hook.INVALID.json` 的 `candidates[].findings` 读 | FN-008 有写 |
| stage4.plan | **直接 exit 2**：`stage4/plan_chapters.json 不在盘上——先跑 stage4.plan`；而 `plan_chapters.INVALID.json` 里 `findings` 字段不存在 | 无记载 |

本次 stage4 撞 `goal_landing` 之后：

```text
✗ 显式终态（勿静默）：1 条阻断级判据命中：['goal_landing']。问题稿已存 `stage4/plan_chapters.INVALID.json` …
RUN_EXITCODE=2
→ stage4.plan --check-only
✗ 显式终态（勿静默）：stage4/plan_chapters.json 不在盘上——先跑 stage4.plan
CHECK_EXITCODE=2
→ inbox
[invalid] stage4  stage4.plan
    stage4/plan_chapters.INVALID.json：首跑被落盘闸门拦下，还没有现役产物
```

**终态只给了 rule id，没给"错在哪几条"，而全链没有任何只读入口能拿到它。**
我最后是读 `novel/stages/stage4/checks.py:261-279` 自己复算判据，才定位到
out5.end→`b05_11`、out12.open→`b12_05`、out12.end→`b12_06` 这 3/36 条。
**这是本次最贵的一段绕路**，而 `recovery.md`「入口原则」写的是
"先读取当前失败报告或终态原文，再选择修复"——在 stage4 这一层，"失败报告"根本不存在。

### 落点与建议改法

`references/field-notes.md` 加一条（同 S-01 一起做，编号顺延）：

```markdown
### FN-014 · 落盘闸拦下后，finding 细节的读法逐层不同

- 状态：`observed`
- 触发：任一层被落盘闸拦下返回 exit 2，需要知道"错在哪几条"而不只是 rule id。
- 判断：`--check-only` 的取值面逐层不同，不要按单一心智模型推断。
  stage2.blueprint 会判活动 `.INVALID`（报告顶部标注）；stage6 只读正式产物，
  候选级 finding 在 `hook.INVALID.json` 的 `candidates[].findings` 里；
  stage4.plan 的 `--check-only` 在没有正式产物时直接 exit 2，且 `.INVALID` 不带 findings。
- 处置：先试 `--check-only`；它 exit 2 且 `.INVALID` 无 findings 时，
  按该层 `checks.py` 里对应 rule 的判据自行在 `.INVALID` 上复算，取得逐条证据后再修。
  不要因为读不到细节就盲改或停机。
- 验证：修复后 `--revalidate` 打印的完整报告里该 rule 归零，且 `state` 判该层完成。
- 依据：本次三层实测行为差异；`novel/stages/stage4/checks.py` 的 `goal_landing` 谓词。
```

### ☑ 实际落地：根因已在代码修掉，经验库那条直接写成 current contract

选的是第二条路——**让 `stage4.plan --check-only` 在正式稿不在盘上时判活动 `.INVALID`**
（`novel/stages/stage4/plan.py::run_check_only`），照 `novel/stages/stage3/style.py:76-79`
早就有的成例：`judged = REL if store.exists(REL) else _INVALID`，两份都不在才抛。
报告第一节因此打出「⚠ 判的这一份是 .INVALID 稿」，命中行完整。

没有选"给 `.INVALID` 加 findings"：那是契约级变更（stage4 schema 无 `findings` 字段，
要动迁移链与 schema 文档），而报告路给出的信息本来就比一个 findings 数组多。
两份并存（修复稿被驳回）时仍判正式稿——那才是下游在消费的那一份。

连带效果：`novel/cli/main.py::_state_check_report` 不再吞掉 stage4 的 `UpstreamMissing`，
`state` 会顺带显示这份问题稿的报告，与 stage3 今天的行为一致。
测试：`tests/test_stage4.py::test_check_only_reads_the_blocked_draft_when_no_formal_plan_exists`
与 `..._still_refuses_when_neither_draft_is_on_disk`。

⇒ 经验库那条不再是止血条，直接按维护协议第 6 步写成 current contract（**FN-015**，
状态 `contract-confirmed`），内容是"`--check-only` 面对 `.INVALID` 的取值面逐层不同"这件事本身。

---

## S-12 · P1 · preview 打出的 argv 没有 shell 引号，逐字执行必错

### 现象

```text
> preview stage2.blueprint <SID> --revalidate --actor agent-operator --reason "fix present_names_invented"

[1] stage2 逐章抽取 --actor --reason --revalidate
    $ …\python.exe -X utf8 -u -m novel.cli run stage2.blueprint 2049864442305892545 --actor agent-operator --reason fix present_names_invented --revalidate --base …
```

`--reason` 的值裸着三个词。而 `SKILL.md` 主循环第 4 条要求：

```markdown
4. 对当前节点运行 `preview`，逐字执行它给出的全部步骤，包括自动追加的复检；不要凭文档手拼真实 argv。
```

**逐字复制这条命令在任何 shell 里都会解析错**（`present_names_invented` 会变成位置参数）。
文档的"逐字执行"与 preview 的输出形式在这里直接冲突。

### 落点与建议改法

`references/workflow.md`「命令协议」补一句：

```markdown
`preview` 输出的是**词元序列而非 shell 字符串**：含空格的参数值（`--reason`、`--actor`）
不带引号。执行时必须自行加引号——这不算"手拼 argv"，节点、flag 与取值仍完全照抄 preview。
```

### ☑ 实际落地：根因已在代码修掉，文档那句反过来写

没有在 skill 里写"执行时必须自行加引号"，而是把 preview 渲染修对——因为
`build_argv` 上方的注释本来就写着「摊开显示的那条命令**就是会跑的那条**，粘进终端也不会再问一遍」，
在含空格的取值上它此前不成立。

- 新增 `novel/orchestrator/nodes.py::shell_line(argv)`：单引号包裹、内部单引号成对翻倍，
  引法与前端 `web/src/api/endpoints.ts::quoteArg` **逐字节一致**（跨语言共用不了函数，只能钉死规则）。
  字符类写成显式 ASCII 而不是 `\w`——Python 的 `\w` 认 Unicode、JS 的不认，用 `\w` 两边会在中文取值上分家。
- 替换**全部五个**人读落点，不只 preview：`_cmd_preview`、`queue.confirm`（人靠它确认参数没点错）、
  `queue.frozen_resume_commands`（续跑前必须摊开的命令）、`jobs.py` 与 `agent.py` 的 `$ ` 日志行
  （日志的价值就是能原样重放）。HTTP 契约不动：`argv` 仍是 `list[str]`，前端继续自己引。
- ⚠ 已知边界：取值**内部**含单引号时两种 shell 无法同时满足（PowerShell 用 `''`、POSIX 用 `'\''`），
  这里按 PowerShell 写并在 docstring 里点名。其余形状——含空格、反斜杠路径、中文、`&`/`|`/`;`——两边都对。
- 测试：`tests/test_cli.py::test_preview_quotes_values_that_a_shell_would_otherwise_split`
  断言的是 **POSIX `shlex` 往返**（拆回来等于原 argv），不是实现长什么样。

⇒ skill 里那句因此写成正向契约："preview 打印的命令已按单引号规则转义，可逐字复制"。

---

## S-13 · 语义审判层的成本分层裁决（**已实施**）

补跑 advisory 之后拿到了 S-01 缺的那半边数据，用户据此在 2026-08-17 做了一组连带裁决。
本节记录**决定、依据与实际落点**；代码、契约、测试、文档、skill 均已同步，
默认测试门与 `validate_skill.py` 全绿。

### 触发这组裁决的两个读数

**① 两个 advisory 节点的性价比差 14 倍**（同一本书，同一次跑）：

| 节点 | 实付调用 | 耗时 | completion+reasoning | 占全书 | 产出 |
|---|---:|---:|---:|---:|---|
| `stage6.semantic` | **2** | 148 秒 | 18,887 | **1.7%** | 两名盲评判官一致把首轮定稿排到最末，**直接改变了交付物** |
| `stage5.semantic` | **28** | 646 秒 | 595,351 | **55%** | 18 章里 5 章命题抽取 `uncertain`，8 条 secret 全部判不动 |

**② profile 不降低每本书的成本，一分钱都不降。** 校准路与 advisory 调的是**同样两名盲判官**
（`novel/stages/stage6/semantic.py` 的 gate 在 `build_prompt` 之前，
`stage5/write.py::_judge_trim_candidate` 同形）——`require_enabled` 只决定结论允不允许标
`automated=true`。⇒ "advisory 太贵所以去建 profile"是不成立的推理：真要省钱，答案是**不跑那个节点**。

### 裁决与落点

| 裁决 | 依据 | 落点（均已实施） |
|---|---|---|
| **`stage5.semantic` 整节点删除** | 本项目重构**不跨章重组**（实测 18/18 章 `from_source_ordinals` 恒等），书级泄底主风险面在计划形态上几乎不存在；而它占实付调用 54%、输出 token 55%，结论却是"判不动" | `NodeSpec` / `PIPELINE_STEPS`（18→17）· `artifacts.py` · `schema/__init__.py` · `schema/stage5.py` 的 `SemanticReview` 一族 · `schema/corpus.py` · `judges/stage5.py` 两条判据 · `novel/stages/stage5/semantic.py`（删文件）· `review.py` / `write.py` / `cli/main.py` / `consumption.py` · 12 个测试文件 · `docs/schema/stage5.md` §三 · `BACKEND_CONTRACT.md` R-7.18b/c 转授 `R-3.7d` |
| **`stage6.semantic --advisory` 写进主链常开** | 2 次调用 / 1.7% token，实测直接纠正过一次定稿 | `workflow.md` 主链表第 13 行 + 表下专段 |
| **不建 evaluator profile** | 它只买"无人值守"，而用户每本都会读终审包；标定需 ≥3 run × ≥100 例 × 2 判官/能力，且改一次 prompt 就重来 | 无代码改动；`FN-013` 写明 advisory 与标定的分界 |
| **`DRAFT_BAND` 0.90–1.10 → 0.80–1.20** | 盘上 31 章两端极值 **0.814 / 1.206** 是这一层的自然方差；放宽后正常波动不再触发收缩臂与 `length_over`/`length_under` | `invariants.py`（`BOOK_BAND` **刻意不跟随**，保持 0.90–1.10）· `schema/stage5.py` 注释④ · `judges/stage5.py` 两处 note · `test_stage5_measure.py` · `BACKEND_CONTRACT.md` R-7.8 · `docs/schema/stage5.md` |

### 书级剧透防线现在长这样（全部 0 成本）

1. **标注层**：stage2 的 `event_early_name` / `present_early_named` / `present_names_late`（`R-3.7d`）——确定性，不存在"模型没把握"这一态；
2. **交付前**：成书逐字对账第 8 项「上游 info 的下游投影」（S-03）。

⚠ **这两条守的是"提前指名"，不是"语义等价地点破"**——后者现在确实没人查，已在
`docs/schema/stage5.md` §十第 1 条如实登记为"查不了"。**恢复条件**：计划开始出现跨章重组时
（`from_source_ordinals` 不再恒等），第 1 条的覆盖面就变了，需要重新裁决。

### 顺带查清的一件事：超限章早就有 0 成本销账路

`trim_equivalence` 的 profile 缺失会让整条收缩臂不跑（`_trim_to_fit` 在构造请求前就退出），
本次实测代价是 **6 章超上限原样交付**（ch2 4.0% · ch4 3.5% · ch7 4.3% · ch14 3.1% · ch16 3.4% · ch18 0.0%）。
但止损路一直都在，只是 skill 没写：

- `stage5.review --retain-pretrim --chapters <N> --actor <id> --reason <text>`——0 次 LLM，hash-bound 销账；
- `stage5.write --apply-edit <绝对路径>`——0 次 LLM，提交该章完整 `ChapterResponse`。

已补进 `recovery.md` 的允许写入面与 `FN-014`。⚠ 这仍是**设计耦合过紧**的一处：
把"篇幅上限"绑死在"语义标定"上、中间没有档位。`DRAFT_BAND` 放宽后触发面大幅收窄，
但脱钩本身是代码需求，不在本文件范围。

### 未收口（本次不做，如实登记）

| 事项 | 现状 | 为什么不做 |
|---|---|---|
| `web/src/fixtures/backend_v1/` 仍含 `stage5.semantic`（enums 7 处、check_stage5 4 处）与 `mock.ts` 节点表 | 前端 mock 仍会列出已删除的节点 | 它是**抓包证据**，按 `BACKEND_CONTRACT.md:1349` 的政策"随新版本**新增目录**"，不该手改字节。需要跑一次新后端重新抓 `backend_v2` |
| `docs/WORKFLOW_REVIEW_20260816.md` 6 处 | 保留原样 | 它是带日期的历史复盘快照，改它等于篡改当时的观察 |

---

## 附：`validate_skill.py` 的"行为断言"检查 —— ☑ 已实施

S-01、S-10、S-11、S-12 有一个共性：**文档声称的 CLI 行为与实际不符，而现有校验器结构上测不到**。
改之前的校验器只对账 `PIPELINE_STEPS`（节点顺序），不对账"文档提到的 flag、退出码、
inbox 分类是否与代码一致"。

三条断言已加进 `_behaviour_errors()`，**全部从代码反查，校验器里不手抄第二份清单**：

1. 从 `INBOX_KIND` 反查 `recovery.md` 的分类列表**与 `SKILL.md`「完成定义」**，缺项即失败——钉住 S-10；
2. flag **双向**对账，S-01 正是被单向检查漏掉的那一半：
   - 文档里出现而注册表（`NODES` / `FREE_ENTRIES` / `PAID_FLAGS` / `VALUED_FLAGS` /
     ast 解析出的 CLI 选项）没有的 `--xxx` ⇒ 失败（写错了）；
   - **`PAID_FLAGS` 里登记、且挂在某个节点上、而整个 skill 目录一次都没提的 ⇒ 失败**。
     ⚠ 判据落在"全 skill 出现过"而不是复盘稿原议的"主链表出现过"：`--revise` 这类
     修复路 flag 本来就该住在 `recovery.md`，钉在主链表上会造假红；
3. 从 `novel/cli/exit_codes.py` 反查 `workflow.md`「结果判定」表的退出码集合。

**第 2 条一上线就捞出三条与 S-01 同形的漏项**：`--resample`、`--purge-role`、
`--repair-surface-habits` 三个付费 flag 注册在案、挂在 `stage2.global` / `stage3.style` 上，
而整个 skill 一次没提；已补进 `recovery.md`（前者进「付费修复与缓存」第 4 条，
后两者进 `llm` remediation 那张定向返工表）。

S-11 那种"同一 flag 逐层行为不同"仍没法静态测，留在经验库（FN-015），
这一点也写进了 `maintenance.md` 的校验小节。

---

## 附二：修上面两条时撞出来的一条退出码误分类 —— ☑ 已修

跑语料验收时 `tests/test_cli.py::test_every_node_is_a_terminal_state_not_a_stack` 红：
`stage4.plan` 落 **exit 3（结构性拒绝＝代码自相矛盾）**。

**它不是任何一次改动引入的回归**，是一条只在「`.env` 在场 + `.llm_cache` 是热的」时才显形的旧问题
——那正是语料验收该有的配置。机制：该测试按**字母序**遍历节点，于是
`stage2.blueprint`（#6）跑在 `stage2.global`（#5）**前面**，与真实主链顺序相反；
blueprint 先落盘并记下指向当时 `global_grounding.json` 的上游边，global 随后重写那份产物
⇒ blueprint 过期 ⇒ `stage4.plan` 读它时被 `store._refuse_stale_upstream` 拒绝。

⚠ 定位过程里有一次**无效的基线对照**值得记下来：拿 `git worktree` 拉 HEAD 做对比时，
worktree 里没有 `.env` 也没有 `.llm_cache`（两者都不进 git），于是 HEAD 那边三个付费节点
全停在 exit 2「没有 API key」，`chapter_blueprints.json` 根本没被创建 ⇒ 两次跑的不是同一条路径，
而对照结果看起来像「HEAD 是绿的」。**worktree 基线只对纯代码差异有效**；一旦被测路径依赖
未纳入版本控制的环境（密钥、缓存、语料），必须先把这些补齐，否则结论是反的。
⚠⚠ 而补 `.env` 进无缓存的 worktree 又是另一个坑：缓存必然 miss ⇒ **真发付费请求**。
这次是掐掉之后靠账本与 `.llm_cache` 修改时间确认了零实付。

**真正的缺陷是分类**：`novel/cli/exit_codes.py` 自述「2 = 数据/流程说这一步不该继续，不是 bug」
「3 = 代码自相矛盾，是 bug，栈要留全」，而「上游已过期」这条拒绝**自带恢复命令**
（「请先从 `<producer>` 重建当前根上的产物」）——人照着做就能解开，它是 2 不是 3。
落进 3 的实际代价：编排层与 agent 执行者按 `recovery.md`「停止条件」的
「exit 3 ⇒ 停机并保留栈」处理，一次本可自愈的上游过期被当成代码 bug 停在那里。

修法（**窄口径，不动 `ProvenanceRefused` 的其余含义**）：

- 新增 `novel/errors.py::UpstreamStale(TerminalState)`，与 `UpstreamMissing` 分开
  （「还没跑」去跑它 vs「跑过但过期」去**重跑**它，且下游缓存会失效）；
- `novel/store.py::_refuse_stale_upstream` 改抛它；
- `novel/api/errors.py` 的 `STATUS` 登记 `UpstreamStale: 409`
  （**不是 404**：那一份在盘上，只是不再是当前根上那一版；与 `ArtifactUnreadable` 同码同因）；
- `ProvenanceRefused` 留给真正自相矛盾的三处：同一把刻度结算两次、缺全链根、根 sha1 分叉。

三处断言随之从 `ProvenanceRefused` 改判 `UpstreamStale`
（`test_root_clean_provenance` / `test_stage2_generation` / `test_stage2_secret_edit`）。
⚠ 还有一处**生产代码**的捕获面必须跟着走，而它是靠全量回归捞出来的，不是靠 grep 想到的：
`novel/stages/stage5/trim_resolution.py` 有三处 `except (ArtifactUnreadable, ProvenanceRefused,
UpstreamMissing)`，其中 `ProvenanceRefused` 站的正是 `read_own(require_current=True)` 的过期位。
拆类之后那三处不再接得住 ⇒ `test_trim_resolution` 当场红。已补上 `UpstreamStale`。
**换异常类型时，「谁在 catch 它」和「谁在 raise 它」一样要数全。**
没有改测试的遍历顺序：顺序不是缺陷，**把可恢复前置条件报成 bug 才是**；分类修对之后，
无论按什么顺序跑，这条测试都不再红。

---

## 复现指针

- 本次输出根：`E:\practice_group\Novel_rewriting\outputs\e2e_20260817_alpha`
- 书目录：`…\女_Alpha_的军校生活_2049864442305892545`
- 成书：`…\stage7\book.txt`（31759 字 / 18 章，sha1 `7ca22960a2f920d92e99ad5181dd98f1c993bd16`）
- 终审包（全部读数、代批记录、修复记录、账本）：`…\final_review_2049864442305892545.md`
- 退出码序列：见终审包第 7 节
- 账本：`…\_service\ledger.jsonl`（本 sid 60 条，全部 `outcome == ok`，模型全部 `deepseek-v4-pro`）
