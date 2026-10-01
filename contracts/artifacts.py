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
    # ── Stage4（preserve／body_design／hook_design／bind／compile）──
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
    # ── Stage7（naming／assemble）──
    'stage7/naming/name_map.json': 'stage7/naming/name_map.json',
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
#:   | 逐章 | `stage5/ch{nn}.json` | 计划里有哪些章 | `chapters_done` |
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
# ⚠ B2 退役派生输入（2026-09-27，D3/D9）：只能识别与拒绝，不再提供读写。
STR06_STAGE2_BOOK_EXTRACTION = RETIRED_ARTIFACT_PATHS['stage2.compiled.book_extraction']
STR06_STAGE2_INPUT_SNAPSHOTS = RETIRED_ARTIFACT_PATHS['stage2.compiled.input_snapshots']
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
STR06_NAME_MAP = artifact_rel('stage7/naming/name_map.json')
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
    'stage2/observations/book.json': 'str06-rc12',
    'stage2/observations/ch{nn}.json': 'str06-rc12',
    'stage2/observations/names.json': 'str06-rc12',
    # rc9（2026-09-28 R1 关联无损）：associate 协议独立升版 stage2-associate-v2
    # ——typed 作用对象显式序列化＋状态句柄位置编码（ch{nn}.b{拍}.e{效应序}.{key}
    # ／book.e{条目序}.{key}）。三类观察协议字面量与请求逐字不变；canonical
    # 无形状变化，不虚构升版（A1.5）。
    'stage2/observations/associate.json': 'str06-rc12',
    # v13（2026-09-26 质量改进批次二）：拍模型三类转移＋场景分段；v12 存量拒读。
    'stage2/blueprints/chapter_blueprints.json': 'str06-rc12',
    # v13 同批：fold 读数结构化（每章空转移拍比例＋书末未揭示 concealed）。
    'stage2/acceptance/fold.json': 'str06-rc12',
    'stage2/characters/characters.json': 'str06-rc12',
    'runtime/preserve_checkpoint.json': 'str06-rc12',
    'stage4/grounding/reconstruction_brief.json': 'str06-rc12',
    'stage4/grounding/semantic_binding.json': 'str06-rc12',
    'stage4/compiled/execution_plan.json': 'str06-rc12',
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
    'stage4/design/body_design.json': 'str06-rc12',
    'stage4/design/presentation_graph.json': 'str06-rc12',
    'stage4/context/hook/hook.json': 'str06-rc12',
    # rc7 同批：BookBrief 带整书风格与稳定人物声线；PendingConfirmation 带事实
    # 地址（D4），投影按地址精确到消费该地址的章包。
    # rc8（2026-09-27 B4，D2）：ChapterIR 增章级验证材料（称谓许可、禁用字面
    # compile 时算好）；完整 IR 只作存储与审计，运行时按章投影章包、不落盘。
    # rc10（R2）：拍级读取值挂拍（AUD-03）、线程投影面、continuity 分区与
    # 书级 world_rules 进 BookBrief——IR 形状升 rc10。
    'stage4/ir/writer_book_ir.json': 'str06-rc12',
    'stage5/hook/hook.txt': 'str06-rc12',
    'stage5/hook/receipt.json': 'str06-rc12',
    'stage5/body/ch{nn}.txt': 'str06-rc12',
    # rc5（rc8 同批）：回执改绑章包摘要＋请求身份＋验证版本＋改名前正文摘要，
    # 不再绑定全书 IR 与前文链（D2）；各章独立有效，支持集合恢复。
    # rc6（rc10 同批，R2 身份拆分）：packet_sha256 单摘要拆成写手视图摘要
    # （writer_view_sha256，材料含 renderer 与写手协议版本）＋验证输入摘要
    # （validation_sha256：禁用字面、称谓许可、篇幅带）＋Machine 审计摘要
    # （machine_view_sha256，仅审计不触发重购）。
    'stage5/acceptance/body/ch{nn}.json': 'str06-rc12',
    'stage7/naming/name_map.json': 'str06-rc12',
    'stage7/final/{title}_rewriting.txt': 'str06-rc12',
    'stage7/final/delivery_manifest.json': 'str06-rc12',
}


# ── 旧链路径常量（F8a 起全部移入退役表；只供旧盘只读识别与拒绝）──────────
# ⚠ 这些常量随 F8c 旧链消费者删除一并删除（F6d/F7e 退役登记）。它们**不再
# 能通过 `artifact_rel` 取到**——那正是 `legacy_incompatible` 的机械形状：
# 旧键不在现役表里，正式 decode/encode 路径拒绝旧字节。
STAGE2_ANALYZE_RECEIPT = RETIRED_ARTIFACT_PATHS['stage2.analyze_receipt']
STAGE2_ANALYZE_RECOVERY = RETIRED_ARTIFACT_PATHS['stage2.analyze_recovery']
STAGE2_ANALYZE_OWNER_DRAFT_EDIT = RETIRED_ARTIFACT_PATHS['stage2.analyze_owner_draft_edit']
STAGE2_ANALYZE_CHARACTERS_DRAFT_REPAIR = RETIRED_ARTIFACT_PATHS['stage2.analyze_characters_draft_repair']
STAGE2_ANALYZE_GLOBAL_DRAFT_REPAIR = RETIRED_ARTIFACT_PATHS['stage2.analyze_global_draft_repair']
STAGE2_CHARACTERS = RETIRED_ARTIFACT_PATHS['stage2.characters']
STAGE2_CHARACTERS_EDIT = RETIRED_ARTIFACT_PATHS['stage2.characters_edit']
STAGE2_GLOBAL_GROUNDING = RETIRED_ARTIFACT_PATHS['stage2.global_grounding']
STAGE2_GLOBAL_REVIEW = RETIRED_ARTIFACT_PATHS['stage2.global_review']
STAGE2_GLOBAL_REVIEW_DECISIONS = RETIRED_ARTIFACT_PATHS['stage2.global_review_decisions']
STAGE2_GLOBAL_EDIT = RETIRED_ARTIFACT_PATHS['stage2.global_edit']
STAGE2_GLOBAL_EDIT_FAILURE = RETIRED_ARTIFACT_PATHS['stage2.global_edit_failure']
STAGE2_GLOBAL_EDIT_INPUT_COMMIT = RETIRED_ARTIFACT_PATHS['stage2.global_edit_input_commit']
STAGE2_GLOBAL_EDIT_COMMIT = RETIRED_ARTIFACT_PATHS['stage2.global_edit_commit']
STAGE2_BLUEPRINT_CHARACTER_PROJECTION = RETIRED_ARTIFACT_PATHS['stage2.blueprint_character_projection']
STAGE2_CHAPTER_BLUEPRINTS = RETIRED_ARTIFACT_PATHS['stage2.chapter_blueprints']
STAGE2_KNOWLEDGE_LINKS = RETIRED_ARTIFACT_PATHS['stage2.knowledge_links']
STAGE2_BLUEPRINT_EDIT = RETIRED_ARTIFACT_PATHS['stage2.blueprint_edit']
STAGE2_BLUEPRINT_RELATION_EDIT = RETIRED_ARTIFACT_PATHS['stage2.blueprint_relation_edit']
RETIRED_STAGE3_STYLE_GROUNDING = RETIRED_ARTIFACT_PATHS['stage3.style_grounding']
STAGE4_BLIND_CANDIDATE = RETIRED_ARTIFACT_PATHS['stage4.blind_candidate']
STAGE4_BLIND_CHAPTER_EDIT = RETIRED_ARTIFACT_PATHS['stage4.blind_chapter_edit']
STAGE4_EVIDENCE_BINDING = RETIRED_ARTIFACT_PATHS['stage4.evidence_binding']
STAGE4_PLAN_CHAPTERS = RETIRED_ARTIFACT_PATHS['stage4.plan_chapters']
STAGE4_PLAN_EDIT = RETIRED_ARTIFACT_PATHS['stage4.plan_edit']
STAGE4_WRITER_CONTEXT = RETIRED_ARTIFACT_PATHS['stage4.writer_context']
STAGE5_CHAPTER = RETIRED_ARTIFACT_PATHS['stage5.chapter']
STAGE5_REJECTED_REVISION = RETIRED_ARTIFACT_PATHS['stage5.rejected_revision']
STAGE5_REVIEWER_REWRITE_REQUEST = RETIRED_ARTIFACT_PATHS['stage5.reviewer_rewrite_request']
STAGE5_REVIEW = RETIRED_ARTIFACT_PATHS['stage5.review']
STAGE5_TRIM_AUDIT = RETIRED_ARTIFACT_PATHS['stage5.trim_audit']
STAGE5_TRIM_RESOLUTION = RETIRED_ARTIFACT_PATHS['stage5.trim_resolution']
STAGE5_WONTFIX = RETIRED_ARTIFACT_PATHS['stage5.wontfix']
STAGE6_PREPARE = RETIRED_ARTIFACT_PATHS['stage6.prepare']
STAGE6_PREPARE_EDIT = RETIRED_ARTIFACT_PATHS['stage6.prepare_edit']
STAGE6_PREPARE_REVIEW = RETIRED_ARTIFACT_PATHS['stage6.prepare_review']
STAGE6_PREPARE_REVIEW_DECISIONS = RETIRED_ARTIFACT_PATHS['stage6.prepare_review_decisions']
STAGE6_PREPARE_COMMIT = RETIRED_ARTIFACT_PATHS['stage6.prepare_commit']
STAGE6_HOOK = RETIRED_ARTIFACT_PATHS['stage6.hook']
STAGE6_HOOK_EDIT = RETIRED_ARTIFACT_PATHS['stage6.hook_edit']
# stage6.semantic（A/B 语义审判）产物已随节点退役（2026-09-15）；
# 旧书 semantic_N.json 不再被现役读取。
STAGE6_WONTFIX = RETIRED_ARTIFACT_PATHS['stage6.wontfix']
STAGE7_NAME_MAP = RETIRED_ARTIFACT_PATHS['stage7.name_map']
STAGE7_NAME_MAP_EDIT = RETIRED_ARTIFACT_PATHS['stage7.name_map_edit']
#: ⚠ **它是模板，不是可以直接落盘的路径**：里面还有 `{title}`。
#: 要具体路径请走 `novel/paths.py::book_txt_rel(novel_dir)`；拿这个常量去
#: `store.path()` / 相等比较，得到的是一个字面叫 `{title}_rewriting.txt` 的东西。
STAGE7_BOOK = RETIRED_ARTIFACT_PATHS['stage7.book']
