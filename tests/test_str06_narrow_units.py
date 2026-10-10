#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_narrow_units —— 节点内窄调用的独立工作单元与来源账引用（免费）。

【验证边界】`name_variants`（names 节点内）与 `cross_cause`（associate 节点内）
两个窄调用 owner 与各自节点的主调用请求身份不同、内容轮序各自独立。若共用节点
的 `book` 单元账：
  • 后一个 ask 取轮前 `_close_unadopted` 会把前一个**已验收但未封存**的首答轮
    记成 `failed/superseded_unadopted`（已采用的结构答案被当作失败）；
  • 两个调用瓜分同一份 4 轮内容额度；
  • 写盘 refs 若丢掉窄调用的引用，产物来源账 usage／prompts 漏记已付费的调用，
    只有因果候选的 associate 更会被标成免费来源。

全部走真实 `PaidRequestSession`＋fake transport 的整段 Stage2（不是 `_AcceptingAsk`
假 ask——假 ask 不经过工作单元与来源账边界）；零真实模型调用。
"""
from __future__ import annotations

import json
from copy import deepcopy

import pytest

from contracts.invariants import NARROW_OWNER_UNITS, narrow_unit_scopes
from novel.llm.paid_requests import commit_bundles, semantic_owner_unit
from novel.llm.transport import ChatResponse
from novel.stages.chain import run_workflow
from novel.work_units import WorkUnitBudget, WorkUnitRefused
from tests.str06_facilities import json_ask, text_ask
from tests.test_str06_body import _RoutingTransport, _make_session
from tests.test_str06_name_resolution import _case
from tests.test_str06_stage2_publish import (
    CHAPTERS, HOOK, STAGE2_NODES, _full_stage2_wires,
)

NAMES_REL = 'stage2/observations/names.json'
ASSOCIATE_REL = 'stage2/observations/associate.json'
OBSERVE_NODES = [node for node in STAGE2_NODES if node != 'str06.stage2.compile']


class _NarrowTransport(_RoutingTransport):
    """路由窄调用 `name_variants`，并可对任一 owner 先给若干份脚本化（失败）回答。"""

    def __init__(self, wires, scripts=None):
        super().__init__(wires)
        self.scripts = {owner: list(rows) for owner, rows in (scripts or {}).items()}

    @staticmethod
    def _owner(request):
        if '判定疑似姓名异写候选' in request.user:
            return 'name_variants'
        return _RoutingTransport._owner(request)

    def send(self, request):
        owner = self._owner(request)
        script = self.scripts.get(owner)
        if script:
            self.calls.append(owner)
            return ChatResponse(
                content=json.dumps(script.pop(0), ensure_ascii=False),
                finish_reason='stop',
                usage={'prompt_tokens': 10, 'completion_tokens': 10}, model='fake')
        return super().send(request)


def _run_stage2(tmp_path, chapters, wires, *, scripts=None, nodes=None):
    repo, session, transport = _make_session(
        tmp_path, wires, transport=_NarrowTransport(wires, scripts))
    run_workflow(repository=repo, chapters=chapters, hook_copy=HOOK,
                 ask=json_ask(session), ask_text=text_ask(session),
                 ask_body=lambda owner, prompt: pytest.fail('Stage2 不得购买正文'),
                 budget=800, through='stage2', node_filter=nodes or STAGE2_NODES)
    return repo, session, transport


def _unit(repo, owner) -> object:
    node, scope = semantic_owner_unit(owner)
    return WorkUnitBudget(repo.root.parent, 'run', node, 'executor', scope,
                          read_only=True)._read()


def _provenance(repo, rel) -> dict:
    return json.loads(repo.path(rel).read_text(encoding='utf-8'))['_provenance']


def _is_free(provenance: dict) -> bool:
    return not provenance.get('prompts') and not provenance.get('usage')


@pytest.fixture
def names_case():
    chapters, wires = _case()
    return chapters, wires


# ───────────────────────── 登记表与单元账准入 ─────────────────────────


def test_narrow_owner_units_are_registered_per_node_and_resolve_to_own_scope():
    """窄调用 owner 归属所在节点、独占 `book:<owner>` scope；主 owner 仍是 `book`。"""
    assert semantic_owner_unit('names') == ('str06.stage2.names', 'book')
    assert semantic_owner_unit('associate') == ('str06.stage2.associate', 'book')
    assert semantic_owner_unit('name_variants') == (
        'str06.stage2.names', 'book:name_variants')
    assert semantic_owner_unit('cross_cause') == (
        'str06.stage2.associate', 'book:cross_cause')
    assert set(NARROW_OWNER_UNITS) == {'name_variants', 'cross_cause'}
    assert narrow_unit_scopes('str06.stage2.names') == ('book:name_variants',)
    assert narrow_unit_scopes('str06.stage2.associate') == ('book:cross_cause',)
    assert narrow_unit_scopes('str06.stage2.book') == ()


def test_unit_ledger_admits_only_registered_narrow_scopes(tmp_path):
    """单元账准入：登记过的窄 scope 放行；未登记的 scope、窄 scope 挂错节点都关闭式拒绝。"""
    chapters, wires = _case()
    repo, _session, _transport = _make_session(tmp_path, wires)
    base = repo.root.parent
    WorkUnitBudget(base, 'run', 'str06.stage2.names', 'executor', 'book:name_variants')
    WorkUnitBudget(base, 'run', 'str06.stage2.associate', 'executor', 'book:cross_cause')
    for node, scope in (('str06.stage2.names', 'book:cross_cause'),
                        ('str06.stage2.associate', 'book:name_variants'),
                        ('str06.stage2.names', 'book:other'),
                        ('str06.stage2.book', 'book:name_variants'),
                        ('str06.stage2.chapters', 'book:name_variants')):
        with pytest.raises(WorkUnitRefused):
            WorkUnitBudget(base, 'run', node, 'executor', scope)


# ───────────────────────── names ＋ name_variants ─────────────────────────


def test_names_and_variants_use_separate_units_and_keep_first_answer_valid(
        tmp_path, names_case):
    """(a)(b)：两个 ask 落在不同工作单元；已验收的结构答案不被记成 failed。"""
    chapters, wires = names_case
    repo, _session, transport = _run_stage2(tmp_path, chapters, wires)
    assert transport.calls.count('names') == 1
    assert transport.calls.count('name_variants') == 1
    main, narrow = _unit(repo, 'names'), _unit(repo, 'name_variants')
    assert main.unit_id != narrow.unit_id
    assert (main.node, main.scope) == ('str06.stage2.names', 'book')
    assert (narrow.node, narrow.scope) == ('str06.stage2.names', 'book:name_variants')
    for ledger in (main, narrow):
        assert [t.ordinal for t in ledger.turns] == [1]
        assert ledger.turns[0].state in ('delivered', 'adopted'), (
            ledger.scope, ledger.turns[0].state, ledger.turns[0].reason_code)
        assert ledger.turns[0].reason_code != 'superseded_unadopted'


def test_names_provenance_claims_both_calls_and_bundle_adopts_both_units(
        tmp_path, names_case):
    """(c)：names.json 的来源账带结构答案＋变体窄调用两份 prompts，实付 2 次；
    CommitBundle 的采用引用指向两个独立单元的内容轮。"""
    chapters, wires = names_case
    repo, _session, _transport = _run_stage2(tmp_path, chapters, wires)
    provenance = _provenance(repo, NAMES_REL)
    prompts = provenance['prompts']
    assert any(key.startswith('names.') for key in prompts), prompts
    assert any(key.startswith('name_variants.') for key in prompts), prompts
    assert provenance['usage']['calls_made'] == 2
    bundle = next(b for b in commit_bundles(repo.root) if b.artifact == NAMES_REL)
    main, narrow = _unit(repo, 'names'), _unit(repo, 'name_variants')
    assert {(a.owner, a.unit_id, a.turn_id) for a in bundle.adoptions} == {
        ('names', main.unit_id, main.turns[0].turn_id),
        ('name_variants', narrow.unit_id, narrow.turns[0].turn_id)}


def test_names_without_variant_candidates_claims_only_the_structure_call(tmp_path):
    """无变体候选＝零窄调用：不开窄单元，来源账只含结构答案、实付 1 次。"""
    repo, _session, transport = _run_stage2(
        tmp_path, deepcopy(CHAPTERS), _full_stage2_wires(), nodes=OBSERVE_NODES[:3])
    assert 'name_variants' not in transport.calls
    provenance = _provenance(repo, NAMES_REL)
    assert provenance['usage']['calls_made'] == 1
    assert not any(k.startswith('name_variants.') for k in provenance['prompts'])
    node, scope = semantic_owner_unit('name_variants')
    unit = WorkUnitBudget(repo.root.parent, 'run', node, 'executor', scope,
                          read_only=True)
    assert unit.unit_id not in unit._registry().unit_ids, '零调用不开窄单元'


def test_narrow_unit_has_its_own_four_round_budget(tmp_path, names_case):
    """(g)：主调用用满返修轮后，窄调用仍有自己的 4 轮额度（共用单元时 3＋3＞4 耗尽）。"""
    chapters, wires = names_case
    good = wires['names']
    # 两份失败回答的根因任务不同（缺人物条目 → Characters 之外的人物），避免被
    # RC-59 无进展闸提前截停；各自把主调用推到第 3 轮才通过。
    ghost = deepcopy(good)
    ghost['people']['ghost'] = deepcopy(good['people']['yun_niang'])
    bad_names = [
        {**deepcopy(good), 'people': {'yun_niang': deepcopy(good['people']['yun_niang'])}},
        ghost]
    variants = wires['name_variants']
    bad_variants = [
        {'protocol_version': variants['protocol_version'], 'variants': []},
        {'protocol_version': variants['protocol_version'], 'variants': [
            {**deepcopy(variants['variants'][0]), 'surface': '苏芸娘X'}]}]
    repo, _session, transport = _run_stage2(
        tmp_path, chapters, wires,
        scripts={'names': bad_names, 'name_variants': bad_variants},
        nodes=OBSERVE_NODES[:3])
    assert transport.calls.count('names') == 3
    assert transport.calls.count('name_variants') == 3
    main, narrow = _unit(repo, 'names'), _unit(repo, 'name_variants')
    assert len(main.turns) == 3 and len(narrow.turns) == 3
    assert main.base_content_limit == narrow.base_content_limit == 4
    assert main.turns[-1].state in ('delivered', 'adopted')
    assert narrow.turns[-1].state in ('delivered', 'adopted')


def test_names_crash_resume_replays_for_free_and_leaves_narrow_ledger_intact(
        tmp_path, names_case):
    """(f)：删掉 names.json 重跑 names 节点——零新增 wire 调用，两个单元账字节不变。"""
    chapters, wires = names_case
    repo, session, transport = _run_stage2(tmp_path, chapters, wires)
    main, narrow = _unit(repo, 'names'), _unit(repo, 'name_variants')
    before = {ledger.scope: ledger.model_dump_json() for ledger in (main, narrow)}
    wire_calls = list(transport.calls)
    repo.path(NAMES_REL).unlink()
    run_workflow(repository=repo, chapters=chapters, hook_copy=HOOK,
                 ask=json_ask(session), ask_text=text_ask(session),
                 ask_body=lambda owner, prompt: pytest.fail('Stage2 不得购买正文'),
                 budget=800, through='stage2', node_filter=['str06.stage2.names'])
    assert transport.calls == wire_calls, '崩溃续跑零新增 wire 调用'
    after = {ledger.scope: ledger.model_dump_json()
             for ledger in (_unit(repo, 'names'), _unit(repo, 'name_variants'))}
    assert after == before
    assert _provenance(repo, NAMES_REL)['usage']['calls_made'] == 2, (
        '续跑重写的来源账仍认领两次实付调用')


# ───────────────────────── associate ＋ cross_cause ─────────────────────────


def test_associate_and_cross_cause_use_separate_units_and_claim_both_calls(tmp_path):
    """(a)(b)(c) 的 associate 侧：身份调用＋因果窄调用各一个单元，来源账认领两次。"""
    chapters, wires = _case()
    repo, _session, transport = _run_stage2(tmp_path, chapters, wires)
    assert transport.calls.count('associate') == 1
    assert transport.calls.count('cross_cause') == 1
    main, narrow = _unit(repo, 'associate'), _unit(repo, 'cross_cause')
    assert main.unit_id != narrow.unit_id
    assert (main.scope, narrow.scope) == ('book', 'book:cross_cause')
    assert main.turns[0].state in ('delivered', 'adopted')
    assert main.turns[0].reason_code != 'superseded_unadopted'
    provenance = _provenance(repo, ASSOCIATE_REL)
    assert any(k.startswith('associate.') for k in provenance['prompts'])
    assert any(k.startswith('cross_cause.') for k in provenance['prompts'])
    assert provenance['usage']['calls_made'] == 2
    bundle = next(b for b in commit_bundles(repo.root) if b.artifact == ASSOCIATE_REL)
    assert {(a.owner, a.unit_id) for a in bundle.adoptions} == {
        ('associate', main.unit_id), ('cross_cause', narrow.unit_id)}


def test_cause_only_associate_is_a_paid_artifact_not_a_free_one(tmp_path):
    """(d)：无身份候选但有因果候选（facts 流的常态路径）——associate.json 是付费来源账，
    usage 计到 cross_cause 那一次，不是 FreeProvenance。"""
    chapters, wires = _plain_flow_case(with_facts=True)
    repo, _session, transport = _run_stage2(tmp_path, chapters, wires)
    assert 'associate' not in transport.calls and transport.calls.count('cross_cause') == 1
    provenance = _provenance(repo, ASSOCIATE_REL)
    assert not _is_free(provenance), provenance
    assert list(provenance['prompts']) and all(
        k.startswith('cross_cause.') for k in provenance['prompts'])
    assert provenance['usage']['calls_made'] == 1
    bundle = next(b for b in commit_bundles(repo.root) if b.artifact == ASSOCIATE_REL)
    narrow = _unit(repo, 'cross_cause')
    assert [(a.owner, a.unit_id) for a in bundle.adoptions] == [
        ('cross_cause', narrow.unit_id)]
    assert len(repo.read_model('stage2/blueprints/chapter_blueprints.json').chapters) == 3
    assert repo.read_model('stage2/characters/characters.json').characters


def test_zero_candidate_associate_stays_declared_free(tmp_path):
    """(e)：身份与因果候选都为空＝零模型参与，associate.json 仍显式声明免费来源账。"""
    chapters, wires = _plain_flow_case(with_facts=False)
    repo, _session, transport = _run_stage2(tmp_path, chapters, wires)
    assert 'associate' not in transport.calls and 'cross_cause' not in transport.calls
    assert _is_free(_provenance(repo, ASSOCIATE_REL))
    assert not any(a.owner in ('associate', 'cross_cause')
                   for b in commit_bundles(repo.root) if b.artifact == ASSOCIATE_REL
                   for a in b.adoptions)
    assert len(repo.read_model('stage2/blueprints/chapter_blueprints.json').chapters) == 3
    assert repo.read_model('stage2/characters/characters.json').characters


# ───────────────────────── 窄 owner 与 wire v10 的免费补丁真实干跑 ─────────────────────────

def _preview_args(chapters):
    from novel.stages.chain import build_evidence

    return dict(evidence=build_evidence(chapters, HOOK), budget=800,
                chapter_count=len(chapters))


def _plain_flow_case(*, with_facts):
    """三章延续同一场景：无身份候选；可选词表流决定因果候选分母。"""
    from tests.test_str06_phase2_facts import _book_wire_v9

    chapters = deepcopy(CHAPTERS)
    wires = _full_stage2_wires()
    facts = ([{'statement': '夫君藏身床底', 'evidence_refs': ['e01_01'],
               'known_at_start': []}] if with_facts else [])
    wires['book'] = _book_wire_v9(*facts)
    for no in range(1, 4):
        wires[f'ch{no:02d}'] = {'protocol_version': 'stage2-chapters-v10', 'beats': [
            {'event': f'第{no}章延续场景', 'on_stage': ['yun_niang'],
             'start': f'e{no:02d}_01', 'new_scene': no == 1,
             'know': ([{'who': 'yun_niang', 'how': 'learns', 'fact': 'f01',
                        'evidence_refs': [f'e{no:02d}_01']}] if with_facts else [])}]}
    wires['associate'] = {'protocol_version': 'stage2-associate-v5',
                          'same_proposition': [], 'loop_pairing': [],
                          'scene_continuation': [], 'cross_chapter_cause': []}
    wires['cross_cause'] = {'protocol_version': 'stage2-cross-cause-v1', 'causes': [
        {'from_beat': f'ch{left:02d}.b1', 'to_beat': f'ch{right:02d}.b1', 'kind': 'none'}
        for left, right in (((1, 2), (1, 3), (2, 3)) if with_facts else ())]}
    return chapters, wires


def test_narrow_owner_free_previews_recheck_real_stage2_without_writes_or_calls(tmp_path):
    """正式盘面上两段原 wire 的预览复用现役编译，身份预览明确不作最终发布证明。"""
    from novel.stages.review import dry_run_patch

    chapters, wires = _case()
    repo, _session, transport = _run_stage2(tmp_path, chapters, wires)
    args = _preview_args(chapters)
    paths = (NAMES_REL, ASSOCIATE_REL, 'stage2/characters/characters.json',
             'stage2/blueprints/chapter_blueprints.json')
    before = {rel: repo.path(rel).read_bytes() for rel in paths}
    calls = list(transport.calls)
    for owner in ('names', 'name_variants', 'associate', 'cross_cause'):
        green = dry_run_patch(repo, owner, deepcopy(wires[owner]), **args)
        assert green['status'] == 'ran' and not green['errors'] and not green['undecided'], green
        if owner == 'associate':
            assert green['association_phase'] == 'identity' and green['final_status'] == 'not_run'
    bad_variants = deepcopy(wires['name_variants'])
    bad_variants['variants'] = []
    red = dry_run_patch(repo, 'name_variants', bad_variants, **args)
    assert red['status'] == 'ran' and red['errors']
    bad_causes = deepcopy(wires['cross_cause'])
    bad_causes['causes'] = []
    red = dry_run_patch(repo, 'cross_cause', bad_causes, **args)
    assert red['status'] == 'ran' and red['errors']
    unresolved = deepcopy(wires['name_variants'])
    unresolved['variants'][0].update(verdict='unresolved', char_id=None, surface=None)
    red = dry_run_patch(repo, 'name_variants', unresolved, **args)
    assert red['status'] == 'ran' and any('名称同一性未决' in e for e in red['errors'])
    assert transport.calls == calls, '免费预览不新增 wire 调用'
    assert before == {rel: repo.path(rel).read_bytes() for rel in paths}


def test_book_and_chapter_wire_v10_fact_previews_use_evidence_text_and_current_vocabulary(tmp_path):
    """真 producer 发布 book/chapters v10 后，免费补丁仍解析段全文与当前 f01 引用。"""
    from novel.stages.review import dry_run_patch
    chapters, wires = _plain_flow_case(with_facts=True)
    repo, _session, transport = _run_stage2(tmp_path, chapters, wires)
    args = _preview_args(chapters)
    calls = list(transport.calls)
    for owner in ('book', 'ch01', 'ch02'):
        green = dry_run_patch(repo, owner, deepcopy(wires[owner]), **args)
        assert green['status'] == 'ran' and not green['errors'] and not green['undecided'], green
    bad = deepcopy(wires['ch02'])
    bad['beats'][0]['know'][0]['fact'] = 'f99'
    red = dry_run_patch(repo, 'ch02', bad, **args)
    assert red['status'] == 'ran' and any('f99' in str(e) for e in red['errors']), red
    assert transport.calls == calls


@pytest.mark.parametrize('owner,missing', [
    ('name_variants', NAMES_REL), ('names', NAMES_REL),
    ('cross_cause', ASSOCIATE_REL)])
def test_narrow_free_preview_reports_missing_formal_upstream(tmp_path, owner, missing):
    """窄 owner 缺正式主回答时明确未运行；不能凭候选 wire 补猜主结构或身份。"""
    from novel.stages.review import dry_run_patch

    chapters, wires = _case()
    repo, _session, transport = _run_stage2(tmp_path, chapters, wires)
    calls = list(transport.calls)
    repo.path(missing).unlink()
    preview = dry_run_patch(repo, owner, deepcopy(wires[owner]), **_preview_args(chapters))
    assert preview['status'] == 'not_run' and '下游正式输入缺失' in preview['reason'], preview
    assert transport.calls == calls


def _changed_identity_case():
    """首章转写命题：distinct 只有 2→3，same 则恢复全部三个事实流拍对。"""
    chapters, current = _plain_flow_case(with_facts=True)
    know = current['ch01']['beats'][0]['know'][0]
    know.pop('fact')
    know['what'] = '夫君藏身于床底'
    current['associate']['same_proposition'] = [
        {'left': 'ch01.k01', 'right': 'f01', 'verdict': 'same'}]
    previous = deepcopy(current)
    previous['associate']['same_proposition'][0]['verdict'] = 'distinct'
    previous['cross_cause']['causes'] = [deepcopy(current['cross_cause']['causes'][-1])]
    return chapters, previous, current


def _tree_bytes(root):
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob('*') if path.is_file()}


def _prepare_changed_identity(tmp_path, *, missing_formal=False, check_preview=False):
    """购买两种身份下的真实回答；第二轮预览仍面对旧正式身份或缺失正式身份。"""
    from novel.llm.paid_requests import ask_json, digest
    from novel.llm.transport import ChatRequest
    from novel.stages.common import SYS, semantic_revision_rules
    from novel.stages.review import dry_run_patch

    chapters, previous, current = _changed_identity_case()
    repo, session, transport = _run_stage2(tmp_path, chapters, previous)
    old_formal = repo.path(ASSOCIATE_REL).read_bytes()
    if missing_formal:
        repo.path(ASSOCIATE_REL).unlink()
    transport.wires = current
    current_request = {}
    seen = []

    def request_for(owner, prompt, *, system=SYS, json_mode=True, **_kwargs):
        # 首身份 prompt 相同，用显式新采样身份购买 same 回答，保留 distinct 的账。
        return ChatRequest(
            model='fake', system=system, user=prompt, reasoning_effort='high',
            thinking=True, json_mode=json_mode, max_tokens=1000,
            sampling_nonce='a' * 64 if owner == 'associate' else None)

    def ask(owner, prompt, validate, patch=None, downstream_preview=None):
        request = request_for(owner, prompt)
        if owner == 'cross_cause':
            assert downstream_preview is not None
            current_request['identity'] = digest({
                'owner': owner, 'provider': session.provider, 'request': request.cache_payload()})
            if check_preview:
                disk = dry_run_patch(repo, owner, current[owner], **_preview_args(chapters))
                if missing_formal:
                    assert disk['status'] == 'not_run', disk
                else:
                    assert disk['status'] == 'ran' and disk['errors'], disk
                    assert any(e.code == 'association_unknown_candidate'
                               for e in disk['errors']), disk
                before, calls = _tree_bytes(repo.root), list(transport.calls)
                green = downstream_preview(deepcopy(current[owner]))
                red = downstream_preview(deepcopy(previous[owner]))
                assert green['status'] == 'ran' and not green['errors'] and not green['undecided'], green
                assert red['status'] == 'ran' and len(red['errors']) == 2, red
                assert all(e.code == 'association_missing_answer' for e in red['errors']), red
                assert _tree_bytes(repo.root) == before, '预览不落身份中间产物或审计记录'
                assert transport.calls == calls, '预览不发新请求'
                seen.append(green)
        return ask_json(session, owner, request, validate, patch=patch,
                        revision_rules=semantic_revision_rules(owner))

    run_workflow(repository=repo, chapters=chapters, hook_copy=HOOK,
                 ask=ask, ask_text=text_ask(session),
                 ask_body=lambda *args: pytest.fail('Stage2 不得购买正文'),
                 budget=800, through='stage2', node_filter=['str06.stage2.associate'])
    assert len(repo.read_model(ASSOCIATE_REL).cross_chapter_cause) == 3
    return (repo, session, transport, chapters, previous, current,
            old_formal, request_for, current_request['identity'], seen)


@pytest.mark.parametrize('missing_formal', [False, True])
def test_current_cause_preview_uses_this_identity_without_publishing_it(tmp_path, missing_formal):
    """本轮 identity 改变因果分母，旧盘面和无盘面都不能覆盖当前上下文预览。"""
    *_, seen = _prepare_changed_identity(
        tmp_path, missing_formal=missing_formal, check_preview=True)
    assert len(seen) == 1


@pytest.mark.parametrize('kind', ['owner_patch', 'resample'])
def test_execute_cause_recovery_records_current_context_preview(tmp_path, monkeypatch, kind):
    """execute 的采用与重采真走审计/发布；预览回执必须用当前 same 身份的三拍对。"""
    from types import SimpleNamespace

    from novel.config import Config
    from novel.stages import chain, revisions

    (repo, session, transport, chapters, previous, current, old_formal,
     request_for, identity, _seen) = _prepare_changed_identity(tmp_path)
    # 模拟上一轮正式产物仍在，当前主身份已购但尚未由本次节点发布。
    repo.path(ASSOCIATE_REL).write_bytes(old_formal)
    candidate = deepcopy(current['cross_cause'])
    candidate['causes'][0]['kind'] = 'motivates'
    transport.wires['cross_cause'] = candidate
    source = tmp_path / 'source.json'
    source.write_text(json.dumps({'chapters': chapters, 'hook_copy': HOOK},
                                 ensure_ascii=False), encoding='utf-8')
    wire_dir = None
    if kind == 'owner_patch':
        wire_dir = tmp_path / 'wires'
        wire_dir.mkdir()
        for owner, wire in (('associate', current['associate']), ('cross_cause', candidate)):
            (wire_dir / f'{owner}_wire.json').write_text(
                json.dumps(wire, ensure_ascii=False), encoding='utf-8')
        (wire_dir / 'cross_cause_patch_note.json').write_text(json.dumps({
            'base_response_identity': identity, 'reason': '确认首章事实为动机前提'}), encoding='utf-8')

    # 仅替换 CLI 的配置/设施装配；execute、真实请求账、采用凭证、节点、判据与写盘不打桩。
    monkeypatch.setattr(chain, 'load_config', lambda: Config(model='fake', base_url='fake'))
    monkeypatch.setattr(chain, 'assemble_paid_boundary', lambda **kwargs: SimpleNamespace(
        repository=repo, session=session, context=repo.context,
        profile=kwargs['profile'], build_request=request_for))
    preview_owner_patch = revisions.preview_owner_patch
    previews = []

    def record_preview(**kwargs):
        before, calls = _tree_bytes(repo.root), list(transport.calls)
        result = preview_owner_patch(**kwargs)
        assert _tree_bytes(repo.root) == before, '真实审计预览本身不落中间身份'
        assert transport.calls == calls
        assert result['downstream']['status'] == 'ran', result
        assert not result['downstream']['errors'] and not result['downstream']['undecided'], result
        previews.append(result)
        return result

    monkeypatch.setattr(revisions, 'preview_owner_patch', record_preview)
    before_calls = list(transport.calls)
    args = SimpleNamespace(
        book_sid=None, source_json=source, base=repo.root.parent, run_dir=repo.root,
        body_band_low=.6, body_band_high=1.3,
        check_only=False, yes=True, allow_source_export=True, adopt_root=[],
        wire_dir=wire_dir, repair_manifest=None, resample='cross_cause' if kind == 'resample' else None,
        reason='确认首章事实为动机前提' if kind == 'resample' else None,
        budget=800, node=['str06.stage2.associate', 'str06.stage2.compile'],
        through='stage2', body_chapters=None, book_title='', reasoning_effort='high')
    chain.execute(args)
    assert len(previews) == 1
    receipt = repo.inspect('runtime/revisions/previews/cross_cause.json')
    assert receipt['downstream'] == previews[0]['downstream']
    assert receipt['baseline_request_sha256'] == identity and not receipt['owner_errors']
    active = repo.inspect('runtime/revisions/active/cross_cause.json')
    assert active['kind'] == kind and active['status'] == 'resolved'
    final = repo.read_model(ASSOCIATE_REL)
    assert final.same_proposition[0].verdict == 'same'
    assert len(final.cross_chapter_cause) == 3
    assert final.cross_chapter_cause[0].kind == 'motivates'
    assert len(repo.read_model('stage2/blueprints/chapter_blueprints.json').chapters) == 3
    assert transport.calls == (before_calls if kind == 'owner_patch' else before_calls + ['cross_cause'])
