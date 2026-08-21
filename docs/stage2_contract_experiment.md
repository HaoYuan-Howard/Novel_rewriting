# stage2 契约实验手册（bounded-fidelity-v3 ⇄ extraction-ir-v1）

自足入口：本页给「跑 A/B、修失败、审读数」的操作者，一页说清双契约怎么选、
钱怎么不白花、读数从哪来。设计依据与逐步实施记录见 `Stage2_improvement.md`
（仓根，设计书）与各 commit message；判据与产物契约的权威在
`docs/schema/stage2.md` 与 `contracts/`。

## 一、双契约是什么、怎么选

`stage2.blueprint` 有两套**响应契约**（产物契约一个字节不动，下游 stage4/API 无感）：

| | `bounded-fidelity-v3`（默认） | `extraction-ir-v1` |
|---|---|---|
| beat 定位 | 模型交 `start_para + end_para` | 模型只交 `end_para`（起点代码推导，末拍必须到底） |
| hinge | `kind + para + phrase`（段内定位） | `kind + phrase`（拍区间内**原样唯一**定位） |
| place | 自由文本 | 判别三态 `P_EXPLICIT / P_INHERIT / P_UNKNOWN`（继承由代码做） |
| summary | 模型逐章自带 | **书级摘要批**独立一调（events 是唯一语义源） |
| 编译器 | `novel/stages/stage2/normalize.py` | `novel/stages/stage2/compile_v2.py` |

选择口（与 `DEEPSEEK_MODEL` 同一条纪律——给 A/B 显式拧一次，不是随手改的变量）：

```text
NOVEL_STAGE2_BLUEPRINT_CONTRACT=extraction-ir-v1   # 缺省 = bounded-fidelity-v3
```

产物 `_provenance.context.architecture` 会如实记下来；定向重生成/`--patch`
按**基线产物**的架构选契约，配置与基线不一致会显式终态（先整跑换架构）。

⚠ **schema_mode 声明**：DeepSeek V4 官方 API 只支持 `response_format=json_object`
（`json_schema` 被拒，2026-08-20 文档探针）。两套契约都是
**prompt 约束 + 本地 pydantic 校验**，不是 constrained decoding——
实验结论不许把「枚举前缀带来的改善」归因给 decoder 约束。

## 二、失败怎么修（ir1 的恢复阶梯）

```text
层0 确定性编译恢复（compile_v2 内建：normalized 命中、place 继承、编号）
层1 stage2.blueprint --patch     字段级（量级=条）：
      · hinge_ambiguous  → 候选选择（代码枚举命中位，模型只挑 candidate_id）
      · hinge_anchor_void → 短语重给（拍内原样唯一，validator 当场验）
层3 stage2.blueprint --chapters  整章重生成（drift / span 族 / cascade 的兜底）
```

- `--patch` 用**默认缓存策略**（中断恢复不重复付费）；`refresh-write` 只属于
  「明确要新采样」的路（`--chapters` / `--resample`）。
- 有整章级根因时 `--patch` 会显式拒绝并指路 `--chapters`——先整章、后字段。
- **摘要批失败的恢复 = 直接重跑整跑**：批失败发生在落盘前（无半份产物），
  重跑时逐章响应命中缓存、只重付摘要批那一次调用。不给它单独入口是刻意的。
- 层2（beat/boundary patch）**刻意不预建**：等六章高难包付费冒烟的真实
  finding 分布出来再立（数据驱动）。冒烟读数见 §六——四例失败同一个根因，
  且都不需要付费修。

## 三、A/B 操作步骤与配对纪律

**付费前必做（一次性）**：
1. 付费产物快照：`python tools/backup_snapshot.py`（private profile 指主仓
   outputs，按该工具的确认流程走）；
2. worktree checklist：从主仓复制 `.env`；`NOVEL_CACHE_DIR` 指主仓 `.llm_cache`
   （worktree 不带缓存，miss 全是真钱）；
3. 零成本重放先过：`-m corpus` 跑 `tests/test_stage2_replay_v2.py` +
   `python tools/replay_stage2_contract.py --base <outputs> --md-out <留档>`；
4. **高难章包冒烟**（`tools/smoke_stage2_ir1.py`）：整跑之前先用 6 章问一次
   「Flash 按 V2 契约自己写出来的响应长什么样」。它发的是**首轮形状**的请求
   （`--chapters` 定向重生成会把 v3 派生的基线喂进 prompt，那是另一个实验），
   与整跑逐字节同构 ⇒ 这几次响应会被后续整跑的缓存原样接住，钱不花第二遍。
   判据跑在内存里的合并稿上（未动章沿用盘上正式稿），**书目录零改动**。

   ```text
   # 0 成本预览
   python -X utf8 tools/smoke_stage2_ir1.py --base <outputs> --sid <id> \
       --chapters 6,8,13,15,17,18
   # 真发：一次 2 章、一律前台（后台句柄会静默终止且报假 exit 0）
   ... --chapters 6,15 --execute --skip-summary-batch
   # 末批给全部目标章：章响应全部命中缓存（0 元），只有摘要批那一次真发
   ... --chapters 6,8,13,15,17,18 --execute --json-out … --md-out …
   ```

   ⚠ 合并稿只能读**逐章绝对判据**。`beats_coarse` 的分母是**本书自己**的
   字/拍中位数，换掉几章就把尺子一起挪了——它在冒烟里出现或消失都不作数
   （报告会把这类判据单独标出来）。

**四臂**：
- A = Pro × v3（历史基线，复用已付产物，不重跑）；
- B = Flash × v3（历史基线，尽量复用缓存复现）；
- C = Flash × ir1（`NOVEL_STAGE2_BLUEPRINT_CONTRACT=extraction-ir-v1` 真付费）；
- D = **同一份 C 响应** + `--patch`（严格配对，不独立重跑 V2）。

**输入冻结硬约束**：B/C/D 消费完全相同的 `stage1/main_text.json`、
`stage2/global_grounding.json`、模型与 effort；对不上一律不进矩阵
（`tools/ab_compare.py` 出对照矩阵）。gold 用人工核验的高难章小集，
Pro 产物不当 gold（它自身有时序与规则问题）。

**账从哪读**：
- 成本：`outputs/_service/ledger.jsonl`（wire 行带
  `attempt_id / request_fingerprint / response_sha256`）；
- 采纳与闸判：`outputs/_service/llm_audit.jsonl`
  （`gate_decision` / `artifact_adoption` 行；「最终采纳档位」=
  adoption → attempt_id → 成本账行反查，不猜「最后一条 ok」）；
- 修复范围：产物 `_provenance.repairs[].fields`（字段级记具体路径；
  整章重生成恒 `["*"]`）。

## 四、验收指标（优先级）

一级硬指标：`hinge_drift=0` · `hinge_anchor_void=0` · 非法 enum=0 ·
迁移字段导致的整章重生成=0 · stage4 首过率不降 · 高难章人工语义审查不降。

二级经济指标：paid_calls/accepted_chapter · 成本/accepted_book ·
patch_calls/accepted_chapter · 最终采纳降档响应的章节比例。

三级诊断：prompt/completion/reasoning tokens。总付费 token 首轮目标
「不高于 B 臂，争取 −10~15%」；−25% 达成算额外收益，不倒逼模型再少想。

## 五、已有读数（零成本重放，2026-08-21，两本真书）

| 读数 | 无情道 | 逼一个恋爱脑 |
|---|---|---|
| hinge 逐字节回收 | 187/189 | 186/188 |
| 拍内多处命中（歧义率） | 2（1.05%） | 2（1.05%） |
| span 膨胀 | 0/0/0 | 0/0/0 |
| events 拼接 vs 人写摘要体积 | 2.44× | 3.88× |
| 白名单外新增 HARD | 0 | 0 |

结论：hinge 去段号在真数据上无损（差额恰为歧义本身，全部可候选选择修复）；
「中段空隙」在 v3 盘上全是段间换行 ⇒ 连续覆盖对 stage4 尺寸读数零影响；
events 拼接进生产被体积实测否决（书级摘要批是正解）。
留档：主仓 `outputs/_ab/replay_extraction_ir_v1_20260821.{json,md}`。

## 六、7A 高难包付费冒烟读数（2026-08-21，无情道 6 章）

臂 = Flash × ir1（`deepseek-v4-flash` / effort high）。对照是盘上 **Pro × v3**
基线——**两个变量一起动了**（模型 + 契约），token 差额不能单独归给契约；
真正的 B 臂（Flash × v3）这本书没跑过，账本里 2026-08-21 之前一次 flash
蓝图调用都没有。留档：`outputs/_ab/smoke_extraction_ir_v1_20260821.{json,md}`。

| 读数 | ir1×Flash | 盘上 v3×Pro |
|---|--:|--:|
| 拍 | 24 | 40 |
| hinge | 40 | 48 |
| 逐字命中（exact / normalized） | 34 / 3 | — |
| `hinge_ambiguous`（多处命中） | **0** | — |
| `hinge_anchor_void`（编的短语） | **0** | — |
| `hinge_drift` | **3** | 0 |
| 末拍不到底 | **1** | — |
| 有地点的拍 | 17 | 34 |
| 付费调用 | 7 章调用（含 1 次闸拒重采）+ 1 摘要批 | 6 |
| completion tokens | 45 656 | 64 819 |

**一个根因解释四例失败**：三处 drift 与那一处「末拍不到底」，都是模型的
`end_para` 比它**自己 `event` 里已经写到的内容**早收 1–2 段。逐例证据：

- `b13_02` event 写「传送位置随机」，而那句话在第 13 段，`end_para=12`；
- `b15_01` event 写「花杳拍大腿说那就好好学」，那句在第 11 段，`end_para=9`；
- `b15_02` event 写「他说想学剑就当面来找我」，那句在第 26 段，`end_para=24`；
- `b13_05` event 结尾就是「青悠的感动戛然而止」，那是第 39 段，`end_para=38`。

⇒ **错的是那一个数字，不是 phrase，也不是分段思路**（event 与 hinge 自洽）。
四例外扩 1–2 段即可，且逐例核过：不挤空下一拍、不挤掉下一拍自己的 hinge。
现行 `REPAIR_ROUTE` 把 drift 一律送整章重生成，在这份数据上是过度反应。

其余读数：

- **歧义预测被推翻**：重放先验（按 v3 的短语）预计 ~1% 多处命中，实测 **0/40**
  ——ir1 模型自己挑的短语更长（`师尊,第三十二式始终不得其要领,求捞。`），
  「不唯一就多带几个字」那条起了作用。5A 的候选选择在本轮**一次都没触发**。
- **place 三态被如实使用**（`P_EXPLICIT` 8 / `P_INHERIT` 9 / `P_UNKNOWN` 7），
  链头继承 0 例。但第 6 章 5 拍全 `P_UNKNOWN`，而 v3 那 8 拍全填了「床上」
  ——ir1 给了「不知道」这个合法出口，模型就用了。**这是产物质量的实质变化**，
  不是判据能抓的东西，归 7B/人工审查。
- **拍粗了 1.8 倍**（中位；min 1.2×、max 2.67×）。冒烟里 `beats_coarse` 的
  两次命中是混架构假象（见 §三 ⚠）；按同倍率投影到全书，3 倍线从 206 抬到 400，
  最粗章 224 仍在线内 ⇒ 判据大概率不会红，但**拍是 stage4 的规划单位**，
  −40% 的拍数够格成为 7B 的头号观察项。
- 摘要批一次调用产 6 章，长度 63–125 字（validator 界 50–400，prompt 目标
  100–220）——**比 prompt 要求的短**，且第 8/15/18 章明显短于 v3 原摘要
  （99 vs 189、120 vs 228、90 vs 257）。
