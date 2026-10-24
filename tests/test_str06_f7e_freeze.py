#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_f7e_freeze —— 候选契约冻结基线钉在测试里（免费）。

【冻结什么】激活的节点表（`contracts/invariants.py::NODES`）、产物表
（`contracts/artifacts.py::ARTIFACT_PATHS`＋`STR06_ARTIFACT_RC_VERSIONS`）与
运行期配置。本文件把冻结时的契约身份、节点／产物计数写成字面快照：表里任何
一行变化都会改变 `candidate_contract_identity()`，本测试当场红——不能
「顺手多激活一个节点」而不改冻结记录。

【B2 重新冻结（2026-09-27）】职责解耦 D9 把 Stage2 拆为五节点
（book／chapters／names／associate／compile），`str06.stage2.source` 退役，
公开节点 11→14；观察与关联协议落地（`contracts/schema/observation.py`，
stage2-observation-v1），联合抽取派生输入（book_extraction／input_snapshots）
退役。F7e 基线（11 节点，`7fb5ecfa…`）原样保存在 Git 历史里；旧候选根按
「契约表已漂移」拒读，只读保留，新协议下新根全量重购（I-13，无转换层）。

【B3 前半重新冻结（2026-09-27）】Stage4 四协议独立版化
（stage4-{preserve,design,bind,hook-design}-v1），Design wire 瘦身——场景/拍
ID、复合引用、权重归一与整数预算改由 compiler 生成（I-21）；TargetDesign 增
结构化跨章字段；读者确认必填事实地址（D4）。节点与运行期配置不变；Stage4
产物（body_design／presentation_graph／writer_book_ir）版本升 `str06-rc7`。
B2 基线（14 节点，`a0f5fb5e…`）原样保存在 Git 历史里；既有候选根按
「契约表已漂移」拒读，只读保留。

【B4 前半重新冻结（2026-09-27）】章包写手（D2）：WriterBookIR 增章级验证材料
（称谓许可／禁用字面 compile 时算好）并升 `str06-rc8`；章回执改绑章包摘要
（回执升 `str06-rc5`），不再绑定全书 IR 与前文链；正文节点并发与集合恢复
（各章独立有效）。节点集合与运行期配置不变。B3 基线（14 节点，
`29cc19d0…`）原样保存在 Git 历史里；既有候选根按「契约表已漂移」拒读，
只读保留，新协议下新根全量重购（I-13，无转换层）。

【R1 重新冻结（2026-09-28）】Stage2 关联无损：associate 观察协议独立升版
`stage2-associate-v2`（`contracts/schema/observation.py`；typed 作用对象显式
序列化＋状态句柄位置编码），观察产物 `stage2/observations/associate.json`
形状升 `str06-rc9`。三类观察协议（book／chapters／names）字面量与请求逐字
不变；canonical（Characters／Blueprint）无形状变化，不升版（A1.5）。节点
集合与运行期配置不变。B4 基线（14 节点，`d552b280…`）原样保存在 Git 历史
里；既有候选根按「契约表已漂移」拒读，只读保留。

【R2 重新冻结（2026-09-28）】语义送达与两层 IR：Design wire 升
`stage4-design-v3`（拍上线程 threads、书级 world_rules、章级 entry；四个静态
跨章列表删除，AUD-04），Stage4 设计产物（body_design／presentation_graph）
形状升 `str06-rc10`；WriterBookIR 升 `str06-rc10`（拍级读取值挂拍、线程投影
面、continuity 分区、world_rules 进书级层）；章回执身份拆分（回执升
`str06-rc6`）。节点集合与运行期配置不变。R1 基线（14 节点，`0d546fda…`）
原样保存在 Git 历史里；既有候选根按「契约表已漂移」拒读，只读保留。

【R2 引擎批重新冻结（2026-09-28 同日）】WriterPacket 指标落盘：
`stage4/acceptance/packet_metrics.json`（audit 报面，`str06-rc10`）进
`str06.stage4.compile` 的 produces 与产物表（E-6 同批闭合——完成度判定与
来源账覆盖该文件）。节点集合与运行期配置不变。R2 契约批基线
（14 节点，`b8501a9d…`）原样保存在 Git 历史里；既有候选根按「契约表已
漂移」拒读，只读保留（新协议无已购产物，重算免费）。

【R4 重新冻结（2026-09-28）】`str06.stage5.body` 的生命周期登记如实化：
`chapter_prefix_replay`（D2 之前的串行前文链语义）改名
`committed_set_recovery`（有效提交集合恢复：只补缺章，崩溃窗口按请求身份
免费重放），input_workpack／review_scope 同步章包输入与单章失败隔离语义。
恢复入口名进契约身份（`_node_identity` 的 recovery_entries），故重新冻结。
节点集合、产物表与运行期配置不变。R2 引擎批基线（14 节点，
`a14a9ef0…`）原样保存在 Git 历史里；既有候选根按「契约表已漂移」拒读，
只读保留（新协议无已购产物，重算免费）。

【语义冻结闸（2026-09-29，裁决 D8）】用户在 R5b 合入后裁决 Stage2/4 语义层按
main tip 现状冻结（`world_constraints` 不拆）：Stage2 观察协议 book／chapters／names
共 `stage2-observation-v1`、associate 独立 `stage2-associate-v2`，Stage4 四 wire
协议 `stage4-preserve-v1`／`stage4-design-v3`／`stage4-bind-v1`／
`stage4-hook-design-v1`，连同上方全部契约表基线。按首版 PLAN.md（Git 2fc03832）分层冻结，写作层
（renderer v5、WriterPacket 呈现、写手协议 v5）不在本闸内——canary 收敛后再冻。
B6-A／B6-B 期间任何语义层升版都意味着已购 Stage2/4 产物作废、须重购，属用户级
议程：先取得用户裁决，再改常量并在 REQUIREMENTS 登记；本文件与各协议测试同向
拦截（字面量漂移当场红）。

【为什么不只靠激活表自己的对账】`test_str06_candidates.py` 查的是表内自洽
（runner／writer／版本逐字相等）；本文件查的是**表与冻结基线**相等：
前者防表内部撒谎，后者防表在批次之间无登记漂移。
"""
from __future__ import annotations

from contracts.artifacts import ARTIFACT_PATHS, STR06_ARTIFACT_RC_VERSIONS
from contracts.invariants import NODES
from contracts.schema.observation import (
    STAGE2_ASSOCIATE_PROTOCOL, STAGE2_BOOK_PROTOCOL, STAGE2_CHAPTERS_PROTOCOL,
    STAGE2_NAMES_PROTOCOL,
)
from contracts.schema.workflow import (
    STAGE4_DESIGN_PROTOCOL, STAGE4_HOOK_DESIGN_PROTOCOL,
    STAGE4_PRESERVE_PROTOCOL, STR06_RC_VERSION, STR06_RUNTIME_CONFIG, STAGE4_PRESERVE_POLICY_VERSION,
)
from novel.llm.paid_requests import candidate_contract_identity

#: R10a a1 重新冻结（2026-10-01，D8 修订三）：Blueprint V14→V15（basis 引文＋
#: Effect.modality＋compiler 派生证据窗口），观察协议 book v3／chapters 独立 v2；
#: 节点、恢复策略与运行期配置不变，旧候选根只读拒识（协议不兼容，不计语义检出）。
#: R10a 收口重新冻结（2026-10-01，rc12 全表）：a2–a5 落地后全链形状定格——
#: preserve v2／policy 5（anchors 锚定、series、evidence_conflicts）、design v5
#: （kp/kf 锁定项、modality 必填、expression_refs、删 dramatic_intent）、Bind
#: series 同拍判据与 epistemic_lock_order 位置 Guard；产物表**全部 25 条统一
#: str06-rc12**（源理解变化波及全书链；names／bind／hook_design 协议字面量
#: 不变、经 RC 表换身份）。旧候选根按版本关闭式拒识，只读保留；写作层
#: renderer／写手协议 v6 不在本批（R10b 再升 v7）。
#: CS-3（2026-10-03，bind LLM 退役）重钉：str06.stage4.bind 退出节点表，
#: semantic_binding 的 writer 改 body_design（双产物）；冻结 13 节点／10 付费。
#: CS-3 余项（同日，D5 统一＋world 规则 id）再重钉：TargetDelta 删 reader 域
#: 与 confirms（读者揭示统一 knowledge(holder=reader)）、Brief book_constraints
#: 升 BookConstraint（wc 稳定规则 id）、design v6 wire 删 conf 例外面；产物表
#: 五条升 str06-rc13（brief／body_design／graph／execution_plan／writer_book_ir），
#: 其余保持 rc12（形状未变）。候选契约身份：节点表＋产物表＋RC 版式＋运行期
#: 配置的 SHA-256。表内任何一行变化都会改变它——先改本记录并登记原因
#: （REQUIREMENTS／DECISIONS），再动表。
# 审阅修复（2026-10-03）：Design v7 know/confirms 与逐次 Brief，五产物 rc14。
# 节点数、付费 owner 与预算不变；批准方案及迁移删除条件登记在 REQUIREMENTS。
# CS-R（2026-10-04）：用户裁决名称异写留证，names/Characters信封v2；
# 身份补齐信封版本与operator_redo，Hook按正式归因进入有限owner修订。
# rc15（2026-10-04 证据寻址，RC-26/27，用户批准）：证据段＝自然段，段号含义
# 改变；三类观察、Characters、Blueprint 升 rc15，RC 版式头升 rc15。
# rc16（2026-10-04 CS-F2，事实锁引用＋称谓投影）：semantic_binding 升 rc16（新增
# fact_bindings）、writer_book_ir 升 rc16（ChapterIR 新增 withheld_bindings），RC 版式头
# 升 rc16；Design wire 升 stage4-design-v8、renderer 升 v10（请求面，不在身份输入内）。
# 旧 rc15 候选根按契约表漂移关闭式拒收，原根作冻结失败证据；原因见 REQUIREMENTS CS-F2。
# rc16 续（2026-10-05 CS-F2 收口，Hook 称谓投影）：hook.json 升 rc16——HookContext 增
# withheld_bindings（按 Hook 自选 surface_releases 算好的唯一绑定称谓表，渲染期投影）；
# WithheldNameBinding 定义随 SurfaceRelease 下沉 state.py（斩断 hook↔book_ir 环）。旧
# rc12 楔子上下文拒读，免费重编译；原因见 REQUIREMENTS CS-F2。
# rc17（2026-10-06 CS-H H3＋H4 集中升版，用户裁决随 H4 一次、不 push 不双升）：
# H3 称谓面（Brief requires_released、BriefFactLock.subjects、ChapterIR
# display_prefix/display_fallback/requires_released、BeatIR.release_introductions、
# HookContext.display_faces/avoid_lines、BODY_VALIDATOR v2）与 H4 associate 协议
# stage2-associate-v3（维度句柄，RC-65）；六份产物换身份（brief／semantic_binding／
# writer_book_ir／hook／章回执／associate 观察）。旧 rc16 根（含 csf4_restore_20261005）
# 按契约表漂移只读拒收；原因见 REQUIREMENTS CS-H。
# rc18（2026-10-07 CS-S 1b，用户批准的观察面收敛批）：Stage2 死字段收口——
# 观察 canonical book／chapters 升 v5（InitialFact.knowledge 与 effect 的
# disclosure／proposition／scope 删）、Blueprints v16／Characters v5；七份产物换
# 身份（book／ch{nn}／blueprints／characters／fold／brief／semantic_binding——
# ObligationAnchor.visibility 删）；RC 版式头升 rc18。旧 rc17 根只读拒收；
# 原因见 REQUIREMENTS CS-S。
# rc18 续（2026-10-07 CS-S 1c）：names 观察 canonical 升 v4（构词派生＋变体窄调用、
# resolutions 增 not_name）——names.json 行随批换 rc18 身份。
# rc19（2026-10-07 CS-S Phase 2）：观察面收敛——book v6 增 facts、chapters v6 增
# know.fact、associate v4 条目级窄 resolver；观察与 fold 换 rc19 身份。
# rc20（2026-10-08 CS-S Phase 3，用户批准的收官批）：change 键入 CHANGE_KEYS 闭集
# （27 个通用维度，rc15 冻结根三本书 184 个真实键聚类定稿）；chapters wire v10
# （ev→event、state→change＋拍级 access）与 canonical v7、book canonical v7、
# associate v5（same_state_dimension 消失）、Blueprints v17（Beat.access＋派生
# reader cognition）、Characters v6（初态键闭集）；RC 版式头升 rc20。读者知情由
# compile 按拍级 access 派生（wire 禁 reader）。旧 rc19 根按契约表漂移只读拒收；
# 原因见 REQUIREMENTS CS-S。
# rc20 续（2026-10-08 CS-S 审阅补升）：writer_book_ir.json 行 rc17→rc18——1b 删
# ObligationAnchor.visibility 时漏升（IR 经 VerificationIR.obligations 内嵌 anchors，
# I-13）；RC 版式头仍为 rc20（同一未发布批次内补齐），冻结身份随该行重钉。
# rc21（2026-10-09，用户批准）：Preserve v3/policy 6、Design wire v9，
# 六类质量产物显式升版；原文同场/顺序仍不作为目标设计硬约束。
# rc22（2026-10-09，用户确认独立事件锚点）：Preserve v5/policy 8，六类质量
# 产物再次显式升版；旧 v4/rc21 拒读，免费 owner 修订须完整复验与双身份绑定。
# rc23（2026-10-10，用户批准 Hook 质量设计）：Hook wire v3、graph rc23、
# HookContext rc18；材料强调、跳时与阅读期待显式化，删除楔子长度配置。
# 正文 Design/IR 的 rc22 及篇幅配置不变；不补造旧楔子的组织或后文依据。
# rc24（2026-10-10 已有 Hook 篇幅包络批）：HookContext 单独升 rc24，
# 正文 Design/IR 不变；补齐现役表的冻结记录，不改节点、配置或正文契约。
# rc25（2026-10-17 用户授权）：身份文本、补充称谓、片段来源与按身份改名。
# rc26 批次 A（2026-10-18 命名前移批，用户批准的执行文档
# docs/specs/target_naming_mainline.md）：str06.stage7.naming 前移为
# str06.stage4.naming（置于 Stage2 compile 之后、Preserve 之前），NameMap v5→v6
# （contract_version str06-rc12→str06-rc26），产物路径 stage7/naming/→
# stage4/naming/name_map.json，旧路径进 RETIRED_ARTIFACT_PATHS 拒读；模块随节点
# 迁入 novel/stages/stage4/naming.py。节点总数仍 13（Stage7 腿只剩 assemble）。
# RC 版式头与全链统一 rc26 随批次 D 的 rc25→rc26 同根升级边落地（避免出现
# 无升级路径的拒读窗口）；旧候选根按契约表漂移只读拒收，原因见 REQUIREMENTS
# 「目标命名前置与普通正文主链」节。
FROZEN_CONTRACT_IDENTITY = 'e495aab494381c36c43dc0cbb47e1a5901c715825cf339fe2c5448fad7edba51'

#: 冻结时的节点集合（命名前移批后 13 个：stage0/1 两座＋Stage2 五节点＋
#: Stage4 五（含 naming）＋Stage5 两＋Stage7 一；bind 的承载位决定并入
#: body_design，旧 str06.stage7.naming 退役）。
FROZEN_NODES = (
    'str06.stage2.associate', 'str06.stage2.book', 'str06.stage2.chapters',
    'str06.stage2.compile', 'str06.stage2.names',
    'str06.stage4.body_design', 'str06.stage4.compile',
    'str06.stage4.hook_design', 'str06.stage4.naming', 'str06.stage4.preserve',
    'str06.stage5.body', 'str06.stage5.hook',
    'str06.stage7.assemble',
)

#: 冻结时的运行期配置（进契约身份；与付费调用额度是两件事）。
FROZEN_RUNTIME_CONFIG = {
    'length_band_low': 0.6, 'length_band_high': 1.3,
    'length_budget_rounding': 1000,
}


def _candidate_nodes() -> dict:
    return {node: spec for node, spec in NODES.items() if node.startswith('str06.')}


def test_frozen_contract_identity_matches_the_live_tables():
    assert candidate_contract_identity() == FROZEN_CONTRACT_IDENTITY, (
        '候选契约身份与 R9 冻结基线不符：先更新本文件的冻结记录，'
        '并在 REQUIREMENTS 登记变化原因（不静默改冻结面）')


def test_frozen_node_set_matches_the_live_table():
    nodes = _candidate_nodes()
    assert tuple(sorted(nodes)) == FROZEN_NODES
    assert len(nodes) == 13
    paid = sorted(n for n, spec in nodes.items() if spec.paid)
    free = sorted(n for n, spec in nodes.items() if not spec.paid)
    assert len(paid) == 10
    assert free == ['str06.stage2.compile', 'str06.stage4.compile',
                    'str06.stage7.assemble'], (
        '付费／免费节点面变化：compile 与 assemble 必须保持 executor_content_limit=0 '
        '结构性禁付')


def test_frozen_runtime_config_matches():
    assert dict(STR06_RUNTIME_CONFIG._asdict()) == FROZEN_RUNTIME_CONFIG


def test_frozen_artifact_table_shape():
    """25 份书产物；每条的 writer 是表内节点、schema_version 非空。"""
    assert len(STR06_ARTIFACT_RC_VERSIONS) == 25
    for rel, version in STR06_ARTIFACT_RC_VERSIONS.items():
        assert rel in ARTIFACT_PATHS, f'{rel}: 未激活进 ARTIFACT_PATHS'
        writer = next((n for n, spec in _candidate_nodes().items()
                       if rel in spec.produces), None)
        assert writer is not None, f'{rel}: writer 不是候选节点'
        assert version.startswith('str06-rc'), f'{rel}: 版本标签形状'
    # 模式产物（{nn}／{title}）恰好四份：冻结面不含第二处逐章／逐书名路径。
    patterned = sorted(rel for rel in STR06_ARTIFACT_RC_VERSIONS
                       if '{nn}' in rel or '{title}' in rel)
    assert patterned == ['stage2/observations/ch{nn}.json',
                         'stage5/acceptance/body/ch{nn}.json',
                         'stage5/body/ch{nn}.txt',
                         'stage7/final/{title}_rewriting.txt'], patterned


def test_frozen_rc_version_label():
    # rc13（CS-3 余项：D5 统一＋world 规则 id）——五产物形状变化升版，其余
    # 产物表条目保持 rc12（形状未变）。rc15：证据寻址（RC-26/27）；rc16：事实锁引用（CS-F2）；
    # rc17：CS-H H3 称谓面＋H4 associate v3；rc18：CS-S Phase 1 死字段收口；
    # rc19：CS-S Phase 2 观察面收敛（facts／know.fact／associate v4／D-5／fold 时间线）；
    # rc20：CS-S Phase 3 收官（CHANGE_KEYS 闭集／access／reader 派生／associate v5）。
    assert STR06_RC_VERSION == 'str06-rc27'


#: 语义冻结闸（2026-09-29，裁决 D8）钉住的语义层协议字面量。键是常量名、值是
#: 冻结时的字面量。契约表基线（节点／产物／RC／配置）已由上方身份测试钉住；
#: 协议字面量是 `candidate_contract_identity` 的输入之外的第二条线，单独钉，
#: 否则一次「顺手升版」不会改变身份哈希、静默混用不同协议的已购产物。
#: 写作层版本（`RENDERER_VERSION`／`WRITER_PROTOCOL_VERSION`）不在此表——
#: 分层冻结，canary 收敛后再冻（首版 PLAN.md，Git 2fc03832）。
#: R10a a1（2026-10-01，D8 修订三裁决）：book 升 v3（初态/约束带 basis 引文）、
#: chapters 拆独立字面 v2（转移带 basis／Effect.modality）；names 输入投影未变
#: 保持 observation-v1。
#: R10a a2（2026-10-01）：preserve 升 v2＋policy 5（义务 anchors 锚定投影句柄、
#: lineage 退出 wire、顶层 series／evidence_conflicts；输入投影改
#: project_preserve_view 确定性渲染）。
#: 旧盘面按版本关闭式拒识（协议不兼容），不计语义检出。
FROZEN_SEMANTIC_PROTOCOLS = {
    # rc18（CS-S 1b）：观察 canonical 升 v5——死字段收口；rc19（CS-S Phase 2）：
    # book v6 增 facts（词表＋known_at_start）、chapters v6 增 know.fact 引用；
    # rc20（CS-S Phase 3）：book/chapters 升 v7——初态键入 CHANGE_KEYS 闭集、
    # ObsBeat 增 access（获知通道）、know.who 禁 reader。
    'STAGE2_BOOK_PROTOCOL': 'stage2-book-v8',
    'STAGE2_CHAPTERS_PROTOCOL': 'stage2-chapters-v8',
    'STAGE2_NAMES_PROTOCOL': 'stage2-names-v5',
    # rc17（CS-H H4，RC-65）：associate 升 v3——维度句柄；rc19（CS-S Phase 2）
    # 升 v4——条目级窄 resolver；rc20（CS-S Phase 3）升 v5——same_state_dimension
    # 消失（change 键闭集＝键即 canonical）。
    'STAGE2_ASSOCIATE_PROTOCOL': 'stage2-associate-v5',
    'STAGE4_PRESERVE_PROTOCOL': 'stage4-preserve-v8',
    # 2026-10-10 用户授权有限补桥：必要源因果完整交接，形状与 wire 不变。
    # rc26 批次 B（2026-10-18 目标材料）：policy 12——Preserve 消费 NameMapV6，
    # 义务文本用目标姓名转述（源旧字面／身份文本标记按条目硬闸）。
    'STAGE4_PRESERVE_POLICY_VERSION': 13,
    'STAGE4_DESIGN_PROTOCOL': 'stage4-design-v6',
    'STAGE4_HOOK_DESIGN_PROTOCOL': 'stage4-hook-design-v5',
    # CS-3（2026-10-03）：bind 协议随节点退役——wire 面由 design v6 的 real
    # 承载标注取代，stage4-bind-v1 不再是现役协议字面量（常量删除）。
}


def test_frozen_semantic_protocols_match_the_live_literals():
    live = {
        'STAGE2_BOOK_PROTOCOL': STAGE2_BOOK_PROTOCOL,
        'STAGE2_CHAPTERS_PROTOCOL': STAGE2_CHAPTERS_PROTOCOL,
        'STAGE2_NAMES_PROTOCOL': STAGE2_NAMES_PROTOCOL,
        'STAGE2_ASSOCIATE_PROTOCOL': STAGE2_ASSOCIATE_PROTOCOL,
        'STAGE4_PRESERVE_PROTOCOL': STAGE4_PRESERVE_PROTOCOL,
        'STAGE4_PRESERVE_POLICY_VERSION': STAGE4_PRESERVE_POLICY_VERSION,
        'STAGE4_DESIGN_PROTOCOL': STAGE4_DESIGN_PROTOCOL,
        'STAGE4_HOOK_DESIGN_PROTOCOL': STAGE4_HOOK_DESIGN_PROTOCOL,
    }
    assert live == FROZEN_SEMANTIC_PROTOCOLS, (
        '语义层协议字面量与 2026-09-29 语义冻结闸（裁决 D8）不符：先取得用户裁决并在 REQUIREMENTS 登记；'
        '升版改变产物准入，已购回答须完整免费修订复验或另行授权新购，'
        '再更新本冻结记录')
