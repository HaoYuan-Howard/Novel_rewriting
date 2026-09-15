"""离线两模式联调：合成模型回答，真实 producer、审批、CLI、Supervisor 与 HTTP。

模型替身只证明工程链路，不作为语义质量或真实用量验收。自动部分从当前上游
和缺失的成书开始，只能执行免费组装；任何模型调用或额外动作都使场景失败。
由 test_delivery_modes 在真实干净 Git 快照中执行，不绕过启动身份守卫。
"""
from pathlib import Path
import json
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest
from fastapi.testclient import TestClient
from contracts.schema.stage2 import ChapterBlueprints
from contracts.schema.stage4 import PlanChapters
from novel.api.app import create_app, UI_HEADER
from novel import paths
from novel.store import ArtifactStore
from novel.stages.stage2 import extract
from novel.stages.stage4 import plan
from novel.stages.stage5 import write, review, measure
from novel.stages.stage6 import hook, semantic
from novel.stages.stage7 import rename
from novel.orchestrator import jobs
from tests.test_stage2_uploaded_flow import uploaded, _ctx, SID, PREFIX, _get
from tests.test_stage2_generation import _client as blueprint_client, _respond_blueprints, _response_blueprints
from tests.test_plot_pipeline import REWRITTEN_BEATS
from tests.test_stage4_v3_integration import _client, _split_a_responder


BEAT_TEXT = {
    'b01_01': '昨夜来信。\n\n信里说起父亲。\n\n他已回城。',
    'b01_02': '李宝珠倚着老宅的廊柱。\n\n春风卷着残花掠过院墙。\n\n她想起昨夜的信，将信纸折起，收进衣袖。\n\n目光仍停在院中落花上，远处传来几声梆子。',
    **REWRITTEN_BEATS,
}


def design_response(blueprints):
    answer = _split_a_responder(blueprints)

    def respond(request):
        raw = answer(request)
        if 'scenes' not in raw:
            return raw
        public = json.loads(request.user.split(plan.prompts.BLOCK_BLIND_CHAPTER_TRUTH + '\n')[1])
        ordinal = public['chapter']['chapter_ordinal']
        if ordinal == 2:
            # 母亲已知来信是归家段落的披露，完整节点仍独立引用，不另造一个等长空场。
            first = raw['scenes'][0]['scene_ref']
            raw['scenes'] = raw['scenes'][:1]
            for placement in raw['beat_placements']:
                placement['deliveries'][0]['scene_ref'] = first
            for key in ('chapter_goal', 'open_hook_goal', 'end_hook_goal'):
                raw[key]['anchor_scene_ref'] = first
        elif ordinal == 3:
            by_alias = {beat['alias']: beat['content'] for beat in public['chapter']['beats']}
            for scene in raw['scenes']:
                content = by_alias[scene['turn_beat_alias']]
                # 长行动段、短回忆和添茶按真实呈现任务分配篇幅；不靠补写重复剧情凑段数。
                scene['weight'] = 'W_STRONG' if '管家' in content else 'W_QUICK' if '童年' in content else 'W_MID'
        assert len(raw['beat_placements']) == len(public['chapter']['beats'])
        return raw
    return respond


def post(http, suffix, payload=None):
    response = http.post(PREFIX + suffix, json=payload)
    assert response.status_code == 200, (suffix, response.text)
    value = response.json()
    if 'exit_code' in value:
        assert value['exit_code'] in (0, 1), http.get(f'/api/jobs/{value["job_id"]}/log').json()
    print('POST_OK', suffix, flush=True)
    return value


def _run_free_next(http, *, node, flags):
    route = _get(http, '/operator-next')
    assert route['state'] == 'ready', route
    assert route['action']['node'] == node and route['action']['flags'] == flags, route['action']
    assert route['action']['paid'] is False
    request = {'sid': SID, 'node': node, 'flags': flags}
    preview = http.post('/api/jobs/preview', json=request)
    assert preview.status_code == 200 and preview.json()['paid'] is False, preview.text
    response = http.post('/api/jobs', json=request)
    assert response.status_code == 202, response.text
    job_id = response.json()['job_id']
    deadline = time.monotonic() + 300
    while not jobs.wait(job_id, 10):
        print('WAIT_FREE_CLI', node, flush=True)
        assert time.monotonic() < deadline, http.get(f'/api/jobs/{job_id}/log').json()
    record = http.get(f'/api/jobs/{job_id}').json()
    assert record['exit_code'] == 0, http.get(f'/api/jobs/{job_id}/log').json()
    print('FREE_CLI_OK', node, flags, flush=True)


def build(base):
    with pytest.MonkeyPatch.context() as env:
        env.delenv('DEEPSEEK_API_KEY', raising=False)
        env.setenv('NOVEL_STAGE2_BLUEPRINT_CONTRACT', 'plot-blueprint-v1')
        if paths.find_novel_dir(base, SID) is None:
            uploaded.__wrapped__(base, env)
        env.setenv('NOVEL_STAGE2_BLUEPRINT_CONTRACT', 'plot-blueprint-v1')
        book = paths.resolve_novel_dir(base, SID)
        with TestClient(create_app(base)) as http:
            http.headers[UI_HEADER] = '1'
            if not (book / 'stage2/chapter_blueprints.json').is_file():
                ctx = _ctx(base, 'stage2.blueprint', paid=True)
                client, wire = blueprint_client(ctx, lambda request, _n: _respond_blueprints(request, _response_blueprints()))
                assert extract.run_blueprint(ctx, client).passed
                assert len(wire.calls) == 3
            # Stage2 语义审阅已退役：蓝图生成后人工批准 s1 门，经统一机械准入进入 Stage4。
            if not _get(http, '/approvals')['gate']['s1']:
                post(http, '/approvals/gate/s1', None)
            bp = ArtifactStore(_ctx(base, 'stage4.design')).read_own(ChapterBlueprints, 'stage2/chapter_blueprints.json')[0]
            bp_json = bp.model_dump(by_alias=True, mode='json')
            if not (book / plan.CANDIDATE_REL).is_file():
                ctx = _ctx(base, 'stage4.design', paid=True)
                client, wire = _client(ctx, design_response(bp_json))
                plan.run_design(ctx, client)
            if _get(http, '/approvals')['design_review']['state'] != 'accepted':
                response = http.get(PREFIX + '/approvals/design/template', params={
                    'actor': 'human-ui', 'reviewer_kind': 'human', 'review_run_id': 'offline-design',
                    'reason': '仅合成链验证，明确接受完整剧情与分场结构'})
                assert response.status_code == 200, response.text
                template = response.json()
                post(http, '/approvals/design', {
                    'actor': template['actor'], 'reviewer_kind': template['reviewer_kind'],
                    'review_run_id': template['review_run_id'], 'general_reason': template['general_reason'],
                    'review_token': template['review_token'], 'strategy': {'decision': 'accept', 'reason': '保留完整剧情语义'},
                    'chapters': [{'output_ordinal': row['output_ordinal'], 'decision': 'accept', 'reason': '完整节点的联合呈现、归因与披露一致'} for row in template['chapters']]})
            if not (book / plan.REL).is_file():
                route = _get(http, '/operator-next')
                print('PLAN_ACTION', route['action'], flush=True)
                _run_free_next(http, node='stage4.plan', flags=route['action']['flags'])
            if _get(http, '/approvals')['structure']['state'] != 'accepted':
                pack = _get(http, '/approvals/structure/workpack')
                post(http, '/approvals/structure', {
                    'actor': 'human-ui', 'reason': '合成联调逐章确认节点、证据和分场结构',
                    'review_token': pack['review_token'],
                    'chapters': [{'output_ordinal': ordinal, 'decision': 'accept',
                                  'reason': '完整节点联合呈现，无新增剧情或提前披露，原章顺序保留'}
                                 for ordinal in pack['chapter_ordinals']]})
            if not (book / plan.CTX_REL).is_file():
                _run_free_next(http, node='stage4.context', flags=[])
            print('STAGE4_OK', flush=True)
            approved = _get(http, '/approvals')
            if not approved['writing']:
                post(http, '/approvals/writing', {
                    'actor': 'human-ui', 'reason': '明确授权合成模型测试，不调用外部模型',
                    'review_token': approved['writing_approval']['review_token']})
            ctx = _ctx(base, 'stage5.write', paid=True)
            store = ArtifactStore(ctx)
            planned = store.read_own(PlanChapters, plan.REL)[0]
            material = review.load_book(ctx, store)
            for chapter in planned.output_chapters:
                ordinal = chapter.output_ordinal
                if store.exists(write._rel(ordinal)):
                    continue
                chapter_material = material.chapters[ordinal]
                paragraph_plan = measure.paragraph_slot_plan(chapter_material, material)
                assert len(chapter_material['scenes']) == len(chapter.scenes) == len(paragraph_plan)
                segments = []
                for scene, delivery_scene, paragraphs in zip(chapter.scenes, chapter_material['scenes'], paragraph_plan):
                    assert scene.slot == delivery_scene['slot']
                    text = '\n\n'.join(BEAT_TEXT[row['beat_ref']] for row in delivery_scene['deliveries'])
                    shaped = measure.reflow_shadow(text, target=paragraphs)
                    assert shaped, (ordinal, scene.slot, paragraphs, text)
                    segments.append({'slot': scene.slot, 'text': shaped})
                writer = _ctx(base, 'stage5.write', paid=True)
                client, wire = _client(writer, {'segments': segments})
                report = write.run_write(writer, client, chapters=str(ordinal))
                assert report.passed, report.model_dump(mode='json')
                print('DRAFT_OK', ordinal, len(wire.calls), flush=True)
            if not _get(http, '/approvals')['gate']['s5']:
                route = _get(http, '/operator-next')
                if route['state'] == 'ready':
                    _run_free_next(http, node='stage5.review', flags=route['action']['flags'])
                template = _get(http, '/approvals/stage5/template')
                assert not template['must_appear'], template['must_appear']
                post(http, '/approvals/stage5', {
                    'actor': 'human-ui', 'reason': '合成联调核对完整剧情、归因与揭示；统计与语义未知仍披露',
                    'review_token': template['review_token'], 'must_appear': [],
                    'group_acks': [{'group': row['group'], 'acknowledged': True}
                                   for row in template['group_acks']]})
            if not (book / 'stage6/prepare.json').is_file():
                # 独立文案准备（Stage6 前置）：真实 Prepare session + 人工接受。
                from tests.test_plot_pipeline import complete_stage6_prepare
                complete_stage6_prepare(book)
                print('PREPARE_OK', flush=True)
            if not (book / hook.REL).is_file():
                from tests.test_stage6 import _fake_client
                ctx = _ctx(base, 'stage6.hook', paid=True)
                hook.run_hook(ctx, _fake_client(ctx, [
                    '一封来信说父亲回了城，李宝珠收好信，却发现母亲早已知情。院门开合，账册翻动，她端起新茶，一口也没有喝。',
                    '父亲的归信藏进衣袖，院门外照常叫卖。李宝珠带回两个包子，等她的母亲早就知道了信里的事。',
                    '米价又涨了，管家候在门外。李宝珠翻着旧账，眼前却是父亲从前的身影；新茶烫手，她迟迟没有入口。',
                ]))
            if not (book / semantic.SEMANTIC_TPL.format(n=1)).is_file():
                from tests.test_stage6_semantic import _response_review
                ctx = _ctx(base, 'stage6.semantic', paid=True)
                prepared = semantic.prepare_semantic_candidate(ctx)
                payload = {
                    'reviews': [_response_review(neutral, text=text)
                                for neutral, _real, text in prepared.aliases],
                    'ranking': [neutral for neutral, _real, _text in prepared.aliases],
                }
                client, wire = _client(ctx, payload, concurrency=2)
                _artifact, report = semantic.run_semantic(ctx, client)
                assert report.passed, report.model_dump(mode='json')
                assert len(wire.calls) == 2
                print('SEMANTIC_OK', flush=True)
            stage6 = _get(http, '/approvals/stage6/workpack')
            if stage6['disposition'] != 'already_current':
                post(http, '/stage6/approve', {
                    'pick': 1, 'actor': 'human-ui', 'reason': '合成事实核对，明确接受候选并保留未标定披露',
                    'review_token': stage6['review_token']})
            if not (book / rename.REL).is_file():
                ctx = _ctx(base, 'stage7.rename', paid=True)
                _system, _user, mat = rename.build_prompt(ctx)
                names = {'李宝珠': '顾明珠'}
                rows = []
                for cid, character in mat.chars.items():
                    old = character['canonical']
                    new = names.get(old, old)
                    rows.append({'char_id': cid, 'new_canonical': new, 'renamed': new != old,
                                 'not_renamed_reason': None if new != old else '通用称谓保持',
                                 'aliases': [{'old': row['surface'], 'new': names.get(row['surface'], row['surface'])}
                                             for row in character['aliases']],
                                 'reason': '合成改名保持身份与称谓结构'})
                client, wire = _client(ctx, {'surname_map': [{'old_surname': '李', 'new_surname': '顾'}], 'characters': rows})
                rename.run_rename(ctx, client)
                print('RENAME_OK', len(wire.calls), flush=True)
            stage7 = _get(http, '/approvals/stage7/workpack')
            if stage7['disposition'] != 'already_current':
                post(http, '/stage7/approve', {
                    'actor': 'human-ui', 'reason': '合成改名逐项核实；实际替换、保留与残留均已查看',
                    'review_token': stage7['review_token']})
            if not (book / paths.book_txt_rel(book)).is_file():
                _run_free_next(http, node='stage7.assemble', flags=[])
            result = _get(http, '/stage7')
            assert result['delivery']['state'] in {'ready', 'ready_with_disclosures'}, result['delivery']
            response = http.get(PREFIX + '/stage7/book.txt')
            assert response.status_code == 200, response.text
            assert response.content == (book / paths.book_txt_rel(book)).read_bytes()
            assert not list(book.rglob('*.md'))
            print('MANUAL_DELIVERY_OK', result['delivery'], flush=True)
    return book


def automatic(base):
    from novel.orchestrator import agent
    from novel.llm.ledger import ledger_path
    from tests.test_agent import _PROFILES

    book = paths.resolve_novel_dir(base, SID)
    target = book / paths.book_txt_rel(book)
    content = target.read_bytes()
    ledger = ledger_path(base).read_bytes()
    dispatched = []
    real_argv = agent._run_step_argv

    # Commander 角色已删：自动跑书是 executor-only，任何派发都必须经 run_step。
    # 只放行免费的 stage7.assemble；其余节点（尤其付费节点）直接断言失败，
    # 因此「自动免费收尾不购买任何模型响应」仍被此替身钉住。
    def only_free_assembly(base, sid, epoch, permit, **kwargs):
        assert permit.node == 'stage7.assemble' and permit.paid is False and permit.flags == []
        argv = real_argv(base, sid, epoch, permit, **kwargs)
        dispatched.append(argv)
        return argv

    with pytest.MonkeyPatch.context() as env:
        env.delenv('DEEPSEEK_API_KEY', raising=False)
        env.setenv('NOVEL_STAGE2_BLUEPRINT_CONTRACT', 'plot-blueprint-v1')
        env.setattr(agent, '_run_step_argv', only_free_assembly)
        target.unlink()
        with TestClient(create_app(base)) as http:
            http.headers[UI_HEADER] = '1'
            request = {'sid': SID, 'authorized': True, 'egress_authorized': True,
                       'profiles': _PROFILES.model_dump(mode='json')}
            response = http.post('/api/agent/runs/preview', json=request)
            assert response.status_code == 200, response.text
            response = http.post('/api/agent/runs', json=request)
            assert response.status_code == 202, response.text
            run_id = response.json()['run_id']
            deadline = time.monotonic() + 300
            while not agent.wait(run_id, 10):
                print('WAIT_AUTOMATIC', run_id, flush=True)
                assert time.monotonic() < deadline, agent.run_log_path(base, run_id).read_text(encoding='utf-8')
            record = next(row for row in agent.records(base) if row.run_id == run_id)
            assert record.state == 'ok', (record.model_dump(mode='json'), agent.run_log_path(base, run_id).read_text(encoding='utf-8'))
            assert record.outcome.state in {'ready', 'ready_with_disclosures'}
            assert all(epoch.identity_version == 2 and epoch.skill_sha256 is None for epoch in record.epochs)
            assert dispatched and all('novel.orchestrator.run_step' in argv for argv in dispatched)
            assert target.read_bytes() == content
            assert ledger_path(base).read_bytes() == ledger
            assert not list(book.rglob('*.md'))
            metrics = _get(http, '/run/metrics')
            print('AUTOMATIC_DELIVERY_OK', record.outcome.model_dump(mode='json'), flush=True)
            print('METRICS_KEYS', sorted(metrics), flush=True)
        with TestClient(create_app(base)) as reopened:
            response = reopened.get(PREFIX + '/stage7/book.txt')
            assert response.status_code == 200 and response.content == content, response.text
            assert _get(reopened, '/stage7')['delivery']['state'] in {'ready', 'ready_with_disclosures'}
            print('RESTART_DELIVERY_OK', flush=True)
    return book


if __name__ == '__main__':
    base = Path(sys.argv[1])
    assert not base.exists(), '场景必须从独立空输出根开始，不能采用预制交付结论'
    base.mkdir(parents=True)
    build(base)
    automatic(base)
