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
层0 确定性编译恢复（compile_v2 内建，0 成本）：
      · normalized 命中、place 继承、编号
      · **护栏式边界外扩**：止段没盖住本拍自己的 hinge / 末拍差几段到底
        ⇒ 外扩 ≤2 段（不挤空下一拍、不抢它的锚点、外扩后仍唯一命中），
        逐条落 `_provenance.boundary_nudges`（7A 实测：4 例 HARD 全由它接住）
层1 stage2.blueprint --patch     字段级（量级=条）：
      · hinge_ambiguous  → 候选选择（代码枚举命中位，模型只挑 candidate_id）
      · hinge_anchor_void → 短语重给（拍内原样唯一，validator 当场验）
层3 stage2.blueprint --chapters  整章重生成（帽子外的 drift / span 族 / cascade）
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

| 读数 | ir1×Flash（层0 外扩后） | 盘上 v3×Pro |
|---|--:|--:|
| 拍 | 24 | 40 |
| hinge | 40 | 48 |
| 定位成功 | **40 / 40** | — |
| 逐字命中（exact / normalized） | 37 / 3 | — |
| `hinge_ambiguous`（多处命中） | **0** | — |
| `hinge_anchor_void`（编的短语） | **0** | — |
| `hinge_drift` | **0**（外扩前 3） | 0 |
| 末拍不到底 | **0**（外扩前 1） | — |
| 层0 边界外扩 | 4 行（逐条落账） | — |
| 合并稿新增 HARD | **无** | — |
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
⇒ 裁决：**层0 护栏式边界外扩**（`compile_v2.py::BOUNDARY_NUDGE_MAX_PARAS`）。
把**同一批已付费的响应**过一遍新编译器：4 例 HARD → **0**，四行外扩值与手算
逐例相同（12→13 / 38→39 / 9→11 / 24→26），**0 次新增付费调用**。
`REPAIR_ROUTE` 里「drift 一律整章重生成」保持不动——它现在管的是**帽子之外**
那些真的切错了的情形。

### 二轮：prompt 收紧被实测否决（同 6 章重付一轮）

同批加过一条「`end_para` 必须盖住你自己在 event 里写到的最后一样东西」，重付一轮：

| | 拍 | event 字数 | hinge | drift |
|---|--:|--:|--:|--:|
| 原模板 | 24 | 2274 | 40 | 3 |
| 加了那一条 | 18 | 1890 | 34 | 0 |

drift 确实压到 0，但**六章的 event 字数无一例外都降**（符号检验 p≈0.016 ⇒ 不是
采样噪声的方向；本臂 temperature 未固定，单章 n=1 不可归因，**方向的一致性**才是）。
ch15 把 5 拍并成 2 拍，「学!那你就好好学!」「黎玄在收徒大典上随手一指」
「每次都等我走了才偷偷吃」等 5 条 hinge 直接消失——模型满足那条规则最省力的办法
是**把 event 写短、把拍并粗**，正好落进模板自己警告过的「把好几件事塞进一条，
等于把它们从清单上划掉」。⇒ **撤回，模板恢复逐字节原样**（那一轮的响应仍在缓存里，
换回去零成本）。教训记在 `prompts.py::_BLUEPRINT_TPL_V2` 上方，防止有人再加回来：
**不拿抽取的完整性去换定位的正确性**——后者代码免费做得到。

## 七、7B stage2 单本全跑（2026-08-21，无情道 23 章）

臂 base：`outputs_ab_ir1/`（只拷 stage0 / stage1 / stage2 的 global 三件套 ⇒ 是**首跑**
不是定向重生成；主仓那本已跑到 stage7，就地跑会让整条下游链过期）。
6 章命中 7A 的缓存，实付 20 次（ch5 闸拒重采两次 + 摘要批 1 次）。

**产物落盘，0 HARD**：66 条 ok、4 条命中全是 INFO（`event_early_name` /
`place_inherit_without_base` / `present_continuity` / `present_ungrounded`，
都归人工待办族）、2 条 unver。`verbatim_failures` **0 处**，
`hinge_ambiguous_pending` **0 处**，`boundary_nudges` **4 处**（全在 ch13/ch15，
即 7A 那两章；另外 17 章一次外扩都不需要）。

| 读数 | v3×Pro | ir1×Flash | |
|---|--:|--:|--:|
| 拍 | 147 | 95 | −35% |
| hinge | 189 | 135 | −29% |
| hinge 定位成功 | 189/189 | **135/135** | — |
| 有地点的拍 | 132 | 54 | **−59%** |
| event 字数 | 8570 | 7145 | −17% |
| summary 字数 | 3606 | 3396 | −6% |
| 字/拍 中位 | 69 | 124 | +81% |
| 付费调用 | 23 | 20（+7A 的 7） | — |
| completion tokens | 206 393 | 112 144（+7A 的 44 640） | −24% |

`beats_coarse` **不红**（中位 124 ⇒ 3 倍线 373，最粗章 224）——7A 里那两次命中确实
是混架构假象，本节坐实。

**下游读取面未动**：把主仓 stage3 拷进臂后 `stage4.plan --dry-run` exit 0，
system 5476 字 + user 全书装配完整，95 拍全部映射成 `pNN_NN` 源内容单元。

**两项待判（判据抓不到，只能人读或跑 stage4）**：

1. **拍数 −35%**。拍是 stage4 的规划单位；23 章里 ch20 从 4 拍塌到 1 拍、
   ch8 从 8 塌到 3、ch12 从 5 塌到 2。每拍反而多带了 hinge（1.29→1.42 条/拍），
   所以不是「材料丢了」而是「切得粗了」——但 stage4 首过率会不会因此掉，本节答不了。
2. **place −59%**（每拍密度 0.90→0.57）。ir1 给了 `P_UNKNOWN` 这个合法出口，
   模型就用了；v3 那边是自由文本，模型倾向于总填点什么（ch6 八拍全填「床上」）。
   哪种对下游更有用，要看 stage4 怎么用 place。

⚠ 口径：对照臂仍是 Pro×v3，**模型与契约两个变量同动**，token 差不能单独归给契约。

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
