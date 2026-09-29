#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R3 自愈——typed issue 路由、有界修订与故障注入全链。

【覆盖面（PLAN.md R3 完成标准）】
  • 契约面：code 轴与 unbound.owner 轴双闭合（未知一律停，E-8）；
  • 同节点修订：`ask_json` 非空但不过闸 ⇒ 修订内容轮（≤2 轮、同问题集重复
    即停），修订轮入 ledger 与整书总闸；
  • 左移校验：人物闭集在章观察验收即查；associate 验收做 compile 干跑，
    未决项归它自己；
  • 跨节点回派：run_compile 的关联未决→associate；run_compile_stage4 的
    必保义务未承载→按 unbound.owner 回 body_design／preserve；无进展按 item 停；
  • 崩溃后恢复免费：同请求身份重放（首轮与修订轮都不重复购买）；
  • 调度面：open issue ⇒ resume_verdict 判 owner_revision REDO；普通队列对
    REDO 关闭式停止，AgentRun operator 经登记策略拿到空 flags。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from contracts.schema.workflow import (
    OWNER_ISSUE_CODES, UNBOUND_OWNER_ROUTES, OwnerIssueV1,
    owner_issues_rel, route_owner_issue,
)
from novel.llm.ledger import CostLedger, ledger_path, read_cost_snapshot
from novel.llm.paid_requests import PaidRequestSession, ask_json
from novel.llm.transport import ChatRequest, ChatResponse
from novel.stages.stage4 import obligation_core_id
from novel.stages.owner_revision import (
    OwnerRevisionRequired, OwnerRevisionStop, discover_owner_issues,
    owner_product_digest, read_owner_issue_file, resolve_owner_issues,
)
from novel.store import Str06Repository
from tests.str06_facilities import make_context, synthetic_ref
from tests.str06_faults import FAULTS, FaultTransport, looped_full_chain_wires


# ═══════════════ 一、契约面：路由闭集（unit_contract） ═══════════════

def test_route_table_closes_code_and_owner_axes():
    """code 轴未知一律停；必保义务按 unbound.owner 闭集回派，未映射即停（E-8）。"""
    for code in OWNER_ISSUE_CODES:
        action, _target = (route_owner_issue(code) if code != 'must_keep_unbound'
                           else route_owner_issue(code, unbound_owner='design'))
        assert action in ('revise_self', 'revise_upstream'), code
    action, why = route_owner_issue('literary_quality')
    assert action == 'stop' and '未知问题码' in why
    assert '文学' not in OWNER_ISSUE_CODES, '文学质量永不进入自动修复（裁决 1）'
    assert route_owner_issue('association_undecided') == (
        'revise_upstream', 'str06.stage2.associate')
    assert route_owner_issue('must_keep_unbound', unbound_owner='grounding') == (
        'revise_upstream', 'str06.stage4.preserve')
    action, why = route_owner_issue('must_keep_unbound', unbound_owner='body_design')
    assert action == 'stop' and '未映射' in why
    assert set(UNBOUND_OWNER_ROUTES) == {'design', 'grounding'}


def test_issue_record_write_bounds_and_resolve(tmp_path):
    """回派账：首见起轮、同指纹无进展停、额度耗尽停给人、复验清账不复位。"""
    repo = _bare_repo(tmp_path)
    _put_owner_product(repo, 'associate', b'{"pairing": "v1"}')
    issues = [OwnerIssueV1(code='association_undecided', item='',
                           message='loop q_yu 缺配对')]
    with pytest.raises(OwnerRevisionRequired) as first:
        discover_owner_issues(repo, raised_by='str06.stage2.compile',
                              owner='associate', node='str06.stage2.associate',
                              issues=issues)
    assert first.value.node == 'str06.stage2.associate'
    record = read_owner_issue_file(repo, 'associate')
    assert record.status == 'open' and record.rounds_total == 1
    assert record.against_sha256 == _digest(b'{"pairing": "v1"}')

    # 同问题集原样重现：无进展，按 item 停（轮数不涨）。
    with pytest.raises(OwnerRevisionStop, match='没有进展'):
        discover_owner_issues(repo, raised_by='str06.stage2.compile',
                              owner='associate', node='str06.stage2.associate',
                              issues=issues)
    assert read_owner_issue_file(repo, 'associate').rounds_total == 1

    # 不同问题集：第二轮放行；再起第三轮即额度耗尽。
    other = [OwnerIssueV1(code='association_undecided', item='',
                          message='scene_continuation 缺右端')]
    with pytest.raises(OwnerRevisionRequired):
        discover_owner_issues(repo, raised_by='str06.stage2.compile',
                              owner='associate', node='str06.stage2.associate',
                              issues=other)
    assert read_owner_issue_file(repo, 'associate').rounds_total == 2
    third = [OwnerIssueV1(code='association_undecided', item='',
                          message='又一个不同的问题')]
    with pytest.raises(OwnerRevisionStop, match='额度'):
        discover_owner_issues(repo, raised_by='str06.stage2.compile',
                              owner='associate', node='str06.stage2.associate',
                              issues=third)

    # 复验清账：resolved 留轮数（终身额度不复位），against 摘要随账留痕。
    resolve_owner_issues(repo, owner='associate', raised_by='str06.stage2.compile')
    resolved = read_owner_issue_file(repo, 'associate')
    assert resolved.status == 'resolved' and resolved.rounds_total == 2
    assert resolved.against_sha256 == _digest(b'{"pairing": "v1"}')
    with pytest.raises(OwnerRevisionStop, match='额度'):
        discover_owner_issues(repo, raised_by='str06.stage2.compile',
                              owner='associate', node='str06.stage2.associate',
                              issues=[OwnerIssueV1(code='association_undecided',
                                                   item='', message='新回合也吃额度')])


def test_issue_record_v1_migration_reads_current_digest(tmp_path):
    """v1 盘上账（R8a 之前）读侧升 v2：against 补 owner 产物当前摘要（集中迁移）。"""
    import json as _json

    repo = _bare_repo(tmp_path)
    _put_owner_product(repo, 'design', b'{"scenes": []}')
    legacy_payload = {
        'schema_version': 1, 'owner': 'design',
        'node': 'str06.stage4.body_design', 'raised_by': 'str06.stage4.compile',
        'status': 'open', 'rounds_total': 1,
        'fingerprint': 'f' * 64,
        'issues': [{'code': 'must_keep_unbound', 'item': 'ob01',
                    'message': '必保义务 ob01 尚未承载'}],
        'updated_at': 100,
    }
    # v1 记录是 R8a 前的裸盘面：直接按字节落文件，不走 publish 封存。
    issue_path = repo.path(owner_issues_rel('design'))
    issue_path.parent.mkdir(parents=True, exist_ok=True)
    issue_path.write_text(_json.dumps(legacy_payload), encoding='utf-8')
    migrated = read_owner_issue_file(repo, 'design')
    assert migrated.against_sha256 == _digest(b'{"scenes": []}')
    assert migrated.rounds_total == 1 and migrated.status == 'open'
    # 未知版本号显式拒绝，不静默当任何一种语义。
    issue_path.write_text(_json.dumps(dict(legacy_payload, schema_version=7)),
                          encoding='utf-8')
    with pytest.raises(ValueError, match='schema_version'):
        read_owner_issue_file(repo, 'design')
    # owner 产物闭集外的 digest 请求显式拒绝。
    from novel.stages.owner_revision import owner_product_digest_at
    with pytest.raises(ValueError, match='未登记正式产物'):
        owner_product_digest_at(repo.root, 'nobody')


# ═══════════════ 二、同节点修订：ask_json（unit_contract＋付费边界） ═══════════════

class _ScriptedTransport:
    """按脚本逐响的假 transport（ask_json 直连用例）；耗尽即 AssertionError。"""

    def __init__(self, contents: list[str]):
        self.contents = list(contents)
        self.calls: list[ChatRequest] = []

    def send(self, request: ChatRequest) -> ChatResponse:
        self.calls.append(request)
        return ChatResponse(content=self.contents.pop(0), finish_reason='stop',
                            usage={'prompt_tokens': 10, 'completion_tokens': 5},
                            model='fake')


def _json_session(tmp_path, transport, name='out'):
    base = tmp_path / name
    base.mkdir(exist_ok=True)
    repo = Str06Repository(base / 'run')
    ledger = CostLedger(path=ledger_path(base))
    repo.attach_context(make_context(base / 'run', ledger=ledger))
    session = PaidRequestSession(repo, ledger, transport, yes=True,
                                 allow_source_export=True, provider='fake',
                                 sid='run')
    return base, repo, session


def _request(user='u'):
    return ChatRequest(model='fake', system='s', user=user, reasoning_effort='high',
                       thinking=True, json_mode=True, max_tokens=99, temperature=0.3)


def test_ask_json_revises_nonconforming_answer_within_budget(tmp_path):
    """非空但不过闸 ⇒ 修订内容轮（原请求＋上一版＋typed 问题）；一次修订过闸。"""
    transport = _ScriptedTransport(['{"ok": false}', '{"ok": true}'])
    base, _repo, session = _json_session(tmp_path, transport)
    data, answer = ask_json(session, 'bind', _request(),
                            lambda w: [] if w.get('ok') else ['闭集外句柄 e99_99'])
    assert data == {'ok': True} and not answer.cached
    assert len(transport.calls) == 2, '一原轮＋一修订轮'
    assert '【修订要求】' in transport.calls[1].user
    assert '闭集外句柄 e99_99' in transport.calls[1].user
    assert '{"ok": false}' in transport.calls[1].user, '修订轮携带上一版全文'
    assert transport.calls[1].model == transport.calls[0].model
    # 修订轮是新请求身份的付费调用：ledger 两行实付、整书总闸计 2（I-17/I-43）。
    rows, _partial = read_cost_snapshot(base)
    paid = [r for r in rows if not r['cached']]
    assert len(paid) == 2 and all(r['purpose'] == 'bind' for r in paid)
    from novel.work_units import read_book_budget
    assert read_book_budget(base, 'run').used == 2


def test_ask_json_stops_when_issue_set_repeats(tmp_path):
    """同一问题集重复即停：一次修订后再原样失败 ⇒ 停（不无限付费）。"""
    transport = _ScriptedTransport(['{"ok": false}', '{"ok": false}'])
    base, _repo, session = _json_session(tmp_path, transport)
    with pytest.raises(ValueError, match='同一问题集重复即停') as excinfo:
        ask_json(session, 'bind', _request(),
                 lambda w: [] if w.get('ok') else ['闭集外句柄 e99_99'])
    assert '闭集外句柄 e99_99' in str(excinfo.value), '停止消息携带 typed 问题'
    assert len(transport.calls) == 2, '原轮＋一次修订轮后即停'


def test_ask_json_stops_after_round_cap_with_changing_issue_sets(tmp_path):
    """问题集在变但修订轮（2）耗尽 ⇒ 停；上限对「有进展但没修完」同样成立。"""
    transport = _ScriptedTransport(
        ['{"v": 1}', '{"v": 2}', '{"v": 3}'])
    base, _repo, session = _json_session(tmp_path, transport)
    with pytest.raises(ValueError, match='节点内校验重问轮（≤2）已耗尽'):
        ask_json(session, 'bind', _request(),
                 lambda w: [f'问题第{w["v"]}类'] if w.get('v') != 4 else [])
    assert len(transport.calls) == 3, '原轮＋两轮修订即到上限'


def test_recovery_after_revision_stop_replays_free(tmp_path):
    """崩溃后恢复免费：停止后的重跑按请求身份重放（含修订轮），不再购买。"""
    transport = _ScriptedTransport(['{"ok": false}', '{"ok": false}'])
    base, repo, session = _json_session(tmp_path, transport)
    with pytest.raises(ValueError):
        ask_json(session, 'bind', _request(),
                 lambda w: [] if w.get('ok') else ['闭集外句柄 e99_99'])
    first_paid = len([r for r in read_cost_snapshot(base)[0] if not r['cached']])

    replay_transport = _ScriptedTransport([])      # 任何新外发都会 IndexError
    _session2 = PaidRequestSession(
        repo, CostLedger(path=ledger_path(base)), replay_transport, yes=True,
        allow_source_export=True, provider='fake', sid='run')
    with pytest.raises(ValueError, match='同一问题集重复即停'):
        ask_json(_session2, 'bind', _request(),
                 lambda w: [] if w.get('ok') else ['闭集外句柄 e99_99'])
    assert replay_transport.calls == [], '恢复重放零外发'
    rows, _partial = read_cost_snapshot(base)
    assert len([r for r in rows if not r['cached']]) == first_paid, '不重复购买'
    assert all(r['cached'] for r in rows[first_paid:])


# ═══════════════ 三、左移校验（stage） ═══════════════

def _chain_runner(tmp_path, monkeypatch, fault, name, fault_once=False):
    """用（可选故障化的）wires 走真实链入口；返回 (runner, repo, transport)。"""
    from contracts.schema.hook import HookConstraints
    from novel.stages.chain import run_workflow
    from tests.str06_facilities import json_ask, text_ask, stub_body_runner
    from tests.test_str06_body import _chapters, _make_session

    wires = looped_full_chain_wires()
    repo, session, transport = _make_session(
        tmp_path, wires, name=name, transport=FaultTransport(wires, fault, fault_once))

    def ask_body(owner, prompt):
        raise AssertionError('自愈用例不在正文 owner 注入故障（正文 runner 已 stub）')

    stub_body_runner(monkeypatch)

    def runner():
        return run_workflow(
            repository=repo, chapters=_chapters(), ask=json_ask(session),
            ask_text=text_ask(session), ask_body=ask_body, budget=2000,
            through='stage7', book_title='报恩',
            hook_constraints=HookConstraints(target_chars=10, hard_max_chars=360))
    return runner, repo, transport


def test_unknown_slug_is_caught_at_chapter_acceptance(tmp_path, monkeypatch):
    """左移：participants 人物闭集在章观察验收即查——不再攒到 compile。"""
    runner, repo, _transport = _chain_runner(tmp_path, monkeypatch,
                                             'unknown_slug', 'slug')
    with pytest.raises(ValueError) as excinfo:
        runner()
    message = str(excinfo.value)
    assert 'ch01 校验失败' in message and 'participants 引用未知人物 ghost' in message
    assert repo.path('stage2/observations/book.json').is_file()
    assert not repo.path('stage2/observations/ch01.json').exists(), \
        '章观察验收失败不落盘半份产物'


def test_missing_pairing_is_caught_at_associate_dry_run(tmp_path, monkeypatch):
    """左移：associate 验收做 compile 干跑，未决项归它自己（同节点修订后停）。"""
    runner, repo, transport = _chain_runner(tmp_path, monkeypatch,
                                            'missing_pairing', 'pairing')
    with pytest.raises(ValueError) as excinfo:
        runner()
    message = str(excinfo.value)
    assert 'associate 校验失败' in message and '关联未决' in message and 'q_yu' in message
    assert not repo.path('stage2/observations/associate.json').exists()
    # 同节点修订轮真实发生：一次原轮＋一次修订轮（修订仍缺配对 ⇒ 同问题集停）。
    assert transport.calls.count('associate') == 2


# ═══════════════ 四、全链靠修订走完（stage；R3 完成标准） ═══════════════

@pytest.mark.parametrize('fault', FAULTS)
def test_faulted_full_chain_completes_via_revision(tmp_path, monkeypatch, fault):
    """五类故障经修订自愈：fault_once（修订轮「改对了」）后全链照常交付面。"""
    runner, repo, transport = _chain_runner(tmp_path, monkeypatch, fault,
                                            f'once-{fault}', fault_once=True)
    report = runner()
    assert report['assembly']['status'] == 'not_ready'   # 正文 runner 已 stub
    assert repo.path('stage2/characters/characters.json').is_file()
    assert repo.path('stage4/ir/writer_book_ir.json').is_file()
    assert repo.path('stage4/compiled/execution_plan.json').is_file()
    # 修订轮入账：受影响 owner 恰好一原轮＋一修订轮（同节点），全链无重复购买。
    base = repo.root.parent
    rows, _partial = read_cost_snapshot(base)
    paid = [r for r in rows if not r['cached']]
    faulty_owner = {'evidence_out_of_range': 'book', 'unknown_slug': 'ch01',
                    'missing_pairing': 'associate', 'obligation_unbound': 'bind',
                    'duplicate_json_key': 'book'}[fault]
    assert sum(1 for r in paid if r['purpose'] == faulty_owner) == 2, \
        '原轮＋修订轮各一行实付'
    from novel.work_units import read_book_budget
    assert read_book_budget(base, 'run').used == len(paid), '总闸按 attempt 合账'
    if fault == 'obligation_unbound':
        record = read_owner_issue_file(repo, 'design')
        assert record.status == 'resolved' and record.rounds_total == 1
        assert record.raised_by == 'str06.stage4.compile'
        assert transport.calls.count('design') == 2, '回派后 body_design 修订重跑'


# ═══════════════ 五、跨节点回派（stage） ═══════════════

def test_no_progress_backfill_stops_per_item_without_repaying_bind(tmp_path,
                                                                   monkeypatch):
    """粘性故障：design 修订后 bind 原样复现 ⇒ 同问题集按 item 停；上游语义
    未变的 bind 不重新购买（缓存重放），额度只记一轮。"""
    from tests.test_str06_body import _chapters, _make_session
    from tests.str06_facilities import json_ask, text_ask, stub_body_runner
    from novel.stages.chain import run_workflow
    from contracts.schema.hook import HookConstraints

    wires = looped_full_chain_wires()
    repo, session, transport = _make_session(
        tmp_path, wires, name='stuck',
        transport=FaultTransport(wires, 'obligation_unbound'))

    def ask_body(owner, prompt):
        raise AssertionError('本用例正文 runner 已 stub')

    stub_body_runner(monkeypatch)
    with pytest.raises(OwnerRevisionStop, match='没有进展') as excinfo:
        run_workflow(repository=repo, chapters=_chapters(), ask=json_ask(session),
                     ask_text=text_ask(session), ask_body=ask_body, budget=2000,
                     through='stage7', book_title='报恩',
                     hook_constraints=HookConstraints(target_chars=10,
                                                      hard_max_chars=360))
    assert f'必保义务 {obligation_core_id("ob01")}' in str(excinfo.value)
    record = read_owner_issue_file(repo, 'design')
    assert record.status == 'open' and record.rounds_total == 1
    assert record.node == 'str06.stage4.body_design'
    # R8a-3：定位与消息都是 Design 可见的源盲 core id，revision prompt 里
    # 不出现 ob 序号；context 带 typed 的依赖与承载事实。
    assert record.issues[0].item == obligation_core_id('ob01')
    assert 'ob01' not in record.issues[0].message
    assert record.issues[0].context['core_id'] == obligation_core_id('ob01')
    assert record.issues[0].context['unbound_owner'] == 'design'
    assert 'summary' in record.issues[0].context
    assert repo.path('stage4/grounding/semantic_binding.json').is_file()
    assert not repo.path('stage4/compiled/execution_plan.json').exists()
    # 回派真实发生（design 原轮＋修订轮），但 bind 只有一次外发——design 语义
    # 未变，bind 的请求身份没变，缓存重放旧答案，不重复付费。
    assert transport.calls.count('design') == 2
    assert transport.calls.count('bind') == 1


def test_compile_backfills_associate_and_revision_heals(tmp_path):
    """run_compile 的关联未决 → associate：写 issues 记录并回派；修订重跑后
    复验通过、记录清账、canonical 发布（直接构造盘面模拟 owner patch 后态）。"""
    from tests.test_str06_body import _chapters, _make_session
    from tests.str06_facilities import json_ask, text_ask
    from novel.stages.chain import ChainContext, run_node
    from novel.stages.stage2 import (
        compile_associate_observation, project_association_space,
    )
    from tests.str06_faults import apply_fault

    wires = looped_full_chain_wires()
    repo, session, _transport = _make_session(tmp_path, wires, name='backfill')
    # 前三节点（book／chapters／names）用干净 wires 跑真链，落正式观察。
    from novel.stages.chain import run_workflow
    run_workflow(repository=repo, chapters=_chapters(), ask=json_ask(session),
                 ask_text=text_ask(session),
                 ask_body=lambda owner, prompt: None, budget=2000,
                 through='stage2', node_filter=(
                     'str06.stage2.book', 'str06.stage2.chapters',
                     'str06.stage2.names'))
    # 直接构造「漏配对」的 associate 观察上盘（模拟 owner patch／崩溃窗口后的
    # 盘面：观察已发布但其未决只在 compile 暴露——associate 干跑之外的路径）。
    book = repo.read_model('stage2/observations/book.json')
    chapters = {c['chapter_ordinal']: repo.read_model(
        f'stage2/observations/ch{c["chapter_ordinal"]:02d}.json') for c in _chapters()}
    space = project_association_space(book, chapters)
    faulty_wire = apply_fault(wires, 'missing_pairing')['associate']
    observation, errors = compile_associate_observation(faulty_wire, space)
    assert not errors, '漏配对是合法的 associate wire（缺项保持未决，D8）'
    repo.write('stage2/observations/associate.json', observation,
               owners=('associate',), refs=(synthetic_ref('associate'),))

    ctx = ChainContext(repository=repo, ask=json_ask(session),
                       ask_text=text_ask(session), ask_body=lambda o, p: None,
                       chapters=_chapters(), budget=2000, provider='fake',
                       model='fake')
    with pytest.raises(OwnerRevisionRequired) as excinfo:
        run_node('str06.stage2.compile', ctx)
    assert excinfo.value.node == 'str06.stage2.associate'
    record = read_owner_issue_file(repo, 'associate')
    assert record.status == 'open' and record.rounds_total == 1
    assert record.raised_by == 'str06.stage2.compile'
    assert all(issue.code == 'association_undecided' for issue in record.issues)
    assert not repo.path('stage2/characters/characters.json').exists(), \
        '未决即拒绝发布 canonical（D8）'

    # 修订重跑：associate 带 open 记录重跑（修订 prompt＋干净配对 wire）→
    # compile 复验通过 → 记录清账、双真源发布。
    run_workflow(repository=repo, chapters=_chapters(), ask=json_ask(session),
                 ask_text=text_ask(session),
                 ask_body=lambda owner, prompt: None, budget=2000,
                 through='stage2', node_filter=('str06.stage2.associate',
                                                'str06.stage2.compile'))
    assert repo.path('stage2/characters/characters.json').is_file()
    healed = read_owner_issue_file(repo, 'associate')
    assert healed.status == 'resolved' and healed.rounds_total == 1


# ═══════════════ 六、调度面：resume_verdict／operator／队列（stage） ═══════════════

def _stuck_overview(tmp_path, monkeypatch):
    """跑一条粘性 obligation_unbound 链到停止，返回 (repo, overview)。"""
    from novel import prov as _prov
    from novel.orchestrator import state as S

    _runner, repo, _transport = _chain_runner(
        tmp_path, monkeypatch, 'obligation_unbound', 'verdict')
    with pytest.raises(OwnerRevisionStop):
        _runner()
    ov = S.overview_at(repo.root, _prov.code_version())
    return repo, ov


def test_resume_verdict_marks_owner_revision_redo_before_freshness(tmp_path,
                                                                   monkeypatch):
    """open issue ⇒ 上游判修订重跑（即使其产物新鲜）；同指纹无进展（R8a）
    改判停给人；复验 resolved 后回到常规判定。"""
    from novel.orchestrator import state as S

    _repo, ov = _stuck_overview(tmp_path, monkeypatch)
    entry = next(i for i in ov.owner_issues if i.node == 'str06.stage4.body_design')
    assert entry.owner == 'design' and entry.rounds_total == 1
    assert 'must_keep_unbound' in entry.codes
    # 粘性故障链停止时 against 已刷新到「最后一次判定的产物摘要」；同指纹
    # 无进展（no_progress）⇒ 修订被证明无效，停给人，不再自动重买（R8a）。
    assert entry.product_sha256 == entry.against_sha256
    assert entry.no_progress is True
    verdict, why = S.resume_verdict(ov, 'str06.stage4.body_design')
    assert verdict == S.RESUME_UNKNOWN and 'no_progress' in why
    # 无 open issue 指向的节点不受 owner_revision 影响：bind 走常规新鲜度判定
    # （测试仓库的候选桥新鲜度面是 unknown——那不是本批语义，只钉「不背
    # owner_revision 的锅」）。
    verdict_bind, why_bind = S.resume_verdict(ov, 'str06.stage4.bind')
    assert 'owner_revision' not in why_bind, why_bind

    # resolved 后：投影不再携带该条，判定回到常规面。
    record = read_owner_issue_file(_repo, 'design')
    from contracts.schema.workflow import OwnerIssueFileV2
    _repo.write(owner_issues_rel('design'), OwnerIssueFileV2(
        owner=record.owner, node=record.node, raised_by=record.raised_by,
        status='resolved', rounds_total=record.rounds_total,
        fingerprint=record.fingerprint, against_sha256=record.against_sha256,
        no_progress=record.no_progress, issues=record.issues,
        updated_at=record.updated_at))
    ov2 = S.overview_at(_repo.root, _prov_code())
    assert all(i.node != 'str06.stage4.body_design' for i in ov2.owner_issues)


def test_resume_verdict_owner_revision_digest_gate(tmp_path, monkeypatch):
    """R8a 门控三分支：产物未变⇒REDO；产物已变⇒不再 owner 重买（等下游复验）；
    轮额度耗尽且产物未变⇒停给人（unknown）。"""
    from novel.orchestrator import state as S

    repo, ov = _stuck_overview(tmp_path, monkeypatch)
    assert 'str06.stage4.body_design' in {
        i.node for i in ov.owner_issues}

    # ① owner 修订写回（产物字节变化）：门控不再判 owner_revision——不回头
    # 重买 owner，等下游按新鲜度复验清账。
    design_path = repo.path('stage4/design/body_design.json')
    design_path.write_bytes(design_path.read_bytes() + b'\n')
    ov_revised = S.overview_at(repo.root, _prov_code())
    entry = next(i for i in ov_revised.owner_issues
                 if i.node == 'str06.stage4.body_design')
    assert entry.product_sha256 != entry.against_sha256
    verdict, why = S.resume_verdict(ov_revised, 'str06.stage4.body_design')
    assert 'owner_revision' not in why, why

    # ② 轮额度耗尽且产物未变：unknown 停给人，不自动重买。
    record = read_owner_issue_file(repo, 'design')
    from contracts.invariants import OWNER_REVISION_MAX_ROUNDS
    from contracts.schema.workflow import OwnerIssueFileV2
    repo.write(owner_issues_rel('design'), OwnerIssueFileV2(
        owner=record.owner, node=record.node, raised_by=record.raised_by,
        status='open', rounds_total=OWNER_REVISION_MAX_ROUNDS,
        fingerprint=record.fingerprint,
        against_sha256=owner_product_digest(repo, 'design'),
        issues=record.issues, updated_at=record.updated_at))
    ov_spent = S.overview_at(repo.root, _prov_code())
    verdict, why = S.resume_verdict(ov_spent, 'str06.stage4.body_design')
    assert verdict == S.RESUME_UNKNOWN and '耗尽' in why
    # operator 的 owner_revision 自动重放通道随之关闭（unknown 不发 flags）。
    assert S.operator_redo_flags(ov_spent, 'str06.stage4.body_design') is None


def test_operator_gets_empty_flags_and_durable_queue_stays_closed(tmp_path,
                                                                  monkeypatch):
    """AgentRun 单步 operator 经登记的 owner_revision 策略拿到空 flags；普通
    durable 队列对同一 REDO 关闭式停止（R-14.4 不放宽）。"""
    from novel.orchestrator import queue as Q
    from novel.orchestrator import state as S

    _repo, ov = _stuck_overview(tmp_path, monkeypatch)
    # no_progress（R8a）：修订被证明无效——owner 的自动重放通道关闭，停给人。
    assert S.operator_redo_flags(ov, 'str06.stage4.body_design') is None
    # 未登记 owner_revision 的节点即使 REDO 也拿不到自动重放（不猜 flags）。
    assert S.operator_redo_flags(ov, 'str06.stage4.bind') is None
    todo, blocked = Q.plan_book_from_overview(
        _repo.root.parent, _repo.root.name, ov, until_stage='stage4',
        follow_check=False)
    item = next(i for i in blocked if i.node == 'str06.stage4.body_design')
    assert item.waiting_on == Q.WAITING_ON_RESUME_DECISION and item.steps == []


def _prov_code() -> str:
    from novel import prov

    return prov.code_version()


def _bare_repo(tmp_path) -> Str06Repository:
    base = tmp_path / 'bare'
    base.mkdir(exist_ok=True)
    repo = Str06Repository(base / 'run')
    repo.attach_context(make_context(base / 'run', ledger=CostLedger(path=ledger_path(base))))
    return repo


def _digest(payload: bytes) -> str:
    import hashlib

    return hashlib.sha256(payload).hexdigest()


def _put_owner_product(repo: Str06Repository, owner: str, payload: bytes) -> None:
    """把 owner 正式产物按裸字节落盘（R8a 门控只看字节摘要，形状与本测试无关）。"""
    from contracts.schema.workflow import OWNER_PRODUCT_RELS

    path = repo.path(OWNER_PRODUCT_RELS[owner])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def test_revision_gate_is_the_single_owner_revision_verdict():
    """R8a-7：门控唯一判定的三分支（进程内回跳与 resume_verdict 共用一把尺）。"""
    from novel.stages.owner_revision import revision_gate

    against = 'a' * 64
    verdict, why = revision_gate(against, against, rounds_total=1)
    assert verdict == 'redo' and '产物未变' in why
    verdict, why = revision_gate(against, against, rounds_total=2)
    assert verdict == 'stopped' and '耗尽' in why
    verdict, why = revision_gate(against, 'b' * 64, rounds_total=1)
    assert verdict == 'wait_reverify' and '复验' in why
    # 产物不在盘上：交常规存在性分支，不自动重买。
    verdict, _why = revision_gate(against, None, rounds_total=1)
    assert verdict == 'wait_reverify'


def test_revision_gate_no_progress_stops_for_human():
    """R8a：同指纹重现（修订被证明无效）⇒ stopped——复验会刷新 against，
    光看轮数会在「1/2＋产物未变」上打转成付费循环（产品链实测）。"""
    from novel.stages.owner_revision import revision_gate

    against = 'a' * 64
    verdict, why = revision_gate(against, against, rounds_total=1,
                                 no_progress=True)
    assert verdict == 'stopped' and 'no_progress' in why
    # 无进展标记缺省时维持原判（轮未耗尽 ⇒ redo）。
    verdict, _ = revision_gate(against, against, rounds_total=1)
    assert verdict == 'redo'
