#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""待人工八类聚合生产者（`novel/orchestrator/inbox_projection.py`）——BE-04 第一条的落地判据。

两条裁决在这里各有一条锚：
  · **UG-11**（计划轻重告警没有处置回环）→ 处置=归人判断：那两条 INFO 的
    remediation 恒为 `human`，而任何 `human` 命中都会被生产者收进清单；
  · **UG-12**（定向重生成被驳回后没有恢复路径）→ `rejected` 类入列，
    `how` 里带 `_rounds/` 归档指针与当前 producer 真有的出路。

⚠ F8c：旧链待办面（stage6 候选定稿、stage7 人名表定稿闸、stage2 称名投影
免费物化、stage5 章级 INVALID/rejected revisions、trim 审计待核验、stage5
Review 收敛账）随旧链整层退役——它们的节点与旗标不在现役 `NODES`。本文件的
现役断言一律走**合成输入**（受控报告 / 最小只读盘面），不再依赖已退役的
stage6 语料；live `.INVALID` 的阻断由 `_invalid_items` 通用投影与 queue 的
收件箱分类负责。
"""
from __future__ import annotations

from types import MappingProxyType, SimpleNamespace

from contracts.invariants import INBOX_KIND
from contracts.schema.orchestration import NovelOverview, OverviewSummary
from novel.orchestrator.inspection import (
    OperatorReadSnapshot, read_surface_seal,
)


def test_human_only_collects_hits_whose_remediation_is_human():
    """用受控报告钉语义；不能把某本现役语料碰巧有两条命中当成契约。"""
    from novel.orchestrator import inbox_projection as I

    finding = SimpleNamespace(
        output_ordinal=4, wontfix=None, unresolved_rounds=0,
        owner='stage1', detail='切分告警需人工判断')
    line = SimpleNamespace(
        status='hit', sev='info', remediation='human', fix=None,
        rule='s1_warnings', findings=[finding])
    human = I._report_items(SimpleNamespace(rules=[line]), 'stage1')
    assert len(human) == 1
    assert human[0].kind == 'human_only'
    assert human[0].chapter == 4
    assert human[0].stage == 'stage1' and human[0].rule == 's1_warnings'
    # 新链全自动单模式：没有任何节点发布 0 成本人工写入口（flags 只有
    # --check-only）⇒ 兜底文案必须如实说「没有」，不许指一条敲不出来的命令。
    assert '没有 0 成本人工入口' in human[0].how
    assert '--apply-edit' not in human[0].how and '--pick' not in human[0].how


def test_retired_human_gate_inbox_producers_stay_deleted():
    """F8c：人工闸整层退役——审批类待办生产者随旧链删除，不得复活。

    新链没有审批面（`HUMAN_NODES` 为空、无 gate/closure/workpack 节点）：
    stage6 候选核验/定稿、stage7 人名表定稿闸、stage2 称名投影免费物化、
    stage5 章级 INVALID/rejected revisions、trim 审计待核验、stage5 Review
    收敛账这些生产者都只能由旧链节点写出，而生产入口已拒识旧节点
    （`RETIRED_NODES` ⇒ legacy_incompatible）⇒ 待办清单只剩盘上 `.INVALID`
    （`_invalid_items`）与 0 成本复检报告（`_report_items`）两类现役数据源。
    任何审批待办生产者回来都意味着第二套人工闸（I-16：每类信息一个权威源）。
    """
    from novel.orchestrator import inbox_projection as I
    for retired in (
        '_approval_items', '_stage2_projection_recovery_items',
        '_stage2_legacy_blueprint_recovery_items',
        '_stage5_stalled', '_stage5_chapter_invalids',
        '_stage5_rejected_revisions', '_trim_verification_items',
        '_report_items_for_inbox',
    ):
        assert not hasattr(I, retired), (
            f'{retired} 是旧链待办生产者，不应随 F8c 复活')


def test_unver_and_skipped_rows_never_enter_the_inbox():
    """「查不了」是判据的边界不是待办——堆进清单就永远清不空。

    F8c：改走受控报告（原用例依赖已退役的 stage6 语料与旧判据表）：只有
    `status=hit` 的结论能进清单，`unver`／`skipped` 一律不入列。
    """
    from novel.orchestrator import inbox_projection as I

    def line(status, rule, detail):
        return SimpleNamespace(
            status=status, sev='unver' if status == 'unver' else 'info',
            remediation='none' if status != 'hit' else 'human', fix=None,
            rule=rule,
            findings=[SimpleNamespace(
                output_ordinal=2, wontfix=None, unresolved_rounds=0,
                owner='stage1', detail=detail)])

    rows = I._report_items(SimpleNamespace(rules=[
        line('unver', 'body_semantics_unver', '语义判不了'),
        line('skipped', 's1_warnings', '这一轮没查'),
        line('hit', 's1_warnings', '切分告警需人工判断'),
    ]), 'stage1')
    assert [row.rule for row in rows] == ['s1_warnings']
    assert not [row for row in rows if 'unver' in row.rule]


def test_counts_total_and_kind_partition_are_mutually_consistent(tmp_path):
    """八键全出（0 也出）、counts == items 分组 == total——载体后置条件。

    F8c：改走合成快照（原用例依赖已退役的 stage6 语料）：空书目录＋未采集
    复检的快照也必须给出完整的八键计数与自洽总分——干净书是 0，不是缺键。
    """
    from novel.orchestrator import inbox_projection as I

    book = tmp_path / 'book-1'
    book.mkdir()
    code = 'code-v1'
    overview = NovelOverview(
        sid='book-1', title='测试书', dirname=book.name, stages=[],
        summary=OverviewSummary(
            verdict='pending', text='待继续', detail='下一步待判',
            artifact_count=0, latest_mtime=None))
    snapshot = OperatorReadSnapshot(
        base=tmp_path.resolve(), sid='book-1', novel_dir=book.resolve(),
        code=code, surface_seal=read_surface_seal(tmp_path, 'book-1', code=code),
        overview=overview, checks=MappingProxyType({}))

    view = I.inbox_from_snapshot(snapshot)

    assert set(view.counts) == set(INBOX_KIND)
    assert view.total == len(view.items) == sum(view.counts.values()) == 0


def test_missing_blueprint_projection_inbox_points_only_to_free_materialization():
    """待办文案不能把缺伴生产物说成要重新付费抽 Global。

    F8c：称名投影恢复生产者随 `stage2.characters` 旧链节点退役（artifact 键
    移入 `RETIRED_ARTIFACT_PATHS`）——同名待办行不再存在，也不得指一条
    已退役节点的 `--revalidate`。存活下来的是同一条纪律的通用形状：live
    `.INVALID` 的恢复文案只指当前 producer 真有的入口，没有就明说没有。
    """
    from novel.orchestrator import inbox_projection as I
    assert not hasattr(I, '_stage2_projection_recovery_items')

    from contracts.artifacts import STR06_STAGE2_BLUEPRINTS, RETIRED_ARTIFACT_PATHS
    from novel import paths

    invalid = paths.invalid_of(STR06_STAGE2_BLUEPRINTS)
    store = _InvalidStore(present={STR06_STAGE2_BLUEPRINTS, invalid}, versions={})
    rows = I._invalid_items(store, 'stage2')
    assert len(rows) == 1 and rows[0].kind == 'rejected'
    # 恢复文案不许指旧链节点的提升路，也不许暗示一次新的付费抽取。
    assert '没有可执行的 INVALID 提升入口' in rows[0].how
    assert '--revalidate' not in rows[0].how
    assert 'stage2.characters' not in rows[0].how
    assert RETIRED_ARTIFACT_PATHS['stage2.blueprint_character_projection']


def test_old_blueprint_has_no_separate_migration_inbox():
    """待办层不能恢复已退役的免费迁移入口。"""
    from novel.orchestrator import inbox_projection as I
    assert not hasattr(I, '_stage2_legacy_blueprint_recovery_items')


def test_unknown_input_invalid_cannot_suppress_normal_validation_inbox():
    """来源未知仍展示人工复验；是否可提升由 producer 判定。"""
    from contracts.artifacts import STR06_STAGE2_BLUEPRINTS
    from novel import paths
    from novel.orchestrator import inbox_projection as I

    invalid_rel = paths.invalid_of(STR06_STAGE2_BLUEPRINTS)

    class Store:
        @staticmethod
        def exists(rel):
            return rel == invalid_rel

    generic = I._invalid_items(Store(), 'stage2')
    assert len(generic) == 1
    # 新链无 --revalidate 提升口（F8a 翻转）：how 明说没有入口，不拼假命令，
    # 也不恢复 Stage3 时代的 --renormalize 猜测。
    assert '没有可执行的 INVALID 提升入口' in generic[0].how
    assert '--renormalize' not in generic[0].how


def test_stage5_book_finding_is_one_task_with_chapter_details_preserved():
    """inbox 直接消费 CheckReport 的书级 finding，不再做第二次聚合。"""
    from novel.orchestrator import inbox_projection as I

    aggregate = SimpleNamespace(
        output_ordinal=None, wontfix=None, unresolved_rounds=0,
        owner='stage5',
        detail=('书级篇幅偏差：2/25 章命中，涉及章节 [2, 7]，'
                '逐章事实可在 evidence 展开'))
    line = SimpleNamespace(
        status='hit', sev='info', remediation='human', fix=None,
        rule='body_length_reading', scanned='3/25 章（按命中章去重）',
        findings=[aggregate],
    )
    rows = I._report_items(SimpleNamespace(rules=[line]), 'stage5')
    assert len(rows) == 1
    assert rows[0].kind == 'human_only' and rows[0].chapter is None
    assert rows[0].rule == 'body_length_reading'
    assert '[2, 7]' in rows[0].what and '2/25 章' in rows[0].what
    assert 'evidence 展开' in rows[0].what
    # 兜底 how 从现役 NODES 派生（F8b 后新链 stage5 无 --apply-edit／--wontfix
    # 人工路）：明说没有 0 成本人工入口，不拼旧链承诺。
    assert '没有 0 成本人工入口' in rows[0].how


def test_stage5_length_over_uses_the_finding_chapter_without_display_side_guessing():
    """inbox 只投影 Review finding 自带的章号；不在展示层做 trim join。"""
    from novel.orchestrator import inbox_projection as I

    finding = SimpleNamespace(
        output_ordinal=3, wontfix=None, unresolved_rounds=0,
        owner='stage5', detail='第 3 章超出目标区间')
    line = SimpleNamespace(
        status='hit', sev='info', remediation='human', fix=None,
        rule='body_length_reading', scanned='1/1 章', findings=[finding])
    rows = I._report_items(SimpleNamespace(rules=[line]), 'stage5')
    assert len(rows) == 1 and rows[0].chapter == 3
    assert '没有 0 成本人工入口' in rows[0].how


def test_the_fallback_how_only_names_commands_that_can_actually_be_built():
    """⭐ **兜底文案不许承诺一条敲不出来的命令**，且每条路都要点名它属于哪个节点。

    ⚠⚠ 这里原来是一个常量，逐字写着「`--apply-edit` 改内容 / `--wontfix` 记「不修」的裁决」，
    而 `--wontfix` **全仓只有 `stage5.review` 有**：stage6 的
    `hook_candidate_contradiction`（`sev=info` · `fix=None` ⇒ `remediation='human'`）
    一命中，清单就建议人去跑一个这一层根本没有的 flag ⇒ `build_argv` 当场拒收。
    与 `contracts/rules.py::_validate` ⑧ 拦的是同一种病，区别只在于
    **`Rule.fix` 那一侧有三道校验，兜底文案这一侧一道都没有**——本条就是那道。

    ⚠⚠ **而这条守卫自己曾经是假绿的。** 它原来重拼了 `_human_how` 内部那一行聚合
    （`{flag for spec in NODES.values() if spec.stage == stage ...}`）再断言
    「文案点名的 ⊆ 自己算的」——等于拿生产的答案去判生产的卷子，
    生产的粒度错了它必然跟着错。实际发生的就是这件事：按 stage 聚合把
    `stage2.global` 的 `--apply-edit` 借给了 `stage2.blueprint`，
    于是 `present_continuity` 的处置行指了一个 blueprint 根本没有的入口，
    而本条一声没吭——直到 2026-08-21 跑《四人成行》时由人撞出来。
    ⇒ 改成**调用生产落点**：文案里点名的每条 `node --flag` 交给 `build_argv`
    真拼一遍，拼不出来就红。这正是 `docs/LESSONS.md` 第 21 条那句
    「守卫要调用生产那一个落点，不要重拼一份」。

    ⚠ 带值 flag 的占位值是**调用协议**，不是被测逻辑：`build_argv`
    要形状完整才肯往下走，而本条断言的是「这个节点到底有没有这个 flag」。
    """
    import re as _re

    from contracts.invariants import NODES, STAGES, VALUED_FLAGS
    from novel.orchestrator.nodes import build_argv, placeholder_for
    from novel.orchestrator.inbox_projection import _human_how

    checked = 0
    for stage in STAGES:
        text = _human_how(stage)
        named = _re.findall(r'`([a-z0-9]+\.[a-z-]+) (--[a-z-]+)`', text)
        for node, flag in named:
            flags = [flag, placeholder_for(flag)] if flag in VALUED_FLAGS else [flag]
            build_argv(node, 'SID', flags, base='.', placeholders=True)
            checked += 1
        assert not _re.findall(r'`(--[a-z-]+)`', text), (
            f'{stage} 的兜底文案出现了不带节点前缀的裸 flag：{text!r}；'
            '每条路都必须点名它属于哪个节点——`stage2` 两个节点的入口并不相同，'
            '裸 flag 会把 global 的入口读成 blueprint 也有')
        if not named:
            assert '--' not in text, f'{stage} 一条 0 成本人工路都没有，却给出了一条命令：{text}'
            assert '没有 0 成本人工入口' in text, (
                f'{stage} 没有 0 成本人工入口时必须如实说明，不编下一步：{text!r}')
    # ⚠ 新链全自动单模式：现役节点的 flags 只有 --check-only，没有任何一层发布
    # 0 成本人工写入口 ⇒ 全 STAGES 都走「如实说没有」分支。哪天新链真发布了
    # 这样的人入口，`checked` 会大于 0，上面那条 build_argv 循环当场接管校验。
    assert checked == 0, (
        f'新链出现了 0 成本人工入口（{checked} 条）：上面已逐条验过可拼，'
        '请同步确认它们确实该对人工发布')


def test_verification_rows_are_disclosure_not_blocking():
    """批次 C：verification 全转披露，不挡交付终验。"""
    from contracts.schema.api import InboxItem
    from novel.orchestrator import inbox_projection as I

    item = InboxItem(
        kind='verification', stage='stage5', chapter=1,
        rule='trim_audit_ch01', what='超限披露', how='机械处置')
    classified = I._classify(item)
    assert classified.delivery_effect == 'disclosure'
    blocking = I._classify(InboxItem(
        kind='invalid', stage='stage6', chapter=None,
        rule='stage6.hook', what='INVALID', how='auto revalidate'))
    assert blocking.delivery_effect == 'blocking'


def test_no_current_node_carries_revalidate_and_instructions_stay_system_only():
    """F8a 翻转后现役 NODES 无 `--revalidate` 提升口；指令格式守卫保留。

    新链的 INVALID 由 owner 重跑／`--check-only` 复检处置。若未来重新引入
    `--revalidate`，`_revalidate_how` 必须仍指系统自动路径——那条格式断言
    在这里继续守着，不为空集放水。
    """
    from contracts.invariants import (
        NODES, SYSTEM_INVALID_REVALIDATE_ACTOR, SYSTEM_INVALID_REVALIDATE_REASON,
    )
    from novel.orchestrator.inbox_projection import _revalidate_how

    nodes = [node for node, spec in NODES.items() if '--revalidate' in spec.flags]
    assert not nodes, f'现役链不应有 --revalidate 节点，实得 {nodes}'
    # 格式守卫：合成节点名走同一拼装路径，重新引入时指令仍是系统自动路。
    text = _revalidate_how('str06.stage5.body')
    assert ('str06.stage5.body --revalidate --actor '
            f'{SYSTEM_INVALID_REVALIDATE_ACTOR}') in text
    assert SYSTEM_INVALID_REVALIDATE_REASON in text
    assert '0 LLM' in text


class _InvalidStore:
    """只给 `_invalid_items` 的最小只读盘面；不借真实语料替 shape-break 作证。"""

    def __init__(self, *, present: set[str], versions: dict[str, int],
                 refuse_version_read: bool = False) -> None:
        self.present = present
        self.versions = versions
        self.refuse_version_read = refuse_version_read
        self.seen_round_calls: list[str] = []

    def exists(self, rel: str) -> bool:
        return rel in self.present

    def schema_version_of(self, rel: str) -> int | None:
        if self.refuse_version_read:
            raise AssertionError('有 --revalidate 的通用节点不应读取 schema version')
        return self.versions.get(rel)

    def seen_rounds(self, rel: str) -> set[int]:
        self.seen_round_calls.append(rel)
        return {2}


def test_generic_invalid_checks_the_producer_revalidate_flag_before_schema_version():
    """通用路由从 NODES 派生；无提升口的节点不得拼出不存在的命令。"""
    from contracts.artifacts import STR06_STAGE2_BLUEPRINTS
    from novel import paths
    from novel.orchestrator import inbox_projection as I

    invalid = paths.invalid_of(STR06_STAGE2_BLUEPRINTS)
    store = _InvalidStore(
        present={STR06_STAGE2_BLUEPRINTS, invalid}, versions={},
        refuse_version_read=True)
    rows = I._invalid_items(store, 'stage2')
    assert len(rows) == 1 and rows[0].kind == 'rejected'
    assert rows[0].rule == 'str06.stage2.source'
    assert '_rounds/' in rows[0].how
    # 新链无 --revalidate 提升口：how 必须明说不存在入口，而不是拼假命令。
    assert '没有可执行的 INVALID 提升入口' in rows[0].how
    assert '--revalidate' not in rows[0].how


def test_every_human_only_row_offers_something_the_operator_can_actually_run():
    """⭐ 端到端：清单上每一条 `human_only` 的 `how` 都必须是**跑得动的**。

    `Rule.fix` 那条路由由注册期三道校验守着（节点存在 · flag 在该节点的 `flags` 里 ·
    不指向别的层），**兜底那条此前一道都没有**——而 stage6 的语义提示恰好走的是兜底。
    """
    import re as _re

    from contracts.invariants import NODES
    from novel.orchestrator import inbox_projection as I

    # 语料此刻没有 human_only 是合法盘面，不应把偶然有行当作契约。
    # 用可控报告直接验证生产路径。
    finding = SimpleNamespace(
        wontfix=None, unresolved_rounds=0, owner='stage6', detail='候选需人工判断')
    line = SimpleNamespace(
        status='hit', sev='info', remediation='human', fix=None,
        rule='hook_candidate_contradiction', findings=[finding])
    rows = I._report_items(SimpleNamespace(rules=[line]), 'stage6')
    assert len(rows) == 1 and rows[0].kind == 'human_only'
    for item in rows:
        for node, flag in _re.findall(r'`([a-z0-9]+\.[a-z-]+) (--[a-z-]+)`', item.how):
            spec = NODES.get(node)
            assert spec is not None, f'{item.rule} 指向不存在的节点 {node}'
            assert flag in spec.flags, f'{item.rule} 指的 {node} {flag} 不在该节点的 flags 里'
        bare = set(_re.findall(r'`(--[a-z-]+)`', item.how))
        have = {f for spec in NODES.values() if spec.stage == item.stage for f in spec.flags}
        assert bare <= have, (
            f'{item.stage}/{item.rule} 的 how 点名了这一层没有的 flag：{sorted(bare - have)}')


def test_a_hard_local_hit_with_a_fix_route_is_repairable_not_human_only():
    from novel.orchestrator import inbox_projection as I

    fix = SimpleNamespace(
        node='stage2.global', flags=['--apply-edit', 'stage2/global_edit.json'],
        paid=False, available=True, unavailable_reason=None)
    finding = SimpleNamespace(
        wontfix=None, unresolved_rounds=0, owner='stage2', detail='设定字段可确定性修复')
    line = SimpleNamespace(
        status='hit', sev='hard', remediation='deterministic', fix=fix,
        rule='global_field_repair', findings=[finding])
    rows = I._report_items(SimpleNamespace(rules=[line]), 'stage2')
    assert len(rows) == 1 and rows[0].kind == 'repairable'
    assert '`stage2.global --apply-edit stage2/global_edit.json`' in rows[0].how
