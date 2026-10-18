#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_preserve_gate —— CS-H H3 判据④：Preserve 自闸与条目返修（RC-64）。

注册判据：`preserve_name_literal_gate`（本文件即其反例套件）。

policy11 自闸按作者声明区分人物与普通义；未声明候选是未决，不伪称已经泄漏。
人物引用只取登记称谓，显式 char_id 能区分同形称谓。义务 statement／satisfaction
及细节问题逐条定位到 `obligations[i]`；历史 policy 保留原身份。条目返修＝同一
协议：窄输入（协议规则＋失败条目＋该义务 anchors 的源原子切片＋合法取值），
无关义务字节不变。零付费：fake transport。
"""
from __future__ import annotations

from copy import deepcopy
from tests.factories import declared_stage4_text


def _wires():
    from tests.test_str06_hook import _full_chain_wires
    return _full_chain_wires()


def _prepared(tmp_path, wires, transport=None, nodes=None):
    """跑通 Stage2（真实入口；命名前移批可加跑 naming）后返回
    (ctx, repo, chars, blueprint)。"""
    from novel.llm.paid_requests import digest
    from novel.stages.chain import ChainContext, run_workflow
    from tests.str06_facilities import name_map_of, json_ask, text_ask
    from tests.test_str06_body import _chapters, _make_session
    repo, session, _transport = _make_session(tmp_path, wires, transport=transport)
    ctx = ChainContext(repository=repo, ask=json_ask(session), ask_text=text_ask(session),
                       ask_body=lambda *a: (_ for _ in ()).throw(AssertionError('stub')),
                       chapters=_chapters(), budget=2000, digest=digest)
    run_workflow(repository=repo, chapters=ctx.chapters, ask=ctx.ask, ask_text=ctx.ask_text,
                 ask_body=ctx.ask_body, budget=ctx.budget, through='stage2')
    if nodes:
        # 命名前移批：naming 在 Stage4 腿最前，追加跑选定节点（不重跑 Stage2）。
        run_workflow(repository=repo, chapters=ctx.chapters, ask=ctx.ask,
                     ask_text=ctx.ask_text, ask_body=ctx.ask_body, budget=ctx.budget,
                     through='stage4', node_filter=nodes)
    return ctx, repo, repo.read_model('stage2/characters/characters.json'), \
        repo.read_model('stage2/blueprints/chapter_blueprints.json')


def test_self_gate_flags_old_name_literal_and_marker_with_item_paths(tmp_path):
    """policy 12：义务文本里的源旧字面（改名人物的 name-bearing 面）与身份文本
    标记残片按条目定位；目标姓名＋保留称谓直书不触发。"""
    from contracts.schema.workflow import STAGE4_PRESERVE_POLICY_VERSION
    from novel.stages.stage4 import assemble_preserve, preserve_name_issues
    from tests.str06_facilities import hook_naming_map
    wires = _wires()
    _ctx, _repo, chars, blueprint = _prepared(tmp_path, wires)
    name_map = hook_naming_map()

    def assemble(wire):
        return assemble_preserve(
            wire, policy_id='preserve-default',
            policy_version=STAGE4_PRESERVE_POLICY_VERSION,
            characters=chars.core_view(), blueprint=blueprint,
            provider='x', model='y', request_sha256='r' * 64,
            name_map=name_map, name_map_sha256='n' * 64)

    wire = deepcopy(wires['preserve'])
    wire['obligations'][0]['statement'] = '芸娘心神不宁，[[name:yun_niang|苏芸娘]]登门'
    checkpoint, errors = assemble(wire)
    assert checkpoint is not None and not errors, errors
    issues = preserve_name_issues(checkpoint, chars, name_map)
    flagged = {(i.context['obligation_id'], i.context['field'], i.code)
               for i in issues}
    assert ('ob01', 'statement', 'preserve_old_name_literal') in flagged, flagged
    assert ('ob01', 'statement', 'preserve_name_text_marker') in flagged, flagged
    assert all(i.context['wire_paths'] == [['obligations', 0]] for i in issues)
    assert all(i.owner == 'preserve' for i in issues)
    # 绿对照：现役好稿（目标姓名＋保留称谓直书）不触发。
    checkpoint, _errors = assemble(wires['preserve'])
    assert checkpoint is not None
    assert preserve_name_issues(checkpoint, chars, name_map) == [], \
        [str(i) for i in preserve_name_issues(checkpoint, chars, name_map)]


def test_one_obligation_repair_sends_only_that_item_and_keeps_others_intact(tmp_path):
    """判据④：修一条义务时请求输入输出只含该条及其依赖（对照本根 54,390／
    19,428 的整份重写），无关义务字节不变。"""
    from novel.llm.ledger import read_cost_snapshot
    from novel.stages.chain import run_preserve
    from tests.test_str06_design_patch import PatchingTransport

    wires = _wires()
    bad = deepcopy(wires['preserve'])
    bad['obligations'][0]['statement'] = '芸娘登门兴师问罪，夫君面色沉沉'
    transport = PatchingTransport(wires, bad, owner='preserve')
    ctx, repo, _chars, _blueprint = _prepared(
        tmp_path, wires, transport=transport, nodes=('str06.stage4.naming',))
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
    _ctx, _repo, chars, blueprint = _prepared(
        tmp_path, wires, nodes=('str06.stage4.naming',))
    name_map = _repo.read_model('stage4/naming/name_map.json')
    wire = deepcopy(wires['preserve'])
    wire['obligations'][0]['statement'] = '芸娘登门兴师问罪'
    report = replay_preserve_round(wire, chars, blueprint, name_map)
    assert report['status'] == 'ran' and not report['passed']
    assert report['codes'].get('preserve_old_name_literal') == 1
    assert report['gate_items'] == ['ob01:statement:芸娘']
    good = replay_preserve_round(wires['preserve'], chars, blueprint, name_map)
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
        checkpoint_id='cp', policy_id='p', policy_version=12, characters_sha256='a' * 64,
        blueprints_sha256='a' * 64, name_map_sha256='n' * 64,
        provider='x', model='y', request_sha256='r' * 64,
        obligations=(obligation,), checkpoint_sha256='a' * 64)


def test_self_gate_does_not_guess_from_bare_surname_or_pure_appellation():
    """policy 12 只拦改名人物的 name-bearing 旧字面：裸姓单字与保留纯称谓不拦
    （候选召回与硬闸是两件事）；身份文本标记残片另立规则码。"""
    from novel.stages.stage4 import preserve_name_issues
    from tests.str06_facilities import hook_naming_map
    from tests.test_str06_hook import _characters
    chars = _characters()
    name_map = hook_naming_map()
    assert preserve_name_issues(_checkpoint_with('陆某登门兴师问罪'), chars, name_map) == [],         '裸姓单字是候选召回事，不作硬闸'
    assert preserve_name_issues(_checkpoint_with('夫君从床底钻出'), chars, name_map) == [],         '保留纯称谓是合法目标面'
    issues = preserve_name_issues(_checkpoint_with('芸娘登门兴师问罪'), chars, name_map)
    assert [i.code for i in issues] == ['preserve_old_name_literal']
    assert issues[0].context['literal'] == '芸娘'
    marker = preserve_name_issues(
        _checkpoint_with('[[name:yun_niang|柳婉]]登门'), chars, name_map)
    assert [i.code for i in marker] == ['preserve_name_text_marker']

def test_optional_details_use_same_name_gate_and_detail_item_paths():
    """细节文本与义务同一把尺：旧名面按 details 条目路径定位；目标姓名合法。"""
    from contracts.schema.grounding import SourceDetail
    from novel.stages.stage4 import preserve_name_issues
    from tests.str06_facilities import hook_naming_map
    from tests.test_str06_hook import _characters
    chars = _characters()
    name_map = hook_naming_map()
    checkpoint = _checkpoint_with('夫君从床底钻出')
    obligation = checkpoint.obligations[0]
    detail = SourceDetail(detail_id='dt_1234567890abcdef',
                          anchor=obligation.anchors[0], text='芸娘从床底钻出')
    changed = checkpoint.model_copy(update={'obligations': (
        obligation.model_copy(update={'details': (detail,)}),)})
    issues = preserve_name_issues(changed, chars, name_map)
    assert len(issues) == 1 and issues[0].code == 'preserve_old_name_literal'
    assert issues[0].context['field'] == 'details/dt_1234567890abcdef'
    assert issues[0].context['wire_paths'] == [['obligations', 0, 'details', 0]]
    allowed = changed.model_copy(update={'obligations': (
        obligation.model_copy(update={'details': (
            detail.model_copy(update={'text': '夫君从床底钻出，顾大哥拍去衣角'}),)}),)})
    assert preserve_name_issues(allowed, chars, name_map) == []

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
    ctx, repo, _chars, _blueprint = _prepared(
        tmp_path, wires, transport=transport, nodes=('str06.stage4.naming',))
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
    ctx, repo, _chars, _blueprint = _prepared(
        tmp_path, wires, nodes=('str06.stage4.naming',))
    run_preserve(ctx)
    checkpoint = repo.read_model('runtime/preserve_checkpoint.json')
    assert all(not obligation.details for obligation in checkpoint.obligations)


def test_preserve_registered_forms_are_accepted_without_mutating_evidence():
    """已证明同人的多个登记面不因共现被拒；validator 保持条件、关系与证据。"""
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
    from tests.str06_facilities import hook_naming_map
    name_map = hook_naming_map()
    checkpoint = _checkpoint_with('表弟来了')
    # 命名前移批（policy 12）：已登记称谓直书，无身份文本标记。
    obligation = checkpoint.obligations[0].model_copy(update={
        'satisfaction': "裴书远以夫君表弟的身份出现，'表弟/书远'指裴书远；不得张冠李戴。"})
    checkpoint = checkpoint.model_copy(update={'obligations': (obligation,)})
    before = checkpoint.model_dump()
    issues = preserve_name_issues(checkpoint, chars, name_map)
    assert issues == [], issues
    assert checkpoint.model_dump() == before, 'validator 不改条件与源证据'
    fixed = checkpoint.model_copy(update={'obligations': (obligation.model_copy(update={
        'satisfaction': '此人物须以夫君表弟身份出现，表兄弟关系不得错配。'}),)})
    assert preserve_name_issues(fixed, chars, name_map) == []


def test_natural_mixed_names_reach_the_writer_packet_without_preserve_repair(tmp_path):
    """首答自然混称经 Preserve 采用、Stage4 编译到真实写手面，不购买条目返修。"""
    from novel.llm.ledger import read_cost_snapshot
    from novel.stages.chain import run_preserve, run_workflow
    from novel.stages.common import (
        project_chapter_packet, render_chapter_packet, writer_material_issues,
        writer_name_projections,
    )
    from tests.test_str06_design_patch import PatchingTransport
    wires = _wires()
    mixed = deepcopy(wires['preserve'])
    # 叙述称谓与对白称呼已登记同人；每次提及及其间行动都应保留。
    # 命名前移批（policy 12）：目标姓名直书（fu_jun→顾），无身份文本标记。
    mixed['obligations'][0]['statement'] = '顾大哥向娘子坦白，顾兄确有难言之隐'
    transport = PatchingTransport(wires, mixed, owner='preserve')
    ctx, repo, _chars, _blueprint = _prepared(
        tmp_path, wires, transport=transport, nodes=('str06.stage4.naming',))
    run_preserve(ctx)
    assert transport.calls.count('preserve') == 1
    assert len(transport.owner_requests) == 1, '首答可用，不发窄返修请求'
    checkpoint = repo.read_model('runtime/preserve_checkpoint.json')
    for index, item in enumerate(mixed['obligations']):
        assert checkpoint.obligations[index].statement == item['statement']
        assert [a.handle for a in checkpoint.obligations[index].anchors] == item['anchors']
    run_workflow(
        repository=repo, chapters=ctx.chapters, ask=ctx.ask, ask_text=ctx.ask_text,
        ask_body=ctx.ask_body, budget=ctx.budget, through='stage4')
    ir = repo.read_model('stage4/ir/writer_book_ir.json')
    packet = project_chapter_packet(ir, 1)
    prompt = render_chapter_packet(packet)
    # 批次 B 中间态：Brief 用目标姓名直书，写手包原样带出；源旧面不出现。
    assert '顾大哥向娘子坦白，顾兄确有难言之隐' in prompt
    assert '陆大哥向娘子坦白' not in prompt and '陆兄确有难言之隐' not in prompt
    assert writer_material_issues(packet, internal_ids=set()) == ()
    # 批次 B 中间态：Brief 目标姓名是直书文本，不产生投影行；源登记面的投影
    # 仍按 Design 显式释放发生（切目标面是批次 C）。
    assert not [(row['literal'], row['replacement'])
                for row in writer_name_projections(packet)
                if row['literal'] in ('顾大哥', '顾兄')]
    rows, _partial = read_cost_snapshot(repo.root.parent)
    assert sum(not row['cached'] and row['purpose'] == 'preserve' for row in rows) == 1


def test_normal_name_reveal_is_not_rewritten_by_preserve_portability_gate(tmp_path):
    """源证据要求的两个姓名面不折叠；目标释放位置继续交给 Design 判断。"""
    from contracts.schema.workflow import STAGE4_PRESERVE_POLICY_VERSION
    from novel.stages.stage4 import assemble_preserve, preserve_name_issues, project_brief
    wires = _wires()
    wires['ch01']['beats'][0]['cognition'] = [{
        'proposition': {'ref': 'p_name', 'statement': '陆大哥正是被称作陆兄的人'},
        'holder': 'yun_niang', 'mode': 'learns',
        'basis': [{'segment': 'e01_01', 'quote': '唤夫君为陆大哥'}]}]
    _ctx, _repo, chars, blueprint = _prepared(
        tmp_path, wires, nodes=('str06.stage4.naming',))
    name_map = _repo.read_model('stage4/naming/name_map.json')
    wire = deepcopy(wires['preserve'])
    wire['obligations'][0].update(kind='identity', anchors=['b01_01.c1'],
        statement='顾大哥正是顾兄',
        satisfaction='第一次点明顾大哥就是顾兄')
    checkpoint, errors = assemble_preserve(
        wire, policy_id='preserve-default', policy_version=STAGE4_PRESERVE_POLICY_VERSION,
        characters=chars.core_view(), blueprint=blueprint, name_map=name_map,
        name_map_sha256='n' * 64, provider='x', model='y', request_sha256='r' * 64)
    assert checkpoint is not None and not errors, errors
    brief, _ = project_brief(checkpoint, chars.core_view(), blueprint, structure=chars.name_structure, name_map=name_map)
    assert set(brief.requirements[0].knowledge_changes[0].requires_released) == {'陆大哥', '陆兄'}
    assert preserve_name_issues(checkpoint, chars, name_map) == []
    assert checkpoint.obligations[0].anchors[0].quotes
