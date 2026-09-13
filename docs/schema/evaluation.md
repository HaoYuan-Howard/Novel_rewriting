# 语义评测契约

本文件覆盖 `contracts/schema/evaluation.py` 的语义 judge 结论
（`EvidenceRef` / `JudgeDecision` / `SemanticDecision`，advisory judge 的产出）。
原 `GlobalReview` 一族已随 Stage2 语义审阅退役（见文末第二节）。

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
**不可表达**而不是校验掉（同 `web/AGENTS.md` U-13 的理由）；一条永远不可能被触发的校验器
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

第四条是人在 前端 inbox 与交付视图里最常读到的一句，它必须同时说出「两名判官其实都说了什么」
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
`stage6.approve --pick N --actor --reason --review-token <s6r4_…>`，落
`basis='human_verification'`
的 hash-bound resolution；下游 `require_approved_clear` 校验那份 resolution 仍绑着当前字节。

调用方只搬运工作包给出的 opaque `review_token`。服务端在同书提交锁内重建工作包，内部同时
复核候选内容、可见 finding、显式审阅契约与当前机械 `validation_seal`；代码或规则变化只要求
免费机械复检，若长期 `review_identity` 未变，既有人工决定自动恢复适用，不要求人重签。

⚠ **产品含义要说明白**：横在「有矛盾的文案」与成书之间的，只剩人在批准时读判官证据这一道。
本仓不再有任何机制能替人否决一个候选，也不再有任何机制能替人放行一个候选。

---

## 二、Stage2 审阅族的退役

原 `GlobalReview` / `GlobalReviewDecisionSet` 复核族及其工作包、决定集、义务继承、
`--review-decisions` / `POST global-review` 入口、`Stage2NodeReviewRequest.core_edits`
统一批次与 Global Commander 内容轮预算已随 Stage2 语义审阅整体退役（2026-09，工作流 v7）。
`stage2/global_review.json` 与 `stage2/global_review_decisions.json` 进入
`contracts/artifacts.py::RETIRED_ARTIFACT_PATHS`，旧字节只配对冻结旧代码解码，不迁移；
evaluation.py 中保留的类型仅作提交 journal 旧格式壳（空 `GlobalReview`、
`post_edit_review` 新提交拒绝），不再构成任何 API、审阅面或生成前置。

Stage2 下游前置由统一机械准入承接（`novel/admission.py`）：必要产物可读、当前、
无未恢复 INVALID 且机械检查全部通过；人物完整、主张获原文支持与剧情抽取正确属
「Stage2 语义未审」披露（`role_fate_unverifiable`、`secrets_absent`、`plot_semantics`），
最终随交付披露该限制。人物与 Global 的 typed 订正分别走 CharactersEdit 与 GlobalEdit
（独立预检 + apply），字段见 `docs/schema/stage2.md`。
