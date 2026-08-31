# 语义评测契约

本文件覆盖 `contracts/schema/evaluation.py` 的两族模型：**语义 judge 的结论**
（`EvidenceRef` / `JudgeDecision` / `SemanticDecision`）与 **Stage2 全局逐项人工复核**
（`GlobalReview` 一族）。前者是付费判官的产出形状，后者是 s2 闸的人工决策面。

> **2026-08-18：evaluator profile 与 ground-truth 标定装置整批下线。**
>
> 本仓曾有一套「用标定过的 profile 把 judge 结论升级成自动 `PASS`/`FAIL`」的装置：八类能力、
> `GroundTruthManifest`（双人独立标注 + 第三方仲裁 + 三轮 A/B 校准）、`CapabilityMetrics` 的
> 固定门槛、`EvaluatorProfile`，以及 `tools/` 下四份离线工具。它从未被标定过——因此
> `stage6.semantic` 的默认路径在每一台宿主上都是 `exit 2`，stage5 收缩臂一次都没有启动过。
>
> 现在这套装置连同它守的那条分支一起删除。**留下来的是判官，不是闸门**：双盲 A/B 仍然跑、
> 仍然产出带证据定位的结论，但合并决议**恒为 `UNVER`**，收口只走 hash-bound 的
> `human_verification`（`docs/BACKEND_CONTRACT.md` R-9.6）。
>
> 恢复需要的东西全在 git 里：`EVALUATOR_ABILITIES` 八元组、`GroundTruthCase` /
> `GroundTruthManifest` / `CapabilityMetrics` / `EvaluatorProfile`、
> `novel.quality.profile`、`tools.build_ground_truth_catalog`、
> `tools.ground_truth_workflow`、`tools.restricted_evaluator`、
> `tools.build_evaluator_profile`，以及本文件此前的四节工作流说明。
> ⚠ 这一段刻意用**点号模块名**而不是仓内路径：反引号里的仓内路径受
> `tests/test_docs_references.py` 保护，指着已删文件会红——而「记下删了什么」
> 与「指向一个还在的文件」是两件事。
> 恢复它等于重新承诺去做那场标定战役，别为了「让某个徽章转绿」而恢复。

---

## 一、语义 judge 是 advisory-only

### 1.1 结论形状

| 模型 | 字段 | 说明 |
|---|---|---|
| `EvidenceRef` | `artifact` / `subject_sha256` / `start` / `end` | **定位，不是正文副本**。`start` 与 `end` 要么同时给要么同时不给；`end > start` |
| `JudgeDecision` | `judge` / `verdict` / `evidence[]` / `reason` | 逐判官的结论。`verdict` 真的会是 `PASS｜FAIL｜UNVER`——**这正是要保留的证据** |
| `SemanticDecision` | `ability` / `subject_key` / `verdict` / `evidence[]` / `reason` / `judges[2]` | 合并决议。`verdict` 的类型是 `Literal['UNVER']` |

两个 `verdict` 是**两件事**，不许压成一个类型：逐判官的可以是三态，合并的恒是 `UNVER`。
把合并 verdict 写成 `Literal['UNVER']` 而不是「三态 + 一条校验器」，是因为本仓偏好让非法态
**不可表达**而不是校验掉（同 `web/DELIVERY.md` U-13 的理由）；一条永远不可能被触发的校验器
会绿一辈子，而没人知道它到底在不在。

`ability` 是产物里**唯一**说得出「这两名判官被问的是什么问题」的一格，所以字段保留，
取值面收成 `Literal['reference_boundary']`（现役唯一一类）。

### 1.2 合并规则

合并只做两件事，不做第三件：

1. **证据并集去重**——两名判官各自给的 `EvidenceRef` 合并，这是「有证据的 UNVER」里
   「证据」那一半。
2. **算出人读的 `reason`**，四选一，逐条不同：
   - 至少一名盲评 judge 明确判为 `UNVER`；
   - 两名盲评 judge 结论不一致；
   - judge 结论缺少可闭合证据；
   - **A/B 同判且各自证据闭合——本仓没有自动放行门，结论仍保持 `UNVER`**。

第四条是人在 inbox 与终审包里最常读到的一句，它必须同时说出「两名判官其实都说了什么」
和「为什么这仍不是绿灯」。把它写成一句笼统的「未校准」等于把最有信息量的一次判官一致
读数丢掉。

### 1.3 运行时身份

`EvaluatorFingerprint` 保留 `{model, prompt_sha256, schema_sha256, decoder_sha256}` 四格，
回答「这一次判官看的是哪一版 prompt / 响应 schema / 解码面」。产物新鲜度判定要用它。

它**不再**有 `dataset_sha256`：没有 dataset 了，而一个恒定的哨兵值**看起来像一个 dataset
身份而其实不是**——那正是「把不同状态压成同一个值」。
同理 `SemanticDecision` 不再有 `automated`（恒 `false`）与 `profile_id`（恒 `null`）。

### 1.4 收口

`stage6.semantic` 产的结论一律进入 `verification` 待办。唯一的收口是
`stage6.approve --pick N --actor --reason --subject-sha256`，落 `basis='human_verification'`
的 hash-bound resolution；下游 `require_approved_clear` 校验那份 resolution 仍绑着当前字节。

⚠ **产品含义要说明白**：横在「有矛盾的文案」与成书之间的，只剩人在批准时读判官证据这一道。
本仓不再有任何机制能替人否决一个候选，也不再有任何机制能替人放行一个候选。

---

## 二、Stage2 全局逐项复核与 s2 gate

`stage2/global_review.json` 不是对「已经抽出来的行」逐行盖章后就算完整。确定性枚举器覆盖的
主体按「**能不能被单独修正**」分两档（`R-2.17`）：

- **事实逐条**——人物辨识度与称谓语境、每个角色的 `role` 与 `fate`、`canon.setting` 的每个键、
  每条 relation、每条世界规则的 kind/version、每条秘密的 fact / known-by / **埋点章** / reveal；
- **解读分组**——`arc.conflict_stages` 一项、`arc` 其余部分（climax / ending / arc_tracks）一项。
  这两格是本层自己声明的解读层，逐条拆开每条都判不出对错。

外加四个集合级 subject：`entities.characters` 的 `character_set_complete`、`entities.relations`
的 `relation_set_complete`、`canon.world_rules` 的 `world_rule_set_complete` 与 `canon.secrets` 的
`secret_set_complete`。即使集合为空，这四项仍存在且初始为 `pending`；只有人工对照来源后才能
裁为 `PASS｜FAIL｜UNVER`。它们与 `conflict_partition`、`arc_analysis` 两个整书解读项一起绑定
Global producer 实际读取的 MainText 输入身份；正文变化会重开这六项，而不会冲掉未变事实的逐项决定。
因此「一个人/一条关系/一条规则/一个秘密都没抽到」不能借空循环变成全绿。

⚠ **没被枚举出来的字段等于没人审。** 下一段说的「缺任一必审项就不能开 gate」守的是这份清单
**自身**的完整性，不是「整份 grounding 都过了人眼」。2026-08-23 之前，`role` 的结局旁路
（`R-2.6` 词表实测命中 0/17 而人读真违规 6/17）、`setting.世界观` 的泄底（逐章字面闸门实测
0/8）、relations 这条第二剧透通道，以及两道下发闸门共同的前件 `setup_chapters`，就全都落在
清单之外——闸照开，人却从没看过它们。

每次生成或编辑 `GlobalGrounding` 后，review 的 `source_sha256` 绑定去除 schema/provenance 后的
完整 typed subject。普通事实的旧决定只在 `(item_id, kind, subject_path, subject_sha256)` 四项完全不变时
继承；上述六个整书项还须 MainText 输入身份不变，变过的项回到 `pending`。读 API 与 s2 gate 不信任盘上 `approved` 推论，而是从当前
`GlobalGrounding` 重算完整 subject SHA 和现役必审 item surface：hash 过期、缺任一必审项、
顺序/身份不一致或 blocking 面存在非 PASS 项，`approved=false`，不能开 gate；disclosure 未决项继续披露。

真实决策入口为：

```powershell
python -m novel.cli run stage2.global <sid> `
  --review-decisions <书内或服务私有目录中的 GlobalReviewDecisionSet.json>
```

同形服务入口为 `POST /api/novels/:sid/stage2/global-review`，读面为对应 GET。请求有两种关闭式形状：

- 逐项 `decisions[]`：每项恰好一次，并带非空 `reason`、显式 `evidence[]` 与
  `expected_subject_sha256`；
- 压缩 `blocking_scope`：绑定当前 `source_sha256`、完整 blocking
  `(item_id, subject_sha256)` 集合 SHA、项目数、正文章全集与实际只读 workpack SHA-256，
  给未列入 `decisions[]` 的 blocking
  项一个显式默认 `PASS｜UNVER`。服务端仍展开成逐项持久决定；`decisions[]` 是例外，disclosure
  永不被 scope 自动裁决。workpack 的 binding 含完整 typed MainText SHA-256，整书完整性决定也绑定
  MainText 输入身份；正文变化会重开这些决定。emit 模板默认 `UNVER`，actor/reason 与
  `expected_workpack_sha256` 都是不可提交的占位值；后者只能从实际读过的 workpack 顶层手工复制，
  只改状态、重新生成模板代替阅读或换用旧 workpack 都会关闭式拒绝。

当前只读包为 `stage2-global-review-workpack-v3`。v3 把旧 `review_note` 混写的语义拆成两根正交轴：
`reading_scope=whole_book｜localized` 表示是否必须通读全书，`evidence_mode=`
`unresolved_formal_evidence｜formal_evidence｜claim_literal｜none` 表示当前展示材料的来源；顶层
`legend` 是上述两轴闭集的唯一解释。正式 evidence 在场时不再复制一份 `exact_literal_hits`；正式槽
存在但 `source=None`、缺失或坐标无效时固定为 `unresolved_formal_evidence`，不得回退到 claim 字面
命中。每行 `effect` 与 `group_key` 都由生产 `review_effect_of` / `review_group_key` 派生；含 blocking
项的组优先，同优先级按 `GlobalReview.items` 中该组第一次出现的顺序，组内保持原序。

默认 blocking 包中的每条 `role` 另带同人物 `fate_companion`：只复制 fate 的 item/subject/hash、
当前状态、delivery effect 与正式/未决 evidence，`read_only=true`，不复制 claim-literal 或另造正文
材料，也不新增、删除或代替 decision item；无唯一同人物 fate 时生成即关闭。legend、两轴、派生
分组与 companion 全部纳入 workpack SHA。应用口只现算 v3；旧 v2 SHA 必须在正式
`global_grounding.json`、`global_review.json` 与 `_rounds/` 的首个写动作前拒绝，人工重新生成并实际复核 v3、绑定新 SHA 后
才可提交。workpack 版本是阅读凭证版本，不改变 `GlobalReviewDecisionSet` v2 或正式 artifact 版本。

`GlobalReviewDecisionSet` 因新增第二种形状由 v1 升为 v2。旧 v1 没有 scope hash 与正文章全集
阅读声明，不自动迁移；由 `--emit-review-template` 零成本重发当前模板，人工复核后重新提交。

两种形状都必须带自报 `actor`；这些字段是乐观锁与审计声明，不是认证身份。CLI 在写前再次读取当前完整底座并拒绝
过期/缺项 review。POST 只有 CLI exit `0`（当前 blocking 面全部 PASS）才尝试盖 s2 章；exit `1`
表示决定已落盘但仍有 FAIL/UNVER/pending，作业为 `landed_red`，gate 保持关闭。Inbox 把三种
未闭合状态都列为 `verification`，GlobalScreen 提供逐项或批量提交入口。
