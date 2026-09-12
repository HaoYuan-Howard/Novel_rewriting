"""V9 接入真实 Stage2 producer、Store、checkpoint 的免费验证。

模型传输使用 FakeTransport，保留现有生成、Global 接受和读回检查；该结果
不能替代模型质量、真实 CLI 子进程或最终 Stage2→Stage5 贯通验收。
"""
from __future__ import annotations

import json

import pytest

from contracts.schema.stage2 import ChapterBlueprints
from contracts.schema.stage1 import MainText
from novel import prov
from novel.stages.stage2 import extract
from novel.store import ArtifactStore
from tests.test_stage2 import main_text
from tests.test_stage2_generation import (
    _ctx, _client, _ok, _response_global, _accept_fixture_global,
)


def responses():
    contents = {
        1: [
            ('昨夜来信告知李宝珠父亲已经回城。', 3, 4, ['char_001']),
            ('春日老宅中，李宝珠站在廊下看落花，回想来信，折信收袖，听见远处梆声。',
             1, 6, ['char_001']),
        ],
        2: [
            ('李宝珠清晨出门，在有小贩叫卖的街上买了两个包子，回来见母亲在门口等她。',
             1, 5, ['char_001', 'char_002']),
            ('母亲其实早已知道那封信的事。', 6, 6, ['char_002']),
        ],
        3: [
            ('李宝珠午后翻账册，听管家报告米价上涨后，让他询问其他商家。窗外槐叶零落。',
             1, 5, ['char_001']),
            ('她回忆童年时父亲翻账，自己当时还够不到桌面。', 6, 7, ['char_001']),
            ('母亲给她添上刚沏的热茶，她端起吹了吹，没有喝。', 8, 10, ['char_001', 'char_002']),
        ],
    }
    return {ordinal: {
        'chapter_ordinal': ordinal, 'chapter_function': '推进家宅生活及家人之间的信息变化',
        'beats': [{
            'beat_key': f'b{i}', 'content': content, 'participant_char_ids': chars,
            'evidence': [{'start_para': start, 'end_para': end}], 'source_contexts': [],
        } for i, (content, start, end, chars) in enumerate(rows, 1)],
        'states': [], 'inherited_state_endings': [], 'constraints': [],
        'entry_state_refs': [], 'carry_out_refs': [],
        'narration': [{'grammatical_person': 'third', 'narrator_char_id': None,
                       'focalizer_char_id': 'char_001', 'pronoun_surfaces': []}],
    } for ordinal, rows in contents.items()}


@pytest.fixture(autouse=True)
def plot_contract(monkeypatch, request):
    monkeypatch.setenv('NOVEL_STAGE2_BLUEPRINT_CONTRACT', 'plot-blueprint-v1')
    if request.node.get_closest_marker('real_code_version') is None:
        monkeypatch.setattr(prov, 'code_version', lambda: 'test-plot-producer')


def prepare_book(tmp_path):
    book = tmp_path / '完整剧情测试_123'
    book.mkdir()
    ctx = _ctx(book, 'stage2.global')
    ArtifactStore(ctx).write(extract.MAIN_TEXT, MainText.model_validate(main_text()))
    client, _ = _client(ctx, lambda _req, _index: _ok(_response_global()))
    extract.run_global(ctx, client)
    _accept_fixture_global(ctx)
    return book


def test_plot_blueprint_real_producer_commits_v9_and_reads_back(tmp_path):
    book = prepare_book(tmp_path)
    ctx = _ctx(book, 'stage2.blueprint')
    by_chapter = responses()

    preview = extract.build_prompt(ctx)
    def respond(req, _index):
        metadata = json.loads(req.user.split('\n', 1)[0])
        ordinal = metadata['chapter_ordinal']
        return _ok({'blueprints': [by_chapter[ordinal]]})

    client, transport = _client(ctx, respond)
    report = extract.run_blueprint(ctx, client)
    assert report.passed
    store = ArtifactStore(ctx)
    artifact, raw = store.read_own(ChapterBlueprints, extract.BLUEPRINT_REL)
    assert len(artifact.blueprints) == len(main_text()) == 3
    assert len(transport.calls) == 3
    assert (transport.calls[0].system, transport.calls[0].user) == preview
    assert all(beat.content for chapter in artifact.blueprints for beat in chapter.beats)
    assert 'fact_atoms' not in json.dumps(raw)
    assert not store.exists(extract._INVALID)
    assert store.freshness(extract.BLUEPRINT_REL).state == 'fresh'
    assert next(row for row in report.rules if row.rule == 'plot_semantics').status == 'unver'
    for request in transport.calls:
        metadata = json.loads(request.user.split('\n', 1)[0])
        ordinal = metadata['chapter_ordinal']
        source = next(row['text'] for row in main_text() if row['chapter_ordinal'] == ordinal)
        assert request.user.count(source.split('\n\n')[0]) == 1

def generate_plot(book, by_chapter=None):
    ctx = _ctx(book, 'stage2.blueprint')
    by_chapter = responses() if by_chapter is None else by_chapter
    client, transport = _client(ctx, lambda req, _index: _ok({
        'blueprints': [by_chapter[json.loads(req.user.split('\n', 1)[0])['chapter_ordinal']]],
    }))
    report = extract.run_blueprint(ctx, client)
    assert report.passed
    return ctx, transport


def test_real_requests_receive_sparse_state_ids_from_previous_verified_chapter(tmp_path):
    book = prepare_book(tmp_path)
    ctx = _ctx(book, 'stage2.blueprint')
    by_chapter = responses()
    by_chapter[1]['states'] = [{
        'state_key': 'st1', 'content': '李宝珠把父亲回城的来信收在袖中。',
        'global_ref': None, 'modality': 'world', 'holder_char_id': None,
        'established_at': {'kind': 'at_beat', 'beat_ref': 'b2'},
        'ended_at': None, 'supersedes_state_refs': [],
        'evidence': [{'start_para': 5, 'end_para': 5}],
    }]
    by_chapter[1]['carry_out_refs'] = ['st1']
    for ordinal in (2, 3):
        by_chapter[ordinal]['entry_state_refs'] = ['st01_01']
        by_chapter[ordinal]['carry_out_refs'] = ['st01_01']
    order = []
    def respond(req, _index):
        metadata = json.loads(req.user.split('\n', 1)[0])
        ordinal = metadata['chapter_ordinal']
        order.append(ordinal)
        if ordinal == 1:
            assert metadata['entry_states'] == []
        else:
            incoming = metadata['entry_states']
            assert len(incoming) == 1
            assert incoming[0]['state']['state_id'] == 'st01_01'
            assert incoming[0]['state']['modality'] == 'world'
            assert incoming[0]['source_sha256']
            assert incoming[0]['evidence_anchors'][0]['start_para'] == 5
        return _ok({'blueprints': [by_chapter[ordinal]]})
    client, transport = _client(ctx, respond)
    report = extract.run_blueprint(ctx, client)
    assert report.passed
    assert order == [1, 2, 3] and len(transport.calls) == 3
    artifact, _ = ArtifactStore(ctx).read_own(ChapterBlueprints, extract.BLUEPRINT_REL)
    assert sum(len(row.states) for row in artifact.blueprints) == 1
    assert artifact.blueprints[2].entry_state_refs == ['st01_01']
    assert artifact.blueprints[2].carry_out_refs == ['st01_01']


@pytest.mark.parametrize('route', ['revalidate', 'renormalize'])
def test_plot_invalid_coordinates_recover_without_a_model_call(tmp_path, route):
    book = prepare_book(tmp_path)
    producer, transport = generate_plot(book)
    store = ArtifactStore(producer)
    artifact, _ = store.read_own(ChapterBlueprints, extract.BLUEPRINT_REL)
    before = artifact.model_dump(mode='json', by_alias=True)
    damaged = artifact.model_copy(deep=True)
    damaged.blueprints[0].evidence_anchors[0].start += 1
    store.write_invalid(extract.BLUEPRINT_REL, damaged, gates_skipped=[])
    recovery = _ctx(book, 'stage2.blueprint', paid=False)
    if route == 'revalidate':
        promoted, report = extract.run_revalidate(
            recovery, actor='测试操作者', reason='按冻结段号免费重算错误坐标')
        assert promoted
    else:
        report = extract.run_renormalize(recovery)
    assert report.passed
    recovered, _ = ArtifactStore(recovery).read_own(ChapterBlueprints, extract.BLUEPRINT_REL)
    assert recovered.model_dump(mode='json', by_alias=True)['blueprints'] == before['blueprints']
    assert recovered.provenance.usage == artifact.provenance.usage
    assert len(transport.calls) == len(main_text())
    assert not ArtifactStore(recovery).exists(extract._INVALID)


def test_plot_commit_tail_resumes_without_rebuying_chapters(tmp_path, monkeypatch):
    from novel.llm import stage2_runs
    from tests.test_stage2_generation import _disk_blueprint_ctx
    book = prepare_book(tmp_path)
    ctx = _ctx(book, 'stage2.blueprint')
    by_chapter = responses()
    client, first_transport = _client(ctx, lambda req, _index: _ok({
        'blueprints': [by_chapter[json.loads(req.user.split('\n', 1)[0])['chapter_ordinal']]],
    }))
    original_mark = stage2_runs.mark_committed
    with monkeypatch.context() as patch:
        def interrupted(_ctx):
            raise RuntimeError('测试：正式产物写后、committed 哨兵之前中断')
        patch.setattr(stage2_runs, 'mark_committed', interrupted)
        with pytest.raises(RuntimeError, match='测试：正式产物写后'):
            extract.run_blueprint(ctx, client)
    assert ArtifactStore(ctx).exists(extract.BLUEPRINT_REL)
    resumed = _disk_blueprint_ctx(book, ctx.ledger.path, paid=True, run_id=ctx.run_id)
    def should_not_buy(_request, _index):
        pytest.fail('完整已购章节恢复不应再次调用模型')
    resumed_client, resumed_transport = _client(resumed, should_not_buy)
    report = extract.run_blueprint(resumed, resumed_client)
    assert report.passed
    assert len(first_transport.calls) == len(main_text())
    assert resumed_transport.calls == []
    assert stage2_runs.committed(resumed) is not None
