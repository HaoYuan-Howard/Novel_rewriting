#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_llm_audit.py —— 采纳审计三事件：wire 行的联结键 · `gate_decision` · `artifact_adoption`。

⚠⚠ 它守的是一个实测踩过的观测洞：成本账那一行是**传输层**结论
（内容闸拒了它照样 `outcome=ok`），「最终采纳档位」原来只能靠
「同 purpose 最后一条 ok 行」猜——并发、缓存重放、中断恢复下都不可靠。
⇒ 判定与采纳各自显式落账（`llm_audit.jsonl`），靠 `attempt_id` 反查。

⚠ 本机 `%TEMP%\\pytest-of-yh255` 的 ACL 坏了，`tmp_path` 一用就炸——一律 `tempfile.mkdtemp()`。
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
import pytest

import novel.llm.keepsake as keepsake_mod
from novel import run_identity
from novel.llm.cache import (
    CachePolicy, ResponseCache, cache_key, request_fingerprint,
)
from novel.llm.client import JSON_OBJECT_MODE, LlmClient
from novel.llm.ledger import AUDIT_REL, CostLedger, audit_path
from novel.llm.transport import ChatRequest, ChatResponse, FakeTransport
from novel.errors import ProvenanceRefused
from novel.paths import ANSWER_LOST_DIRNAME
from novel.run import RunContext


class Resp(BaseModel):
    voice: dict = Field(...)
    surface_habits: list[str] = Field(...)


OK_JSON = ChatResponse(
    content=json.dumps({'voice': {'a': 1}, 'surface_habits': ['h']},
                       ensure_ascii=False),
    finish_reason='stop', usage={'prompt_tokens': 10, 'completion_tokens': 5},
    model='deepseek-v4-pro')


def _book() -> Path:
    base = Path(tempfile.mkdtemp())
    (base / '书名_123' / 'stage1').mkdir(parents=True)
    return base


def _ledgered_ctx() -> tuple[RunContext, Path]:
    d = Path(tempfile.mkdtemp())
    ctx = RunContext.open(_book(), '123', 'stage6.hook', paid=True,
                          ledger=CostLedger(path=d / 'ledger.jsonl'))
    return ctx, d


def _client(transport, ctx, **kw) -> LlmClient:
    return LlmClient(ctx, transport, sleep=lambda _s: None, **kw)


def _rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line)
            for line in path.read_text('utf-8').splitlines() if line.strip()]


def test_wire_rows_carry_the_attempt_identity_triplet():
    """成本账 wire 行要能被审计行**指回来**：attempt_id + 请求指纹 + 响应内容身份。"""
    ctx, d = _ledgered_ctx()
    client = _client(FakeTransport.always(OK_JSON), ctx)
    client.ask_json('sys', 'usr', model_cls=Resp, purpose='p')

    rows = _rows(d / 'ledger.jsonl')
    assert len(rows) == 1
    row = rows[0]
    assert isinstance(row['attempt_id'], str) and len(row['attempt_id']) == 16
    # ⚠ `json_mode` 跟着 `JSON_OBJECT_MODE`，别写死：这条守的是「账上那个指纹
    #   算的就是真发出去的那个请求」，不是「ask_json 恒发 json_object」。
    expected_req = ChatRequest(
        model=client.model, system='sys', user='usr',
        reasoning_effort='high', thinking=True, json_mode=JSON_OBJECT_MODE,
        max_tokens=row['max_tokens'])
    assert row['request_fingerprint'] == request_fingerprint(expected_req)
    assert client.accepted_request_fingerprint('p') == row['request_fingerprint']
    assert row['response_sha256'] == hashlib.sha256(
        OK_JSON.content.encode('utf-8')).hexdigest()


def test_one_run_id_closes_child_env_wire_gate_and_adoption_accounts(monkeypatch):
    """子进程继承的同一身份必须逐字落进成本请求、内容闸与正式采纳三类账。"""
    run_id = 'acceptance-run-42'
    monkeypatch.setenv(run_identity.RUN_ID_ENV, run_id)
    ctx = RunContext.open(_book(), '123', 'stage6.hook', paid=True)
    client = _client(FakeTransport.always(OK_JSON), ctx)
    client.ask_json('sys', 'usr', model_cls=Resp, purpose='p')
    attempt_id = client.accepted_attempt('p')
    ctx.ledger.note_adoption(
        sid=ctx.sid, node=ctx.node, purpose='p', attempt_id=attempt_id,
        artifact='stage2/chapter_blueprints.json', artifact_fingerprint='fp-run',
        chapter=3)

    assert ctx.ledger.path is not None
    wire = _rows(ctx.ledger.path)
    audit = _rows(ctx.ledger.path.with_name(AUDIT_REL[-1]))
    assert len(wire) == 1
    assert [row['kind'] for row in audit] == ['gate_decision', 'artifact_adoption']
    observed = [ctx.run_id, ctx.ledger.run_id, wire[0]['run_id'],
                *(row['run_id'] for row in audit)]
    assert observed == [run_id] * len(observed)
    assert wire[0]['attempt_id'] == audit[0]['attempt_id'] == audit[1]['attempt_id']


def test_legacy_ledger_without_run_id_is_claimed_without_backfill():
    """旧成本行可用于恢复，但读取绝不能把当前 run_id 猜写回历史字节。"""
    d = Path(tempfile.mkdtemp())
    path = d / 'ledger.jsonl'
    attempt_id, fingerprint = 'legacy-attempt-01', 'a' * 64
    legacy = {
        'attempt_id': attempt_id,
        'sid': '123', 'node': 'stage2.blueprint', 'purpose': 'chapter:1',
        'request_fingerprint': fingerprint,
        'model': 'deepseek-v4-flash', 'request_model': 'deepseek-v4-flash',
        'cached': False,
        'reasoning_effort': 'high', 'thinking': True, 'json_mode': True,
        'max_tokens': 2048, 'response_sha256': 'b' * 64,
        'chapter': 1,
        'prompt_tokens': 10, 'completion_tokens': 5, 'reasoning_tokens': 3,
    }
    before = (json.dumps(legacy, ensure_ascii=False, sort_keys=True) + '\n').encode('utf-8')
    path.write_bytes(before)
    ledger = CostLedger(path=path, run_id='resume-run-42')

    ledger.claim_prior_attempts(
        [attempt_id], sid='123', node='stage2.blueprint', purpose='chapter:1',
        request_fingerprints=[fingerprint])

    assert ledger.attempt_ids_for_purpose('chapter:1') == (attempt_id,)
    assert ledger.request_identity_for_attempt(attempt_id)['request_fingerprint'] == fingerprint
    usage = ledger.usage()
    assert usage is not None and usage.calls_made == 1 and usage.reasoning_tokens == 3
    assert path.read_bytes() == before
    assert 'run_id' not in json.loads(path.read_text('utf-8'))


def test_usage_for_exact_attempt_identities_is_independent_of_live_counter():
    """精确 attempt 聚合只数 checkpoint 所指行，并复用 token-null 算法。"""
    d = Path(tempfile.mkdtemp())
    path = d / 'ledger.jsonl'
    run_id = 'selected-usage-run'
    sid, node = '123', 'stage4.design'

    def row(
            attempt: str, purpose: str, fingerprint_char: str, *,
            cached: bool, prompt: int | None,
            completion: int | None, reasoning: int | None,
    ) -> dict:
        return {
            'run_id': run_id, 'attempt_id': attempt,
            'sid': sid, 'node': node, 'purpose': purpose,
            'request_fingerprint': fingerprint_char * 64,
            'model': 'gpt-5.6-sol', 'request_model': 'gpt-5.6-sol',
            'cached': cached, 'reasoning_effort': 'high',
            'thinking': None, 'json_mode': True, 'max_tokens': None,
            'response_sha256': attempt[0] * 64, 'chapter': None,
            'prompt_tokens': prompt, 'completion_tokens': completion,
            'reasoning_tokens': reasoning,
        }

    selected = [
        row('1' * 16, 'stage4.design:strategy', 'a',
            cached=False, prompt=10, completion=5, reasoning=None),
        row('2' * 16, 'stage4.design:strategy', 'b',
            cached=False, prompt=20, completion=7, reasoning=3),
        row('3' * 16, 'stage4.design:ch0001', 'c',
            cached=True, prompt=None, completion=2, reasoning=None),
    ]
    unrelated = row(
        '4' * 16, 'stage4.design:ch9999', 'd',
        cached=False, prompt=999, completion=999, reasoning=999)
    path.write_text(
        ''.join(json.dumps(item, sort_keys=True) + '\n'
                for item in (*selected, unrelated)),
        encoding='utf-8')
    ledger = CostLedger(path=path, run_id=run_id)
    # 污染当前实例计数器；精确聚合不得读它。
    ledger.calls_made = 77
    ledger.prompt_tokens = 7777
    ledger._seen['prompt_tokens'] = 77
    before = path.read_bytes()

    def identity(item: dict) -> dict[str, object]:
        return {
            'attempt_id': item['attempt_id'],
            'request_fingerprint': item['request_fingerprint'],
            'model': item['model'], 'request_model': item['request_model'],
            'reasoning_effort': item['reasoning_effort'],
            'thinking': item['thinking'], 'json_mode': item['json_mode'],
            'max_tokens': item['max_tokens'],
            'response_sha256': item['response_sha256'],
        }

    usage = ledger.usage_for_attempt_identities(
        [(item['purpose'], identity(item)) for item in selected],
        sid=sid, node=node)

    assert usage is not None
    assert usage.model_dump() == {
        'calls_made': 2, 'calls_cached': 1,
        'prompt_tokens': 30, 'completion_tokens': 14,
        'reasoning_tokens': 3,
    }
    assert path.read_bytes() == before
    assert ledger.calls_made == 77 and ledger.prompt_tokens == 7777

    # checkpoint 只锚住每个 purpose 的最终采纳行；complete 边界
    # 仍须纳入 strategy 的前一次失败/重试，但排除其他 purpose。
    complete = ledger.usage_for_complete_purposes(
        ['stage4.design:strategy', 'stage4.design:ch0001'],
        sid=sid, node=node,
        required_identities=[
            (selected[1]['purpose'], identity(selected[1])),
            (selected[2]['purpose'], identity(selected[2])),
        ])
    assert complete == usage

    tampered = identity(selected[0])
    tampered['response_sha256'] = 'f' * 64
    with pytest.raises(ProvenanceRefused, match='identity|ledger'):
        ledger.usage_for_attempt_identities(
            [(selected[0]['purpose'], tampered)], sid=sid, node=node)


def test_complete_purpose_usage_uses_exact_in_memory_scope_without_path():
    """path=None 仍从同源 purpose counter 结算，不借全局累计值。"""
    ledger = CostLedger(path=None, run_id='memory-purpose-run')
    sid, node = '123', 'stage4.design'

    def record(
            attempt: str, purpose: str, fingerprint_char: str, *,
            row_sid: str = sid, row_node: str = node,
            cached: bool = False, prompt: int | None = None,
            completion: int | None = None,
    ) -> None:
        ledger.record(
            node=row_node, sid=row_sid, model='gpt-5.6-sol', code='test',
            cached=cached, cache_policy='default', purpose=purpose,
            prompt_tokens=prompt, completion_tokens=completion,
            attempt_id=attempt,
            request_fingerprint=fingerprint_char * 64,
            response_sha256=fingerprint_char * 64,
            request_model='gpt-5.6-sol', reasoning_effort='high',
            thinking=None, json_mode=True, max_tokens=None)

    record(
        'memory-attempt-1', 'stage4.design:strategy', 'a',
        prompt=5, completion=None)
    record(
        'memory-attempt-2', 'stage4.design:ch0001', 'b',
        cached=True, prompt=None, completion=2)
    # 同 purpose 但不同 node 不得因为 path=None 串入。
    record(
        'memory-attempt-other', 'stage4.design:strategy', 'c',
        row_node='stage4.plan', prompt=999, completion=999)
    accepted = ledger.request_identity_for_attempt('memory-attempt-2')

    usage = ledger.usage_for_complete_purposes(
        ['stage4.design:strategy', 'stage4.design:ch0001'],
        sid=sid, node=node,
        required_identities=[('stage4.design:ch0001', accepted)])

    assert usage is not None
    assert usage.model_dump() == {
        'calls_made': 1, 'calls_cached': 1,
        'prompt_tokens': 5, 'completion_tokens': 2,
        'reasoning_tokens': None,
    }


def test_gate_decisions_and_adoption_answer_which_model_was_finally_used():
    """⭐ 反查全链：Flash 被 parse 闸拒 → Pro 采纳 → adoption → 实际模型。

    这正是「最终采纳档位」的读法：**不看**「最后一条 ok 行」（首档那行也是 ok），
    看 `artifact_adoption → attempt_id → 成本账行` 这条联结。

    ⚠ **首档这一份刻意是【截断】的**（`finish_reason='length'`），不是随便一个坏 JSON：
    2026-08-22 起除截断外的每一种拒绝都先**同档重采**（`_worth_resampling`），
    而本条测试承重的是最后那两行——「被拒的是 Flash、采纳的是 Pro」这条
    **跨模型**反查；两端的 effort/thinking 必须完全相同，不能暗中恢复降档。
    """
    ctx, d = _ledgered_ctx()
    bad = ChatResponse(content='{"voice": {"a": 1}, "surface_hab', finish_reason='length')
    t = FakeTransport(responder=lambda _r, i: bad if i == 0 else OK_JSON)
    client = _client(t, ctx)
    client.ask_json('sys', 'usr', model_cls=Resp, purpose='p')

    audit = _rows(d / AUDIT_REL[-1])
    assert [row['kind'] for row in audit] == ['gate_decision', 'gate_decision']
    rejected, accepted = audit
    assert (rejected['decision'], rejected['gate']) == ('rejected', 'parse')
    assert (rejected['rung_index'], rejected['sample'],
            rejected['resample']) == (0, 1, False)
    assert rejected['source'] == 'wire'
    assert (accepted['decision'], accepted['gate']) == ('accepted', None)
    assert accepted['rung_index'] == 0

    adopted = client.accepted_attempt('p')
    assert adopted == accepted['attempt_id']

    ctx.ledger.note_adoption(
        sid=ctx.sid, node=ctx.node, purpose='p', attempt_id=adopted,
        artifact='stage2/chapter_blueprints.json', artifact_fingerprint='fp10',
        chapter=3)
    adoption = _rows(d / AUDIT_REL[-1])[-1]
    assert adoption['kind'] == 'artifact_adoption'
    assert adoption['chapter'] == 3

    wire = {row['attempt_id']: row for row in _rows(d / 'ledger.jsonl')}
    # 两行都是传输层 ok —— 单看成本账分不出谁被采纳，这正是审计账存在的理由。
    assert all(row['outcome'] == 'ok' for row in wire.values())
    assert wire[adoption['attempt_id']]['model'] == 'deepseek-v4-pro'
    assert wire[rejected['attempt_id']]['model'] == 'deepseek-v4-flash'
    assert {row['reasoning_effort'] for row in wire.values()} == {'high'}
    assert client.accepted_model('p') == 'deepseek-v4-pro'
    assert client.accepted_request_fingerprint('p') == wire[
        adoption['attempt_id']]['request_fingerprint']
    assert client.accepted_request_fingerprint('p') != wire[
        rejected['attempt_id']]['request_fingerprint']


def test_cache_hits_settle_gates_and_are_adoptable_too():
    """缓存来的响应同样过闸、同样可被采纳——联结键不因来源缺席。"""
    cache_dir = Path(tempfile.mkdtemp())
    ctx1, _d1 = _ledgered_ctx()
    _client(FakeTransport.always(OK_JSON), ctx1,
            cache=ResponseCache(cache_dir)).ask_json(
        'sys', 'usr', model_cls=Resp, purpose='p')

    ctx2, d2 = _ledgered_ctx()
    t = FakeTransport.always(OK_JSON)
    client = _client(t, ctx2, cache=ResponseCache(cache_dir))
    client.ask_json('sys', 'usr', model_cls=Resp, purpose='p')
    assert not t.calls, '有合格缓存时必须 0 次真请求'

    audit = _rows(d2 / AUDIT_REL[-1])
    assert [(row['decision'], row['source']) for row in audit] == [
        ('accepted', 'cache')]
    ledger_rows = _rows(d2 / 'ledger.jsonl')
    assert len(ledger_rows) == 1 and ledger_rows[0]['cached'] is True
    assert ledger_rows[0]['attempt_id'] == audit[0]['attempt_id']
    assert client.accepted_attempt('p') == audit[0]['attempt_id']
    assert client.accepted_request_fingerprint('p') == ledger_rows[0][
        'request_fingerprint']


def test_audit_events_survive_in_memory_when_there_is_no_ledger_file():
    """`--dry-run` / 测试（`write_ledger=False`）没有盘上账本，判定仍要能被断言。"""
    ctx = RunContext.open(_book(), '123', 'stage6.hook', paid=True,
                          write_ledger=False)
    transport = FakeTransport.always(OK_JSON)
    client = LlmClient(ctx, transport, sleep=lambda _s: None)
    client.ask_json('sys', 'usr', model_cls=Resp, purpose='p',
                    cache_policy=CachePolicy.default())
    kinds = [row['kind'] for row in ctx.ledger.audit_events]
    assert kinds == ['gate_decision']
    assert ctx.ledger.audit_events[0]['decision'] == 'accepted'
    assert client.accepted_request_fingerprint('p') == request_fingerprint(
        transport.calls[0])


def test_candidate_patch_audit_is_hash_only_and_idempotent():
    ledger = CostLedger(run_id='candidate-patch-run')
    kwargs = dict(
        sid='123', node='stage2.global', purpose='stage2.global',
        attempt_id='patch-attempt-01',
        base_candidate_sha256='a' * 64,
        result_candidate_sha256='b' * 64,
        changed_path_count=1,
    )

    ledger.note_candidate_patch(**kwargs)
    ledger.note_candidate_patch(**kwargs)

    row, = ledger.audit_events
    assert row['kind'] == 'candidate_patch_applied'
    assert row['attempt_id'] == 'patch-attempt-01'
    assert row['changed_path_count'] == 1
    assert row['base_candidate_sha256'] == 'a' * 64
    assert row['result_candidate_sha256'] == 'b' * 64
    assert 'path' not in row and 'locator' not in row


def test_evidence_locator_rewrite_audit_omits_text_and_binds_artifact_bytes():
    ledger = CostLedger(run_id='locator-rewrite-run')
    kwargs = dict(
        sid='123', node='stage2.global', purpose='stage2.global',
        artifact='stage2/global_grounding.json',
        artifact_sha256='c' * 64,
        claim_path='entities.relations.rel_39.predicate',
        original_chapter_ordinal=9,
        resolved_chapter_ordinal=12,
        match_mode='exact',
    )

    ledger.note_evidence_locator_rewrite(**kwargs)
    ledger.note_evidence_locator_rewrite(**kwargs)

    row, = ledger.audit_events
    assert row['kind'] == 'evidence_locator_rewrite'
    assert row['artifact_sha256'] == 'c' * 64
    assert (row['original_chapter_ordinal'], row['resolved_chapter_ordinal']) == (9, 12)
    assert row['match_mode'] == 'exact'
    assert 'locator' not in row and 'source' not in row and 'text' not in row


def test_request_fingerprint_lookup_refuses_a_missing_attempt_instead_of_estimating():
    with pytest.raises(ProvenanceRefused, match='拒绝按默认 effort/thinking 估算'):
        CostLedger().request_fingerprint_for_attempt('not-recorded')


def test_model_lookup_refuses_a_missing_attempt_instead_of_using_the_primary_model():
    with pytest.raises(ProvenanceRefused, match='拒绝按 client 主模型估算'):
        CostLedger().model_for_attempt('not-recorded')


def test_ledger_refuses_a_truncated_cache_key_as_request_identity():
    ledger = CostLedger()
    with pytest.raises(ProvenanceRefused, match='完整的小写 SHA-256'):
        ledger.record(
            node='stage5.write', sid='123', model='m', code='c', cached=False,
            cache_policy='default', attempt_id='attempt',
            request_fingerprint='a' * 32)
    assert ledger.calls_made == 0, '拒收截断身份前不能先记成一次调用'


def test_one_attempt_id_cannot_be_rebound_to_another_request():
    ledger = CostLedger()
    common = dict(
        node='stage5.write', sid='123', model='m', code='c', cached=False,
        cache_policy='default', attempt_id='same-attempt')
    ledger.record(**common, request_fingerprint='a' * 64)
    with pytest.raises(ProvenanceRefused, match='同时指向两个请求指纹'):
        ledger.record(**common, request_fingerprint='b' * 64)
    assert ledger.calls_made == 1, '拒收碰撞前不能先把同一次请求重复计费'


def test_one_attempt_id_cannot_be_rebound_to_another_model():
    ledger = CostLedger()
    common = dict(
        node='stage5.write', sid='123', code='c', cached=False,
        cache_policy='default', attempt_id='same-attempt',
        request_fingerprint='a' * 64)
    ledger.record(**common, model='deepseek-v4-flash')
    with pytest.raises(ProvenanceRefused, match='同时指向两个模型'):
        ledger.record(**common, model='deepseek-v4-pro')
    assert ledger.calls_made == 1


def test_audit_path_is_next_to_the_ledger():
    assert audit_path('X').as_posix() == 'X/_service/llm_audit.jsonl'


LOST = ChatResponse(
    content='', reasoning='整章正文都写在这条通道里了',
    finish_reason='stop',
    usage={'prompt_tokens': 10, 'completion_tokens': 900, 'reasoning_tokens': 900},
    model='deepseek-v4-flash')


def test_the_lost_answer_is_kept_on_disk_and_the_audit_row_points_at_it():
    """⭐ 「答案流丢失」那一份**整份留证**，审计账上只有指针与长度读数。

    ⚠⚠ 它补的是一个诊断洞：`ResponseCache.put` **拒写空正文**（那是对的——缓存下来
    只会让重试每次命中同一个空壳，各请求配置读回自己那份空的），于是空响应被 `evict`
    之后盘上一个字都不剩，「那条推理通道里到底装了什么」只能拿 token 体积去猜。
    ⇒ 另落一份留证：留证与缓存是两件事，缓存会被**读回来当答案用**，留证只给人看。

    ⚠ 留证跟**书**走不跟服务走（`<书目录>/_answer_lost/`）：那份 `reasoning` 里装的是
    这本书的正文——答案本来就该写进 `content`，只是走错了通道。删书即删证据。
    ⚠ 指针存**相对书目录**的路径：绝对路径换台机器（或换个 Output 根）就断。
    ⚠ token 数不抄进审计行——那归成本账那一行（同一个 `attempt_id`），
      一个数不该有第二个声称拥有它的地方；留证文件里另存供应商原样的 `usage`。
    """
    ctx, d = _ledgered_ctx()
    t = FakeTransport(responder=lambda _r, i: LOST if i == 0 else OK_JSON)

    _client(t, ctx).ask_json('sys', 'usr', model_cls=Resp, purpose='p')

    rows = [r for r in _rows(d / AUDIT_REL[-1]) if r['kind'] == 'answer_lost']
    assert len(rows) == 1, '一次丢失记一行'
    row = rows[0]
    assert (row['finish_reason'], row['content_chars']) == ('stop', 0)
    assert row['reasoning_chars'] == len(LOST.reasoning)
    assert (row['source'], row['json_mode'],
            row['purpose']) == ('wire', JSON_OBJECT_MODE, 'p')
    assert row['dump_error'] is None
    assert row['dump'] == f'{ANSWER_LOST_DIRNAME}/{row["attempt_id"]}.json'
    # 正文一个字都不进账——它只在留证文件里。
    assert LOST.reasoning not in json.dumps(row, ensure_ascii=False)
    # 同一次拒绝仍然有它那条 `gate_decision`：两行各答一个问题，不重复。
    gates = [r for r in _rows(d / AUDIT_REL[-1])
             if r['kind'] == 'gate_decision' and r['decision'] == 'rejected']
    assert [g['gate'] for g in gates] == ['answer_lost']
    assert gates[0]['attempt_id'] == row['attempt_id']

    kept = json.loads((ctx.novel_dir / row['dump']).read_text(encoding='utf-8'))
    assert kept['reasoning'] == LOST.reasoning and kept['content'] == ''
    # 供应商原样的 usage 是这一类的**服务端自证**：答案整份记在了推理侧。
    assert kept['usage'] == LOST.usage
    assert (kept['attempt_id'], kept['purpose']) == (row['attempt_id'], 'p')
    assert (kept['sid'], kept['node']) == (ctx.sid, ctx.node)


def test_a_keepsake_that_cannot_be_written_does_not_kill_the_paid_call(monkeypatch):
    """⭐ 留证写不成 ⇒ **证据缺席，不是调用失败。**

    盘满 / 路径超长（Windows `MAX_PATH`，本仓踩过）/ 权限不对——任何一种都不该让
    一次**已经付过钱**、且马上就要正常重采的调用抛在诊断这一步。
    ⚠ 但它也不许**静默**：失败原因要留在审计行上，否则「这次为什么没有留证」无解。
    """
    ctx, d = _ledgered_ctx()
    blocker = ctx.novel_dir / '这是个文件不是目录'
    blocker.write_text('x', encoding='utf-8')
    monkeypatch.setattr(keepsake_mod.paths, 'answer_lost_path',
                        lambda _dir, aid: blocker / f'{aid}.json')
    t = FakeTransport(responder=lambda _r, i: LOST if i == 0 else OK_JSON)

    got = _client(t, ctx).ask_json('sys', 'usr', model_cls=Resp, purpose='p')

    assert got.voice == {'a': 1}, '留证失败不该影响这一次调用的结果'
    row, = [r for r in _rows(d / AUDIT_REL[-1]) if r['kind'] == 'answer_lost']
    assert row['dump'] is None
    assert row['dump_error'] and 'Error' in row['dump_error']


class Beat(BaseModel):
    role: Literal['R_SETUP', 'R_PAYOFF']


class Planlike(BaseModel):
    """顶层两个必需键 + 一个**可选**的逐项列表——`require` 只派生前两个。"""
    voice: dict = Field(...)
    surface_habits: list[str] = Field(...)
    beats: list[Beat] = Field(default_factory=list)


#: 模型编出来的越界枚举值。**它一个字都不许出现在审计账里。**
LEAKY = 'R_模型自己编的值'


def test_a_schema_rejection_says_which_field_and_never_leaks_the_body():
    """⭐⭐ **`gate_decision` 要说得出「拒在哪个字段」，而且不带模型正文。**

    ⚠⚠ 它补的洞是 2026-08-22 现场咬过的那一口：拒因只进 stdout 事件，而 stdout
    **随进程一起没**——那次整跑被超时杀掉之后，账上只剩 `gate: schema` 四个字，
    两次拒到底拒在哪个字段再也查不出来（历史见 `docs/BACKEND_CONTRACT.md` §十 2026-08-22）。
    ⇒ 能机器数的那一半必须落账。

    ⚠ **下标压成 `[]`**：`beats.0.role` 与 `beats.1.role` 是同一个字段的两次，
    分开记就答不了「是不是同一种错」——而那正是这份摘要唯一的用途。
    ⚠⚠ **只带 `loc` + `type` + 计数。** `input` 是模型写的正文片段，`msg` 又会把它
    插值进去（`literal_error` 的 msg 里就带着 `input_value=...`）；而这份账与产物
    同一棵目录树，正文外发边界管的是同一批字节。
    """
    ctx, d = _ledgered_ctx()
    bad = ChatResponse(
        content=json.dumps({'voice': {'a': 1}, 'surface_habits': ['h'],
                            'beats': [{'role': LEAKY}, {'role': LEAKY}]},
                           ensure_ascii=False),
        finish_reason='stop')
    good = ChatResponse(
        content=json.dumps({'voice': {'a': 1}, 'surface_habits': ['h'],
                            'beats': [{'role': 'R_SETUP'}]}, ensure_ascii=False),
        finish_reason='stop')
    t = FakeTransport(responder=lambda _r, i: bad if i == 0 else good)

    _client(t, ctx).ask_json('sys', 'usr', model_cls=Planlike, purpose='p')

    rows = [r for r in _rows(d / AUDIT_REL[-1]) if r['kind'] == 'gate_decision']
    rejected = [r for r in rows if r['decision'] == 'rejected']
    assert [r['gate'] for r in rejected] == ['schema']
    detail = rejected[0]['reject_detail']
    # 两条同字段的错去重成一格、计数 2；下标压成 `[]`。
    assert detail == [{'loc': 'beats[].role', 'type': 'literal_error', 'count': 2}]
    # ⚠ 承重：模型写的那个值一个字都不许进账。
    assert LEAKY not in json.dumps(rows, ensure_ascii=False)
    # 过了的那一行没有拒因——写个空列表会让统计多一格。
    assert [r['reject_detail'] for r in rows if r['decision'] == 'accepted'] == [None]


def test_a_truncated_response_says_it_broke_at_the_end_not_in_the_middle():
    """⭐ `parse` 那一支要分得开**「被截断」**与**「中间就坏了」**。

    两类的处置不同：前者不做同配置重采、直接进入 Pro 同配置兜底；
    后者先同配置重采。⚠ 只取解析器自己那句固定措辞与位置，不取正文。

    ⚠⚠ **这一条钉的是「`truncated` 不许从 `pos` 推」。** 本例正是最典型的截断
    （`finish_reason='length'`、字符串写到一半就没了），而解析器给的
    `pos=20` 指向那个字符串的**起点**、文档长 32 ⇒ 任何「pos 是不是贴着末尾」
    的启发式都会把它判成「中间坏的」，然后把它推去做同档重采（截断唯一不该重采的那一类）。
    """
    ctx, d = _ledgered_ctx()
    cut = ChatResponse(content='{"voice": {"a": 1}, "surface_hab',
                       finish_reason='length')
    t = FakeTransport(responder=lambda _r, i: cut if i == 0 else OK_JSON)

    _client(t, ctx).ask_json('sys', 'usr', model_cls=Resp, purpose='p')

    rejected = [r for r in _rows(d / AUDIT_REL[-1])
                if r['kind'] == 'gate_decision' and r['decision'] == 'rejected']
    assert [r['gate'] for r in rejected] == ['parse']
    one, = rejected[0]['reject_detail']
    assert (one['type'], one['truncated']) == ('json_decode', True)
    assert one['doc_chars'] == len(cut.content) and isinstance(one['error'], str)
    # `pos` 留着答「坏在哪」，但它指向字符串起点、不是末尾——别拿它判截断。
    assert one['pos'] < one['doc_chars'] - 1
    assert cut.content not in json.dumps(rejected, ensure_ascii=False)


def test_a_missing_required_key_is_named_in_the_account():
    """⭐ 空壳闸也说得出**缺的是哪个键**——键名来自 `model_cls` 的派生，不是模型正文。"""
    ctx, d = _ledgered_ctx()
    shell = ChatResponse(content=json.dumps({'voice': {'a': 1}}),
                         finish_reason='stop')
    t = FakeTransport(responder=lambda _r, i: shell if i == 0 else OK_JSON)

    _client(t, ctx).ask_json('sys', 'usr', model_cls=Resp, purpose='p')

    rejected = [r for r in _rows(d / AUDIT_REL[-1])
                if r['kind'] == 'gate_decision' and r['decision'] == 'rejected']
    assert rejected[0]['gate'] == 'require'
    assert rejected[0]['reject_detail'] == [
        {'loc': 'surface_habits', 'type': 'missing_or_empty', 'count': 1}]
