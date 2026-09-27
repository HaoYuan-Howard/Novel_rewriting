# Stage2 局部架构

本文件维护 Stage2（STR-06 新链）的局部不变量与目录约束。跨 Stage 硬闸见根
`AGENTS.md` 的 I-\* / S-\*；产品行为与 R 编号见 `docs/BACKEND_CONTRACT.md`；字段
形状见 `docs/schema/` 与 `contracts/schema/`。不复制根不变量全文。

⚠ F8c（2026-09-25）：本包由 STR-06 迁移包的 Stage2 两个 owner
分流重建，**不是旧 Stage2 链的回流**（旧链整层已退役，只读拒识为
`legacy_incompatible`）。目录形状按新链 owner 重写，不复用旧包的
`analyze/characters/knowledge/blueprint/shared` 分层。

心智公式：LLM = understand → Compiler = normalize → Canonical = truth →
Projection = expose → Validator = enforce。

## 局部不变量（S2-\*）

| 编号 | 规则 |
|---|---|
| S2-1 | Stage2 的外部业务真相只有已登记的正式 artifact（`contracts/artifacts.py::ARTIFACT_PATHS` 的 `stage2/**` 键）。中间 wire／snapshot／recovery 状态都不是下游 API。 |
| S2-2 | LLM 只负责语义购买；不得直接写正式 artifact。付费响应的 parse／shape 问题走免费修复，不得重新购买掩盖契约或编译器缺陷。 |
| S2-3 | Compiler 只做确定性转换：ID、ref、anchor、closed-set binding、normalization、projection。禁止补猜语义（I-20／I-21）。 |
| S2-4 | Projection 是 canonical artifact 的只读派生视图，不得反写 truth，也不得形成第二真相源（I-16）。 |
| S2-5 | 源抽取的 core 与章 Blueprint 落为正式派生产物（`stage2/compiled/book_extraction.json`、`stage2/blueprints/chapter_blueprints.json`）：下游 owner 的单节点输入从盘面闭合，不靠内存传递。**过渡注记（2026-09-27）**：职责解耦裁决 D9 将在 B2 由免费 `str06.stage2.compile` 直接发布 Characters＋Blueprint 双真源并退役 `stage2/compiled/book_extraction.json`，本条届时改写；见 `docs/schema/str06.md` Stage2 五节点表。 |
| S2-6 | Stage2 只产出 artifact、projection 与机械证明。是否进入下一节点由 orchestrator 决定；producer／compile 不得顺手启动下游节点。 |

## 目录约束

```text
stage2/
  source.py  # stage2.source owner：book＋chNN 语义抽取、Blueprint 编译、证据折叠
  names.py   # stage2.names owner：源名称结构窄调用并发布 Characters v4
```

- 正式 schema 只在 `contracts/schema/`（及 `docs/schema/`）；stage2 不复制 schema truth。
- 跨 stage 共用的确定性原语（字面扫描、机械判定、prompt 组装、场景投影、阅读界面
  渲染）住 `novel/stages/common.py`，不在本包复制第二份。
- 薄编排与设施装配住 `novel/stages/chain.py`；本包不 import `novel.llm`／
  `novel.store`／`novel.orchestrator`（守卫见 `tests/test_str06_invariants.py`）。
- 不为对称铺空目录树；公式落在各 owner 内部模块。

## 现役公开面

| Owner | 权威 path | 节点 |
|---|---|---|
| source | `novel/stages/stage2/source.py` | `str06.stage2.source` |
| names | `novel/stages/stage2/names.py` | `str06.stage2.names` |

（过渡注记：2026-09-27 职责解耦裁决 D8/D9 将在 B2 把本包拆为 book／chapters／names/
associate／compile 五个 owner、`str06.stage2.source` 退役并同步本表与目录约束；
目标形态见 `docs/schema/str06.md` Stage2 五节点表，B2 落地前本表仍是现役落点。）

下游跨 Stage 应优先消费正式 artifact 与登记投影，禁止依赖 wire／snapshot 中间态
（S2-1）。
