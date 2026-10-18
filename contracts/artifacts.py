#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""产物相对路径目录。

这里是路径字面的唯一权威表；schema 注册表、节点 ``produces``、消费投影和运行时读写
只引用符号名。模块只依赖标准库，因而 ``contracts.invariants`` 与 ``contracts.schema``
都能安全导入，不会形成层间环。
"""
from __future__ import annotations

from typing import Final


ARTIFACT_PATHS: Final[dict[str, str]] = {
    # ⚠ 2026-08-22 阶段 C：`revisions.json` 是本层**唯一可变的那一格**，
    #   下面两条退成它的物化投影（路径与字节形态一个字没动，见 `contracts/schema/stage0.py`）。
    #   同批删除的三份：`machine_snapshot.json` / `merge_candidate.json` /
    #   `_audit/edit_{n}.json` —— 它们守的事全部由 revision 链本身承担。
    'stage0.revisions': 'stage0/revisions.json',
    'stage0.clean_text': 'stage0/clean_text.txt',
    'stage1.main_text': 'stage1/main_text.json',
    'stage1.hook_copy': 'stage1/hook_copy.json',
    # ── STR-06 新链（F8a 激活；键即相对路径）──────────────────────────────
    # 【为什么新链的键是路径本身】三条理由，都指向「不许有第二份命名」：
    # ① 候选产物表（`contracts/schema/workflow.py::STR06_CANDIDATE_ARTIFACTS`）
    #   就以 rel 为键，F8a 激活的是同一张表——再套一层符号名就是两份会漂的真相（I-16）；
    # ② 三级家族路径的来源账键是**完整相对路径**（`paths.family_input_key`），
    #   路径即键时账键与注册键天然一致，反查不会塌级；
    # ③ `{nn}`／`{title}` 占位符由 `artifact_regex` 编译（见 `contracts/schema/__init__.py`），
    #   与符号键的 `artifact_rel(name, nn=…)` 是同一套占位符语义。
    # Stage0/1 保持符号键不变——那一层不在本轮范围，它的消费者照旧。
    # ── Stage2（B2 五节点：book／chapters／names／associate／compile）──
    'stage2/observations/book.json': 'stage2/observations/book.json',
    'stage2/observations/ch{nn}.json': 'stage2/observations/ch{nn}.json',
    'stage2/observations/names.json': 'stage2/observations/names.json',
    'stage2/observations/associate.json': 'stage2/observations/associate.json',
    'stage2/blueprints/chapter_blueprints.json': 'stage2/blueprints/chapter_blueprints.json',
    'stage2/acceptance/fold.json': 'stage2/acceptance/fold.json',
    'stage2/characters/characters.json': 'stage2/characters/characters.json',
    # ── Stage4（naming／preserve／body_design／hook_design／compile）──
    'stage4/naming/name_map.json': 'stage4/naming/name_map.json',
    'runtime/preserve_checkpoint.json': 'runtime/preserve_checkpoint.json',
    'stage4/grounding/reconstruction_brief.json': 'stage4/grounding/reconstruction_brief.json',
    'stage4/grounding/semantic_binding.json': 'stage4/grounding/semantic_binding.json',
    'stage4/compiled/execution_plan.json': 'stage4/compiled/execution_plan.json',
    'stage4/acceptance/guard/compile.json': 'stage4/acceptance/guard/compile.json',
    'stage4/acceptance/guard/report.json': 'stage4/acceptance/guard/report.json',
    # R2.8：WriterPacket 逐章指标（派生读数；I-38 不进领域 schema，dict 落盘）
    'stage4/acceptance/packet_metrics.json': 'stage4/acceptance/packet_metrics.json',
    'stage4/design/body_design.json': 'stage4/design/body_design.json',
    'stage4/design/presentation_graph.json': 'stage4/design/presentation_graph.json',
    'stage4/context/hook/hook.json': 'stage4/context/hook/hook.json',
    'stage4/ir/writer_book_ir.json': 'stage4/ir/writer_book_ir.json',
    # ── Stage5（body／hook）──
    'stage5/hook/hook.txt': 'stage5/hook/hook.txt',
    'stage5/hook/receipt.json': 'stage5/hook/receipt.json',
    'stage5/body/ch{nn}.txt': 'stage5/body/ch{nn}.txt',
    'stage5/acceptance/body/ch{nn}.json': 'stage5/acceptance/body/ch{nn}.json',
    # ── Stage7（assemble）──
    'stage7/final/{title}_rewriting.txt': 'stage7/final/{title}_rewriting.txt',
    'stage7/final/delivery_manifest.json': 'stage7/final/delivery_manifest.json',
}
# 只供识别并拒绝退役产物；不进入当前 artifact registry，不提供解码或迁移。
# ⚠ F8a：旧 Stage2/4/5/6/7 链全部路径键移入本表（STR-06 切换，F7e 退役登记）。
#   旧书只读识别为 `legacy_incompatible`：`_refuse_retired_artifact` 在正式
#   decode/encode 口拒绝它们，独立新根重建，不手补旧产物、不继承旧批准。
RETIRED_ARTIFACT_PATHS: Final[dict[str, str]] = {
    # ── 旧 Stage2 链 ──
    'stage2.analyze_receipt': 'stage2/analyze_receipt.json',
    'stage2.analyze_recovery': 'stage2/analyze_recovery.json',
    'stage2.analyze_owner_draft_edit': 'stage2/analyze_owner_draft_edit.json',
    'stage2.analyze_characters_draft_repair': 'stage2/analyze_characters_draft_repair.json',
    'stage2.analyze_global_draft_repair': 'stage2/analyze_global_draft_repair.json',
    'stage2.characters': 'stage2/characters.json',
    'stage2.characters_edit': 'stage2/characters_edit.json',
    'stage2.global_grounding': 'stage2/global_grounding.json',
    'stage2.global_edit': 'stage2/global_edit.json',
    'stage2.global_edit_failure': 'stage2/global_edit_failure.json',
    'stage2.global_edit_input_commit': 'stage2/global_edit_input_commit.json',
    'stage2.global_edit_commit': 'stage2/global_edit_commit.json',
    'stage2.blueprint_character_projection': 'stage2/blueprint_character_projection.json',
    'stage2.chapter_blueprints': 'stage2/chapter_blueprints.json',
    'stage2.blueprint_edit': 'stage2/blueprint_edit.json',
    # ── 旧 Stage4 链 ──
    'stage4.blind_candidate': 'stage4/blind_candidate.json',
    'stage4.blind_chapter_edit': 'stage4/blind_chapter_edit.json',
    'stage4.evidence_binding': 'stage4/evidence_binding.json',
    'stage4.plan_edit': 'stage4/plan_edit.json',
    'stage4.writer_context': 'stage4/writer_context.json',
    # ── 旧 Stage5 链 ──
    'stage5.chapter': 'stage5/ch{nn}.json',
    'stage5.rejected_revision': 'stage5/ch{nn}.rejected.json',
    'stage5.reviewer_rewrite_request': 'stage5/reviewer_rewrite_request.json',
    'stage5.review': 'stage5/review_{n}.json',
    'stage5.trim_audit': 'stage5/trim_audit_ch{nn}.json',
    'stage5.trim_resolution': 'stage5/trim_resolution_ch{nn}.json',
    'stage5.wontfix': 'stage5/wontfix.json',
    # ── Stage6 整层（三候选改选与 prepare 链）──
    'stage6.prepare': 'stage6/prepare.json',
    'stage6.prepare_edit': 'stage6/prepare_edit.json',
    'stage6.prepare_review': 'stage6/prepare_review.json',
    'stage6.prepare_review_decisions': 'stage6/prepare_review_decisions.json',
    'stage6.prepare_commit': 'stage6/prepare_commit.json',
    'stage6.hook': 'stage6/hook.json',
    'stage6.hook_edit': 'stage6/hook_edit.json',
    'stage6.semantic': 'stage6/semantic_{n}.json',
    'stage6.wontfix': 'stage6/wontfix.json',
    # ── 旧 Stage7 链 ──
    'stage7.name_map': 'stage7/name_map.json',
    'stage7.name_map_edit': 'stage7/name_map_edit.json',
    # 命名前移批（2026-10-18，rc26）：str06.stage7.naming 退役，末端产物路径
    # 只读拒识；存量根经 rc25→rc26 同根升级归档重建，不静默采用旧字节。
    'stage7.naming_name_map': 'stage7/naming/name_map.json',
    'stage7.book': 'stage7/{title}_rewriting.txt',
    # ── 更早的退役项（原样保留）──
    'stage2.blueprint_relation_edit': 'stage2/blueprint_relation_edit.json',
    'stage2.knowledge_links': 'stage2/knowledge_links.json',
    'stage2.global_review': 'stage2/global_review.json',
    'stage2.global_review_decisions': 'stage2/global_review_decisions.json',
    # ── B2 五节点重建退役（2026-09-27，D3/D9）：联合抽取的派生输入面──
    # book_extraction 由 compile 直接发布 Characters 双真源取代（D9）；
    # input_snapshots 随 D3「章观察互不依赖」退役（不再有逐章输入快照）。
    # 旧根上的同类文件只读保留，新协议下新根全量重购（I-13，无转换层）。
    'stage2.compiled.book_extraction': 'stage2/compiled/book_extraction.json',
    'stage2.compiled.input_snapshots': 'stage2/compiled/input_snapshots.json',
    'stage3.style_grounding': 'stage3/style_grounding.json',
    # STR-03-S4：正式计划退役；章集/预算/对账边拆入 Design + Context provenance。
    'stage4.plan_chapters': 'stage4/plan_chapters.json',
}



#: 带占位符的路径分三类，**分类是声明的，不是从字面猜的**：
#:
#:   | 类 | 例 | 占位符由谁决定 | 完成度落点 |
#:   |---|---|---|---|
#:   | 轮次编号 | `stage5/review_{n}.json` | 盘上有几份 | `rounds_done[节点]` |
#:   | 逐章 | `stage5/body/ch{nn}.txt` | 计划里有哪些章 | `chapters_done` |
#:   | **书级** | `stage7/{title}_rewriting.txt` | **这本书是谁** | `artifacts[]`（解析后是具名的） |
#:
#: ⚠⚠ **这张表存在的理由**：前两类问「盘上有几份」，第三类问「它叫什么名字」——
#: 解析出来仍是**一份**具名产物。而 `state.py::_is_pattern` 只看 `'{' in rel`、
#: `paths.is_numbered_pattern` 只看「tail 里恰好一个占位符」，两者都会把书级那条
#: **误判**成轮次编号类：前者把它整个丢出 `artifacts[]`（成书从完成度、freshness 徽章、
#: inbox 里同时消失），后者拿非数字去解析编号得到空集。
#: ⇒ 书级路径必须在那两道分类之前先被解析掉，而「谁是书级」只能从这里查，不能靠字面猜。
# ⚠ F8a：书级路径翻为新链成书（`stage7/final/{title}_rewriting.txt`）。
BOOK_SCOPED_PATHS: Final[frozenset[str]] = frozenset({'stage7/final/{title}_rewriting.txt'})


def artifact_rel(name: str, **values: object) -> str:
    """取路径模板；给参数时返回具体路径，``nn`` 统一补零两位。"""
    try:
        template = ARTIFACT_PATHS[name]
    except KeyError:
        raise KeyError(f'未知产物 id：{name!r}') from None
    if not values:
        return template
    supplied = dict(values)
    if 'nn' in supplied:
        supplied['nn'] = f'{int(supplied["nn"]):02d}'
    try:
        return template.format(**supplied)
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError(f'{name} 的路径参数不完整或非法：{values!r}') from exc


STAGE0_REVISIONS = artifact_rel('stage0.revisions')
STAGE0_CLEAN_TEXT = artifact_rel('stage0.clean_text')
STAGE1_MAIN_TEXT = artifact_rel('stage1.main_text')
STAGE1_HOOK_COPY = artifact_rel('stage1.hook_copy')

# ── STR-06 新链路径常量（F8a 激活；键即 rel，常量只是便利别名）────────────
# 新链 runner／store／state 一律直接写 rel 字面（与候选产物表同一来源）；
# 这些常量只服务「同一个路径在多处出现」的场合，不构成第二份命名权威。
STR06_STAGE2_OBS_BOOK = artifact_rel('stage2/observations/book.json')
STR06_STAGE2_OBS_CHAPTER = artifact_rel('stage2/observations/ch{nn}.json')
STR06_STAGE2_OBS_NAMES = artifact_rel('stage2/observations/names.json')
STR06_STAGE2_OBS_ASSOCIATE = artifact_rel('stage2/observations/associate.json')
STR06_STAGE2_BLUEPRINTS = artifact_rel('stage2/blueprints/chapter_blueprints.json')
STR06_STAGE2_FOLD = artifact_rel('stage2/acceptance/fold.json')
STR06_STAGE2_CHARACTERS = artifact_rel('stage2/characters/characters.json')
STR06_PRESERVE_CHECKPOINT = artifact_rel('runtime/preserve_checkpoint.json')
STR06_RECONSTRUCTION_BRIEF = artifact_rel('stage4/grounding/reconstruction_brief.json')
STR06_SEMANTIC_BINDING = artifact_rel('stage4/grounding/semantic_binding.json')
STR06_EXECUTION_PLAN = artifact_rel('stage4/compiled/execution_plan.json')
STR06_GUARD_COMPILE = artifact_rel('stage4/acceptance/guard/compile.json')
STR06_GUARD_REPORT = artifact_rel('stage4/acceptance/guard/report.json')
STR06_PACKET_METRICS = artifact_rel('stage4/acceptance/packet_metrics.json')
STR06_BODY_DESIGN = artifact_rel('stage4/design/body_design.json')
STR06_PRESENTATION_GRAPH = artifact_rel('stage4/design/presentation_graph.json')
STR06_HOOK_CONTEXT = artifact_rel('stage4/context/hook/hook.json')
STR06_WRITER_BOOK_IR = artifact_rel('stage4/ir/writer_book_ir.json')
STR06_HOOK_TEXT = artifact_rel('stage5/hook/hook.txt')
STR06_HOOK_RECEIPT = artifact_rel('stage5/hook/receipt.json')
STR06_STAGE5_BODY = artifact_rel('stage5/body/ch{nn}.txt')
STR06_STAGE5_BODY_RECEIPT = artifact_rel('stage5/acceptance/body/ch{nn}.json')
STR06_NAME_MAP = artifact_rel('stage4/naming/name_map.json')
STR06_DELIVERY_MANIFEST = artifact_rel('stage7/final/delivery_manifest.json')


def str06_chapter_observation_rel(ordinal: int) -> str:
    """新链逐章观察路径（`stage2/observations/ch{nn}.json`，B2 五节点）。"""
    return artifact_rel('stage2/observations/ch{nn}.json', nn=ordinal)


def str06_body_rel(ordinal: int) -> str:
    """新链逐章正文路径（`stage5/body/ch{nn}.txt`）。"""
    return artifact_rel('stage5/body/ch{nn}.txt', nn=ordinal)


def str06_body_receipt_rel(ordinal: int) -> str:
    """新链逐章接受回执路径（`stage5/acceptance/body/ch{nn}.json`）。"""
    return artifact_rel('stage5/acceptance/body/ch{nn}.json', nn=ordinal)


def str06_book_rel(title: str) -> str:
    """新链成书路径（`stage7/final/{title}_rewriting.txt`）。"""
    return artifact_rel('stage7/final/{title}_rewriting.txt', title=title)


# ── 新链产物的 RC 批次标签（F8a 激活；F8c 分流自候选产物表）──────────────────
# 【它为什么单独一张表】`ARTIFACT_PATHS` 答「路径叫什么」，本表答「盘上形状是哪
# 一批定格的」。两者是同一份产物的两格不同事实：路径进来源账键与消费路由，RC
# 标签进候选契约身份（`candidate_contract_identity` 的输入之一）与
# `contracts/schema/workflow.py::RC_LABELS` 的版本面。信封版本（`current_version`）
# 对「切换时新建的命名空间」不携带信息（盘上没有旧信封字节可读），形状史由
# ① 模型自带 `contract_version` 字面量 与 ② 本表 两条线承载。
# ⚠ 表键与 `ARTIFACT_PATHS` 的新链条目**逐字相等**（22 条）；对账在
# `tests/test_str06_f7e_freeze.py`。改一条路径就必须同批改这里，否则契约身份
# 与版本面当场分家。
# rc14（2026-10-03 审阅修复）：Brief 保留逐次 knowledge_changes；Design v7
# know 短引用与 confirms 明确拍位和事实关联；五份受影响产物显式换身份。
# 当时 rc12/rc13 无损字段经 store 集中迁移；rc21 质量批退役该旧质量迁移面，
# 只保留形状未变化 ExecutionPlan 的登记适配，其它质量产物显式拒旧。
STR06_ARTIFACT_RC_VERSIONS: Final[dict[str, str]] = {
    # rc12（2026-10-01 R10a 收口，PLAN §二.4 全表）：关键语义交接批把源理解到
    # 正文的全链形状定格——Stage2 引文证据与事件时态（book v3／chapters v2／
    # Blueprint v15）、Preserve 锚定与 series（preserve-v2/policy 5）、Brief
    # 锁定项与 Design v5、Bind series 同拍判据与 epistemic_lock_order 位置
    # Guard。源理解变化波及全书链（Characters→Blueprint→Preserve→Design→
    # Bind→IR→回执→交付），故**全表统一 rc12**：旧候选根按版本关闭式拒读
    # （协议不兼容，不计语义检出），只读保留；names／bind／hook-design 协议
    # 字面量不变，只因输入投影变而换身份。
    # rc6（2026-09-27 B2 五节点重建，D3/D8/D9）：观察与关联协议落地
    # （`contracts/schema/observation.py`，协议版本 stage2-observation-v1）；
    # 联合抽取的派生输入（book_extraction／input_snapshots）退役，Characters
    # 与 Blueprint 改由 str06.stage2.compile 作为唯一 writer 发布。
    # rc15（2026-10-04 证据寻址，RC-26/27）：证据段＝自然段，段号含义改变；
    # 凡携带段号的产物（三份观察、Characters、Blueprint）换身份，旧根拒读。
    # associate（只有位置句柄）、fold 与 Stage4 起各产物不带段号，不虚构升版。
    # rc18（2026-10-07 CS-S 1b）：观察 canonical 升 v5——InitialFact.knowledge
    # 与 effect 的 disclosure／proposition／scope 删；旧 rc15 根只读拒收。
    # rc19（CS-S Phase 2）：book v6 增 facts（书级命题词表＋known_at_start）；
    # chapters v6 增 know.fact 引用——观察换身份，旧 rc18 根只读拒收。
    # rc20（CS-S Phase 3）：book v7／chapters v7——initial_state 键入 CHANGE_KEYS
    # 闭集；chapters 增拍级 access（获知通道）、know.who 禁 reader（读者知情由
    # compile 派生）。旧 rc19 根只读拒收。
    'stage2/observations/book.json': 'str06-rc20',
    'stage2/observations/ch{nn}.json': 'str06-rc20',
    # rc18（CS-S 1c）：names canonical 升 v4——构词 surface_forms 由 compiler
    # 派生、resolutions 来自变体窄调用（增 not_name）。Phase 2 形状未变不升。
    'stage2/observations/names.json': 'str06-rc18',
    # rc9（2026-09-28 R1 关联无损）：associate 协议独立升版 stage2-associate-v2
    # ——typed 作用对象显式序列化＋状态句柄位置编码（ch{nn}.b{拍}.e{效应序}.{key}
    # ／book.e{条目序}.{key}）。三类观察协议字面量与请求逐字不变；canonical
    # 无形状变化，不虚构升版（A1.5）。
    # rc17（2026-10-06 CS-H H4，RC-65）：协议升 stage2-associate-v3——
    # same_state_dimension 改配维度句柄（dim{nn}，按（作用对象，键）地址聚合），
    # 章内已自开自闭的 close 不进可配对闭集；旧 v2 wire 关闭式拒读。
    # rc19（CS-S Phase 2，S2-9 落地）：协议升 stage2-associate-v4——条目级窄
    # resolver（输入＝代码给出的未决候选＋最小文本，逐候选 verdict；无候选＝
    # 零调用）；跨章因果独立窄调用 wire stage2-cross-cause-v1（D-2 A）。
    # rc20（CS-S Phase 3）：协议升 stage2-associate-v5——same_state_dimension
    # 消失（change 键闭集＝键即 canonical，维度句柄与整类合并面删除）。
    'stage2/observations/associate.json': 'str06-rc20',
    # v13（2026-09-26 质量改进批次二）：拍模型三类转移＋场景分段；v12 存量拒读。
    # Phase 2：cognition 命题引用可指向 f{nn}（词表编号）——值域扩展非形状变化，
    # 不升版（Blueprints 保持 rc18）。
    # rc20（CS-S Phase 3）：Blueprints 升 v17——Beat 增 access、效果键入
    # CHANGE_KEYS 闭集、cognition 的 reader 条目＝compile 派生读者知情。
    'stage2/blueprints/chapter_blueprints.json': 'str06-rc20',
    # v13 同批：fold 读数结构化（每章空转移拍比例＋书末未揭示 concealed）。
    # rc18：fold 删 concealed_unrevealed（concealed 效应已删）。
    # rc19：fold 增知情时间线读数（facts known_at_start 起步的逐命题知情读数，
    # T-5：结构化只读读数，不进下游 API）。
    # rc20：fold 时间线含派生 reader 条目（Phase 3 读者知情派生进 fold 读数）。
    'stage2/acceptance/fold.json': 'str06-rc20',
    # rc20（CS-S Phase 3）：Characters 升 v6——initial_state 键入 CHANGE_KEYS
    # 闭集（schema 层兜底直接构造的表外键）。
    'stage2/characters/characters.json': 'str06-rc20',
    # rc21 创作材料批：六类质量产物增可选细节/显式系列概括与目标前情。
    # rc22 事件锚点批：ObligationAnchor 增独立事件与正式证据窗口，六类质量
    # 产物同批显式升版；rc21/v4 不做隐式迁移，FREE owner patch 全量复验。
    # checkpoint 也携带 contract_version；旧版/缺版本不可补空值作为新证据。
    # 旧五质量产物迁移退役，仅未变化 ExecutionPlan 保留登记的 rc12/13 适配。
    'runtime/preserve_checkpoint.json': 'str06-rc26',
    # rc13（2026-10-03 CS-3 余项，D5 统一＋world 规则 id）：读者揭示统一走
    # knowledge(holder=reader)（TargetDelta 删 reader 域与 confirms；Brief 知
    # 情锁同写法）；Brief.book_constraints 升 WorldConstraint（wc_{nn} 稳定
    # 规则 id）；ConstraintSpec.domain 改类别标签。历史登记迁移在 rc21 退役，
    # 此行只接受显式当前质量契约；ExecutionPlan 的独立无损适配仍保留。
    # rc17（2026-10-06 CS-H H3，RC-61）：Brief 认知变化条目新增 requires_released
    # （必保揭名派生，随认知增量携带）；缺省读入兼容旧盘，身份随 rc17 换。
    # rc18：ObligationAnchor.visibility 删（effect 命题锚只在 cognition）。
    'stage4/grounding/reconstruction_brief.json': 'str06-rc26',
    # rc16（2026-10-04 CS-F2）：Binding 增 fact_bindings（事实锁→已有目标状态的
    # 映射，只存引用不复制 value）；旧 rc12 Binding 缺该映射、无损推不回，拒读。
    # rc17（2026-10-06 CS-H H3，RC-63）：BriefFactLock 增 subjects（主体包含判定）。
    'stage4/grounding/semantic_binding.json': 'str06-rc26',
    'stage4/compiled/execution_plan.json': 'str06-rc14',
    'stage4/acceptance/guard/compile.json': 'str06-rc12',
    'stage4/acceptance/guard/report.json': 'str06-rc12',
    # rc10 同批新增（R2.8 引擎批）：compile 落盘的 WriterPacket 指标（audit_only）。
    'stage4/acceptance/packet_metrics.json': 'str06-rc12',
    # rc7（2026-09-27 B3 前半）：Stage4 四协议独立版化（stage4-{preserve,design,
    # bind,hook-design}-v1），Design wire 瘦身——场景/拍 ID、复合引用、权重归一
    # 与整数预算由 compiler 生成（I-21）；TargetDesign 增结构化跨章字段；读者
    # 确认必填事实地址（D4）。形状/判据变化，旧 Design／graph／WriterIR 拒读。
    # rc10（2026-09-28 R2 语义送达）：Design wire 升 stage4-design-v2——拍上线程
    # （threads）、书级 world_rules、章级 entry；四个静态跨章列表删除（AUD-04）。
    'stage4/design/body_design.json': 'str06-rc25',
    # rc23：Hook 组织与阅读期待改为正式选择，正文载荷仍是 rc22。
    'stage4/design/presentation_graph.json': 'str06-rc25',
    # rc16（2026-10-05 CS-F2）：HookContext 增 withheld_bindings（按 Hook 自选
    # surface_releases 算好的唯一绑定称谓表，渲染期投影）；旧 rc12 楔子上下文
    # 拒读，免费重编译。正文与楔子投影同尺（RC-40/41）。
    # rc17（2026-10-06 CS-H H3，RC-60）：HookContext 增 display_faces/avoid_lines
    # （显示面投影与材料所引场 avoid 并集）。
    # rc18：继承全书风格与人物声线；材料强调与跳时显式投影，取消字数边界。
    # rc24（2026-10-10）：恢复按书级章预算派生的篇幅包络（length_low/high 目标
    # 区间随请求送达写手 + length_cap 机械硬上限由 validator 拒收）——rc18 全
    # 放开后写手按材料戏剧密度膨胀（选材收缩到 4 拍 1 develop 仍写满千字）；
    # 不回到 rc22 前对全书写死的 280/360。旧上下文免费重编译。
    'stage4/context/hook/hook.json': 'str06-rc25',
    # rc7 同批：BookBrief 带整书风格与稳定人物声线；PendingConfirmation 带事实
    # 地址（D4），投影按地址精确到消费该地址的章包。
    # rc8（2026-09-27 B4，D2）：ChapterIR 增章级验证材料（称谓许可、禁用字面
    # compile 时算好）；完整 IR 只作存储与审计，运行时按章投影章包、不落盘。
    # rc10（R2）：拍级读取值挂拍（AUD-03）、线程投影面、continuity 分区与
    # 书级 world_rules 进 BookBrief——IR 形状升 rc10。
    # rc16（2026-10-04 CS-F2）：ChapterIR 增 withheld_bindings（禁用字面中已证明唯一
    # 绑定人物的完整登记称谓，供渲染期确定性投影）；旧 rc14 IR 拒读，免费重编译。
    # rc17（2026-10-06 CS-H H3）：ChapterIR 增 display_fallback（回落显示面分表），
    # display_prefix 改只含章首已释放称谓、增 requires_released（必保揭名），
    # BeatIR 增 release_introductions；免费重编译。
    # rc18（CS-S 1b 补升）：ChapterIR 经 VerificationIR.obligations 内嵌
    # ObligationAnchor，其 visibility 字段在 1b 已删，IR 形状随之变化；不升版则
    # 旧 rc17 IR 读入只得到无信息的 extra_forbidden（I-13）。免费重编译。
    'stage4/ir/writer_book_ir.json': 'str06-rc25',
    'stage5/hook/hook.txt': 'str06-rc12',
    'stage5/hook/receipt.json': 'str06-rc25',
    'stage5/body/ch{nn}.txt': 'str06-rc12',
    # rc5（rc8 同批）：回执改绑章包摘要＋请求身份＋验证版本＋改名前正文摘要，
    # 不再绑定全书 IR 与前文链（D2）；各章独立有效，支持集合恢复。
    # rc6（rc10 同批，R2 身份拆分）：packet_sha256 单摘要拆成写手视图摘要
    # （writer_view_sha256，材料含实际正文 system、renderer 与写手协议版本）＋验证输入摘要
    # （validation_sha256：禁用字面、称谓许可、篇幅带）＋Machine 审计摘要
    # （machine_view_sha256，仅审计不触发重购）。
    # rc17（2026-10-06 CS-H H3，RC-61）：BODY_VALIDATOR 升 v2——必保揭名必现
    # 字面与场景标记进章级机械校验，回执 validator_version 随行。
    'stage5/acceptance/body/ch{nn}.json': 'str06-rc25',
    # rc26（2026-10-18 命名前移批）：str06.stage4.naming 前移，NameMap v6 落
    # stage4/naming/name_map.json；旧 stage7 末端路径与 NameMapV5 字节拒读。
    'stage4/naming/name_map.json': 'str06-rc26',
    'stage7/final/{title}_rewriting.txt': 'str06-rc12',
    'stage7/final/delivery_manifest.json': 'str06-rc12',
}


# ── 旧链路径常量（F8a 起全部移入退役表；只供旧盘只读识别与拒绝）──────────
# ⚠ 这些常量随 F8c 旧链消费者删除一并删除（F6d/F7e 退役登记）——2026-10-05
# 合并复审先清掉零引用的 34 个别名；下列 10 个仍有现役消费者（store／
# source_identity 的只读解码与 tests 守卫），保留到消费者一并退役。它们**不再
# 能通过 `artifact_rel` 取到**——那正是 `legacy_incompatible` 的机械形状：
# 旧键不在现役表里，正式 decode/encode 路径拒绝旧字节。
STAGE2_ANALYZE_RECEIPT = RETIRED_ARTIFACT_PATHS['stage2.analyze_receipt']
STAGE2_BLUEPRINT_CHARACTER_PROJECTION = RETIRED_ARTIFACT_PATHS['stage2.blueprint_character_projection']
STAGE2_GLOBAL_REVIEW = RETIRED_ARTIFACT_PATHS['stage2.global_review']
STAGE2_GLOBAL_EDIT = RETIRED_ARTIFACT_PATHS['stage2.global_edit']
RETIRED_STAGE3_STYLE_GROUNDING = RETIRED_ARTIFACT_PATHS['stage3.style_grounding']
STAGE4_PLAN_EDIT = RETIRED_ARTIFACT_PATHS['stage4.plan_edit']
STAGE5_TRIM_AUDIT = RETIRED_ARTIFACT_PATHS['stage5.trim_audit']
STAGE5_TRIM_RESOLUTION = RETIRED_ARTIFACT_PATHS['stage5.trim_resolution']
# stage6.semantic（A/B 语义审判）产物已随节点退役（2026-09-15）；
# 旧书 semantic_N.json 不再被现役读取。
STAGE6_HOOK_EDIT = RETIRED_ARTIFACT_PATHS['stage6.hook_edit']
STAGE7_NAME_MAP_EDIT = RETIRED_ARTIFACT_PATHS['stage7.name_map_edit']
