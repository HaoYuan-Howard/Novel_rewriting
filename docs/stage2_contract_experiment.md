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
- 层2（beat/boundary patch）**刻意不预建**：等七章高难包付费冒烟的真实
  finding 分布出来再立（数据驱动）。

## 三、A/B 操作步骤与配对纪律

**付费前必做（一次性）**：
1. 付费产物快照：`python tools/backup_snapshot.py`（private profile 指主仓
   outputs，按该工具的确认流程走）；
2. worktree checklist：从主仓复制 `.env`；`NOVEL_CACHE_DIR` 指主仓 `.llm_cache`
   （worktree 不带缓存，miss 全是真钱）；
3. 零成本重放先过：`-m corpus` 跑 `tests/test_stage2_replay_v2.py` +
   `python tools/replay_stage2_contract.py --base <outputs> --md-out <留档>`。

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
