#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_api_edits.py —— 人工写回三条路由的**行为**（真起 CLI 子进程）。

【四类行为】（BE-03）：成功 / 上游未运行（200+failed，不误发 5xx）/
产物损坏（200+failed，日志可读）/ 参数不合法（422 载体层）。
【钱】三条全是 0 成本节点（`--apply-edit` / approve 均在 FREE 面）——
`no_api_key` 把钥匙拿走，任何一条走到真发请求的路都当场现形。

⚠ 遗留 `mkdtemp()` 由 conftest 收进受管根；新增用例使用 `tmp_path`。**原语料一个字节都不许动**。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import warnings
from pathlib import Path
from types import SimpleNamespace

import pytest

fastapi = pytest.importorskip(
    'fastapi', reason='没装 fastapi ⇒ 这一批**没在查**，不是「查过没事」')
from fastapi.testclient import TestClient          # noqa: E402

from contracts.invariants import APPROVAL_GATES   # noqa: E402
from contracts.schema.api import DraftEditPreview # noqa: E402
from novel.api.app import UI_HEADER, create_app    # noqa: E402
from novel.run import RunContext                    # noqa: E402
from novel.stages.stage7 import assemble, rename    # noqa: E402
from tests import corpus, sandbox                  # noqa: E402

INPUTS = ('stage0/revisions.json',
          'stage1/main_text.json', 'stage1/hook_copy.json',
          'stage2/global_grounding.json', 'stage2/chapter_blueprints.json',
          'stage4/plan_chapters.json', 'stage4/writer_context.json',
          'stage6/hook.json', 'stage7/name_map.json')


def books() -> list[str]:
    out = []
    for book in sorted({a.book_dir for a in corpus.iter_artifacts()
                        if a.rel == 'stage7/name_map.json'}):
        plan = sandbox.cut(book, 'stage4/plan_chapters.json')
        if (plan is not None and '_provenance' in plan
                and (corpus.corpus_root() / book / 'stage5' / 'ch01.json').is_file()):
            out.append(book)
    return out


def _stage5_rels(book: str) -> tuple[str, ...]:
    return tuple(sorted(
        a.rel for a in corpus.iter_artifacts()
        if a.book_dir == book and a.rel.startswith('stage5/')
        and '_rounds' not in a.rel and a.rel.endswith('.json')
        and (Path(a.rel).parent == Path('stage5'))))


def _stage6_semantic_rels(book: str) -> tuple[str, ...]:
    return tuple(sorted(
        a.rel for a in corpus.iter_artifacts()
        if a.book_dir == book and a.rel.startswith('stage6/semantic_')
        and a.rel.removeprefix('stage6/semantic_').removesuffix('.json').isdigit()))


@pytest.fixture(autouse=True)
def no_api_key(monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)


#: 本组真正要修改第 1 章；不用层级前缀把 trim/review 混进 freshness 前提。
_DRAFT_RELS = ('stage5/ch01.json',)


def _corpus_params():
    current = books()
    if current:
        # ⚠ 取**成稿链条相对上游是当前**的那一本，不是排序第一的那一本：本组多数用例
        #   要经 CLI 子进程改产物，成稿过期时那个子进程 exit 2（`UpstreamStale`），
        #   HTTP 层看到的是「作业失败」，断言红在退出码上而不是它要验的那件事。
        return [pytest.param(corpus.first_current_book(current, *_DRAFT_RELS) or current[0],
                             marks=pytest.mark.corpus)]
    return [pytest.param(
        None,
        marks=(pytest.mark.corpus, pytest.mark.skip(
            reason='盘上没有 API 编辑语料——这些集成用例没在查')),
        id='no-corpus',
    )]


@pytest.fixture(params=_corpus_params(), ids=lambda b: b[:10] if b else 'no-corpus')
def box(request):
    book = request.param
    # ⚠ `sandbox.edits_of(book)`：人工决策集是**必要的可选项**。缺 `stage2/global_edit.json`
    #   ⇒ `stage2/global_grounding.json` 判 `unknown/ghost` ⇒ 审批凭据 `s2` 填不出
    #   ⇒ 由 `_GATE_REQUIREMENTS` 级联堵死 `s4`→`s7`，而报出来的是「预置审批链失败」，
    #   与 `global_edit` 三个字毫无字面关系。
    s = sandbox.Sandbox(
        book,
        INPUTS + sandbox.edits_of(book) + _stage5_rels(book) + _stage6_semantic_rels(book))
    corpus_book = corpus.corpus_root() / book
    for rel in ('stage0/clean_text.txt',):
        source = corpus_book / rel
        assert source.is_file(), f'{book} 少了 {rel}，审批 freshness 沙箱无法建立'
        target = s.dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    yield s
    s.close()


@pytest.fixture
def client(box):
    with TestClient(create_app(box.root)) as c:
        c.headers.update({UI_HEADER: '1'})
        yield c


@pytest.fixture
def validation_client(tmp_path):
    """只测 HTTP 载体 schema；不得借真语料把 422 回归排出默认套件。"""
    with TestClient(create_app(tmp_path)) as c:
        c.headers.update({UI_HEADER: '1'})
        yield c


def _edits_dir(box):
    return box.root / '_service' / 'edits'


def _draft_apply_body(client, box, ordinal: int, segments: list[dict]) -> dict:
    preview = client.post(
        f'/api/novels/{box.sid}/review/{ordinal}/edit/preview',
        json={'segments': segments})
    assert preview.status_code == 200, preview.text[:500]
    projection = preview.json()
    return {
        'segments': segments,
        'actor': 'api-test-editor',
        'reason': '逐项核对只读预览后提交本轮人工改稿',
        'current_chapter_sha256': projection['current_chapter_sha256'],
        'preview_subject_sha256': projection['preview_subject_sha256'],
        'confirmation': projection['required_confirmation'],
    }


def _dummy_draft_apply_body(segments: list[dict]) -> dict:
    """只用于让请求进入 CLI 后验证缺上游/越界终态；摘要绝不伪装可提交。"""
    return {
        'segments': segments,
        'actor': 'api-test-editor',
        'reason': '验证 CLI 关闭式失败',
        'current_chapter_sha256': 'a' * 64,
        'preview_subject_sha256': 'b' * 64,
        'confirmation': 'formal',
    }


def _stamp_chain(box, upto: str) -> None:
    """预置审批检查单到 `upto`（含）——模拟一本正常走到中后段的书。

    审批文档是 `_service/` 的服务状态（不是产物），测试直接落一份合法 doc
    是在摆前置事实，不是绕守卫；`approve_gate` 的链式约束由 approvals 自己的测试守。
    """
    from novel.orchestrator import approvals

    assert upto in APPROVAL_GATES, f'不是现役审批闸：{upto}'
    upto_index = APPROVAL_GATES.index(upto)
    required_gates = APPROVAL_GATES[:upto_index + 1]
    # Stage2 已读/接受入口已随语义审阅退役；backfill 只补可由产物确定的 gate，
    # 绝不再替 Commander 伪造阅读事实。
    # Explicit backfill creates the same typed/root/freshness proofs as the real
    # product path.  Then trim only the checklist gates beyond the precondition
    # this test needs; never invent legacy v1 booleans against today's files.
    filled = approvals.backfill_from_disk(box.root, box.sid)
    assert all(filled.approvals.gate[gate] for gate in required_gates)
    record = approvals._read_current(box.root, box.sid)
    gates = dict(record.gate)
    proofs = dict(record.gate_proofs)
    for gate in APPROVAL_GATES[upto_index + 1:]:
        gates[gate] = False
        proofs[gate] = None
    approvals._write(box.root, record.model_copy(update={
        'gate': gates, 'gate_proofs': proofs,
    }))


def test_stamp_chain_follows_current_gate_order_without_touching_stage3(monkeypatch):
    from novel.orchestrator import approvals

    current_gates = {gate: True for gate in APPROVAL_GATES}
    record = approvals._fresh_record('book-1').model_copy(update={
        'gate': current_gates,
        'gate_proofs': {gate: f'proof-{gate}' for gate in APPROVAL_GATES},
    })
    written = []
    monkeypatch.setattr(
        approvals, 'backfill_from_disk',
        lambda _root, _sid: SimpleNamespace(
            approvals=SimpleNamespace(gate=current_gates)))
    monkeypatch.setattr(approvals, '_read_current', lambda _root, _sid: record)
    monkeypatch.setattr(approvals, '_write', lambda _root, updated: written.append(updated))

    _stamp_chain(SimpleNamespace(root=Path('unused'), sid='book-1'), 's5')

    assert len(written) == 1
    assert written[0].gate == {
        's0': True, 's1': True, 's4': True,
        's5': True, 's6': False, 's7': False,
    }
    assert 's3' not in written[0].gate
    assert written[0].gate_proofs['s6'] is None
    assert written[0].gate_proofs['s7'] is None

def test_stamp_writes_normally_through_the_short_admission_scope(tmp_path, monkeypatch):
    from novel.api import approval_stamp
    from novel.orchestrator import jobs

    stamped = []
    monkeypatch.setattr(approval_stamp.approvals, 'approve_gate',
                        lambda base, sid, gate: stamped.append((base, sid, gate)))
    # stage0 approve 路由已退役（2026-09-15 全自动裁决）；helper 仅剩遗留盘面
    # 复刻与自身 admission-scope 语义的职责。
    approval_stamp.stamp_approval_after_landed(str(tmp_path), '422', 's0')
    assert stamped == [(str(tmp_path), '422', 's0')]
    assert jobs.REGISTRY.conflicting_holder('422') is None


@pytest.mark.parametrize('conflict_kind', ['live_agent', 'durable_orphan'])
def test_landed_job_record_survives_a_stamp_writer_conflict(
        validation_client, tmp_path, monkeypatch, conflict_kind):
    """定稿后不再写第二份服务章；writer 抢锁不产生补录警告。"""
    from novel.api import approval_stamp
    from novel.api.routes import edits as route
    from novel.orchestrator import admission

    key = route.jobs.agent_lock_key('422')
    holder = 'agent-between-job-and-stamp'
    parent_run_id = 'orphan-between-job-and-stamp'
    armed = False

    def landed_then_conflict(base, node, sid, flags, **_kwargs):
        nonlocal armed
        if conflict_kind == 'live_agent':
            route.jobs.AGENT_REGISTRY.acquire(key, holder)
        else:
            admission.open_gate(base, parent_run_id, sid)
        armed = True
        start = route.jobs.plan_start(node, sid, flags, base=base)
        return route.JobRecord(
            job_id=f'landed-before-{conflict_kind}', start=start,
            started=1, ended=2, exit_code=0, state='ok',
            steps_started=len(start.steps),
        )

    monkeypatch.setattr(route.jobs, 'run', landed_then_conflict)
    # 本条的主题是盖章竞态；改选重建链（真实 books 才会有盘面）在此是无关变量，
    # 显式隔离，不让它的环境性告警污染「无补录警告」断言。
    monkeypatch.setattr(route, '_enqueue_hook_rebuild', lambda *_args: None)
    monkeypatch.setattr(
        approval_stamp.approvals, 'approve_gate',
        lambda *_args: pytest.fail('同书 writer 冲突时不得进入审批写动作'))
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            response = validation_client.post(
                '/api/novels/422/stage6/approve',
                json={
                    'pick': 1,
                    'actor': 'api-stamp-race-test',
                    'reason': '确认已落 JobRecord 不被盖章竞态伪装成失败',
                    'review_token': 's6r4_' + 'b' * 64,
                },
            )
    finally:
        if armed and conflict_kind == 'live_agent':
            route.jobs.AGENT_REGISTRY.release(key, holder)
        elif armed:
            admission.close_gate(tmp_path, parent_run_id)
            admission.mark_drained(tmp_path, parent_run_id)

    assert response.status_code == 200, response.text
    assert response.json()['job_id'] == f'landed-before-{conflict_kind}'
    assert response.json()['state'] == 'ok'

    assert not [item for item in caught if issubclass(item.category, RuntimeWarning)]


# ───────────────────────── 一、章节改稿 ─────────────────────────


def test_draft_preview_route_returns_the_shared_typed_projection(
        validation_client, monkeypatch):
    """HTTP preview 直接消费生产评估，不上传临时稿、不启动 CLI 作业。"""
    from novel.api.routes import edits as route

    expected = DraftEditPreview.model_validate({
        'output_ordinal': 1,
        'current_chapter_sha256': 'a' * 64,
        'preview_subject_sha256': 'b' * 64,
        'normalized_segments': [{'slot': 's01', 'text': '归一后的正文。'}],
        'findings': [],
        'current_hard_findings': [],
        'introduced_hard_findings': [],
        'resolved_hard_findings': [],
        'required_confirmation': 'formal',
    })
    seen = []
    monkeypatch.setattr(
        route, 'RunContext',
        SimpleNamespace(open=lambda *args, **kwargs: SimpleNamespace()))
    monkeypatch.setattr(
        route.write, 'prepare_edit_preview',
        lambda _ctx, ordinal, response: (
            seen.append((ordinal, response))
            or SimpleNamespace(preview=lambda: expected)))
    monkeypatch.setattr(
        route.jobs, 'run',
        lambda *_args, **_kwargs: pytest.fail('只读 preview 不得启动 CLI 作业'))

    response = validation_client.post(
        '/api/novels/422/review/1/edit/preview',
        json={'segments': [{'slot': 's01', 'text': '归一前正文。'}]})

    assert response.status_code == 200
    assert response.json() == expected.model_dump(mode='json')
    assert len(seen) == 1 and seen[0][0] == 1


def test_draft_apply_route_forwards_the_complete_preview_bound_cli_argv(
        validation_client, tmp_path, monkeypatch):
    """HTTP 只组一次现役 CLI argv；双 SHA、身份与确认模式一格都不能漏。"""
    from novel.api.routes import edits as route

    upload = tmp_path / '_service' / 'edits' / '422' / 'ch01.json'
    seen = {}
    monkeypatch.setattr(
        route.uploads, 'save_draft_edit',
        lambda base, sid, ordinal, response: upload)
    monkeypatch.setattr(
        route.uploads, 'discard_edit_plan',
        lambda base, path: seen.setdefault('discarded', path))

    def fake_run(base, node, sid, flags, *, follow_check):
        seen.update(base=base, node=node, sid=sid, flags=list(flags),
                    follow_check=follow_check)
        start = route.jobs.plan_start(node, sid, flags, base=base)
        return route.JobRecord(
            job_id='job-stage5-edit-forwarding', start=start,
            started=1, ended=2, exit_code=0, state='ok',
            steps_started=len(start.steps),
        )

    monkeypatch.setattr(route.jobs, 'run', fake_run)
    body = {
        'segments': [{'slot': 's01', 'text': '归一候选。'}],
        'actor': 'editor',
        'reason': '核对完整预览主体后提交',
        'current_chapter_sha256': 'a' * 64,
        'preview_subject_sha256': 'b' * 64,
        'confirmation': 'working_draft',
    }
    response = validation_client.post(
        '/api/novels/422/review/1/edit', json=body)

    assert response.status_code == 200, response.text
    assert seen['node'] == 'stage5.write' and seen['sid'] == '422'
    assert seen['follow_check'] is False
    assert seen['flags'] == [
        '--apply-edit', str(upload),
        '--actor', 'editor',
        '--reason', '核对完整预览主体后提交',
        '--current-chapter-sha256', 'a' * 64,
        '--preview-subject-sha256', 'b' * 64,
        '--edit-confirmation', 'working_draft',
    ]
    assert seen['discarded'] == upload

def test_a_draft_edit_lands_as_a_new_human_round(client, box):
    """⭐ 成功路：人工稿落成新一轮——`origin=human`、round+1、旧轮进 `_rounds/`、
    一次性稿已清理。**与生成稿同一个判定落点**（仍有阻断级只报不拦）。"""
    rel = box.dir / 'stage5' / 'ch01.json'
    before = json.loads(rel.read_text(encoding='utf-8'))
    marker = '她把那句话咽了回去，转身出门。'
    segments = [dict(segment) for segment in before['segments']]
    # 不改变自然段数量；paragraph_budget_missed 是 apply 的绝对拒绝项，不能拿
    # 一个故意破坏段落预算的候选去测成功路。
    segments[-1]['text'] += marker
    reviews_before = sorted((box.dir / 'stage5').glob('review_*.json'))
    body = _draft_apply_body(client, box, 1, segments)

    r = client.post(f'/api/novels/{box.sid}/review/1/edit',
                    json=body)
    assert r.status_code == 200, r.text[:300]
    assert r.json()['state'] == 'ok', r.text[:300]

    after = json.loads(rel.read_text(encoding='utf-8'))
    assert marker in '\n\n'.join(segment['text'] for segment in after['segments'])
    assert after['_provenance']['origin'] == 'human'
    assert 'prompts' not in after['_provenance'] and 'model' not in after['_provenance'], \
        '人工稿记了 prompt/model——那是假账（RoundFreeProvenance 在结构上没有那两格）'
    assert int(after['_provenance']['round']) == int(
        before.get('_provenance', {}).get('round', 1)) + 1
    rounds = list((box.dir / 'stage5' / '_rounds').glob('ch01.r*.json'))
    assert rounds, '旧轮没有归档——覆盖前的那一份再也回不去了'
    assert not any(_edits_dir(box).rglob('*.json')), '一次性人工稿没清理'
    assert sorted((box.dir / 'stage5').glob('review_*.json')) == reviews_before, \
        '逐次 edit 不得自动追跑 Review；多次改完后只跑一次最终 Review'
    actions = [json.loads(line) for line in (
        box.root / '_service' / 'human_actions.jsonl'
    ).read_text(encoding='utf-8').splitlines()]
    assert [row['phase'] for row in actions] == ['prepared', 'committed']
    committed = actions[-1]
    assert committed['actor'] == body['actor']
    assert committed['reason'] == body['reason']
    assert committed['confirmation'] == body['confirmation']
    assert committed['request_id'] == actions[0]['request_id']
    assert committed['action_id'] == actions[0]['action_id']


def test_a_draft_edit_without_upstream_is_a_failed_job_not_a_5xx(client, box):
    """上游未运行：删 ctx ⇒ CLI 显式终态 ⇒ **200 + state=failed**（作业失败不是 HTTP 错误）。"""
    (box.dir / 'stage4' / 'writer_context.json').unlink()
    r = client.post(f'/api/novels/{box.sid}/review/1/edit',
                    json=_dummy_draft_apply_body([
                        {'slot': 's01', 'text': '改过的正文。'}]))
    assert r.status_code == 200, r.text[:300]
    assert r.json()['state'] == 'failed'
    assert not any(_edits_dir(box).rglob('*.json')), '失败的作业也要清理一次性稿'


def test_a_draft_edit_for_a_chapter_outside_the_plan_fails_loudly(client, box):
    """章号不在计划里：CLI `TerminalState` ⇒ failed，日志说得出为什么。"""
    r = client.post(f'/api/novels/{box.sid}/review/999/edit',
                    json=_dummy_draft_apply_body([
                        {'slot': 's01', 'text': '不存在的章。'}]))
    assert r.status_code == 200 and r.json()['state'] == 'failed'
    log = client.get(f'/api/jobs/{r.json()["job_id"]}/log').json()['text']
    assert '999' in log, '日志里连章号都没有——人无从知道错在哪'


@pytest.mark.parametrize('body,why', [
    ({'segments': []}, '空稿不是一次修改'),
    ({'segments': [
        {'slot': 's01', 'text': '甲。'}, {'slot': 's01', 'text': '乙。'}]}, 'slot 重复'),
    ({'mode': 'deep', 'segments': [{'slot': 's01', 'text': '正文'}]},
     '已退役 mode 字段必须被载体拒绝'),
    ({}, '缺 segments'),
])
def test_an_illegal_edit_body_is_422_from_the_carrier(validation_client, body, why):
    r = validation_client.post('/api/novels/422/review/1/edit', json=body)
    assert r.status_code == 422, why


# ───────────────────────── 二、文案定稿 ─────────────────────────

def _approve_body(box, pick: int) -> dict:
    from novel.stages.stage6 import hook as hook_node
    from novel.store import ArtifactStore

    ctx = RunContext.open(
        str(box.root), box.sid, 'stage6.approve', paid=False,
        write_ledger=False)
    pack = hook_node.approval_workpack(ArtifactStore(ctx))
    return {
        'pick': pick,
        'actor': 'api-test-editor',
        'reason': '逐项核对了当前候选与全书事实',
        'review_token': pack.review_token,
    }


def _approval_target(box) -> int:
    """挑一条生产的批准谓词真能收口的候选，返回它的 `pick`。

    ⚠ 自动放行门与判官退役（2026-09-15）后 `basis` 在类型上只剩
    `default_policy` / `human_selection`——**服务端不替人否决任何候选**。
    `run_approve` 唯一还会抛的候选级判据就是机械 HARD，这里**调它那把尺子**
    （`hook._sev_of`）而不是重拼一份 REGISTRY 查询。
    """
    from contracts.schema.stage6 import Hook
    from novel.stages.stage6 import hook as hook_node
    from novel.store import ArtifactStore

    ctx = RunContext.open(str(box.root), box.sid, 'stage6.hook', paid=False,
                          write_ledger=False)
    store = ArtifactStore(ctx)
    art, _raw = store.read_own(Hook, 'stage6/hook.json')
    refused: list[str] = []
    for pick, candidate in enumerate(art.candidates, 1):
        mechanical = sorted({
            item.rule for item in candidate.findings
            if hook_node._sev_of(item.rule) == 'hard'
        })
        if mechanical:
            refused.append(f'{pick}:mechanical={mechanical}')
            continue
        return pick
    raise AssertionError(
        '现役 corpus 没有一条可依法定稿的 Stage6 候选；不能用 skip 冒充 API 闭环：'
        + '；'.join(refused))


def test_picking_a_hook_candidate_approves_and_stamps_the_gate(client, box):
    """⭐ 一条 hash-bound 的人工改选能定稿，并在前置链完成时盖上 `s6`。

    ⚠ 判官退役后 `basis` 在类型上只剩 `human_selection`（人工改选）与
    `default_policy`（自动定稿）——**没有第二种依据可写**。
    ⚠ 退出码契约：`0/1` 都是「定稿**落了**」（`1` 只是报告仍有红行），
    `2/3` 才是没跑成。**盖章看落没落，红不红归 check 屏。**
    """
    pick = _approval_target(box)
    _stamp_chain(box, 's5')
    before = json.loads((box.dir / 'stage6' / 'hook.json').read_text(encoding='utf-8'))
    body = _approve_body(box, pick)
    r = client.post(f'/api/novels/{box.sid}/stage6/approve', json=body)
    assert r.status_code == 200, r.text[:300]
    assert r.json()['exit_code'] in (0, 1), r.text[:300]

    after = json.loads((box.dir / 'stage6' / 'hook.json').read_text(encoding='utf-8'))
    assert after['approved'] is not None
    assert after['approved']['slot'] == f'c{pick}'
    assert after['approved']['origin'] == 'human'
    resolution = after['approved']['resolution']
    assert resolution['basis'] == 'human_selection'
    assert resolution['actor'] == body['actor']
    assert resolution['reason'] == body['reason']
    assert resolution['subject_sha256'] == hashlib.sha256(
        before['candidates'][pick - 1]['text'].encode('utf-8')).hexdigest()
    assert resolution['review_identity_sha256']
    doc = client.get(f'/api/novels/{box.sid}/approvals').json()
    assert doc['gate']['s6'] is True


def test_an_approve_before_the_chain_still_lands_in_the_artifact(client, box):
    """⭐ 审批链没到位（中途接手的书）⇒ 定稿**照样落**（产物是真相），
    检查单那一格保持未盖——**不把检查单的顺序翻成定稿的失败**。"""
    pick = _approval_target(box)
    body = _approve_body(box, pick)
    r = client.post(f'/api/novels/{box.sid}/stage6/approve', json=body)
    assert r.status_code == 200, r.text[:300]
    assert r.json()['exit_code'] in (0, 1)
    after = json.loads((box.dir / 'stage6' / 'hook.json').read_text(encoding='utf-8'))
    assert after['approved'] is not None, '定稿没落——链只是检查单，不该拦产物'
    assert after['approved']['resolution']['basis'] == 'human_selection'
    assert after['approved']['resolution']['review_identity_sha256']
    doc = client.get(f'/api/novels/{box.sid}/approvals').json()
    assert doc['gate']['s6'] is False, '链没到位却盖了章——前置约束被绕过了'


def test_an_out_of_range_pick_fails_without_stamping(client, box):
    """越界 pick = 显式终态（exit 2）——**没跑成**，一个脚印都不留。"""
    _stamp_chain(box, 's5')
    r = client.post(f'/api/novels/{box.sid}/stage6/approve', json=_approve_body(box, 99))
    assert r.status_code == 200 and r.json()['state'] == 'failed'
    assert r.json()['exit_code'] >= 2, '越界 pick 该是「没跑成」，不是「跑完了判定红」'
    doc = client.get(f'/api/novels/{box.sid}/approvals').json()
    assert doc['gate']['s6'] is False, '没跑成的定稿不许盖章'


def test_a_corrupt_hook_makes_the_approve_job_fail(client, box):
    """产物损坏：CLI 读不回 hook.json ⇒ failed（不是 5xx，也绝不盖章）。"""
    _stamp_chain(box, 's5')
    body = _approve_body(box, 1)
    (box.dir / 'stage6' / 'hook.json').write_text('这不是 JSON', encoding='utf-8')
    r = client.post(f'/api/novels/{box.sid}/stage6/approve', json=body)
    assert r.status_code == 200 and r.json()['state'] == 'failed'
    assert r.json()['exit_code'] >= 2
    assert client.get(f'/api/novels/{box.sid}/approvals').json()['gate']['s6'] is False


def test_a_zero_pick_is_422_from_the_carrier(validation_client):
    body = {
        'pick': 0,
        'actor': 'api-test-editor',
        'reason': 'exercise the carrier lower bound',
        'review_token': 's6r4_' + '0' * 64,
    }
    assert validation_client.post(
        '/api/novels/422/stage6/approve', json=body).status_code == 422


# ───────────────────────── 三、人名定稿 ─────────────────────────

def _name_map_auto_seal(box) -> dict:
    """批次 B4：rename --recheck 机械通过即 auto seal，盖上 s7。"""
    ctx = RunContext.open(
        str(box.root), box.sid, 'stage7.rename', paid=False, write_ledger=False)
    sealed, _report = rename.run_recheck(ctx)
    assert sealed.approved is not None
    assert sealed.approved.origin == 'auto'
    assert sealed.approved.actor == 'system:stage7-auto'
    after = json.loads((box.dir / 'stage7' / 'name_map.json').read_text(encoding='utf-8'))
    assert after['approved']['origin'] == 'auto'
    return after


def test_approving_the_name_map_stamps_s7_and_assembly_keeps_it_current(
    client, box, monkeypatch,
):
    """rename auto seal 盖 `s7`；正常组装只写派生交付账，不能撤销同一批准。"""
    from novel.stages.stage6 import semantic

    # ⚠ 本条经 `stage7.rename --recheck` auto seal 写回批准；映射一旦相对上游过期，
    #   写盘口后置条件当场 `ProvenanceRefused`。一份当前的映射是本条的**输入**。
    corpus.requires_current(box.book, 'stage7/name_map.json',
                            produced_by='stage7.rename --recheck', cost='0 成本')
    _stamp_chain(box, 's6')
    after = _name_map_auto_seal(box)
    assert after['approved']['review_identity_sha256']
    assert client.get(f'/api/novels/{box.sid}/approvals').json()['gate']['s7'] is True

    monkeypatch.setattr(semantic, 'require_approved_clear', lambda *_args: None)
    ctx = RunContext.open(
        str(box.root), box.sid, 'stage7.assemble', paid=False,
        write_ledger=False)
    _path, assembled, _report = assemble.run_assemble(ctx)
    assert assembled.book_txt is not None
    assert client.get(f'/api/novels/{box.sid}/approvals').json()['gate']['s7'] is True


def test_stage7_approve_http_endpoint_is_retired(validation_client):
    """批次 B4：POST /stage7/approve 已删；载体不再接受定稿请求体。"""
    assert validation_client.post(
        '/api/novels/422/stage7/approve', json={}).status_code == 405


def test_residual_adoption_api_only_accepts_explicit_high(validation_client):
    assert validation_client.post(
        '/api/novels/422/stage7/adopt-residual',
        json={'confidence': 'low'},
    ).status_code == 422

    response = validation_client.post(
        '/api/novels/422/stage7/adopt-residual',
        json={'confidence': 'high'},
    )
    assert response.status_code == 200
    assert response.json()['state'] == 'failed', (
        '空测试书没有 name_map，但合法请求必须真正抵达 0 成本作业而不是被载体误拒')


# ─────────────── 默认套件：CLI typed 定稿 → 只读公开审批投影 ───────────────

def _stage0_on_disk(book: Path, source: Path, source_bytes: bytes, clean_bytes: bytes) -> None:
    """stage0 权威 transform + 唯一 clean 投影（test_store 的最小本文件副本）。"""
    import hashlib
    from contracts.schema.stage0 import ExternalFileRef, Stage0Source
    from novel import prov
    from novel.stages.stage0.revisions import make_transform

    (book / 'stage0').mkdir(parents=True, exist_ok=True)
    (book / 'stage0' / 'clean_text.txt').write_bytes(clean_bytes)
    text = source_bytes.decode('utf-8')
    clean = clean_bytes.decode('utf-8')
    transform = make_transform(
        source=Stage0Source(
            source_ref=ExternalFileRef(
                kind='external_file', absolute_path=str(source.resolve())),
            raw_sha256=hashlib.sha256(source_bytes).hexdigest(),
            encoding='utf-8',
            normalized_source_sha1=prov.content_sha1(source_bytes),
            total_code_points=len(text),
        ),
        code='test-api-edits',
        clean_text=clean,
        removed=[],
    )
    rel = 'stage0/revisions.json'
    target = book / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    from contracts.schema import encode_artifact
    target.write_text(
        json.dumps(encode_artifact(rel, transform.model_dump(mode='json')),
                   ensure_ascii=False),
        encoding='utf-8', newline='',
    )


@pytest.fixture
def final_gate_book(tmp_path, monkeypatch):
    """真实 Store、CLI 定稿、workpack/proof 与 HTTP；隔离 Stage0～5 前置业务。

    两份候选是合成输入，归一/判据环境固定为空异常；本组不测生成质量。
    不替换最终 approval_is_current、freshness、gate 投影、定稿 producer 或 GET。
    """
    from contracts.schema.stage1 import HookCopy, MainText
    from contracts.schema.stage6 import Hook, Mech
    from contracts.schema.stage7 import NameMap, NameMapFreeProvenance
    from novel import prov
    from novel.cli import main as cli
    from novel.orchestrator import approvals
    from novel.stages.stage5 import closure
    from novel.stages.stage6 import hook
    from novel.store import ArtifactStore
    from tests.factories import TEXT_A, TEXT_B, TEXT_C
    from tests.factories import stage6_hook as _hook
    from novel.stages.stage1.partition import partition_text

    from novel.work_units import initialize_workflow
    initialize_workflow(tmp_path)
    sid = '866'
    book = tmp_path / f'定稿投影_{sid}'
    (book / 'stage0').mkdir(parents=True)
    clean = book / 'stage0/clean_text.txt'
    source = tmp_path / 'source.txt'
    source_bytes = '开篇悬念。\n\n1\n沈月来到门前。\n\n2\n她走进屋内。\n'.encode('utf-8')
    source.write_bytes(source_bytes)
    _stage0_on_disk(book, source, source_bytes, source_bytes)
    partitioned = partition_text(source_bytes.decode('utf-8'))
    stage1 = ArtifactStore(RunContext.open(
        tmp_path, sid, 'stage1.partition', paid=False, write_ledger=False))
    stage1.write('stage1/main_text.json', partitioned.main_text)
    stage1.write('stage1/hook_copy.json', partitioned.hook_copy)

    def hook_upstreams(_ctx, store):
        store.read(HookCopy, 'stage1/hook_copy.json')
        return SimpleNamespace(judged={}, upto=1)

    def rename_upstreams(_ctx, store):
        store.read(MainText, 'stage1/main_text.json')
        return SimpleNamespace(judged={}, upto=1)

    monkeypatch.setattr(hook, '_Upstreams', hook_upstreams)
    from tests.test_final_approval_workpacks import _stage6_materials
    monkeypatch.setattr(hook, '_approval_materials', lambda *_args: _stage6_materials())
    monkeypatch.setattr(hook, '_judge_artifact', lambda *_args: [])
    monkeypatch.setattr(hook, '_normalize_and_judge', lambda text, _up: (
        text, Mech(punct_fixed=0, quotes_fixed=0, paras_fixed=0), []))
    monkeypatch.setattr(rename, '_Upstreams', rename_upstreams)
    monkeypatch.setattr(rename, '_judge', lambda *_args: [])
    # Stage7 免费重判保持同一合成业务面；重写来源、批准继承与正式落盘仍走
    # producer，本组不再复制人物替换器的业务判据。
    def rebuild_name(ctx, _up, _model, _extra, provenance):
        current, _ = ArtifactStore(ctx).read_own(NameMap, rename.REL)
        return current.model_copy(update={'provenance': provenance}), [], None
    monkeypatch.setattr(rename, '_build', rebuild_name)

    # Stage6 prepare 是 Hook 定稿的新前置：经真实 producer + 免费独立接受建立。
    import json as _json

    from tests.test_stage6_prepare import direct_prepare

    prepare_ctx = RunContext.open(
        tmp_path, sid, 'stage6.prepare', paid=True, write_ledger=False)
    prepare_response = {'source_blurb': {
        'facts': [{'kind': 'supported', 'claim_id': 'hook_001',
                   'claim': '沈月来到门前。',
                   'evidence': {'chapter_ordinal': 1, 'start_para': 1, 'end_para': 1}}],
        'opening': {'kind': 'proposed', 'through_chapter': 1,
                    'rationale': '首章门前一场完整承接原钩子',
                    'boundary_evidence': {'chapter_ordinal': 1, 'start_para': 1,
                                          'end_para': 1}},
    }}
    prepare_result, _prepare_transport = direct_prepare(prepare_ctx, prepare_response)
    assert prepare_result is not None
    from novel.stages.stage6 import prepare as prepare_node
    prepare_store = ArtifactStore(RunContext.open(
        tmp_path, sid, 'stage6.prepare', paid=False, write_ledger=False))
    prepare_pack = prepare_node.approval_workpack(prepare_store)
    from contracts.schema.stage6_prepare import PrepareDecision
    prepare_node.submit_review(prepare_store.ctx, PrepareDecision(
        review_token=prepare_pack.state.review_token,
        source_sha256=prepare_pack.state.subject_sha256,
        decision='accept', actor='final-gate-fixture',
        reason='已核对原文与连续开篇范围', edit=None))
    assert prepare_node.run_check_only(RunContext.open(
        tmp_path, sid, 'stage6.prepare', paid=False,
        write_ledger=False)).passed

    ctx = RunContext.open(tmp_path, sid, 'stage6.hook', paid=True, write_ledger=False)
    store = ArtifactStore(ctx)
    store.read(HookCopy, 'stage1/hook_copy.json')
    candidate = _hook([TEXT_A, TEXT_B, TEXT_C])
    candidate = candidate.model_copy(update={'provenance': candidate.provenance.model_copy(update={
        'code': ctx.code, 'root_clean_sha1': ctx.root_clean_sha1, 'inputs': ctx.edges,
        'prompts': {'RULES': prov.fingerprint(prov.rules_fingerprint('stage6'))},
    })})
    store.write(hook.REL, candidate)

    ctx7 = RunContext.open(tmp_path, sid, 'stage7.rename', paid=False, write_ledger=False)
    store7 = ArtifactStore(ctx7)
    store7.read(MainText, 'stage1/main_text.json')
    name_map = NameMap(
        surname_map={'沈': '陆'}, characters=[{
            'char_id': 'char_001', 'old_canonical': '沈月', 'new_canonical': '陆月',
            'renamed': True, 'not_renamed_reason': None, 'aliases': [],
            'reason': '测试输入明确决定改姓。',
        }], extra_subs=[], subs={'沈月': '陆月'}, kept=[],
        applied={'chapters': {}, 'hook': {'counts': {}}}, residual=[], baselines={},
        approved=None, book_txt=None, judged={}, hook_sha1=None,
        hook_consumed={'present': False, 'candidates_sha1': prov.fingerprint('')},
        sources={'writer_context': {'state': 'fresh', 'detail': [], 'code': ctx7.code}},
        book=[], identity=None,
        _provenance=ctx7.free_provenance(
            NameMapFreeProvenance, origin='human', params={'max_tokens': 1024},
            prompts={'RULES': prov.rules_fingerprint('stage7')}))
    store7.write(rename.REL, name_map)
    assert store.freshness(hook.REL).state == store7.freshness(rename.REL).state == 'fresh'

    proof = approvals._ApprovalProof(
        root_clean_sha1=ctx.root_clean_sha1,
        artifacts={'stage0/clean_text.txt': hashlib.sha256(clean.read_bytes()).hexdigest()},
        decision_sha256='a' * 64)
    real_gate_proof = approvals._proof_for_gate
    monkeypatch.setattr(approvals, '_proof_for_gate', lambda root, gate, record: (
        real_gate_proof(root, gate, record) if gate in {'s6', 's7'} else proof))
    monkeypatch.setattr(approvals, '_proof_for_writing', lambda *_args: proof)
    monkeypatch.setattr(approvals, '_spawns', lambda *_args: {})
    monkeypatch.setattr(closure, 'require_stage5_closed', lambda *_args: None)
    structure = approvals._StoredStructureDecision(
        actor='fixture-prerequisite', reason='前置结构决定由本测试输入给定',
        subject_sha256='b' * 64, decided_at='2026-09-08T00:00:00Z', proof=proof,
        chapters=[{'output_ordinal': 1, 'decision': 'accept', 'reason': '已核对前置'}])
    closure_decision = approvals._StoredClosureDecision(
        actor='fixture-prerequisite', reason='前置闭合决定由本测试输入给定',
        workpack_sha256='c' * 64, review_sha256='d' * 64, decision_sha256='e' * 64,
        decided_at='2026-09-08T00:00:00Z', proof=proof)
    # Stage4/5 是本组给定的前置，不让无关的缺计划/Review 显示口否定已给定
    # 的 typed 前提；使用完整公开载体，最终两层仍现场读产物、重算 proof。
    from contracts.schema.api import StructureApprovalView, Stage5ClosureView, Stage5ClosureWorkpack
    public_structure = approvals._public_structure_decision(structure)
    monkeypatch.setattr(approvals, '_structure_view', lambda *_args: StructureApprovalView(
        state='accepted', current_subject_sha256=structure.subject_sha256,
        review_token=public_structure.review_token,
        current_decision=public_structure, history=[public_structure]))
    public_closure = approvals._public_closure_decision(closure_decision)
    closure_pack = Stage5ClosureWorkpack(
        review_seq=1, review_rel='stage5/review_1.json', review_sha256='d' * 64,
        planned_chapters=[1], missing_chapters=[],
        chapter_subjects=[{'output_ordinal': 1, 'chapter_sha256': 'f' * 64}],
        invalid_chapters=[], hard_findings=[], unresolved_trim_chapters=[],
        unresolved_trim_items=[], trim_subjects=[], wontfix_sha256=None, must_appear=[],
        workpack_sha256='c' * 64, review_token=public_closure.review_token)
    monkeypatch.setattr(approvals, '_closure_view', lambda *_args: Stage5ClosureView(
        state='closed', workpack=closure_pack, current_decision=public_closure,
        history=[public_closure]))
    raw = approvals._fresh_record(sid)
    raw = raw.model_copy(update={
        'structure_history': [structure], 'structure_decision': structure,
        'closure_history': [closure_decision], 'closure_decision': closure_decision,
        'gate': {gate: gate not in {'s6', 's7'} for gate in APPROVAL_GATES},
        'gate_proofs': {gate: None if gate in {'s6', 's7'} else proof for gate in APPROVAL_GATES},
        'writing': True, 'writing_proof': proof,
    })
    approvals._write(tmp_path, raw)
    return SimpleNamespace(root=tmp_path, dir=book, sid=sid, cli=cli,
                           hook=hook, approvals=approvals, ArtifactStore=ArtifactStore,
                           models={'s6': Hook, 's7': NameMap})


def _final_gate_cli(box, gate):
    if gate == 's6':
        ctx = RunContext.open(
            box.root, box.sid, 'stage6.approve', paid=False, write_ledger=False)
        pack = box.hook.approval_workpack(box.ArtifactStore(ctx))
        argv = [
            'run', 'stage6.approve', box.sid, '--base', str(box.root),
            '--actor', 'cli-final-reviewer', '--reason', '核对完整测试工作包后定稿',
            '--review-token', pack.review_token, '--pick', '1',
        ]
        assert box.cli.main(argv) in (0, 1)
        current, _ = box.ArtifactStore(ctx).read_own(box.models['s6'], box.hook.REL)
        assert current.approved is not None
        return current
    # 批次 B4：s7 由 rename --recheck → auto seal，不再走 stage7.approve。
    assert box.cli.main([
        'run', 'stage7.rename', box.sid, '--base', str(box.root), '--recheck',
    ]) in (0, 1)
    ctx = RunContext.open(
        box.root, box.sid, 'stage7.rename', paid=False, write_ledger=False)
    current, _ = box.ArtifactStore(ctx).read_own(box.models['s7'], rename.REL)
    assert current.approved is not None
    assert current.approved.origin == 'auto'
    return current


def _final_gate_bytes(box):
    return {path.relative_to(box.root).as_posix(): path.read_bytes()
            for path in box.root.rglob('*') if path.is_file()}


def _get_final_gate_doc(client, box):
    response = client.get(f'/api/novels/{box.sid}/approvals')
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize('old_stamp_failed', [False, True])
def test_cli_final_approvals_are_public_without_secondary_service_stamps(
        final_gate_book, old_stamp_failed):
    box = final_gate_book
    for gate in ('s6', 's7'):
        _final_gate_cli(box, gate)
    raw_path = box.approvals._path(box.root, box.sid)
    if old_stamp_failed:
        # 复刻旧 HTTP 已落稿、后置 stamp 因 writer 冲突失败的真实遗留盘面。
        from novel.api.approval_stamp import stamp_approval_after_landed
        from novel.orchestrator import jobs
        key, holder = jobs.agent_lock_key(box.sid), 'legacy-final-stamp-conflict'
        jobs.AGENT_REGISTRY.acquire(key, holder)
        try:
            with pytest.warns(RuntimeWarning, match='没有并发写盘'):
                stamp_approval_after_landed(str(box.root), box.sid, 's6')
        finally:
            jobs.AGENT_REGISTRY.release(key, holder)
    before = _final_gate_bytes(box)
    raw_before = raw_path.read_bytes()
    with TestClient(create_app(box.root)) as client:
        for _ in range(2):
            doc = _get_final_gate_doc(client, box)
            assert doc['gate']['s6'] is doc['gate']['s7'] is True
    assert _final_gate_bytes(box) == before
    assert raw_path.read_bytes() == raw_before
    raw = box.approvals._read_raw(box.root, box.sid)
    assert raw.gate['s6'] is raw.gate['s7'] is False
    assert raw.gate_proofs['s6'] is raw.gate_proofs['s7'] is None
    assert not (box.root / '_service/ledger.jsonl').exists()


@pytest.mark.parametrize('gate', ['s6', 's7'])
@pytest.mark.parametrize('damage', ['missing', 'invalid', 'corrupt', 'no_decision',
                                    'subject_changed', 'evidence_changed', 'upstream_closed'])
def test_public_final_gate_refuses_missing_damaged_or_changed_typed_evidence(
        final_gate_book, gate, damage):
    box = final_gate_book
    for current_gate in ('s6', 's7'):
        _final_gate_cli(box, current_gate)
    module = box.hook if gate == 's6' else rename
    path = box.dir / module.REL
    data = json.loads(path.read_text(encoding='utf-8'))
    if damage == 'missing':
        path.unlink()
    elif damage == 'invalid':
        path.with_name(path.stem + '.INVALID.json').write_bytes(path.read_bytes())
    elif damage == 'corrupt':
        path.write_text('损坏的 JSON', encoding='utf-8')
    elif damage == 'upstream_closed':
        raw = box.approvals._read_raw(box.root, box.sid)
        raw.gate['s5'] = False
        raw.gate_proofs['s5'] = None
        box.approvals._write(box.root, raw)
    else:
        if damage == 'no_decision':
            data['approved'] = None
        elif damage == 'subject_changed':
            if gate == 's6':
                data['candidates'][0]['text'] += '新的一句话改变了候选。'
            else:
                data['characters'][0]['reason'] = '改名理由与人看过的决定不同。'
        elif gate == 's6':
            data['rationale'] = '审阅材料已发生变化。'
        else:
            data['applied']['hook']['counts'] = {'沈月': 99}
        path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    before = _final_gate_bytes(box)
    with TestClient(create_app(box.root)) as client:
        doc = _get_final_gate_doc(client, box)
    assert doc['gate'][gate] is False
    if gate == 's6':
        assert doc['gate']['s7'] is False
    assert _final_gate_bytes(box) == before


@pytest.mark.parametrize('gate', ['s6', 's7'])
def test_final_gate_never_combines_old_typed_decision_with_new_unapproved_bytes(
        final_gate_book, monkeypatch, gate):
    box = final_gate_book
    for current_gate in ('s6', 's7'):
        _final_gate_cli(box, current_gate)
    module = box.hook if gate == 's6' else rename
    path = box.dir / module.REL
    original = box.approvals._require_typed_gate_decision
    replaced = False
    def replace_after_typed_read(store, current_gate):
        nonlocal replaced
        result = original(store, current_gate)
        if current_gate == gate and not replaced:
            replaced = True
            data = json.loads(path.read_text(encoding='utf-8'))
            data['approved'] = None
            path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        return result
    monkeypatch.setattr(box.approvals, '_require_typed_gate_decision', replace_after_typed_read)
    service_before = box.approvals._path(box.root, box.sid).read_bytes()
    with TestClient(create_app(box.root)) as client:
        changed = client.get(f'/api/novels/{box.sid}/approvals')
        assert changed.status_code == 409, changed.text
        assert changed.json()['kind'] == 'ArtifactUnreadable'
        assert '盘面发生变化' in changed.json()['message']
        # 同请求内漂移不能拼接旧决定与新字节；重读稳定的新盘面仍须关闭该门。
        doc = _get_final_gate_doc(client, box)
    assert replaced
    assert doc['gate'][gate] is False
    assert box.approvals._path(box.root, box.sid).read_bytes() == service_before


@pytest.mark.parametrize('gate', ['s6', 's7'])
def test_zero_cost_recheck_preserves_same_typed_final_decision_without_backfill(
        final_gate_book, monkeypatch, gate):
    box = final_gate_book
    for current_gate in ('s6', 's7'):
        _final_gate_cli(box, current_gate)
    module = box.hook if gate == 's6' else rename
    original = json.loads((box.dir / module.REL).read_text(encoding='utf-8'))['approved']
    raw_before = box.approvals._path(box.root, box.sid).read_bytes()
    def current_pack():
        node = 'stage6.approve' if gate == 's6' else 'stage7.rename'
        ctx = RunContext.open(box.root, box.sid, node,
                              paid=False, write_ledger=False)
        return module.approval_workpack(box.ArtifactStore(ctx))

    before_pack = current_pack()
    old_rules = box.approvals.prov.rules_fingerprint
    monkeypatch.setattr(box.approvals.prov, 'code_version', lambda: 'new-current-code')
    monkeypatch.setattr(box.approvals.prov, 'rules_fingerprint',
                        lambda stage: old_rules(stage) + ':new-validation-epoch')
    after_pack = current_pack()
    assert after_pack.validation_seal_sha256 != before_pack.validation_seal_sha256
    assert after_pack.review_identity_sha256 == before_pack.review_identity_sha256
    with TestClient(create_app(box.root)) as client:
        # GET 已按当前代码/规则免费重验；同业务证据无需人为造 stale 或补录章。
        assert _get_final_gate_doc(client, box)['gate'][gate] is True
        for node in ('stage6.hook', 'stage7.rename'):
            assert box.cli.main(['run', node, box.sid, '--base', str(box.root),
                                 '--recheck']) in (0, 1)
        assert _get_final_gate_doc(client, box)['gate'][gate] is True
    assert current_pack().review_identity_sha256 == before_pack.review_identity_sha256
    assert json.loads((box.dir / module.REL).read_text(encoding='utf-8'))['approved'] == original
    assert box.approvals._path(box.root, box.sid).read_bytes() == raw_before
    assert not (box.root / '_service/ledger.jsonl').exists()


def test_backfill_does_not_write_final_stamps_or_manufacture_other_gate_facts(final_gate_book):
    box = final_gate_book
    for gate in ('s6', 's7'):
        _final_gate_cli(box, gate)
    before = box.approvals._path(box.root, box.sid).read_bytes()
    result = box.approvals.backfill_from_disk(box.root, box.sid)
    assert 's6' not in result.backfilled and 's7' not in result.backfilled
    assert result.approvals.gate['s6'] is result.approvals.gate['s7'] is True
    assert box.approvals._path(box.root, box.sid).read_bytes() == before
    # 普通前置格仍必须由显式路径登记，GET 不自动补。
    raw = box.approvals._read_raw(box.root, box.sid)
    raw.gate['s1'] = False
    raw.gate_proofs['s1'] = None
    box.approvals._write(box.root, raw)
    before = box.approvals._path(box.root, box.sid).read_bytes()
    with TestClient(create_app(box.root)) as client:
        doc = _get_final_gate_doc(client, box)
        # B2 起 gate 链不再经过 s4（现役 {s1←s0, s6←s5, s7←s6}），s6/s7
        # 不再传递依赖 s1；「GET 不自动补前置格」仍由 s1 保持 False 钉住。
        assert doc['gate']['s1'] is False
        assert doc['gate']['s6'] is doc['gate']['s7'] is True
    assert box.approvals._path(box.root, box.sid).read_bytes() == before


def test_current_final_artifacts_never_silently_migrate_a_legacy_service_record(final_gate_book):
    box = final_gate_book
    for gate in ('s6', 's7'):
        _final_gate_cli(box, gate)
    path = box.approvals._path(box.root, box.sid)
    legacy = json.loads(path.read_text(encoding='utf-8'))
    legacy['v'] = 6
    for key in ('closure_history', 'closure_decision', 'seen_subjects'):
        legacy.pop(key, None)
    legacy['gate']['s5'] = False
    legacy['gate_proofs']['s5'] = None
    path.write_text(json.dumps(legacy, ensure_ascii=False), encoding='utf-8')
    before = _final_gate_bytes(box)
    with TestClient(create_app(box.root)) as client:
        response = client.get(f'/api/novels/{box.sid}/approvals')
        assert response.status_code == 409, response.text
    assert _final_gate_bytes(box) == before
