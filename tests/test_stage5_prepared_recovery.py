#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage5 历史 ``prepared`` 的 0-wire 跨 run 恢复合同。

这一份只造测试目录与合成 sidecar，不读取真实 ``outputs/``，也不启动 executor、
driver 或模型。重点守住两条危险边界：默认付费写作必须在首个 wire 前发现
foreign prepared；显式恢复则只能把已封存响应按当前代码重判、留下 typed lineage。
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from contracts.invariants import FREE_ENTRIES, NODES, VALUED_FLAGS, WRITE_EFFECTS
from contracts.schema.common import (
    InputEdge, PreparedCheckpointAdoption, RoundFreeProvenance,
    RoundLlmProvenance, Usage,
)
from contracts.schema.orchestration import AgentProfile
from contracts.schema.stage5 import (
    Chapter, ChapterSegment, PreparedRecoveryAction,
    PreparedRecoveryChapter,
    PreparedRecoveryJournal,
    PreparedRecoveryManifest,
    PreparedRecoveryWorkpack,
)
from novel.errors import ArtifactUnreadable, ProvenanceRefused, UnsupportedRunMode
from novel.agents import session_audit as agent_session_audit
from novel.llm import agent_runs, stage5_runs
from novel.llm.cache import CachePolicy
from novel.llm.ledger import CostLedger, ledger_path
from novel.orchestrator import nodes as N
from novel.run import RunContext


SID = '2007780338055790990'
SOURCE_RUN_ID = 'source-run-1'
TARGET_RUN_ID = 'target-run-2'
SHA = 'a' * 64
OTHER_SHA = 'b' * 64
ATTEMPT_ID = '1' * 16
PROFILE = AgentProfile(
    driver='codex', model='gpt-5.6-sol', reasoning_effort='high',
    execution_mode='standard',
)


def _chapter_payload(*, chapter: int = 2) -> dict[str, object]:
    touched = (
        f'stage5/ch{chapter:02d}.INVALID.json',
        f'stage5/ch{chapter:02d}.json',
        f'stage5/ch{chapter:02d}.rejected.json',
        f'stage5/trim_audit_ch{chapter:02d}.json',
        f'stage5/trim_resolution_ch{chapter:02d}.json',
    )
    payload: dict[str, object] = {
        'output_ordinal': chapter,
        'mode': 'write',
        'purpose': f'stage5.write·ch{chapter}',
        'source_code': 'old-code',
        'source_run_identity_sha256': SHA,
        'source_run_file_sha256': SHA,
        'source_commit_sha256': SHA,
        'source_commit_file_sha256': SHA,
        'source_checkpoint_sha256': SHA,
        'source_checkpoint_file_sha256': SHA,
        'source_checkpoint_revision': 1,
        'source_attempt_id': ATTEMPT_ID,
        'source_response_sha256': SHA,
        'source_request_fingerprint': SHA,
        'source_session_run_id': SOURCE_RUN_ID,
        'source_provider_session_id': 'provider-session-2',
        'source_adoption_id': '0' * 64,
        'input_sha256': SHA,
        'system_sha256': SHA,
        'user_sha256': SHA,
        'response_schema_sha256': SHA,
        'response_model': 'ChapterResponse',
        'requested_output_token_ceiling': 4096,
        'profile': PROFILE.model_dump(mode='json'),
        'candidate_sha256': SHA,
        'touched_paths': touched,
        'source_baselines': {path: None for path in touched},
        'current_baselines': {path: None for path in touched},
        'hard_rules': (),
        'regressions': (),
    }
    payload['source_adoption_id'] = stage5_runs.source_adoption_id(
        sid=SID,
        source_run_id=SOURCE_RUN_ID,
        chapter=chapter,
        source_run_identity_sha256=SHA,
        source_run_file_sha256=SHA,
        source_commit_sha256=SHA,
        source_commit_file_sha256=SHA,
        source_checkpoint_sha256=SHA,
        source_checkpoint_file_sha256=SHA,
        source_response_sha256=SHA,
    )
    return payload


def _workpack_payload() -> dict[str, object]:
    payload: dict[str, object] = {
        'version': 1,
        'kind': 'stage5-prepared-recovery-workpack-v1',
        'read_only': True,
        'sid': SID,
        'node': 'stage5.write',
        'source_run_id': SOURCE_RUN_ID,
        'target_run_id': TARGET_RUN_ID,
        'source_code': 'old-code',
        'target_code': 'current-code',
        'chapters': (_chapter_payload(),),
        'subject_sha256': '0' * 64,
    }
    payload['subject_sha256'] = stage5_runs.workpack_subject(payload)
    return payload


def _journal_payload(*, state: str = 'prepared') -> dict[str, object]:
    chapter = _chapter_payload()
    touched = chapter['touched_paths']
    assert isinstance(touched, tuple)
    committed = state == 'committed'
    return {
        'state': state,
        'prepared_at': '2026-08-29T00:00:00Z',
        'committed_at': '2026-08-29T00:00:01Z' if committed else None,
        'sid': SID,
        'source_run_id': SOURCE_RUN_ID,
        'target_run_id': TARGET_RUN_ID,
        'code': 'current-code',
        'source_code': 'old-code',
        'output_ordinal': 2,
        'subject_sha256': SHA,
        'source_adoption_id': chapter['source_adoption_id'],
        'source_run_identity_sha256': SHA,
        'source_run_file_sha256': SHA,
        'source_commit_sha256': SHA,
        'source_commit_file_sha256': SHA,
        'source_checkpoint_sha256': SHA,
        'source_checkpoint_file_sha256': SHA,
        'source_attempt_id': ATTEMPT_ID,
        'source_response_sha256': SHA,
        'actor': 'operator',
        'reason': '逐项核对',
        'primary_artifact': 'stage5/ch02.json',
        'primary_payload_sha256': SHA,
        'primary_payload_fingerprint': 'a' * 10,
        'finalization_sha256': SHA,
        'touched_paths': touched,
        'baselines': {path: None for path in touched},
        'landed_payload_sha256': SHA if committed else None,
        'post_file_sha256s': (
            {path: (SHA if path == 'stage5/ch02.json' else None) for path in touched}
            if committed else None
        ),
        'journal_sha256': SHA,
    }


def _adoption_for(journal: PreparedRecoveryJournal) -> PreparedCheckpointAdoption:
    return PreparedCheckpointAdoption(
        source_run_id=journal.source_run_id,
        target_run_id=journal.target_run_id,
        source_code=journal.source_code,
        source_run_identity_sha256=journal.source_run_identity_sha256,
        source_run_file_sha256=journal.source_run_file_sha256,
        source_commit_sha256=journal.source_commit_sha256,
        source_commit_file_sha256=journal.source_commit_file_sha256,
        source_checkpoint_sha256=journal.source_checkpoint_sha256,
        source_checkpoint_file_sha256=journal.source_checkpoint_file_sha256,
        source_attempt_id=journal.source_attempt_id,
        source_response_sha256=journal.source_response_sha256,
        source_adoption_id=journal.source_adoption_id,
        action_subject_sha256=journal.subject_sha256,
        actor=journal.actor,
        reason=journal.reason,
    )


def _adoption_for_chapter(
    chapter: PreparedRecoveryChapter, action: PreparedRecoveryAction,
) -> PreparedCheckpointAdoption:
    return PreparedCheckpointAdoption(
        source_run_id=action.source_run_id,
        target_run_id=action.target_run_id,
        source_code=chapter.source_code,
        source_run_identity_sha256=chapter.source_run_identity_sha256,
        source_run_file_sha256=chapter.source_run_file_sha256,
        source_commit_sha256=chapter.source_commit_sha256,
        source_commit_file_sha256=chapter.source_commit_file_sha256,
        source_checkpoint_sha256=chapter.source_checkpoint_sha256,
        source_checkpoint_file_sha256=chapter.source_checkpoint_file_sha256,
        source_attempt_id=chapter.source_attempt_id,
        source_response_sha256=chapter.source_response_sha256,
        source_adoption_id=chapter.source_adoption_id,
        action_subject_sha256=action.subject_sha256,
        actor=action.actor,
        reason=action.reason,
    )


def _typed_chapter(
    *, text: str, round_no: int,
    adoption: PreparedCheckpointAdoption | None,
    root_clean_sha1: str = 'a' * 40,
) -> Chapter:
    if adoption is None:
        provenance = RoundFreeProvenance(
            code='current-code', root_clean_sha1=root_clean_sha1, inputs={},
            round=round_no, origin='human')
    else:
        provenance = RoundLlmProvenance(
            code='current-code', root_clean_sha1=root_clean_sha1,
            inputs={
                'stage4/writer_context.json': InputEdge(
                    file='b' * 10, consumed=None),
            },
            usage=Usage(
                calls_made=0, calls_cached=0,
                prompt_tokens=0, completion_tokens=0, reasoning_tokens=0),
            prompts={'SYS': 'c' * 10}, model='claude-opus-5',
            adopted_models=['claude-opus-5'], round=round_no,
            request_fingerprint=SHA,
            prepared_checkpoint_adoption=adoption,
        )
    return Chapter(output_ordinal=2, segments=[ChapterSegment(slot='s01', text=text)], _provenance=provenance)


def _committed_journal_for(
    chapter: PreparedRecoveryChapter,
    action: PreparedRecoveryAction,
    primary: Chapter | dict[str, object],
) -> PreparedRecoveryJournal:
    from novel import prov

    primary_payload = (
        primary.model_dump(by_alias=True, mode='json')
        if isinstance(primary, Chapter) else dict(primary)
    )
    primary_sha = agent_runs.canonical_sha256(primary_payload)
    raw = _journal_payload(state='committed')
    raw.update({
        'sid': SID,
        'source_run_id': action.source_run_id,
        'target_run_id': action.target_run_id,
        'code': 'current-code',
        'source_code': chapter.source_code,
        'output_ordinal': chapter.output_ordinal,
        'subject_sha256': action.subject_sha256,
        'source_adoption_id': chapter.source_adoption_id,
        'source_run_identity_sha256': chapter.source_run_identity_sha256,
        'source_run_file_sha256': chapter.source_run_file_sha256,
        'source_commit_sha256': chapter.source_commit_sha256,
        'source_commit_file_sha256': chapter.source_commit_file_sha256,
        'source_checkpoint_sha256': chapter.source_checkpoint_sha256,
        'source_checkpoint_file_sha256': chapter.source_checkpoint_file_sha256,
        'source_attempt_id': chapter.source_attempt_id,
        'source_response_sha256': chapter.source_response_sha256,
        'actor': action.actor,
        'reason': action.reason,
        'primary_artifact': f'stage5/ch{chapter.output_ordinal:02d}.json',
        'primary_payload_sha256': primary_sha,
        'primary_payload_fingerprint': prov.projection_fingerprint(
            primary_payload),
        'touched_paths': chapter.touched_paths,
        'baselines': chapter.current_baselines,
        'landed_payload_sha256': primary_sha,
        'post_file_sha256s': {
            rel: (OTHER_SHA if rel == f'stage5/ch{chapter.output_ordinal:02d}.json'
                  else None)
            for rel in chapter.touched_paths
        },
        'journal_sha256': '0' * 64,
    })
    raw['journal_sha256'] = stage5_runs._journal_sha256(raw)
    candidate = PreparedRecoveryJournal.model_validate(raw)
    closed = candidate.model_dump(mode='json')
    closed['journal_sha256'] = stage5_runs._journal_sha256(closed)
    return PreparedRecoveryJournal.model_validate(closed)


def _note_recovery_audit(
    ledger: CostLedger,
    chapter: PreparedRecoveryChapter,
    action: PreparedRecoveryAction,
    *, artifact_fingerprint: str = 'a' * 10,
) -> None:
    ledger.note_prepared_recovery_adoption(
        sid=SID,
        purpose=chapter.purpose,
        chapter=chapter.output_ordinal,
        source_run_id=action.source_run_id,
        source_code=chapter.source_code,
        source_run_identity_sha256=chapter.source_run_identity_sha256,
        source_run_file_sha256=chapter.source_run_file_sha256,
        source_commit_sha256=chapter.source_commit_sha256,
        source_commit_file_sha256=chapter.source_commit_file_sha256,
        source_checkpoint_sha256=chapter.source_checkpoint_sha256,
        source_checkpoint_file_sha256=chapter.source_checkpoint_file_sha256,
        source_attempt_id=chapter.source_attempt_id,
        source_response_sha256=chapter.source_response_sha256,
        source_adoption_id=chapter.source_adoption_id,
        subject_sha256=action.subject_sha256,
        actor=action.actor,
        reason=action.reason,
        artifact=f'stage5/ch{chapter.output_ordinal:02d}.json',
        artifact_fingerprint=artifact_fingerprint,
    )


def _recover_flags() -> list[str]:
    return [
        '--recover-prepared', SOURCE_RUN_ID,
        '--actor', 'operator',
        '--reason', '逐项核对工作包后按当前闸门收口',
        '--subject-sha256', SHA,
    ]


def _write_source_commit(book: Path, *, chapter: int = 2) -> tuple[Path, dict[str, object]]:
    """只造 foreign envelope 专测所需的 generic commit，不冒充完整 checkpoint。"""
    touched = tuple(sorted(stage5_runs.touched_paths(chapter)))
    target = (
        stage5_runs.paths.stage5_chapter_run_path(
            book, SOURCE_RUN_ID, chapter)
        / agent_runs.COMMIT_FILE
    )
    record: dict[str, object] = {
        'version': 1,
        'state': 'prepared',
        'prepared_at': '2026-08-29T00:00:00Z',
        'committed_at': None,
        'run_id': SOURCE_RUN_ID,
        'sid': SID,
        'node': 'stage5.write',
        'code': 'old-code',
        'run_sha256': SHA,
        'primary_artifact': f'stage5/ch{chapter:02d}.json',
        'primary_payload_sha256': SHA,
        'primary_payload_fingerprint': 'a' * 10,
        'finalization_sha256': SHA,
        'touched_paths': list(touched),
        'baselines': {path: None for path in touched},
        'purpose': f'stage5.write·ch{chapter}',
        'attempt_id': ATTEMPT_ID,
        'request_identity': {},
        'landed_payload_sha256s': None,
        'post_file_sha256s': None,
        'commit_sha256': '0' * 64,
    }
    record['commit_sha256'] = agent_runs._commit_hash(record)
    agent_runs._atomic_json(target, record)
    (target.parent / agent_runs.RUN_FILE).write_bytes(b'immutable-source-run')
    checkpoint = target.parent / 'checkpoints' / 'rev-0001.json'
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b'immutable-source-checkpoint')
    return target, record


def _source_sidecar_bytes(
        book: Path, *, chapter: int = 2,
) -> dict[str, bytes]:
    root = stage5_runs.paths.stage5_chapter_run_path(
        book, SOURCE_RUN_ID, chapter)
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob('*'))
        if path.is_file() and not path.name.endswith('.lock')
    }


def _recovery_chapter(
        ctx, source_commit_path: Path, source_commit: dict[str, object],
        *, chapter: int = 2,
) -> PreparedRecoveryChapter:
    payload = _chapter_payload(chapter=chapter)
    payload.update({
        'source_commit_sha256': source_commit['commit_sha256'],
        'source_commit_file_sha256': agent_runs.canonical_sha256(
            source_commit_path.read_bytes()),
    })
    payload['current_baselines'] = {
        rel: agent_runs.file_sha256(ctx, rel)
        for rel in payload['touched_paths']
    }
    payload['source_adoption_id'] = stage5_runs.source_adoption_id(
        sid=SID,
        source_run_id=SOURCE_RUN_ID,
        chapter=chapter,
        source_run_identity_sha256=str(payload['source_run_identity_sha256']),
        source_run_file_sha256=str(payload['source_run_file_sha256']),
        source_commit_sha256=str(payload['source_commit_sha256']),
        source_commit_file_sha256=str(payload['source_commit_file_sha256']),
        source_checkpoint_sha256=str(payload['source_checkpoint_sha256']),
        source_checkpoint_file_sha256=SHA,
        source_response_sha256=str(payload['source_response_sha256']),
    )
    return PreparedRecoveryChapter.model_validate(payload)


def _manifest_for_chapter(
        ctx, chapter: PreparedRecoveryChapter, *,
        actor: str = 'operator', reason: str = '逐项核对',
        write: bool = True,
) -> tuple[PreparedRecoveryManifest, PreparedRecoveryAction]:
    workpack_raw: dict[str, object] = {
        'version': 1,
        'kind': 'stage5-prepared-recovery-workpack-v1',
        'read_only': True,
        'sid': ctx.sid,
        'node': 'stage5.write',
        'source_run_id': SOURCE_RUN_ID,
        'target_run_id': ctx.run_id,
        'source_code': chapter.source_code,
        'target_code': ctx.code,
        'chapters': [chapter.model_dump(mode='json')],
        'subject_sha256': '0' * 64,
    }
    workpack_raw['subject_sha256'] = stage5_runs.workpack_subject(workpack_raw)
    workpack = PreparedRecoveryWorkpack.model_validate(workpack_raw)
    action = PreparedRecoveryAction(
        actor=actor,
        reason=reason,
        subject_sha256=workpack.subject_sha256,
        source_run_id=SOURCE_RUN_ID,
        target_run_id=ctx.run_id,
    )
    raw: dict[str, object] = {
        'version': 1,
        'kind': 'stage5-prepared-recovery-manifest-v1',
        'workpack': workpack.model_dump(mode='json'),
        'action': action.model_dump(mode='json'),
        'manifest_sha256': '0' * 64,
    }
    raw['manifest_sha256'] = stage5_runs.manifest_sha256(raw)
    manifest = PreparedRecoveryManifest.model_validate(raw)
    if write:
        stage5_runs.write_recovery_manifest(ctx, manifest)
    return manifest, action


class _RecoveryLedger:
    def __init__(self) -> None:
        self.calls_made = 0
        self.calls_cached = 0
        self.required: list[dict[str, object]] = []
        self.lineage_required: list[dict[str, object]] = []
        self.lineage_available = True
        self.preclaims: list[dict[str, object]] = []
        self.preclaim_existing = False

    def require_prepared_recovery_adoption(self, **kwargs) -> None:
        self.required.append(dict(kwargs))

    def require_prepared_recovery_lineage(self, **kwargs) -> None:
        self.lineage_required.append(dict(kwargs))
        if not self.lineage_available:
            raise ProvenanceRefused('合成 recovery adoption audit 已删除')

    def preclaim_prepared_recovery_adoption(self, **kwargs) -> bool:
        self.preclaims.append(dict(kwargs))
        if self.preclaim_existing and kwargs.get('expected_row') is None:
            raise ProvenanceRefused('合成 source adoption 已由旧 target 占用')
        return self.preclaim_existing


def _recovery_ctx(book: Path, run_id: str):
    return SimpleNamespace(
        novel_dir=book,
        run_id=run_id,
        code='current-code',
        sid=SID,
        node='stage5.write',
        ledger=_RecoveryLedger(),
        paid=False,
        executor_profile=None,
    )


def _recovery_store_ctx(book: Path, run_id: str) -> RunContext:
    """真实写盘夹具使用可写运行上下文；来源采用账仍单独隔离测试。"""
    from novel.work_units import initialize_workflow
    initialize_workflow(book.parent)
    clean = book / 'stage0' / 'clean_text.txt'
    if not clean.exists():
        clean.parent.mkdir(parents=True, exist_ok=True)
        clean.write_text('prepared 恢复的固定源正文。\n', encoding='utf-8')
    ctx = RunContext(
        novel_dir=book, run_id=run_id, code='current-code',
        node='stage5.write', paid=False, cache=CachePolicy.default(),
        ledger=CostLedger(path=ledger_path(book.parent), run_id=run_id))
    ctx.ledger = _RecoveryLedger()
    return ctx


def test_recovery_flags_are_free_valued_and_keep_read_write_effects_distinct():
    emit = '--emit-prepared-recovery-workpack'
    recover = '--recover-prepared'
    assert {emit, recover} <= set(FREE_ENTRIES)
    assert {emit, recover} <= set(VALUED_FLAGS)
    assert {emit, recover} <= set(NODES['stage5.write'].flags)

    emit_flags = [emit, SOURCE_RUN_ID]
    recover_flags = _recover_flags()
    assert N.is_paid('stage5.write', emit_flags) is False
    assert N.is_paid('stage5.write', recover_flags) is False
    assert N.would_write('stage5.write', emit_flags) is False
    assert N.would_write('stage5.write', recover_flags) is True
    assert WRITE_EFFECTS[emit] == ''
    assert 'source sidecar' in WRITE_EFFECTS[recover]

    emit_argv = N.build_argv(
        'stage5.write', SID, emit_flags, base='E:/synthetic-output')
    recover_argv = N.build_argv(
        'stage5.write', SID, recover_flags, base='E:/synthetic-output')
    assert '--yes' not in emit_argv and '--yes' not in recover_argv
    assert emit_argv[emit_argv.index(emit) + 1] == SOURCE_RUN_ID
    assert recover_argv[recover_argv.index(recover) + 1] == SOURCE_RUN_ID
    assert recover_argv[recover_argv.index('--subject-sha256') + 1] == SHA


@pytest.mark.parametrize('extra', [
    ['--chapters', '2'],
    ['--revise'],
    ['--response-repair', 'a' * 16],
    ['--actor', '不能给只读动作塞人工凭据'],
    ['--recover-prepared', SOURCE_RUN_ID],
])
def test_emit_workpack_is_an_exact_read_only_flag_combination(extra):
    flags = ['--emit-prepared-recovery-workpack', SOURCE_RUN_ID, *extra]
    with pytest.raises(UnsupportedRunMode, match='纯读动作'):
        N.build_argv('stage5.write', SID, flags, base='E:/synthetic-output')


@pytest.mark.parametrize('bad_flags', [
    ['--recover-prepared', SOURCE_RUN_ID],
    [
        '--recover-prepared', SOURCE_RUN_ID,
        '--actor', 'operator', '--reason', '理由完整',
    ],
    [*_recover_flags(), '--chapters', '2'],
    [*_recover_flags(), '--revise'],
    [*_recover_flags(), '--response-repair', 'a' * 16],
    [*_recover_flags(), '--emit-prepared-recovery-workpack', SOURCE_RUN_ID],
])
def test_recover_prepared_accepts_only_source_and_three_typed_human_fields(bad_flags):
    with pytest.raises(UnsupportedRunMode):
        N.build_argv('stage5.write', SID, bad_flags, base='E:/synthetic-output')


def test_recovery_subject_sha_cannot_be_orphaned_into_default_paid_writing():
    with pytest.raises(UnsupportedRunMode, match='只绑定 --recover-prepared'):
        N.build_argv(
            'stage5.write', SID, ['--subject-sha256', SHA],
            base='E:/synthetic-output',
        )


def test_recovery_flags_cannot_acquire_executor_or_egress_capabilities():
    with pytest.raises(UnsupportedRunMode):
        N.build_argv(
            'stage5.write', SID,
            ['--emit-prepared-recovery-workpack', SOURCE_RUN_ID],
            base='E:/synthetic-output', executor_profile=PROFILE,
        )
    with pytest.raises(UnsupportedRunMode):
        N.build_argv(
            'stage5.write', SID, _recover_flags(),
            base='E:/synthetic-output', executor_profile=PROFILE,
        )
    with pytest.raises(UnsupportedRunMode):
        N.build_argv(
            'stage5.write', SID, _recover_flags(),
            base='E:/synthetic-output', egress_authorized=True,
        )


def test_workpack_closes_scope_and_refuses_revision_or_failed_revalidation():
    valid = PreparedRecoveryWorkpack.model_validate(_workpack_payload())
    assert [row.output_ordinal for row in valid.chapters] == [2]
    assert valid.read_only is True

    same_run = _workpack_payload()
    same_run['target_run_id'] = SOURCE_RUN_ID
    with pytest.raises(ValidationError, match='新 target run'):
        PreparedRecoveryWorkpack.model_validate(same_run)

    revision = _workpack_payload()
    revision['chapters'][0]['mode'] = 'revise'  # type: ignore[index]
    with pytest.raises(ValidationError, match='只接 initial write'):
        PreparedRecoveryWorkpack.model_validate(revision)

    red = _workpack_payload()
    red['chapters'][0]['hard_rules'] = ('verbatim_missing',)  # type: ignore[index]
    with pytest.raises(ValidationError, match='当前重判已通过'):
        PreparedRecoveryWorkpack.model_validate(red)

    tampered = _workpack_payload()
    tampered['target_code'] = 'tampered-after-review'
    with pytest.raises(ValidationError, match='subject SHA 不闭合'):
        PreparedRecoveryWorkpack.model_validate(tampered)


def test_workpack_builder_is_read_only_and_covers_every_unresolved_prepared(
        tmp_path, monkeypatch):
    from novel.stages.stage5 import write

    book = tmp_path / 'prepared工作包_2007780338055790990'
    book.mkdir()
    source_marker = (stage5_runs.paths.service_stage_runs_dir(book, 'stage5')
                     / SOURCE_RUN_ID / 'source.marker')
    source_marker.parent.mkdir(parents=True)
    source_marker.write_bytes(b'immutable-source-sidecar')
    before = source_marker.read_bytes()
    ctx = SimpleNamespace(
        novel_dir=book,
        run_id=TARGET_RUN_ID,
        code='current-code',
        sid=SID,
        node='stage5.write',
        paid=False,
        executor_profile=None,
    )
    rows = {
        chapter: PreparedRecoveryChapter.model_validate(
            _chapter_payload(chapter=chapter))
        for chapter in (2, 3)
    }
    monkeypatch.setattr(write, 'ArtifactStore', lambda _ctx: object())
    monkeypatch.setattr(write, 'require_canonical_layout', lambda _store: None)
    monkeypatch.setattr(write.review, 'load_book', lambda *_args: object())
    monkeypatch.setattr(
        stage5_runs,
        'unresolved_prepared_envelopes',
        lambda _ctx, source: {
            chapter: {'commit_sha256': row.source_commit_sha256}
            for chapter, row in rows.items()
        } if source == SOURCE_RUN_ID else pytest.fail('source run 漂移'),
    )
    monkeypatch.setattr(
        write,
        '_historical_recovery_unit',
        lambda _ctx, _book, *, source_run_id, ordinal: (
            rows[ordinal], object(), object()),
    )

    workpack = write.build_prepared_recovery_workpack(ctx, SOURCE_RUN_ID)

    assert tuple(row.output_ordinal for row in workpack.chapters) == (2, 3)
    assert workpack.subject_sha256 == stage5_runs.workpack_subject(
        workpack.model_dump(mode='json'))
    assert source_marker.read_bytes() == before
    assert not stage5_runs.paths.stage5_prepared_recovery_manifest_path(
        book, TARGET_RUN_ID).exists()


def test_workpack_subject_is_recomputed_at_manifest_read_write_and_apply_boundaries(
        tmp_path, monkeypatch):
    from novel.orchestrator import approvals
    from novel.stages.stage5 import write

    book = tmp_path / 'prepared主体边界_2007780338055790990'
    book.mkdir()
    ctx = _recovery_ctx(book, TARGET_RUN_ID)
    valid = PreparedRecoveryWorkpack.model_validate(_workpack_payload())
    action = PreparedRecoveryAction(
        actor='operator', reason='逐项核对',
        subject_sha256=valid.subject_sha256,
        source_run_id=SOURCE_RUN_ID, target_run_id=TARGET_RUN_ID,
    )
    poisoned = valid.model_copy(update={'target_code': 'poisoned-current-code'})
    forged = PreparedRecoveryManifest.model_construct(
        workpack=poisoned, action=action, manifest_sha256=SHA)

    with pytest.raises(ProvenanceRefused, match='subject SHA 不闭合'):
        stage5_runs.write_recovery_manifest(ctx, forged)

    raw = {
        'version': 1,
        'kind': 'stage5-prepared-recovery-manifest-v1',
        'workpack': valid.model_dump(mode='json'),
        'action': action.model_dump(mode='json'),
        'manifest_sha256': '0' * 64,
    }
    raw['manifest_sha256'] = stage5_runs.manifest_sha256(raw)
    path = stage5_runs.paths.stage5_prepared_recovery_manifest_path(
        book, TARGET_RUN_ID)
    agent_runs._atomic_json(path, raw)
    poisoned_raw = deepcopy(raw)
    poisoned_raw['workpack']['target_code'] = 'poisoned-on-disk'  # type: ignore[index]
    poisoned_raw['manifest_sha256'] = stage5_runs.manifest_sha256(poisoned_raw)
    agent_runs._atomic_json(path, poisoned_raw)
    with pytest.raises(ArtifactUnreadable, match='subject SHA 不闭合'):
        stage5_runs.read_recovery_manifest(ctx)

    path.unlink()
    monkeypatch.setattr(
        write, 'build_prepared_recovery_workpack',
        lambda *_args, **_kwargs: poisoned)
    monkeypatch.setattr(
        approvals, 'authorize_prepared_recovery',
        lambda *_args, **_kwargs: pytest.fail(
            'apply subject 重算失败时不得进入授权/写盘'))
    with pytest.raises(ProvenanceRefused, match='subject SHA 不闭合'):
        write.run_prepared_recovery(
            ctx, SOURCE_RUN_ID, actor='operator', reason='逐项核对',
            subject_sha256=valid.subject_sha256)


def test_recovery_producer_authorizes_every_chapter_and_stays_zero_wire(
        tmp_path, monkeypatch):
    """producer 纵切保留真实 manifest，只隔离昂贵上游与已由低层专测的提交机。"""
    from novel.orchestrator import approvals
    from novel.stages.stage5 import write

    book = tmp_path / 'prepared整书恢复_2007780338055790990'
    book.mkdir()
    source_marker = (stage5_runs.paths.service_stage_runs_dir(book, 'stage5')
                     / SOURCE_RUN_ID / 'source.marker')
    source_marker.parent.mkdir(parents=True)
    source_marker.write_bytes(b'immutable-source-run-marker')
    source_before = source_marker.read_bytes()
    ctx = _recovery_ctx(book, TARGET_RUN_ID)
    ctx.paid = False
    ctx.executor_profile = None
    rows = tuple(
        PreparedRecoveryChapter.model_validate(
            _chapter_payload(chapter=chapter))
        for chapter in (2, 3)
    )
    workpack_raw: dict[str, object] = {
        'version': 1,
        'kind': 'stage5-prepared-recovery-workpack-v1',
        'read_only': True,
        'sid': SID,
        'node': 'stage5.write',
        'source_run_id': SOURCE_RUN_ID,
        'target_run_id': TARGET_RUN_ID,
        'source_code': 'old-code',
        'target_code': 'current-code',
        'chapters': [row.model_dump(mode='json') for row in rows],
        'subject_sha256': '0' * 64,
    }
    workpack_raw['subject_sha256'] = stage5_runs.workpack_subject(workpack_raw)
    workpack = PreparedRecoveryWorkpack.model_validate(workpack_raw)
    authorized: list[tuple[Path, str, list[int]]] = []
    finalized: list[tuple[int, PreparedRecoveryAction]] = []
    zero_wire_scans: list[str] = []
    report = SimpleNamespace(passed=True)

    real_zero_wire_guard = stage5_runs.require_prepared_recovery_target_clean

    def zero_wire_guard(got):
        zero_wire_scans.append(got.run_id)
        real_zero_wire_guard(got)

    monkeypatch.setattr(
        stage5_runs, 'require_prepared_recovery_target_clean', zero_wire_guard)

    monkeypatch.setattr(
        write, 'build_prepared_recovery_workpack',
        lambda got, source: (
            workpack
            if got is ctx and source == SOURCE_RUN_ID
            else pytest.fail('producer source/ctx 漂移')
        ),
    )
    monkeypatch.setattr(
        approvals,
        'authorize_prepared_recovery',
        lambda base, sid, chapters: authorized.append(
            (Path(base), sid, list(chapters))),
    )
    monkeypatch.setattr(
        stage5_runs,
        'source_prepared_envelopes',
        lambda got, source: (
            {2: object(), 3: object()}
            if got is ctx and source == SOURCE_RUN_ID
            else pytest.fail('producer source scope 漂移')
        ),
    )
    monkeypatch.setattr(write, 'ArtifactStore', lambda got: object())
    monkeypatch.setattr(write, 'require_canonical_layout', lambda _store: None)
    monkeypatch.setattr(write.review, 'load_book', lambda *_args: object())
    monkeypatch.setattr(
        write,
        '_historical_recovery_unit',
        lambda got, _book, *, source_run_id, ordinal: (
            rows[(2, 3).index(ordinal)],
            SimpleNamespace(ordinal=ordinal),
            SimpleNamespace(findings=()),
        ) if got is ctx and source_run_id == SOURCE_RUN_ID
        else pytest.fail('producer current unit 漂移'),
    )
    monkeypatch.setattr(stage5_runs, 'recovery_state', lambda *_args: None)

    def finalize(got, prepared, _evaluation, row, action):
        assert got is ctx and prepared.ordinal == row.output_ordinal
        finalized.append((row.output_ordinal, action))
        return SimpleNamespace(
            where=f'stage5/ch{row.output_ordinal:02d}.json',
            trim_notes=(),
            findings=(),
        )

    monkeypatch.setattr(write, '_finalize_recovered_prepared', finalize)
    monkeypatch.setattr(write, '_report', lambda *_args, **_kwargs: report)
    monkeypatch.setattr(
        write,
        '_generate_chapter',
        lambda *_args, **_kwargs: pytest.fail(
            'prepared recovery producer 不得进入 client/wire 生成路'),
    )

    got = write.run_prepared_recovery(
        ctx,
        SOURCE_RUN_ID,
        actor='operator',
        reason='逐章审阅完整工作包后收口',
        subject_sha256=workpack.subject_sha256,
    )

    assert got is report
    assert authorized == [(book.parent, SID, [2, 3])]
    assert [chapter for chapter, _action in finalized] == [2, 3]
    assert all(
        action == PreparedRecoveryAction(
            actor='operator',
            reason='逐章审阅完整工作包后收口',
            subject_sha256=workpack.subject_sha256,
            source_run_id=SOURCE_RUN_ID,
            target_run_id=TARGET_RUN_ID,
        )
        for _chapter, action in finalized
    )
    manifest = stage5_runs.read_recovery_manifest(ctx)
    assert manifest is not None and manifest.workpack == workpack
    assert manifest.action == finalized[0][1]
    assert zero_wire_scans == [TARGET_RUN_ID] * 4
    assert ctx.ledger.calls_made == ctx.ledger.calls_cached == 0
    assert source_marker.read_bytes() == source_before


@pytest.mark.parametrize('cached', [False, True])
@pytest.mark.parametrize('scope', [
    {'sid': SID, 'node': 'stage5.write'},
    {'sid': 'wrong-sid', 'node': 'stage4.plan'},
    {'sid': SID},
    {'node': 'stage5.write'},
    {},
])
def test_recovery_zero_wire_refuses_persistent_cost_rows_after_restart(
        tmp_path, cached, scope):
    book = tmp_path / 'prepared持久成本污染_2007780338055790990'
    book.mkdir()
    ctx = _recovery_ctx(book, TARGET_RUN_ID)
    target = ledger_path(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({
        'run_id': TARGET_RUN_ID,
        'purpose': 'stage5.write·ch2',
        'cached': cached,
        **scope,
    }, ensure_ascii=False) + '\n', encoding='utf-8')

    with pytest.raises(ProvenanceRefused, match='任意持久 ledger'):
        stage5_runs.require_prepared_recovery_target_clean(ctx)


@pytest.mark.parametrize('event_node', ['stage5.write', 'stage4.plan', None])
def test_recovery_zero_wire_refuses_any_persistent_target_agent_session(
        tmp_path, event_node):
    book = tmp_path / 'prepared持久会话污染_2007780338055790990'
    book.mkdir()
    ctx = _recovery_ctx(book, TARGET_RUN_ID)
    event = agent_session_audit.AgentSessionEvent(
        session_run_id='target-session-1',
        parent_run_id=TARGET_RUN_ID,
        role='executor',
        node=event_node,
        chapter=2,
        kind='started',
        ts=1,
    )
    target = agent_session_audit.audit_path(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_text(
        json.dumps(event.to_dict(), ensure_ascii=False) + '\n',
        encoding='utf-8')

    with pytest.raises(ProvenanceRefused, match='持久 agent session'):
        stage5_runs.require_prepared_recovery_target_clean(ctx)


def test_recovery_zero_wire_refuses_unclosed_persistent_tail(tmp_path):
    book = tmp_path / 'prepared半行污染_2007780338055790990'
    book.mkdir()
    ctx = _recovery_ctx(book, TARGET_RUN_ID)
    target = ledger_path(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_bytes(b'{"run_id":"target-run-2"')

    with pytest.raises(ProvenanceRefused, match='未闭合末行'):
        stage5_runs.require_prepared_recovery_target_clean(ctx)


@pytest.mark.parametrize('revise', [False, True])
def test_direct_stage5_write_agent_refuses_before_factory_probe_turn_or_accounts(
        tmp_path, monkeypatch, revise):
    from novel.orchestrator.node_agents import Stage5WriteAgent

    book = tmp_path / 'prepared直连执行者_2007780338055790990'
    book.mkdir()
    run_id = 'paid-target-run'
    ctx = RunContext(
        novel_dir=book,
        node='stage5.write',
        paid=True,
        code='current-code',
        cache=CachePolicy.default(),
        ledger=CostLedger(path=ledger_path(tmp_path), run_id=run_id),
        executor_profile=PROFILE,
        run_id=run_id,
    )
    factory_calls: list[int] = []

    def factory(chapter: int):
        factory_calls.append(chapter)
        pytest.fail('foreign prepared guard 前不得构造/probe driver')

    monkeypatch.setattr(
        stage5_runs,
        'refuse_foreign_prepared_before_wire',
        lambda got, chapters: (_ for _ in ()).throw(
            ProvenanceRefused(
                f'foreign prepared pre-wire: {got.run_id}/{chapters}')),
    )
    agent = Stage5WriteAgent(
        ctx, driver_factory=factory, chapters='2', revise=revise)

    with pytest.raises(ProvenanceRefused, match='foreign prepared pre-wire'):
        agent.run()

    assert factory_calls == []
    assert agent.pool.turns_used == 0
    assert ctx.ledger.calls_made == ctx.ledger.calls_cached == 0
    assert not ledger_path(tmp_path).exists()
    assert not agent_session_audit.audit_path(tmp_path).exists()


def test_recovery_action_manifest_and_canonical_adoption_are_hash_bound_and_typed():
    workpack = PreparedRecoveryWorkpack.model_validate(_workpack_payload())
    action = PreparedRecoveryAction(
        actor='operator', reason='逐项核对', subject_sha256=workpack.subject_sha256,
        source_run_id=SOURCE_RUN_ID, target_run_id=TARGET_RUN_ID,
    )
    manifest = PreparedRecoveryManifest(
        workpack=workpack, action=action, manifest_sha256=OTHER_SHA,
    )
    assert manifest.action.subject_sha256 == manifest.workpack.subject_sha256

    adoption = PreparedCheckpointAdoption(
        actor=action.actor,
        reason=action.reason,
        source_run_id=SOURCE_RUN_ID,
        target_run_id=TARGET_RUN_ID,
        source_code='old-code',
        source_run_identity_sha256=SHA,
        source_run_file_sha256=SHA,
        source_commit_sha256=SHA,
        source_commit_file_sha256=SHA,
        source_checkpoint_sha256=SHA,
        source_checkpoint_file_sha256=SHA,
        source_attempt_id=ATTEMPT_ID,
        source_response_sha256=SHA,
        source_adoption_id=SHA,
        action_subject_sha256=workpack.subject_sha256,
    )
    assert adoption.action_subject_sha256 == workpack.subject_sha256

    mismatched = action.model_copy(update={'subject_sha256': OTHER_SHA})
    with pytest.raises(ValidationError, match='身份漂移'):
        PreparedRecoveryManifest(
            workpack=workpack, action=mismatched, manifest_sha256=SHA)
    with pytest.raises(ValidationError, match='人工凭据不能只含空白'):
        PreparedCheckpointAdoption.model_validate({
            **adoption.model_dump(mode='json'), 'actor': '   ',
        })
    with pytest.raises(ValidationError):
        PreparedCheckpointAdoption.model_validate({
            **adoption.model_dump(mode='json'), 'undeclared': True,
        })


def test_recovery_adoption_journal_and_audit_share_one_exact_semantic_row():
    journal = PreparedRecoveryJournal.model_validate(
        _journal_payload(state='committed'))
    ledger = CostLedger(run_id=TARGET_RUN_ID)
    ledger.note_prepared_recovery_adoption(
        sid=journal.sid,
        purpose=stage5_runs.purpose_of(journal.output_ordinal),
        chapter=journal.output_ordinal,
        source_run_id=journal.source_run_id,
        source_code=journal.source_code,
        source_run_identity_sha256=journal.source_run_identity_sha256,
        source_run_file_sha256=journal.source_run_file_sha256,
        source_commit_sha256=journal.source_commit_sha256,
        source_commit_file_sha256=journal.source_commit_file_sha256,
        source_checkpoint_sha256=journal.source_checkpoint_sha256,
        source_checkpoint_file_sha256=journal.source_checkpoint_file_sha256,
        source_attempt_id=journal.source_attempt_id,
        source_response_sha256=journal.source_response_sha256,
        source_adoption_id=journal.source_adoption_id,
        subject_sha256=journal.subject_sha256,
        actor=journal.actor,
        reason=journal.reason,
        artifact=journal.primary_artifact,
        artifact_fingerprint=journal.primary_payload_fingerprint,
    )
    expected = stage5_runs._expected_recovery_audit(journal)
    ledger.require_prepared_recovery_adoption(expected_row=expected)
    actual = {
        key: value for key, value in ledger.audit_events[0].items()
        if key != 'at'
    }
    assert actual == expected

    ledger.audit_events[0]['reason'] = '被篡改的旁路理由'
    with pytest.raises(ProvenanceRefused, match='整行一致'):
        ledger.require_prepared_recovery_lineage(expected_row=expected)


def test_recovery_journal_is_a_closed_prepared_to_committed_state_machine():
    prepared = PreparedRecoveryJournal.model_validate(_journal_payload())
    committed = PreparedRecoveryJournal.model_validate(
        _journal_payload(state='committed'))
    assert prepared.state == 'prepared' and prepared.committed_at is None
    assert committed.state == 'committed' and committed.committed_at is not None

    half_prepared = deepcopy(_journal_payload())
    half_prepared['landed_payload_sha256'] = SHA
    with pytest.raises(ValidationError, match='prepared 字段不闭合'):
        PreparedRecoveryJournal.model_validate(half_prepared)

    half_committed = deepcopy(_journal_payload(state='committed'))
    half_committed['post_file_sha256s'] = None
    with pytest.raises(ValidationError, match='committed 字段不闭合'):
        PreparedRecoveryJournal.model_validate(half_committed)


def test_recovery_journal_read_refuses_semantic_bytes_with_stale_self_hash(
        tmp_path):
    book = tmp_path / 'prepared日志自哈希_2007780338055790990'
    raw = _journal_payload(state='committed')
    raw['journal_sha256'] = stage5_runs._journal_sha256(raw)
    path = stage5_runs.paths.stage5_chapter_recovery_journal_path(
        book, TARGET_RUN_ID, 2)
    agent_runs._atomic_json(path, raw)
    poisoned = deepcopy(raw)
    poisoned['reason'] = '篡改后只重写外层字节、不重算 self hash'
    agent_runs._atomic_json(path, poisoned)

    with pytest.raises(ArtifactUnreadable, match='journal SHA 不闭合'):
        stage5_runs._validated_recovery_journal(path)


def test_recovery_journal_survives_prepared_crash_and_commits_without_touching_source(
        tmp_path, monkeypatch):
    """用生产 journal 路径走 prepared → 重启 → committed；全程没有 client/wire。"""
    book = tmp_path / 'prepared恢复_2007780338055790990'
    source_path, source_commit = _write_source_commit(book)
    source_before = _source_sidecar_bytes(book)
    first = _recovery_ctx(book, TARGET_RUN_ID)
    chapter = _recovery_chapter(first, source_path, source_commit)
    _manifest, action = _manifest_for_chapter(first, chapter)
    primary = {'chapter': 2, 'content': '当前代码重铸的 canonical'}
    finalization = {'chapter': 2, 'mode': 'write'}

    with stage5_runs.commit_ownership(book, 2):
        prepared = stage5_runs.prepare_recovery_journal(
            first,
            chapter=chapter,
            action=action,
            primary_payload=primary,
            finalization_payload=finalization,
        )
    assert prepared.state == 'prepared'
    journal_path = stage5_runs.paths.stage5_chapter_recovery_journal_path(
        book, TARGET_RUN_ID, 2)
    prepared_bytes = journal_path.read_bytes()
    assert _source_sidecar_bytes(book) == source_before

    # 模拟进程在 prepared 后退出；新 context 必须从同一 target run 接续。
    restarted = _recovery_ctx(book, TARGET_RUN_ID)
    assert stage5_runs.recovery_state(restarted, 2) == prepared
    with stage5_runs.commit_ownership(book, 2):
        resumed = stage5_runs.prepare_recovery_journal(
            restarted,
            chapter=chapter,
            action=action,
            primary_payload=primary,
            finalization_payload=finalization,
        )
    assert resumed == prepared
    assert journal_path.read_bytes() == prepared_bytes

    formal = book / 'stage5' / 'ch02.json'
    formal.parent.mkdir(parents=True)
    formal.write_bytes(b'synthetic-current-canonical')
    expected_formal_sha = agent_runs.file_sha256(restarted, 'stage5/ch02.json')
    assert expected_formal_sha is not None
    # 模拟 canonical 已写而进程尚未把 journal 提升为 committed 的尾窗。
    tail_restarted = _recovery_ctx(book, TARGET_RUN_ID)
    with stage5_runs.commit_ownership(book, 2):
        assert stage5_runs.recovery_target_state(
            tail_restarted, 2, prepared, 'stage5/ch02.json',
            expected_file_sha256=expected_formal_sha,
        ) == 'expected'
    monkeypatch.setattr(
        stage5_runs, '_source_sidecars_match_journal',
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        stage5_runs, '_committed_recovery_closes_live_now',
        lambda *_args, **_kwargs: True,
    )
    with stage5_runs.commit_ownership(book, 2):
        stage5_runs.mark_recovery_committed(
            tail_restarted,
            chapter=2,
            primary_payload=primary,
            finalization_payload=finalization,
        )
        committed = stage5_runs.recovery_state(tail_restarted, 2)
        assert committed is not None and committed.state == 'committed'
        stage5_runs.verify_recovery_committed(
            tail_restarted,
            2,
            committed,
            primary_payload=primary,
            finalization_payload=finalization,
        )
        # committed 尾窗再跑一次只能做幂等复核，不能创建第三态。
        stage5_runs.mark_recovery_committed(
            tail_restarted,
            chapter=2,
            primary_payload=primary,
            finalization_payload=finalization,
        )
    assert stage5_runs.recovery_state(tail_restarted, 2) == committed
    assert _source_sidecar_bytes(book) == source_before
    assert tail_restarted.ledger.required, 'committed 必须对上稳定 source adoption 账'


def test_same_source_prepared_cannot_be_replayed_into_a_second_target_run(tmp_path):
    book = tmp_path / 'prepared重放_2007780338055790990'
    source_path, source_commit = _write_source_commit(book)
    source_before = _source_sidecar_bytes(book)
    first = _recovery_ctx(book, TARGET_RUN_ID)
    chapter = _recovery_chapter(first, source_path, source_commit)
    _manifest, first_action = _manifest_for_chapter(
        first, chapter, actor='operator-1', reason='第一次收口')
    primary = {'chapter': 2, 'content': 'canonical'}
    finalization = {'chapter': 2}
    with stage5_runs.commit_ownership(book, 2):
        stage5_runs.prepare_recovery_journal(
            first,
            chapter=chapter,
            action=first_action,
            primary_payload=primary,
            finalization_payload=finalization,
        )

    second = _recovery_ctx(book, 'target-run-3')
    second_manifest, _second_action = _manifest_for_chapter(
        second, chapter, actor='operator-2', reason='试图跨新 run 重放',
        write=False)
    with pytest.raises(ProvenanceRefused, match='manifest 写盘前拒绝跨 run 重放'):
        stage5_runs.write_recovery_manifest(second, second_manifest)
    assert _source_sidecar_bytes(book) == source_before
    assert not stage5_runs.paths.stage5_prepared_recovery_manifest_path(
        book, 'target-run-3').exists()
    assert not stage5_runs.paths.stage5_chapter_recovery_journal_path(
        book, 'target-run-3', 2).exists()


def test_full_book_foreign_guard_reports_the_explicit_emit_and_recover_path(tmp_path):
    book = tmp_path / 'prepared首wire闸_2007780338055790990'
    _write_source_commit(book)
    ctx = _recovery_ctx(book, TARGET_RUN_ID)

    with pytest.raises(ProvenanceRefused, match='emit-prepared-recovery-workpack') as caught:
        stage5_runs.refuse_foreign_prepared_before_wire(ctx, None)
    assert '--recover-prepared' in str(caught.value)

    # 定向写别章不应被兄弟章 reservation 误拦；写到该章才必须停。
    stage5_runs.refuse_foreign_prepared_before_wire(ctx, '1')
    with pytest.raises(ProvenanceRefused, match='禁止新 driver/wire'):
        stage5_runs.refuse_foreign_prepared_before_wire(ctx, '2')


def test_committed_recovery_lineage_survives_retain_recheck_and_archived_rewrite(
        tmp_path, monkeypatch):
    """真实走 recovery → 原位 recheck → rewrite；历史只需保留 typed adoption。"""
    from novel import store as store_module

    book = tmp_path / 'prepared后继改写_2007780338055790990'
    book.mkdir()
    recovery_ctx = _recovery_store_ctx(book, TARGET_RUN_ID)
    chapter_row = PreparedRecoveryChapter.model_validate(_chapter_payload())
    _manifest, action = _manifest_for_chapter(recovery_ctx, chapter_row)
    adoption = _adoption_for_chapter(chapter_row, action)
    recovered = _typed_chapter(
        text='恢复后的正文', round_no=1, adoption=adoption,
        root_clean_sha1=recovery_ctx.root_clean_sha1)
    journal = _committed_journal_for(chapter_row, action, recovered)
    journal_path = stage5_runs.paths.stage5_chapter_recovery_journal_path(
        book, TARGET_RUN_ID, 2)
    agent_runs._atomic_json(journal_path, journal.model_dump(mode='json'))
    source_record = {
        'state': 'prepared',
        'commit_sha256': journal.source_commit_sha256,
        'attempt_id': journal.source_attempt_id,
    }
    # 本例只测 store 的正式/refresh/轮次字节语义；freshness 判据另有专测。
    monkeypatch.setattr(
        store_module.ArtifactStore, '_assert_self_fresh',
        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        store_module.ArtifactStore, '_refuse_stale_upstream',
        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        stage5_runs, '_source_sidecars_match_journal',
        lambda *_args, **_kwargs: True)

    store = store_module.ArtifactStore(recovery_ctx)
    store.write(journal.primary_artifact, recovered)
    later_ctx = _recovery_store_ctx(book, 'later-paid-run')

    # retain：live 仍是当初 exact recovery；后继 guard 不再要求旧 post 仍 live。
    assert stage5_runs._committed_recovery_closes_live(
        later_ctx, journal, source_record)
    assert not stage5_runs._committed_recovery_closes_live_now(
        later_ctx, journal, source_record)

    # recheck：v3 不再写派生副本，原恢复主体原样保留，也不制造历史轮次。
    rechecked = recovered.model_copy(deep=True)
    before_recheck = store.path(journal.primary_artifact).read_bytes()
    store.refresh_formal(journal.primary_artifact, rechecked)
    assert store.path(journal.primary_artifact).read_bytes() == before_recheck
    rounds_dir = store.path('stage5/_rounds')
    assert not rounds_dir.exists() or not list(rounds_dir.glob('ch02.r*.json'))
    assert agent_runs.canonical_sha256(
        rechecked.model_dump(by_alias=True, mode='json')) \
        == journal.primary_payload_sha256
    assert stage5_runs._committed_recovery_closes_live(
        later_ctx, journal, source_record)

    # rewrite：归档原样保留的 recovery bytes，typed adoption 仍须闭合。
    rewritten = _typed_chapter(
        text='后续合法人工改写', round_no=2, adoption=None,
        root_clean_sha1=recovery_ctx.root_clean_sha1)
    store.write_round(journal.primary_artifact, rewritten)
    archived_path = rounds_dir / 'ch02.r1.json'
    archived, _raw = store.read_own(
        Chapter, 'stage5/_rounds/ch02.r1.json', require_current=False)
    assert archived.provenance.prepared_checkpoint_adoption == adoption
    assert agent_runs.canonical_sha256(
        archived.model_dump(by_alias=True, mode='json')) \
        == journal.primary_payload_sha256
    assert stage5_runs._committed_recovery_closes_live(
        later_ctx, journal, source_record)

    archived_path.unlink()
    assert not stage5_runs._committed_recovery_closes_live(
        later_ctx, journal, source_record)


@pytest.mark.parametrize('damage', ['missing', 'self_hash', 'action'])
def test_committed_recovery_requires_exact_target_manifest_for_now_and_later(
        tmp_path, monkeypatch, damage):
    """提交当下与后继 supersession 都必须重读同一份完整 target manifest。"""
    from novel import store as store_module

    book = tmp_path / f'prepared_manifest_{damage}_2007780338055790990'
    book.mkdir()
    ctx = _recovery_store_ctx(book, TARGET_RUN_ID)
    chapter_row = PreparedRecoveryChapter.model_validate(_chapter_payload())
    manifest, action = _manifest_for_chapter(ctx, chapter_row)
    adoption = _adoption_for_chapter(chapter_row, action)
    recovered = _typed_chapter(
        text='带完整恢复来源的正文', round_no=1, adoption=adoption,
        root_clean_sha1=ctx.root_clean_sha1)

    monkeypatch.setattr(
        store_module.ArtifactStore, '_assert_self_fresh',
        lambda *_args, **_kwargs: None)
    store = store_module.ArtifactStore(ctx)
    store.write('stage5/ch02.json', recovered)
    journal = _committed_journal_for(chapter_row, action, recovered)
    journal_raw = journal.model_dump(mode='json')
    journal_raw['post_file_sha256s'] = {
        rel: agent_runs.file_sha256(ctx, rel)
        for rel in journal.touched_paths
    }
    journal_raw['journal_sha256'] = stage5_runs._journal_sha256(journal_raw)
    journal = PreparedRecoveryJournal.model_validate(journal_raw)
    source_record = {
        'state': 'prepared',
        'commit_sha256': journal.source_commit_sha256,
        'attempt_id': journal.source_attempt_id,
    }
    monkeypatch.setattr(
        stage5_runs, '_source_sidecars_match_journal',
        lambda *_args, **_kwargs: True)

    assert stage5_runs._committed_recovery_closes_live_now(
        ctx, journal, source_record)
    assert stage5_runs._committed_recovery_closes_live(
        ctx, journal, source_record)

    manifest_path = stage5_runs.paths.stage5_prepared_recovery_manifest_path(
        book, TARGET_RUN_ID)
    if damage == 'missing':
        manifest_path.unlink()
    else:
        damaged = manifest.model_dump(mode='json')
        if damage == 'self_hash':
            damaged['manifest_sha256'] = OTHER_SHA
        else:
            damaged['action']['reason'] = '被篡改但重新闭合 outer SHA 的理由'
            damaged['manifest_sha256'] = stage5_runs.manifest_sha256(damaged)
        agent_runs._atomic_json(manifest_path, damaged)

    if damage == 'action':
        assert not stage5_runs._committed_recovery_closes_live_now(
            ctx, journal, source_record)
        assert not stage5_runs._committed_recovery_closes_live(
            ctx, journal, source_record)
    else:
        expected = ProvenanceRefused if damage == 'missing' else ArtifactUnreadable
        with pytest.raises(expected):
            stage5_runs._committed_recovery_closes_live_now(
                ctx, journal, source_record)
        with pytest.raises(expected):
            stage5_runs._committed_recovery_closes_live(
                ctx, journal, source_record)


def test_orphan_global_audit_preclaim_refuses_before_any_new_target_write(tmp_path):
    """旧 target journal 丢了也不能重放；append-only audit 在第一笔写盘前占位。"""
    base = tmp_path / 'output'
    book = base / 'prepared孤儿审计_2007780338055790990'
    book.mkdir(parents=True)
    chapter = PreparedRecoveryChapter.model_validate(_chapter_payload())

    old_ctx = _recovery_ctx(book, TARGET_RUN_ID)
    _old_manifest, old_action = _manifest_for_chapter(
        old_ctx, chapter, actor='operator-old', reason='旧 target 已采用',
        write=False)
    audit_path = ledger_path(base).with_name('llm_audit.jsonl')
    old_ledger = CostLedger(path=ledger_path(base), run_id=TARGET_RUN_ID)
    _note_recovery_audit(old_ledger, chapter, old_action)
    audit_before = audit_path.read_bytes()
    assert not stage5_runs.paths.stage5_chapter_recovery_journal_path(
        book, TARGET_RUN_ID, 2).exists(), '本例刻意模拟旧 target journal 丢失'

    new_ctx = _recovery_ctx(book, 'target-run-3')
    new_ctx.ledger = CostLedger(path=ledger_path(base), run_id='target-run-3')
    new_manifest, new_action = _manifest_for_chapter(
        new_ctx, chapter, actor='operator-new', reason='试图重放孤儿审计',
        write=False)
    new_manifest_path = stage5_runs.paths.stage5_prepared_recovery_manifest_path(
        book, 'target-run-3')
    new_journal_path = stage5_runs.paths.stage5_chapter_recovery_journal_path(
        book, 'target-run-3', 2)
    canonical = book / 'stage5' / 'ch02.json'

    with pytest.raises(ProvenanceRefused, match='任何 manifest/journal/live 写盘前重放'):
        stage5_runs.write_recovery_manifest(new_ctx, new_manifest)
    assert not new_manifest_path.exists()
    assert not new_journal_path.exists()
    assert not canonical.exists()
    assert audit_path.read_bytes() == audit_before

    # 追加口本身仍是 0-or-exact；同 adoption id 的另一 target 整行不能冒充幂等。
    with pytest.raises(ProvenanceRefused):
        _note_recovery_audit(new_ctx.ledger, chapter, new_action)
    assert audit_path.read_bytes() == audit_before


def test_foreign_guard_refuses_committed_supersession_after_audit_is_deleted(
        tmp_path, monkeypatch):
    """live canonical/journal 看似闭合也不能代替全局 adoption audit。"""
    from novel import store as store_module

    book = tmp_path / 'prepared审计缺口_2007780338055790990'
    source_path, source_commit = _write_source_commit(book)
    ctx = _recovery_ctx(book, TARGET_RUN_ID)
    primary = {'chapter': 2, 'content': 'typed recovered canonical'}
    chapter = _recovery_chapter(ctx, source_path, source_commit)
    _manifest, action = _manifest_for_chapter(ctx, chapter)
    journal = _committed_journal_for(chapter, action, primary)
    journal_path = stage5_runs.paths.stage5_chapter_recovery_journal_path(
        book, TARGET_RUN_ID, 2)
    agent_runs._atomic_json(journal_path, journal.model_dump(mode='json'))

    adoption = _adoption_for(journal)

    class Artifact:
        provenance = SimpleNamespace(prepared_checkpoint_adoption=adoption)

        def model_dump(self, **_kwargs):
            return dict(primary)

    monkeypatch.setattr(
        stage5_runs, '_source_sidecars_match_journal',
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        agent_runs,
        'file_sha256',
        lambda _ctx, rel: journal.post_file_sha256s[rel],
    )
    monkeypatch.setattr(
        store_module,
        'ArtifactStore',
        lambda _ctx: SimpleNamespace(
            exists=lambda _rel: True,
            read_own=lambda *_args, **_kwargs: (Artifact(), {})),
    )

    with stage5_runs.commit_ownership(book, 2):
        stage5_runs.refuse_foreign_commit_reservations(ctx, 2)
    assert ctx.ledger.lineage_required, 'supersession 必须核对全局 adoption audit'

    # 模拟 append-only audit 行从全局账消失；其余 journal/live/source 全不变。
    ctx.ledger.lineage_available = False
    with stage5_runs.commit_ownership(book, 2):
        with pytest.raises(ProvenanceRefused, match='lineage/live artifact 不闭合'):
            stage5_runs.refuse_foreign_commit_reservations(ctx, 2)


def test_default_paid_stage5_refuses_foreign_prepared_before_client(
        monkeypatch):
    """guard 若放在 ``run_write`` 内就已经太晚：CLI 此前会先构造 client/driver。"""
    from novel.cli import main as cli_main
    from novel.llm import stage5_runs
    from novel.stages.stage5 import write

    ctx = SimpleNamespace(node='stage5.write')
    calls: list[tuple[object, str | None]] = []

    def refuse(got, chapters):
        calls.append((got, chapters))
        raise ProvenanceRefused('foreign prepared；请先 emit/recover')

    monkeypatch.setattr(stage5_runs, 'refuse_foreign_prepared_before_wire', refuse)
    monkeypatch.setattr(
        cli_main, '_llm_client',
        lambda _ctx: pytest.fail('foreign prepared 必须在 client/driver 前拒绝'),
    )
    monkeypatch.setattr(
        write, 'run_write',
        lambda *_args, **_kwargs: pytest.fail('guard 拒绝后不得进入 producer'),
    )

    with pytest.raises(ProvenanceRefused, match='emit/recover'):
        cli_main._run_stage5(ctx, [])
    assert calls == [(ctx, None)]


def test_explicit_recovery_cli_is_zero_wire_and_forwards_hash_bound_action(
        monkeypatch):
    from novel.cli import main as cli_main
    from novel.stages.stage5 import write

    ctx = SimpleNamespace(node='stage5.write')
    captured: list[tuple[object, str, str, str, str]] = []

    def recover(got, source_run_id, *, actor, reason, subject_sha256):
        captured.append((
            got, source_run_id, actor, reason, subject_sha256))
        return SimpleNamespace(passed=True)

    monkeypatch.setattr(write, 'run_prepared_recovery', recover)
    monkeypatch.setattr(cli_main, 'render', lambda _report: 'synthetic report')
    monkeypatch.setattr(
        cli_main, '_llm_client',
        lambda _ctx: pytest.fail('--recover-prepared 不得构造 LLM client'),
    )

    assert cli_main._run_stage5(ctx, _recover_flags()) == 0
    assert captured == [(
        ctx, SOURCE_RUN_ID, 'operator',
        '逐项核对工作包后按当前闸门收口', SHA,
    )]
