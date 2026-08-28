# 产物 schema —— 怎么读，怎么产

**本目录定的是「内容」，不是「载体」。** 有哪些字段、什么类型、哪些必填、取值面是什么——
这些与代码放哪儿无关，现在就能定死。
**载体已定：Pydantic v2**——
但那不改变本目录的职责：**这些 `.md` 是人读的源，模型在骨架阶段照它们写，方向别搞反。**
所以下面的表里**不出现任何 Python 类型标注**，只出现「类型 / 必填 / 取值面 / 证据」。

---

## 「证据」列 —— 本目录的核心机制

它与需求文档那一列「谁在查」是同一个东西：**让「我们真的知道」与「我们只是写着」读起来不一样。**

| 取值 | 含义 |
|---|---|
| **`三源`** | 盘上观察到 + 契约写了 + 代码写了。可信度最高 |
| **`盘·码`** | 盘上有、代码有，**契约没写**。契约文档漏了它 |
| **`契·码`** | 契约有、代码有，**盘上零样本**——异常路径没被触发过。<br>**类型和取值面是从代码读出来的，不是量出来的** |
| **`⚠冲突`** | 三源说法不一致。**定契约之前必须先裁决哪个是权威** |

⚠ **「盘上 3/3」不等于「必填」，更不等于「非空」。** 凡是「盘上零样本」或
「盘上取值面明显偏窄」的字段，都要去**全语料**或**代码**再确认一次。

⚠ **反过来也成立：「盘上有」不等于「该落盘」。** 定完取值面还要再问一句
**「这条信息能不能 0 成本重新算出来」**——能现算的字段落盘只是抄第二份，
而两份同源数据哪天不一致时，没人知道该信谁。
实例：Stage1 的原文章号、切片区间、章节计数与异常都能从当前
`stage0/clean_text.txt` 确定性复扫，因此不落盘第二份。Stage1 只留下下游真正消费的
`main_text.json` 与 `hook_copy.json`；它们还必须与当前 clean 的确定性切片逐字相等。
不可再生的信息才落盘：如 Stage0 revision 里的源身份与人工剔除决定
（人工圈选无法从 clean 反推，见 `stage0.md` §2.5）。

## 目录

| 文件 | 覆盖 |
|---|---|
| `stage0.md` | `revisions.json` · `clean_text.txt` |
| `stage1.md` | `main_text.json` · `hook_copy.json`；章号诊断与校验是当前内容的运行时视图 |
| `stage2.md` | `global_grounding.json` · `blueprint_character_projection.json` · `chapter_blueprints.json`　**全链第一个调 LLM 的层** |
| `stage4.md` | `blind_candidate.json` · `evidence_binding.json` · `plan_chapters.json` / `plan_edit.json` · `writer_context.json`　A/B 两相各自产一份 LLM 中间产物，计划由代码编译，写作上下文再确定性组装；`--identity` 只作内存诊断；<br>也是**全链的收口点**——前三层所有逐章闸门最后都汇到 `writer_context` 这一份上 |
| `stage5.md` | `chNN.json`（含 `.INVALID`）· `review_<n>.json` · `wontfix.json` · trim / `_rounds/`<br>**全链唯一产出正文的层**，也是**唯一带 LLM 返工回路**的层；现役为 canonical 闭卷单臂，机械验收 0 成本，书级 `stage5.semantic` 与展示投影 `draft.md` 已下线 |
| `stage6.md` | `hook.json` · `_rounds/hook.rN.json`　**全书级单次产出**（不是逐章），三候选经人定稿后才进入下游；<br>判据表分两半的边界是**产物类**（文案＝全文改写 ⇒ 连续文本探针结构性失效），不是判据质量 |
| `stage7.md` | `name_map.json` · `_rounds/name_map.rN.json` · **`name_map_edit.json`（人产的输入，也是契约）** · **成书 `<书名>_rewriting.txt`（字节形态，§四）**<br>**全链最后一层**，也是唯一产出交付物的一层；LLM 只写两个键，其余 19 个顶层键一个字都不经模型 |
| `_common.md` | `_provenance` 的五个共有键（含必填 `root_clean_sha1`）· **两把尺子**（40 位内容身份 / 10 位来源指纹）· `inputs` 的边 `{file, consumed}` · 新鲜度三态与**幽灵边** · `.INVALID` 与提升路（**签名里没有 `prompts`/`model`/`inputs`**）· 轮次账 · `Finding` 14 字段 · `usage` · 记账键的出口规则 |
| `_report.md` | `CheckReport`　**不是产物**——它是 `--check-only` 打印的、`/api/novels/:sid/check/:kind` 返回的那一份。 |
| `_orchestration.md` | `Step` · 作业事件流 · 完成度 · 队列　**不是产物**——服务契约与产物契约改版节奏不同。 |
| `_api.md` | HTTP 响应　**不是产物**，也**不定路由表**（路由在 `web/DELIVERY.md` §3）。 |
| `corpus.md` | current-schema corpus 的无正文 manifest、ledger 汇总与 `--require-corpus` 硬验收 |

## 版本与路径注册表

产物路径字面只登记在 `contracts/artifacts.py::ARTIFACT_PATHS`；节点 `produces`、消费投影、
schema 路由与 stage 真实读写均引用该目录。路径模板由 `artifact_rel()` 展开，逐章 `nn` 统一补零两位。
`contracts/schema/__init__.py::ARTIFACT_SPECS` 只为其中的 JSON 子集叠加当前模型与 schema version，
`ARTIFACTS` 作为模型投影从它派生，不再有第二张手抄路径表。

当前普通对象顶层带 `_schema_version`，RootModel 用 `{_schema_version,payload}` 信封。读取只接受
注册表声明的当前版本；无标记、低版本和高版本都明确拒读，错误必须带文件与版本。当前 Pydantic
模型同样不接受旧字段别名或双形状，需要保留的输入从产地按当前契约重新生成。

**没有迁移机制。** 版本号是“**这个产物的形状被破坏过几次**”的计数器，不是家族标签。
`stage2/chapter_blueprints.json` 已破坏五次：2026-08-19 锚点从逐字转写改成段落索引，升 v2；
2026-08-23 architecture 从七值收窄为 v1.1/v2/v3.1，升 v3；2026-08-24 再收为
v2/v3.1，升 v4；2026-08-25 以原子事实契约替换旧 event 形状，升 v5；
2026-08-26 删除摘要、展示名、重复父键与无消费来源账，升 v6。Stage4
`blind_candidate.json` / `evidence_binding.json` 为 v2，`plan_chapters.json` / `plan_edit.json` 与
writer context 为 v4；identity artifact 已退役。旧 Stage4 产物不迁移，先跑 `stage4.design`、再跑
`stage4.plan` 重建，Context 可免费重建。Stage5 canonical 章、Review 与
trim 两类产物也在 2026-08-24 单臂折平时升到 v2：旧 `stage5/deep|shallow/` 只读保留，
`stage5.write` 付费重建正文、`stage5.review` 免费重建报告，不迁移也不搬文件冒充 current。
每一次破坏都登记在
`tests/test_artifact_versions.py::_SHAPE_BREAKS`，那张表与注册表互为对方的守卫。曾经有过一梯 v2→v3 migration，删除那天的实测是：
登记了 migration 的 8 个家族在盘上一份低版本文件都没有，而盘上唯二的 v2 文件恰好属于**刻意不登记
migration** 的那一族 ⇒ 整条梯子零消费者，却让上一段那句话假了一整段时间。判准是**谁来重建**：
答得出“从 producer 重跑”就不该有迁移。旧产物不手补 `_schema_version` / `root_clean_sha1`
冒充当前产地，必须从 producer 重建。

## 反推工具

盘上那一源由一个机械反推脚本产出（0 成本、可重跑）：遍历 `Output/` 下每本书的产物，
逐路径记类型 / 出现率 / 取值面。它**只报「观察到了什么」，不推断语义**。
脚本在 `tools/derive_schema.py`。产物目录**只从 `NOVEL_OUTPUT` 取、没有默认值**，
指到 `outputs/` 下的某个批次即可。
⚠ **没设或找不到目录时它都显式终态**，不会静默产出一份「所有字段都零样本」的 schema——
那种产物与真跑过的长得一模一样。

> ### ⚠ 有「人修完再落盘」这条路的层，`_rounds/` 归档**必须一起反推**
>
> 正式产物是**修完之后**的状态，它会把「修之前才出现的取值」整个抹掉。
> 実测（stage7）：`residual[].confidence` 在 3 份正式产物上是**单值 `low`**（19/19），
> 而 8 份归档里有 **35 条 `high`**——高置信的那些早就被人勾选、走人工补充修掉了。
> **只反推正式产物，会定出一个把 `high` 当成零样本分支的 schema。**
