"""真实零模型编辑、稳定身份、拆合历史与提交尾窗恢复；不把预检当语义接受。"""
from __future__ import annotations

import copy
import json
import pytest

from contracts.schema import decode_artifact, encode_artifact
from contracts.schema.plot_edit import PlotEdit
from contracts.schema.stage2 import ChapterBlueprints
from novel.errors import ProvenanceRefused, TerminalState
from novel.stages.stage2 import extract, plot_edit
from novel.store import ArtifactStore
from tests.test_plot_producer import prepare_book, generate_plot, plot_contract
from tests.test_stage2_generation import _ctx


def request_for(ctx):
    payload = decode_artifact(plot_edit.REQUEST_REL, plot_edit.edit_template(ctx)).payload
    payload.update(actor='测试编辑人', reason='依据原文纠正节点边界')
    payload['chapters'] = payload['chapters'][:1]
    return payload


def save_request(ctx, raw):
    path = ctx.novel_dir / 'plot-edit-request.json'
    path.write_text(json.dumps(encode_artifact(plot_edit.REQUEST_REL, raw), ensure_ascii=False), encoding='utf-8')
    return str(path)


def test_wording_edit_keeps_ids_and_unrelated_chapters_and_replays_after_commit(tmp_path, monkeypatch):
    book = prepare_book(tmp_path)
    generate_plot(book)
    ctx = _ctx(book, 'stage2.blueprint', paid=False)
    before = ArtifactStore(ctx).read_own(ChapterBlueprints, extract.BLUEPRINT_REL)[0]
    request = request_for(ctx)
    request['chapters'][0]['chapter']['beats'][0]['content'] = '昨夜的来信告知李宝珠，父亲已经回城。'
    preview = plot_edit.prepare_edit(ctx, PlotEdit.model_validate(request)).preview()
    assert preview.would_apply and preview.changed_chapters == [1]
    assert not preview.tombstones
    # 预检没有顺手修改持久或内存基线的身份账。
    assert not before.provenance.plot_revisions
    assert not ArtifactStore(ctx).read_own(ChapterBlueprints, extract.BLUEPRINT_REL)[0].provenance.plot_revisions
    supplied = save_request(ctx, request)
    original_write = extract._write_formal
    def interrupted(*args, **kwargs):
        original_write(*args, **kwargs)
        raise RuntimeError('正式写回后的尾窗中断')
    with monkeypatch.context() as patch:
        patch.setattr(extract, '_write_formal', interrupted)
        with pytest.raises(RuntimeError, match='尾窗中断'):
            plot_edit.apply_edit(ctx, supplied)
    replay = _ctx(book, 'stage2.blueprint', paid=False)
    landed, report = plot_edit.apply_edit(replay, supplied)
    assert report.passed
    assert landed.blueprints[0].beats[0].beat_id == before.blueprints[0].beats[0].beat_id
    assert landed.blueprints[1:] == before.blueprints[1:]
    assert len(landed.provenance.plot_revisions) == 1
    assert landed.provenance.usage == before.provenance.usage
    assert plot_edit.submitted_edit_is_current(book.parent, ctx.sid, encode_artifact(plot_edit.REQUEST_REL, request))
    assert plot_edit.apply_edit(_ctx(book, 'stage2.blueprint', paid=False), supplied)[0] == landed


def test_split_allocates_new_ids_and_tombstone_cannot_be_reused(tmp_path):
    book = prepare_book(tmp_path)
    generate_plot(book)
    ctx = _ctx(book, 'stage2.blueprint', paid=False)
    request = request_for(ctx)
    chapter = request['chapters'][0]
    original = chapter['chapter']['beats'][1]
    first, second = copy.deepcopy(original), copy.deepcopy(original)
    first.update(beat_key='b10', content='李宝珠站在春日老宅廊下看落花，回想昨夜的来信。')
    second.update(beat_key='b11', content='李宝珠将信折起收进袖中，听见远处梆声。')
    chapter['chapter']['beats'][1:] = [first, second]
    chapter['identities'][1] = dict(collection='beats', kind='replace',
                                    old_refs=['b01_02'], new_keys=['b10', 'b11'])
    preview = plot_edit.prepare_edit(ctx, PlotEdit.model_validate(request)).preview()
    assert preview.would_apply
    assert preview.tombstones[0].node_id == 'b01_02'
    replacements = preview.tombstones[0].superseded_by
    assert len(replacements) == 2 and 'b01_02' not in replacements
    landed, report = plot_edit.apply_edit(ctx, save_request(ctx, request))
    assert report.passed
    assert [row.beat_id for row in landed.blueprints[0].beats][1:] == replacements
    retry = request_for(_ctx(book, 'stage2.blueprint', paid=False))
    retry['chapters'][0]['chapter']['beats'][1]['content'] += '（订正）'
    retry['chapters'][0]['identities'][1]['old_refs'] = ['b01_02']
    with pytest.raises(TerminalState, match='不重不漏'):
        plot_edit.prepare_edit(_ctx(book, 'stage2.blueprint', paid=False), PlotEdit.model_validate(retry))
    # 旧 token 不能再次改变已经拆分的章。
    with pytest.raises(ProvenanceRefused, match='过期'):
        plot_edit.prepare_edit(_ctx(book, 'stage2.blueprint', paid=False), PlotEdit.model_validate(request))


def test_replay_retires_only_the_original_invalid_after_formal_replace(tmp_path, monkeypatch):
    book = prepare_book(tmp_path)
    generate_plot(book)
    ctx = _ctx(book, 'stage2.blueprint', paid=False)
    store = ArtifactStore(ctx)
    before = store.read_own(ChapterBlueprints, extract.BLUEPRINT_REL)[0]
    damaged = before.model_copy(deep=True)
    damaged.blueprints[0].beats[0].content = '错抽的来信内容'
    store.write_invalid(extract.BLUEPRINT_REL, damaged, gates_skipped=[])
    request = request_for(ctx)
    request['chapters'][0]['chapter']['beats'][0]['content'] = before.blueprints[0].beats[0].content
    supplied = save_request(ctx, request)
    def interrupted(store, rel, artifact):
        # 模拟硬杀窗口；普通 write_round 已自行归档 INVALID，不能用于此注入。
        store._write_formal(rel, artifact, retire_invalid=False)
        raise RuntimeError('formal 已替换，INVALID 尚未归档')
    with monkeypatch.context() as patch:
        patch.setattr(extract, '_write_formal', interrupted)
        with pytest.raises(RuntimeError, match='尚未归档'):
            plot_edit.apply_edit(ctx, supplied)
    assert store.exists(extract._INVALID)
    payload = encode_artifact(plot_edit.REQUEST_REL, request)
    assert not plot_edit.submitted_edit_is_current(book.parent, ctx.sid, payload)
    assert plot_edit.submitted_edit_is_current(
        book.parent, ctx.sid, payload, require_action_complete=False)
    landed, report = plot_edit.apply_edit(_ctx(book, 'stage2.blueprint', paid=False), supplied)
    assert report.passed and not store.exists(extract._INVALID)
    assert plot_edit.submitted_edit_is_current(book.parent, ctx.sid, payload)
    assert len(landed.provenance.plot_revisions) == 1
    assert landed.blueprints == before.blueprints
    newer = landed.model_copy(deep=True)
    newer.blueprints[0].beats[0].content = '此后的另一轮错误，必须独立处置。'
    store.write_invalid(extract.BLUEPRINT_REL, newer, gates_skipped=[])
    invalid_bytes = store.path(extract._INVALID).read_bytes()
    assert not plot_edit.submitted_edit_is_current(book.parent, ctx.sid, payload)
    assert not plot_edit.submitted_edit_is_current(
        book.parent, ctx.sid, payload, require_action_complete=False)
    with pytest.raises(ProvenanceRefused, match='更新的问题稿'):
        plot_edit.apply_edit(_ctx(book, 'stage2.blueprint', paid=False), supplied)
    assert store.path(extract._INVALID).read_bytes() == invalid_bytes


def test_http_edit_template_and_preview_use_the_same_compiler(tmp_path):
    from fastapi.testclient import TestClient
    from novel.api.app import create_app
    book = prepare_book(tmp_path)
    generate_plot(book)
    with TestClient(create_app(str(book.parent))) as http:
        reply = http.get('/api/novels/123/stage2/blueprint/edit/template?chapter_ordinal=1')
        assert reply.status_code == 200, reply.text
        request = reply.json()
        assert len(request['chapters']) == 1
        request.update(actor='测试编辑', reason='原文支持的措辞订正')
        request['chapters'][0]['chapter']['beats'][0]['content'] = '昨夜来信说李宝珠的父亲已回城。'
        reply = http.post('/api/novels/123/stage2/blueprint/edit/preview', json=request,
                          headers={'x-novel-ui': '1'})
        assert reply.status_code == 200, reply.text
        assert reply.json()['would_apply']
        assert reply.json()['changed_chapters'] == [1]


def test_edit_reopens_only_changed_chapter_without_releasing_cross_review(tmp_path):
    from novel.orchestrator import approvals
    from novel.views.plot_review import PlotReviewSnapshot
    from novel.views.stage2 import blueprint_review_workpack
    from tests.test_plot_review_runtime import request as accept_request
    book = prepare_book(tmp_path)
    ctx, _ = generate_plot(book)
    for scope in ('chapter:1', 'chapter:2', 'chapter:3', 'cross_chapter'):
        pack = blueprint_review_workpack(book.parent, ctx.sid, scope=scope)
        approvals.approve_stage2_review(book.parent, ctx.sid, accept_request(pack))
    editing = _ctx(book, 'stage2.blueprint', paid=False)
    request = request_for(editing)
    request['chapters'][0]['chapter']['beats'][0]['content'] = '昨夜来信告诉李宝珠，她的父亲已经回城。'
    plot_edit.apply_edit(editing, save_request(editing, request))
    snapshot = PlotReviewSnapshot.read(ArtifactStore(editing),
        approvals.stage2_review_history(book.parent, ctx.sid))
    states = {row.scope: row.state for row in snapshot.progress().scopes}
    assert states == {'chapter:1': 'expired', 'chapter:2': 'accepted',
                      'chapter:3': 'accepted', 'cross_chapter': 'expired'}
