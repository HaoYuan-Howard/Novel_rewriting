"""两章以上证据自洽测试书的真实 Stage0→Stage7 纵贯；只有模型传输用合成响应。"""
from __future__ import annotations

import json

from contracts.invariants import HOOK_RECIPE
from novel.store import ArtifactStore
from novel.orchestrator import approvals
from tests.test_plot_design import produce_through_first_chapter
from tests.test_stage2_generation import _ctx, _client, _ok


def grounded_global():
    from tests.test_stage2 import global_grounding, _evidence
    data = global_grounding()
    chars = data['entities']['characters']
    chars['char_001']['role'] = '李宝珠，在家中翻阅账册'
    chars['char_002'].update(canonical='母亲', role='李宝珠的母亲')
    relation = data['entities']['relations'][0]
    relation['valid_from_chapter'] = 2
    relation['visibility']['from_chapter'] = 2
    setting = data['canon']['setting']
    setting['时代背景'] = '原文未明确年代'
    setting['世界观'] = '宅院与街市中的日常生活'
    data['canon']['world_rules'] = []
    data['canon']['secrets'] = [{
        'secret_id': 'secret_01', 'fact': '母亲早就知道那封信的事。',
        'fact_evidence': _evidence(2, '其实母亲早就知道那封信的事。'),
        'knowledge_events': [{
            'subject': {'kind': 'character', 'char_id': 'char_002'},
            'known_from_chapter': 2,
            'evidence': _evidence(2, '其实母亲早就知道那封信的事。'),
        }],
        'setup': {'kind': 'cold'}, 'first_reveal_chapter': 2,
    }]
    return data


REWRITTEN_BEATS = {
    'b02_01': '天色才亮。\n\n李宝珠打开院门，走到街上。\n\n小贩们已开始叫卖。\n\n她买了两个包子，还热着。\n\n回到家时，母亲正守在门边等她。',
    'b02_02': '那封信的消息，母亲其实早已知晓。',
    'b03_01': '午后的阳光叫人犯懒。\n\n李宝珠翻开账簿，逐页看去。\n\n管家在门外禀报，米价又抬高了。\n\n她蹙眉让他去别家打听。\n\n窗外散着槐树的碎叶。',
    'b03_02': '父亲从前也是这样翻账的。\n\n她那时尚小，连桌沿都够不着。',
    'b03_03': '母亲进来了，给她添上茶水。\n\n新沏的茶还烫着。\n\n她捧起茶盏吹了吹，仍未喝下。',
}


def stage5_writer(book):
    """仅替换模型传输，供正常生成与提交故障使用同一真实写作入口。"""
    from novel.stages.stage5 import review
    ctx = _ctx(book, 'stage5.write')
    writer_book = review.load_book(ctx, ArtifactStore(ctx))
    requested = []
    def write_next(req, _index):
        catalog = json.loads(req.user.split('完整剧情目录（每个节点全文只在此出现一次）：\n', 1)[1].split('\n', 1)[0])
        ordinal = int(catalog[0]['beat_ref'].split('_')[0][1:])
        requested.append(ordinal)
        entry = writer_book.chapters[ordinal]
        return _ok({'segments': [
            {'slot': scene['slot'], 'text': '\n\n'.join(
                REWRITTEN_BEATS[d['beat_ref']] for d in scene['deliveries'])}
            for scene in entry['scenes']]})
    client, wire = _client(ctx, write_next)
    return ctx, client, wire, requested


def complete_stage5(book):
    from novel.stages.stage5 import write, review
    ctx, client, wire, requested = stage5_writer(book)
    written = write.run_write(ctx, client, chapters='2,3')
    assert written.passed, written.model_dump_json()
    assert sorted(requested) == [2, 3] and len(wire.calls) == 2
    rev, report = review.run_review(_ctx(book, 'stage5.review', paid=False))
    assert report.passed, report.model_dump_json()
    assert len(rev.chapters) == 3
    # 批次 B3：闭合回执由 run_review 的机械闭合自动落账（system:stage5-auto）。
    assert approvals.get(book.parent, ctx.sid).gate['s5'] is True


def complete_stage6_prepare(book):
    """真实 Prepare session/落盘/独立接受；只替换模型传输，保留未接受阻断。"""
    import pytest
    from contracts.schema.stage6_prepare import PrepareDecision
    from novel.errors import ProvenanceRefused
    from novel.orchestrator.node_agents import Stage6PrepareAgent
    from novel.stages.stage6 import hook, prepare
    from tests.test_stage2_global_agent import _Driver, _profile

    ctx = _ctx(book, 'stage6.prepare')
    ctx.executor_profile = _profile()
    response = {'source_blurb': {
        'facts': [{'kind': 'supported', 'claim_id': 'hook_001',
                   'claim': '李宝珠收到父亲已经回城的来信。',
                   'evidence': {'chapter_ordinal': 1, 'start_para': 3, 'end_para': 4}}],
        'opening': {'kind': 'proposed', 'through_chapter': 1,
                    'rationale': '首章来信与收信行动完整承接原钩子，次章另起清晨场景',
                    'boundary_evidence': {'chapter_ordinal': 1, 'start_para': 5, 'end_para': 6}},
    }}
    driver = _Driver([json.dumps(response, ensure_ascii=False)], book,
                     session_id=f'prepare-{ctx.run_id}')
    result = Stage6PrepareAgent(ctx, driver=driver).run()
    assert result.passed and result.turns == len(driver.calls) == 1
    request = driver.calls[0][1].prompt
    assert request.count('春风吹过老宅的院墙。') == 1
    assert request.count('李宝珠收到父亲已经回城的来信。') == 1
    assert 'entities' not in request and 'arc_secrets' not in request

    with pytest.raises(ProvenanceRefused, match='Prepare 未接受'):
        hook.build_prompt(_ctx(book, 'stage6.hook', paid=False))
    review_ctx = _ctx(book, 'stage6.prepare', paid=False)
    pack = prepare.approval_workpack(ArtifactStore(review_ctx))
    assert pack.state.state == 'pending'
    assert not approvals.get(book.parent, ctx.sid).gate['s6']
    decision = PrepareDecision(
        review_token=pack.state.review_token, source_sha256=pack.state.subject_sha256,
        decision='accept', actor='test:独立文案准备审阅者',
        reason='全文与原钩子核对，来信支持与首章连续范围成立', edit=None)
    prepare.submit_review(review_ctx, decision)
    prepare_report = prepare.run_check_only(_ctx(book, 'stage6.prepare', paid=False))
    assert prepare_report.passed and prepare_report.node == 'stage6.prepare'
    assert {row.rule for row in prepare_report.rules} == {
        'prepare_evidence_valid', 'prepare_semantic_review'}
    assert prepare_report.counts.skipped == 0
    reloaded = ArtifactStore(_ctx(book, 'stage6.hook', paid=False))
    assert prepare.require_accepted(reloaded).source_blurb.opening.through_chapter == 1
    assert not approvals.get(book.parent, ctx.sid).gate['s6'], 'Prepare 接受不得代替最终文案接受'
    assert len(driver.calls) == 1, '免费审阅和重启读回不得重购原准备回答'
    return result.artifact


def complete_stage6(book):
    complete_stage6_prepare(book)
    from novel.stages.stage6 import hook
    hook_ctx = _ctx(book, 'stage6.hook')
    texts = ['来信说，父亲已经回了城。\n\n廊下的李宝珠看过残花，把那封信折进衣袖。',
             '李宝珠清早买回两个包子，母亲就在门口等着她。\n\n那封信的消息，母亲早就知道了。',
             '管家来报，米价又涨了。\n\n李宝珠皱着眉，让他到别家问问，手边的热茶没有喝。']
    hook_client, hook_wire = _client(hook_ctx, lambda req, _i: _ok({
        'candidates': [{'approach': approach, 'text': text} for approach, text in zip(HOOK_RECIPE, texts)],
        'rationale': '分别呈现回城来信、母亲知情与账房日常。'}))
    hook_art = hook.run_hook(hook_ctx, hook_client)
    assert len(hook_wire.calls) == 1
    assert set(hook_art.judged) == {'stage5/ch01.json'}, 'Hook仅绑定实际开篇章稿'
    # stage6.semantic 判官已退役（2026-09-15）：hook 落盘后直接走人工定稿。
    approve_ctx = _ctx(book, 'stage6.approve', paid=False)
    pack = hook.approval_workpack(ArtifactStore(approve_ctx))
    assert [row.chapter_ordinal for row in pack.materials.rewritten_chapters] == [1, 2, 3]
    assert pack.materials.prepare_subject_sha256
    hook.run_approve(approve_ctx, pick=1, actor='test:文案审阅者',
                     reason='合成候选与原文、全书成稿已对照', review_token=pack.review_token)
    from contracts.rules import REGISTRY
    checked = hook.run_check_only(_ctx(book, 'stage6.hook', paid=False))
    assert checked.passed and checked.counts.skipped == 0, checked.model_dump_json()
    assert {row.rule for row in checked.rules} == {rule.rule for rule in REGISTRY.of_stage('stage6')}
    preparation = {row.rule: row for row in checked.rules if row.rule.startswith('prepare_')}
    assert preparation['prepare_evidence_valid'].status == 'ok'
    assert preparation['prepare_semantic_review'].status == 'unver'
    assert '当前 Prepare 已经独立接受' in preparation['prepare_semantic_review'].why
    assert approvals.get(book.parent, hook_ctx.sid).gate['s6']
    grid, identity_report = hook.run_identity(_ctx(book, 'stage6.hook', paid=False))
    assert not set(preparation).intersection(grid), '准备接受不是原作文案字符串的 0/1 恒等格'
    identity_preparation = {row.rule: row for row in identity_report.rules if row.rule.startswith('prepare_')}
    assert identity_preparation == preparation, '恒等报告也须实际核验同一 Prepare 前置'


def complete_stage7(book):
    from novel.stages.stage7 import rename, assemble
    rename_ctx = _ctx(book, 'stage7.rename')
    def rename_characters(req, _index):
        assert 'fact_atoms' not in req.user
        return _ok({'surname_map': [{'old_surname': '李', 'new_surname': '顾'}], 'characters': [
            {'char_id': 'char_001', 'renamed': True, 'new_canonical': '顾清棠',
             'not_renamed_reason': None, 'aliases': [], 'reason': '替换真实姓名。'},
            {'char_id': 'char_002', 'renamed': False, 'new_canonical': '母亲',
             'not_renamed_reason': '只有亲属称谓，无本名。', 'aliases': [], 'reason': '保留亲属称谓。'},
        ]})
    rename_client, rename_wire = _client(rename_ctx, rename_characters)
    rename.run_rename(rename_ctx, rename_client)
    assert len(rename_wire.calls) == 1
    approve_ctx = _ctx(book, 'stage7.approve', paid=False)
    pack = rename.approval_workpack(ArtifactStore(approve_ctx))
    rename.run_approve(approve_ctx, actor='test:改名审阅者',
                       reason='合成角色姓名替换与亲属关系已复核', review_token=pack.review_token)
    assembled_ctx = _ctx(book, 'stage7.assemble', paid=False)
    path, _map, report = assemble.run_assemble(assembled_ctx)
    assert report.passed, report.model_dump_json()
    from pathlib import Path
    result = Path(path).read_text(encoding='utf-8')
    assert '顾清棠' in result and '李宝珠' not in result and '母亲' in result
    return book


def build_plot_pipeline(tmp_path, monkeypatch):
    """用正式 producer 建立小型整链盘面，供消费与失效测试准备上游。"""
    book = produce_through_first_chapter(
        tmp_path, monkeypatch, distributed=True, grounding=grounded_global())
    complete_stage5(book)
    complete_stage6(book)
    return complete_stage7(book)


def test_gate_b_finishes_all_written_chapters_and_reads_them_after_restart(tmp_path, monkeypatch):
    """Gate B 停在 Stage5；完整成书仍由下面 Stage7 用例独立验收。"""
    from contracts.schema.stage5 import Chapter
    from novel.stages.stage5 import review
    book = produce_through_first_chapter(tmp_path, monkeypatch, distributed=True,
                                         grounding=grounded_global())
    complete_stage5(book)
    ctx = _ctx(book, 'stage5.review', paid=False)
    store = ArtifactStore(ctx)
    reopened = review.load_book(ctx, store)
    assert set(reopened.chapters) == {1, 2, 3}
    for ordinal in reopened.chapters:
        artifact, _ = store.read_own(Chapter, f'stage5/ch{ordinal:02d}.json', require_current=True)
        assert artifact.segments
    assert approvals.get(book.parent, ctx.sid).gate['s5']
    assert not store.exists('stage6/hook.json')


def test_plot_pipeline_reaches_final_delivery_and_restarts(tmp_path, monkeypatch):
    from novel.stages.stage7 import assemble
    book = build_plot_pipeline(tmp_path, monkeypatch)
    # 重新创建读取上下文，最终正式账与文本仍一致；没有重用内存审批状态。
    verified = assemble.read_current_book(_ctx(book, 'stage7.assemble', paid=False))
    from novel import paths
    assert verified == (book / paths.book_txt_rel(book)).read_text(encoding='utf-8')
