#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""待人工八类聚合生产者（`novel/views/inbox.py`）——BE-04 第一条的落地判据。

两条裁决在这里各有一条锚：
  · **UG-11**（计划轻重告警没有处置回环）→ 处置=归人判断：那两条 INFO 的
    remediation 恒为 `human`，而任何 `human` 命中都会被生产者收进清单；
  · **UG-12**（定向重生成被驳回后没有恢复路径）→ `rejected` 类入列，
    `how` 里带 `_rounds/` 归档指针与 `--revalidate` 出路。
"""
from __future__ import annotations

import json
import shutil
from types import SimpleNamespace

import pytest

from novel.views.inbox import inbox_view
from tests import corpus, sandbox

STAGE6_INPUTS = ('stage1/hook_copy.json', 'stage2/global_grounding.json',
                 'stage4/writer_context.json',
                 'stage6/hook.json')

#: 盘上有 `hook_ending_shape` 命中（×2）的那本——对账表 `test_stage6.EXPECTED_HITS` 同源。
BOOK_WITH_HITS = '我把夫君捧成头牌后，全家悔疯了_7615069877019300633'


def _books() -> set[str]:
    return {a.book_dir for a in corpus.iter_artifacts() if a.rel == 'stage6/hook.json'}

def _STAGE6_CORPUS(test):
    test = pytest.mark.corpus(test)
    return pytest.mark.skipif(
        not _books(), reason='盘上没有 stage6 语料——这几条**没在查**，不是「查过没事」')(test)


def _drafts_of(book: str) -> tuple[str, ...]:
    return tuple(sorted(
        a.rel for a in corpus.iter_artifacts()
        if a.book_dir == book and a.pattern == 'stage5/ch{nn}.json'
        and '_rounds/' not in a.rel))


@pytest.fixture
def box():
    """⚠ 首选 `BOOK_WITH_HITS`，其次**成稿链条相对上游是当前**的那一本。

    收缩审计那条用例要在 stage5 的待核验清单里看见自己种下的那一行，而清单是从
    验收报告来的：成稿一旦相对重生成过的 `stage4/writer_context.json` 过期，
    stage5 那一段整段产不出来 ⇒ 清单为空，用例红在「种下的那一行没进清单：[]」上，
    看起来像视图漏了行。实测：过期的两本 0 行，链条干净的那本 3 行且含种下的那一行。
    """
    books = _books()
    book = BOOK_WITH_HITS if BOOK_WITH_HITS in books else next(
        (candidate for candidate in sorted(books)
         if (rels := _drafts_of(candidate)) and corpus.is_current(candidate, *rels)),
        sorted(books)[0])
    s = sandbox.Sandbox(book, STAGE6_INPUTS + _drafts_of(book))
    yield s
    s.close()


def test_human_only_collects_hits_whose_remediation_is_human():
    """用受控报告钉语义；不能把某本现役语料碰巧有两条命中当成契约。"""
    from novel.views import inbox as I

    finding = SimpleNamespace(
        output_ordinal=4, wontfix=None, unresolved_rounds=0,
        owner='stage6', detail='结尾形态需人工裁决')
    line = SimpleNamespace(
        status='hit', sev='info', remediation='human', fix=None,
        rule='hook_ending_shape', findings=[finding])
    human = I._report_items(SimpleNamespace(rules=[line]), 'stage6')
    assert len(human) == 1
    assert human[0].kind == 'human_only'
    assert human[0].chapter == 4
    assert human[0].stage == 'stage6' and human[0].rule == 'hook_ending_shape'
    assert '--apply-edit' in human[0].how or '--pick' in human[0].how


def test_final_approval_inbox_commands_bind_the_same_fresh_workpack():
    """恢复命令必须从同一份 fresh 包取 subject/workpack 双哈希。"""
    from novel.views import inbox as I

    candidate = SimpleNamespace(
        index=2, slot='c2', approach='scene_punchline',
        subject_sha256='a' * 64,
        semantic_state='uncertain', semantic_reason='A/B 证据待人工核验',
    )
    stage6_pack = SimpleNamespace(
        disposition='review_required', workpack_sha256='b' * 64,
        candidates=[candidate],
    )
    stage7_pack = SimpleNamespace(
        disposition='review_required', workpack_sha256='c' * 64,
        decision_subject_sha256='d' * 64,
    )

    verification = I._hook_verification_items(
        '.', 'code', workpack=stage6_pack, workpack_checked=True)
    approvals = I._approval_items(
        '.', 'code', stage6_workpack=stage6_pack,
        stage7_workpack=stage7_pack, workpacks_checked=True)

    assert len(verification) == 1
    assert '--subject-sha256 ' + 'a' * 64 in verification[0].how
    assert '--workpack-sha256 ' + 'b' * 64 in verification[0].how
    by_rule = {row.rule: row for row in approvals}
    assert set(by_rule) == {'stage6.approve', 'stage7.approve'}
    assert '--workpack-sha256 ' + 'b' * 64 in by_rule['stage6.approve'].how
    assert '--subject-sha256 ' + 'd' * 64 in by_rule['stage7.approve'].how
    assert '--workpack-sha256 ' + 'c' * 64 in by_rule['stage7.approve'].how


def test_final_approval_inbox_omits_already_current_actions():
    """同一当前工作包已经盖章时不再发布重复 approve/verification 动作。"""
    from novel.views import inbox as I

    stage6_pack = SimpleNamespace(
        disposition='already_current', workpack_sha256='b' * 64,
        candidates=[],
    )
    stage7_pack = SimpleNamespace(
        disposition='already_current', workpack_sha256='c' * 64,
        decision_subject_sha256='d' * 64,
    )

    assert I._hook_verification_items(
        '.', 'code', workpack=stage6_pack, workpack_checked=True) == []
    assert I._approval_items(
        '.', 'code', stage6_workpack=stage6_pack,
        stage7_workpack=stage7_pack, workpacks_checked=True) == []


def test_conflict_stage_coverage_inbox_points_to_typed_edit_not_paid_resample():
    from contracts.rules import REGISTRY
    from novel.report.builder import remediation_of
    from novel.views import inbox as I

    rule = REGISTRY.get('conflict_stage_coverage')
    finding = SimpleNamespace(
        output_ordinal=None, wontfix=None, unresolved_rounds=0,
        owner='stage2', detail='冲突阶段缺章')
    line = SimpleNamespace(
        status='hit', sev=rule.sev, remediation=remediation_of(rule), fix=None,
        rule=rule.rule, findings=[finding])

    rows = I._report_items(SimpleNamespace(rules=[line]), 'stage2')

    assert rule.fix is None and remediation_of(rule) == 'human'
    assert len(rows) == 1 and rows[0].kind == 'human_only'
    assert '`conflict_stages_replace`' in rows[0].how
    assert ('`stage2.global --apply-edit stage2/global_edit.json`'
            in rows[0].how)
    assert '付费 `--resample` 只作最后手段' in rows[0].how


def test_missing_blueprint_projection_inbox_points_only_to_free_materialization():
    """待办文案不能把缺伴生产物说成要重新付费抽 Global。"""
    from novel.views import inbox as I

    grounding = SimpleNamespace(
        rel='stage2/global_grounding.json', exists=True, readable=True,
        freshness='fresh', invalid=False,
    )
    projection = SimpleNamespace(
        rel='stage2/blueprint_character_projection.json', exists=False,
        readable=True, freshness='unknown', invalid=False,
    )
    ov = SimpleNamespace(stages=[SimpleNamespace(
        stage='stage2', artifacts=[grounding, projection])])

    rows = I._stage2_projection_recovery_items(ov)

    assert len(rows) == 1 and rows[0].kind == 'repairable'
    assert rows[0].rule == 'blueprint_character_projection'
    assert 'stage2.global --renormalize' in rows[0].how
    assert '0 LLM' in rows[0].how and '不会重采付费 Global' in rows[0].how


def test_legacy_v6_blueprint_inbox_points_only_to_free_renormalize():
    """旧来源账有独立 0-LLM 待办，不应建议付费重采 Blueprint。"""
    from novel.views import inbox as I

    projection = SimpleNamespace(
        rel='stage2/blueprint_character_projection.json', exists=True,
        readable=True, freshness='fresh', invalid=False, reason='match',
    )
    blueprint = SimpleNamespace(
        rel='stage2/chapter_blueprints.json', exists=True,
        readable=True, freshness='unknown', invalid=False,
        reason='consumed_unresolved',
    )
    ov = SimpleNamespace(stages=[SimpleNamespace(
        stage='stage2', artifacts=[projection, blueprint])])

    rows = I._stage2_legacy_blueprint_recovery_items(ov)

    assert len(rows) == 1 and rows[0].kind == 'repairable'
    assert rows[0].rule == 'chapter_blueprints_legacy_inputs'
    assert 'stage2.blueprint --renormalize' in rows[0].how
    assert '0 LLM' in rows[0].how and '不会重采付费 Blueprint' in rows[0].how


def test_stage5_band_drift_is_one_book_task_with_chapter_details_preserved():
    """inbox 直接消费 CheckReport 的书级 finding，不再做第二次聚合。"""
    from novel.views import inbox as I

    aggregate = SimpleNamespace(
        output_ordinal=None, wontfix=None, unresolved_rounds=0,
        owner='stage5',
        detail=('书级档位漂移：2/25 章命中，涉及章节 [2, 7]，'
                '逐章事实可在 evidence 展开'))
    line = SimpleNamespace(
        status='hit', sev='info', remediation='human', fix=None,
        rule='band_drift', scanned='3/25 章（按命中章去重）',
        findings=[aggregate],
    )
    rows = I._report_items(SimpleNamespace(rules=[line]), 'stage5')
    assert len(rows) == 1
    assert rows[0].kind == 'human_only' and rows[0].chapter is None
    assert rows[0].rule == 'band_drift'
    assert '[2, 7]' in rows[0].what and '2/25 章' in rows[0].what
    assert 'evidence 展开' in rows[0].what
    assert '0 LLM 费用' in rows[0].how
    assert '所有 producer-owned slot' in rows[0].how and '整章全文' in rows[0].how


def test_stage5_length_over_uses_the_finding_chapter_without_display_side_guessing():
    """inbox 只投影 Review finding 自带的章号；不在展示层做 trim join。"""
    from novel.views import inbox as I

    finding = SimpleNamespace(
        output_ordinal=3, wontfix=None, unresolved_rounds=0,
        owner='stage5', detail='第 3 章超出目标区间')
    line = SimpleNamespace(
        status='hit', sev='info', remediation='human', fix=None,
        rule='length_over', scanned='1/1 章', findings=[finding])
    rows = I._report_items(SimpleNamespace(rules=[line]), 'stage5')
    assert len(rows) == 1 and rows[0].chapter == 3
    assert '0 LLM 费用' in rows[0].how
    assert '所有 producer-owned slot' in rows[0].how and '整章全文' in rows[0].how


@_STAGE6_CORPUS
def test_unver_rows_never_enter_the_inbox(box):
    """「查不了」是判据的边界不是待办——堆进清单就永远清不空。"""
    from contracts.rules import REGISTRY
    view = inbox_view(str(box.root), box.sid)
    unver_rules = {r.rule for r in REGISTRY.all() if r.sev == 'unver'}
    assert not [it for it in view.items if it.rule in unver_rules]


@_STAGE6_CORPUS
def test_counts_total_and_kind_partition_are_mutually_consistent(box):
    """八键全出（0 也出）、counts == items 分组 == total——载体后置条件与实产共同钉住。"""
    view = inbox_view(str(box.root), box.sid)
    assert set(view.counts) == {'invalid', 'rejected', 'stalled',
                                'repairable', 'human_only', 'verification',
                                'upstream', 'approval'}
    assert view.total == len(view.items) == sum(view.counts.values())


@_STAGE6_CORPUS
def test_an_invalid_without_formal_is_invalid_and_with_formal_is_rejected(box):
    """同一个 `.INVALID`，正式稿在不在决定它是「在等人改」还是「钱花了稿丢了」。"""
    formal = box.dir / 'stage6' / 'hook.json'
    shutil.copyfile(formal, box.dir / 'stage6' / 'hook.INVALID.json')

    view = inbox_view(str(box.root), box.sid)
    rejected = [it for it in view.items if it.kind == 'rejected' and it.stage == 'stage6']
    assert len(rejected) == 1
    assert rejected[0].rule == 'stage6.hook'
    # UG-12 的锚：被驳回之后的下一条恢复路径写在 how 里——归档指针 + 0 成本出路。
    assert '_rounds' in rejected[0].how and '--revalidate' in rejected[0].how
    assert '--actor <id>' in rejected[0].how and '--reason <why>' in rejected[0].how
    assert '0 LLM、同轮重判、写入不可变审计' in rejected[0].how

    formal.unlink()
    view2 = inbox_view(str(box.root), box.sid)
    invalid = [it for it in view2.items if it.kind == 'invalid' and it.stage == 'stage6']
    assert len(invalid) == 1
    assert '--revalidate' in invalid[0].how
    assert '--actor <id>' in invalid[0].how and '--reason <why>' in invalid[0].how
    assert '0 LLM、同轮重判、写入不可变审计' in invalid[0].how
    assert not [it for it in view2.items if it.kind == 'rejected' and it.stage == 'stage6']


@_STAGE6_CORPUS
def test_approval_lists_the_unapproved_hook_gate(box):
    """定稿闸门没盖章 ⇒ `approval` 入列；它不是故障，但确实该在清单上。"""
    rel = box.dir / 'stage6' / 'hook.json'
    data = json.loads(rel.read_text(encoding='utf-8'))
    baseline = inbox_view(str(box.root), box.sid)
    if data.get('approved') is None:
        assert [it for it in baseline.items if it.kind == 'approval']
        return
    data['approved'] = None
    rel.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    view = inbox_view(str(box.root), box.sid)
    gates = [it for it in view.items if it.kind == 'approval']
    assert [it.rule for it in gates] == ['stage6.approve']
    assert '--pick' in gates[0].how


def test_plan_info_warnings_route_to_human_and_thus_into_the_inbox():
    """Stage4 INFO 的处置是「归人判断」——remediation 恒 `human`，
    而 `_report_items` 把任何 `human` 命中收进清单（上一条已验）。两半合起来即回环。"""
    from contracts.rules import REGISTRY
    from novel.report.builder import remediation_of
    for rule_id in ('causal_disclosure', 'verbatim_starved'):
        rule = REGISTRY.get(rule_id)          # 改名会在这里当场说话
        assert rule.sev == 'info'
        assert remediation_of(rule) == 'human'


def test_stalled_items_only_take_hard_info_at_the_stop_threshold(monkeypatch):
    """轮次到顶只升级 HARD/INFO；常驻 METRIC/UNVER 不是修复失败。"""
    from novel.stages.stage5 import review
    from novel.views import inbox as I

    def finding(rule, rounds, wontfix=None):
        return SimpleNamespace(rule=rule, detail=f'{rule} 连续未消', evidence_key='e',
                               unresolved_rounds=rounds, wontfix=wontfix)

    rev = SimpleNamespace(
        chapters=[SimpleNamespace(output_ordinal=3, findings=[
            finding('band_drift', 2),
            finding('dialogue_ratio_drift', 1),
            finding('invented_number', 2, wontfix=object()),
        ])],
        book=[
            finding('book_length', 2),
            finding('end_hook_surface_report', 2),
            finding('must_hold_unver', 2),
        ])
    status = SimpleNamespace(state='fresh', fresh=True, reasons=[], review=rev)
    monkeypatch.setattr(review, 'review_status', lambda _ctx, _store: status)

    # ⚠ 豁免计数**不再由这个函数交**：那张账逐层各一份（`<stage>/wontfix.json`），
    #   由 `inbox_view` 跨层求和。顺手在这里数会把 stage5 那一份数两次。
    items = I._stage5_stalled(SimpleNamespace(ctx=object()))
    assert [(it.rule, it.chapter) for it in items] == [('book_length', None), ('band_drift', None)]
    assert '[3]' in next(it.what for it in items if it.rule == 'band_drift')
    assert all(it.kind == 'stalled' and 'wontfix' in it.how for it in items)
    assert all('stage5.review --wontfix' in it.how for it in items),         'stalled 那条路要指名节点——`--wontfix` 现在两层都有'


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
    from novel.views.inbox import _human_how

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
    assert checked, '正则一条 `node --flag` 都没匹配到——文案格式漂了，这条守卫已经静默失效'


def test_every_revalidate_inbox_instruction_requires_audit_credentials():
    from contracts.invariants import NODES
    from novel.views.inbox import _revalidate_how

    nodes = [node for node, spec in NODES.items() if '--revalidate' in spec.flags]
    assert nodes
    for node in nodes:
        text = _revalidate_how(node)
        assert f'{node} --revalidate --actor <id> --reason <why>' in text
        assert '0 LLM、同轮重判、写入不可变审计' in text


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
    """通用路由从 NODES 派生；有真实提升口时不做 Stage3 专属版本猜测。"""
    from contracts.artifacts import STAGE6_HOOK
    from novel import paths
    from novel.views import inbox as I

    invalid = paths.invalid_of(STAGE6_HOOK)
    store = _InvalidStore(
        present={STAGE6_HOOK, invalid}, versions={}, refuse_version_read=True)
    rows = I._invalid_items(store, 'stage6')
    assert len(rows) == 1 and rows[0].kind == 'rejected'
    assert rows[0].rule == 'stage6.hook'
    assert '_rounds/' in rows[0].how
    assert '`stage6.hook --revalidate --actor <id> --reason <why>`' in rows[0].how


def test_every_human_only_row_offers_something_the_operator_can_actually_run():
    """⭐ 端到端：清单上每一条 `human_only` 的 `how` 都必须是**跑得动的**。

    `Rule.fix` 那条路由由注册期三道校验守着（节点存在 · flag 在该节点的 `flags` 里 ·
    不指向别的层），**兜底那条此前一道都没有**——而 stage6 的语义提示恰好走的是兜底。
    """
    import re as _re

    from contracts.invariants import NODES
    from novel.views import inbox as I

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
    from novel.views import inbox as I

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


@_STAGE6_CORPUS
def test_global_review_pending_fail_and_unver_are_verification_rows(box):
    """未决 / `UNVER` / `FAIL` 进待核验清单，`PASS` 不进。

    ⚠⚠ **审阅面必须从 `build_review` 现算，不许手搓几条 item。** 这里曾经手搓四条
    （`source_sha256='b'*64` + 自造的 `subject_path`/`subject_sha256`），而
    `_global_review_freshness`（`novel/store.py:1120-1138`）拿 `build_review(grounding)`
    现算的主体哈希与逐条**身份四元组**去比 ⇒ 手搓的载荷**按构造永远算不出 `fresh`**；
    而 `stage2/global_review.json` 在 `SELF_FRESH_DETERMINISTIC` 里 ⇒ `store.write` 必拒
    （实测 `ProvenanceRefused: … stale/file_changed`）。
    这是 `docs/LESSONS.md` 第 21 条第一形状：**守卫重拼了生产的实参**。
    ⇒ 身份四元组一个字不动，只改 `state`/`actor`/`reason` 三格——那三格恰恰是新鲜度
    **不看**的，理由就写在 `novel/store.py:1121-1122`（批准完成度是闸门/界面的事，不是新鲜度）。
    """
    from contracts.artifacts import STAGE2_GLOBAL_REVIEW
    from novel import prov
    from novel.orchestrator.state import readonly_ctx
    from novel.quality.global_review import build_review
    from novel.store import ArtifactStore

    expected = build_review(box.read('stage2/global_grounding.json'))
    corpus.declared_dependency(
        len(expected.items) >= 4,
        needs=(f'一份至少 4 条审阅项的 stage2/global_grounding.json'
               f'（这本现算出 {len(expected.items)} 条，凑不出四种决策状态）'),
        produced_by='stage2.global', cost='付费')

    decided = {'actor': 'reviewer', 'decided_at': '2026-08-16T00:00:00+00:00'}
    items = list(expected.items)
    items[1] = items[1].model_copy(update={'state': 'UNVER', 'reason': '证据不足', **decided})
    items[2] = items[2].model_copy(update={'state': 'FAIL', 'reason': '与原文冲突', **decided})
    items[3] = items[3].model_copy(update={'state': 'PASS', 'reason': '已核实', **decided})

    store = ArtifactStore(readonly_ctx(box.dir, 'stage2.global', prov.code_version()))
    store.write(STAGE2_GLOBAL_REVIEW, expected.model_copy(update={'items': items}))
    assert store.freshness(STAGE2_GLOBAL_REVIEW).state == 'fresh', \
        '只改三格决策就被判过期了——那说明新鲜度现在也看决策状态，本条的构造前提没了'

    rows = [row for row in inbox_view(str(box.root), box.sid).items
            if row.kind == 'verification' and row.stage == 'stage2']
    # ⚠ 断言写成**取值面的补集**，不写成手数出来的那几个 id：现役语料一本有几十条审阅项，
    #   数出来的那份一换书就错，而它会红在「这本书碰巧有几条」上，不是它要验的那件事。
    assert {row.rule for row in rows} == {
        item.item_id for item in items if item.state != 'PASS'}
    assert all('--review-decisions' in row.how and 'subject hash' in row.how for row in rows)


@_STAGE6_CORPUS
def test_an_over_length_trim_audit_is_a_verification_row(box):
    import hashlib

    from contracts.artifacts import STAGE4_WRITER_CONTEXT, artifact_rel
    from contracts.schema.evaluation import TrimAudit
    from contracts.schema.stage4 import WriterContext
    from novel import prov
    from novel.orchestrator.state import readonly_ctx
    from novel.stages.stage5 import measure as M
    from novel.store import ArtifactStore

    store = ArtifactStore(readonly_ctx(box.dir, 'stage5.write', prov.code_version()))
    drafts, _invalid, _judged = M.load_drafts(store)
    ordinal = next(iter(sorted(drafts)))
    draft = drafts[ordinal]
    before = hashlib.sha256(draft['text'].encode('utf-8')).hexdigest()
    audit = TrimAudit(
        output_ordinal=ordinal, before_sha256=before,
        reason='超上限；本仓没有自动收缩路径，保留 pretrim，转 verification')
    store.write(artifact_rel('stage5.trim_audit', nn=ordinal), audit)

    rows = [row for row in inbox_view(str(box.root), box.sid).items
            if row.kind == 'verification' and row.stage == 'stage5']
    # ⚠ 断言**不能**写成「恰好只有这一行」：语料书自己可能带着一批未采用的收缩审计
    #   （现役这本 11 章都因为 evaluator profile 缺席保留了 pretrim），
    #   那时这一条会红在「语料碰巧有几行」上，而不是它要验的那件事。
    by_rule = {row.rule: row for row in rows}
    rule = f'trim_audit_ch{ordinal:02d}'
    assert rule in by_rule, f'种下的那一行没进清单：{sorted(by_rule)}'
    assert all(row.rule.startswith('trim_audit_') for row in rows), \
        f'stage5 的 verification 只该由收缩审计产生：{sorted(by_rule)}'
    seeded = by_rule[rule]
    assert seeded.chapter == ordinal
    assert audit.reason in seeded.what, \
        f'这一行不是从种下的那份 audit 渲染的：{seeded.what}'

    # `what` 里的读数全由 `trim_resolution.verification_context` **现算**，
    # 所以这里按产地各取一次再比字面，不只查 `当前 pretrim` / `目标 ` 两个前缀：
    # 拿错章的字数、读错目标区间、把超限差算反这三种漂移只查前缀时全程绿着，
    # 而这条 detail 存在的全部理由就是把这三个数摊给人看。
    # 字数取自 `draft['text']`（装载器那一路）而不是再 `read_own` 一遍正式稿：
    # 两者必须同文本，否则 audit 算不上 current、上面那行就进不了清单。
    chars = M.chars_of(draft['text'])
    entry = next(row for row in store.read(WriterContext, STAGE4_WRITER_CONTEXT).chapters
                 if row.output_ordinal == ordinal)
    lo, hi = M.target_range(entry.model_dump(by_alias=True))
    # 这一行故意不把「语料这一章真的超上限」当前提声明（也不走 `declared_dependency`）：
    # 种下的 audit 本就是合成的，真正被测的是「现算的关系句说不说得准」；
    # 拿它当前提会让整条用例在一本「没有超限章」的书上静默跳过。
    relation = (f'超上限 {chars - hi} 字' if chars > hi
                else f'未超上限（距上限 {hi - chars} 字）')
    assert (f'当前 pretrim {chars} 字，目标 {lo}~{hi} 字，{relation}'
            in seeded.what), f'读数对不上产地：{seeded.what}'
    # ⚠ 这里曾并排断言 `after={after}`，而 `after` 自 cfc747a（收缩臂整臂下线）起
    #   就没有定义了：`TrimAudit.after_sha256` 随另外五格一起删除（它恒等于 before，
    #   是拷贝不是读数），`verification_context` 的 identity 同批收成单边。
    #   身份只剩一个哈希，另一半改由上面三个**带值**的读数承担；
    #   别照旧文案把 `after=` 加回产地。
    assert f'audit before={before}' in seeded.what

    # `how` 里那句「未采用收缩候选」同批消失（收缩候选这个东西已经不存在）。
    # 今天这一格要说的是两件事：保留 pretrim 的现状、以及那条 0 成本销账口；
    # 命令连 `--chapters` 一起断，是因为它必须点名**本章**才能直接粘去跑。
    assert '当前保留 pretrim 原稿' in seeded.how and '0 次 LLM 调用' in seeded.how
    assert (f'stage5.review --retain-pretrim --chapters {ordinal} '
            f'--actor <自报身份> --reason <理由>') in seeded.how
