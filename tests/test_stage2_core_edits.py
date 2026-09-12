"""统一 Commander 的原决定先落盘，再由独立 owner 免费提交及恢复。"""
from __future__ import annotations

import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from contracts.artifacts import STAGE2_CHARACTERS, STAGE2_GLOBAL_GROUNDING, STAGE2_GLOBAL_REVIEW
from contracts.schema.api import Stage2NodeReviewRequest
from contracts.schema.characters import CharacterRegistry, CharactersEdit
from contracts.schema.evaluation import GlobalReview
from contracts.schema.knowledge import KnowledgeGrounding
from novel import paths
from novel.api.app import UI_HEADER, create_app
from novel.errors import ArtifactUnreadable, NovelError
from novel.node_agents import Stage2AnalyzeAgent
from novel.orchestrator import approvals, jobs, queue
from novel.stages.stage2 import characters, core_edits, extract, global_review
from novel.store import ArtifactStore
from tests.test_stage2_analyze import _ctx, _driver
from tests.test_stage2_global_agent import _free_ctx


def seed(tmp_path):
    ctx = _ctx(tmp_path)
    Stage2AnalyzeAgent(ctx, driver=_driver(ctx)).run()
    ctx = _free_ctx(ctx)
    ctx.node = 'stage2.global'
    return ctx


def test_global_commander_built_prompt_matches_current_core_boundaries(tmp_path, monkeypatch):
    """从真实 core 和工作包 producer 构造最终提示；仅省去只读 CLI 的进程运输。"""
    from types import SimpleNamespace
    from novel.orchestrator import inspection, reviewer_tasks
    from tests.test_stage2 import main_text
    ctx = seed(tmp_path)

    def read_command(argv, **_kwargs):
        if '--emit-review-workpack' in argv:
            return global_review.review_workpack(ctx)
        if '--emit-review-template' in argv:
            return json.loads(global_review.review_template_json(ctx))
        pytest.fail(f'Global 首轮不应读取其他阶段材料：{argv}')

    monkeypatch.setattr(reviewer_tasks, '_json_command', read_command)
    current = SimpleNamespace(
        state='blocked', code_version=ctx.code,
        surface_seal=inspection.read_surface_seal(tmp_path, ctx.sid, code=ctx.code),
        action=SimpleNamespace(waiting_on=queue.WAITING_ON_STAGE2_REVIEW,
                               action_sha256='a' * 64))
    target = paths.scratch_session_dir(tmp_path, ctx.sid, 'core-prompt') / 'request.json'
    task = reviewer_tasks.build_task(tmp_path, ctx.sid, current, target, epoch_id='core-prompt')
    assert task is not None and not target.exists()
    assert 'secret/secret_setup/reveal 项可以按完整 secret 组提交' in task.prompt
    assert '人物、setting、关系、规则及集合完整性均为 must_fix，不得隔离' in task.prompt
    assert '同响应 core_edits.global 的关系端点可引用本次 add 的 cN' in task.prompt
    assert '独立 GlobalEdit 没有这份新增映射，不得使用 cN' in task.prompt
    for obsolete in ('secret/relation/world_rule 才能', '钩子独立证据',
                     'knowledge_links.pending', 'trigger_fact_ref', 'Blueprint producer --close-review'):
        assert obsolete not in task.prompt
    for required in ('role 只能描述首次公开身份', '关键匿名个体', '每实体每个 surface 只登记一次',
                     '事实生效、失效、读者揭示和证据章独立', '同场产生并当场揭晓的局部惊喜不得晋升'):
        assert required in task.prompt
    envelope = json.loads(task.prompt.split('<immutable-workpack>\n', 1)[1].split(
        '\n</immutable-workpack>', 1)[0])
    material = envelope['materials']['global']
    assert material['main_text_chapters'] == main_text()
    assert any(row['kind'] == 'alias_identity'
               for card in material['subject_cards'] for row in card['items'])
    control = next(option for option in task.submission_options
                   if option['id'] == 'approval:stage2_global_review')
    assert 'core_edits.schema' in control['allowed_values']
    assert '由代码冻结正式ID并统一重写' in control['allowed_values']['core_edits.purpose']


def snapshot(ctx):
    return {rel: ArtifactStore(ctx).path(rel).read_bytes() for rel in (
        STAGE2_CHARACTERS, STAGE2_GLOBAL_GROUNDING, STAGE2_GLOBAL_REVIEW)}


def request_for(ctx, *, evidence_only=False, global_edit=True):
    store = ArtifactStore(ctx)
    pack = global_review.review_workpack(ctx)
    char_ctx = _free_ctx(ctx)
    char_ctx.node = characters.NODE
    edit = characters.edit_template(char_ctx)
    edit['operations'] = edit['operations'][:1]
    row = edit['operations'][0]['character']
    if evidence_only:
        row['aliases'][0]['evidence_refs'] = [
            {'chapter_ordinal': 1, 'locator': '李宝珠站在廊下'}]
    else:
        row['role'] = '站在老宅廊下的女子'
        row['role_evidence_refs'] = [
            {'chapter_ordinal': 1, 'locator': '李宝珠站在廊下看着落花。'}]
    coverage = pack['review_coverage']
    problem = next(item for item in coverage['scope']['obligations']
                   if item['owner'] == 'stage2.characters')
    return Stage2NodeReviewRequest.model_validate({
        'node': 'stage2.global', 'review_token': pack['review_token'],
        'decision': 'rework', 'actor': 'test:commander', 'reason': '依据原文订正人物与长期背景',
        'review': {'scope_sha256': coverage['scope']['scope_sha256'],
                   'default_decision': 'accept', 'exceptions': [{
                       'obligation_id': problem['obligation_id'], 'decision': 'rework',
                       'reason': '首次身份或归属证据需要本轮明确订正'}]},
        'core_edits': {
            'expected_characters_sha256': store.full_sha256_or_none(STAGE2_CHARACTERS),
            'expected_global_sha256': store.full_sha256_or_none(STAGE2_GLOBAL_GROUNDING),
            'characters': edit,
            'global': {'setting': {
                'social_context': '城中老宅的家庭生活',
                'social_context_evidence_refs': [
                    {'chapter_ordinal': 1, 'locator': '春风吹过老宅的院墙。'}],
            }} if global_edit else None,
        },
    })


def _seed_with_relation(tmp_path):
    from tests.test_stage2_analyze import _response
    ctx = _ctx(tmp_path)
    response = _response()
    response['global']['relations'] = [dict(
        relation_ref='r1', subject_ref='c2', object_ref='c1', predicate='母亲',
        effective_from=2, effective_until=None, revealed_from=2, evidence_refs=['e000011'])]
    Stage2AnalyzeAgent(ctx, driver=_driver(ctx, response)).run()
    ctx = _free_ctx(ctx)
    ctx.node = 'stage2.global'
    return ctx


def _add_character_request(ctx, *, shape='replacement'):
    from novel.quality.global_review import subject_sha256
    raw = request_for(ctx, global_edit=False).model_dump(mode='json', by_alias=True)
    proof = {'chapter_ordinal': 1, 'locator': '信上说父亲已经回城。'}
    raw['core_edits']['characters']['operations'] = [dict(kind='add', character_ref='c9', character=dict(
        canonical='父亲', aliases=[dict(surface='父亲', identity_revealed_from=1, evidence_refs=[proof])],
        first_chapter=1, first_appearance_evidence_refs=[proof], role='李宝珠之父',
        role_evidence_refs=[proof], name_distinctiveness='common'))]
    obligation = next(row for row in global_review.review_workpack(ctx)['review_coverage']['scope']['obligations']
                      if row['subject_path'] == 'characters.characters')
    raw['review']['exceptions'] = [dict(obligation_id=obligation['obligation_id'],
        decision='rework', reason='正文已有父亲的独立前史，人物登记需补齐')]
    relation = dict(relation_id='rel_02', subject_id='c9', object_id='char_001', predicate='父亲',
                    effective_from=1, effective_until=None, revealed_from=1, evidence_refs=[proof])
    current = ArtifactStore(ctx).read_own(KnowledgeGrounding, STAGE2_GLOBAL_GROUNDING)[0]
    if shape == 'replacement':
        edit = {'relations_replace': [relation]}
    elif shape == 'patch':
        edit = {'relations': [{**relation, 'relation_id': 'rel_01'}]}
    else:
        if shape == 'add':
            operation = {'op': 'add', 'item': relation}
        else:
            retained = current.relations[0].model_dump(mode='json')
            retained['relation_id'] = 'rel_03'
            operation = {'op': 'split', 'subject_id': 'rel_01', 'items': [relation, retained]}
        edit = {'collection_edits': [dict(collection='relations',
            expected_collection_sha256=subject_sha256([row.model_dump(mode='json') for row in current.relations]),
            operations=[operation])]}
    raw['core_edits']['global'] = edit
    return Stage2NodeReviewRequest.model_validate(raw)


@pytest.mark.parametrize('shape', ['replacement', 'patch', 'add', 'split'])
def test_same_response_new_character_refs_compile_in_every_relation_edit_shape(tmp_path, shape):
    ctx = _seed_with_relation(tmp_path)
    original = snapshot(ctx)
    ledger = ctx.ledger.path.read_bytes()
    request = _add_character_request(ctx, shape=shape)
    identity = approve(ctx, request)
    directory = paths.stage2_core_edit_dir(ctx.novel_dir, identity)
    frozen = core_edits.PreparedCoreEdits.model_validate_json((directory / 'prepared.json').read_bytes())
    mapping = frozen.characters_candidate.provenance.characters_edit.added_character_ids
    assert mapping == {'c9': 'char_003'}
    assert frozen.request == request and snapshot(ctx) == original
    target = next(row for row in frozen.global_candidate.relations if row.predicate == '父亲')
    assert target.subject_id == mapping['c9'] and target.object_id == 'char_001'
    assert 'c9' in json.dumps(request.model_dump(mode='json'), ensure_ascii=False)
    core_edits.apply(ctx, identity)
    landed = ArtifactStore(ctx).read_own(KnowledgeGrounding, STAGE2_GLOBAL_GROUNDING)[0]
    assert next(row for row in landed.relations if row.predicate == '父亲').subject_id == mapping['c9']
    assert ArtifactStore(ctx).read_own(CharacterRegistry, STAGE2_CHARACTERS)[0].characters[mapping['c9']].canonical == '父亲'
    assert ctx.ledger.path.read_bytes() == ledger
    assert any(row.state == 'pending' for row in ArtifactStore(ctx).read_own(GlobalReview, STAGE2_GLOBAL_REVIEW)[0].items)


def test_new_character_mapping_survives_partial_submit_and_cli_replay(tmp_path, monkeypatch):
    from novel.cli.main import _run_stage2
    ctx = _seed_with_relation(tmp_path)
    request = _add_character_request(ctx)
    identity = approve(ctx, request)
    directory = paths.stage2_core_edit_dir(ctx.novel_dir, identity)
    frozen = (directory / 'prepared.json').read_bytes()
    ledger = ctx.ledger.path.read_bytes()
    real_commit = extract.commit_core_edit

    def interrupted(*_args, **_kwargs):
        raise KeyboardInterrupt('新增人物已提交，Global 提交前退出')

    monkeypatch.setattr(extract, 'commit_core_edit', interrupted)
    with pytest.raises(KeyboardInterrupt):
        core_edits.apply(ctx, identity)
    registry = ArtifactStore(ctx).read_own(CharacterRegistry, STAGE2_CHARACTERS)[0]
    assert registry.provenance.characters_edit.added_character_ids == {'c9': 'char_003'}
    monkeypatch.setattr(extract, 'commit_core_edit', real_commit)
    assert _run_stage2(_free_ctx(ctx), ['--apply-core-edits', identity]) == 0
    landed = snapshot(ctx)
    assert _run_stage2(_free_ctx(ctx), ['--apply-core-edits', identity]) == 0
    assert snapshot(ctx) == landed
    assert (directory / 'prepared.json').read_bytes() == frozen
    assert ctx.ledger.path.read_bytes() == ledger
    assert len(ArtifactStore(ctx).read_own(CharacterRegistry, STAGE2_CHARACTERS)[0].characters) == 3


def test_unknown_new_character_ref_is_rejected_before_any_owner_or_decision_write(tmp_path):
    ctx = _seed_with_relation(tmp_path)
    raw = _add_character_request(ctx).model_dump(mode='json', by_alias=True)
    raw['core_edits']['global']['relations_replace'][0]['subject_id'] = 'c8'
    request = Stage2NodeReviewRequest.model_validate(raw)
    original = snapshot(ctx)
    ledger = ctx.ledger.path.read_bytes()
    with pytest.raises(NovelError, match='本批 Characters add'):
        approvals.approve_stage2_review(tmp_path, ctx.sid, request)
    assert snapshot(ctx) == original and approvals.stage2_review_history(tmp_path, ctx.sid) == []
    assert ctx.ledger.path.read_bytes() == ledger
    assert not (paths.stage2_core_edit_dir(ctx.novel_dir, core_edits.request_sha(request)) / 'prepared.json').exists()


def test_core_global_endpoint_cannot_guess_this_batch_new_persistent_id(tmp_path):
    ctx = _seed_with_relation(tmp_path)
    raw = _add_character_request(ctx).model_dump(mode='json', by_alias=True)
    # 编译器将为 c9 分配这个值；恰好猜中也没有局部句柄的语义绑定权。
    raw['core_edits']['global']['relations_replace'][0]['subject_id'] = 'char_003'
    request = Stage2NodeReviewRequest.model_validate(raw)
    original = snapshot(ctx)
    ledger = ctx.ledger.path.read_bytes()
    with pytest.raises(NovelError, match='不能猜测正式 char_id'):
        approvals.approve_stage2_review(tmp_path, ctx.sid, request)
    assert snapshot(ctx) == original and approvals.stage2_review_history(tmp_path, ctx.sid) == []
    assert ctx.ledger.path.read_bytes() == ledger
    assert not (paths.stage2_core_edit_dir(ctx.novel_dir, core_edits.request_sha(request)) / 'prepared.json').exists()


def test_previous_add_receipt_does_not_bind_a_later_global_only_request(tmp_path):
    ctx = _seed_with_relation(tmp_path)
    core_edits.apply(ctx, approve(ctx, _add_character_request(ctx)))
    raw = request_for(ctx).model_dump(mode='json', by_alias=True)
    raw['core_edits']['characters'] = None
    raw['core_edits']['global'] = {'relations': [{'relation_id': 'rel_02', 'object_id': 'c9',
        'evidence_refs': [{'chapter_ordinal': 1, 'locator': '信上说父亲已经回城。'}]}]}
    request = Stage2NodeReviewRequest.model_validate(raw)
    original = snapshot(ctx)
    history = approvals.stage2_review_history(tmp_path, ctx.sid)
    with pytest.raises(NovelError, match='本批 Characters add'):
        approvals.approve_stage2_review(tmp_path, ctx.sid, request)
    assert snapshot(ctx) == original and approvals.stage2_review_history(tmp_path, ctx.sid) == history


def test_core_batch_cannot_guess_new_persistent_id_for_same_response_replace(tmp_path):
    import copy
    ctx = _seed_with_relation(tmp_path)
    raw = _add_character_request(ctx).model_dump(mode='json', by_alias=True)
    added = raw['core_edits']['characters']['operations'][0]
    raw['core_edits']['characters']['operations'].append(dict(
        kind='replace', char_id='char_003', character=copy.deepcopy(added['character'])))
    request = Stage2NodeReviewRequest.model_validate(raw)
    original = snapshot(ctx)
    ledger = ctx.ledger.path.read_bytes()
    with pytest.raises(NovelError):
        approvals.approve_stage2_review(tmp_path, ctx.sid, request)
    assert snapshot(ctx) == original and approvals.stage2_review_history(tmp_path, ctx.sid) == []
    assert ctx.ledger.path.read_bytes() == ledger
    assert not (paths.stage2_core_edit_dir(ctx.novel_dir, core_edits.request_sha(request)) / 'prepared.json').exists()


def approve(ctx, request):
    approvals.approve_stage2_review(ctx.novel_dir.parent, ctx.sid, request)
    identity = core_edits.request_sha(request)
    assert core_edits.pending(ctx.novel_dir.parent, ctx.sid) == identity
    return identity


def test_queue_cold_start_uses_joint_owner_and_partial_submit_keeps_exact_run(tmp_path, monkeypatch):
    from novel.orchestrator import operator, execution_permit
    from novel.stages.stage2 import analyze, analyze_recovery
    ctx = _ctx(tmp_path)
    todo, blocked = queue.plan_book(tmp_path, ctx.sid, until_stage='stage2')
    nodes = [row.node for row in [*todo, *blocked]]
    assert 'stage2.analyze' in nodes
    assert 'stage2.characters' not in nodes and 'stage2.global' not in nodes
    original_write = ArtifactStore.write_round
    def stop_global(store, rel, value):
        if rel == analyze.GLOBAL:
            raise KeyboardInterrupt('首次联合提交人物后硬杀')
        return original_write(store, rel, value)
    monkeypatch.setattr(ArtifactStore, 'write_round', stop_global)
    with pytest.raises(KeyboardInterrupt):
        Stage2AnalyzeAgent(ctx, driver=_driver(ctx)).run()
    ledger = ctx.ledger.path.read_bytes()
    recovery = queue.stage2_core_recovery_item(tmp_path, ctx.sid)
    assert recovery.node == analyze.NODE
    assert recovery.flags == ['--recover-analyze-run', ctx.run_id]
    current = operator.next_action(tmp_path, ctx.sid)
    assert current.state == 'ready' and current.action.node == analyze.NODE
    assert current.action.flags == recovery.flags and current.action.paid is False
    permit = operator.permit(tmp_path, ctx.sid, analyze.NODE, recovery.flags)
    assert permit.paid is False
    execution_permit.revalidate_active_execution_surface(permit, flags=recovery.flags)
    monkeypatch.setattr(ArtifactStore, 'write_round', original_write)
    free = _free_ctx(ctx)
    analyze_recovery.recover_initial(free, ctx.run_id)
    assert queue.stage2_core_recovery_item(tmp_path, ctx.sid) is None
    assert ctx.ledger.path.read_bytes() == ledger
    assert analyze.cold_start_reason(ctx.novel_dir) is not None


def test_fixed_characters_route_saved_global_draft_to_free_recovery(tmp_path):
    from novel.stages.stage2 import analyze_recovery
    from tests.test_stage2_analyze import _awaiting_global
    ctx = _ctx(tmp_path)
    free = _awaiting_global(ctx)
    ledger = ctx.ledger.path.read_bytes()
    recovery = queue.stage2_core_recovery_item(tmp_path, ctx.sid)
    assert recovery.node == 'stage2.global' and recovery.flags == ['--recover-analyze']
    todo, blocked = queue.plan_book(tmp_path, ctx.sid, until_stage='stage2')
    assert [row.flags for row in todo] == [['--recover-analyze']] and blocked == []
    analyze_recovery.recover(free)
    assert queue.stage2_core_recovery_item(tmp_path, ctx.sid) is None
    assert ctx.ledger.path.read_bytes() == ledger


def test_core_batch_operator_permit_binds_exact_flags_and_prepared_bytes(tmp_path):
    from novel.orchestrator import operator, execution_permit, inspection
    ctx = seed(tmp_path)
    identity = approve(ctx, request_for(ctx))
    flags = ['--apply-core-edits', identity]
    current = operator.next_action(tmp_path, ctx.sid)
    assert current.state == 'ready'
    assert current.action.node == 'stage2.global' and current.action.flags == flags
    assert current.action.paid is False and current.inbox.total == 0
    permit = operator.permit(tmp_path, ctx.sid, 'stage2.global', flags)
    execution_permit.revalidate_active_execution_surface(permit, flags=flags)
    with pytest.raises(NovelError):
        execution_permit.revalidate_active_execution_surface(
            permit, flags=['--apply-core-edits', 'f' * 64])
    before = inspection.read_surface_seal(tmp_path, ctx.sid)
    plan = paths.stage2_core_edit_dir(ctx.novel_dir, identity) / 'prepared.json'
    plan.write_bytes(plan.read_bytes() + b' ')
    with pytest.raises(ArtifactUnreadable):
        inspection.read_surface_seal(tmp_path, ctx.sid)
    with pytest.raises(ArtifactUnreadable):
        execution_permit.revalidate_active_execution_surface(permit, flags=flags)
    assert before == current.surface_seal


def test_invalid_second_owner_preflight_preserves_first_owner_and_decision(tmp_path):
    ctx = seed(tmp_path)
    original = snapshot(ctx)
    ledger = ctx.ledger.path.read_bytes()
    request = request_for(ctx).model_dump(mode='json', by_alias=True)
    request['core_edits']['global']['setting']['social_context_evidence_refs'][0]['locator'] = '源文绝无此句'
    typed = Stage2NodeReviewRequest.model_validate(request)
    with pytest.raises(NovelError):
        approvals.approve_stage2_review(tmp_path, ctx.sid, typed)
    assert snapshot(ctx) == original
    assert approvals.stage2_review_history(tmp_path, ctx.sid) == []
    assert ctx.ledger.path.read_bytes() == ledger
    assert core_edits.pending(tmp_path, ctx.sid) is None


def test_saved_batch_partial_submit_restarts_without_rebuy_and_reopens_review(tmp_path, monkeypatch):
    ctx = seed(tmp_path)
    original = snapshot(ctx)
    ledger = ctx.ledger.path.read_bytes()
    request = request_for(ctx)
    identity = approve(ctx, request)
    decision = approvals.stage2_review_history(tmp_path, ctx.sid)[0]
    directory = paths.stage2_core_edit_dir(ctx.novel_dir, identity)
    raw_plan = (directory / 'prepared.json').read_bytes()
    assert hashlib.sha256(raw_plan).hexdigest() == decision.core_edit_prepared_sha256
    assert snapshot(ctx) == original  # service 仅持久化原决定和计划。
    assert queue.stage2_core_recovery_item(tmp_path, ctx.sid).flags == ['--apply-core-edits', identity]
    real_commit = extract.commit_core_edit
    def stopped(*args, **kwargs):
        raise KeyboardInterrupt('人物已提交，知识写前硬杀')
    monkeypatch.setattr(extract, 'commit_core_edit', stopped)
    with pytest.raises(KeyboardInterrupt):
        core_edits.apply(ctx, identity)
    assert snapshot(ctx)[STAGE2_CHARACTERS] != original[STAGE2_CHARACTERS]
    assert snapshot(ctx)[STAGE2_GLOBAL_GROUNDING] == original[STAGE2_GLOBAL_GROUNDING]
    assert core_edits.pending(tmp_path, ctx.sid) == identity
    # 原 token 已过期，重试同一原决定依然幂等，不购买另一轮 Commander。
    approvals.approve_stage2_review(tmp_path, ctx.sid, request)
    assert approvals.stage2_review_history(tmp_path, ctx.sid) == [decision]
    monkeypatch.setattr(extract, 'commit_core_edit', real_commit)
    core_edits.apply(_free_ctx(ctx), identity)
    store = ArtifactStore(ctx)
    chars = store.read_own(CharacterRegistry, STAGE2_CHARACTERS)[0]
    knowledge = store.read_own(KnowledgeGrounding, STAGE2_GLOBAL_GROUNDING)[0]
    assert chars.characters['char_001'].role == '站在老宅廊下的女子'
    assert knowledge.setting.social_context == '城中老宅的家庭生活'
    assert knowledge.provenance.core_edit.request_sha256 == identity
    assert store.freshness(STAGE2_GLOBAL_GROUNDING).state == 'fresh'
    assert core_edits.pending(tmp_path, ctx.sid) is None
    assert (directory / 'prepared.json').read_bytes() == raw_plan
    assert ctx.ledger.path.read_bytes() == ledger
    review = store.read_own(GlobalReview, STAGE2_GLOBAL_REVIEW)[0]
    assert any(row.state == 'pending' and row.subject_path.startswith('global.setting')
               for row in review.items)
    assert approvals.get(tmp_path, ctx.sid, include_spawns=False).stage2_reviews[
        'stage2.global'].state != 'accepted'
    landed = snapshot(ctx)
    core_edits.apply(_free_ctx(ctx), identity)
    assert snapshot(ctx) == landed


def test_alias_evidence_batch_does_not_rewrite_global_or_reopen_completed_batch(tmp_path):
    ctx = seed(tmp_path)
    original = snapshot(ctx)
    ledger = ctx.ledger.path.read_bytes()
    request = request_for(ctx, evidence_only=True, global_edit=False)
    identity = approve(ctx, request)
    plan_path = paths.stage2_core_edit_dir(ctx.novel_dir, identity) / 'prepared.json'
    plan = core_edits.PreparedCoreEdits.model_validate_json(plan_path.read_bytes())
    assert plan.global_candidate is None
    core_edits.apply(ctx, identity)
    assert snapshot(ctx)[STAGE2_GLOBAL_GROUNDING] == original[STAGE2_GLOBAL_GROUNDING]
    char_ctx = _free_ctx(ctx)
    char_ctx.node = characters.NODE
    independent = characters.edit_template(char_ctx)
    independent['operations'] = independent['operations'][:1]
    independent['operations'][0]['character']['role'] = '站在老宅廊下的女子'
    independent['operations'][0]['character']['role_evidence_refs'] = [
        {'chapter_ordinal': 1, 'locator': '李宝珠站在廊下看着落花。'}]
    characters.apply_edit(char_ctx, CharactersEdit.model_validate(independent),
                          actor='test:human', reason='联合批次完成后的独立人物修订')
    after = snapshot(ctx)
    assert core_edits.pending(tmp_path, ctx.sid) is None
    core_edits.apply(_free_ctx(ctx), identity)
    assert snapshot(ctx) == after
    assert ctx.ledger.path.read_bytes() == ledger


def test_prepared_original_bytes_tamper_blocks_recovery(tmp_path):
    ctx = seed(tmp_path)
    original = snapshot(ctx)
    request = request_for(ctx)
    identity = approve(ctx, request)
    target = paths.stage2_core_edit_dir(ctx.novel_dir, identity) / 'prepared.json'
    target.write_bytes(target.read_bytes() + b' ')
    with pytest.raises(ArtifactUnreadable, match='原始字节'):
        core_edits.pending(tmp_path, ctx.sid)
    with pytest.raises(ArtifactUnreadable, match='原始字节'):
        core_edits.apply(ctx, identity)
    assert snapshot(ctx) == original


def test_second_batch_cannot_replace_unfinished_original_decision(tmp_path):
    ctx = seed(tmp_path)
    request = request_for(ctx)
    identity = approve(ctx, request)
    original = approvals.stage2_review_history(tmp_path, ctx.sid)
    other = request.model_copy(update={'reason': '另一个并发 Commander 批次'})
    with pytest.raises(NovelError, match='尚未闭合'):
        approvals.approve_stage2_review(tmp_path, ctx.sid, other)
    assert approvals.stage2_review_history(tmp_path, ctx.sid) == original
    assert core_edits.pending(tmp_path, ctx.sid) == identity


def test_orphan_prepared_cannot_launder_a_target_not_in_original_request(tmp_path):
    from novel.llm import agent_runs
    ctx = seed(tmp_path)
    request = request_for(ctx)
    original = snapshot(ctx)
    core_edits.prepare(tmp_path, ctx.sid, request, code=ctx.code)
    plan = paths.stage2_core_edit_dir(ctx.novel_dir, core_edits.request_sha(request)) / 'prepared.json'
    altered = json.loads(plan.read_bytes())
    altered['global_candidate']['setting']['social_context'] = '未由原 Commander 请求的新背景'
    agent_runs._atomic_json(plan, altered)
    with pytest.raises(NovelError, match='拒绝追认'):
        approvals.approve_stage2_review(tmp_path, ctx.sid, request)
    assert snapshot(ctx) == original
    assert approvals.stage2_review_history(tmp_path, ctx.sid) == []


def test_deleted_character_keeps_original_commander_problem_pending(tmp_path):
    ctx = seed(tmp_path)
    pack = global_review.review_workpack(ctx)
    problem = next(row for row in pack['review_coverage']['scope']['obligations']
                   if 'char_002' in row['subject_path'] and row['subject_path'].endswith('.role'))
    raw = request_for(ctx, global_edit=False).model_dump(mode='json', by_alias=True)
    raw['core_edits']['characters']['operations'] = [{
        'kind': 'delete', 'char_id': 'char_002', 'reason': '原实体登记有误，须保留原疑点供后续核查'}]
    raw['review']['exceptions'] = [{
        'obligation_id': problem['obligation_id'], 'decision': 'rework',
        'reason': '该主体身份归属尚有疑点，删除不能代替核查'}]
    identity = approve(ctx, Stage2NodeReviewRequest.model_validate(raw))
    core_edits.apply(ctx, identity)
    coverage = approvals.stage2_review_coverage(tmp_path, ctx.sid, 'stage2.global')
    removed = next(row for row in coverage.scope.obligations
                   if row.obligation_id == problem['obligation_id'])
    assert removed.kind == 'removed_problem'
    assert removed.problem_subject_sha256 == problem['subject_sha256']
    assert removed.obligation_id in coverage.pending_obligation_ids
    assert any(row.obligation.obligation_id == removed.obligation_id and row.decision == 'rework'
               for row in coverage.prior_problems)


def test_alias_add_and_global_deletions_preserve_mixed_review_coverage_in_final_workpack(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from contracts.schema.stage1 import MainText
    from contracts.schema.stage2_review import Stage2ReviewCoverage
    from novel.orchestrator import inspection, reviewer_tasks
    from novel.stages.stage2 import review_obligations
    from tests.stage2_fixtures import knowledge_response
    from tests.test_stage2 import main_text
    from tests.test_stage2_analyze import _response

    ctx = _ctx(tmp_path)
    source = main_text()
    source[0]['text'] = source[0]['text'].replace(
        '李宝珠站在廊下看着落花。', '李宝珠又名小珠，站在廊下看着落花。')
    ArtifactStore(ctx).write(extract.MAIN_TEXT, MainText.model_validate(source))
    response = _response()
    response['global'] = knowledge_response()
    Stage2AnalyzeAgent(ctx, driver=_driver(ctx, response)).run()
    ctx = _free_ctx(ctx)
    ctx.node = 'stage2.global'
    original = global_review.review_workpack(ctx)
    obligations = original['review_coverage']['scope']['obligations']
    character_set = next(row['obligation_id'] for row in obligations
                         if row['subject_path'] == 'characters.characters')
    stable = next(row for row in obligations if row['obligation_id'].startswith(
        'alias_identity:characters.characters.char_001.'))
    deleted = {row['obligation_id'] for row in obligations if row['subject_path'].startswith(
        ('global.world_rules.wr_01.', 'global.arc_secrets.secret_01'))}
    assert deleted
    approvals.approve_stage2_review(tmp_path, ctx.sid, Stage2NodeReviewRequest(
        node='stage2.global', review_token=original['review_token'], decision='uncertain',
        actor='test:first-commander', reason='原文有遗漏称谓，其余合成主体已核对',
        review={'scope_sha256': original['review_coverage']['scope']['scope_sha256'],
            'default_decision': 'accept', 'exceptions': [dict(
                obligation_id=character_set, decision='uncertain', reason='需要补齐原文称谓')]}))
    first = approvals.stage2_review_history(tmp_path, ctx.sid)[-1]
    first_raw = first.model_dump(mode='json')
    assert all(unit.decision == 'accept' for unit in first.obligation_receipt.units
               if unit.obligation.obligation_id in deleted)

    char_ctx = _free_ctx(ctx)
    char_ctx.node = characters.NODE
    edit = characters.edit_template(char_ctx)
    edit['operations'] = edit['operations'][:1]
    edit['operations'][0]['character']['aliases'].append(dict(
        surface='小珠', identity_revealed_from=1,
        evidence_refs=[dict(chapter_ordinal=1, locator='李宝珠又名小珠')]))
    current = global_review.review_workpack(ctx)
    request = Stage2NodeReviewRequest.model_validate({
        'node': 'stage2.global', 'review_token': current['review_token'], 'decision': 'rework',
        'actor': 'test:second-commander', 'reason': '补称谓并纠正长期规则与秘密的过度晋升',
        'review': {'scope_sha256': current['review_coverage']['scope']['scope_sha256'],
            'default_decision': 'accept', 'exceptions': [dict(
                obligation_id=key, decision='rework', reason='较新反查确认本项需订正')
                for key in sorted(deleted | {character_set})]},
        'core_edits': {**current['core_edit_binding'], 'characters': edit,
            'global': {'world_rules_replace': [], 'arc_secrets_replace': []}},
    })
    ledger = ctx.ledger.path.read_bytes()
    core_edits.apply(ctx, approve(ctx, request))
    pack = global_review.review_workpack(ctx)
    coverage = Stage2ReviewCoverage.model_validate(pack['review_coverage'])
    inherited = next(unit for unit in coverage.inherited
                     if unit.obligation.obligation_id == stable['obligation_id'])
    assert inherited.obligation.subject_sha256 == stable['subject_sha256']
    assert set(inherited.obligation.context_refs) > set(stable['context_refs'])
    assert inherited.actor == first.actor and inherited.reason == first.reason
    history = approvals.stage2_review_history(tmp_path, ctx.sid)
    assert inherited.source_decision_sha256 == review_obligations.sha(history[-1].model_dump(mode='json'))
    assert history[0].model_dump(mode='json') == first_raw
    assert deleted <= set(coverage.pending_obligation_ids)
    assert deleted <= {unit.obligation.obligation_id for unit in coverage.prior_problems
                       if unit.decision == 'rework'}
    assert {row['obligation_id'] for row in pack['removed_problem_materials']} == deleted
    assert all(row['available'] for row in pack['removed_problem_materials'])
    assert len(coverage.scope.obligations) == len(coverage.inherited) + len(coverage.pending_obligation_ids)

    def read_command(argv, **_kwargs):
        if '--emit-review-workpack' in argv:
            return global_review.review_workpack(ctx)
        if '--emit-review-template' in argv:
            return json.loads(global_review.review_template_json(ctx))
        pytest.fail(f'本轮不应读取其他阶段：{argv}')

    monkeypatch.setattr(reviewer_tasks, '_json_command', read_command)
    frontier = SimpleNamespace(state='blocked', code_version=ctx.code,
        surface_seal=inspection.read_surface_seal(tmp_path, ctx.sid, code=ctx.code),
        action=SimpleNamespace(waiting_on=queue.WAITING_ON_STAGE2_REVIEW, action_sha256='a' * 64))
    target = paths.scratch_session_dir(tmp_path, ctx.sid, 'mixed-core-review') / 'request.json'
    task = reviewer_tasks.build_task(tmp_path, ctx.sid, frontier, target, epoch_id='mixed-core-review')
    envelope = json.loads(task.prompt.split('<immutable-workpack>\n', 1)[1].split(
        '\n</immutable-workpack>', 1)[0])
    material = envelope['materials']['global']
    assert {row['obligation_id'] for row in material['removed_problem_materials']} == deleted
    assert material['main_text_chapters'] == source
    assert not target.exists() and ctx.ledger.path.read_bytes() == ledger


def _only_removed_problem(ctx):
    """第一轮删除角色；第二轮明确接受当前父集合，仍保留原 role 疑点。"""
    pack = global_review.review_workpack(ctx)
    problem = next(row for row in pack['review_coverage']['scope']['obligations']
                   if row['subject_path'] == 'characters.characters.char_002.role')
    original_row = next(row for row in pack['items'] if row['subject_path'] == problem['subject_path'])
    raw = request_for(ctx, global_edit=False).model_dump(mode='json', by_alias=True)
    raw['core_edits']['characters']['operations'] = [dict(
        kind='delete', char_id='char_002', reason='实体登记存疑，删除后继续核对原身份')]
    raw['review']['exceptions'] = [dict(obligation_id=problem['obligation_id'],
        decision='rework', reason='原首次身份需要核对，不能以删除代替判断')]
    core_edits.apply(ctx, approve(ctx, Stage2NodeReviewRequest.model_validate(raw)))
    current = global_review.review_workpack(ctx)
    approvals.approve_stage2_review(ctx.novel_dir.parent, ctx.sid, Stage2NodeReviewRequest(
        node='stage2.global', review_token=current['review_token'], decision='uncertain',
        actor='test:commander', reason='当前集合已核对，原删除问题继续待审',
        review={'scope_sha256': current['review_coverage']['scope']['scope_sha256'],
            'default_decision': 'accept', 'exceptions': [dict(obligation_id=problem['obligation_id'],
                decision='uncertain', reason='尚须核对被删角色原身份')]}))
    return problem, original_row


def test_second_round_only_removed_problem_gets_original_claim_and_one_source_in_final_wire(tmp_path):
    from novel.orchestrator import reviewer_tasks
    from novel.views.global_review import global_review_view
    from tests.test_stage2 import main_text
    ctx = seed(tmp_path)
    problem, original_row = _only_removed_problem(ctx)
    pack = global_review.review_workpack(ctx)
    assert pack['review_coverage']['pending_obligation_ids'] == [problem['obligation_id']]
    assert global_review_view(str(tmp_path), ctx.sid).review_token == pack['review_token']
    wire = reviewer_tasks._compact_stage2_materials({'global': pack})['global']
    restored = wire['removed_problem_materials'][0]
    assert restored['available'] and restored['problem_subject_sha256'] == problem['subject_sha256']
    assert restored['row']['subject'] == original_row['subject']
    assert restored['context_rows']  # 原 role 必须连同当时实体身份可读，不只给旧 char_id。
    assert any(row['kind'] == 'character_identity' for row in restored['context_rows'])
    assert wire['subject_cards'] == [] and wire['main_text_chapters']
    assert wire['source_passages'] == {}
    for row in [restored['row'], *restored['context_rows']]:
        for evidence in row['formal_evidence']:
            assert evidence['source_text_ref'].startswith('chapter:')
            assert 'literal' not in evidence and 'context' not in evidence
    rendered = json.dumps(wire, ensure_ascii=False)
    for chapter in main_text():
        assert rendered.count(json.dumps(chapter['text'], ensure_ascii=False)) == 1


def test_missing_removed_source_changes_token_and_old_pass_replay_cannot_accept_deleted_problem(tmp_path):
    from contracts.artifacts import STAGE2_GLOBAL_REVIEW_DECISIONS
    from contracts.schema import encode_artifact
    from contracts.schema.evaluation import GlobalReviewDecisionSet
    from novel.errors import HumanEditProtected
    ctx = seed(tmp_path)
    problem, _row = _only_removed_problem(ctx)
    pack = global_review.review_workpack(ctx)
    source_sha = pack['removed_problem_materials'][0]['source']['characters_sha256']
    store = ArtifactStore(ctx)
    archive = next(path for path in store.path(paths.rounds_dir_of(STAGE2_CHARACTERS)).glob('*.json')
                   if hashlib.sha256(path.read_bytes()).hexdigest() == source_sha)
    original = archive.read_bytes()
    archive.write_bytes(original + b' ')
    missing = global_review.review_workpack(ctx)
    assert not missing['removed_problem_materials'][0]['available']
    assert missing['review_token'] != pack['review_token']
    with pytest.raises(HumanEditProtected, match='旧 token'):
        approvals.approve_stage2_review(tmp_path, ctx.sid, Stage2NodeReviewRequest(
            node='stage2.global', review_token=pack['review_token'], decision='uncertain',
            actor='test', reason='旧阅读不能越过原来源损坏'))
    # 当前事实可以明确 PASS；已删除问题不在 GlobalReviewDecisionSet 的 item 闭集中。
    raw = global_review.review_template(ctx).model_dump(mode='json')
    raw['actor'] = 'test:commander'
    raw['blocking_scope'].update(
        state='PASS', reason='本轮仅接受当前存在的事实',
        expected_workpack_sha256=missing['workpack_sha256'])
    decision = GlobalReviewDecisionSet.model_validate(raw)
    store.write(STAGE2_GLOBAL_REVIEW_DECISIONS, decision)
    global_review.apply_file(ctx, STAGE2_GLOBAL_REVIEW_DECISIONS)
    assert global_review.pending_removed_problems(ctx)
    assert global_review.submitted_decisions_is_current(str(tmp_path), ctx.sid,
        encode_artifact(STAGE2_GLOBAL_REVIEW_DECISIONS, raw), require_action_complete=True)
    with pytest.raises(HumanEditProtected, match='原主张'):
        approvals.approve_stage2_review(tmp_path, ctx.sid, Stage2NodeReviewRequest(
            node='stage2.global', review_token=global_review.review_token(ctx), decision='accept',
            actor='test', reason='不能在来源未知时接受删除'))
    archive.write_bytes(original)
    global_review.apply_file(ctx, STAGE2_GLOBAL_REVIEW_DECISIONS)
    assert global_review.pending_removed_problems(ctx)  # 恢复来源也不能赋权给先前旧 PASS。
    current = global_review.review_workpack(ctx)
    assert current['removed_problem_materials'][0]['available']
    approvals.approve_stage2_review(tmp_path, ctx.sid, Stage2NodeReviewRequest(
        node='stage2.global', review_token=current['review_token'], decision='accept',
        actor='test:commander', reason='已对照原被删角色、证据与全文，明确接受删除',
        review={'scope_sha256': current['review_coverage']['scope']['scope_sha256'],
                'default_decision': 'accept', 'exceptions': []}))
    assert not global_review.pending_removed_problems(ctx)
    approvals.require_stage2_node_accepted(tmp_path, ctx.sid, 'stage2.global')


def test_foreign_character_edit_after_partial_submit_is_not_overwritten(tmp_path, monkeypatch):
    ctx = seed(tmp_path)
    identity = approve(ctx, request_for(ctx))
    real_commit = extract.commit_core_edit
    def stopped(*args, **kwargs):
        raise KeyboardInterrupt('人物已提交后停止')
    monkeypatch.setattr(extract, 'commit_core_edit', stopped)
    with pytest.raises(KeyboardInterrupt):
        core_edits.apply(ctx, identity)
    char_ctx = _free_ctx(ctx)
    char_ctx.node = characters.NODE
    independent = characters.edit_template(char_ctx)
    independent['operations'] = independent['operations'][:1]
    independent['operations'][0]['character']['role'] = '住在老宅内的女子'
    characters.apply_edit(char_ctx, CharactersEdit.model_validate(independent),
                          actor='test:other-owner', reason='模拟另一个合法人物写口')
    foreign = snapshot(ctx)
    monkeypatch.setattr(extract, 'commit_core_edit', real_commit)
    with pytest.raises((NovelError, ValueError)):
        core_edits.apply(_free_ctx(ctx), identity)
    assert snapshot(ctx) == foreign
    assert core_edits.pending(tmp_path, ctx.sid) == identity


@pytest.mark.real_code_version
def test_core_batch_http_to_free_cli_and_restart_keeps_shared_source(tmp_path, monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    ctx = seed(tmp_path)
    original = snapshot(ctx)
    ledger = ctx.ledger.path.read_bytes()
    request = request_for(ctx)
    identity = core_edits.request_sha(request)
    endpoint = f'/api/novels/{ctx.sid}'
    with TestClient(create_app(str(tmp_path))) as client:
        client.headers[UI_HEADER] = '1'
        char_view = client.get(endpoint + '/stage2/characters')
        global_view = client.get(endpoint + '/stage2/global')
        assert char_view.status_code == global_view.status_code == 200
        char_source = char_view.json()['registry']['_provenance']['shared_analysis_source']
        assert char_source == global_view.json()['_provenance']['shared_analysis_source']
        assert 'characters' not in global_view.json()
        submitted = client.post(endpoint + '/approvals/stage2-review',
                                json=request.model_dump(mode='json', by_alias=True))
        assert submitted.status_code == 200, submitted.text
        assert snapshot(ctx) == original
        command = {'sid': ctx.sid, 'node': 'stage2.global',
                   'flags': ['--apply-core-edits', identity]}
        preview = client.post('/api/jobs/preview', json=command)
        assert preview.status_code == 200, preview.text
        assert preview.json()['paid'] is False
        started = client.post('/api/jobs', json=command)
        assert started.status_code == 202, started.text
        job_id = started.json()['job_id']
        assert jobs.wait(job_id, 60), '免费 Core CLI 尚未结束'
        result = client.get(f'/api/jobs/{job_id}').json()
        assert result['exit_code'] == 0, client.get(f'/api/jobs/{job_id}/log').json()
        assert result['start']['paid'] is False
    with TestClient(create_app(str(tmp_path))) as restarted:
        restarted.headers[UI_HEADER] = '1'
        chars = restarted.get(endpoint + '/stage2/characters').json()
        knowledge = restarted.get(endpoint + '/stage2/global').json()
        assert chars['registry']['characters']['char_001']['role'] == '站在老宅廊下的女子'
        assert knowledge['setting']['social_context'] == '城中老宅的家庭生活'
        assert knowledge['freshness'] == chars['artifact']['freshness'] == 'fresh'
        assert core_edits.pending(tmp_path, ctx.sid) is None
        review = restarted.get(endpoint + '/stage2/global-review')
        assert review.status_code == 200, review.text
        assert review.json()['generation_safe'] is False
    assert ctx.ledger.path.read_bytes() == ledger
