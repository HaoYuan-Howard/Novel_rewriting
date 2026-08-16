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

> **已实现（2026-08-17）**：`NodeSpec.required_flags` +
> `CONDITIONAL_REQUIRED_FLAGS`（跟着另一条跑法才必填的那些）+
> `REQUIRED_PLACEHOLDER_PREFIX`。拒收开在 `validate_run`；`preview` 渲染
> `待填·actor` 这类占位符并把 `VALUED_FLAGS` 的说明一起打出来，
> 而**占位符本身在真跑那一趟仍被拒**——照抄不改跑得到的是一句人话，
> 不是一条把占位符写进人工凭据的假账。详见第二部分 §〇。

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

> **已实现（2026-08-16）**：`stage5.semantic` 与 `stage6.semantic` 新增显式
> `--advisory`。默认路径仍在 transport 前拒绝缺失/漂移的 profile；advisory 运行实际 A/B，
> 以专用无 ground-truth 哨兵绑定请求 surface，并强制合并决议为 `UNVER`。
> Stage 6 报告会保留匿名排序和逐候选证据，不再把未校准产物显示成“0 份审判”；Stage 5
> 也不会把“没有已确认泄漏”误显示成自动 PASS。
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

---
---

# 第二部分 · 2026-08-17 复盘

从**新 stage4 落地后的第一次全链路**里长出来的设计意见
（`常春杜若_1668963766200045568`，25 章，`outputs/e2e_20260817_changchun`；
被验代码是 `d28e3be` + 工作树里 `--advisory` 那批未提交改动）。

写在这份文件里而不是另起一份，是因为下面大量条目直接引用上半部分的编号
（§二 / §三 / §四 / §五 / §六 / §7.x），拆成两份会让引用跨文件。分级口径沿用上半部分。

结论先说：**上半部分诊断的三类接缝，本轮有两类被同一次跑书当场复现了，而且各长出一个新实例。**
新增的第三类是本轮独有的，也是唯一一个**会让主链断**的：

> **判据要求的信息，请求面里根本没给。**
> 不是 prompt 写得不够清楚，是那条判据要模型引用的标识符，模型手里一个都没有。

---

## 〇、本轮已经改掉的（先说清楚基线）

| # | 位置 | 问题 | 处置 | 与上半部分的关系 |
|---:|---|---|---|---|
| 1 | `contracts/invariants.py` · `novel/orchestrator/nodes.py` · `novel/cli/main.py` · `novel/api/enums.py` | `NodeSpec` 表达不出「必填 flag」⇒ `preview stage0.approve` 摊开一条注定 `exit 2` 的命令 | 新增 `NodeSpec.required_flags` + `CONDITIONAL_REQUIRED_FLAGS` + `REQUIRED_PLACEHOLDER_PREFIX`；拒收开在 `validate_run`（展示与执行共用的那一处）；`preview` 把缺项渲染成 `待填·actor` 占位符并标注，**占位符本身在真跑那一趟仍被拒** | **§六.1 落地** |
| 2 | `novel/stages/stage4/fact_refs.py` · `plan.py` · `prompts.py` | `goal_landing`（hard）按构造无法满足 —— 详见第一章 | 事实目录补 `event_units`（`event 编号 → 源内容单元编号`）这道 join；不改 schema / 判据 / 严重度 / 公开契约 | 新 |

改完后：默认门 **1519 → 1525 passed**（净增 5 条新回归 + 1 条被契约变更打红后修好的）；
`stage4.plan` 首跑 20+2 条 HARD → 重跑后 1 条 → 手修 1 条后 **PASS**。

---

## 一、P0 · 「判据要的信息，下发面里没给」——本轮唯一会断主链的那一类

### 现象

新 stage4 首跑 `exit 2`，`goal_landing`（hard）命中 **20 条**（报告只打印前 8 条）。
`goal_landing` 要求：`open_hook_goal` 至少引用**首个写作单元**里的一条 event，
`end_hook_goal` 同理指向最后一个单元。全书 25 章 × 2 个边界目标 = 50 个，**错了 20 个**。

### 根因（不是「模型不听话」）

把 `--dry-run` 摊开的那份 user 逐块看一遍，两块拼不起来：

| 块 | 给了什么 | 没给什么 |
|---|---|---|
| `BLOCK_SKELETON` 章节骨架 | `plan_beat: p02_03` + 该单元的**事件正文** | **没有 `beat_id`** —— 实测「骨架块之后 `b\d\d_\d\d` 命中 **0** 次」 |
| `BLOCK_FACT_REFS` 可引用事实目录 | `{"events":{"b02_05":2,…}}` —— b 号 → 章号 | **没有正文，也没有所属单元** |

而 `project_blueprints_for_planner` 的注释写着这是**故意的**：
「源 beat 的编号一旦下发，模型就有了『重新编组』的抓手」。

⇒ 模型被要求在 `fact_refs` 里写一个 `bNN_MM`，并让它落进某个 `pNN_NN` 里，
而**下发面里没有任何一处把这两个编号空间连起来**。它只能猜。

### 这不是猜测，数据自己说了

20 条错的里面，**20 条的 `rationale` 全部正确描述了目标单元里的那一拍**：

```
out2 open_hook_goal
  rationale：「以温夏白踹门为冷开场。」   ← 踹门 = b02_05，正确
  fact_refs：b02_04                      ← 引错
  segments[0].from_beats = [b02_05, b02_06]
```

`out6 / out8 / out9 / out10 / out12 / out13 / out15 / out17 / out18 / out21 / out25` 同形。
**模型读得懂骨架正文，只是没法把正文映回 b 号。** 通过的那 30 条是序号碰巧对上。

补上 join 之后重跑（同一模型、同一配置，只多了一张 `event_units` 表）：
`goal_landing` **20 → 1**、`style_words` **2 → 0**。

### 判断

这一条已经修好了，但**它是一类，不是一个**。当前没有任何机制守住这条不变量：

> 一条 `sev=hard` 且 `mechanical/llm` 的判据，它引用的每个标识符空间，
> 必须在**本层实际下发的 user** 里可解析。

`PLAN_SYS` 与 `project_blueprints_for_planner` 在**同一个提交**（`9760ea8`）里
被同时改动，而它们互相矛盾——加规则的人和裁下发面的人是同一个人、同一次改动，
仍然没有一层出声。这正是本仓一直在防的形状（判据 26 那一族），只是换了个位置。

### 建议

**给这类判据补一条「可满足性」测试，作为跨层纪律。** 形状是：
拿**当前**下发面，构造一份能通过该判据的响应；构造不出来 ⇒ 那条判据是不可满足的。

本轮已在 stage4 上做了单点实现，可以照抄推广：

```python
# tests/test_stage4.py::test_the_planner_can_actually_satisfy_goal_landing_from_what_it_is_given
refs = json.loads(user.split(BLOCK_FACT_REFS)[1].split(BLOCK_SKELETON)[0])
assert set(refs['event_units']) == set(refs['events'])     # 每条 event 都查得到所属单元
assert f'"plan_beat":"{sorted(by_unit)[0]}"' in skeleton   # 单元编号与骨架块同一套
```

⚠ 它守的**不是**「模型会不会照做」（那没有遵守率保证，§7.2 已经记过一次），
而是「照做所需的信息在不在下发面里」——后者是确定性的，可以被测试钉死。

轻量版落地路径：给 `RuleSpec` 加一格 `needs_id_spaces: tuple[str, ...]`，
`build_prompt` 侧声明 `provides_id_spaces`，一条参数化测试对账全部 hard·llm 判据。
重的那半（真去构造合法响应）只对**引用型**判据做。

---

## 二、P0 · `--revalidate` 把人改过的字节洗成「模型产的」

### 现象

`stage4.plan` 被闸门拦下时，终态文案说的是：

```
问题稿已存 `stage4/plan_chapters.INVALID.json`，**正式产物没有被覆盖**。
⚠ 这次不必再付费：手改那几行，`stage4.plan --revalidate` 提升即可（0 成本）
```

我照做了：手改 `.INVALID` 里 **一个** `fact_refs[0].ref`（`b10_04` → `b10_05`），
`--revalidate` 提升。提升后的正式产物 `_provenance`：

```json
{"code": "d28e3be…-dirty…", "model": "deepseek-v4-pro",
 "prompts": {"N4_SYS": "cbd2be9398"}, "revalidated": true,
 "usage": {"calls_made": 1, "prompt_tokens": 33361, …}}
```

**没有任何一个字段说「这份产物里有一个字节是人改的」。**

### 对照：本仓其它两处同类动作都留痕

| 路径 | 留痕 |
|---|---|
| `stage0.edit --apply-edit` | `edited_by_human` + `origin`，并由 `s0_human_preserved`（hard）守着「人工编辑状态没有被机器重跑抹掉」 |
| `stage4.plan --apply-edit` | `rewrite_provenance(prev, by='--apply-edit')`，标 `partial_rewrite` |
| `stage4.plan --revalidate` | **只有一个 `revalidated: true`**，它答的是「重判过」，不是「被改过」 |

`revalidated` 这个布尔分不开两件事：「原样重判、判据变宽了所以过了」与
「人改了字节所以过了」。而本层**本来就有**一条会留痕的路（`--apply-edit` + `plan_edit.json`，
`EDIT = STAGE4_PLAN_EDIT` 一直在 `ARTIFACTS` 里登记着）——
**终态文案却只提留不下账的那一条。**

### 判断

这是本仓最核心的纪律（「每一笔都要有账」）上的一个洞，而且是**被文案主动引导着走进去的**。
stage6 / stage7 的同类闸门形态一致，同一个洞大概率也在。

### 建议（按代价从小到大，建议全做）

1. **终态文案同时给两条路，并说清区别**：
   「改 `.INVALID` 再 `--revalidate`」= 改这一轮的字节，**不留人工痕迹**；
   「写 `plan_edit.json` 再 `--apply-edit`」= 落成新一轮，来源账标 `partial_rewrite`。
2. **`--revalidate` 落盘前比对 `.INVALID` 的内容指纹**：与被拦下那一刻记下的指纹不同
   ⇒ 说明人改过，`_provenance` 补 `edited_by_human=True` 与改动摘要（改了哪几个 JSON 路径）。
   `write_invalid` 那一步顺手记一个 sha256 即可，0 成本。
3. 更彻底：给 `--revalidate` 也补 `--actor` / `--reason`（本轮新增的 `required_flags`
   机制可以直接表达），与第七章的凭据统一。

---

## 三、P1 · stage4 是「全书一次调用 + 全或无落盘」，一条 HARD 拖垮 24 章合格规划

### 现象

补完 join 重跑后，`goal_landing` 只剩 **1 条**（out10 一个 `fact_refs`），
而这一条把**整份 25 章的规划**挡在 `.INVALID` 里。

那一次调用的账：prompt 33,361 / completion 22,024 / reasoning 13,300 token、**248 秒**。
本层是全仓最贵的单次请求（`max_tokens=96000`，user 里带着全书原文）。

### 对照：别的层都有更细的粒度

| 层 | 拦下之后 |
|---|---|
| `stage5.write` | `--chapters` 定向重生成那几章；收缩有 `TRIM_ROUNDS` 多轮 |
| `stage6.hook` | 一次产三条候选，闸门拦的是「一条干净的都没有」；可 `--apply-edit` 只重写一条 |
| `stage2.blueprint` | `--chapters` 定向重生成 |
| **`stage4.plan`** | **只有全书重跑（付费）或手改 JSON（0 成本但要人读懂 `segments[].from_beats` 与 `goal.fact_refs` 的关系）** |

### 建议

**给 `stage4.plan` 补 `--chapters N,M` 定向重规划。** 本层的产物形状允许：
`output_chapters[]` 逐章独立，`one_to_one` 保证 `output_ordinal == from_source_ordinals[0]`。

- 只把命中章的 `output_chapter` 交回模型重产，其余章原样保留；
- **把被判违规的具体条目回灌进 user**（同 §三 对 stage6 的建议：报比率不如报位置）；
- ⚠ `reconstruction_strategy` 是**书级**的，定向重跑不许改它——它一改，
  `structure_changed` / `style_words` 的判定对象就换了。
- 成本从「全书一次 33k/22k」降到按章。

---

## 四、P1 · `rn_residual_stem` 的 `confidence` 对本轮 5/5 真漏网全部失效

### 现象

stage7 首轮 `rn_residual_stem` 报 **22 处**旧姓残留，`confidence` **全部 `low`**。
逐条读上下文之后：**其中 5 处是真漏网。**

| 真漏网 | 次数 | 形态 |
|---|---:|---|
| `温兄` | 4 | **未登记简称** —— 人物表收了 `温师兄` / `温大师兄`，没收 `温兄` |
| `汤、临` | 1 | **顿号拆名** —— 「裴清玄：『……汤、临。』」，旧 canonical 被标点切开 |

其余 17 处是中文没有词边界的固有假阳：
温柔 / 温度 / 温文尔雅 / 温驯 / 温声道 / 温和 / 温温柔柔 / 温热 / 反常 / 往常 / 常常躺 / 四周 / 周围。

### 根因

`confidence` 的**全部**输入是一格（`novel/stages/stage7/subs.py:203`）：

```python
'confidence': 'high' if nxt in RESIDUAL_HIGH_STEMS else 'low'   # ('家','府','氏','宅','门')
```

`nxt` 是旧姓后**紧邻的一个字**。于是：
`温兄` 的 `nxt` 是「兄」（称呼后缀，不在表里）⇒ low；
`汤、临` 的 `nxt` 是「、」（标点）⇒ low。**5/5 真漏网全部被判 low。**

那条判据自己提供的第二个信号——覆盖率——在本轮只对了一半：

| 姓 | 覆盖率 | 残留 | 其中真漏网 | 覆盖率判别器给的答案 |
|---|---:|---:|---:|---|
| 汤 | 99% | 1 | **1** | ✅「≥90% 的字，残留几乎必然是真漏网」——说对了 |
| 温 | 78% | 15 | **4** | ❌ 78% 落在「既不是 ≥90% 也不是 <50%」的中间带，给不出答案 |
| 常 | 83% | 4 | 0 | ❌ 同上，而这 4 条全是假阳 |
| 周 | 50% | 2 | 0 | ✅「<50% 基本是普通词」——说对了 |

⇒ **本轮唯一能把 5 处真漏网找出来的，是我手写的一个补扫脚本**，而那个脚本只有两条规则。

### 建议：把那两条规则做成 `confidence` 的正交信号

两类都是**确定性可判**、且都不是普通词：

```
① 旧姓 + 常见称呼后缀  →  high
   兄/姐/妹/弟/公子/少主/仙子/姑娘/道友/前辈/家/府/宗/氏/宅/门
   （现有 RESIDUAL_HIGH_STEMS 是它的真子集，合并即可）

② 旧 canonical 被标点/空白拆开  →  high
   pat = r'[、，。！？…·\s]{1,3}'.join(map(re.escape, old_canonical))
```

**在本轮语料上实测这两条的效果**：
① 能把 `温兄`×4 提到 high；② 能把 `汤、临`×1 提到 high ⇒ **5/5 真漏网全部提上来**；
而 17 处普通词假阳里，没有一条会被这两条规则误提（「温柔」的 `柔` 不在后缀表里；
「往常一样」里没有标点拆名的形）⇒ **0 假阳被提升**。

⚠ 沿用现有立场：**只影响排序与标记，绝不自动替换**
（「不允**许家**人」那条注释仍然成立，且形态 ② 更危险——它跨标点）。

⚠ 顺带：形态 ② 说明**替换面本身够不着这一类**。`extra_subs` 能收
（我这次就是写了 `汤、临 → 陆、景`），但那要求人先发现它。检查这一侧补上之后，
`suggest` 那一格顺手就能给出可直接用的 `extra_subs` 条目。

---

## 五、P1 · 定稿唯一有效的信号是 `info`，而它在报告里一个字都不打印

### 现象

stage6 三条候选，`stage6.hook --check-only` → **ok 15 · 命中 0**：机械层一条都没拦下。
而双盲语义判官对其中两条各给出一条具体矛盾：

| 候选 | 匿名总分 | 语义矛盾 |
|---|---:|---|
| `scene_punchline` | **8.66（第一）** | 让汤临说「你拿着**我送的**剑」——秋山剑是大比魁首奖品，汤临送的是定光剑。**凭空事实** |
| `payoff_preview` | 7.35 | 称旁观者以为当众亲吻是脸盲认错——正文该处在表白之后。**改变事件性质**，且直接写出失忆/下药的核心反转 |
| `source_architecture` | 8.36（第二） | **0 条** |

也就是说：**匿名质量排序第一的那条是脏的**，唯一能把它筛掉的信号是
`hook_candidate_contradiction`——而它是 `info`。

### 更要命的是：那条 info 在报告里没有 detail

实跑输出逐字如下，**只有规则名，一条证据都没打印**：

```
[!!] hook_candidate_contradiction（info · semantic/human）逐候选列出实质矛盾或审判不确定项，供定稿时避开
```

这条判据的**全部价值**就是那几条矛盾正文。我是**读了 `stage6/semantic_1.json` 原文**
才看到「秋山剑不是汤临送的」这句话的。一个只看报告的人（或 agent）拿不到它。

对照 `rn_residual_stem` 就做对了：它带 `where` / `hit` / `context` / `suggest`
——所以第四章那 22 条我能逐条判。

### 建议

1. **`hook_candidate_contradiction` 的 detail 逐候选打印 `assertion` / `evidence` / `reason`**。
   这与上半部分 §三（报比率的判据要补位置）是同一条纪律的另一面：
   **报存在性的判据要补内容**。
2. **把「所选候选有语义矛盾」提升为定稿闸门的一等输入。** 现在 `stage6.approve` 拦的是
   *机械* hard（`mechanical_hard`）与 `require_human_resolution`；
   语义判官逐条写下来的矛盾**不进闸门**。建议：所选候选在最新语义报告里
   `contradictions` 非空 ⇒ 拒绝定稿，除非人显式写 `--reason` 说明为何仍然选它。
   ⚠ 这一条**不依赖 evaluator profile**：矛盾条目是判官写出来的事实陈述，
   与「合并决议是否 automated」是两件事——本轮 `source_architecture` 的合并决议
   是 `UNVER`，但它的 `contradictions` 确实是空数组。
3. 机械侧能不能捞回一部分？`hook_secret_literal` 自己写着「字面探针在这个产物类上
   结构性失效」。可以试一条**弱**的确定性判据：文案里出现的 `<人名> + 送/给/赠 + <物件>`
   三元组，对着 stage2 的 `beats[].event` 做字面核对。本轮那条脏候选会被它抓到。
   但它假阳率未知，建议先做成 `metric` 常驻报读数，攒够样本再谈严重度。

---

## 六、P1 · 同一层两条判据，一条带章号一条不带 —— §四 的折叠方案因此做不到

### 现象

本轮 `inbox` 共 160 项，stage5 占 77 条 `human_only` + 10 条 `verification`。
其中上半部分 §四 点名的那一对**完全复现**：`length_over` **10 条** / `trim_audit` **10 条**。

但这一次没法像 §四 那样「复算两边的章号，逐个相同」——因为**其中一边根本没有章号**：

```
[human_only] stage5  length_over
      篇幅超上限：930 字 > 825 字（目标 675~825，1.13× 上限）
      → 0 成本人工路：`--apply-edit` 改内容 / `--wontfix` 记「不修」的裁决

[verification] stage5 ch3  trim_audit_ch03          ← 这一条有 `ch3`
      第 3 章收缩机械检查已过，但 未运行语义裁决：…
```

`inbox` 的 `where` 那一格，同一层里一条填了、一条空着。10 条 `length_over` 在清单上
长得几乎一样，只能靠「930 字 > 825 字」这串数字去反推是哪一章。

### 判断

§四 提的方案 1（「`length_over` 在该章存在 trim 未采用审计时，降级为那条审计的附注」）
**在当前数据结构下做不到**：降级要按章 join，而 `length_over` 的 finding 里没有章。

⇒ **先补 `where`，才谈得上按根因折叠。** 这是 §四 的前置条件，不是替代方案。

### 建议

1. `length_over` / `length_under` / `band_drift` / `simile_drift` / `xinli_drift` /
   `dialogue_ratio_drift` 这一族逐章判据，finding 一律带 `output_ordinal`
   （`views/inbox.py` 的 `where` 从它取）。
2. 补完之后再做 §四 的方案 1。
3. 顺带：`length_over` 的 `→ 0 成本人工路：--apply-edit 改内容` 措辞可疑——
   stage5 的 `--apply-edit` 吃的是**整章全文**，对人来说那不是「0 成本」，
   是重写一章。这里的「0 成本」说的是**不花模型的钱**，与人读到的意思不是一回事。

---

## 七、P1 · 人工凭据强度差四档（§五 的新实例）

上半部分 §五 记了三档，本轮又撞出第四档：

| 动作 | 凭据 | 落盘记了什么 |
|---|---|---|
| `stage0.approve` | `--actor` `--reason` **必填** | actor / reason / approval_origin |
| `stage6.approve` | `--actor` `--reason` `--subject-sha256` **必填** | 全套 + basis + semantic_review_seq |
| `stage7.approve` | **无** | `{"origin": "human"}` |
| **`stage5.review --retain-pretrim`** | **`--actor` / `--reason` 静默默认空串** | 一条 **hash-bound** 的 resolution，actor 可以是 `''` |

第四档最刺眼：`novel/cli/main.py` 里写的是
`actor=_valued(flags, '--actor') or ''`——一条绑定了 mode、正文 SHA-256、
audit 完整 SHA-256 与 before/after hashes 的**正式人工裁决**，允许没有署名、没有理由。
而 `inbox` 给出的操作提示反而写着 `--actor <自报身份> --reason <理由>`，
**提示比代码严**。

⇒ 本轮新增的 `CONDITIONAL_REQUIRED_FLAGS` 正好能表达这件事，一行登记即可：

```python
('stage5.review', '--retain-pretrim'): ('--chapters', '--actor', '--reason'),
```

（本轮只登记了 `--chapters`，因为把 actor/reason 从「可选」改成「必填」是一次
**行为变更**，属于产品裁决，没有自行推进。）

---

## 八、P2 · 其余摩擦

### 8.1 一条判据独占 stage5 待人工清单的 39%，而它 26 条说的是同一句话

`band_drift` 在 stage5 的 77 条 `human_only` 里占 **26 条**，逐条内容只有两种：

```
形态漂移 段落形态：底座「短段」→ 成稿「极短段（多为一句一段）」
形态漂移 句子形态：底座「短句」→ 成稿「中等长度句」
```

25 章 × 2 维 = 50 个可能位置，命中 26 个——**超过一半的章在同一个方向上漂**。
这不像逐章的偶然，更像**写手系统性地比原文写得更碎**（`para_len_drift` 那条独立佐证：
本书均段 21.8 字 → 某章 13.0 字，判语是「电报体（段被剁得太碎）」）。

⇒ 逐章报 26 次，人读到的是 26 件事；而它其实是 **1 件书级的事**。
建议：`band_drift` 出书级汇总（「25 章里 13 章段落形态下移一档」）+ 逐章明细折叠，
计数按书级算。这与 §四 是同一条「清单可信度」纪律。

### 8.2 语料测试的生命周期不受测试控制

本轮给新写的 stage4 回归找语料时，第一选择 `outputs/e2e_changchun_duruo_20260816`
**已被用户删掉**（正常清理），于是那批 corpus 测试当场
`0 collected`（pytest `exit 5`）——而 `addopts` 默认带 `-m "not corpus"`，
所以日常根本跑不到，**删语料这件事不会被任何东西报出来**。

这给上半部分 §7.4（语料测试分层）补了一个新论据：不只是「换一本书会大面积红」，
还有「**语料没了会静默变绿**」。⇒ 第 (i) 层「任何一本书都必须成立的不变量」
必须用 fixture 固定，不能吃现役 `NOVEL_OUTPUT`。

### 8.3 产物 `usage` 与账本的口径差

`stage4/plan_chapters.json` 的 `_provenance.usage.calls_made = 1`，
而账本里 `stage4.plan` 是 **2** 次实付（第一次被闸门拦下、稿子进了 `.INVALID`）。

两个数都对——产物说的是「**这一份**是几次调用产的」，账本说的是「**这本书**花了几次」。
但只读产物去汇总成本会系统性低估。⇒ 建议在 `docs/schema/_common.md` 把这条口径写死一句，
并在终审包模板里点明「成本只认账本」。

### 8.4 P1 · 按字节钉哈希的 fixture 在 Windows 上**一次全新 checkout 就全破**

本轮为了验证拆出来的那个 commit 单独是否绿，`git worktree add` 了一份干净检出，
在里面跑全量门 —— **10 条红**，而同一份代码在主仓里是全绿。逐条查下来根因只有一个：

```
tests/fixtures/scenarios/long_context.json
  主仓     ： 77 字节 · CRLF 0 处 · sha256 2ea1a268…   ← 与 manifest 相同
  新 checkout： 82 字节 · CRLF 5 处 · sha256 ffb1f098…   ← 对不上
```

`core.autocrlf=true`（本仓当前配置）在检出时把 LF 换成 CRLF，而
`tests/fixtures/manifest.json` 钉的是**字节** sha256。逐条复算：

| fixture | 主仓 sha 匹配 | 新 checkout 匹配 | 检出后被插入的 CRLF |
|---|---|---|---:|
| `long_context` | ✅ | ❌ | 5 |
| `secret_numbers` | ✅ | ❌ | 5 |
| `half_draft` | ✅ | ❌ | 5 |
| `rejected_archive` | ✅ | ❌ | 10 |
| `retry_branch` | ✅ | ❌ | 9 |

**5/5 全破。** 连带 `test_current_corpus_paid_preview.py` 9 条一起红（它们消费同一批 fixture）。

主仓之所以是绿的，只是因为它的工作副本从来没经过一次真正的 checkout——
这些文件是工具写出来的、一直是 LF。**也就是说：这条门在「换台机器 clone 一遍」
的那一刻才会红，而那是最不该红的时刻。**

⚠ 本仓**已经解决过同一个问题**，就在 `.gitattributes` 里：

```
# The pinned tokenizer is a byte-verified upstream resource. Git must never
# rewrite its LF bytes to CRLF on Windows checkouts.
novel/llm/resources/deepseek_v4_tokenizer.json -text
```

那条注释里的每一个字都适用于这 5 个 fixture ——「byte-verified」「must never rewrite」。
只是当时只豁免了一个文件，而**「按字节校验」这件事后来长出了第二处，没人回头补**。

**建议（一行）**：

```
tests/fixtures/** -text
```

⚠ 顺带一条纪律：**凡是把 sha256 钉进仓库的文件，都必须同时进 `.gitattributes`**。
这两件事之间今天没有任何东西对账 —— 可以加一条 0 成本测试：
遍历 `manifest.json` 的 `path`，断言每条都被某条 `-text` 规则覆盖
（`git check-attr text -- <path>` 或直接匹配 `.gitattributes` 的模式）。

### 8.5 上半部分两条 P2 本轮原样复现，未修

- **§7.1 `state` 不带 badge**：本轮 `state` 显示 stage5 ✓，而 `stage5.review` 是
  AMBER、11 条命中。两件事都对，摆在一起仍然容易读成「这层没问题」。
- **§7.2 `global_review.json` 显示 `unknown/no_provenance`**：本轮逐字复现。

---

## 九、建议的落地顺序（第二轮）

| 顺序 | 项 | 理由 |
|---:|---|---|
| 1 | 二、`--revalidate` 留痕 | 「每一笔都要有账」上的洞，且文案在主动把人往里引；改动小 |
| 2 | 五.1 报告补逐候选矛盾正文 | 纯展示改进、零风险；本轮它是唯一能筛掉脏候选的信号却看不见 |
| 3 | 四、`confidence` 补两条正交信号 | 实测能把 5/5 真漏网提上来且 0 假阳；确定性、可测 |
| 4 | 六、逐章判据补 `output_ordinal` | 它是上半部分 §四 折叠方案的**前置条件** |
| 5 | 一、可满足性测试作为跨层纪律 | 防的是本轮唯一断链的那一类；先按 stage4 的单点实现推广 |
| 6 | 三、`stage4.plan --chapters` 定向重规划 | 省的是全仓最贵那次调用；改动中等 |
| 3.5 | 八.4 `.gitattributes` 补 `tests/fixtures/** -text` | **一行**，且它挡的是「换台机器 clone 一遍就 10 条红」；本仓已有同款先例 |
| 7 | 八.2 语料测试分层 | 与上半部分 §7.4 合并做 |
| 8 | 五.2 语义矛盾进定稿闸门 | **需要产品裁决**：它会让「有矛盾但我就要这条」变成必须写理由 |
| 9 | 七、`--retain-pretrim` 的 actor/reason 改必填 | **需要产品裁决**：行为变更 |

**需要产品裁决、我不自行推进的**：五.2、七，以及上半部分遗留的
八②（要不要投入标注建 profile）、四（两个方案选哪个）、六.2（选 a 还是 b）。

---

## 十、本轮验证了上半部分哪些判断是对的

- **§八① Advisory 模式** —— 落地并经受住了一次真实全链路。没有它，本轮
  `stage5.semantic` / `stage6.semantic` 会双双 `exit 2`，整个语义层缺席；
  有了它，两层都跑出了带证据的 `UNVER`，而且 **`UNVER` 一次都没有被洗成 `PASS`**
  （`semantic_secret_leak` 5/5 条 secret 保持未决、`hook_semantic_contradiction`
  保持 `uncertain`）。上半部分「profile 是自动裁决的准入门，不是能不能判的门」这句话成立。
- **落盘闸门 + 0 成本提升路** —— stage4 首跑被拦下后，**正式产物一个字节都没被覆盖**；
  两次修复（改 `.INVALID` / `--apply-edit`）都没再付一分钱。这套设计本轮救了一次全书规划。
- **`unver` 不算闸门命中**（`_hard_hits` 明确排除）—— 本轮 `semantic_secret_leak`
  是 `sev=hard` 且落 `UNVER`。若把它算进闸门，一本没有 profile 的书永远交付不了；
  而它现在正确地表现为「不阻断、但必须在终审包顶格披露」。
- **`--yes` 由 `build_argv` 补** —— 本轮 6 个付费节点，摊开的命令与执行的逐字相同。
- **字节形态 LF-only** —— 成书对账 `CR 字节 0 个`，又省掉一整类问题。
- **`preview` 自动追跑 `--check-only`** —— stage2 `--full-extract` 的两条追跑
  （blueprint / style）本轮都跑出了独立结论，没有一条是白追的。
