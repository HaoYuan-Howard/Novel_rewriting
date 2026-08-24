# stage2 契约实验手册（现役 v2 / v3.1；其余为历史实验）

自足入口：本页给「跑 A/B、修失败、审读数」的操作者，一页说清多契约怎么选、
钱怎么不白花、读数从哪来。设计依据与逐步实施记录见 `Stage2_improvement.md`
（仓根，设计书）与各 commit message；判据与产物契约的权威在
`docs/schema/stage2.md` 与 `contracts/`。

## 一、响应契约是什么、怎么选

`stage2.blueprint` 只保留两套可选择的**响应契约**。v2 把逐章抽取拆成边界与细节两相；
当前默认 v3.1 是一相带召回脚手架和关闭引用的契约：

| | `extraction-ir-v2` | `extraction-ir-v3.1`（默认） |
|---|---|---|
| 逐章调用 | **两相**：先回忆并压缩边界，再在冻结边界上填细节 | **一相带脚手架**：同一响应先 cue 召回、再交完整拍 |
| beat 定位 | 第一相交 `micro_events + beats[].end_para`；两列均递增到底 | 同一响应交 micro cue + 详细 beats；本地守同一边界不变量 |
| hinge | 第二相交 `kind + phrase`，不能改第一相边界 | 候选 id 优先、phrase 兜底 |
| 人物引用 | 在场者从可见 `char_id` 闭集选 | narrator/focalizer 也闭集选择 |
| summary | **书级摘要批**独立一调 | 同左；micro cue 编译前丢弃 |
| 编译器 | `compile_v2_refs` | `compile_v31_refs` |

选择口（与 `DEEPSEEK_MODEL` 同一条纪律——显式拧一次，不是随手改的变量）：

```text
NOVEL_STAGE2_BLUEPRINT_CONTRACT=extraction-ir-v2     # 缺省 = extraction-ir-v3.1
```

`bounded-fidelity-v3`、`extraction-ir-v1`、`extraction-ir-v1.1`、`extraction-ir-v2.1`、
`extraction-ir-v3` 已退役，不再能从配置或修复入口选择。最近一次取值面收窄使
`chapter_blueprints.json` schema v3 → v4；旧 v3 信封与退役 architecture 产物均由
`stage2.blueprint` 付费整跑重建，不迁移、不手补版本。
v1 的历史裁决见 §四、§八与 §十一；v2 翻默认的付费依据与未达成项见 §十四；
v3/v3.1 的预注册与迭代见 §十五～§十七；v2.1 的预注册与双书付费结果见 §十八；
最终两书三契约对照与 v3.1 翻默认的产品裁决见 §十九；
v1.1 退役与 schema v4 裁决见 §二十。

⚠ **schema_mode 声明**：DeepSeek V4 官方 API 只支持 `response_format=json_object`
（`json_schema` 被拒，2026-08-20 文档探针）。两套现役契约都是
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
3. **高难章包冒烟**（`tools/smoke_stage2_ir1.py`）：整跑之前先用少量章检查
   现役契约的首轮响应形状。它发的是**首轮形状**的请求，
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

**一本书（无情道）的结账（2026-08-21，读数见 §六~§十）**：

| 指标 | 结果 |
|---|---|
| `hinge_drift` / `hinge_anchor_void` / 非法 enum | 0 / 0 / 0 ✓ |
| 迁移字段导致的整章重生成 | 0 ✓ |
| stage4 首过率 | 升（A 首轮全废重跑；C 一次调用 + 0 LLM 提升）✓ |
| 高难章人工语义审查 | 通过，留「切得粗」一条已知代价 ✓ |
| 成本/accepted_book | 赢（B 臂 149k 没落盘；C 156k 落盘）✓ |
| 总付费 token vs B 臂 | **+5%，未达成**（目标是不高于 B） ✗ |

⚠ n=1。7C 三本配对 A/B 尚未跑；上表是一本书的读数，不是三本的结论。
⚠ 「切得粗」那一条的归因见 §十一：它是**契约 × 模型的交互项**——在生产默认的
Flash 上契约几乎不影响拍数（+2%），在 Pro 上才压粗 20%。

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

⚠ 本节的对照臂是 Pro×v3，**模型与契约两个变量同动**——下面 §八 补了 B 臂之后，
这里「拍数 −35%」「place −59%」的归因**已被推翻**：大头是模型不是契约。

## 八、B 臂补齐：单变量对照（2026-08-21，同书同上游）

臂 base `outputs_ab_v3_flash/`，`DEEPSEEK_MODEL=deepseek-v4-flash` +
`NOVEL_STAGE2_BLUEPRINT_CONTRACT=bounded-fidelity-v3`，其余与 C 臂逐项相同。

| | A：Pro×v3（盘上基线） | B：Flash×v3 | C：Flash×ir1 |
|---|--:|--:|--:|
| 落盘 | ✓ | **✗ 闸门拦** | ✓（0 HARD） |
| 拦下的判据 | — | `hinge_drift`×3 · `verbatim_failed` | — |
| 拍 | 147 | 93 | 95 |
| hinge（其中定位成功） | 189（189） | 133（**130**） | 135（**135**） |
| 有地点的拍 | 132 | 75 | 54 |
| event 字数 | 8570 | 7529 | 7145 |
| 字/拍 中位 | 69 | 113 | 124 |
| 付费调用 | 23 | 26 | 27 |
| completion tokens | 206 393 | 149 236 | 156 784 |

**归因（B 臂把两个变量拆开了）**：

- ⚠⚠ **本段的归因在 §十一（D 臂）被修正**：只有 B/C 两格时看起来像「拍变粗是模型
  效应不是契约」，补完 Pro×ir1 之后发现那是**交互效应**——契约在 Flash 上确实
  +2%，在 Pro 上却压粗 20%。下面这句「不是契约」**只在 Flash 那一行成立**，
  不许外推。以 §十一 的四格表为准。
- 在 Flash 这一行：B 93 拍 vs C 95 拍，契约几乎无影响；Pro×v3 那 147 拍是另一格的事。
  event 字数同理：−17% 里 −12% 归模型、−5% 归契约。
  place 则是契约的独立责任（见 §十一，两个模型上都掉）。
- **契约的净贡献只有一条，正是要命的那条**：同模型下 v3 有 3 条锚点归错拍
  （`b08_04` / `b13_02` / `b20_01`，「短语不在它指定的那一段里，而出现在别处」），
  整份稿进 `.INVALID`；ir1 是 135/135 全定位、0 HARD 直接落盘。
  这与设计书的判断一致：**去掉段号这一维，模型就不会把段号写错**——
  它交的是短语，位置由代码算。
  （`b13_02` 两臂都出问题：那一处原文本身难切。v3 那边无法自动修，
  ir1 那边被层0 边界外扩接住了。）
- **token 目标未达成**：原定「不高于 B 臂，争取 −10~15%」，实际 C 比 B **高 5%**。
  但 B 的 149k 是**没落盘**的 149k——要救得再 `--chapters 8,13,20` 重生成三章。
  「成本/被接受的书」这一项 ir1 赢，「成本/次调用」这一项没赢。**不要把后者写成前者。**

## 九、stage4 验证（2026-08-21，臂 base，钉 Pro 控变量）

`DEEPSEEK_MODEL=deepseek-v4-pro` 跑 C 臂的 `stage4.plan`，与基线同模型，
只留蓝图契约一维。

| | A：v3 的 stage4 | C：ir1 的 stage4 |
|---|---|---|
| 首轮结局 | **整轮作废**（三档阶梯全被 schema 闸拒，未落盘） | 落 `.INVALID`，1 条 HARD |
| 实付调用 | 4（其中 3 次是首轮全废） | **1** |
| prompt / completion | 106 182 / 76 438 | 23 717 / 24 241 |

那 1 条 HARD 是 `goal_landing`：第 21 章 `end_hook_goal` 指着 `b21_05`
（剑宗长老再争取），而末尾写作单元 `s04` 装的是 `b21_06`（黎玄送行）。
章的收束是后者 ⇒ 改 ref、`--revalidate`，**0 次 LLM** 提升为正式稿，
写作上下文同批重建。⇒ **一级硬指标「stage4 首过率不降」成立**（且实际是升）。

prompt 少 78% 与「stage4 的输入从 147 拍降到 95 拍」一致——但按 §八 的归因，
这笔便宜主要该记在模型头上，不是契约。

## 十、人工语义审查（用户，2026-08-21）

对照页按**原文区间重叠**把两臂逐拍对齐（v3 独有锚点 90 / ir1 独有 30 /
共有约 99；地点被抹平 64 处，分布在 13 章）。用户读后裁定：
**「ir1 确实切得太粗，但影响不大」** ⇒ 一级硬指标「高难章人工语义审查不降」通过，
并留下「切得粗」这一条已知代价（按 §八，它主要是模型带来的）。

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

## 十一、D 臂补完 2×2：拍变粗是**交互效应**（2026-08-21）

§八 只有 B/C 两格时，把「拍变粗」整个记在了模型头上。补上 D（Pro×ir1，
臂 base `outputs_ab_ir1_pro/`，24 次实付）之后这个归因被推翻——**两个因素都有份，
而且互相依赖**。

**拍数四格**：

| | `bounded-fidelity-v3` | `extraction-ir-v1` | 契约效应 |
|---|--:|--:|--:|
| **Pro** | 147 | **117** | **−20%** |
| **Flash** | 93 | 95 | +2% |
| 模型效应 | −37% | −19% | |

**四项读数的效应分解**（行=读数，列=在另一维固定时的变化）：

| 读数 | 契约@Pro（A→D） | 契约@Flash（B→C） | 模型@v3（A→B） | 模型@ir1（D→C） |
|---|--:|--:|--:|--:|
| 拍 | −20% | +2% | −37% | −19% |
| hinge | −30% | +2% | −30% | +2% |
| 有地点的拍 | **−60%** | **−28%** | −43% | +2% |
| event 字数 | −8% | −5% | −12% | −9% |

三条读法：

1. **拍与 hinge 是交互项，不是主效应。** 形状像一个**地板**：Flash 不管哪套契约
   都切到 93–95 就压不动了；Pro 在 v3 下能到 147，换 ir1 掉到 117。可解释为
   ir1 的结构约束（末拍必须到底、两拍自然相接、短语要拍内唯一）占掉了一部分
   推理预算——Pro 有余量所以还剩 117，Flash 本来就在地板上。**这是假说不是结论**：
   本仓没有能直接观测「预算被什么占用」的装置。
2. **地点是契约的独立责任**：两个模型上都掉（−60% / −28%）。`P_UNKNOWN` 是个
   合法出口，两个模型都会用它。这一项赖不到模型头上。
3. **event 字数两个因素都只有个位数影响**——材料总量没怎么变，变的是切法与
   挂锚点的密度。

**对已下裁决的影响**：翻默认**不受影响**——生产默认模型是 Flash，那一行契约效应
是 +2%，而 Flash×v3 根本落不了盘。但「ir1 不影响切分粒度」这句话是**错的**，
不许这么写：换回 Pro 生成的那天它会显影（少切 20% 的拍、少挂 30% 的锚点）。

**D 臂自身读数**：落盘 0 HARD（3 条命中全是 INFO）· hinge 132/132 全定位 ·
`verbatim_failures` 0 · `place_inherit_without_base` 6 · 24 次实付 ·
prompt 152 570 / completion 190 331（比 A 便宜 8%）。

⚠ 每格 n=1，且本臂 temperature 未固定 —— 单章差异读不出显著性，
上表读的是**全书合计**与**方向**。

## 十二、E 臂：Flash × ir1 × **effort=max** —— 否决（2026-08-21）

问题：把思考强度拉到 `max`，能不能把拍切细一点？**不能，反而更粗，而且落不了盘。**

`max` 此前被三处闸关闭式拒绝，本轮为这个实验启用（官方 effort 前缀从
`4744479^:novel/llm/budget.py` 逐字节取回，476 字符 / 79 token，测试当场重量；
它在当时实现中走 `rungs_from_effort` 的覆盖梯 `max→high→low`；2026-08-24 起现役
异常恢复已取消 effort 降级，这里只记录 E 臂发生时的实验形状）。

| 读数 | C：×high | E：×max | |
|---|--:|--:|--:|
| 落盘 | ✓ 0 HARD | **✗ 闸门拦**（`hinge_drift`） | |
| 拍 | 95 | **87** | −8% |
| hinge | 135 | 105 | −22% |
| 定位成功 | 135/135 | 104/105 | |
| 有地点的拍 | 54 | 38 | −30% |
| event 字数 | 7145 | 6851 | −4% |
| 字/拍 中位 | 124 | 137 | 更粗 |
| 付费调用 | 20 | 27 | |
| completion tokens | 112 144 | 182 793 | **+63%** |
| reasoning tokens | 96 475 | 164 621 | **+71%** |

逐章看方向也不一致：变细 3 章、变粗 7 章、持平 13 章（ch15 5→9 是最细的一章，
但 ch2 6→2、ch3 4→2、ch5 4→2、ch23 4→2 大幅变粗），净效果是更粗。

**机制上自洽**：那段官方前缀命令模型「写出完整推敲过程，记录每一个中间步骤、
考虑过的备选与被否决的假设」。多出来的预算花在**推敲叙述**上，不是花在把章切细上——
`reasoning +71%` 而 `event 字数 −4%` 正是这个形状：想得更多，交出来的结构反而更少。
与 FC-13 那次往下测 `low` 的结论同向：**这个节点上 effort 这一维没有可换的收益**。

### ⚠ 这一臂第一次跑是**假的**，值得记一笔

首跑 27 次调用**全部命中缓存**、0 元、产物与 C 臂逐字节相同——因为
`client.py` 在发新请求前会横扫**阶梯上所有档位**的缓存（`_read_cached`，
为「中断恢复不重复付费」而设），而 max 的梯是 `max→high→low`，
C 臂那批 `high` 响应正好把它喂饱了。账本没说谎（如实记 `high`），
但**实验静默地没有发生**。⇒ 每条 effort 实验臂必须配独立
`NOVEL_CACHE_DIR`，跑完核账本 `reasoning_effort` 那一列全是目标档才算数。
守卫已加进 `tools/effort_ab.py` 的 docstring 与预览输出。

## 十三、`extraction-ir-v1.1`：只修地点这一笔契约债（2026-08-21，0 成本层）

### 为什么只改 place

按「把生成侧换成 Flash」这个**真目标**（不是降 token）重排账，四格表里三个缺口
只有一个是契约自己欠的：

| 读数 | A: Pro×v3（旧生产） | B: Flash×v3 | C: Flash×ir1（新生产） | 原缺口 | ir1 补回 |
|---|--:|--:|--:|--:|--:|
| 落盘 | ✓ | **✗ 整份 INVALID** | ✓ 0 HARD | — | **全补** |
| hinge 定位成功率 | 100% | 97.7% | **100%** | — | **全补** |
| 拍 | 147 | 93 | 95 | 54 | +2（3.7%） |
| hinge | 189 | 133 | 135 | 56 | +2（3.6%） |
| 有地点的拍 | 132 | 75 | 54 | 57 | **−21（−37%）** |

⇒ **ir1 解决的是「能不能落盘」，不是「质量够不够」。** 拍与 hinge 是交互效应
（§十一），措辞层面的路已走死——三种廉价解释都被实测排除：不是截断（149 次
blueprint 调用 `finish_reason` **全是 `stop`**，峰值 50,324 tok vs 上限 256,000）、
不是 reasoning 预算（§十二 E 臂 `max` 给了 +71% reasoning，拍反而 95→87）、
不是 prompt 没说清（§六 二轮加压，拍 24→18 **更粗**，p≈0.016）。
而地点那一格 §十一 实测**两个模型上都掉**（Pro −60% / Flash −28%）⇒ 契约的独立责任。

**机制在措辞里看得见**：v3 写「用原文的说法…确实无从判断**才**填 null」——null 是
**最后一档**，「才」是抑制；ir1 把三个 mode **并列陈列**、语义上等权，`P_UNKNOWN`
只剩五个字，抑制没了，`P_INHERIT` 还加了「程序会替你抄」（更省事）。
⇒ 三态把 null 从「最后一档」升成了「三选一」。

### 落成的形状

新契约 `extraction-ir-v1.1`，**与 ir1 只差 system 里 place 那一段**：三个 mode 的
JSON 形状一个字不动，改的是**次序**（并列 → 有序降级链，恢复「才」的抑制），
外加一句「不要为了填满而编地点」。响应模型 / 编译器 / 投影函数 / 摘要批开关
逐个共用 ⇒ 单变量对照。**当时默认未翻**（仍是 ir1）；2026-08-23 的默认已由
§十四所述付费验收翻到 v2，v1.1 仍可经 env 显式选择。

三条守卫钉住它（`tests/test_stage2_contract_dispatch.py`）：两臂差别恰好是
`architecture`/`sys_name`/`system` 三格 · system 的 diff 是**恰好一个连续区块**且
那块讲 place · ir1 老臂字节未被动过。中间那条同时防两个反向失败：**差异为空**
⇒ 换臂等于没换（同一缓存键，实验静默不发生，E 臂踩过）；**差异溢出** ⇒ 归不了因。

### 配套探针 `place_explicit_unlocatable`（INFO）

加压时模型会找最省力的出路，而 place 是**单字段**、没有别的字段可牺牲 ⇒ **唯一的
规避路径是编一个地点**。探针就装在这条路径上：填充率涨了而它同步涨，涨的是幻觉
不是覆盖。

⚠⚠ **第一版设计错了，被自己的基线读数否决。** 初版按「本拍区间」匹配（照抄 hinge
的作用面），在盘上一量：

| 匹配面 | Pro×v3 无情道 132 个有地点的拍里「找不到」 |
|---|--:|
| 本拍区间 | **103（78%）** |
| 本章全文 | 56（42%） |
| **截止本章正文** | **17（13%）** |

78% 的基线等于信号被噪声淹死——地点本来就常写在上文（那正是 `P_INHERIT` 存在的
理由），「合欢宗」「擂台」「灵网」这类场景名更不会在某一拍里再出现一次。
⇒ 改取与 `present_names_invented` 同源的面（截止本章正文）；用「截止本章」而不是
「全书」还有一层语义：只在后文出现的说法本身就是后见之明。

⚠ **就算 13% 那一档也不是幻觉**：17 条逐条看全是**用原文成分组合**出来的
（「武场外」「黎玄的洞府」「前往秘境的路上」「合欢宗门口」），一条凭空编的都没有。
⇒ 这条**判 INFO 且不该升 HARD**，读法是**相对下面这个基线的变化**，不是命中即违规。

**基线（Pro×v3 无情道，经生产落点 `compile_v2` 复算）：17/132 ≈ 13%。**

### 验收判据（按真目标重排，替代 §四）

一级硬指标：① Flash 全链跑通到 `<书名>_rewriting.txt`；② 成稿与主仓 Pro 成稿盲读
对照**不劣**；③ `place` 填充率回升——目标接近 A 臂 132/147 ≈ 90%，**底线不低于
B 臂 75/93 ≈ 81%**（当前 C 臂 54/95 ≈ 57%）；④ 落盘 0 HARD 保持；⑤ hinge 定位
100% 保持。二级读数：`stage4.plan` 首轮结局 · 成本对照 · `place_explicit_unlocatable`
相对 13% 基线的变化 · 拍数与 hinge 数（**只报不判**，它们是下一步裁决的输入）。

⚠ 拍数 −35% 这一格**本轮不攻**（用户 2026-08-21 裁定）：先用成稿质量判它伤不伤，
成稿不劣就接受并扩样本，成稿掉了且能归因到拍粗再上分片抽取
（`end_para` 单边界让片间拼接无缝，v3 的双边界做不到——那是 ir1 尚未兑现的红利）。

### 本节的验证边界

**全部是 0 成本层**：全量非 corpus `pytest` 0 failed（`--junitxml` 留档）；
探针基线经**生产落点** `compile_v2` 复算，不是另写一份统计。
**ir1.1 的 prompt 改动有没有真的救回填充率，本节没有任何付费实证**——
那要等阶段 2 的全链跑（无情道，独立 `NOVEL_CACHE_DIR`）。

## 十四、`extraction-ir-v2`：Flash 两相抽取付费验收（2026-08-23）

### 配对与根因

用户明确授权把《无情道第一她来自合欢宗》全文及派生上下文发往 DeepSeek API，并承担
Pro/Flash Stage2 A/B 费用。三臂使用同一份 `stage1/main_text.json`（正文 SHA-256
`e32f675b2da96174a29b68f190d81d18bc87ebcb61ee2c554e8c2243e714d6d1`）、同一份
`stage2/global_grounding.json`、`high + thinking`；严格对照只改模型或逐章响应契约。
曾有一份 Pro 目录混入 Flash 响应，已从正式对照剔除；A 臂只认重新建立的 clean base。

同契约 `extraction-ir-v1.1` 的模型差异很清楚：Pro 130 拍 / 168 hinge，Flash 91 / 145；
Flash 的在场者 198 项里有 9 项代词，Pro 为 0。把 Flash effort 从 high 拉到 max 后反而只剩
87 拍 / 105 hinge，因此根因不是推理档位不够，而是以下响应形状放大了模型能力差：

1. 一次响应同时承担切分、事件、地点、在场者、hinge 与叙述信息，Flash 会优先交一份
   结构完整但切得过粗的答案；
2. `present_names: list[str]` 要模型生成原文字面，白白打开代词、别名歧义与名册外字面三条失败面；
3. 三态地点把短且便宜的 `P_UNKNOWN` / `P_INHERIT` 与显式地点并列，两个模型都会走捷径；
4. `stage2.global` 的 secret/rule 差距仍在，但它不是本次逐章 v2 的作用面，不能把改善归给它。

### 为什么是「回忆层 + 压缩层」，不是只让模型多切

三轮 7 章付费 smoke 把边界 prompt 的失败形状测了出来：

| 边界方案 | 7 章拍数 | 结论 |
|---|--:|---|
| 只交数字止段 | — | 全书试跑只得 70 拍，且出现整章坍缩；拒绝 |
| 强制逐件微事件 | 105 | 明显过切；拒绝 |
| 只写「局部因果拍」 | 20 | Flash 跳过了内部枚举；拒绝 |
| 同一响应先列 `micro_events`、再压成 `beats` | **41** | 落在过切与坍缩之间，全部通过边界 validator；采纳 |

最终 v2 因而不是让第一相直接交唯一一列边界：`micro_events` 是完整回忆用的中间脚手架，
`beats` 才是下游需要的局部因果单元；两列都必须止段递增到章尾，且每个 beat 止段必须来自
micro-event 止段。第二相收到代码冻结的 beat 边界，只填事实；在场者从截止本章可见的
`char_id` 候选中选择，地点恢复成有据字面或 `null`。最终仍编译成原有
`ChapterBlueprints`，所以 artifact schema/version 与 stage4/API 都不动。

### 完整 23 章结果

| 读数 | Pro × v1.1 | Flash × v1.1 | Flash × v2 |
|---|--:|--:|--:|
| 拍 | 130 | 91 | **126** |
| hinge | 168 | 145 | **169** |
| span 定位 / 原文字数覆盖 | 130/130 · 98% | 91/91 · 98% | **126/126 · 98%** |
| 在场者引用 | 287 | 198 | 276 |
| 歧义 / 名册外 / 代词 / 过早 | 0 / 14 / 0 / 0 | 0 / 9 / 9 / 0 | **0 / 0 / 0 / 0** |
| event 点名但未列在场者 | 25 | 36 | 30 |
| 冷揭示 secret | 0/4 | 1/4 | 1/4 |
| named place | 72/130（55.4%） | 56/91（61.5%） | **84/126（66.7%）** |
| Stage2 HARD 残留 | 0 | 0 | **0** |
| human_only inbox | 2 | 4 | 3 |

v2 首轮唯一失败在 ch17（1 条 hinge drift / verbatim failure），一次
`--chapters 17` 定向重生成后收敛；最终 169/169 hinge 可定位。拍数与 Pro 的绝对差从
39 缩到 4（约补回 90%），hinge 差从 23 缩到 1（约补回 96%）。这些是契约一致性与
信息覆盖的代理读数，**不是人工盲读，也不是普遍质量分数**。

地点仍是明确未达成项：v2 比两个 v1.1 臂都高，但 66.7% 仍低于 §十三预登记的 81% 底线，
不能写成「地点已修好」。secret 冷揭示也没有追平 Pro，因为它来自未改动的全书级响应。

### 成本与裁决

| 成本事实 | Pro × v1.1 | Flash × v1.1 | Flash × v2 |
|---|--:|--:|--:|
| 真调用 / 缓存命中 | 26 / 0 | 25 / 2 | **43 / 6** |
| prompt tokens | 162,768 | 158,365 | **213,880** |
| completion tokens（已含 reasoning） | 216,442 | 136,431 | **271,462** |
| reasoning tokens（completion 子集） | 191,389 | 118,640 | **240,889** |
| 近似墙钟 | 669.4s | 385.0s | **621.0s** |

`completion` 已包含 `reasoning`，严禁相加。v2 以约两倍 Flash completion、43 次真调用换取
接近 Pro 的信息覆盖；它不是降本改造。好处是 621.0s 仍略低于本次 Pro 的 669.4s，且供应商
prompt cache 命中降低了实际输入成本，但缓存命中率受运行时序影响，不归因于模型或契约。

裁决：默认翻到 `extraction-ir-v2`。理由是当前生产默认模型正是 Flash，而 v2 在这一本书上
关闭了自由字面引用失败面，并把拍/hinge 覆盖拉到 Pro 附近；不翻默认就没有真实 consumer
会走到新路径。回退只需改 `contracts/invariants.py::BLUEPRINT_ARCHITECTURE`，旧架构继续登记，
存量定向修复仍按基线架构走。验收边界是 **n=1**：下一本不同题材必须复报同一矩阵，尤其是
地点填充、`present_broad`、secret 与调用成本，不得把本节外推成「Flash 已追平 Pro」。

## 十五、`extraction-ir-v3 / v3.1`：reasoning 降耗预注册（2026-08-23）

### 已知基线与两轮单变量

§十四的同书 Flash v2 是当前成本基线：23 章经一次定向修复后，真调用 43、prompt
213,880、completion 271,462（其中 reasoning 240,889）、126 拍、169 hinge、0 HARD。
v2 的质量提升来自强制“先召回再压缩”，代价是每章边界相与细节相各做一次 high 推理。

两轮必须分开跑，禁止一次把全部改动混成一臂：

1. `extraction-ir-v3` 只改**调用拓扑**：每章一次响应同时返回短 cue 的
   `micro_events` 与完整 `beats`；边界子集、递增与章尾覆盖仍由同一动态 validator 守，
   细节字段、可见人物闭集、摘要批、编译器产物与判据不变。`micro_events` 编译前丢弃，
   不成为第二份持久事件真相。
2. `extraction-ir-v3.1` 只在 v3 上继续改**引用形状**：narration 的姓名改成可见
   `char_id`；hinge 对引号、带单位数字、显式因果短句优先返回确定性候选 id。模型仍判断
   是否为 hinge 与 kind；物件/隐式因果覆盖不到时保留 phrase 兜底，防止降耗变成漏抓。
   候选每章最多 32 项，id 与精确 `SourceSpan` 由正文位置稳定派生。

两臂都只扩 `BlueprintContext.architecture` 的取值面，最终仍落现役
`ChapterBlueprints` v2，故不升 artifact schema。调用拓扑、response model、compiler、
repair projection 与能力开关统一登记在 `novel/stages/stage2/contract.py::ContractSpec`；
预览、整跑、定向修复不按架构名字或可空字段猜分支。

### 付费 A/B 方法与判据（运行前冻结）

未经正文外发与费用授权不运行。获授权后，对同一本 current-schema 书冻结
`main_text` SHA-256、`global_grounding` SHA-256、模型 `deepseek-v4-flash`、`high + thinking`、
代码 commit 与并发；v2/v3/v3.1 各用独立空缓存目录，先 `preview` 留真实 argv，再整跑。
缓存 replay 不得算样本；每臂至少报原始 wire、被采纳 attempt 与产物 hash。

一级质量门（任一失败即否决降耗臂）：

- 正式产物落盘、0 HARD、span 100% 定位、hinge 100% 定位；
- 拍数与 hinge 数各不低于同场 v2 的 95%，不得靠粗切或少抽换 token；
- 在场者的歧义/名册外/代词/过早继续全为 0；v3.1 的 narrator/focalizer 清单外为 0；
- named place 不低于同场 v2 超过 5 个百分点；人工抽查重点看 cue→beat 压缩是否漏掉
  问答、决定、局部结果，以及候选 id 是否诱导模型只抓容易枚举的 hinge。

降耗主指标按**通过质量门后的被采纳整本**比较：总 reasoning tokens、reasoning/章、
completion（已含 reasoning）、真调用、重采次数与墙钟。v3 相对 v2 的预登记成功线是
reasoning 至少下降 25%、正常无重采拓扑从约 `2N+1` 降为 `N+1`；v3.1 只有在质量门
不退且 reasoning 不高于 v3 时才算增益。另报 v3.1 的 candidate/phrase 使用率、候选覆盖率、
候选表输入 token；若候选表反而抬高总 token，就保留闭合 narration、撤候选 hinge，不捆绑裁决。

### 当前状态

三臂付费 A/B 已于 2026-08-23 串行执行，结论是 **v3 与 v3.1 均不得翻默认**。
v3 的 reasoning 相对同场 v2 下降 69.6%，但 hinge 只保留到 v2 的 85.7%，且关闭人物引用
新增 6 个歧义与 2 个名册外值，一级质量门失败；v3.1 的全部模型调用完成后在编译正式产物时
因空 `SourceSpan.normalized` 触发 schema 异常，产物未落盘，独立 `--check-only` 亦以 exit 2
确认缺失。当时默认继续 `extraction-ir-v2`；后续施工、重验与最终产品裁决见 §十六～§十九。

### 付费 A/B 实测结果（2026-08-23）

**授权与冻结。** 用户明确授权将《无情道第一她来自合欢宗》正文及 Stage2 上下文发送至
DeepSeek API，并承担 v2 → v3 → v3.1 三臂费用。三臂使用
`deepseek-v4-flash`、初始 `high + thinking`、并发 32、独立空缓存，按该顺序串行运行；
账本 `cached=false` 为 118/118。冻结输入为：

- `stage1/main_text.json` SHA-256
  `e32f675b2da96174a29b68f190d81d18bc87ebcb61ee2c554e8c2243e714d6d1`；
- `stage2/global_grounding.json` SHA-256
  `fc6ea73305eb544b9d3107dec6bdec0221f7a7c385634e96a1a607507faeda1d`；
- HEAD `307067afb2e73596fe58df840ca9ca56e5e75e39`，三臂账本记录同一运行时代码身份
  `307067afb2e73596fe58df840ca9ca56e5e75e39-dirty.5d50cda7778d8ec4`；冻结时 tracked diff
  的 Git blob hash 为 `873063f811ed299e39380ac12a24ec7d76a09283`，未跟踪新文件
  `novel/stages/stage2/references.py` 的 SHA-256 为
  `939a8229e16a7cb4cfe73c1fb92c0469d6f96da4323fb892f3eb9ff7788fc66b`。

**成本与运行。** completion 含 reasoning；墙钟是该节点账本首尾 span，不把三臂之间的操作间隔
混入。v3 的 3 次 wire 因同一内容闸连续失败由既有阶梯从 high 落到 low，其余 34 次仍是 high；
这不是配置静默漂移，审计中有 3 个 fallback step。v2 与 v3.1 的 wire 全部是 high，三臂
thinking 全为 true。

| 臂 | 真调用 | prompt | completion | reasoning | reasoning/章 | 内容闸拒绝 | effort wire | span |
|---|---:|---:|---:|---:|---:|---:|---|---:|
| v2 | 56 | 265,490 | 414,244 | 370,647 | 16,115 | 3 | high 56 | 957.2s |
| v3 | 37 | 238,192 | 135,434 | 112,731 | 4,901 | 10 | high 34 / low 3 | 421.2s |
| v3.1 | 25 | 159,142 | 187,733 | 161,218 | 7,009 | 1 | high 25 | 322.7s |

v3 相对 v2 的 reasoning 下降 **69.6%**，越过 25% 成本线；但该线只对“先通过质量门的
被采纳整本”有效，所以不能据此判成功。v3.1 reasoning 又比 v3 高 **43.0%**，即使忽略未落盘，
也不满足“不高于 v3”。

**一级质量门。** v2 初跑后定向修复 `6,14,16`，再修复第 6 章，最终 exit 0；v3 初跑后
定向修复 `12,16`，最终 exit 0。两份正式产物均经独立 `--check-only` exit 0。v3.1 在 23 章
与摘要调用均完成后，编译第 19 章第 5 拍的第二条 hinge 时得到空
`source.normalized`，`ChapterBlueprints.model_validate` 抛错、运行 exit 70；不修改冻结代码，
也不把缓存 replay 冒充本轮正式样本。

| 质量项 | v2 | v3 | v3.1 | 预注册裁决 |
|---|---:|---:|---:|---|
| 正式产物 / HARD | 已落盘 / 0 | 已落盘 / 0 | 未落盘 | v3.1 失败 |
| beat | 127 | 136（107.1%） | 不可得 | v3 通过 |
| hinge | 161 | 138（85.7%） | 不可得 | v3 失败（需 ≥95%） |
| span 定位 / 字符覆盖 | 127/127 / 98% | 136/136 / 98% | 不可得 | 已落盘两臂通过 |
| 人物歧义 / 名册外 / 代词 / 过早 | 0 / 0 / 0 / 0 | 6 / 2 / 0 / 0 | 不可得 | v3 失败 |
| named place | 95/127（74.8%） | 115/136（84.6%） | 不可得 | v3 通过，不降反升 |
| artifact SHA-256 | `e8af332087585902795a4a6c629eaca9a156dcbbe0237fbc84520b8a52f29322` | `ec815ff7242418508cde6d889d3a9a4750050cd79ebb222958cdc51d26af7306` | 无 | — |

人工抽查第 6、8、12、16、22 章的 event 切分：v3 没出现足以推翻硬闸结论的系统性问答、决定或
局部结果漏失，但同一臂同时出现把局部动作合并得更粗和把短动作拆得更碎两种漂移；这与总 hinge
回吐相符，不能仅凭 beat 数上升判质量不降。v3.1 没有正式产物，candidate/phrase 使用率、候选覆盖率
与候选表输入 token 均**不可得**；在这种状态下分析候选偏置会把未被正式 compiler/adoption 接受的
中间响应误当成成品证据。

**wire、采纳与复算证据。** 原始 wire 的唯一成本源仍是各臂 `_service/ledger.jsonl`，内容闸与
采纳链在同目录 `llm_audit.jsonl`。其 SHA-256 分别为：v2 ledger
`2bb7c61d78899133b386b899b71506d7852533e08391fc4901481a1839c5b206`、audit
`41fac6e8b9ed46c3502703e85563204751adaa168cfcf55590157a9512af9c3e`；v3 ledger
`f750952069d27f5d71209c9b1b53de15f7e7423c96fd47dfe7888418f0e257c0`、audit
`6fa9431f50d07ff77d29d8e1e56f60cde547cdcd10762a2c0a639b8f4f3e09c8`；v3.1 ledger
`670514465fabc47e102686137b3bb6896d3e447897c512eb6ba19312f4c8a40f`、audit
`7cb7e13b17a6f6746b2a151370153c6266c87a43ee265774a11eea90ccd8313e`。
最终修复落盘的 `artifact_adoption` 为：v2 第 6 章 `c59205827e9c46e0`、摘要
`717307e9582d4acf`；v3 第 12 章 `cdc443b93d4a4ec0`、第 16 章
`d4c5b34a902e45e2`、摘要 `ba5d770f16da43c1`；v3.1 因正式产物未形成，没有 adoption，不能
伪造一个“被采纳 attempt”。聚合复算文件 `outputs/_stage2_flash_quality/v3_ab_20260823/ab_compare.json`
的 SHA-256 为 `aea9188d8b56423ed3575522587f5344821d1ad9fc32c784a8d3c73ce5101149`。

### `v3.1-fixed` 单臂真实诊断（2026-08-23）

本节是用户随后指定的**单臂诊断**，只回答「v3.1 契约修好后能否形成正式产物、真实表现
怎样」。它没有在同一份修复后代码上重跑 v2 / v3，故**不得覆盖上面的正式三臂 A/B，
也不得据此翻默认**。

**零成本修复与回归。** 旧候选生成器把 `「?」`、`「......」` 这类 `text` 非空、但
`content_only(text)` 为空的纯标点引用放进候选表；旧 v3.1 恰好选择 ch19 的该项，compiler
复制空 `normalized` 后才在最终 `SourceSpan` 建模处 exit 70。修复是在候选排序、去重与 32 项
截断**之前**剔除这类项：不能把空串洗成证据，也不让无效项占候选额度。真书重建后为 23 章、
243 项候选，逐项都能通过正式 `SourceSpan`。新增生产契约回归同时钉住纯标点被删与有效引文
仍保留；Stage2 定向 66 项、冻结契约五文件 325 项、全量非 corpus 回归均 exit 0。

**冻结与运行。** 正文、全书底座、模型、并发与初始档位沿用上面的冻结值；另建空缓存
`stage2_v31_fixed_20260823`，只执行 `extraction-ir-v3.1`。账本记录运行时代码身份
`307067afb2e73596fe58df840ca9ca56e5e75e39-dirty.fd663a1a0849a8d0`；修复后的
`novel/stages/stage2/references.py` SHA-256 为
`419e18a50e03e06560726a7a8189dcf49a25da74fa97d250dd06ec5f996635b3`。主跑 exit 0，
独立 `--check-only` 亦 exit 0；产物 fresh/match、Stage2 为 AMBER（0 HARD / 4 INFO / 2 UNVER）。

| 诊断项 | `v3.1-fixed` | 与预注册门的关系 |
|---|---:|---|
| 正式产物 / HARD | 已落盘 / 0 | 通过结构门 |
| 真调用 / 本地缓存 | 28 / 0 | 23 章 + 摘要 + 4 次同档重采 |
| prompt / completion / reasoning | 177,625 / 193,762 / 163,987 | completion 已含 reasoning |
| effort / thinking | high 28 / true 28 | 0 次 low，配置未漂移 |
| span / 字符覆盖 | 134/134 / 98% | 通过 |
| beat / hinge | 134 / 175 | 分别为同场 v2 的 105.5% / 108.7%，通过数量门 |
| 人物歧义 / 名册外 / 代词 / 过早 | 6 / 0 / 0 / 0 | 歧义不为 0，一级质量门失败 |
| named place | 105/134（78.4%） | 高于同场 v2 的 74.8%，通过 |
| candidate_id / phrase | 149 / 26（85.1% / 14.9%） | 兜底真实被用到，未被候选完全吞掉 |
| 墙钟 span | 346.1s | 只作本臂读数 |

149 个 candidate 引用逐章均不重复，占 243 项候选表的 61.3%；23 章响应形状为
8 章 candidate-only、14 章 mixed、1 章 phrase-only。按固定 DeepSeek V4 tokenizer 复算，
下发的候选 JSON 合计 5,338 token，连同块标题与约束文字为 6,465 token（中位每章分别
236 / 285 token）。最终 `verbatim_failures=[]`，所以 175 条 hinge 全部进入正式 compiler/adoption，
不是拿未采纳的中间响应算覆盖率。

**空正文与降档。** 本臂 28 次 wire 中有 7 次 `content=''` 且答案在 reasoning：4 份从
reasoning 取 JSON 后通过与正文相同的 `json.loads → require → model_validate → validator`
四道闸并直接采纳，3 份因章尾或 candidate 所属拍不合法被拒、在同一 high 档重采成功；另有
1 份正常正文被 validator 拒后同档重采。全程没有 low。说明 `answer_lost` 捞回机制由公共
`LlmClient.ask_json` 在 Stage2 **真实复用且按契约关闭式工作**，不是 Stage4 专属旁路。
对照旧 v3 的 3 次 low：三次都紧跟在各 purpose 的第二份 high `answer_lost` 仍未过闸之后，
是既有阶梯的显式 fallback，不是模型配置被静默调低。

**裁决。** 修复证明 v3.1 契约本身可运行，但仍不得翻默认：其 reasoning 比正式 v3 的
112,731 高 **45.5%**，不满足「不高于 v3」；人物歧义又违反一级质量门。候选引用确实把
hinge 从 v3 的 138 条恢复到 175 条，说明这条设计不是单纯漏抓，但它尚未同时换到质量与降耗收益。
正式产物 SHA-256 为
`694b2c97bd94ac3bb0d8bf8f788c4dc070b021718476a919acb69ece934bd80d`；本臂 ledger / audit
SHA-256 分别为 `11f192627c8958f6c64feebc63de32a8edbbcc107159e3657454b16b6d5fbbc9` /
`c93103359e11b298cf384f7b1e3e27b052bb1219b3b027a39639f2a598f180a3`。描述性复算文件
`v31_fixed/diagnostic_compare.json` 的 SHA-256 为
`fb30bed2785b6ffd24ff75a18b0783d8061ef87fa4a54e3669c562fe7d85d6b3`；它跨了代码冻结，
只作诊断对照，不是新 A/B 矩阵。

## 十六、`extraction-ir-v3.1` Flash 默认候选：误报复核与双书预注册（2026-08-23）

### 契约边界与旧证据重判

本轮目标是把 Stage2 **响应契约/默认架构**候选收敛到 `extraction-ir-v3.1`；盘上的
`ChapterBlueprints` 仍由 `contracts/schema/stage2.py::ChapterBlueprints` 与
`contracts/schema/__init__.py::ARTIFACT_SPECS` 登记为 artifact schema **v2**。响应 JSON 先由
各自 compiler 归一成同一个 v2 artifact，故翻默认不构成盘上 schema 破坏，也不触发迁移。
`extraction-ir-v2` 继续留在注册表，供显式回退与存量产物重检。

§十五的“6 项人物歧义”经逐条复核后不是模型选错角色。6 项反投影字面都是“师尊”，而响应中的
`present_char_ids` 已分别唯一指向 `char_002` 或 `char_006`；旧判据只在架构名恰等于
`extraction-ir-v2` 时读取关闭引用，错误地把 v3/v3.1 重新按开放字面匹配。判据改为从
`novel/stages/stage2/contract.py::CONTRACTS` 的 `uses_character_refs` /
`uses_narration_refs` 派生后，在隔离复制的 `v3.1-fixed` 正式产物上执行真实
`stage2.blueprint --check-only`，人物歧义/名册外/代词/过早均为 **0/285**，exit 0；历史证据
目录没有被改写。这个结论消除了一级门的假红，但不自动证明 hinge 质量。

### 175 条 hinge 的盲序抽样

在揭示 A/B 身份前，按响应中候选密度选 ch16/ch19/ch21，按 phrase 密度补 ch1/ch18，并与冻结的
Pro × `extraction-ir-v1.1` 逐章比较。样本覆盖 v3.1 的 **63/175** 条 hinge，其中
candidate 52/149、phrase 11/26；它是定向风险抽样，不是假装随机总体或 ground truth。

- ch1 的 7 条基本有效，v3.1 的事件颗粒度有增益；ch18 大体可用，但出现一条跨换行截断的 phrase
  和少数边缘标题/对白；
- ch16 把担忧、身份揭示与补救提议粗并在 4 拍，且把普通数量、整段对白与其中“十日”重复抬成
  hinge；这是抽样中明确的粗切反例；
- ch19 的 8 拍比 Pro 的 5 拍更清楚，但候选表从“第二天/下一秒”截出“二天/一秒”，普通问句与
  辱骂也被批量选中；
- ch21 同一因果句/问句与其中“三个”“五十年”重复选择，普通对白密度偏高。

机械嵌套统计为 v3.1 **3 对**、Pro **1 对**。因此 175 既包含真实召回，也包含已确认的过度抽取，
不能据数量宣称整体质量超过 Pro v1.1；单书、单次定向审查也不足以外推。修复选择保留
candidate→确定性 `SourceSpan` 的架构收益，而不是撤回 v3.1：候选生成不再从“第二天/下一秒”
产生后缀伪词；逐章 prompt 明说候选表不是预筛结果，恢复四类 hinge 的语义边界，禁止批量选择、
非因果数字以及整句/子串重复。

### 冷揭示差距

Pro 的全书秘密表保留了“黎玄从收徒当天就知道主角来自合欢宗”（setup ch1、reader reveal ch16）；
`v3.1-fixed` 却把“主角真实身份与任务”登记成 secret，并在 ch1 就 reader reveal。后者是开篇已经
直接告诉读者的前提，只是世界内其他角色不知道，应落在 `character_known_by`，不是读者秘密；它还
挤掉了真正的后置揭示。此缺口属于 `stage2.global`，与 ChapterBlueprint 响应架构正交。全书 prompt
现明确“第一章明牌不是秘密”，并要求从后文“其实/原来/一直/早就/从一开始/收徒那天就知道”反扫
会改写前文理解的后置揭示。

### 零成本冻结前验证

上述修改同时钉进生产回归：v2/v3/v3.1 的关闭在场者引用共用一组测试，v3.1 narration 关闭引用
另测；候选后缀、hinge 反批量/反嵌套规则与冷揭示提示均有直接断言。Stage2 定向四文件共
**118 项非 corpus 回归 exit 0**；冻结契约五文件 325 项、完整非 corpus 回归与前端
seams/typecheck/build 也全部 exit 0。前端两项编译只读复用了同锁文件主 worktree 的
`node_modules` 临时目录联接，验证后已删除，未安装或改写依赖。没有
`NOVEL_CORPUS_MANIFEST`，这些结果都不得称为 corpus 全量绿。

### 双书付费验收预注册

本轮不再要求 v3 成为 v3.1 Flash 翻默认的陪跑臂，也不等待 `v3.1 + Pro`。现有同书证据已经回答
v2/v3/v3.1 的成本拓扑；新钱只用于验证修复后的目标 consumer。两本书均使用冻结代码、Flash、
thinking=true、high、独立空缓存；先 `preview`，再按真实 argv 运行并从 `_service/ledger.jsonl`
统计调用/token，禁止把 usage、日志或旧缓存混加。

1. 当前书 `无情道第一她来自合欢宗`：重跑 `stage2.global` 后再跑
   `stage2.blueprint=extraction-ir-v3.1`。global 与 blueprint 成本分开报告，blueprint 可与冻结的
   Pro v1.1、Flash v2、旧 v3.1 对照；新 global 不能偷并进旧 blueprint 口径。
2. 异构书 `偶像公寓打工日常`：18 章、现代背景、对话密度高，结构与仙侠短书不同；复用其已验证的
   stage0/stage1，只生成新的 global 与 v3.1 blueprint。它用于验证方向稳定，不冒充第二份 Pro A/B。
3. 每本都先过正式 schema、fresh/match、0 HARD、beat span 100%、hinge 可定位 100%，关闭人物引用
   四项全 0；不得以放宽判据、减少 hinge 或粗并 beat 达标。
4. 当前书的 global 必须删掉“ch1 已明示身份”伪 secret，并恢复“黎玄早已知情”的 ch16 后置揭示；
   第二本逐条核 reader reveal 不是开篇明牌，也不能把世界事实发生时间误作读者揭示时间。
5. 每本按**新响应**重算候选/phrase 密度，盲序审查候选最密 3 章与 phrase 最密 2 章；明确的普通
   寒暄/问答、非因果数字后缀、整句/子串重复必须为 0，不能出现系统性假 hinge、重大漏事件或
   ch16 式跨场景粗并。机械嵌套只作筛查，每一对仍人工裁决。
6. 成本门按节点报告真调用、prompt/completion/reasoning 与墙钟；当前书 blueprint 应继续显著低于
   Flash v2，并不劣于 Pro v1.1 的既有 completion/reasoning 优势。第二书只要求没有异常重采或
   token/章反弹，不从不同正文硬算百分比优胜。

本节当时预注册：只有两本同时通过 3–6，才将
`contracts/invariants.py::BLUEPRINT_ARCHITECTURE` 改为 `extraction-ir-v3.1` 并验证默认真实
consumer；任一本失败都保持 v2 默认并登记失败章、证据与修复路。即便通过，结论也只限于“n=2
支持 Flash 默认切换”，不宣称所有题材或 Pro 档都整体超过。该实验门后来没有通过；§十九记录的是
用户在新一轮双书对照后覆盖这一前置门的产品裁决，不重写本节的历史结果。

### 双书真实结果与裁决

用户已明确授权把两本书正文及 Stage2 上下文发送至 DeepSeek API，并承担两书各自
`stage2.global` 与 `stage2.blueprint=extraction-ir-v3.1` 的费用。真实运行冻结在代码
`5ce162dd95eb93ebce9f420abef61817e8e1d57a`、`deepseek-v4-flash`、thinking=true、初始 high；
隔离 base 为 `outputs/_stage2_flash_quality/v31_default_gate_20260823/`，两书使用各自独立空缓存。
每条命令均先经 `preview` 取真实 argv，再串行执行；token 只从该 base 的
`_service/ledger.jsonl` 聚合。

| 书 / 节点 | 真调用 | prompt | completion | reasoning | wire latency 合计 | 操作墙钟 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 无情道 / global | 2 | 26,844 | 32,364 | 27,788 | 235.8s | 238s |
| 无情道 / blueprint | 31 | 199,991 | 229,226 | 200,953 | 1,802.2s | 495s + 独立复检 3s |
| 偶像公寓 / global | 1 | 18,983 | 2,674 | 6 | 19.3s | 22s + 零成本归一 2s |
| 偶像公寓 / blueprint | 23 | 173,614 | 222,304 | 190,040 | 1,761.5s | 363s + 定向修复 166s + 复检 3s |

无情道 global 的首个 high 响应只有 reasoning、正文为空，不能构造 schema；同参数 high 重试成功，
故账本是 2 条真调用而不是隐藏成 1 条。新正式稿删掉了 ch1 已明示身份的伪 secret，并准确恢复
`secret_01`：“黎玄在收徒当天就已知迟折月是合欢宗弟子”，`setup=[]`、reader reveal=ch16。
`setup=[]` 表示前文没有显式伏笔，不等于再次漏掉这个冷揭示。偶像公寓 global 首稿被
`secret_setup_order` 拦成 `.INVALID`；走 inbox 登记的零调用 `--renormalize` 后正式落盘，没有为同一
问题再付一次钱。三条秘密分别在 ch4/ch11/ch15 向读者揭示，均不是开篇明牌，发生时间与读者揭示
时间也未混写。

| 质量项 | 无情道最终稿 | 偶像公寓最终稿 |
| --- | ---: | ---: |
| beat / hinge | 130 / 133 | 157 / 193 |
| beat span | 130/130 | 157/157 |
| 关闭人物引用：歧义/名册外/代词/过早 | 0/0/0/0（分母 278） | 0/0/0/0（分母 426） |
| candidate / phrase 采纳 | 106 / 27 | 176 / 17 |
| 机械嵌套 hinge | 0 对 | 0 对 |
| 最终状态 | AMBER、0 HARD、fresh/match | AMBER、0 HARD、fresh/match |

关闭人物引用与反嵌套修复都成立；但**默认门仍失败**，而且失败不是指标误报：

- 无情道按新响应的 candidate 密度盲序检查 ch16/ch19/ch21、按 phrase 密度补 ch7/ch14，再与冻结的
  Pro × v1.1 揭盲比较。旧版的普通数量后缀、整句/子串重复已消失，密集章的精度总体提高；但新稿
  只有 133 hinge，低于 Flash v2 的 161 和 Pro v1.1 的 168。追加检查稀疏章后确认了反向漏召回：
  ch4 没保留笑点落点“抽出隐藏款了”，ch12 没保留“除了修炼就是来看师姐练剑”这条暗恋证据，
  虽然 event 概括了事实，下游需要的原句材料已经丢失，故不能用“0 嵌套”换算成质量通过。
- 无情道 blueprint 仍比 Flash v2 少 44.6% 调用、45.8% reasoning、44.7% completion、48.3% 墙钟，
  但相对 Pro v1.1 的既有优势反转：调用多 19.2%，reasoning 多 5.0%，completion 多 5.9%；预注册
  成本门“继续保住 Pro completion/reasoning 优势”未达成。
- 偶像公寓的风险章 ch7/ch10/ch13/ch16 没发现系统性普通对白、数字后缀或嵌套；ch13 有少量边界项，
  但不是 193 条整体过抽的证据。真正的稳定性反例发生在首轮落盘：ch1、ch7 分别为 386、418
  字/beat，超过全书中位数 127 的 3 倍，被 `beats_coarse` 关闭式拦截。随后按登记恢复路只重生成
  ch1/ch7，并重做摘要，才从 4/5 beat 修到 7/11 beat、形成正式稿。正常拓扑本应是 18 章加摘要
  19 调；本次实际 23 调还包含一次同 high 边界重采与三次修复调用，故“无异常重采、首轮无粗切”
  同样未过。

裁决是继续保持 `contracts/invariants.py::BLUEPRINT_ARCHITECTURE = 'extraction-ir-v2'`；
`ChapterBlueprints` artifact schema 仍是 v2。两份最终稿证明 v3.1 可以经既有恢复路形成机械干净的
产物，却没有证明默认 consumer 能首轮稳定达到 Pro v1.1 质量与成本组合。

针对本次真实反例，后续零成本施工把 v3/v3.1 的同相脚手架补齐为“行动→反应→信息改变→决定”先
召回、长对话遇揭示/关系转折/承诺/拒绝必须切拍；v3.1 hinge 同时新增“反批量不等于少交”，要求
保留关系证据、后文回收原话和笑点最后一击的唯一原文材料。该修改发生在本轮冻结运行**之后**，
只能称下一候选，必须重新付费验证，不能拿上述旧响应证明已修好。

证据 SHA-256：无情道 global / blueprint 为
`856b18d454cc386f88270d871eb44dafa702ad09bb46579e28fd6247ef9a3c0a` /
`cd942a3b33a0d9b778be5269c5201ba9dfe9c41402facf846abc8d2db1c09dbb`；偶像公寓为
`97a6f7786c9c6e859d9055b0c84dbbc7c4b1c578a47332e403feceff0bd2a428` /
`e736e739bb73e8e79487e804eb66601594eed4f4988ad36f521e2809fe0b0ab9`；ledger / operator audit 为
`dd8dbae0fa1f5b91c7568ae6f155f12ef5ed4e314234c549c7b044ab9a13e671` /
`770f4dbbab4c689c16c934ebdf4eb5e8d978b3bd996f0094e08d3baa81872bed`。

## 十七、同输入 Pro v3.1 诊断与结构化候选（2026-08-23）

### 单变量教师跑

为回答“v3.1 的问题来自架构还是 Flash 能力”，在提交
`5ce162dd95eb93ebce9f420abef61817e8e1d57a` 上冻结无情道的同一份正文与已审 global，只把模型从
`deepseek-v4-flash` 换成 `deepseek-v4-pro`，thinking=true、响应契约仍为
`extraction-ir-v3.1`。`stage1/main_text.json` 与 `stage2/global_grounding.json` 的 SHA-256 分别为
`e32f675b2da96174a29b68f190d81d18bc87ebcb61ee2c554e8c2243e714d6d1`、
`856b18d454cc386f88270d871eb44dafa702ad09bb46579e28fd6247ef9a3c0a`，与冻结 Flash v3.1 完全一致；
独立 base 为 `outputs/_stage2_flash_quality/v31_pro_teacher_20260823/`，使用独立空缓存。

首轮 23 章均一次构造响应成功，摘要后被 `chapter_no_hinge` 关闭式拦在 ch17；人工对照原文确认该章
确有需要保留的疗伤说法与书名回收，故没有放宽判据，而是按 inbox 的唯一恢复路只重生成 ch17 并重做
摘要。最终正式稿 152 beat / 134 hinge、152/152 span，关闭人物引用歧义/名册外/代词/过早四项
均为 0（分母 314），地点命名 117/152（77.0%），0 HARD。最终 blueprint SHA-256 为
`b67abf381b21381bada0cdab5f4f355f4ea83ee7fe0639eb49609b84ec96d38d`。

| 冻结臂（均含各自登记恢复） | 真调用 | prompt | completion | reasoning | wire latency 合计 | beat / hinge |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Pro × v1.1 | 26 | 162,768 | 216,442 | 191,389 | 3,444.3s | 130 / 168 |
| Flash × v3.1 | 31 | 199,991 | 229,226 | 200,953 | 1,802.2s | 130 / 133 |
| Pro × v3.1 | 26 | 162,933 | 226,522 | 188,128 | 3,478.5s | 152 / 134 |

Pro v3.1 相对 Pro v1.1 的 reasoning 低 1.7%，但 completion 高 4.7%；调用数相同，wire latency 近似。
因此它不是成本与质量同时严格支配 v1.1。账本里 Pro v3.1 的首轮为 24 调，修复 ch17 与全书摘要再
各 1 调；合计操作墙钟约 582s。ledger / operator audit SHA-256 为
`82289bd0901ed7289eddcd0ba896b2ed2f5ff50aa461e42e149fc6e4db0f5a8e` /
`b383744bb924aab8515311845282523400af37b70b33fbb3861b1e2fe403a420`。

### 原文盲读裁决

不按条数直接判优，而是检查同章是否保存了下游真正需要的事件边界与原句材料：

- ch4：Pro v3.1 恢复笑点落点“抽出隐藏款了”；Flash v3.1 只概括事件，没有 hinge；
- ch12：Pro v3.1 保留 4 条关键原句，包括“除了修炼就是来看师姐练剑”；Flash 只留下糖人一句；
- ch13：Pro v3.1 恢复“戏骨仙”揭示与“鬼域门口禁止唱歌”的回收，Flash 都只留在 event 概括；
- ch18：Pro v3.1 用 7 beat 保存身份爆发和后续告知的因果次序，优于 Flash 的 5 beat 粗并；
- ch22：Pro v3.1 从 Flash 的 3 beat 恢复为 13 beat，关系步骤齐全，但又比 Pro v1.1 的 7 beat
  明显更细，必须作为过切反例继续审查；
- ch3：Pro v3.1 为 6 beat / 5 hinge，而 Pro v1.1 为 8 / 17；后者存在把普通台词抬成 hinge 的明显
  过抽，说明 168 对 134 不能直接解释为 v1.1 质量更高。

可观察的响应 JSON 还显示：Flash ch22 已列 12 个 micro-event，却只编成 3 个 beat；Pro 列 19 个并
编成 13 个。ch12 同样不是“完全没召回 micro-event”，而是最终边界和关键原句义务没有落实。由此得到
的限定结论是：**v3.1 架构有能力补回 v1.1 的真实内容并降低假 hinge，但 Flash 的短板集中在从
micro-event 到最终 beat/hinge 的履约；Pro v3.1 也会过切，不能把 Pro 输出或数量当成真值。** Pro
只作离线诊断教师，不把其推理文本或整份答案蒸馏进 prompt。

### 结构化候选、付费烟测与否决

下一候选把上述差异转成响应字段与本地可执行约束，而不是继续扩写自然语言 system prompt：

1. 仅 v3.1 的 micro-event 增加 `function` 闭集（行动、反应、上下文、揭示、决定、结果、关系、
   回收、转场）与 `fidelity` 闭集（事实 / 必须原句）；v3 响应与盘上 artifact 不变；
2. 模型一旦把 micro-event 自报为揭示、决定、结果、回收或转场，其 `end_para` 必须成为最终 beat
   边界；自报 `F_VERBATIM` 的区间必须有一条可确定反投影的 hinge。这是响应内部一致性义务，不冒充
   人工 ground truth；
3. 章内候选按引语、数字、因果分层抽样并保留各层首尾，避免 32 项上限被章前引语占满；
4. 章节 HARD 预检前不再调用全书摘要。正常成功仍为 `N+1`，修复成功由旧的 `N+1+k+1` 收为
   `N+k+1`；正式 artifact、摘要和 fresh/match 口径不变。

相对上一候选，v3.1 system prompt 仅由 6,577 增至 6,591 字（+14）；局部结构 hint 从 538 增至
678 字（+140），主要变化在响应 schema、compiler 与 validator。定向、Stage2 四文件与完整非 corpus
回归均 exit 0，但测试通过只证明契约能执行，不能代替真模型的成本与内容裁决。

用户授权后在提交 `e720d03219232c5694acf1107a3948200c88db25` 上做两书首轮子集烟测；隔离 base 为
`outputs/_stage2_flash_quality/v31_structured_smoke_e720d03_20260823/`，模型为
`deepseek-v4-flash`、thinking=true、独立空缓存。工具先核每个子集请求与后续整跑逐字节同构，不写
任何书内 artifact；两组均省略摘要批。首次无情道命令因 worktree 没加载本地凭据，在传输前以
`MissingApiKey` 结束，没有正文离机、成功调用或费用，operator 仍如实登记 exit 1；随后从主工作区
本地 `.env` 仅注入不回显的既有凭据，按相同预览重试。

| 同批风险章 | 旧冻结调用 / prompt / completion / reasoning | 结构化候选调用 / prompt / completion / reasoning |
| --- | ---: | ---: |
| 无情道 ch4/12/13/16/18/19/21/22 | 10 / 68,241 / 86,565 / 76,944 | 10 / 70,437 / 145,555 / 130,860 |
| 偶像 ch1/7/10/13/16 | 8 / 66,003 / 87,071 / 75,147 | 7 / 58,467 / 160,635 / 139,875 |
| 合计 | 18 / 134,244 / 173,636 / 152,091 | 17 / 128,904 / 306,190 / 270,735 |

新候选少 1 调、prompt 低 4.0%，但 completion 高 76.3%、reasoning 高 78.0%。4/13 个目标章触发
validator 重采：无情道 ch12/ch16 缺自报 `F_VERBATIM` 区间 hinge，偶像 ch7/ch10 把自报关键事件
压过 beat 边界。说明闭集确实抓到履约问题，但把一次短响应变成双份事件表示与逐项自证，成本不可接受。

内容侧同样是混合裁决：

- 无情道 8 章全部可编译、0 HARD、span/hinge 全定位；ch4 exact 恢复“抽出隐藏款了”，ch12 exact
  恢复“除了修炼就是来看师姐练剑”和糖人，ch13 exact 恢复戏骨仙、“你嘴没她碎”和鬼域禁唱；
  ch22 为 7 beat / 12 hinge，完整保存请求—原因—安抚—常来—答应，没有 Pro v3.1 的 13 beat 过切；
- 偶像 ch1 为 13 beat，ch7 为 9 beat，既有粗切反例消失；但 ch1 从 12 增至 21 hinge、ch13 从 15
  增至 26，普通介绍、训斥与接话被批量抬成 hinge，不能用“定位 100%”洗成精度通过；
- 两份内存合并稿均 0 HARD，正式书目录零改动；无情道只新增一条 ch22 群体人物字面依据 INFO，
  不是响应 schema 失败，但整跑前仍须复核。

无情道 / 偶像 JSON 报告 SHA-256 为
`6d7672f692f44a45a4c79c00994bba45d269fe4bad19207ac0d4ddc52e711c2d` /
`8f9bda388cde970c47ff4df3879c6a6afde7dfce24fb4c4f8f9242a5604dbc43`；ledger / LLM audit / operator
audit 为 `1028427dadc0d25cd580018c3595c5dd0a3ae2cbf4340060a13f32439aca280d` /
`6f1e21242fd9f8b1e11c4f87ea10a90a2c3fbbfc21b1809baf15504aebefd150` /
`a57692fc745dd86c47c7a749baf4906d86447f5daab1f32f2b78f8c3c6461c6c`。

裁决是撤回 `function` / `fidelity` 字段及其动态 validator，system/hint 回到 e268 的 6,577/538 字；
保留候选分层抽样与摘要前 HARD 预检，因为两者不要求模型多交一份语义自证。下一步先复测 6 个核心章，
通过才扩大到其余密集章；不再为已否决候选整跑。默认继续 `extraction-ir-v2`，盘上
`ChapterBlueprints` schema 继续是 v2，第三本 holdout 仍未启封。

### 撤回自报义务后的核心烟测

在提交 `dd5ea686cd76a00494cb1e8de6d3f20b1cb81a1f` 上使用独立 base
`outputs/_stage2_flash_quality/v31_minimal_smoke_dd5ea68_20260823/` 与独立缓存，仍为
`deepseek-v4-flash`、thinking=true；省略摘要批。6 个目标章全部首轮接受，没有 validator 重采：

| 核心章 | 调用 / prompt / completion / reasoning | beat / hinge | 人工裁决 |
| --- | ---: | ---: | --- |
| 无情道 ch4/12/13/22 | 4 / 26,884 / 40,462 / 35,827 | 3/2 · 4/3 · 8/6 · 5/8 | 通过：笑点、关系原话、戏骨仙与鬼域回收、ch22 关系链都在 |
| 偶像 ch1/7 | 2 / 15,601 / 36,981 / 32,093 | 11/20 · 7/12 | ch7 可接受；ch1 普通对白系统性过抽，失败 |

最小候选相对同 6 章的结构化自报稿，completion/reasoning 约下降 31.6%/32.7%，证明撤回双份
语义自证是对的；但 ch1 仍保留负责人介绍隐私、负责人让人注意言辞、楼梯上的普通接话、询问为何有
女人、正式介绍、工资追问与“一进门不到十分钟”等材料。不能把“0 重采 + 0 HARD”当成 precision
通过。无情道 / 偶像 JSON 报告 SHA-256 为
`8f14800f4b3b244fe1f24b28660def5e7527a4ecefec7b4f22fa465d6f5c2da1` /
`79c6c8d1a399cac342a90855ea5d004793901bd5bae6f6a274af42d7f72a04cb`；ledger / LLM audit /
operator audit 为 `7c7d9087226d33a594f39d3aeae6aeb97c615cd0b050f910e59cf4706702e9f1` /
`4354e467b98655f112a3daf1cdad4671dc817d7fce2e0678407d096c7d67a63c` /
`e9f870c8912389c69628ea10c80e8ff5c8681505efac46bd7b77a1420b24e284`。

### 两条低思考后处理反例

为避免继续扩写章级 prompt，先测两条 `low + thinking=false`、只读内存编译稿的受限小臂；两者都
复用上述章响应缓存，正式书目录零改动：

1. 提交 `b5bff17937e1788ef2d8105e7df233108b7ca3b2` 的既有 hinge keep/drop 复核：偶像 ch1
   20→18，只删掉“注意言辞”“语气友好”两句，仍保留普通介绍、追问、接话与“十分钟”；无情道
   ch22 阴性对照 8→8。2 次付费调用合计 prompt 2,703、completion 179、reasoning 0。结论是安全但
   几乎没有区分力，不接生产。偶像 / 无情道报告 hash 为
   `3102f0294e4fe6a62822f1b36cb1f7bfbf0784716ddf8105196bc349f5c90351` /
   `d3773002d979bd67b2e563f0a95baf483fccc410cc6ec2191923997b640217bf`；ledger / LLM audit /
   operator audit 为 `d0c11cfe9bf1490bf9b93408630c5a49660bab1c36a1dc5e28f23083a78a14a6` /
   `4cd8dfc5a72b97ee8fc4c5bab1d536c328b1affac158890a4f965a863b2ba351` /
   `8038371780b6cdd082ad3a6b8e63f7251ee92617ab93d261bbe3424fb18c2ede`。
2. 提交 `baa0cc397aa776aab5af267af10f96b1d8ef23cc` 的独立候选探针不看模型原选择，只在冻结 beat
   上从代码机械候选重选。偶像 ch1/ch7 都选 16 条，仍选普通介绍、楼梯接话，甚至把“四个”当硬数字；
   无情道 ch4/ch12/ch13/ch22 分别选 2/3/6/5 条，却漏掉“抽出隐藏款了”、ch12 关系原话与戏骨仙，
   precision 和 recall 同时失败。2 调合计 prompt 6,807、completion 2,066、reasoning 0。偶像 /
   无情道报告 hash 为 `1ad155602424580466ec38a93af6fb2aa6404479f7a62cbc49994077ffe9171b` /
   `d533558af7b96de9c8e918829715339da29815a057983713cbfefe61db358cb0`；ledger / LLM audit /
   operator audit 为 `c9b6b0d106e8efe51dbd94762163f42667635358ee7b2e78294098b120bf16b2` /
   `c02ce8a1495a50e9eeb7f3f7d0a9a1518ef7f44deceac9e00eb18601f006fd8` /
   `155b75fabe5860be39ae8e7527329b9561479c28dcd8f8611b3bb8320b67a193`。

共同结论不是“再写一版 selector prompt”，而是低思考 Flash 对 hinge 语义本身偏宽，且独立相会漏回收。
下一候选改动候选**曝光量**而非语义义务：保留按族、按章节位置分层与自由 phrase 兜底，但不再让
对话密集章看到满 32 条机械候选。先做零成本覆盖投影，再只复跑同一 6 章；默认与 artifact schema
均不变，第三本 holdout 继续封存。

零成本投影先把每章曝光上限从 32 收到 20、引语保底从 20 收到 8（数字 4、因果 8 不变，未用额度
仍回流）。无情道 ch4/ch12/ch13/ch22 的候选总数只有 5/11/12/18，现稿 17 条 candidate-backed
hinge **17/17 仍在新表**；偶像 ch1/ch7 才从 32 收到 20，现稿 candidate-backed hinge 分别
19→保留 11、10→保留 5。退出候选表不等于禁止：响应仍可按原文交 `phrase`；这项投影只证明改动
确实打在对话密集反例上、没有机械截掉无情道已恢复的候选，不代替真实模型的 recall/precision 验收。

### 候选 20 的核心、密集章烟测与救回稿对照

在提交 `80a932264a05296a6eaada5a8839c5a1a670623a` 上，以
`outputs/_stage2_flash_quality/v31_candidate20_smoke_80a9322_20260823/` 为隔离 base，继续使用
`deepseek-v4-flash`、thinking=true。无情道核心四章的请求与 dd5 版本逐字节相同，故从独立旧缓存
零成本重放；偶像核心两章因候选表收短而真实付费。后续七个密集章均真实付费，正式书目录不写产物。

| 子集 | 调用 / prompt / completion / reasoning | beat / hinge | 人工裁决 |
| --- | ---: | ---: | --- |
| 无情道核心 ch4/12/13/22 | 4 / 26,884 / 40,462 / 35,827 | 3/2 · 4/3 · 8/6 · 5/8 | 请求未变，原已审响应逐字节重放；笑点、关系原话、揭示回收和 ch22 五步关系链完整 |
| 偶像核心 ch1/7 | 2 / 14,883 / 25,201 / 21,135 | 12/15 · 7/10 | ch1 关键笑点、三次拒绝、预算因果和结尾回收均在；仍约 3–4 条普通/重复 hinge，但不再系统性抬升；ch7 通过 |
| 无情道密集 ch16/18/19/21 | 5 / 36,151 / 53,107 / 46,455 | 10/12 · 7/6 · 8/11 · 9/12 | ch18 首样本 `answer_lost` 后同档重采；四章的冷揭示、身份爆发、结契与离宗关系链完整 |
| 偶像密集 ch10/13/16 | 3 / 24,121 / 27,374 / 23,243 | 13/16 · 15/12 · 8/6 | ch10/ch16 通过；ch13 这一行是从 reasoning 救回的合法 JSON，切成 15 拍且地点全 unknown，单列为反例而非采纳质量 |
| 偶像 ch13 正常正文对照 | 1 / 7,992 / 14,009 / 11,775 | 10/14 | 同一请求正常 `content` 样本；10/10 拍地点明确，照护—自贬—安慰—服药回收的分组明显优于救回稿 |

按每章**真正生成所采纳响应**的账本归并，若把 ch13 救回稿按新策略拒绝并采用正常正文对照，13 章
合计 15 调、prompt 110,031、completion 160,153、reasoning 138,435。旧冻结同 13 章为
18 / 134,244 / 173,636 / 152,091；新候选分别少 3 调、低 18.0% / 7.8% / 9.0%。结构化自报候选
则为 17 / 128,904 / 306,190 / 270,735。首轮异常面从结构化候选的 4/13 降为 ch18 一次真实重采，
再加 ch13 一次因质量政策触发的重采，仍未接近 4/13。

ch13 的对照改变了 `SALVAGE_ANSWER_FROM_REASONING` 的适用边界：同一套 schema/动态 validator 只能
证明 JSON 可编译，不能证明章节切分质量等价。修法不增加 prompt，也不全局关闭恢复路；
冻结实现当时只为 `extraction-ir-v3.1` 关闭救回、由客户端同档重采，首轮与定向修复共用该策略；v3
仍可救回，盘上 artifact schema 仍是 v2。LLM salvage、Stage2 生成/契约/修复组合回归共 211 项
exit 0。后续双书整跑证明“全拒”成本不可接受，现行同档后备策略见本节末尾。

报告 SHA-256：无情道核心 / 密集为
`79889976148c5903b0eceb8a5f77983dc97ffe835e3d7d64d2aad3d5c10faea0` /
`774ff8bc05fc75faa5134966ef9304fd7e2f1905cb815bd04764fa77119b7a35`；偶像核心 / 密集 / ch13 正常对照为
`9f59340989bc203de5c9ac458988a639a65a72b503fe413e05acc657aa473c2e` /
`c228da02e30e3fefa8ab986b777ddf6473321ec4aa36defb6d1304b2cd65bb80` /
`1243e4ab148051d33b2f4a6a88e0b7fd29f5d188884e71daebb70a2bdb770d20`；合并 ledger / LLM audit /
agent progress 为 `bfec80789b0cc1d056aa2fb034f85b800393dedc09a53f1e87e22821e89651c2` /
`23112951d0adf75c2389f22815697e3c2a83e92794d973dc11afc713f288978d` /
`e920fd68bb123d1b67b8a6a217a8e49175182f17dab9fab4e71a528734dff49d`。

裁决是核心与密集两道烟测门通过，进入独立空缓存双开发书整跑；这仍不是默认切换。第三本 holdout
继续封存，只有双开发书整跑冻结后才启封，且不得根据其结果继续调参后仍称外部验证。

### 候选 20 双开发书空缓存整跑与人工对照

隔离 base 为
`outputs/_stage2_flash_quality/v31_full_dual_00757d0_20260823/`；两书复用此前真实生成且 hash 冻结的
`stage2.global`，blueprint 使用各自独立空缓存、`deepseek-v4-flash + thinking=true`。无情道首轮产在
`00757d0`，确定性恢复链修复到 `f040563`；偶像从 `f040563` 空缓存起跑。代码跨提交只修落盘闸与
恢复可达性，没有改两书的逐章 system/user 字节或 artifact schema。盘上
`ChapterBlueprints` 仍为 v2，默认仍为 `extraction-ir-v2`。

| 开发书 | 真实调用 / prompt / completion / reasoning | 操作墙钟 | beat / hinge / span | 地点 | 人物歧义/名册外/代词/过早 | 恢复 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 无情道第一她来自合欢宗 | 37 / 247,508 / 278,203 / 240,579 | 约 551s | 133 / 141 / 133/133 | 100/133 = 75.2% | 0/0/0/0 | 首轮 ch23 因章尾仅有 `【完】` 被旧 validator 拒；层0 已有 1 段外扩能力却不可达。修守卫后，缺章定向恢复又暴露“没有正式基线就不能修”的死路；补首轮形状 backfill 后，ch23 一章 + 摘要落盘。独立 check exit 0 |
| 偶像公寓打工日常 | 32 / 249,944 / 286,791 / 250,097 | 约 550s | 160 / 168 / 160/160 | 117/160 = 73.1% | 0/0/0/0 | 18 章均生成，但 ch10 只有 5 拍、417 字/拍，真实命中 `beats_coarse`；定向重生成后 5→14 拍、6→16 hinge，合并稿与独立 check exit 0 |
| 合计 | **69 / 497,452 / 564,994 / 490,676** | 约 1,101s | 293 / 309 / 293/293 | 217/293 = 74.1% | 0/0/0/0 | 两书最终 fresh/match、0 HARD；两书都没有首轮直接落正式稿 |

无情道调用审计为 24 accepted、7 次 `answer_lost`、6 次 validator 拒绝；偶像为 20 accepted、
12 次 `answer_lost`。后者的 12 次全来自 v3.1 为 ch13 同请求 A/B 反例而全拒 reasoning 通道救回稿；
ch2/ch8/ch9/ch10/ch11 连续两份 reasoning-only 后又降到 low，成为成本爆炸的主要形状。它不是偶发保险：
两书合计 41 章实际用了 69 次 wire。候选 20 因此没有通过“成本低于 Pro v1.1 且首轮稳定”的默认门。

人工对照不按 hinge 条数裁决。无情道 v3.1 的事件边界整体优于 Pro v1.1：ch16 的“收徒当天已知身份”、
ch19 的结契/表白、ch22 的请求—原因—安抚—常来—答应和 ch23 的多人收束均完整；但 hinge precision
仍不稳定。ch8 把普通问答与铺垫成批抬高，ch12 把“一次/两次/三次”分别标成 hinge，ch19/ch21 也有
普通台词入选；反方向 ch4 的“抽出隐藏款了”只在 event、没有 hinge。偶像复现同一边界：ch1 的一般
时长/承接问句、ch7 的“商场有监控”及购物/拉链过渡被抬高；而 ch10 定向高档重采与 ch18 收束的
关键冲突、身份站队和承诺材料又基本准确。结论是“v3.1 有更强事件召回上限”，不是“309 条越多越好”，
也不是“实际质量已超过 Pro v1.1”。低思考 selector 两条既有反例已经证明再加独立模型相既会漏真材料
也删不掉普通对白；没有 ground truth 前不加自动删 hinge 判据。

默认门裁决：**失败，第三本 holdout 继续封存。** 原因有三条独立证据：两本都需恢复、两本成本均未
严格支配 Pro v1.1、hinge precision 尚未收口。既有 Pro v3.1 教师跑已经回答架构上限问题；把 Pro
整份输出或推理文本塞回 Flash prompt 的第一版结构化蒸馏又已被 +76.3% completion 与普通对白反例
否决，故不再跑一遍 Pro 冒充“学习”。

本轮随后只做不增加 prompt 的代码优化：v3.1 不再全拒合法 reasoning 救回稿，而登记为“正常正文
优先”。首份通过完整 schema + 动态 validator 的救回稿只留作同档后备；第二采拿到正常 content 就
采用正常稿，第二采仍不可用则采用后备，不再降到 low。其他响应契约仍是合法救回立即采纳。两条通用
客户端测试分别钉住“第二采正常稿胜出”和“第二采失败采用后备”，Stage2 首轮/定向共用接线；132 项
定向回归 exit 0。已有 reasoning-only 响应按设计不进缓存，因此这项只能称**零成本实现与反事实上限**，
不能拿历史账改写成已真实节省；下次自然触发才回收付费读数。

冻结 SHA-256：无情道 global / blueprint 为
`856b18d454cc386f88270d871eb44dafa702ad09bb46579e28fd6247ef9a3c0a` /
`011cade95b94c287993e889c8cfa53fee72232e55088750e1bf1065cb6c0e1b4`；偶像为
`97a6f7786c9c6e859d9055b0c84dbbc7c4b1c578a47332e403feceff0bd2a428` /
`5e1323e8e65aae71beb401b1dadfa1eb34be5e49ca3222ee38801977a1d85c8e`；合并 ledger / LLM audit /
agent progress 为 `5e1a5984a293d6e933880167cdda6114368781137e7f14d4142808c853abe39c` /
`324a7036eb61a0d016c6db9133b0fc99c7353b288ad432117c57cd69f2750739` /
`a498d46ff7ba03bd5fda261a1a8977024f9446c34a4bbf6c8474bb9839fe7ca4`。

## 十八、`extraction-ir-v2.1`：两相混合 effort 历史实验（已否决并退役，2026-08-23）

### 定义与归因边界

用户的新方向不是继续压缩 v3.1，而是保留已把拍数/hinge 缺口大幅追近 Pro v1.1、但成本较高的
v2 两相契约，
只把不同难度的相分配到不同 effort。v2.1 的定义固定如下：

- 第一相 `boundaries` 继续 `deepseek-v4-flash + high + thinking`。v2 的质量收益来自
  `micro_events → beats` 的完整回忆与因果压缩；这一相决定正式拍边界，降档会把最危险的
  “静默少切剧情”重新放回来；
- 第二相逐章细节改为 `deepseek-v4-flash + low + thinking`。边界已经由代码冻结，人物引用
  已收成可见 `char_id` 闭集，响应还要过 schema、边界恒等与人物闭集三道本地闸；它只填
  time layer、地点、event、hinge 与叙述信息，是本轮唯一降档的自变量；
- 书级摘要批继续 `deepseek-v4-flash + high + thinking`，不把第三个变量混进第一轮实验。
  三处均为固定单档；客户端仍可在同档重采一次，但不会静默滑到 `thinking=false`。

`extraction-ir-v2.1` 与 v2 的 system/user 字节、响应模型、编译器、字段级修复能力和最终
`ChapterBlueprints` 逐格相同；差别只有 architecture 与三处相级 rungs。模型名不写进契约注册表：
本轮实验冻结 Flash，但“模型名 → 契约”不是永久映射。当时 artifact schema 仍是 v2，新增
architecture 属于放宽；实验失败后该 architecture 已从现役注册表撤下，当前 schema 为 v3。

### 配对方法

先在同一冻结代码、同一 `stage1/main_text.json`、同一 `stage2/global_grounding.json` 上跑
Flash v2 高档基线。v2.1 的质量配对**复用这一次已经验过的 high 边界响应缓存**，让两臂的
`frozen_boundaries` 与 `boundary_events` 逐字相同；只让细节请求因 effort 改成 low 而产生新 cache key。
这样质量差异才能归给第二相 effort，而不是另一份随机边界样本。摘要 user 随 low 细节 event 改变，
按 v2.1 的 high 策略真实生成。

配对质量门通过之后，才另开独立空缓存做一次 v2.1 完整整跑，取得真实操作墙钟与异常重采率。
经济账分两层报告：① 配对臂按“基线 high 边界 wire + v2.1 low 细节/摘要 wire”复算完整书成本；
② 空缓存臂报告真实总墙钟与全部 wire。缓存命中不冒充没执行，completion 已含 reasoning，二者不得相加。
两本开发书先跑核心/密集章，再整书；开发门不过则不启封第三本 holdout。

### 预登记裁决门

质量先于成本，任一条失败即保持 v2 默认、不再用便宜解释质量：

1. 两书正式产物均 fresh/match、0 HARD、span 与 hinge 定位 100%，人物歧义/名册外/代词/过早四项全 0；
2. 因为边界响应逐字复用，v2.1 每章 `end_para` 序列必须与 v2 **完全相同**；任何变化都是接线错误；
3. hinge 总量不低于同边界 v2 的 95%，named place 比例下降不超过 3 个百分点；不能只看总数，必须盲序
   对照无情道 ch4/ch12/ch13/ch16/ch18/ch22 与偶像 ch1/ch7/ch10/ch13/ch16，确认笑点落点、关系原话、
   冷揭示、身份爆发、拒绝/承诺链和硬数字没有真漏项，也没有普通对白系统性反弹；
4. 摘要逐章人工核对不得漏掉正式 beats 中改变后续因果的事件；Stage4 投影的 beat/hinge 数不得减少。

质量全过才读成本：low 细节相 reasoning 至少下降 25%，按完整拓扑复算的总 reasoning 至少下降 15%；
空缓存 v2.1 的总 wire 不高于同书 v2，操作墙钟至少下降 15%，且不得靠更多 schema/validator 重采换取。
两本开发书共同通过后才启封第三本结构不同的小说；第三本仍通过同一门，才允许把
`contracts/invariants.py::BLUEPRINT_ARCHITECTURE` 从 v2 翻到 v2.1。v2 永久保留为显式回退。

### 零成本工程验证

v2.1 的相级 rungs 已同时接入首跑、`--chapters` 恢复、摘要重建和 smoke 报告；生产请求测试直接
检查三相 wire，确认边界/细节/摘要依次为 `high + thinking`、`low + thinking`、
`high + thinking`，并钉住 v2.1 相对 v2 只改变 architecture 与三处 effort 策略。Stage2 定向五文件
118 项、冻结契约相关回归、完整非 corpus 回归，以及前端 seams/typecheck/build 均 exit 0；
`git diff --check` 通过。前端验证只读复用了依赖声明相同的主 worktree `node_modules` 临时目录联接，
验证后已删除；两个 worktree 的 lockfile 哈希不同，因此该结果只证明当前源码可由现有兼容依赖构建，
不冒充目标 lockfile 的干净安装验证。当前仍无 `NOVEL_CORPUS_MANIFEST`，以上不得称为 corpus 全量绿。

### 双书付费结果与裁决

用户随后明确授权把《无情道第一她来自合欢宗》《偶像公寓打工日常》的正文与 Stage2 派生上下文
发送至 DeepSeek API，并承担本轮实验费用。实验在分支 `codex/stage2-flash-quality`、基线提交
`4918118718c338e866e8699ee7f368d5d0a2de6e`、付费前冻结工作区 diff SHA-256
`1bc19ecffc985ebf9dda2160046b3783006b979efbba210b29621ebc14d3b122` 上串行执行；模型/契约分别固定为
v1.1 + `deepseek-v4-pro`、v2 + `deepseek-v4-flash`、v2.1 + `deepseek-v4-flash`，三臂都启用 thinking。
付费前发现 smoke 工具虽然生产路径已按 `ContractSpec` 取相级 rungs，但实验入口没有把 rungs 透传给
边界与摘要调用；先补齐透传与回归，33 项定向测试通过后才真发，故本表没有“名叫 v2.1、实际仍跑
默认阶梯”的污染。

冻结输入与盘上正式 Stage2 基线在六臂后哈希不变：无情道 main/global/blueprint 为
`e32f675b2da96174a29b68f190d81d18bc87ebcb61ee2c554e8c2243e714d6d1` /
`856b18d454cc386f88270d871eb44dafa702ad09bb46579e28fd6247ef9a3c0a` /
`011cade95b94c287993e889c8cfa53fee72232e55088750e1bf1065cb6c0e1b4`；偶像为
`b808c137eb11c268219f62b62af1d7ba52c0e22bf03778de00f5d7fd301528b0` /
`97a6f7786c9c6e859d9055b0c84dbbc7c4b1c578a47332e403feceff0bd2a428` /
`5e1323e8e65aae71beb401b1dadfa1eb34be5e49ca3222ee38801977a1d85c8e`。smoke 只生成内存候选；正式产物
零改动，`answer_lost` 证据、ledger、audit 与报告按设计新增。

实际付费运行读数如下。`completion` **已经包含** `reasoning`；墙钟是进程级实测，wire 毫秒求和因逐章
并发而不等于墙钟。v2.1 配对行含 high 边界缓存，不能拿其 token 或墙钟冒充独立整跑：

| 书 | 臂 | 实付 / 缓存 | prompt | completion | reasoning | 墙钟 | 内存候选 HARD |
|---|---|---:|---:|---:|---:|---:|---|
| 无情道 | v1.1 Pro | 24 / 0 | 154,864 | 196,634 | 172,422 | 462.3s | 2 |
| 无情道 | v2 Flash | 48 / 0 | 221,878 | 332,754 | 296,198 | 507.1s | 2 |
| 无情道 | v2.1 Flash 配对 | 27 / 20 | 162,807 | 98,969 | 78,160 | 177.6s | 0 |
| 偶像 | v1.1 Pro | 19 / 0 | 137,629 | 214,018 | 185,437 | 705.1s | 2 |
| 偶像 | v2 Flash | 39 / 0 | 210,705 | 226,158 | 189,491 | 502.5s | 5 |
| 偶像 | v2.1 Flash 配对 | 25 / 13 | 161,012 | 156,901 | 129,147 | 415.2s | 2 |

为避免把缓存当成降本，v2.1 完整拓扑成本按事前口径重建：取同书 v2 的全部 high 边界 wire，加 v2.1
的 low 细节与 high 摘要 wire；配对臂因缓存未命中而重发的 8 个随机 high 边界不重复计入。只按输出侧
判成本，两书合计 v1.1 Pro 为 43 调 / 410,652 completion / 357,859 reasoning；v2 Flash 为
87 / 558,912 / 485,689；v2.1 重建为 87 / 503,177 / 424,559。故 v2.1 相对 v2 只降
10.0% completion、12.6% reasoning，未到 15% 总 reasoning 门；相对 v1.1 仍多 22.5% completion、
18.6% reasoning。细节相合计 reasoning 虽降 29.4%，但两书方向不一致：无情道降 48.7%，偶像反而
升 6.2%，不能写成稳定收益。v1.1 与 v2 的两书墙钟合计为 1,167.4s / 1,009.6s，v2 快 13.5%；
v2.1 因质量门先失败没有运行独立空缓存臂，真实整跑墙钟明确记为**未测**。配对臂 592.8s 是 33/41
个 high 边界命中缓存后的诊断读数，不进入速度排名；重建 wire 求和也只降 10.5%，不是墙钟。

质量比较只取 `end_para` 完全一致的严格配对章。无情道 20/23 章配对，v2 → v2.1 为 152 → 105
hinge（69.1%），named place 86/107 → 74/107（80.4% → 69.2%，−11.2pp）；偶像 13/18 章配对，
131 → 78 hinge（59.5%），65/79 → 56/79（82.3% → 70.9%，−11.4pp）。合计 283 → 183
hinge（64.7%，比 95% 门低 30.3pp），地点比例下降 11.3pp；两书独立失败。v2.1 的已报 hinge
定位率反而从 98.7% / 97.0% 升到 100% / 100%，说明它交出的少量锚点较易定位，却不能说明未交出的
材料不存在；“候选 HARD 更少”同样只证明现存锚点合法，不能替召回背书。

人工核对也排除了“只是删掉普通对白”的解释。无情道 ch16 在相同边界上 18 → 7，未把“昏迷十日”、
“你什么时候知道”“丹田受损”“别的人帮你我不放心”等因果/关系原话登记为 hinge；ch22 的“等师姐
多久都不久”与允许常来的收束链也断。偶像 ch1 为 14 → 7，“身无分文”“经费里扣”“进门不到十分钟”
等设定/笑点锚点消失；ch13 为 20 → 12，“别物化自己—不接受替换—按医嘱治疗”的关系推进链被稀释。
这里裁的是**下游可消费 hinge 漏标**，不是断言对应事件一定没写在 event 里；但它已经足以否决“质量
几乎不变”。

裁决：**v2.1 失败，默认保持 `extraction-ir-v2`；不运行两本独立空缓存 v2.1，不启封第三本
holdout。** v1.1 Pro 在本次首轮样本上拍更细、机械稳定性更好；v2 Flash 的 hinge 总量不低且
named-place 覆盖明显更高，并在两书合计快 13.5%，但输出 token 更多、偶像候选 HARD 也更多；二者
各有强项。v2.1 虽在无情道显著降耗，却没有跨书稳定性，并以系统性 hinge 召回下降换取较少红灯，
不进入默认候选。完整逐臂 JSON/Markdown/log 与汇总见
`outputs/_stage2_flash_quality/v21_dual_ab_20260823/_reports/`；本活动项已裁决，第三本继续封存。

交付验证：文档契约五文件 + Stage2 相关三文件合跑 410 项 exit 0；完整非 corpus `pytest -q`
exit 0；前端 seams（96 文件/20 份 mock 基线）、typecheck、build 均 exit 0；`git diff --check`
通过。前端仍是临时复用主 worktree 的兼容 `node_modules`，联接已删除；两份 lockfile 哈希不同，
不冒充目标 lockfile 的干净安装。目标 worktree 的临时 `.env` 与两份 pytest 短路径目录也已删除，
主工作区 `.env`、实验缓存、账本、诊断和报告保留。没有 `NOVEL_CORPUS_MANIFEST`，故只称非 corpus 回归。

## 十九、v1.1 / v2 / v3.1 同模型双书复验与 v3.1 默认裁决（2026-08-24）

### 比较边界

用户当时要求把三套契约统一放在 `deepseek-v4-flash + high + thinking` 下比较，并在第一本后再跑
一本。《无情道第一她来自合欢宗》（23 章）与《偶像公寓打工日常》（18 章）均固定同书正文、同书
已审 global、同一模型与初始 effort；三臂使用独立空本地缓存并串行运行。第二书的共享 global 先经
typed edit 与 296/296 项人工 review PASS，再分叉 blueprint，避免把全书事实漏抽算到响应契约头上。
成本只取各臂 ledger；共同 global 建底不摊入任一臂。`completion` 已含 `reasoning`，不能重复相加；
仓内没有供应商费率，因此下表是 token 与墙钟，不冒充货币账单。

这不是旧活动项预登记的第三本外部 holdout：使用的仍是两本开发书，且旧预登记门此前已经失败。
本节记录的是用户在看完新的同模型双书结果后作出的**产品默认裁决**，不是把未跑的 holdout 伪装成
通过，也不把 n=2 外推成全题材结论。

### 冻结结果

| 指标（两本累计） | v1.1 | v2 | v3.1 |
|---|---:|---:|---:|
| 匿名盲审（第一本 → 第二本） | 22.0 → 20.2 | 20.0 → 24.4 | **26.0 → 25.0** |
| 最终正式产物 | **2/2** | 1/2 | **2/2** |
| 首轮正式产物 | 0/2 | 0/2 | **1/2** |
| 真实 wire / 本地缓存 | **46 / 0** | 97 / 0 | 61 / 0 |
| prompt | **311,003** | 494,153 | 446,396 |
| completion（含 reasoning） | **437,685** | 445,392 | 596,894 |
| reasoning（completion 子集） | 399,383 | **390,462** | 523,803 |
| prompt + completion（非金额） | **748,688** | 939,545 | 1,043,290 |
| 付费命令墙钟 | **855s** | 1,073s | 958s |

第一书 v2 在 54 wire / 744s 后仍为 `.INVALID`；v3.1 是该书唯一首轮形成正式产物的臂。第二书
v3.1 与 v2 同档，只领先 0.6 分，而 v2 的周转和 token 明显更好。合并两书后，v3.1 相对 v1.1
多 39.4% prompt+completion、慢 12.0%，换来两书盲审都高约 3～5 分及更稳定的人物 ID/场景边界；
相对 v2 多 11.0% prompt+completion，但快 10.7%，并多形成一本正式产物。故 v1.1 是明确的低
token 臂，v2 是方差较大的高密度回退臂，v3.1 在这两本上提供最好的质量/可交付可靠性组合。

### 裁决、回退与已知风险

用户据此明确裁定把 `contracts/invariants.py::BLUEPRINT_ARCHITECTURE` 从
`extraction-ir-v2` 翻为 `extraction-ir-v3.1`。`extraction-ir-v1.1` 与
`extraction-ir-v2` 继续登记，可经 `NOVEL_STAGE2_BLUEPRINT_CONTRACT` 显式选择；定向修复仍按
基线产物自己的 architecture 选契约。这里只改变默认值：`ChapterBlueprints` artifact schema 仍为
v3，不迁移、不重建存量产物。

风险账不随翻默认消失。第二书 v3.1 与 v2 都在 ch6 把女装江尽夏绑定成 ch15 的孤儿院小妹妹
`char_029`，机械 HARD 没抓到；两臂另各有动作施受、跨场景或 summary 完成态错误。因而“v3.1
默认”不等于“可未经语义复核直接进入 Stage4/5”。在同一实体揭示链的 ID 一致性防线补齐前，人工
语义复核仍是必需边界。

逐书成本、终态、hash 与匿名逐章证据分别见
`outputs/_stage2_contract_ab_20260824/stage2_contract_ab_report.md`、
`outputs/_stage2_contract_ab_book2_20260824/stage2_contract_ab_book2_report.md` 与同目录
`blind_review.md`。这些 `outputs/` 证据不提交；本节保留可继续引用的聚合裁决。

切换后的零成本工程验证：Stage2 定向 147 项、冻结契约五文件 325 项、完整非 corpus 回归均
exit 0；前端 seams（104 文件/20 份 mock）、typecheck、build 均 exit 0。当前仍无 corpus manifest，
所以这些结果不称 corpus 全量绿。本轮设置默认没有新增任何 LLM 调用。

## 二十、退役 v1.1，契约面收为 v2 / v3.1（2026-08-24）

§十九记录的「v1.1 继续登记」是当时的裁决；本节是后续裁决，以本节为准。
用户明确要求删除 `extraction-ir-v1.1`，`stage2.blueprint` 现役只保留
`extraction-ir-v2` 与默认 `extraction-ir-v3.1`。历史付费 A/B 读数仍然有效且保留，
但 v1.1 不再是配置、整跑、定向修复或冒烟工具的可达入口。

收窄面同批闭合：

- `contracts/invariants.py::BLUEPRINT_ARCHITECTURES` 与
  `novel/stages/stage2/contract.py::CONTRACTS` 只剩 v2 / v3.1，配置校验与 API 枚举从前者派生；
- v1.1 专属的响应载体、prompt/hint 导出、旧蓝图重放工具与 corpus 守卫删除；
- 前端枚举夹具、冒烟白名单和通用测试基线同步收到保留契约；
- `BlueprintContext.architecture` 的 Literal 从三值收成两值，因此
  `stage2/chapter_blueprints.json` 从 schema v3 升到 v4。本仓没有迁移机制；
  存量 v3、`.INVALID` 与 `_rounds/` 均由 `stage2.blueprint` 付费整跑重建，
  不手补 marker 或 architecture 冒充当前产地。

本次退役是 0 LLM 的工程改动；它不改写§十九的质量、token 与墙钟史实。
