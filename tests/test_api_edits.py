#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_api_edits.py —— 人工写回三条路由的**行为**（真起 CLI 子进程）。

【四类行为】（BE-03）：成功 / 上游未运行（200+failed，不误发 5xx）/
产物损坏（200+failed，日志可读）/ 参数不合法（422 载体层）。
【钱】三条全是 0 成本节点（`--apply-edit` / approve 均在 FREE 面）——
`no_api_key` 把钥匙拿走，任何一条走到真发请求的路都当场现形。

⚠ 沙箱一律 `tempfile.mkdtemp()`；**原语料一个字节都不许动**。
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

fastapi = pytest.importorskip(
    'fastapi', reason='没装 fastapi ⇒ 这一批**没在查**，不是「查过没事」')
from fastapi.testclient import TestClient          # noqa: E402

from contracts.invariants import APPROVAL_GATES   # noqa: E402
from contracts.schema import decode_artifact      # noqa: E402
from contracts.schema.api import DraftEditPreview # noqa: E402
from contracts.schema.stage7 import NameMap        # noqa: E402
from novel.api.app import UI_HEADER, create_app    # noqa: E402
from novel.run import RunContext                    # noqa: E402
from novel.stages.stage7 import assemble, rename    # noqa: E402
from tests import corpus, sandbox                  # noqa: E402

INPUTS = ('stage0/revisions.json',
          'stage1/main_text.json', 'stage1/hook_copy.json',
          'stage2/global_grounding.json', 'stage2/chapter_blueprints.json',
          'stage2/global_review.json',
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


def _name_approval_body(box) -> dict[str, str]:
    raw = json.loads((box.dir / rename.REL).read_text(encoding='utf-8'))
    art = NameMap.model_validate(decode_artifact(rename.REL, raw).payload)
    return {
        'actor': 'reviewer',
        'reason': 'reviewed the complete name decision surface',
        'subject_sha256': rename.approval_subject_sha256(art),
    }


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
    # ⚠ 现役语料的 stage2 审阅项全是 `pending` ⇒ `s2` 的 typed 决策闸永远过不去，
    #   `_stamp_chain` 因此一格都预置不出来。人工决策是本组的**输入**不是结论。
    sandbox.decide_global_review(s)
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
        's0': True, 's1': True, 's2': True, 's4': True,
        's5': True, 's6': False, 's7': False,
    }
    assert 's3' not in written[0].gate
    assert written[0].gate_proofs['s6'] is None
    assert written[0].gate_proofs['s7'] is None


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
    data = json.loads((box.dir / 'stage6' / 'hook.json').read_text(encoding='utf-8'))
    index = max(0, min(pick - 1, len(data['candidates']) - 1))
    text = data['candidates'][index]['text']
    return {
        'pick': pick,
        'actor': 'api-test-editor',
        'reason': '逐项核对了当前候选与全书事实',
        'subject_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
    }


def _approval_target(box) -> int:
    """挑一条生产的批准谓词真能收口的候选，返回它的 `pick`。

    ⚠ 这里曾分「校准 `clear` 走自动 basis」与「人工核验」两条路，并额外排除
    `historical_fail`。自动放行门下线后 `CandidateAssessment` 上那两格都没了，
    `basis` 在类型上只剩 `human_verification`——**服务端不替人否决任何候选**。
    `run_approve` 唯一还会抛的候选级判据就是机械 HARD，这里**调它那把尺子**
    （`hook._sev_of`）而不是重拼一份 REGISTRY 查询。
    ⚠ 也**不再回传当时的语义状态**：两个调用点都只断言 `basis == 'human_verification'`
    （它在类型上就只剩这一个值），没人读那一格；为它跑一次 `current_assessments`
    等于纯为丢弃值读一遍 `semantic_<n>.json` 并算一遍新鲜度。
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
    """⭐ 一条 hash-bound 的人工核验能定稿，并在前置链完成时盖上 `s6`。

    ⚠ 这里曾写「自动/人工依据都能定稿」。自动放行门下线后 `basis` 在类型上只剩
    `human_verification`，那句话已经是假的——**没有第二种依据可写**。
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
    assert after['approved']['approach'] == before['candidates'][pick - 1]['approach']
    assert after['approved']['origin'] == 'human'
    resolution = after['approved']['resolution']
    assert resolution['basis'] == 'human_verification'
    assert resolution['actor'] == body['actor']
    assert resolution['reason'] == body['reason']
    assert resolution['subject_sha256'] == body['subject_sha256']
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
    assert after['approved']['resolution']['basis'] == 'human_verification'
    assert after['approved']['resolution']['subject_sha256'] == body['subject_sha256']
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
        'subject_sha256': '0' * 64,
    }
    assert validation_client.post(
        '/api/novels/422/stage6/approve', json=body).status_code == 422


# ───────────────────────── 三、人名定稿 ─────────────────────────

def test_approving_the_name_map_stamps_s7_and_assembly_keeps_it_current(
    client, box, monkeypatch,
):
    """API 定稿盖 `s7`；正常组装只写派生交付账，不能撤销同一批准。"""
    from novel.stages.stage6 import semantic

    # ⚠ 本条要经 `stage7.approve` 把批准写回 `stage7/name_map.json`，而那条路明写
    #   「`_provenance` 一个字都不动」⇒ 映射一旦相对上游过期，写回的就是一份过期账，
    #   写盘口后置条件当场 `ProvenanceRefused`（退出码 3），断言红在退出码上而不是
    #   它要验的「定稿盖 s7」。一份当前的映射是本条的**输入**，不是它的结论。
    corpus.requires_current(box.book, 'stage7/name_map.json',
                            produced_by='stage7.rename --recheck', cost='0 成本')
    _stamp_chain(box, 's6')
    body = _name_approval_body(box)
    r = client.post(f'/api/novels/{box.sid}/stage7/approve', json=body)
    assert r.status_code == 200, r.text[:300]
    assert r.json()['exit_code'] in (0, 1), r.text[:300]
    after = json.loads((box.dir / 'stage7' / 'name_map.json').read_text(encoding='utf-8'))
    assert after['approved'] == {'origin': 'human', **body}
    assert client.get(f'/api/novels/{box.sid}/approvals').json()['gate']['s7'] is True

    monkeypatch.setattr(semantic, 'require_approved_clear', lambda *_args: None)
    ctx = RunContext.open(
        str(box.root), box.sid, 'stage7.assemble', paid=False,
        write_ledger=False)
    _path, assembled, _report = assemble.run_assemble(ctx)
    assert assembled.book_txt is not None
    assert client.get(f'/api/novels/{box.sid}/approvals').json()['gate']['s7'] is True


def test_approving_names_with_an_old_subject_fails_without_stamping(client, box):
    before = (box.dir / 'stage7' / 'name_map.json').read_bytes()
    body = _name_approval_body(box)
    body['subject_sha256'] = '0' * 64

    r = client.post(f'/api/novels/{box.sid}/stage7/approve', json=body)

    assert r.status_code == 200 and r.json()['state'] == 'failed'
    assert r.json()['exit_code'] >= 2
    assert (box.dir / 'stage7' / 'name_map.json').read_bytes() == before
    assert client.get(f'/api/novels/{box.sid}/approvals').json()['gate']['s7'] is False


def test_approving_names_without_a_name_map_fails(client, box):
    _stamp_chain(box, 's6')
    body = _name_approval_body(box)
    (box.dir / 'stage7' / 'name_map.json').unlink()
    r = client.post(f'/api/novels/{box.sid}/stage7/approve', json=body)
    assert r.status_code == 200 and r.json()['state'] == 'failed'
    assert r.json()['exit_code'] >= 2
    assert client.get(f'/api/novels/{box.sid}/approvals').json()['gate']['s7'] is False


def test_approving_names_requires_all_hash_bound_credentials(validation_client):
    assert validation_client.post(
        '/api/novels/422/stage7/approve', json={}).status_code == 422


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
