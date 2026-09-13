#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_schema_api.py —— HTTP 响应模型的载体层守卫（`docs/schema/_api.md` ↔ `contracts/schema/api.py`）。

【本文件盯三件事】
  ① 现役读屏模型的后置条件必须配会红的反例；Stage3 ``StyleView`` 已随整层退役。
  ② **契约层不许手抄取值面**（`Literal['a','b']`）：取值面的唯一落点是 `invariants`。
  ③ **契约层不许背上 web 栈**：离线产 TS 类型的脚本一行 HTTP 都不发。

⚠ 本文件**不需要 fastapi**。ApprovalDoc v9 还须关闭式拒绝退役的 s2 gate 与已读账面。
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

import contracts.judges.stage5  # noqa: F401  # 注册现役 Stage5 判据供 fixture 对账
from contracts.invariants import (
    AGENT_EXECUTOR_NODES, APPROVAL_GATES, RETENTION_CANDIDATE_KINDS,
)
from contracts.rules import REGISTRY
from contracts.schema.api import (
    AgentCapabilitiesResponse, AgentDriverCapability, AgentModelCapability,
    AgentRunRequest, AgentRunList, ApprovalDoc, ApprovalWorkpack, BlueprintReadingWorkpack, DesignReviewTemplate,
    CharacterRegistryView, DraftAlignView, DraftView, ErrorBody, GlobalView, NovelOverview,
    JobList, LogChunk, LogStreamEvent, NovelList, PreviewRequest, ReadingView,
    LegacyModelCapability, LegacyProviderCapability, LegacyThinkingCapability,
    RetentionApplyRequest, RetentionPlanView, RetentionPolicyRequest,
    ReviewChapterView, ReviewView,
    Stage0EditRequest, Stage0EditResult, Stage0Segment, Stage0Segments,
    Stage1Segment, Stage1View,
    Stage4View, Stage5View, Stage6View, Stage7View,
    TrashRestoreApplyRequest, TrashRestorePlanView, UploadResult,
)
from contracts.schema.plot import SourceContext
from contracts.schema.orchestration import (
    AgentProfile, AgentRunStart, ArtifactState, JobStart, RunProgressView, Step,
)
from contracts.schema.report import CheckReport
from contracts.schema.run_metrics import RunMetricsView
from contracts.schema.stage5 import ChapterSegment


ROOT = Path(__file__).parents[1]
API_PY = ROOT / 'contracts' / 'schema' / 'api.py'


def test_service_maintenance_apply_requires_complete_preview_shapes():
    retention = RetentionPlanView(
        wire_version=1, generated_at=1,
        policy=RetentionPolicyRequest(), examined=0, candidates=[],
        plan_token='1' * 64)
    assert RetentionApplyRequest(plan=retention).confirmed is False
    restore = TrashRestorePlanView(
        wire_version=1, generated_at=1,
        trash_id='1700000000-deadbeef', sid='42', title='测试书',
        original_dirname='测试书_42', metadata_sha256='2' * 64,
        manifest_file_sha256='3' * 64, manifest_sha256='4' * 64,
        plan_token='5' * 64)
    assert TrashRestoreApplyRequest(plan=restore).confirmed is False


def test_retention_candidate_kind_uses_the_invariant_value_domain():
    import typing

    from contracts.schema.api import RetentionCandidateView

    assert typing.get_args(RetentionCandidateView.model_fields['kind'].annotation) == \
        RETENTION_CANDIDATE_KINDS


def test_global_evidence_fixture_matches_its_archived_source_snapshot():
    fixtures = ROOT / 'web' / 'src' / 'fixtures' / 'backend_v1'
    # 冻结旧字节继续证明历史证据的坐标来源；不把旧知识图当成当前 Core HTTP 正例。
    archived = {name: (fixtures / name).read_bytes()
                for name in ('global.json', 'reading_ch3.json')}
    assert {name: hashlib.sha256(data).hexdigest() for name, data in archived.items()} == {
        'global.json': 'e296473948f158f13268a826ba1337dfea08efa71544220f71c5f2a8e71a6b20',
        'reading_ch3.json': '1ad21dc8d156783080c38f410b0e8beab24d90e4bb37a47818b754c99fdb5588',
    }
    raw = json.loads(archived['global.json'])
    with pytest.raises(ValidationError):
        GlobalView.model_validate(raw)
    assert {row['visibility']['kind'] for row in raw['relations']} == {'public', 'via_secret'}
    assert {event['subject_kind'] for secret in raw['secrets']
            for event in secret['knowledge_events']} == {
        'character', 'group'}

    reading = json.loads(archived['reading_ch3.json'])
    # 保留 Global 的七类证据反例；旧阅读快照只作源文，不能冒充 V9 HTTP 响应。
    with pytest.raises(ValidationError):
        ReadingView.model_validate(reading)
    check = json.loads((fixtures / 'check_stage2.json').read_text(encoding='utf-8'))
    CheckReport.model_validate(check)
    chapter = ''.join(segment['text'] for segment in reading['segments'])
    pending: list[object] = [raw]
    located = 0
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            source = item.get('source')
            if isinstance(source, dict):
                assert chapter[source['start']:source['end']] == source['text']
                located += 1
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    assert located >= 7, '七类 evidence 没有都在 fixture 里留下可回取正例'


def test_plot_flow_fixtures_are_closed_current_http_and_review_views():
    from novel.api.enums import payload as enum_payload
    from contracts.schema.api import (InboxView,
        Stage6PrepareView, Stage6PrepareWorkpack, Stage6ApprovalWorkpack,
        Stage7ApprovalWorkpack)

    captured = json.loads((ROOT / 'web/src/fixtures/plot_v1/flow.json').read_text(
        encoding='utf-8'))
    models = {
        'design_workpack': ApprovalWorkpack,
        'design_template': DesignReviewTemplate,
        'structure_workpack': ApprovalWorkpack,
        **{f'http:reading/{ordinal}': ReadingView for ordinal in range(1, 4)},
        **{f'http:stage4/{ordinal}': Stage4View for ordinal in range(1, 4)},
        'http:stage5/1': Stage5View,
        'http:overview': NovelOverview,
        'http:approvals': ApprovalDoc,
        'http:stage1': Stage1View,
        'http:stage2/global': GlobalView,
        'http:stage2/characters': CharacterRegistryView,
        'http:run': RunProgressView,
        'http:run/metrics': RunMetricsView,
        # 审阅退役后只余只读阅读工作包：查看与证据回源共用，不带接受语义。
        'http:stage2/blueprint-reading-workpack?scope=chapter%3A1': BlueprintReadingWorkpack,
        'http:stage2/blueprint-reading-workpack?scope=cross_chapter': BlueprintReadingWorkpack,
        'api:jobs': JobList,
        'api:agent/runs': AgentRunList,
        'http:check/stage2': CheckReport,
        'http:check/stage5': CheckReport,
    }
    # 同一次贯通分别保留 Stage5 前检查点与最终交付，不把最终接受盖到旧材料上。
    models.update({f'final:{name}': model for name, model in list(models.items())
                   if name.startswith('http:')})
    models.update({
        'final:http:inbox': InboxView,
        'final:http:stage6/prepare': Stage6PrepareView,
        'final:http:stage6/prepare/workpack': Stage6PrepareWorkpack,
        'final:http:stage6': Stage6View,
        'final:http:stage7': Stage7View,
        'final:http:approvals/stage6/workpack': Stage6ApprovalWorkpack,
        'final:http:approvals/stage7/workpack': Stage7ApprovalWorkpack,
        'final:http:approvals/design/workpack': ApprovalWorkpack,
        'final:http:approvals/structure/workpack': ApprovalWorkpack,
        'final:http:check/stage4': CheckReport,
        'final:http:check/stage6': CheckReport,
        'final:http:check/stage7': CheckReport,
        **{f'final:http:stage5/{ordinal}': Stage5View for ordinal in range(1, 4)},
        **{f'final:http:review/{ordinal}': ReviewView for ordinal in range(1, 4)},
        **{f'final:http:stage7/{ordinal}': Stage7View for ordinal in range(1, 4)},
    })
    # enums 唯一取值面由纯发布器产生；下载仍为纯文本，不伪造 JSON 响应。
    assert captured.keys() == models.keys() | {'api:enums', 'metadata', 'final:http:stage7/book.txt'}
    assert captured['api:enums'] == enum_payload()
    for name, model in models.items():
        model.model_validate(captured[name])
    assert captured['metadata']['synthetic'] is True
    assert captured['metadata']['sid'] == captured['final:http:overview']['sid']
    assert '顾清棠' in captured['final:http:stage7/book.txt']
    assert '李宝珠' not in captured['final:http:stage7/book.txt']
    assert captured['final:http:approvals']['prepare'] == captured['final:http:stage6/prepare']['status']
    assert captured['final:http:stage6/prepare']['status']['state'] == 'accepted'
    assert captured['final:http:approvals/stage6/workpack']['disposition'] == 'already_current'
    for stage in ('stage2', 'stage5'):
        check = CheckReport.model_validate(captured['http:check/' + stage])
        assert [line.rule for line in check.rules] == sorted(
            rule.rule for rule in REGISTRY.of_stage(stage))


def test_plot_flow_keeps_its_synthetic_source_semantics_through_context():
    """已知合成书的语义回归；证据坐标与 schema 合法不证明剧情归属正确。"""
    captured = json.loads((ROOT / 'web/src/fixtures/plot_v1/flow.json').read_text(
        encoding='utf-8'))
    sources = {ordinal: captured[f'http:reading/{ordinal}']['text']
               for ordinal in range(1, 4)}
    assert '信上说父亲已经回城。' in sources[1]
    assert '其实母亲早就知道那封信的事。' in sources[2]
    world = captured['http:stage2/global']
    characters = captured['http:stage2/characters']
    assert {char_id: (row['canonical'], row['role'])
            for char_id, row in characters['registry']['characters'].items()} == {
        'char_001': ('李宝珠', '李宝珠，在家中翻阅账册'),
        'char_002': ('母亲', '李宝珠的母亲'),
    }
    assert 'characters' not in world
    assert world['setting']['era'] == '原文未明确年代'
    assert world['world_rules'] == []
    assert len(world['arc_secrets']) == 1
    secret = world['arc_secrets'][0]
    assert secret['fact'] == '母亲早就知道那封信的事。'
    assert secret['revealed_from'] == 2
    assert 'knowledge_events' not in secret
    assert [(row['effective_from'], row['revealed_from'])
            for row in world['relations']] == [(2, 2)]
    assert [(row['subject']['raw'], row['predicate'], row['object']['raw'])
            for row in world['relations']] == [('char_001', '母女', 'char_002')]

    # 两个真实 HTTP 响应分别拥有证据表；只用同次 Reading 响应还原原文，不能跨 owner 借表。
    for anchors, snippets in (
        (world['evidence_anchors'], world['evidence_snippets']),
        (characters['registry']['evidence_anchors'], characters['evidence_snippets']),
    ):
        assert anchors and anchors.keys() == snippets.keys()
        for evidence_id, evidence in anchors.items():
            source = sources[evidence['chapter_ordinal']]
            text = source[evidence['start']:evidence['end']]
            assert text == snippets[evidence_id]
            assert hashlib.sha256(text.encode('utf-8')).hexdigest() == evidence['text_sha256']

    # “母亲知情”由完整 Beat 保留；Global 不再拥有逐人物知情记录。
    chapter_two = captured['http:reading/2']['blueprint']
    knowledge_beat = next(row for row in chapter_two['beats'] if row['beat_id'] == 'b02_02')
    assert knowledge_beat['content'] == '母亲其实早已知道那封信的事。'
    assert knowledge_beat['participants'] == [{'char_id': 'char_002', 'presentation_ref': None}]
    context = captured['http:stage4/2']['ctx_chapter']
    mother = next(row for row in context['characters'] if row['display_name'] == '母亲')
    written_beat = next(row for row in context['beats'] if row['beat_ref'] == 'b02_02')
    assert written_beat['content'] == knowledge_beat['content']
    assert written_beat['participants'] == [mother['actor_ref']]
    subject = next(row for row in context['global_subjects']
                   if row['subject_ref']['kind'] == 'secret')
    assert subject['value']['fact'] == secret['fact']
    # KnowledgeLinks 揭示事件随语义审阅退役：同章揭示安排由现有计划表达，
    # WriterContext 不再有逐场知识许可表，也就没有 secret_reveal 事件可断言。
    assert not any(row.get('kind') == 'secret_reveal' for row in context.get('knowledge_events', []))
    assert not any(row['subject_ref']['kind'] == 'secret'
                   for row in captured['http:stage4/1']['ctx_chapter']['global_subjects'])
    for ordinal in sources:
        chapter = captured[f'http:reading/{ordinal}']['blueprint']
        planned = captured[f'http:stage4/{ordinal}']
        assert planned['plan_chapter']['chapter_rewrite_brief'] == chapter['chapter_function']
        assert planned['ctx_chapter']['chapter_rewrite_brief'] == chapter['chapter_function']


def test_active_stage5_fixtures_are_exact_slim_http_views():
    fixtures = ROOT / 'web' / 'src' / 'fixtures' / 'backend_v1'
    stage5 = Stage5View.model_validate_json(
        (fixtures / 'stage5_ch3.json').read_text(encoding='utf-8'))
    review = ReviewView.model_validate_json(
        (fixtures / 'review_ch3.json').read_text(encoding='utf-8'))

    assert stage5.legacy_incompatible is True and not stage5.drafts
    assert review.review_state == 'legacy_incompatible'
    assert set(review.chapter.model_dump()) == {'output_ordinal', 'findings'}
    assert set(review.index[0].model_dump()) == {
        'output_ordinal', 'finding_counts', 'unresolved',
    }

    check = CheckReport.model_validate_json(
        (fixtures / 'check_stage5.json').read_text(encoding='utf-8'))
    # 历史报告不能补签后来增加的判据；当前完整取值面由 plot_flow 实际 HTTP 报告验证。
    assert 'knowledge_position_unver' not in {line.rule for line in check.rules}


def test_preview_sid_is_nullable_but_never_empty():
    assert PreviewRequest(sid=None, node='stage0.remove').sid is None
    with pytest.raises(ValidationError):
        PreviewRequest(sid='', node='stage0.remove')


def test_old_job_and_agent_records_keep_missing_profiles_as_unknown():
    """兼容字段缺席时保持 ``None``，绝不拿今天的 registry 默认倒推历史。"""
    step = Step(
        argv=['python'], cwd='E:/repo', label='旧步骤', paid=False, writes='')
    job = JobStart(
        sid='old', node='stage0.remove', steps=[step], python='3.10', code='old',
        paid=False, writes=False,
    )
    agent = AgentRunStart(
        sid='old', argv=['claude'], prompt='旧提示词',
        authorized=False, egress_authorized=True,
    )

    assert PreviewRequest(sid='old', node='stage0.remove').executor_profile is None
    assert PreviewRequest(sid='old', node='stage0.remove').egress_authorized is False
    assert AgentRunRequest(sid='old').profiles is None
    assert step.executor_profile is None and job.executor_profile is None
    assert step.egress_authorized is None and job.egress_authorized is None
    assert agent.profiles is None


def test_artifact_state_invalid_source_fields_are_paired_and_never_leak_to_formal_only():
    base = dict(
        rel='stage2/chapter_blueprints.json', exists=True, bytes=1, mtime=1,
        freshness='fresh', display_freshness='fresh', reason='match',
        invalid=True, readable=True, condition='invalid',
    )
    state = ArtifactState(
        **base,
        invalid_freshness='unknown', invalid_reason='consumed_unresolved')
    assert {'invalid_freshness', 'invalid_reason'} <= set(
        ArtifactState.model_json_schema()['required'])
    assert (state.freshness, state.reason) == ('fresh', 'match')
    assert (state.invalid_freshness, state.invalid_reason) == (
        'unknown', 'consumed_unresolved')

    with pytest.raises(ValidationError, match='必须成对出现'):
        ArtifactState(
            **base, invalid_freshness='unknown', invalid_reason=None)
    with pytest.raises(ValidationError, match='Field required'):
        ArtifactState(**base)
    with pytest.raises(ValidationError, match='invalid=False'):
        ArtifactState(
            **{**base, 'invalid': False, 'condition': 'ready'},
            invalid_freshness='unknown', invalid_reason='consumed_unresolved')


def test_executor_egress_snapshots_distinguish_old_unknown_from_new_bool():
    profile = AgentProfile(
        driver='codex', model='gpt-5.6-sol', reasoning_effort='high',
        execution_mode='standard')
    step_fields = dict(
        argv=['python'], cwd='E:/repo', label='executor', paid=True, writes='artifact')

    # Phase2/3 的 append-only 记录没有新格：字段缺席只能读成 unknown，不能倒推。
    old_step = Step(**step_fields, executor_profile=profile)
    old_job = JobStart(
        sid='old', node='stage2.global', steps=[old_step], python='3.11', code='old',
        paid=True, writes=True, executor_profile=profile)
    assert old_step.executor_backend is not None
    assert old_step.executor_backend.kind == 'agent'
    assert 'executor_profile' not in old_step.model_dump(mode='json')
    assert old_step.model_dump(mode='json')['executor_backend']['profile'] == \
        profile.model_dump(mode='json')
    assert old_step.egress_authorized is None
    assert old_job.egress_authorized is None

    # 公共 HTTP 会把旧缺席格投影成显式 null；再次解码仍必须保留 unknown。
    old_round_trip = JobStart.model_validate_json(old_job.model_dump_json())
    assert old_round_trip.egress_authorized is None
    assert old_round_trip.steps[0].egress_authorized is None
    with pytest.raises(ValidationError, match='不能携带正文外发授权'):
        Step(**step_fields, executor_profile=None, egress_authorized=False)
    with pytest.raises(ValidationError, match='与 argv'):
        Step(**step_fields, executor_profile=profile, egress_authorized=True)

    current_step = Step(
        **step_fields, executor_profile=profile, egress_authorized=False)
    current_job = JobStart(
        sid='new', node='stage2.global', steps=[current_step], python='3.11', code='new',
        paid=True, writes=True, executor_profile=profile, egress_authorized=False)
    assert current_job.egress_authorized is False
    with pytest.raises(ValidationError, match=r'steps\[\] 不一致'):
        JobStart(
            sid='new', node='stage2.global', steps=[current_step],
            python='3.11', code='new', paid=True, writes=True,
            executor_profile=profile, egress_authorized=True)


def test_agent_capability_response_keeps_ids_labels_and_options_separate():
    model = AgentModelCapability(
        id='gpt-5.6-sol', label='GPT-5.6 Sol',
        reasoning_efforts=['none', 'high'], execution_modes=['standard'],
    )
    driver = AgentDriverCapability(
        id='codex', provider='openai', label='Codex',
        roles=['commander', 'executor'],
        availability='unknown', availability_reason='只做了本机版本探针',
        models=[model],
    )
    legacy = LegacyProviderCapability(
        id='deepseek', label='DeepSeek API', models=[LegacyModelCapability(
            id='deepseek-v4-pro', label='DeepSeek V4 Pro',
            thinking_modes=[LegacyThinkingCapability(
                id='on', label='开启思考', reasoning_efforts=['high'])],
            executor_nodes=list(AGENT_EXECUTOR_NODES),
        )],
    )
    response = AgentCapabilitiesResponse(
        drivers=[driver], legacy_providers=[legacy],
        executor_nodes=list(AGENT_EXECUTOR_NODES))

    assert response.drivers[0].models[0].id == 'gpt-5.6-sol'
    assert response.drivers[0].models[0].label == 'GPT-5.6 Sol'
    assert response.drivers[0].provider == 'openai'
    with pytest.raises(ValidationError, match='不得重复'):
        AgentCapabilitiesResponse(
            drivers=[driver, driver], legacy_providers=[legacy],
            executor_nodes=list(AGENT_EXECUTOR_NODES))


def test_stage0_continuous_view_rejects_a_gap_and_duplicate_ids():
    first = Stage0Segment(
        id='same', kind='keep', start=0, end=2, chars=2, flagged=False, text='甲乙')
    gap = Stage0Segment(
        id='same', kind='keep', start=3, end=4, chars=1, flagged=False, text='丙')
    with pytest.raises(ValidationError, match='连续|重复'):
        Stage0Segments(
            available=True,
            reason=None,
            segments=[first, gap],
        )
    with pytest.raises(ValidationError):
        Stage0Segments(
            available=False,
            reason='',
            segments=[],
        )


def test_stage1_continuous_view_and_chapter_index_reject_gaps():
    first = Stage1Segment(
        id='hook', kind='hook', start=0, end=1, chars=1, text='甲')
    gap = Stage1Segment(
        id='ch2', kind='chapter', start=2, end=3, chars=1, text='乙',
        chapter_ordinal=2, source_chapter_no=2, label_delta=1,
    )
    with pytest.raises(ValidationError, match='连续'):
        Stage1View(segments=[first, gap])
    contiguous = gap.model_copy(update={'start': 1, 'end': 2})
    with pytest.raises(ValidationError, match='连续有序'):
        Stage1View(segments=[first, contiguous])


def test_reading_source_presence_distinguishes_unknown_from_explicit_empty():
    base = dict(evidence_refs=['e01_01'], time_layer='present', place=None)
    assert SourceContext(**base, present=[]).present == []
    assert SourceContext(**base, present=None).present is None
    with pytest.raises(ValidationError):
        SourceContext(**base)
    actor = {'char_id': 'char_001', 'presentation_ref': 'ap01_01'}
    with pytest.raises(ValidationError):
        SourceContext(**base, present=[actor, actor])
    distinct = SourceContext(**base, present=[actor,
        {'char_id': 'char_001', 'presentation_ref': 'ap01_02'}])
    assert [row.presentation_ref for row in distinct.present] == ['ap01_01', 'ap01_02']
    # 旧字段保留拒绝反例；不能用它证明当前完整 ActorRef 的重复检查。
    with pytest.raises(ValidationError):
        SourceContext(**base, present_char_ids=['char_001', 'char_001'])


def test_anchor_coverage_warning_is_a_server_decision():
    low = DraftAlignView(
        beats=5, anchors=2, rows=2, inversions=0, coverage=0.4,
        coverage_threshold=0.6, coverage_state='low', note='对应关系，不是判定')
    assert low.coverage_state == 'low'
    with pytest.raises(ValidationError, match='coverage_state'):
        DraftAlignView(
            beats=5, anchors=2, rows=2, inversions=0, coverage=0.4,
            coverage_threshold=0.6, coverage_state='adequate', note='错误地说安全')


def test_stage5_views_keep_draft_segments_as_the_only_text_carrier():
    segments = [
        ChapterSegment(slot='s01', text='前段。'),
        ChapterSegment(slot='s02', text='后段。'),
    ]
    provenance = {
        'code': 'test', 'root_clean_sha1': '0' * 40, 'inputs': {},
        'origin': 'human', 'round': 1, 'revision_of': None,
        'review': None, 'fixes': None,
    }
    draft = dict(
        chars=8, budget_chars=8,
        overlap_excl_verbatim=0.0, segments=segments,
        align=None, align_rows=[], freshness='fresh', reason='match',
        _provenance=provenance,
    )
    view = DraftView.model_validate(draft)
    assert view.segments == segments
    assert set(view.model_dump(by_alias=True)) == {
        'chars', 'budget_chars', 'overlap_excl_verbatim', 'segments',
        'align', 'align_rows', 'freshness', 'reason', '_provenance',
    }
    with pytest.raises(ValidationError, match='Extra inputs'):
        DraftView.model_validate({**draft, 'text': '重复的整章正文。'})

    review = ReviewChapterView(output_ordinal=1, findings=[])
    assert set(review.model_dump()) == {'output_ordinal', 'findings'}
    with pytest.raises(ValidationError, match='Extra inputs'):
        ReviewChapterView.model_validate({
            'output_ordinal': 1, 'findings': [], 'segments': segments})


def test_the_api_models_never_spell_out_a_value_domain():
    """⭐ `contracts/schema/api.py` 里不许出现 `Literal['a', 'b']` 这种**字面量**取值面。

    取值面的唯一落点是 `contracts/invariants.py`。一份响应模型抄一份取值面，
    就是那份取值面的第 N 个落点——而本仓每一处 `Literal[XXX]`（大写的名字，不是字符串）
    都是在说同一件事：**取值面从表里来**。
    """
    tree = ast.parse(API_PY.read_text(encoding='utf-8'), filename=str(API_PY))
    bad = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Name) and node.value.id == 'Literal'):
            continue
        inner = node.slice
        items = inner.elts if isinstance(inner, ast.Tuple) else [inner]
        if any(isinstance(x, ast.Constant) and isinstance(x.value, str) for x in items):
            bad.append(node.lineno)
    assert not bad, (
        f'`contracts/schema/api.py` 第 {bad} 行把取值面**抄成了字面量**。'
        f'⇒ 写成 `Literal[SOME_TUPLE]`，取值面只许有一处（`contracts/invariants.py`）')
    # 守卫的守卫：一个 `Literal[...]` 都没扫到时，上面那条会静静地绿。
    lits = [n for n in ast.walk(tree) if isinstance(n, ast.Subscript)
            and isinstance(n.value, ast.Name) and n.value.id == 'Literal']
    assert len(lits) >= 2, f'只扫到 {len(lits)} 个 Literal——AST 多半解析错了'


def test_the_api_models_never_carry_a_verdict():
    """⭐ `R-11.4`：视图不带判定。**`badge` / `passed` / `counts` 只许住在 `CheckReport` 里。**

    视图给的是「两份数字并排放着让人自己看」（`R-11` 节首语的原话），
    判定给的是「合不合格」。混在一格里，界面上就分不出
    「这是服务端判的」与「这是这一屏自己凑出来的」。
    """
    verdicts = {'badge', 'passed', 'counts', 'hard_count', 'unver_count'}
    for model in (
        NovelList, JobList, LogChunk, LogStreamEvent, PreviewRequest, ErrorBody,
        AgentCapabilitiesResponse,
        Stage0Segment, Stage0Segments, Stage1Segment, Stage1View, UploadResult,
        GlobalView, Stage6View, Stage7View,
        ReadingView, Stage4View, Stage5View, ReviewView,
    ):
        leaked = verdicts & set(model.model_fields)
        assert not leaked, f'{model.__name__} 带着判定字段 {leaked}——那是第二个判官'


def test_the_contract_layer_never_imports_the_web_stack():
    """离线产前端 TS 类型的脚本要 import 契约层，而它一行 HTTP 都不发。

    ⚠ 这条与 `tests/test_layering.py` 那份是**一对**：那边扫整个 `contracts/`，
    这边只盯新增的 `api.py`（它是最容易顺手写进 `from fastapi import ...` 的那一个）。
    """
    tree = ast.parse(API_PY.read_text(encoding='utf-8'), filename=str(API_PY))
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name.split('.')[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mods.add(node.module.split('.')[0])
    assert not (mods & {'fastapi', 'starlette', 'uvicorn', 'novel'}), \
        f'contracts/schema/api.py 长出了不该有的依赖：{sorted(mods)}'
    assert 'pydantic' in mods, 'pydantic 都没 import 到——AST 多半解析错了'


def test_approval_doc_v9_has_the_current_six_gates_and_no_seen_ledger():
    """Stage2 语义审阅退役后：gate 不再有 s2，整本已读/接受账面（``seen``）退场。"""
    payload = {
        'v': 9,
        'sid': '123',
        'gate': {gate: False for gate in APPROVAL_GATES},
        'writing': False,
        'prepare': {'state': 'missing', 'reason': '尚未生成文案准备',
            'subject_sha256': None, 'review_token': None, 'prepare': None,
            'review': None, 'pending_commit': False},
        'structure': {
            'state': 'unavailable', 'current_subject_sha256': None,
            'current_decision': None, 'history': [],
        },
        'spawns': {},
    }
    doc = ApprovalDoc.model_validate(payload)
    assert set(doc.gate) == set(APPROVAL_GATES) == \
        {'s0', 's1', 's4', 's5', 's6', 's7'}
    assert 'seen' not in ApprovalDoc.model_fields
    with pytest.raises(ValidationError):
        ApprovalDoc.model_validate({**payload, 'v': 8})
    with pytest.raises(ValidationError):
        ApprovalDoc.model_validate({
            **payload,
            'gate': {**payload['gate'], 's2': False},
        })
    # 旧 v8 的已读凭证不再有落点：extra 关闭式拒绝，不能被静默丢掉。
    with pytest.raises(ValidationError):
        ApprovalDoc.model_validate({**payload, 'seen': {'read': False}})


def test_log_chunk_counts_bytes_not_characters():
    """`next_byte` 是**字节**偏移。

    ⚠ 中文一字跨 3 字节：拿 `len(text)` 去推下一次的 offset，
    偏移会越滑越远，而表现是日志「跳着显示」——没有任何东西会报错。
    """
    chunk = LogChunk(text='中文三字', next_byte=12)
    assert chunk.next_byte != len(chunk.text)
    with pytest.raises(ValidationError):
        LogChunk(text='x', next_byte=-1)


def test_log_stream_event_has_an_explicit_terminal_shape():
    """SSE 数据块与终态不能长得一样，否则前端只能再猜一次连接是否结束。"""
    live = LogStreamEvent(text='中文', next_byte=6, done=False, state=None)
    end = LogStreamEvent(text='', next_byte=6, done=True, state='ok')
    assert not live.done and end.state == 'ok'
    with pytest.raises(ValidationError):
        LogStreamEvent(text='', next_byte=6, done=True, state=None)
    with pytest.raises(ValidationError):
        LogStreamEvent(text='', next_byte=6, done=True, state='running')


def test_error_body_carries_an_id_not_a_phrase():
    """`kind` 是异常类名。前端的四态靠它分「后端没起来 / 这本书没有 / 这一步还没跑」。"""
    e = ErrorBody(kind='UpstreamMissing', message='这本书不在 Output 下', missing='novel')
    assert e.kind == 'UpstreamMissing' and e.hint is None
    with pytest.raises(ValidationError):
        ErrorBody(kind='', message='空 kind 等于没说')


def test_every_response_model_forbids_extra_fields():
    """`extra='forbid'` 是不可协商的那一条——也适用于响应模型。

    ⚠ 它在这一层守的是**反方向**：产物模型防「上游多写了一个字段被静默丢掉」，
    响应模型防「我们多发了一个字段而契约文档里没有它」——
    前端会开始依赖那个字段，而 `_api.md` 不知道它存在。
    """
    for model in (
        NovelList, JobList, LogChunk, LogStreamEvent, PreviewRequest, ErrorBody,
        AgentCapabilitiesResponse,
        Stage0Segment, Stage0Segments, Stage0EditRequest, Stage0EditResult,
        Stage1Segment, Stage1View,
        UploadResult, GlobalView, Stage6View, Stage7View,
        ReadingView, Stage4View, Stage5View, ReviewView,
    ):
        assert model.model_config['extra'] == 'forbid', model.__name__


def test_a_name_ref_that_failed_to_resolve_keeps_the_raw_id():
    """view 不编名字：解析不动时 `display == raw`，且 `resolved=False` 看得见。"""
    from contracts.schema.api import NameRef
    ref = NameRef(raw='char_099', display='char_099', resolved=False)
    assert ref.display == ref.raw
    with pytest.raises(ValidationError):
        NameRef(raw='', display='x', resolved=True)


def test_stage4_review_subject_distinguishes_ready_unavailable_and_reference():
    """参考基线不许绑；正式计划依赖不足时说明原因，不能伪造审阅身份。

    用 ``model_construct`` 只运行待测后置条件，不为这条守卫复制整份读屏夹具。
    """
    common = {
        'ordinal': 1,
        'chapters': [1],
        'plan_provenance': None,
        'writer_target': None,
        'ctx_chapter': None,
    }
    for identity, subject, reason in (
        (True, None, '仅参考基线'), (False, 'a' * 64, None),
        (False, None, '已保存计划但所需知识关联过期'),
    ):
        view = Stage4View.model_construct(
            **common,
            identity_plan=identity,
            structure_subject_sha256=subject,
            structure_subject_unavailable_reason=reason,
        )
        Stage4View._postconditions(view)

    for identity, subject, reason in (
        (True, 'a' * 64, None), (False, None, None),
        (False, 'a' * 64, '不能与可用身份并存'),
    ):
        view = Stage4View.model_construct(
            **common,
            identity_plan=identity,
            structure_subject_sha256=subject,
            structure_subject_unavailable_reason=reason,
        )
        with pytest.raises(ValueError, match='审阅身份|审阅主体'):
            Stage4View._postconditions(view)


def test_stage7_chapter_block_is_all_or_nothing():
    """`ordinal` 与 `rows` 同进同出——「问了 :o」与「书级」不许混出一个半吊子。

    ⚠ 用 `model_construct` 骗过必填字段、只让待测的那条后置条件跑——
    这里测的是校验器本身，不是造一份全须全尾的产物。
    """
    base = Stage7View.model_construct()
    ok = dict(base.__dict__, chapters=[1, 2], ordinal=None, rows=None)
    Stage7View._chapter_block_is_all_or_nothing(Stage7View.model_construct(**ok))
    bad = dict(base.__dict__, chapters=[1, 2], ordinal=1, rows=None)
    with pytest.raises(ValueError):
        Stage7View._chapter_block_is_all_or_nothing(Stage7View.model_construct(**bad))
    out_of_range = dict(base.__dict__, chapters=[1, 2], ordinal=9, rows=[])
    with pytest.raises(ValueError):
        Stage7View._chapter_block_is_all_or_nothing(
            Stage7View.model_construct(**out_of_range))
