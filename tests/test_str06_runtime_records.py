#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_runtime_records —— B1 执行设施：空回答反例与五层记录（SemanticRequest /
ContentRevision / ProviderAttempt / AcceptanceRecord / CommitBundle）。

【回归反例（先红后绿，D5）】
① 现役缺陷：章首个回答为空时 ``write_chapter`` 第二轮取 ``candidates[-1]`` 抛
   ``IndexError``，重跑必然复现；
② 现役缺陷：空正文以 ``answered`` 落盘后占据请求身份，重跑免费读回同一空回答，
   同一节点永远无法恢复；
③ D5：JSON 调用空回答先免费从 reasoning 救回（整段过同一套解析与校验才采纳），
   救不回再在同一内容轮内付费重试（占尝试额度，如实计费）。

【五层记录】原响应按 attempt 存储并被 attempt 引用，空回答不占据请求身份；
AcceptanceRecord 取代逐请求 validation 文件；CommitBundle 封存输入清单、采用
证据、输出摘要与 seal，采用状态从 seal 派生；整书总闸按 attempt 计数、
unknown 计入、重启不重置。B0–B5 全部 fake transport、零模型调用。
"""
from __future__ import annotations

import json

import pytest

from contracts.schema.hook import HookConstraints
from contracts.schema.workflow import CommitBundleV1
from novel.llm.ledger import CostLedger, ledger_path, read_cost_snapshot
from novel.llm.paid_requests import (
    PaidRequestSession, ask_json, commit_bundles, sealed_turn_references,
)
from novel.llm.retry import TransportRetryPolicy
from novel.llm.transport import ChatRequest, ChatResponse
from novel.store import Str06Repository
from novel.stages.stage5.body import AnsweredText, write_chapter
from tests.str06_facilities import make_context
from tests.test_str06_body import _answered, _packet_kw


# ─── 反例①：章首个回答为空，第二轮不得取 candidates[-1] 崩溃 ────────────────────

def test_empty_first_body_answer_resends_packet_instead_of_indexerror():
    """首个回答为空 → 同一请求重发（同内容轮下一 attempt），不再 IndexError。"""
    good = '洞房之夜，苏芸娘望着夫君，把旧约问了个明白。' * 3

    class EmptyThenGood:
        def __init__(self):
            self.prompts: list[str] = []

        def __call__(self, prompt):
            self.prompts.append(prompt)
            if len(self.prompts) == 1:
                return _answered(None)
            return _answered(good, identity='id-2')

    fake = EmptyThenGood()
    outcome = write_chapter(**_packet_kw(), ask=fake)
    assert outcome.prose == good
    assert outcome.request_identity == 'id-2'
    # 空回答不携带上一稿：第二轮重发同一份章包（同请求身份 → 同轮重试）。
    assert fake.prompts[0] == fake.prompts[1]
    assert len(outcome.attempts) == 2
    assert outcome.attempts[0].errors[-1].startswith('[non_empty]')


# ─── 反例②：空回答不得占据请求身份（重跑必须能恢复，不得读回同一空回答） ────────

class _EmptyThenGoodTransport:
    """第 1 次返回空 content（答案流丢失形态），第 2 次起正常回答。"""

    def __init__(self, payload='{"entries": []}'):
        self.payload = payload
        self.calls = 0

    def send(self, request):
        self.calls += 1
        if self.calls == 1:
            return ChatResponse(content='', reasoning='想了很多，但答案没流出来',
                                finish_reason='stop',
                                usage={'prompt_tokens': 7, 'completion_tokens': 3},
                                model='fake')
        return ChatResponse(content=self.payload, finish_reason='stop',
                            usage={'prompt_tokens': 7, 'completion_tokens': 20},
                            model='fake')


def _make_session(tmp_path, transport, *, yes=True, sid='book'):
    base = tmp_path / 'out'
    base.mkdir(exist_ok=True)
    repo = Str06Repository(base / 'run')
    ledger = CostLedger(path=ledger_path(base))
    repo.attach_context(make_context(base / 'run', ledger=ledger))
    session = PaidRequestSession(repo, ledger, transport, yes=yes,
                                 allow_source_export=yes, provider='fake', sid=sid)
    return base, repo, ledger, session


def _request(**kw):
    base = dict(model='fake', system='系统', user='用户', reasoning_effort='high',
                thinking=True, json_mode=True, max_tokens=100)
    base.update(kw)
    return ChatRequest(**base)


def test_empty_answer_does_not_occupy_request_identity(tmp_path):
    transport = _EmptyThenGoodTransport()
    base, repo, ledger, session = _make_session(tmp_path, transport)
    # 第一次：空回答计一次失败 attempt，同轮内付费重试后成功。
    data, ref = ask_json(session, 'bind', _request(), lambda _: [])
    assert data == {'entries': []}
    assert transport.calls == 2, '空回答必须在同一内容轮内重试，不得把空壳当已购结果'
    # 重跑（新 session、同一请求身份）：免费采用成功的回答，不得读回空壳。
    replay = PaidRequestSession(repo, CostLedger(path=ledger_path(base)), transport,
                                provider='fake', sid='book')
    data2, ref2 = ask_json(replay, 'bind', _request(), lambda _: [])
    assert data2 == {'entries': []}
    assert transport.calls == 2, '重跑免费采用，不得因空回答历史而重复购买'
    assert ref2.attempt_id == ref.attempt_id, '引用必须指向成功的那次 attempt'


def test_empty_answer_retries_within_one_content_turn_and_counts_attempts(tmp_path):
    transport = _EmptyThenGoodTransport()
    base, repo, ledger, session = _make_session(tmp_path, transport)
    ask_json(session, 'bind', _request(), lambda _: [])
    from novel.work_units import WorkUnitBudget
    unit = WorkUnitBudget(base, 'book', 'str06.stage4.bind', 'executor', 'book')
    turns = unit._read().turns
    assert len(turns) == 1, '空回答重试属于同一内容轮（ContentRevision），不得另取轮'
    attempts = turns[0].attempts
    assert len(attempts) == 2, '一次外发一次 ProviderAttempt'
    assert [a.outcome for a in attempts] == ['failed', 'ok']
    # 原响应按 attempt 存储并被 attempt 引用；失败 attempt 的空回答也留证。
    assert attempts[0].response_sha256 and attempts[1].response_sha256
    assert attempts[0].response_sha256 != attempts[1].response_sha256
    # 整书总闸按 attempt 计数（外发前计一次，含失败 attempt）。
    from novel.work_units import read_book_budget
    assert read_book_budget(base, 'book').used == 2
    rows, partial = read_cost_snapshot(base)
    assert not partial and len(rows) == 2
    assert [r['attempt'] for r in rows] == [1, 2]
    assert rows[0]['outcome'] == 'error' and rows[1]['outcome'] == 'ok'
    assert sum(r['completion_tokens'] for r in rows) == 23, '失败 attempt 如实计费'


def test_json_empty_answer_is_salvaged_from_reasoning_free(tmp_path):
    """D5：JSON 空回答先免费从 reasoning 救回（整段过同一套解析与校验）。"""
    salvaged = json.dumps({'entries': []}, ensure_ascii=False)

    class ReasoningOnly:
        def __init__(self):
            self.calls = 0

        def send(self, request):
            self.calls += 1
            return ChatResponse(content='', reasoning=salvaged, finish_reason='stop',
                                usage={'prompt_tokens': 7, 'completion_tokens': 3},
                                model='fake')

    transport = ReasoningOnly()
    base, repo, ledger, session = _make_session(tmp_path, transport)
    data, ref = ask_json(session, 'bind', _request(), lambda _: [])
    assert data == {'entries': []}
    assert transport.calls == 1, '救回成功不得付费重试'
    # 救回也要过同一套校验：救回内容不满足契约时必须继续重试，不得放行。
    class BadReasoning(ReasoningOnly):
        def send(self, request):
            self.calls += 1
            return ChatResponse(content='', reasoning='{"entries": "坏形状"}',
                                finish_reason='stop', model='fake')

    transport2 = BadReasoning()
    (tmp_path / 'b').mkdir()
    base2, repo2, _, session2 = _make_session(tmp_path / 'b', transport2)
    with pytest.raises(Exception) as excinfo:
        ask_json(session2, 'bind', _request(), lambda _: ['形状不合约定'])
    assert transport2.calls >= 2, '救回未过闸必须重试，不得采纳'


def test_unknown_delivery_stops_without_automatic_retry(tmp_path):
    """已送达后结果未知：记 unknown，不自动重试（Q-13）。"""
    from novel.llm.paid_requests import ExecutionError

    class Unconfirmed:
        def __init__(self):
            self.calls = 0

        def send(self, request):
            self.calls += 1
            raise TimeoutError('模拟读超时：已送达但结果未知')

    transport = Unconfirmed()
    base, repo, ledger, session = _make_session(tmp_path, transport)
    with pytest.raises((ExecutionError, TimeoutError)):
        ask_json(session, 'bind', _request(), lambda _: [])
    assert transport.calls == 1, 'unknown 不得自动重试'
    from novel.work_units import WorkUnitBudget, read_book_budget
    unit = WorkUnitBudget(base, 'book', 'str06.stage4.bind', 'executor', 'book')
    turn = unit._read().turns[0]
    assert turn.state == 'unknown'
    assert read_book_budget(base, 'book').used == 1, 'unknown 也占额'


def test_errors_carry_stage_node_item_phase_code_context(tmp_path):
    from novel.llm.paid_requests import ExecutionError, PurchaseRequired

    transport = _EmptyThenGoodTransport()
    base, repo, ledger, session = _make_session(tmp_path, transport, yes=False)
    with pytest.raises(PurchaseRequired):
        ask_json(session, 'bind', _request(), lambda _: [])
    # 未知付费 owner 的错误带完整定位字段。
    class Broken:
        def send(self, request):
            raise RuntimeError('boom')

    base2 = tmp_path / 'b2'
    base2.mkdir()
    repo2 = Str06Repository(base2 / 'run')
    ledger2 = CostLedger(path=ledger_path(base2))
    repo2.attach_context(make_context(base2 / 'run', ledger=ledger2))
    session2 = PaidRequestSession(repo2, ledger2, Broken(), yes=True,
                                  allow_source_export=True, provider='fake', sid='book')
    with pytest.raises(ExecutionError) as excinfo:
        ask_json(session2, 'names', _request(), lambda _: [])
    err = excinfo.value
    assert err.code and err.phase and err.node and err.stage and err.context


# ─── 五层记录：AcceptanceRecord 取代 validation 文件；CommitBundle 封存 ─────────

def test_acceptance_record_replaces_validation_file_and_adopts_by_seal(tmp_path):
    transport = _EmptyThenGoodTransport()
    base, repo, ledger, session = _make_session(tmp_path, transport)
    data, ref = ask_json(session, 'bind', _request(), lambda _: [])
    # 逐请求 validation 文件退役；接受分项记录按 attempt 留证（语义恒 unver）。
    assert not list((base / 'run' / 'runtime').glob('validation_*.json'))
    assert not list((base / 'run' / 'runtime').glob('request_*.json'))
    assert not list((base / 'run' / 'runtime').glob('admission.json'))
    from novel.work_units import WorkUnitBudget
    unit = WorkUnitBudget(base, 'book', 'str06.stage4.bind', 'executor', 'book')
    acceptance = unit.acceptance_records()
    assert {attempt_id for attempt_id, _ in acceptance} == {
        a.attempt_id for a in unit._read().turns[0].attempts}
    assert all(record['semantic'] == 'unver' for _, record in acceptance)
    # 正式提交：写产物时以显式引用封存 CommitBundle（输入清单＋采用证据＋输出摘要＋seal）。
    repo.write('stage4/grounding/semantic_binding.json', {'x': 1}, refs=(ref,))
    stored = repo.inspect('stage4/grounding/semantic_binding.json')
    assert stored['_provenance']['adopted_models']
    bundles = commit_bundles(repo.root)
    assert len(bundles) == 1
    bundle: CommitBundleV1 = bundles[0]
    assert bundle.artifact == 'stage4/grounding/semantic_binding.json'
    assert [a.turn_id for a in bundle.adoptions] == [ref.turn_id]
    assert bundle.seal
    # 采用状态从 seal 派生：被引用的内容轮不再被「未采纳关闭」翻面。
    assert (ref.unit_id, ref.turn_id) in sealed_turn_references(repo.root)
    session2 = PaidRequestSession(repo, CostLedger(path=ledger_path(base)), transport,
                                  yes=True, allow_source_export=True,
                                  provider='fake', sid='book')
    ask_json(session2, 'bind', _request(user='第二个请求'), lambda _: [])
    states = [t.state for t in unit._read().turns]
    assert states[0] == 'adopted', '已封存的轮对账为 adopted（采用状态从 seal 派生）'
    assert states[1] == 'delivered'
    ask_json(session2, 'bind', _request(user='第三个请求'), lambda _: [])
    states = [t.state for t in unit._read().turns]
    assert states[1] == 'failed', '未封存的已交付轮在下一次 reserve 前关闭'
    assert states[2] == 'delivered'


# ─── B1 完成条件面：重启不重置、预算竞争、各提交窗口崩溃 ─────────────────────

def _full_chain_wires():
    from tests.test_str06_hook import _full_chain_wires as make
    return make()


def _run_chain(repo, base, transport, tmp_path, *, ceiling=None, node_filter=None):
    from novel.stages.chain import run_workflow
    from tests.test_str06_body import _RoutingTransport, _chapters
    from tests.str06_facilities import body_ask, json_ask, text_ask
    ledger = CostLedger(path=ledger_path(base))
    session = PaidRequestSession(repo, ledger, transport, yes=True,
                                 allow_source_export=True, provider='fake',
                                 book_call_ceiling=ceiling, sid=repo.root.name)
    return run_workflow(repository=repo, chapters=_chapters(),
                        ask=json_ask(session), ask_text=text_ask(session),
                        ask_body=body_ask(session),
                        budget=2000, through='stage7', book_title='报恩',
                        hook_constraints=HookConstraints(target_chars=10,
                                                         hard_max_chars=360),
                        length_band=(0.01, 0.05), node_filter=node_filter)


def test_commit_bundle_loss_replays_and_reseals_free(tmp_path):
    """提交窗口崩溃（产物已写、封存未写）：重跑免费补封存，零新购、字节一致。"""
    from novel.stages.chain import run_workflow
    from tests.test_str06_body import _RoutingTransport, _chapters
    from tests.str06_facilities import body_ask, json_ask, text_ask
    wires = _full_chain_wires()
    base = tmp_path / 'out'
    base.mkdir()
    repo = Str06Repository(base / 'run')
    repo.attach_context(make_context(base / 'run', ledger=CostLedger(path=ledger_path(base))))
    transport = _RoutingTransport(wires)
    _run_chain(repo, base, transport, tmp_path)
    bundles = list((base / 'run' / 'runtime' / 'commit').rglob('*.json'))
    assert bundles, '正式提交必须封存 CommitBundle'
    final_rel = 'stage7/final/报恩_rewriting.txt'
    final_before = repo.path(final_rel).read_bytes()
    calls_before = len(transport.calls)
    # 崩溃窗口：抹掉全部封存记录 → 重跑免费重放并重新封存。
    for bundle in bundles:
        bundle.unlink()
    report = _run_chain(repo, base, transport, tmp_path)
    assert report['assembly']['status'] == 'ready'
    assert len(transport.calls) == calls_before, '封存补尾不得新购'
    assert repo.path(final_rel).read_bytes() == final_before
    resealed = list((base / 'run' / 'runtime' / 'commit').rglob('*.json'))
    assert len(resealed) >= 12, '重跑必须把全部重写产物重新封存'


# ─── R6 统一发布权威：免费产物同样封存；正式读取校验 seal 与摘要（AUD-05） ──────

def _wired_repo(tmp_path):
    """全链 fake 设施（repo 附上下文＋路由 transport；链由调用方驱动）。"""
    from tests.test_str06_body import _RoutingTransport
    wires = _full_chain_wires()
    base = tmp_path / 'out'
    base.mkdir()
    repo = Str06Repository(base / 'run')
    repo.attach_context(make_context(base / 'run', ledger=CostLedger(path=ledger_path(base))))
    transport = _RoutingTransport(wires)
    return repo, base, transport


def test_free_artifacts_are_sealed_like_paid_ones(tmp_path):
    """免费产物同样封存：无采用引用的正式写盘也落 CommitBundle（adoptions 空）。

    不另立付费/免费两套发布语义（R6）：`read_model` 的 seal 校验面前，免费
    compile 产物与付费产物同规。
    """
    repo, base, _transport = _wired_repo(tmp_path)
    repo.write('stage4/ir/writer_book_ir.json', {'book_brief': {}, 'chapters': []})
    bundles = [b for b in commit_bundles(repo.root)
               if b.artifact == 'stage4/ir/writer_book_ir.json']
    assert len(bundles) == 1, '免费（无采用引用）的正式产物必须同样封存发布证明'
    bundle = bundles[0]
    assert bundle.adoptions == ()
    assert [o.key for o in bundle.outputs] == ['stage4/ir/writer_book_ir.json']
    assert bundle.seal


def test_unsealed_formal_read_is_refused_then_free_recovery_reseals(tmp_path):
    """AUD-05：删掉全部 CommitBundle 后，单跑 Stage7 组装的正式消费必须拒绝。

    原行为是 bundle 全删仍可读且 assemble=ready（读取路径不看 seal）。统一发布
    权威启用后：未封存的产物不是正式输入；恢复走重跑全链——付费段按请求身份
    免费重放、免费段确定性重编译，零新购，交付字节不变，再单跑组装重新就绪。
    """
    from novel.errors import UnsealedArtifact
    repo, base, transport = _wired_repo(tmp_path)
    _run_chain(repo, base, transport, tmp_path)
    final_rel = 'stage7/final/报恩_rewriting.txt'
    final_before = repo.path(final_rel).read_bytes()
    calls_before = len(transport.calls)
    bundles = list((base / 'run' / 'runtime' / 'commit').rglob('*.json'))
    assert bundles, '正式提交必须封存 CommitBundle'
    for bundle in bundles:
        bundle.unlink()
    # 只跑 Stage7 组装：正式输入全部未封存 ⇒ 拒绝消费（不能照旧 ready）。
    with pytest.raises(UnsealedArtifact, match='未封存'):
        _run_chain(repo, base, transport, tmp_path,
                   node_filter=('str06.stage7.assemble',))
    # 免费恢复：重跑全链按请求身份重放并重新封存，再交付。
    report = _run_chain(repo, base, transport, tmp_path)
    assert report['assembly']['status'] == 'ready'
    assert len(transport.calls) == calls_before, '恢复补尾不得新购'
    assert repo.path(final_rel).read_bytes() == final_before
    resealed = list((base / 'run' / 'runtime' / 'commit').rglob('*.json'))
    assert len(resealed) >= 12, '重跑必须把全部重写产物重新封存'
    # 恢复后单跑组装重新放行；摘要漂移的产物仍拒（seal＋摘要双校验）。
    report = _run_chain(repo, base, transport, tmp_path,
                        node_filter=('str06.stage7.assemble',))
    assert report['assembly']['status'] == 'ready'
    ir_path = repo.path('stage4/ir/writer_book_ir.json')
    ir_path.write_text(ir_path.read_text(encoding='utf-8') + '\n', encoding='utf-8')
    with pytest.raises(UnsealedArtifact, match='摘要'):
        repo.read_model('stage4/ir/writer_book_ir.json')


def test_compile_stage4_keeps_formal_positions_unpublished_on_guard_failure(
        tmp_path, monkeypatch):
    """R6 验过才发布：机械 Guard 拦下时正式位不发布，失败结果只进诊断面。

    现行为在 Guard 判定前就把 writer_book_ir／packet_metrics 写进正式位——
    失败结果冒充可交付。修复后：问题清单只进 guard report（诊断），四个正式
    派生产物（execution_plan／hook 上下文／写手 IR／packet 指标）一概不落盘。
    """
    from novel.stages import chain as chain_module
    repo, base, transport = _wired_repo(tmp_path)
    monkeypatch.setattr(
        chain_module, 'run_mechanical_guard',
        lambda **kwargs: ['M-TEST 注入的机械问题：必保义务未承载'])
    with pytest.raises(ValueError, match='M-TEST'):
        _run_chain(repo, base, transport, tmp_path)
    # 失败结果只进诊断：guard report 留证问题清单（逐场景各报一次）。
    report = repo.inspect('stage4/acceptance/guard/report.json')
    assert report['guard_errors'], 'Guard 问题必须留证在诊断面'
    assert set(report['guard_errors']) == {'M-TEST 注入的机械问题：必保义务未承载'}
    # 正式位不发布（未被验证过的结果不是可交付物）。
    assert not repo.path('stage4/ir/writer_book_ir.json').exists()
    assert not repo.path('stage4/acceptance/packet_metrics.json').exists()
    assert not repo.path('stage4/context/hook/hook.json').exists()
    assert not repo.path('stage4/compiled/execution_plan.json').exists()


def test_restarted_run_reuses_purchased_answers_across_sessions(tmp_path):
    """重启（新 session）不重置额度、不重购：工作单元账＋整书总闸跨调用连续。"""
    from tests.test_str06_body import _RoutingTransport, _chapters
    from tests.str06_facilities import body_ask, json_ask, text_ask
    wires = _full_chain_wires()
    base = tmp_path / 'out'
    base.mkdir()
    repo = Str06Repository(base / 'run')
    repo.attach_context(make_context(base / 'run', ledger=CostLedger(path=ledger_path(base))))
    transport = _RoutingTransport(wires)
    calls_first = len(transport.calls)
    _run_chain(repo, base, transport, tmp_path)
    purchased = len(transport.calls) - calls_first
    # B2 五节点：book＋ch01–ch02＋names＋associate＋preserve＋design＋bind＋
    # hook_design＋naming＝10 个 JSON owner，加 hook_writer 与两章 body＝13。
    assert purchased == 13
    from novel.work_units import read_book_budget
    used_after_first = read_book_budget(base, 'run').used
    assert used_after_first == purchased, '每次外发各计一次（含空回答等失败 attempt）'
    # 新 session 读同一份持久账：已消耗额度不重置（Q-10）。
    calls_before = len(transport.calls)
    _run_chain(repo, base, transport, tmp_path)
    assert len(transport.calls) == calls_before, '重启重放零新购'
    assert read_book_budget(base, 'run').used == used_after_first, '重放不占额'


def test_budget_competition_stops_at_ceiling_with_attempt_accounting(tmp_path):
    """预算竞争：--book-call-ceiling 从整书总闸派生；attempt 计数、unknown 占额。"""
    from tests.test_str06_body import _RoutingTransport, _chapters
    wires = _full_chain_wires()
    base = tmp_path / 'out'
    base.mkdir()
    repo = Str06Repository(base / 'run')
    repo.attach_context(make_context(base / 'run', ledger=CostLedger(path=ledger_path(base))))
    transport = _RoutingTransport(wires)
    from novel.stages.chain import run_workflow
    from tests.str06_facilities import body_ask, json_ask, text_ask
    ledger = CostLedger(path=ledger_path(base))
    session = PaidRequestSession(repo, ledger, transport, yes=True,
                                 allow_source_export=True, provider='fake',
                                 book_call_ceiling=2, sid=repo.root.name)
    with pytest.raises(Exception, match='book-call-ceiling'):
        run_workflow(repository=repo, chapters=_chapters(),
                     ask=json_ask(session), ask_text=text_ask(session),
                     ask_body=body_ask(session),
                     budget=2000, through='stage7', book_title='报恩',
                     hook_constraints=HookConstraints(target_chars=10,
                                                      hard_max_chars=360),
                     length_band=(0.01, 0.05))
    from novel.work_units import read_book_budget, sealed_commit_bundles
    assert read_book_budget(base, 'run').used == 2, '恰好停在第二笔外发，不静默超限'
    # 停止顺序（B1）：准入已关（不再外发），已完成提交窗口的封存记录
    # 完好无损（seal 全部通过核对），书级账不因停止被重置或追加。
    for _path, bundle in sealed_commit_bundles(repo.root):
        assert bundle.seal and bundle.adoptions


def test_not_delivered_transport_failure_retries_within_same_turn(tmp_path):
    """未送达（429/连接失败）：同一内容轮内有界退避重试，占尝试额度、如实计费。"""
    from novel.llm.transport import TransportError

    class RateLimitedThenOk:
        def __init__(self):
            self.calls = 0

        def send(self, request):
            self.calls += 1
            if self.calls == 1:
                raise TransportError('429 rate limit', retryable=True, status=429,
                                     kind='rate_limit')
            return ChatResponse(content='{"entries": []}', finish_reason='stop',
                                usage={'prompt_tokens': 7, 'completion_tokens': 20},
                                model='fake')

    transport = RateLimitedThenOk()
    base, repo, ledger, session = _make_session(tmp_path, transport)
    session.retry_policy = TransportRetryPolicy(max_retries=3, backoff_base=0)
    data, answer = ask_json(session, 'bind', _request(), lambda _: [])
    assert data == {'entries': []}
    assert transport.calls == 2, '未送达在同轮内重试一次后成功'
    from novel.work_units import WorkUnitBudget, read_book_budget
    unit = WorkUnitBudget(base, 'book', 'str06.stage4.bind', 'executor', 'book')
    turn = unit._read().turns[0]
    assert len(turn.attempts) == 2 and [a.outcome for a in turn.attempts] == ['failed', 'ok']
    assert turn.state == 'delivered'
    assert read_book_budget(base, 'book').used == 2, '重试 attempt 也占整书总闸'
    rows, partial = read_cost_snapshot(base)
    assert not partial and len([r for r in rows if not r['cached']]) == 2
    assert rows[0]['error_kind'] == 'rate_limit' and rows[0]['will_retry'] is True


# ─── B1 后半：截断即停（D6）＋ExecutionProfile 冻结贯通＋并发登记 ───────────────

def test_truncated_answer_stops_item_without_retry_or_extra_budget(tmp_path):
    """截断（finish_reason=length）：即停该 item，留证、不重试、不加预算（D6）。"""
    from novel.llm.paid_requests import ExecutionError

    class Truncated:
        def __init__(self):
            self.calls = 0

        def send(self, request):
            self.calls += 1
            return ChatResponse(
                content='{"entries": [', reasoning='想了一半被切断',
                finish_reason='length',
                usage={'prompt_tokens': 700, 'completion_tokens': 131072,
                       'reasoning_tokens': 90000},
                model='fake')

    transport = Truncated()
    base, repo, ledger, session = _make_session(tmp_path, transport)
    with pytest.raises(ExecutionError) as excinfo:
        ask_json(session, 'bind', _request(), lambda _: [])
    err = excinfo.value
    assert err.code == 'truncated' and err.phase == 'accept'
    assert err.context['completion_tokens'] == 131072
    assert err.context['reasoning_tokens'] == 90000, '截断必须如实记录 reasoning 与正文 token'
    assert transport.calls == 1, '截断不自动重试'
    from novel.work_units import WorkUnitBudget, read_book_budget
    unit = WorkUnitBudget(base, 'book', 'str06.stage4.bind', 'executor', 'book')
    turn = unit._read().turns[0]
    assert turn.state == 'delivered', '截断响应已完整交付并耐久留证'
    assert [a.outcome for a in turn.attempts] == ['ok'], 'ok 留证保证重跑免费命中，不自动重购'
    assert read_book_budget(base, 'book').used == 1, '外发占额一次，截断不追加预算'
    rows, partial = read_cost_snapshot(base)
    assert not partial
    paid = [r for r in rows if not r['cached']]
    assert len(paid) == 1 and paid[0]['finish_reason'] == 'length'
    assert paid[0]['completion_tokens'] == 131072 and paid[0]['reasoning_tokens'] == 90000


def test_truncated_cached_replay_stops_again_without_rebuying(tmp_path):
    """重启重放命中同一截断响应：仍即停，不重购、不换回答。"""
    from novel.llm.paid_requests import ExecutionError

    class Truncated:
        def __init__(self):
            self.calls = 0

        def send(self, request):
            self.calls += 1
            return ChatResponse(content='{"entries": [', finish_reason='length',
                                usage={'prompt_tokens': 700, 'completion_tokens': 131072},
                                model='fake')

    transport = Truncated()
    base, repo, ledger, session = _make_session(tmp_path, transport)
    with pytest.raises(ExecutionError, match='截断'):
        ask_json(session, 'bind', _request(), lambda _: [])
    replay = PaidRequestSession(repo, CostLedger(path=ledger_path(base)), transport,
                                provider='fake', sid='book')
    with pytest.raises(ExecutionError) as excinfo:
        ask_json(replay, 'bind', _request(), lambda _: [])
    assert excinfo.value.code == 'truncated'
    assert transport.calls == 1, '免费命中同一截断响应，不得再付费重购'
    assert excinfo.value.context['cached'] is True


def test_execution_profile_freezes_config_for_whole_run():
    """ExecutionProfile：effort/max_tokens 取值面收紧；配置只冻结一次。"""
    from novel.config import Config
    from novel.stages.chain import (
        ExecutionProfile, STR06_EFFORT_LEVELS, str06_chat_request,
    )
    from contracts.invariants import LLM_MAX_OUTPUT_TOKENS

    assert STR06_EFFORT_LEVELS == ('low', 'high', 'max')
    cfg = Config(model='fake-model', thinking_mode='on')
    profile = ExecutionProfile(cfg=cfg, reasoning_effort='high')
    assert profile.max_tokens == LLM_MAX_OUTPUT_TOKENS == 131072
    for bad in ('medium', 'xhigh', '', 'HIGH'):
        with pytest.raises(ValueError, match='effort'):
            ExecutionProfile(cfg=cfg, reasoning_effort=bad)
    with pytest.raises(ValueError, match='131072'):
        ExecutionProfile(cfg=cfg, reasoning_effort='high', max_tokens=65536)
    request = str06_chat_request(profile, 'bind', '提示', json_mode=True,
                                 system='系统', temperature=0.7, enforce=False)
    assert request.model == 'fake-model'
    assert request.reasoning_effort == 'high' and request.thinking is True
    assert request.max_tokens == 131072, '全部付费节点统一 131072（D6）'
    # 请求身份与 ledger 列由同一份 profile 决定：model/effort/thinking/max_tokens
    # 全部落在 cache_payload（= 请求身份）里。
    payload = request.cache_payload()
    assert payload['max_tokens'] == 131072 and payload['reasoning_effort'] == 'high'


def test_assemble_paid_boundary_never_rereads_config_at_execution(tmp_path):
    """删执行时 `load_config()` 重读：装配只消费 run 启动时冻结的 profile。"""
    import novel.stages.chain as chain_mod
    from novel.config import Config
    from novel.stages.chain import assemble_paid_boundary, freeze_execution_profile

    base = tmp_path / 'out'
    base.mkdir()
    from novel.work_units import require_workflow
    require_workflow(base)
    cfg = Config(model='frozen-model')
    profile = freeze_execution_profile(cfg=cfg, reasoning_effort='low',
                                       base=base, sid='run')
    assert profile.book_call_ceiling == 512, '账缺失时冻结默认整书上限'

    original_load_config = chain_mod.load_config

    def _refuse(*a, **kw):
        raise AssertionError('执行期不得重读 load_config()')

    chain_mod.load_config = _refuse
    try:
        boundary = assemble_paid_boundary(
            base=base, run_dir=base / 'run',
            source_identity='a' * 64, root_sha1='b' * 40, profile=profile)
    finally:
        chain_mod.load_config = original_load_config
    assert boundary.model == 'frozen-model'
    assert boundary.session.transport.cfg is profile.cfg, 'transport 实参来自冻结 profile'
    assert boundary.session.book_call_ceiling == 512, '整书上限贯通到付费边界'
    assert boundary.profile.reasoning_effort == 'low'


def test_execution_profile_ceiling_derives_from_admission_ledger(tmp_path):
    """单次运行上限从同一准入账派生：ceiling＋grants；显式值优先（I-17）。"""
    from novel.config import Config
    from novel.stages.chain import freeze_execution_profile
    from novel.work_units import grant_book_budget, read_book_budget

    base = tmp_path / 'out'
    base.mkdir()
    from novel.work_units import require_workflow
    require_workflow(base)
    profile = freeze_execution_profile(cfg=Config(), reasoning_effort='high',
                                       base=base, sid='book')
    assert profile.book_call_ceiling == 512
    grant_book_budget(base, 'book', actor='人工', reason='追加', additional=3)
    budget = read_book_budget(base, 'book')
    profile2 = freeze_execution_profile(cfg=Config(), reasoning_effort='high',
                                        base=base, sid='book')
    assert profile2.book_call_ceiling == budget.ceiling + 3 == 515, '含显式追加'
    profile3 = freeze_execution_profile(cfg=Config(), reasoning_effort='high',
                                        base=base, sid='book', book_call_ceiling=7)
    assert profile3.book_call_ceiling == 7, '显式 --book-call-ceiling 优先'


def test_parallel_registry_registers_new_chain_chapter_node_only():
    """并发登记改新链逐章节点：退役旧链键删除，键必须在 NODES 且逐章 scope。"""
    from contracts.invariants import NODES, PIPELINE_PARALLEL_NODES

    # B2（2026-09-27）：章观察 owner `str06.stage2.chapters` 登记进并发覆盖表。
    assert PIPELINE_PARALLEL_NODES == ('str06.stage2.chapters', 'str06.stage5.body')
    for node in PIPELINE_PARALLEL_NODES:
        assert node in NODES, f'并发覆盖节点必须在现役注册表：{node}'
        # 关闭批：退役名册已删，并发面只认现役注册表。
        spec = NODES[node].lifecycle
        assert spec is not None and spec.unit_kind == 'chapter', \
            f'{node} 必须是逐章节点（scope=chapter:N）'
    for legacy in ('stage2.blueprint', 'stage4.design', 'stage4.plan', 'stage5.write'):
        assert legacy not in PIPELINE_PARALLEL_NODES, '退役旧链键已删除'
    from contracts.schema.orchestration import PipelineConcurrency
    overrides = PipelineConcurrency(node_overrides={'str06.stage5.body': 4})
    assert overrides.node_overrides == {'str06.stage5.body': 4}
    with pytest.raises(ValueError, match='逐章节点'):
        PipelineConcurrency(node_overrides={'stage4.design': 4})
