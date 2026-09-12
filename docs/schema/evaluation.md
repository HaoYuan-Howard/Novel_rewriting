# 语义评测契约

本文件覆盖 `contracts/schema/evaluation.py` 的两族模型：**语义 judge 的结论**
（`EvidenceRef` / `JudgeDecision` / `SemanticDecision`）与 **Stage2 人物和长期知识统一复核**
（`GlobalReview` 一族）。前者是 advisory judge 的产出，后者由受托 Commander 或人工明确裁决。

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
`stage6.approve --pick N --actor --reason --review-token <s6r3_…>`，落
`basis='human_verification'`
的 hash-bound resolution；下游 `require_approved_clear` 校验那份 resolution 仍绑着当前字节。

调用方只搬运工作包给出的 opaque `review_token`。服务端在同书提交锁内重建工作包，内部同时
复核候选内容、可见 finding、显式审阅契约与当前机械 `validation_seal`；代码或规则变化只要求
免费机械复检，若长期 `review_identity` 未变，既有人工决定自动恢复适用，不要求人重签。

⚠ **产品含义要说明白**：横在「有矛盾的文案」与成书之间的，只剩人在批准时读判官证据这一道。
本仓不再有任何机制能替人否决一个候选，也不再有任何机制能替人放行一个候选。

---

## 二、Stage2 人物与长期知识的统一审阅

`stage2/global_review.json` 同时审阅独立的 Characters v2 与 Global v7。它是审阅账，
不是第三份知识产物。GlobalReview 与 GlobalReviewDecisionSet 均为 v3；旧版本不自动迁移
或继承审批。源钩子事实和连续开篇范围归 Stage6 Prepare，不属于当前 Stage2 审阅面。

### 2.1 必要主体与依赖

确定性枚举器按可独立订正的主体建立义务（`R-2.17`）：

- Characters：人物身份及首次出场、每个去重称谓的实体归属与 `identity_revealed_from`、
  首次公开 `role`、`name_distinctiveness`。
- Global：`setting.era` 与 `setting.social_context`、规则类别及各版本、重要有向关系及
  成立与披露时间、arc secret 的命题、铺垫章和揭示时间。
- 四项集合完整性：人物、关系、规则、秘密。空集合仍须对照全书明确审阅，不能借空循环通过。

Commander 判断事实支持性、实体误合并、遗漏、过度概括、时间语义与秘密晋升；ID、引用存在、
坐标、重复、合法章范围及 `fact` / `fact_ref` 二选一由代码检查。同场景局部惊喜不进入 Global；
同章跨独立情节单元的例外须有语义说明，Stage2 不用段落数量冒充分场验证。

每项摘要绑定实际主张与所用证据，集合完整性剥除坐标但绑定 MainText。称谓路径按精确 surface
稳定标识，重排不转移原问题。关系只绑定端点的必要身份；无关人物证据订正不重开全部知识。
完整审阅来源 SHA 与读取 token 另守本轮并发快照，不能用它推导所有消费者失效。

`GlobalReview` 的当前性从两份正式产物重算，不能信任盘上旧的 approved 推论。缺项、身份漂移、
过期或必要材料缺失均不放行；`resolution_required` 是代码派生值，不能改审阅账来解除它。
`approved` 只表示当前事实面全部 PASS。`generation_safe` 独立计算：当前 Core 各项均为 blocking，
仅秘密的完整闭合组可对 UNVER 显式 `quarantine_group`；人物、关系、规则、背景及集合完整性
不能隔离。FAIL、pending 与未处置 UNVER 仍阻断，隔离不改写为 PASS。

### 2.2 读取材料与决定入口

当前工作包为 `stage2-global-review-workpack-v6`，token 为 `s2gr2_`，节点接受契约为
`stage2-global-acceptance-v2`。工作包独立呈现人物、知识与各自产物证据，不携带旧 fate、
逐次 address、人物知情矩阵或源钩子准备材料。

`reading_scope=whole_book｜localized` 表达是否需通读，`evidence_mode=`
`unresolved_formal_evidence｜formal_evidence｜claim_literal｜none` 表达证据来源。正式槽存在但缺失、
无来源或坐标无效时保留 unresolved，不得回退到字面命中。需要全书反查时 MainText 各章完整
提供一次；删除问题的复审同样提供原问题材料与当前正文。legend、主体、证据、正文和完整
binding 一并进入 workpack SHA。

Commander 传输面由不可变工作包确定性投影为 `stage2-global-review-delta-v2`：待审项进入
subject cards，理解它们所需的已接受主体进入只读 context cards；其余接受只保留数量和摘要。
每张卡保留 item、path、hash 及 `subject_ref` / `evidence_ref`，可从内容寻址 catalog 完整回读；
不再复制同义 `claim_evidence_index`。正文各章仍只出现一次，已接受上下文不变成新的待审项。
传输裁剪不改变原工作包、binding 或 workpack SHA。

正式逐项决定入口为：

```powershell
python -m novel.cli run stage2.global <sid> `
  --review-decisions <书内或服务私有目录中的 GlobalReviewDecisionSet.json>
```

同形服务入口为 `POST /api/novels/:sid/stage2/global-review`，对应 GET 提供只读状态。决定有两种
关闭式形状：

- `decisions[]`：每项一次，携非空 reason、显式 evidence 和 expected_subject_sha256；仅 UNVER
  可附 generation_disposition，且仍受服务端完整组白名单限制。
- `blocking_scope`：绑定当前 source SHA、完整 item/hash 范围 SHA、项目数、正文章全集及实际
  阅读的 workpack SHA。未列入 decisions 的项采用明确 PASS 或 UNVER，代码展开逐项保存，
  FAIL/UNVER 例外不能丢失。

`--emit-review-template` 默认 UNVER；actor、reason 与 workpack SHA 的占位不能直接提交。
服务端锁内重建当前材料，旧 SHA、缺项、未读正文声明或来源损坏均拒绝。旧版本只能免费获取
当前模板后重新审阅，不能通过重写版本号继承接受。

### 2.3 统一接受、typed 编辑与恢复

正常首抽的一次 Commander scope 决定经免费 CLI 落盘后，同时保存 Global 节点接受，不额外
购买一轮接受。正式事实通过不能顺带接受已删除问题：统一 Commander 必须明确审阅当前
`removed_problem` 的原主张与处置。当前义务由本轮决定与可证明继承完整覆盖，较新否定遮住
较早接受；删除或回滚不能抹去问题。

继承比较业务身份与实际证据，不把 `context_refs` 阅读导航当作新业务。原历史 scope 仍校验
完整摘要；继承保留原 actor、理由及决定来源，仅绑定当前义务的导航。新增主体产生自己的待审
义务，原问题与引用材料不得因导航调整丢失。未经决定覆盖的新语义继续待审。

单 owner 修订分别使用 CharactersEdit 或 GlobalEdit；同一 Commander 回答可通过
`Stage2NodeReviewRequest.core_edits` 同时提交两侧 typed 编辑。审批先整体预检，保存原决定与
冻结 prepared，再由 `stage2.global --apply-core-edits <request_sha256>` 免费按 owner 提交。
两侧 CAS、正式 ID 映射和完成标记在重启后继续有效；补尾不覆盖第三方修改，不重复分配身份，
不重购联合首抽。字段、服务边界与恢复凭证见 [stage2_review.md](./stage2_review.md)。

独立 GlobalEdit 的 `post_edit_review` 绑定修前未闭合范围；保持原 hash 的旧红项须显式复判，
新打开项才可使用明确默认决定。原样/no-op、未闭合 blocking、身份漂移、无进展或预算耗尽均
关闭。隔离仅改变下游内存视图：原 Global、GlobalReview 与 UNVER 审计保留，消费视图删除完整
秘密组并重新通过正式 schema，不能顺带删除被引用关系或规则。

Global Commander 每个持久工作单元基础最多四次内容调用，包含首次及格式/内容纠错；换候选、
run、epoch 或物理进程不重置。恢复和机械重验免费。Global 当前有效且必要义务接受后才允许
Blueprint；Blueprint 的按章及跨章审阅、知识闭合仍属于独立工作单元，见
[stage2_review.md](./stage2_review.md)。

两种事实决定形状都显式声明 actor，它是审计声明，不是认证。CLI exit 0 表示当前 blocking
事实面 generation-safe，显式隔离的 UNVER 仍不能冒充 approved；exit 1 表示决定已落盘但仍红，
gate 保持关闭。Inbox 保留非 PASS 项和隔离披露，Global 页面提供逐项、批量及 typed 订正入口。
