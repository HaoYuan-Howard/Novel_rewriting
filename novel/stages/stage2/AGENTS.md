# Stage2 局部架构

本文件维护 Stage2（STR-06 新链）的局部不变量与目录约束。跨 Stage 硬闸见根
`AGENTS.md` 的 I-\* / S-\*；产品行为与 R 编号见 `docs/BACKEND_CONTRACT.md`；字段
形状见 `docs/schema/` 与 `contracts/schema/`。不复制根不变量全文。

⚠ B2（2026-09-27 职责解耦 D9）：本包按语义单元与作用域拆**五个 owner**——
`str06.stage2.book`／`chapters`／`names`／`associate`（付费观察与关联）＋
`str06.stage2.compile`（免费确定性编译）；`str06.stage2.source`（book＋chNN
联合抽取）退役。观察与关联协议在 `contracts/schema/observation.py`
（三类观察 `stage2-observation-v1`；R1 起 associate 独立版本
`stage2-associate-v2`——typed 目标显式序列化＋状态句柄位置编码，显式版本化
I-13）。**B2 第二步（2026-09-27）已落地**：
book／chapters 两 owner 引擎（`book.py`／`chapters.py`）与 `chain.py` 接线
（`run_book`／`run_chapters` 替换 `run_source`，`run_names` 重挂书级观察输入，
names 引擎不变）、付费 owner 记账映射改挂新节点。**B2 第三步（2026-09-27）已
落地**：`associate.py`（跨章关联 owner：句柄投影、闭集核对、只选已给句柄）与
`compile.py`（双真源唯一发布 owner：解析证据、生成 ID、执行关联、未决拒发；
源 fold 唯一实现随迁本模块）；`chain.py` 的 `run_associate`／`run_compile`
入列，Stage4 三腿（Preserve／BodyDesign／HookDesign）改读 canonical——
`stage2/compiled/book_extraction.json` 派生输入写路随之退役（D9）。
旧联合抽取引擎已随 B5（2026-09-28）S-05 整包删除：反例全部迁至五 owner 引擎，
源 fold 唯一实现住 `compile.py`（历史落点与删除登记见
`docs/REQUIREMENTS.md` B5 行与 `docs/DECISIONS.md`，原文按 Git 历史读取）。

心智公式：LLM = understand → Resolver = locate → Compiler = normalize →
Canonical = truth → Projection = expose → Validator = enforce。

## 局部不变量（S2-\*）

| 编号 | 规则 |
|---|---|
| S2-1 | Stage2 的外部业务真相只有已登记的正式 artifact（`contracts/artifacts.py::ARTIFACT_PATHS` 的 `stage2/**` 键）。中间 wire／recovery 状态都不是下游 API。 |
| S2-2 | LLM 只负责语义购买；不得直接写正式 artifact。付费响应的 parse／shape 问题走免费修复，不得重新购买掩盖契约或编译器缺陷。 |
| S2-3 | Compiler／Resolver 只做确定性转换与定位：ID、ref、anchor、closed-set binding、normalization、projection。禁止补猜语义、不模糊匹配、不猜笔误（I-20／I-21；重复、不存在、冲突定位产生精确 issue）。证据段＝源文一个非空自然段、请求一段一行；可由引文推导的 basis 段号由 Resolver 定位，不让模型抄（RC-26/27）。 |
| S2-4 | Projection 是 canonical artifact 的只读派生视图，不得反写 truth，也不得形成第二真相源（I-16）。 |
| S2-5 | `str06.stage2.compile` 是 Characters（`stage2/characters/characters.json`）与 Blueprint（`stage2/blueprints/chapter_blueprints.json`）两份真源的**唯一 writer**（D9）：解析证据、生成 ID、执行关联后以同一提交记录发布；存在未决项时拒绝发布并按 item 报告（D8），恢复走 owner patch 或 associate 定向修订。派生输入 `stage2/compiled/book_extraction.json` 与逐章输入快照已退役（D3/D9），Stage4 一律改读 canonical（人物 core 由 Characters 投影）。下游 owner 的单节点输入从盘面闭合，不靠内存传递。 |
| S2-6 | Stage2 只产出 artifact、projection 与机械证明。是否进入下一节点由 orchestrator 决定；producer／compile 不得顺手启动下游节点。 |
| S2-7 | `str06.stage2.associate` 只建立同一性与引用（D8）：输入是 compiler 投影的**已给句柄闭集及最小文本**，输出只选已给句柄；缺项、歧义、冲突保持未决——不默认合并，也不默认不同；不移动事实成立、角色知情或读者揭示时点。 |

章内同一悬念句柄的open／close各最多一次，本地close不得早于本地open（拍序及
同拍条目序）；没有本地open的close仍走跨章配对。接受与下游预检复用只读判据；存量
错误按`chapters + chapter:N`回派，发现位置不授予修复权。associate修订只接收
本单位可改且具有稳定规则与位置身份的关联问题（RC-18～20）。

## 目录约束

```text
stage2/
  book.py      # str06.stage2.book owner：书级观察引擎（闭集/锚点/canonical 闭合＋core view 投影，B2）
  chapters.py  # str06.stage2.chapters owner：逐章观察引擎（本章证据闭集/作用域归一/章内句柄纪律，D3，B2）
  associate.py # str06.stage2.associate owner：跨章关联引擎（句柄投影/闭集核对/五类配对，D8，B2）
  compile.py   # str06.stage2.compile owner：双真源唯一发布引擎（ID 生成/关联执行/未决拒发＋源 fold 唯一实现，D9，B2）
  names.py     # stage2.names owner：源名称结构窄调用（语义协议不变，D9；输入已重挂 book 观察）
  resolver.py  # Stage2 证据 Resolver：句段闭集选择、逐字锚唯一定位、basis 引文所在段定位与证据清单渲染（B2；RC-26/27）
```

- 正式 schema 只在 `contracts/schema/`（及 `docs/schema/`）；stage2 不复制 schema truth。
  观察与关联协议模型在 `contracts/schema/observation.py`。
- 跨 stage 共用的确定性原语（字面扫描、机械判定、prompt 组装、场景投影、阅读界面
  渲染）住 `novel/stages/common.py`，不在本包复制第二份。
- 薄编排与设施装配住 `novel/stages/chain.py`；本包不 import `novel.llm`／
  `novel.store`／`novel.orchestrator`（守卫见 `tests/test_str06_invariants.py`）。
- 不为对称铺空目录树；公式落在各 owner 内部模块。

## 现役公开面

| Owner | 权威 path | 节点 |
|---|---|---|
| book | `novel/stages/stage2/book.py` | `str06.stage2.book` |
| chapters | `novel/stages/stage2/chapters.py` | `str06.stage2.chapters`（并发节点集合已登记） |
| names | `novel/stages/stage2/names.py` | `str06.stage2.names` |
| associate | `novel/stages/stage2/associate.py` | `str06.stage2.associate` |
| compile | `novel/stages/stage2/compile.py` | `str06.stage2.compile`（免费；双真源唯一 writer） |
| resolver | `novel/stages/stage2/resolver.py` | 供 book／chapters／associate／compile 调用的定位设施 |

下游跨 Stage 应优先消费正式 artifact 与登记投影，禁止依赖 wire 中间态
（S2-1）。
