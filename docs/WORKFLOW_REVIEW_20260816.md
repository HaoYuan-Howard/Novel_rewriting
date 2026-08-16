# 工作流设计复盘 · 2026-08-16

从一次完整跑书（`前任赘夫逃跑后_2022040243025528636`，19 章，`outputs/e2e_20260816`）里长出来的
设计意见。**每一条都带这次跑出来的读数或代码位置**；没有证据的猜测不写进来。

结论先说：**主链的骨架是好的**——落盘闸门 + 0 成本提升路、`unver` 与「查过没问题」分家、
分母必报、来源账逐边记、字节形态拒收 `\r`，这几条在这次跑书里都真的挡住了东西。
问题集中在**三类接缝**上：

1. **写盘方与产地方可以不是同一个节点，而没有任何东西守这条缝**（本次唯一一个真的会断主链的 bug）；
2. **同一个前置条件缺失，在三个调用点有三种行为**（profile 那件事）；
3. **报告说的是"多少"，人要修的是"哪一段"**——判据对了，可操作性没跟上。

分级口径：`P0` = 会让主链断或让错误静默通过；`P1` = 会让人做无用功或做错判断；`P2` = 摩擦。

---

## 〇、本次已经改掉的（先说清楚基线）

| # | 位置 | 问题 | 处置 |
|---:|---|---|---|
| 1 | `novel/stages/stage4/plan.py` | `stage4.plan` 同批重建的 `writer_context.json` 当场判 `stale/consumed_changed`，`stage5.write` 读它会被 `_refuse_stale_upstream` 拒绝 —— **主链在这里断** | 新增 `CTX_NODE` / `context_ctx()`，`run_context` 恒在 `stage4.context` 名下开 ctx |
| 2 | `novel/stages/stage7/checks.py` | `rn_kinship_touched` 把 `周子白` 的名 `子白` 判成关系词（`子` 在 `_STEM_KINSHIP` 里） | 新增 `given_name_halves()`，按 `(旧,新)` **配对**豁免 |
| 3 | `novel/stages/stage7/checks.py` | `rn_surname_split` 把只由别名承载的 `陈→林` 报成幽灵映射 | orphan 判定加一路：别名也算「用过这个姓」；恒等别名不算 |
| 4 | `tests/sandbox.py` 等 7 个测试文件 | 沙箱不带全链根 `stage0/clean_text.txt`，语料门整批红在同一句话上 | `Sandbox` 恒带全链根；另修 4 处过拟合断言 |

改完后：默认门 **1511/1511 全绿**；语料门 51 红 → **20 红**（剩下的全是过拟合到已删语料的断言）。

---

## 一、P0 · 写盘没有后置条件：一份产物可以「刚落盘就已经过期」

### 现象

`stage4.plan` 跑完退出码 0、报告 PASS 24/24，而 `state` 立刻说
`stage4/writer_context.json  stale/consumed_changed`。

### 根因

`consumed` 投影按 `(节点, 上游)` 登记（`novel/consumption.py::CONSUMED_PROJECTIONS`）：

- 写的一侧，`ArtifactStore.read` 用 **`self.ctx.node`** 取投影；
- 判新鲜度的一侧，`ArtifactStore.freshness` 用 **`producer_node(rel)`** 取投影。

只要某个节点顺手写了**别人产的**产物，这两个 node 就不是同一个，来源账必然对不上。
`stage4.plan` 内部调 `run_context(ctx)` 正是这一形。
`stage2.global --full-extract` 在 CLI 层做对了（给 `stage2.blueprint` / `stage3.style`
各开一个 `RunContext`），但那是**调用点的自觉**，不是机制。

### 建议

**给 `store.write` 加一条后置条件：写完立刻自判新鲜度，非 `fresh` 就 `ProvenanceRefused`。**

```
落盘 → freshness(rel) → 不是 fresh ⇒ 拒绝并回滚
```

理由与本仓已有的「落盘闸门」同构：一份**自己都判自己过期**的产物，唯一的作用是把 bug
埋到下一层，而下一层报出来的是「上游过期」，看起来像数据问题。
`freshness` 是纯重算、0 成本、无副作用，代价只有一次投影。

⚠ 例外面要显式登记：`stage0/6/7.approve` 写的是**继承来的**来源账，它们照理也应当 fresh
（本次实测确实 fresh），但要先跑一遍全语料确认，再决定是白名单还是无例外。

### 收益

这一条能把「A1 这一类 bug」从「跑完一整层付费节点之后才发现」提前到「写盘那一瞬间」，
而且对所有层一次性生效。

---

## 二、P0 · 同一个前置条件缺失，三个调用点三种行为

### 现象

`NOVEL_EVALUATOR_PROFILE` 未配置时：

| 调用点 | 行为 | 代码位置 |
|---|---|---|
| `stage5.write` 收缩臂 | **catch 掉**，写一份 `TrimAudit(semantic=None, adopted=False)`，转 `verification`，注释原话「a normal human-only outcome」 | `write.py:594-610` |
| `stage5.semantic` | **raise**，CLI exit 2 显式终态，整层无产出 | `semantic.py:438` |
| `stage6.semantic` | **raise**，CLI exit 2 显式终态，整层无产出 | `stage6/semantic.py:155` |

而**决策层本来就支持降级**：`double_blind_decision(profile=None)` 返回
`verdict='UNVER', automated=False`，并且**照样带着两名 judge 的全部证据 span**
（`novel/quality/evaluator.py:84-88`）——它不抛。

### 判断

「缺 profile」到底是**正常的人工-only 结果**还是**显式终态**，本仓内部没有统一答案。
`stage5.write` 那条注释是对的；两个 semantic 节点与它矛盾。

### 建议

统一到 `stage5.write` 的口径（详见第七章）。这条不改任何判据严重度，只是把**已经存在的
降级路**接到节点入口。

---

## 三、P1 · 报告说「多少」，人要修「哪一段」

### 现象

`hook_reuses_source` 报：`[source_architecture] 与原作文案段的 20 字窗口重合率 9.6%（上限 0%）`。

**9.6% 不是一片弥散的相似**。逐窗口复算（`novel/core/text.py::window_overlap`，窗口 20、
`norm` 空间）：136 个窗口里命中 13 个，而这 13 个是**同一段连续照抄**的滑窗：

```
我哭湿了十几条帕子后娘看得愁眉不展她又领回来个皮白腰细的新夫郎冷
```

即模型原样搬了**连续 32 个内容字**（两句半）。另两条候选 `payoff_preview` /
`scene_punchline` 的重合率都是 **0.0** —— 阈值本身没问题，能做到。

### 判断

**判据是对的，报告的粒度错了。** 人拿到「9.6%」不知道该改哪儿，只能自己去 diff；
拿到那 32 个字就是一句话的事。

而且本仓**已经有**现成的函数：`novel/core/spans.py::lcs()`（最长公共**子串** → 起点+长度），
stage5 的 `must_appear_missing` 用的就是它，注释里还写着「子串不是子序列」的理由。

### 建议

`hook_reuses_source` 的 detail 追加最长公共子串与偏移：

```
[source_architecture] 与原作文案段最长逐字重合 32 字（候选 offset 41）：
「我哭湿了十几条帕子。后娘看得愁眉不展。她又领回来个皮白腰细的新夫郎，冷…」
窗口重合率 9.6%（13/136，上限 0%）
```

阈值、严重度、判据本身一个字不动 —— 只补位置。同一条改法适用于所有**报比率**的判据
（`rn_residual_stem` 已经做对了：它带 `where` / `hit` / `context` / `suggest`）。

---

## 四、P1 · 同一个根因在待人工清单里出现两次

### 现象

本次 `inbox` 133 项。其中：

- 11 条 `[human_only] stage5 length_over`（篇幅超上限 1.02×~1.12×）
- 11 条 `[verification] stage5 trim_audit_ch{nn}`（未运行语义裁决，保留 pretrim）

复算两边的章号：**逐个相同**，都是 `{3,4,5,6,7,8,13,14,17,18,19}`。

它们是同一件事的两半：收缩臂因为 profile 缺席没启动 ⇒ 超预算的章保留 pretrim ⇒ 于是
`length_over` 必然命中。133 项里有 22 项是**一个根因**。

### 建议

二选一：

1. **判据侧**：`length_over` 在该章存在「trim 未采用」审计时，降级为那条审计的附注
   （不再单独占一行）——它此刻**没有独立的判别力**；
2. **清单侧**：`inbox` 按根因折叠，同一 `trim_audit` 派生的行归成一组，计数按组算。

我倾向 1，因为它在**产生结论**的地方解决，而不是在展示层遮盖。

⚠ 顺带：这 11 条 `verification` 本来有 0 成本闭环 —— `stage5.review --retain-pretrim`
（`FREE_ENTRIES` 里写着「只写 hash-bound resolution，不调用模型」）。本次没用，
因为「接受这些章超 2%~12%」是内容判断，留给人。**但 skill 的跑书流程里没提这条路**，
建议补进 `references/workflow.md` 的收尾步骤。

---

## 五、P1 · 三个 approve 节点的人工凭据差三档，而最主观的那层记得最少

### 现象

| 节点 | 必填凭据 | 落盘记了什么 |
|---|---|---|
| `stage0.approve` | `--actor` `--reason` | actor / reason / approval_origin |
| `stage6.approve` | `--actor` `--reason` `--subject-sha256` | actor / reason / subject_sha256 / basis / semantic_review_seq |
| `stage7.approve` | **无**（`NodeSpec.flags=()`） | `{"origin": "human"}` —— **就这一个键** |

而 stage7 恰恰是**主观性最强**的一层：`rn_taste_unver` 自己写着
「新名的时代感 / 性别气质 / 雅俗没有确定性判据……**现役闸门是人定稿那一步，不是代码**」。
也就是说：这一层把全部把关责任交给人，然后**不记录这个人是谁、凭什么放行**。

### 建议

1. `stage7.approve` 至少补 `--actor` / `--reason`，落进 `ApprovedNameMap`；
2. 更进一步补 `--subject-sha256` 乐观锁 —— 映射表可能在人看完之后被 `--recheck` /
   `--apply-edit` 改过，而当前的 `{'origin':'human'}` **无法证明批准的是哪一版**；
3. 三个 approve 的凭据形状统一到一个模型（现在 stage6 有 `HookApprovalResolution`，
   另两个各写各的）。

---

## 六、P1 · `preview` 会产出一条注定失败的命令；人工输入面的必填键没写进文档

### 6.1 preview ↔ run 分家

```
$ preview stage0.approve <sid>
    $ ... run stage0.approve <sid> --base <BASE>      ← 照抄执行
✗ 显式终态：stage0.approve 必须显式给 --actor 与 --reason      ← exit 2
```

`build_argv` 确实是 preview 与真跑共用的（`R-11.8` 守住了），但**必填 flag 这件事
在 `NodeSpec` 里没有表达**，所以 preview 只能拼出一条缺参数的命令。
而 skill 的工作流契约明写「逐字执行 preview 给出的全部步骤」——两边对不上。

**建议**：`NodeSpec` 加 `required_flags: tuple[str, ...]`；`validate_run` 在构造 argv
那一步就拒绝；preview 把必填项渲染成显式占位符并标注。这样 `R-11.8`
（显示的就是会跑的那条）才真正成立。

### 6.2 `*_edit.json` 的 `_schema_version`

`docs/schema/stage7.md` §三 列了 `NameMapEdit` 的四个可选字段，**没有提 `_schema_version`**；
实跑：

```
✗ 显式终态：stage7/name_map_edit.json: 缺少当前 schema v1 标记
```

这是**全仓唯一一个人手敲的输入面**，却要求一个文档没写、没有模板的内部版本标记。

**建议**（二选一，我倾向 a）：

- **(a)** 文档补上必填 `_schema_version` 并给一段可复制的最小骨架；再加一个 0 成本入口
  `--emit-edit-template`，直接吐一份带当前版本号的空壳；
- **(b)** 对**人工输入类**产物允许省略版本标记（缺省即 current）。它们不是「存量产物」，
  不存在「旧版本冒充新版本」的风险面——那条纪律是为节点产物立的。

---

## 七、P2 · 其余摩擦

### 7.1 `state` 的读数会被误读

- `state` 只答「产物在不在 / 新不新鲜」，**不带最新 badge**。本次 stage2 显示 ✓，
  而 `stage2.blueprint` 的 check 是 AMBER（4 info + 2 unver）。两件事都对，但摆在一起容易读成"这层没问题"。
  **建议**：每层附最新 check badge 与命中数。
- `stage2/global_review.json` 被显示成 `unknown/no_provenance`。它其实**精确当前**——
  用 `novel/quality/global_review.py::review_status()` 复算，`exact_current=True`。
  它只是用 `source_sha256` 而不是通用来源块绑定。
  **建议**：接进 `store._deterministic_freshness`（该机制已覆盖 stage0×2 / stage1×3 /
  `book.txt`，加一格即可），别让一份当前的产物在完成度视图上显示"不知道"。

### 7.2 stage6 没有候选级重试

`HOOK_SYS` 里**明文写着**「不要出现与原始文案连续 20 个内容字符相同的片段」
（`stage6/prompts.py:32`），模型仍然照抄了 32 字。落盘闸门只拦「三条全脏」，
所以这条脏候选永久带着 HARD 躺在产物里，唯一出路是 `hook_edit.json` + `--apply-edit`
整条重写。

对比 `stage5.write`：收缩有 `TRIM_ROUNDS` 多轮，第 2 轮起 `CachePolicy.refresh()`。

**建议**：stage6 加候选级重试——只重生成命中 HARD 的那一条，并把**被判违规的具体片段**
（见第三章的 lcs）回灌进 user。成本是 1/3 次调用，而不是一次全量重跑。

### 7.3 `residual.suggest` 算出来了却要人手抄

本次 3 处高置信残留的 `suggest` 就是 `顾家` / `林家`，我原样抄进了 `extra_subs`。
判据 detail 自己写着「人从上往下读，勾中的走 `--apply-edit` 补进 `extra_subs`」，
而 schema 注释写着 `suggest`「只为省一次敲字，不参与任何判定，**绝不自动生效**」。

**建议**：加 0 成本入口 `--adopt-residual high`（显式 opt-in，只采纳 `confidence=high`），
既保住「绝不自动生效」的立场，又去掉纯转抄的一步。低置信的照旧要人读上下文。

### 7.4 语料测试把「契约」与「这一本书的数据形状」混在一起

换一本书之后语料门 51 红。修掉机制类的之后还剩 20 条，典型形态：

- `test_apply_edit` / `test_stage4`：假设 `chapter_goal.rationale` 一定是非空串
  （本书 19 章**全为 null**）⇒ `TypeError: NoneType += str`，**测试自己炸了，闸门一次都没跑到**；
- `test_views_inbox`：断言 stage5 的 verification「恰好只有 1 行」（本书有 11 行）；
- `test_approvals`：要求 stage2 人工复核清单全 PASS（本书 66 项 `pending`）；
- `test_api_screens`：断言屏幕集恰为 `['s0','s1','s4','s5',...]`。

**建议**：把语料测试拆成两层——
**(i)** 「任何一本书都必须成立」的不变量（分母、形状、闸门方向、往返等价）；
**(ii)** 「这一本的期望值」（用 fixture 固定，不吃现役语料）。
现在两层混在一起，结果是**换一本语料就大面积红，而红的不是被测的东西**。

### 7.5 环境（不是代码问题，但会伪装成代码问题）

`--basetemp` 指向深目录时，stage0 的内容寻址归档路径超过 Windows `MAX_PATH(260)`，
`os.replace` 抛 `WinError 3`，错误行正好落在 `store.py::retire_bytes`。
本次 `test_stage0_merge.py` 19 条里 14 条这样假红，换短 basetemp 后 19/19 全绿。
**建议**：`tests/conftest.py` 在 setup 阶段测一次「basetemp + 最长归档文件名」的长度，
超了就 `pytest.UsageError` 明说，别让它长成一条看起来像归档 bug 的红。

---

## 八、语义审判层必须要 evaluator profile 吗？

### 结论

**不必须。profile 是「自动裁决的准入门」，不是「能不能判」的门。**

### 依据

`double_blind_decision`（`novel/quality/evaluator.py:63-111`）在 `profile=None` 时：

```python
if not enabled:
    return SemanticDecision(verdict='UNVER', automated=False,
                            evidence=evidence,   # ← 两名 judge 的证据照样带着
                            reason=why, ...)
```

它**不抛异常**，返回一个诚实的「未校准 ⇒ 不自动采信」结论，并保留证据 span。
也就是说「跑判官 → 拿到带证据的 UNVER → 交人闭环」这条路**在决策层已经通了**，
`stage5.write` 用的正是它。挡在门口的只是两个 semantic 节点开头那句 `require_enabled`。

### 四条可选路（可叠加）

**① Advisory 模式（推荐，改动最小）**

给 `stage5.semantic` / `stage6.semantic` 一个 `--advisory`（或直接把「缺 profile」当作
advisory，与 `stage5.write` 对齐）：照常发两次判官调用，把 `profile=None` 传下去，
结论一律 `automated=False` / `UNVER`，逐条进 `verification` 并带证据定位。

- 拿回什么：逐条可读的语义线索 + 证据 span + 可追溯的判官原文。
- 不会拿到什么：任何「未经校准的自动绿灯」——`UNVER` 仍然是 `UNVER`。
- 代价：要花钱；报告必须显式标注「未校准 · 仅供人工参考」，并且**不许**参与徽章转绿。
- 风险控制：`require_human_resolution` 那套（actor / reason / subject-sha256 乐观锁、
  **已有的校准 FAIL 不可被人工绕过**）原样复用。

**② 自建 ground truth（唯一能真正拿回"自动"的路，但门槛比想象的高得多）**

先把真实成本摆出来（`contracts/schema/evaluation.py` 的两个 validator 是硬性的）：

| 约束 | 值 | 位置 |
|---|---|---|
| 能力数 | **恰好 8 个**（`event_order` / `verbatim_fidelity` / `hard_number_fidelity` / `reference_boundary` / `secret_timeline` / `world_rule_consistency` / `trim_equivalence` / `style_transfer`） | `EvaluatorProfile.capabilities` min=max=8 |
| **每个能力**的标注例 | **≥100**，其中 PASS ≥40、FAIL ≥40 | `GroundTruthManifest._held_out_floor` |
| 每个 label 的体裁分布 | ≥4 个体裁、各 ≥10 例 | 同上 |
| 标注者 | ≥2 人 + 1 名仲裁人，标注者间一致率 ≥0.8 | 同上 |
| 复跑 | ≥3 轮，阈值必须**评测前**锁定 | `EvaluatorProfile` |
| 每能力启用门 | pass/fail precision 与 violation recall 的 **Wilson 95% 下界均 ≥0.90**，evidence_closure ≥0.95，rerun_consistency ≥0.95 | `CapabilityMetrics.threshold_passed` |

也就是说：**至少 800 条双人标注 + 仲裁**，而且 Wilson 下界 ≥0.90 在 n=100 上几乎是临界
（p=0.95、n=100 时下界约 0.887，**过不了**）⇒ 实际要么样本更多、要么判官准确率 ≥96%。
这是一次研究规模的投入，不是"顺手标几十条"。

⚠⚠ **而且这里有一个粒度不一致，值得单独裁一次**：

- **启用是逐能力的** —— `capability_enabled` 按 ability 查行，
  文档也明说「即使某能力暂无现役自动 consumer……可如实保留 `enabled=false`」；
- **但 manifest 的下限是全 8 项无条件的** —— `_held_out_floor` 的循环写死
  `for ability in EVALUATOR_ABILITIES`，不看你打算开哪几个。

结果：本仓现役真正有 consumer 的只有 3 个能力（`trim_equivalence` / `secret_timeline` /
`reference_boundary`），**却必须先把 8 个能力各标满 100 例，manifest 才能通过校验**。
想「先只把泄底这一维打通」在当前 schema 下是做不到的。

**建议**：把 manifest 的下限改成**按已登记能力逐项校验**（例如 `cases` 里出现过的
ability 才检查其下限，profile 里未覆盖的能力如实记 `enabled=false` 且 `n=0`），
把投入从「800 条起步」降到「按需 100 条起步」。这一条不放宽任何**启用**门槛
——达不到 Wilson 下界照旧不许 `enabled=true`，它只是不再强制为用不上的能力先付标注成本。

**③ 用确定性判据啃掉一部分语义面**

泄底这一维已经有确定性半场：`role_leaks_fate`（8 词表）、`novel/core/facts.py` 的
`secret_keywords` / `locate_secret`、stage2 的 `reader_reveal_events` 时间线。
可以再加一条**纯确定性**判据：

> 成稿第 N 章命中某 secret 的关键词，而该 secret 的 `reader_reveal_events` 最早章 > N
> ⇒ 转抄式跨章泄底。

这能把「转抄式泄底」整个搬进 0 成本层，**只剩改写式泄底**留给语义判官
——而那正是判据自己承认唯一够不着的那一半（`role_fate_unverifiable` 的原话：
「词表只挡转抄式，实测某本词表命中 0/17 而人读真违规 6/17」）。

**④ 降低对"自动"的需求**

`--wontfix` / `--retain-pretrim` 已经把「人看过并决定」做成了带 hash 锁的正式记录。
把 stage6 的 `require_human_resolution` 推广到 stage5 的泄底结论，全链就有了**统一的人工闭环**，
「没有 profile」从一个阻断状态变成一个正常的待办状态。

### 明确不建议

- 伪造 profile 或放宽 `capability_enabled` 的阈值 —— 那等于把 `unver` 改写成 `ok`；
- 删掉 `require_enabled` —— 校准门本身是对的，错的是它在两个节点上的**失败方式**；
- 拿 `tests/fixtures/manifest.json` 当真 manifest —— 那是测试夹具，不是标注数据。

---

## 九、建议的落地顺序

| 顺序 | 项 | 理由 |
|---:|---|---|
| 1 | 一、写盘后置条件 | 一次性给所有层装上守卫，且能防住 A1 那一类静默 stale |
| 2 | 八① Advisory 模式 | 改动小，立刻把语义层从"整层缺席"变成"有线索待人核" |
| 3 | 六、`required_flags` + edit 模板/文档 | 直接消掉两类"照文档做却 exit 2" |
| 4 | 三、判据 detail 补位置（lcs） | 纯报告改进，零风险，可操作性收益最大 |
| 5 | 五、stage7.approve 补 actor/reason/乐观锁 | 补上最主观那层的问责缺口 |
| 6 | 四、`length_over` 与 trim audit 去重 | 清单可信度 |
| 7 | 八② manifest 下限改成逐能力 | 把「要开一个能力得先标 800 条」降到「按需 100 条」，不放宽任何启用门 |
| 8 | 八③ 确定性泄底判据 | 把语义层的负担真正减下来 |
| 9 | 七、其余摩擦 + 测试分层 | 长期维护成本 |

**需要产品裁决、我不自行推进的**：八② 要不要投入标注去建 profile（真实门槛见该章表格）；
四 的两个方案选哪个；六.2 选 (a) 还是 (b)。

---

## 十、我看过、认为设计是对的（写下来省得以后重复讨论）

- **`consumed` 投影按节点登记** —— 第一章那个 bug 的根因是「谁写的记在谁名下」没被守住，
  不是分节点这件事本身错。同一份上游被不同节点消费不同字段，这是事实，投影必须跟着分。
- **`hook_reuses_source` 的 0% 上限** —— 另两条候选实测都是 0.0，做得到；
  而 `source_architecture` 违的是 prompt 里明文写着的规则。
- **`unver` / `skipped` / 分母为零三态分家，绿灯必须带分母** —— 这次至少三处靠它避免了误读
  （`s0_correctness` 分母 0、`rn_coverage_unver`、`hook_semantic_contradiction`）。
- **落盘闸门 + 配套 0 成本提升路** —— stage7 首轮被拦下后，`--apply-edit` 让我没再花一分钱。
- **`--yes` 由 `build_argv` 补而不是调用方传** —— 摊开显示的那条命令就是会跑的那条。
- **字节形态产物拒收 `\r`、LF-only** —— 成书逐字对账时省掉了一整类问题。
- **`preview` 自动追跑 `--check-only`** —— 「退出码 0 也可能是残缺产物」这句话本次成立过
  （stage4.plan 退出 0，追跑 check 才暴露 writer_context stale）。
