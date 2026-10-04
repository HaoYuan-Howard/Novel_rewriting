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

# ⚠⚠ F8b：不再在收集期 import `contracts.judges.stage5`——旧链判据表（随旧链
#   F8c 删除）一旦注册进进程内 REGISTRY，同进程后续的 coverage／rules_registry
#   反向覆盖账就会看到新旧两组判据而误红。本文件的 fixture 对账只做
#   model_validate，不查 REGISTRY；新链判据注册见 `contracts/judges/str06.py`。
from contracts.invariants import (
    LLM_EXECUTOR_NODES, APPROVAL_GATES, RETENTION_CANDIDATE_KINDS,
)
from contracts.rules import REGISTRY
from contracts.schema.api import (
    AgentCapabilitiesResponse,
    AgentRunRequest, AgentRunList, ApprovalDoc, ErrorBody, NovelOverview,
    JobList, LogChunk, LogStreamEvent, NovelList, PreviewRequest,
    LegacyModelCapability, LegacyProviderCapability, LegacyThinkingCapability,
    RetentionApplyRequest, RetentionPlanView, RetentionPolicyRequest,
    Stage0Segment, Stage0Segments,
    Stage1Segment, Stage1View,
    TrashRestoreApplyRequest, TrashRestorePlanView, UploadResult,
)
from contracts.schema.plot import SourceContext
from contracts.schema.orchestration import (
    LegacyExecutorBackend, LegacyLlmProfile,
    AgentRunStart, ArtifactState, JobStart, RunProgressView, Step,
)
from contracts.schema.report import CheckReport
from contracts.schema.run_metrics import RunMetricsView


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


# ───────────────── 已退役：旧链 HTTP fixture 对账（F8c） ─────────────────
#
# 原来这里有两项对账旧链读屏形状的用例：
#   · `test_global_evidence_fixture_matches_its_archived_source_snapshot`
#   · `test_active_stage5_fixtures_are_exact_slim_http_views`
#
# 【为什么退役】它们的证据是 `web/src/fixtures/backend_v1/` 下六份**旧链**冻结
# fixture（global／reading_ch3／check_stage2／stage5_ch3／review_ch3／
# check_stage5）。F8b 随旧链读屏（`novel/views/stage{2,4,5}.py`、
# `blueprint_progress.py`）与旧链 routes 整批删除了这些 fixture，旧链 HTTP 面已
# 不存在——`GlobalView`／`ReadingView`／`Stage5View`／`ReviewView`／`CheckReport`
# 的旧形状没有任何现役消费者。这正落在 F7e 红账对
# `test_schema_api 3 项既有失败` 登记的处置「修复或随旧链形状退役；不删分母」
# 的**退役**分支：不是判据放宽，是被钉的旧链形状连同其冻结证据一起消失。
#
# 【反例没有静默丢失】① 旧字节与旧形状断言按 Git 历史读取（删除前的工作树）；
#   ② 现役候选链监控面的同名纪律由 `tests/test_str06_views.py::
#      test_candidate_api_models_keep_the_active_disciplines` 承接（不抄取值面、
#      不带判定字段、不背 web 栈）；③ 现役契约层的两条纪律由本文件余下用例
#      （`Literal` 取值面、web 栈依赖）继续守。
# ─────────────────────────────────────────────────────────────────────────────

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

    assert PreviewRequest(sid='old', node='stage0.remove').executor_backend is None
    assert PreviewRequest(sid='old', node='stage0.remove').egress_authorized is False
    assert AgentRunRequest(sid='old').profiles is None
    assert step.executor_backend is None and job.executor_backend is None
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
    profile = LegacyLlmProfile(
        provider='deepseek', model='deepseek-v4-pro', reasoning_effort='high',
        thinking_mode='on')
    step_fields = dict(
        argv=['python'], cwd='E:/repo', label='executor', paid=True, writes='artifact')

    # Phase2/3 的 append-only 记录没有新格：字段缺席只能读成 unknown，不能倒推。
    old_step = Step(**step_fields, executor_backend=LegacyExecutorBackend(profile=profile))
    old_job = JobStart(
        sid='old', node='str06.stage4.preserve', steps=[old_step], python='3.11', code='old',
        paid=True, writes=True, executor_backend=LegacyExecutorBackend(profile=profile))
    assert old_step.executor_backend is not None
    assert old_step.executor_backend.kind == 'legacy'
    assert 'executor_profile' not in old_step.model_dump(mode='json')
    assert old_step.model_dump(mode='json')['executor_backend']['profile'] == \
        profile.model_dump(mode='json')
    assert old_step.egress_authorized is None
    assert old_job.egress_authorized is None

    # 公共 HTTP 会把旧缺席格投影成显式 null；再次解码仍必须保留 unknown。
    old_round_trip = JobStart.model_validate_json(old_job.model_dump_json())
    assert old_round_trip.egress_authorized is None
    assert old_round_trip.steps[0].egress_authorized is None
    # F8c：外发授权与 executor backend 解钩——直连 LLM step（无 backend）可携带
    # 显式授权，但 argv 与字段必须同进同出（不一致=第二个真相源）。
    with pytest.raises(ValidationError, match='与 argv'):
        Step(**step_fields, executor_backend=LegacyExecutorBackend(profile=profile), egress_authorized=True)
    direct_step = Step(
        **dict(step_fields, argv=[*step_fields['argv'], '--allow-egress']),
        executor_backend=None, egress_authorized=True)
    assert direct_step.egress_authorized is True
    # 显式 False（未授权）与 None（旧盘未知）都不夹带旗标，同样合法。
    assert Step(**step_fields, executor_backend=None,
                egress_authorized=False).egress_authorized is False
    assert Step(**step_fields, executor_backend=None,
                egress_authorized=None).egress_authorized is None
    # 夹带旗标却声明未授权，仍是同一类不一致。
    with pytest.raises(ValidationError, match='与 argv'):
        Step(**dict(step_fields, argv=[*step_fields['argv'], '--allow-egress']),
             executor_backend=None, egress_authorized=False)
    with pytest.raises(ValidationError, match='0 成本 step 不能携带正文外发授权'):
        Step(**dict(step_fields, argv=[*step_fields['argv'], '--allow-egress'],
                    paid=False),
             executor_backend=None, egress_authorized=True)

    current_step = Step(
        **step_fields, executor_backend=LegacyExecutorBackend(profile=profile), egress_authorized=False)
    current_job = JobStart(
        sid='new', node='str06.stage4.preserve', steps=[current_step], python='3.11', code='new',
        paid=True, writes=True, executor_backend=LegacyExecutorBackend(profile=profile), egress_authorized=False)
    assert current_job.egress_authorized is False
    with pytest.raises(ValidationError, match='外发授权不一致'):
        JobStart(
            sid='new', node='str06.stage4.preserve', steps=[current_step],
            python='3.11', code='new', paid=True, writes=True,
            executor_backend=LegacyExecutorBackend(profile=profile), egress_authorized=True)


def test_agent_capability_response_is_legacy_only_and_keeps_ids_labels_separate():
    legacy = LegacyProviderCapability(
        id='deepseek', label='DeepSeek API', models=[LegacyModelCapability(
            id='deepseek-v4-pro', label='DeepSeek V4 Pro',
            thinking_modes=[LegacyThinkingCapability(
                id='on', label='开启思考', reasoning_efforts=['high'])],
            executor_nodes=list(LLM_EXECUTOR_NODES),
        )],
    )
    response = AgentCapabilitiesResponse(
        legacy_providers=[legacy],
        executor_nodes=list(LLM_EXECUTOR_NODES))

    assert response.legacy_providers[0].models[0].id == 'deepseek-v4-pro'
    assert response.legacy_providers[0].models[0].label == 'DeepSeek V4 Pro'
    assert not hasattr(response, 'drivers') or 'drivers' not in response.model_fields
    with pytest.raises(ValidationError, match='不得重复'):
        AgentCapabilitiesResponse(
            legacy_providers=[legacy, legacy],
            executor_nodes=list(LLM_EXECUTOR_NODES))
    with pytest.raises(ValidationError):
        AgentCapabilitiesResponse(
            legacy_providers=[legacy],
            executor_nodes=list(LLM_EXECUTOR_NODES)[:-1])


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
    # 现役契约（contracts/schema/plot.py）：省略 present = 已确定与 participants
    # 一致（合法默认），None = 未知，[] = 明确空——三态必须分开。
    base = dict(evidence_refs=['e01_01'], time_layer='present', place=None)
    assert SourceContext(**base, present=[]).present == []
    assert SourceContext(**base, present=None).present is None
    assert SourceContext(**base).present is None, '省略 = 合法默认，不是错误'
    actor = {'char_id': 'char_001', 'presentation_ref': 'ap01_01'}
    with pytest.raises(ValidationError):
        SourceContext(**base, present=[actor, actor])
    distinct = SourceContext(**base, present=[actor,
        {'char_id': 'char_001', 'presentation_ref': 'ap01_02'}])
    assert [row.presentation_ref for row in distinct.present] == ['ap01_01', 'ap01_02']
    # 旧字段保留拒绝反例；不能用它证明当前完整 ActorRef 的重复检查。
    with pytest.raises(ValidationError):
        SourceContext(**base, present_char_ids=['char_001', 'char_001'])


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
        # F8b-3：ApprovalDoc.prepare 字段随 Stage6 prepare 链退役删除。
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
    assert 'prepare' not in ApprovalDoc.model_fields
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
        Stage0Segment, Stage0Segments,
        Stage1Segment, Stage1View,
        UploadResult,
    ):
        assert model.model_config['extra'] == 'forbid', model.__name__
