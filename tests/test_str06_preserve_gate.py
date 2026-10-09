#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_preserve_gate —— CS-H H3 判据④：Preserve 自闸与条目返修（RC-64）。

注册判据：`preserve_name_literal_gate`（本文件即其反例套件）。

自闸＝与下游材料归因同一把尺：能被显示面投影的唯一归属带名面合法；**永不投影**
的字面（裸姓／多义带名面；未登记别名因含裸姓成分同被查出；家族构词不拒）在义务
statement／satisfaction 里被逐条定位（`obligations[i]` 路径）。条目返修＝同一
协议：窄输入（协议规则＋失败条目＋该义务 anchors 的源原子切片＋合法取值），
无关义务字节不变。零付费：fake transport。
"""
from __future__ import annotations

from copy import deepcopy


def _wires():
    from tests.test_str06_hook import _full_chain_wires
    return _full_chain_wires()


def _prepared(tmp_path, wires, transport=None):
    """跑通 Stage2（真实入口）后返回 (ctx, repo, chars, blueprint)。"""
    from novel.llm.paid_requests import digest
    from novel.stages.chain import ChainContext, run_workflow
    from tests.str06_facilities import json_ask, text_ask
    from tests.test_str06_body import _chapters, _make_session
    repo, session, _transport = _make_session(tmp_path, wires, transport=transport)
    ctx = ChainContext(repository=repo, ask=json_ask(session), ask_text=text_ask(session),
                       ask_body=lambda *a: (_ for _ in ()).throw(AssertionError('stub')),
                       chapters=_chapters(), budget=2000, digest=digest)
    run_workflow(repository=repo, chapters=ctx.chapters, ask=ctx.ask, ask_text=ctx.ask_text,
                 ask_body=ctx.ask_body, budget=ctx.budget, through='stage2')
    return ctx, repo, repo.read_model('stage2/characters/characters.json'), \
        repo.read_model('stage2/blueprints/chapter_blueprints.json')


def test_self_gate_flags_bare_surname_and_unregistered_alias_with_item_paths(tmp_path):
    """裸姓／未登记别名逐条定位（`obligations[i]`）；可投影带名面与家族构词合法。"""
    from contracts.schema.workflow import STAGE4_PRESERVE_POLICY_VERSION
    from novel.stages.stage4 import assemble_preserve, preserve_name_issues
    wires = _wires()
    _ctx, _repo, chars, blueprint = _prepared(tmp_path, wires)

    def assemble(wire):
        return assemble_preserve(
            wire, policy_id='preserve-default',
            policy_version=STAGE4_PRESERVE_POLICY_VERSION,
            characters=chars.core_view(), blueprint=blueprint,
            provider='x', model='y', request_sha256='r' * 64)

    wire = deepcopy(wires['preserve'])
    wire['obligations'][0]['statement'] = '陆某登门兴师问罪，芸娘心神不宁'
    checkpoint, errors = assemble(wire)
    assert checkpoint is not None and not errors, errors
    issues = preserve_name_issues(checkpoint, chars)
    flagged = {(i.context['obligation_id'], i.context['field'], i.context['literal'])
               for i in issues}
    assert flagged == {('ob01', 'statement', '陆')}, flagged
    assert issues[0].context['wire_paths'] == [['obligations', 0]]
    assert all(i.code == 'preserve_name_literal' and i.owner == 'preserve'
               for i in issues)
    # 绿对照：现役好稿（纯称谓＋可投影带名面）不触发。
    checkpoint, _errors = assemble(wires['preserve'])
    assert checkpoint is not None
    assert preserve_name_issues(checkpoint, chars) == [], \
        [str(i) for i in preserve_name_issues(checkpoint, chars)]


def test_one_obligation_repair_sends_only_that_item_and_keeps_others_intact(tmp_path):
    """判据④：修一条义务时请求输入输出只含该条及其依赖（对照本根 54,390／
    19,428 的整份重写），无关义务字节不变。"""
    from novel.llm.ledger import read_cost_snapshot
    from novel.stages.chain import run_preserve
    from tests.test_str06_design_patch import PatchingTransport

    wires = _wires()
    bad = deepcopy(wires['preserve'])
    bad['obligations'][0]['statement'] = '陆某登门兴师问罪，夫君面色沉沉'
    transport = PatchingTransport(wires, bad, owner='preserve')
    ctx, repo, _chars, _blueprint = _prepared(tmp_path, wires, transport=transport)
    run_preserve(ctx)

    assert transport.calls.count('preserve') == 2, '首答＋一轮条目返修'
    revision = transport.owner_requests[1]
    # 窄输入：条目返修＋窄返修形状；不带整份投影视图，也不带其他义务的内容。
    assert '【条目返修】' in revision and '【窄返修】' in revision
    assert '句柄即 anchors 闭集' not in revision, '不重发整份输入投影视图'
    assert '【协议规则】' in revision and '【依赖切片】' in revision
    assert 'obligations[0]' in revision
    good = wires['preserve']
    for item in good['obligations'][1:]:
        assert item['statement'][:10] not in revision, '无关义务的 statement 不进窄请求'
    # 输出面：发布的 checkpoint 只换该条 statement，其余义务逐字段不变。
    checkpoint = repo.read_model('runtime/preserve_checkpoint.json')
    published = {o.obligation_id: o for o in checkpoint.obligations}
    for index, item in enumerate(good['obligations']):
        oid = f'ob{index + 1:02d}'
        assert oid in published, item
        if index == 0:
            assert published[oid].statement == item['statement'], '修复值来自好稿'
        else:
            assert published[oid].statement == item['statement'], '无关义务字节不变'
            assert published[oid].satisfaction == (item.get('satisfaction') or '')
    # 成本：原轮＋条目返修各一次实付。
    rows, _partial = read_cost_snapshot(repo.root.parent)
    assert sum(1 for r in rows if not r['cached'] and r['purpose'] == 'preserve') == 2


def test_replay_preserve_round_reports_gate_items(tmp_path):
    """判据⑤（preserve 面）：只读重放把已购 Preserve 回答按现役自闸逐条定位。"""
    from tools.replay_candidates import replay_preserve_round
    wires = _wires()
    _ctx, _repo, chars, blueprint = _prepared(tmp_path, wires)
    wire = deepcopy(wires['preserve'])
    wire['obligations'][0]['statement'] = '陆某登门兴师问罪'
    report = replay_preserve_round(wire, chars, blueprint)
    assert report['status'] == 'ran' and not report['passed']
    assert report['codes'].get('preserve_name_literal') == 1
    assert report['gate_items'] == ['ob01:statement:陆']
    good = replay_preserve_round(wires['preserve'], chars, blueprint)
    assert good['passed'] and good['reuse'] == 'green'


def _checkpoint_with(statement: str):
    from contracts.schema.grounding import (
        ObligationAnchor, PreserveCheckpoint, SourceLineage, SourceObligation,
    )
    from contracts.schema.policy import PreserveKind
    anchor = ObligationAnchor(
        handle='b01_01.c1', source_kind='cognition', holder='hero', mode='learns',
        proposition_ref='p_x', proposition_statement='x', quotes=('x',))
    obligation = SourceObligation(
        obligation_id='ob01', kind=PreserveKind.EVENT_CORE, statement=statement,
        anchors=(anchor,), lineage=SourceLineage(blueprint_refs=('b01_01',),
                                                  character_ids=('fu_jun',)),
        must_preserve=True, satisfaction='自然落地')
    return PreserveCheckpoint(
        checkpoint_id='cp', policy_id='p', policy_version=1, characters_sha256='a' * 64,
        blueprints_sha256='a' * 64, provider='x', model='y', request_sha256='r' * 64,
        obligations=(obligation,), checkpoint_sha256='a' * 64)


def test_self_gate_flags_ambiguous_name_bearing_surface():
    """多义带名面（同一登记称谓属于两个人物）永不投影——下游必成泄漏，必须在 Preserve
    自己的接受循环里被定位；可投影的唯一归属带名面仍合法（绿对照）。"""
    from contracts.schema.source import CharactersArtifactV6
    from novel.stages.stage4 import preserve_name_issues
    from tests.test_str06_hook import _characters
    data = _characters().model_dump(mode='json')
    data['characters']['lu_er'] = {'canonical': '陆二', 'surfaces': [
        {'surface': '陆二', 'from_chapter': 1, 'until_chapter': None, 'source_refs': ['e01_01']},
        {'surface': '陆大哥', 'from_chapter': 1, 'until_chapter': None, 'source_refs': ['e01_01']}]}
    data['name_structure']['people']['lu_er'] = {
        'family_id': 'fam_lu', 'given': '二', 'aliases': [],
        'surface_forms': {'陆二': '{family}{given}', '陆大哥': '{family}大哥'}}
    chars = CharactersArtifactV6.model_validate(data)
    ambiguous = preserve_name_issues(_checkpoint_with('陆大哥登门兴师问罪'), chars)
    assert [(i.context['literal'], i.context['field']) for i in ambiguous] == \
        [('陆', 'statement')], '多义带名面不可绑定、不可投影，须被逐条定位'
    # 绿对照：唯一归属的带名面（陆二）可投影，合法。
    assert preserve_name_issues(_checkpoint_with('陆二登门兴师问罪'), chars) == []


def test_optional_details_use_same_name_gate_and_detail_item_paths():
    from contracts.schema.grounding import SourceDetail
    from novel.stages.stage4 import preserve_name_issues
    from tests.test_str06_hook import _characters
    checkpoint = _checkpoint_with('夫君从床底钻出')
    obligation = checkpoint.obligations[0]
    detail = SourceDetail(detail_id='dt_1234567890abcdef',
                          anchor=obligation.anchors[0], text='陆某从床底钻出')
    changed = checkpoint.model_copy(update={'obligations': (
        obligation.model_copy(update={'details': (detail,)}),)})
    issues = preserve_name_issues(changed, _characters())
    assert len(issues) == 1 and issues[0].code == 'preserve_name_literal'
    assert issues[0].context['field'] == 'details/dt_1234567890abcdef'
    assert issues[0].context['wire_paths'] == [['obligations', 0, 'details', 0]]
    allowed = changed.model_copy(update={'obligations': (
        obligation.model_copy(update={'details': (
            detail.model_copy(update={'text': '陆大哥从床底钻出'}),)}),)})
    assert preserve_name_issues(allowed, _characters()) == []


def test_bad_detail_anchor_repairs_one_item_and_replays_without_new_purchase(tmp_path):
    """真实 Preserve 入口定位单条细节；其它细节和义务不变，已购好答免费采用。"""
    from novel.llm.ledger import read_cost_snapshot
    from novel.stages.chain import run_preserve
    from tests.test_str06_design_patch import PatchingTransport
    wires = _wires()
    anchor = wires['preserve']['obligations'][0]['anchors'][0]
    wires['preserve']['obligations'][0]['details'] = [
        {'anchor': anchor, 'text': '话音未落，人已从床底钻出'},
        {'anchor': anchor, 'text': '站起后先掸一掸衣角'},
    ]
    good = deepcopy(wires['preserve'])
    bad = deepcopy(good)
    bad['obligations'][0]['details'][0]['anchor'] = 'b99_99.e1'
    transport = PatchingTransport(wires, bad, owner='preserve')
    ctx, repo, _chars, _blueprint = _prepared(tmp_path, wires, transport=transport)
    run_preserve(ctx)
    assert transport.calls.count('preserve') == 2
    narrow = transport.owner_requests[1]
    assert '- 路径 obligations[0].details[0]：' in narrow
    assert '- 路径 obligations[0]：' not in narrow
    assert '句柄即 anchors 闭集' not in narrow
    checkpoint = repo.read_model('runtime/preserve_checkpoint.json')
    assert [d.text for d in checkpoint.obligations[0].details] == [
        d['text'] for d in good['obligations'][0]['details']]
    assert checkpoint.obligations[0].details[1].anchor.handle == anchor
    assert [o.statement for o in checkpoint.obligations] == [
        o['statement'] for o in good['obligations']]
    paid_before = sum(not row['cached'] and row['purpose'] == 'preserve'
                      for row in read_cost_snapshot(repo.root.parent)[0])
    run_preserve(ctx)
    paid_after = sum(not row['cached'] and row['purpose'] == 'preserve'
                     for row in read_cost_snapshot(repo.root.parent)[0])
    assert transport.calls.count('preserve') == 2
    assert paid_after == paid_before == 2


def test_preserve_real_entry_accepts_absent_optional_details(tmp_path):
    from novel.stages.chain import run_preserve
    wires = _wires()
    assert all('details' not in item for item in wires['preserve']['obligations'])
    ctx, repo, _chars, _blueprint = _prepared(tmp_path, wires)
    run_preserve(ctx)
    checkpoint = repo.read_model('runtime/preserve_checkpoint.json')
    assert all(not obligation.details for obligation in checkpoint.obligations)


def test_preserve_projection_block_is_reported_without_mutating_evidence():
    """真实姓名映射反例：已登记别名仍可能触发下游无损性守卫。"""
    from novel.stages.stage4 import preserve_name_issues
    from tests.test_str06_display_surface import _characters
    from contracts.schema.source import CharactersArtifactV6
    data = _characters().model_dump(mode='json')
    data['characters']['pei_shuyuan'] = {'canonical': '裴书远', 'surfaces': [
        {'surface': s, 'from_chapter': 1, 'source_refs': ['e01_01']}
        for s in ('裴书远', '书远', '表弟')]}
    data['name_structure']['families']['fam_pei'] = {'literal': '裴', 'forms': []}
    data['name_structure']['people']['pei_shuyuan'] = {
        'family_id': 'fam_pei', 'given': '书远', 'aliases': [],
        'surface_forms': {'裴书远': '{family}{given}', '书远': '{given}', '表弟': None}}
    chars = CharactersArtifactV6.model_validate(data)
    checkpoint = _checkpoint_with('表弟来了')
    obligation = checkpoint.obligations[0].model_copy(update={
        'satisfaction': "裴书远以夫君表弟的身份出现，'表弟/书远'指裴书远；不得张冠李戴。"})
    checkpoint = checkpoint.model_copy(update={'obligations': (obligation,)})
    before = checkpoint.model_dump()
    issues = preserve_name_issues(checkpoint, chars)
    assert any(i.code == 'preserve_name_projection' for i in issues), issues
    assert all(i.context['wire_paths'] == [['obligations', 0]] for i in issues)
    assert checkpoint.model_dump() == before, 'validator 不改条件与源证据'
    fixed = checkpoint.model_copy(update={'obligations': (obligation.model_copy(update={
        'satisfaction': '此人物须以夫君表弟身份出现，表兄弟关系不得错配。'}),)})
    assert preserve_name_issues(fixed, chars) == []


def test_projection_block_repairs_only_one_obligation_in_real_entry(tmp_path):
    from novel.stages.chain import run_preserve
    from tests.test_str06_design_patch import PatchingTransport
    wires = _wires()
    bad = deepcopy(wires['preserve'])
    # fixture 登记“陆大哥／陆兄”两种带名面；在同一句中不能无损压成一面。
    bad['obligations'][0]['statement'] = '陆大哥向娘子坦白，陆兄确有难言之隐'
    transport = PatchingTransport(wires, bad, owner='preserve')
    ctx, repo, _chars, _blueprint = _prepared(tmp_path, wires, transport=transport)
    run_preserve(ctx)
    assert transport.calls.count('preserve') == 2
    assert '- 路径 obligations[0]：' in transport.owner_requests[1]
    checkpoint = repo.read_model('runtime/preserve_checkpoint.json')
    for index, item in enumerate(wires['preserve']['obligations']):
        assert checkpoint.obligations[index].statement == item['statement']
        assert [a.handle for a in checkpoint.obligations[index].anchors] == item['anchors']


def test_normal_name_reveal_is_not_rewritten_by_preserve_portability_gate(tmp_path):
    """源证据要求的两个姓名面不折叠；目标释放位置继续交给 Design 判断。"""
    from contracts.schema.workflow import STAGE4_PRESERVE_POLICY_VERSION
    from novel.stages.stage4 import assemble_preserve, preserve_name_issues, project_brief
    wires = _wires()
    wires['ch01']['beats'][0]['cognition'] = [{
        'proposition': {'ref': 'p_name', 'statement': '陆大哥正是被称作陆兄的人'},
        'holder': 'yun_niang', 'mode': 'learns',
        'basis': [{'segment': 'e01_01', 'quote': '唤夫君为陆大哥'}]}]
    _ctx, _repo, chars, blueprint = _prepared(tmp_path, wires)
    wire = deepcopy(wires['preserve'])
    wire['obligations'][0].update(kind='identity', anchors=['b01_01.c1'],
        statement='陆大哥正是陆兄', satisfaction='第一次点明陆大哥就是陆兄')
    checkpoint, errors = assemble_preserve(wire, policy_id='preserve-default',
        policy_version=STAGE4_PRESERVE_POLICY_VERSION, characters=chars.core_view(),
        blueprint=blueprint, provider='x', model='y', request_sha256='r' * 64)
    assert checkpoint is not None and not errors, errors
    brief, _ = project_brief(checkpoint, chars.core_view(), blueprint, structure=chars.name_structure)
    assert set(brief.requirements[0].knowledge_changes[0].requires_released) == {'陆大哥', '陆兄'}
    assert preserve_name_issues(checkpoint, chars, blueprint) == []
    assert checkpoint.obligations[0].anchors[0].quotes
