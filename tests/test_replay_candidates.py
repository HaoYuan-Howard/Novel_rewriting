"""CS-H H0：`tools/replay_candidates.py`——已购候选的只读重放。

根由真实 producer 造出（Stage1 源章 → Stage2 观察 → Preserve → Design 两轮：首轮称谓泄漏、
次轮合格，由 fake transport 按序给出）；重放读的是**盘上**的工作单元账与响应 blob，不是内存里的
wire。工具是读数器不是判官（R-13.6）：只读、不写盘、不发请求，「记录（当时输入）」与「重放
（当前输入）」并排给出，不一致时不断言缺陷。
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from novel.llm.paid_requests import DurableAnswer, digest, format_json_path
from novel.llm.transport import ChatResponse
from novel.stages.chain import (
    ChainContext, derive_total_length_budget, run_body_design, run_preserve, run_workflow,
)
from tests.str06_facilities import json_ask, text_ask
from tests.test_str06_body import _RoutingTransport, _chapters, _make_session
from tests.test_str06_candidate_preflight import _bad_design
from tests.test_str06_hook import _full_chain_wires
from tests.test_str06_workflow import _write_stage1
from novel.work_units import initialize_workflow
from tools import replay_candidates as rc

SID = 'run'


class _DesignSequence(_RoutingTransport):
    """Design 按序给出预设回答（最后一份重复）；其余 owner 沿用路由。"""

    def __init__(self, wires, designs):
        super().__init__(wires)
        self.designs = list(designs)

    def send(self, request):
        if self._owner(request) != 'design':
            return super().send(request)
        self.calls.append('design')
        wire = self.designs.pop(0) if len(self.designs) > 1 else self.designs[0]
        return ChatResponse(content=json.dumps(wire, ensure_ascii=False), finish_reason='stop',
                            usage={'prompt_tokens': 111, 'completion_tokens': 222,
                                   'reasoning_tokens': 33}, model='fake')


def _manifest(base: Path) -> dict[str, str]:
    """整根逐字节指纹：只读重放前后必须逐字相同（含成本账与单元账）。"""
    return {p.relative_to(base).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(base.rglob('*')) if p.is_file()}


def _build_root(tmp_path, *, designs=None, through_design=True):
    """Stage1＋Stage2＋Preserve（＋Design）；返回 (base, repo, ctx)。"""
    chapters = _chapters()
    wires = _full_chain_wires()
    if designs is None:
        bad, good = deepcopy(wires['design']), deepcopy(wires['design'])
        _bad_design(bad)
        designs = [bad, good]
    transport = _DesignSequence(wires, designs)
    repo, session, _ = _make_session(tmp_path, wires, transport=transport)
    base = repo.root.parent
    initialize_workflow(base)  # 清单先于书目录里的 stageN 产物（真实产品同序）
    _write_stage1(base, SID, chapters=chapters, hook='')
    (base / SID / 'stage1' / 'hook_copy.json').unlink()  # 源书无引子：缺失按空字节参与身份
    budget = derive_total_length_budget(chapters)

    def no_body(*_args):
        raise AssertionError('重放测试不调用正文写手')

    ctx = ChainContext(repository=repo, ask=json_ask(session), ask_text=text_ask(session),
                       ask_body=no_body, chapters=chapters, budget=budget, digest=digest)
    run_workflow(repository=repo, chapters=chapters, ask=ctx.ask, ask_text=ctx.ask_text,
                 ask_body=ctx.ask_body, budget=budget, through='stage2')
    run_preserve(ctx)
    if through_design:
        run_body_design(ctx)
    return base, repo, ctx


# ─── 纯函数 ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('path', [(), ('scenes',), ('scenes', 1, 'beats', 0), ('a', 10, 'b'), ('x', 0)])
def test_parse_json_path_is_the_inverse_of_format_json_path(path):
    # H1b 起路径解析与候选合并住在 `novel/stages/candidates.py`（生产与只读重放
    # 共用同一把尺，不为工具保留第二份实现）。
    from novel.stages.candidates import parse_json_path
    assert parse_json_path(format_json_path(path)) == path


def test_error_code_groups_structured_bracketed_and_plain_diagnostics():
    from novel.core.validation import ValidationIssue
    issue = ValidationIssue('文案不参与分组', code='fact_lock_outside_obligation',
                            location=('lock', 'kf_1'), owner='design')
    assert rc.error_code(issue) == 'fact_lock_outside_obligation'
    assert rc.error_code('positive_material_leak[ch01/book_style@120]：未释放专名字面') \
        == 'positive_material_leak'
    assert rc.error_code('M4 事实锁 kf_1 的承载拍不属于必保义务 ob3') == 'M4 事实锁 kf_1 的承载拍不属于必保义务 ob3'


def _answer(content: str) -> DurableAnswer:
    return DurableAnswer(owner='design', request_sha256='a' * 64, sid=SID, unit_id='b' * 64,
                         turn_id='t', ordinal=2, attempt_id='x', response_sha256='c' * 64,
                         content=content, reasoning=None, finish_reason='stop', usage=None,
                         model='fake', cached=True, attempts_used=1, attempts_max=1)


def test_patch_round_merges_onto_the_previous_candidate_and_never_guesses_a_base():
    from novel.stages.candidates import decode_round
    previous = {'protocol_version': 'v8', 'scenes': [{'k': 1}, {'k': 2}], 'tail': 'keep'}
    patch = json.dumps({'patches': [{'path': 'scenes[1]', 'value': {'k': 99}}]})
    shape, wire, _, note = decode_round(_answer(patch), previous)
    assert shape == 'patch' and wire['scenes'] == [{'k': 1}, {'k': 99}] and wire['tail'] == 'keep'
    assert 'scenes[1]' in note and previous['scenes'][1] == {'k': 2}, '合并不得改写前一份候选'
    shape, wire, _, note = decode_round(_answer(patch), None)
    assert (shape, wire) == ('undecodable', None) and '缺基底' in note
    shape, wire, _, note = decode_round(_answer(json.dumps(
        {'patches': [{'path': 'nope[3]', 'value': 1}]})), previous)
    assert (shape, wire) == ('undecodable', None), '路径在基底上不存在不得补造'
    shape, wire, _, _ = decode_round(_answer(json.dumps({'protocol_version': 'v8', 'scenes': []})), previous)
    assert shape == 'full' and wire['scenes'] == []


def test_read_only_repository_refuses_every_write(tmp_path):
    repo = rc._ReadOnlyRepository(tmp_path)
    for call in (lambda: repo.write('runtime/x.json', {}), lambda: repo.publish(()),
                 lambda: repo.write_text('runtime/x.txt', ''), lambda: repo.write_invalid('a', {}, problems=()),
                 lambda: repo.write_evidence_bytes('runtime/revisions/evidence/x.json', b'')):
        with pytest.raises(RuntimeError, match='只读重放'):
            call()


# ─── 真实 producer 造根 → 重放盘上的已购回答 ─────────────────────────────────

def test_replay_reads_purchased_design_rounds_against_current_inputs(tmp_path):
    base, _, _ = _build_root(tmp_path)
    report = rc.replay(base, SID)
    design = report['owners']['design']
    assert design['status'] == 'ran' and design['node'] == 'str06.stage4.body_design'
    first, second = design['rounds']
    # 首轮：整份回答，称谓泄漏被当前验收拒绝；记录与重放同为未通过
    assert (first['shape'], first['replay']['passed'], first['replay']['reuse']) == ('full', False, 'failed')
    assert first['replay']['codes'].get('positive_material_leak', 0) >= 2
    assert first['recorded'] == {'accepted': False, 'parse': 'failed', 'errors': first['recorded']['errors']}
    # CS-H H2 起接受记录里的错误按根因聚合成任务（同一规则码的多处命中＝一个任务，附受
    # 影响位置）：重放按条数计的两处泄漏，在记录里是一条任务——记录数＝根因数。
    assert first['recorded']['errors'] == len(first['replay']['codes']) == 1
    # 次轮：完整预检全绿——当前输入下可零调用采用；用量读自账内回答值
    assert second['replay']['passed'] and second['replay']['reuse'] == 'green'
    assert {c['name']: c['status'] for c in second['replay']['checks']}['writer_materials'] == 'passed'
    assert second['recorded']['accepted'] is True
    assert second['usage'] == {'prompt_tokens': 111, 'completion_tokens': 222, 'reasoning_tokens': 33}
    summary = design['summary']
    assert (summary['green'], summary['failed'], summary['unknown']) == ([2], [1], [])
    assert summary['recorded_accepted_but_replay_failed'] == []
    assert summary['usage']['completion_tokens'] == 444 and summary['usage']['completion_tokens_unknown'] == 0
    # 重放用的章数组／预算／篇幅带与原运行身份吻合（run_workflow 记的 input_sha256）
    assert report['inputs']['input_identity'] == 'match'
    assert report['inputs']['chapter_count'] == 2


def test_replay_never_writes_the_root(tmp_path):
    """写盘、发请求、改账三条同一道断言：整根逐字节指纹前后相同。"""
    base, _, _ = _build_root(tmp_path)
    before = _manifest(base)
    assert any(rel.endswith('ledger.jsonl') for rel in before) and len(before) > 10
    rc.replay(base, SID)
    rc.main([str(base), '--sid', SID])
    assert _manifest(base) == before


def test_cli_prints_text_and_machine_readable_json(tmp_path, capsys):
    base, _, _ = _build_root(tmp_path)
    assert rc.main([str(base)]) == 0, '单书输出根不必显式 --sid'
    text = capsys.readouterr().out
    assert '第1轮 整份' in text and '第2轮 整份' in text
    assert '完整预检全绿——当前输入下可零调用采用' in text and '输入身份与原运行吻合' in text
    assert rc.main([str(base), '--sid', SID, '--json']) == 0
    # CLI 缺省＝全部已接入 owner（H3d 起 preserve、H4 起 associate）。
    assert json.loads(capsys.readouterr().out) == rc.replay(
        base, SID, owners=('design', 'preserve', 'associate'))


def test_input_identity_mismatch_is_reported_not_hidden(tmp_path, capsys):
    """Stage1 与原运行不一致时，重放照常运行但在报告头单独标出口径存疑。"""
    base, _, _ = _build_root(tmp_path)
    chapters = _chapters() + [{'chapter_ordinal': 3, 'text': '补一章。' * 400}]
    _write_stage1(base, SID, chapters=chapters, hook='')
    (base / SID / 'stage1' / 'hook_copy.json').unlink()
    report = rc.replay(base, SID)
    assert report['inputs']['input_identity'] == 'mismatch'
    assert report['owners']['design']['status'] == 'ran'
    assert '不吻合' in rc.render(report)


def test_unpublished_upstream_is_not_run_rather_than_failed(tmp_path):
    base, repo, _ = _build_root(tmp_path)
    repo.path('runtime/preserve_checkpoint.json').unlink()
    entry = rc.replay(base, SID)['owners']['design']
    assert entry['status'] == 'not_run' and 'preserve_checkpoint.json' in entry['reason']
    assert 'rounds' not in entry, '输入读不到时不得报告任何轮的通过或失败'


def test_owner_without_purchase_or_replayer_is_stated_not_skipped(tmp_path):
    base, _, _ = _build_root(tmp_path, through_design=False)
    report = rc.replay(base, SID, owners=('design', 'preserve', 'associate', 'naming'))
    assert report['owners']['design']['status'] == 'no_unit'
    # CS-H H3d：preserve 重放已接入——本根有已购轮 ⇒ ran；H4 起 associate 同样
    # 接入（本根已购 v3 空 wire）⇒ ran；未接入的例用 naming（H5 余项）。
    assert report['owners']['preserve']['status'] == 'ran'
    assert report['owners']['associate']['status'] == 'ran'
    assert report['owners']['naming']['status'] == 'not_wired'
    assert 'naming' in report['owners']['naming']['reason']
    rendered = rc.render(report)
    assert 'no_unit' in rendered and 'not_wired' in rendered


def test_missing_stage1_is_not_run_not_a_crash(tmp_path):
    base, _, _ = _build_root(tmp_path)
    (base / SID / 'stage1' / 'main_text.json').unlink()
    report = rc.replay(base, SID)
    assert report['inputs'] == {}
    assert report['owners']['design']['status'] == 'not_run'
    assert 'Stage1' in report['owners']['design']['reason']
