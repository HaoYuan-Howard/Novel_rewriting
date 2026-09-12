"""生成检查点与正式范围审批的轻投影；不构建 prompt、证据包或 Commander 工作包。"""
from __future__ import annotations

from contracts.artifacts import STAGE1_MAIN_TEXT, STAGE2_CHAPTER_BLUEPRINTS
from contracts.schema.plot_review import (
    BlueprintChapterProgress, BlueprintRunProgress, PlotReviewProgress,
)
from contracts.schema.stage1 import MainText
from novel import paths
from novel.errors import TerminalState
from novel.orchestrator import approvals, state
from novel.store import ArtifactStore


def get(base: str, sid: str, units: list[dict] | None) -> BlueprintRunProgress:
    from novel.llm.stage2_runs import plot_checkpoint_progress
    from novel.stages.stage2.plot_inputs import read_input_digests

    missing = BlueprintRunProgress(artifact_state='unreadable', chapters=[])
    try:
        ctx = state.readonly_ctx(paths.resolve_novel_dir(base, sid),
                                 'stage2.blueprint', approvals._projection_code())
        store = ArtifactStore(ctx)
        main = store.read(MainText, STAGE1_MAIN_TEXT)
        ordinals = [row.chapter_ordinal for row in main.root]
        artifact_state = ('invalid' if store.exists(paths.invalid_of(STAGE2_CHAPTER_BLUEPRINTS))
                          else 'formal' if store.exists(STAGE2_CHAPTER_BLUEPRINTS) else 'missing')
        progress = next((row['review']['plot_progress'] for row in units or []
                         if row.get('review') and row['review'].get('plot_progress')), None)
        review = PlotReviewProgress.model_validate(progress) if progress else None
        input_reason = None
        try:
            inputs = read_input_digests(store)
        except (TerminalState, OSError, ValueError) as exc:
            inputs, input_reason = {}, str(exc)
        chapters = []
        for ordinal in ordinals:
            unit = next((row for row in units or [] if row['node'] == 'stage2.blueprint'
                         and row['role'] == 'executor' and row['scope'] == f'chapter:{ordinal}'), None)
            processing = unit.get('processing') if unit else None
            row = dict(chapter_ordinal=ordinal, checkpoint_state='missing')
            if units is None:
                row.update(checkpoint_state='unver', reason='工作单元持久账不可读取')
            elif processing:
                row['run_id'] = processing['run_id']
                row['processing_state'] = next((key for key in (
                    'pending', 'generated', 'mechanical', 'history_adopted',
                    'cached', 'failed', 'unknown') if processing[key]), None)
                if str(ordinal) in inputs:
                    checkpoint = plot_checkpoint_progress(ctx, chapter=ordinal,
                        run_id=processing['run_id'], input_sha256=inputs[str(ordinal)])
                    row.update(checkpoint_state=checkpoint['state'], reason=checkpoint['reason'])
                else:
                    row.update(checkpoint_state='unver', reason=input_reason)
            chapters.append(BlueprintChapterProgress.model_validate(row))
        checkpoint_count = sum(row.checkpoint_state == 'current' for row in chapters)
        generation_known = units is not None and all(row.checkpoint_state != 'unver' for row in chapters)
        generated = sum(row.checkpoint_state == 'current' or (
            row.checkpoint_state == 'missing' and row.processing_state == 'generated') for row in chapters)
        reason = input_reason
        if artifact_state != 'missing' and review is None:
            reason = next((row.get('review_unavailable_reason') for row in units or []
                           if row['node'] == 'stage2.blueprint' and row.get('review_unavailable_reason')),
                          reason or '当前蓝图或审批身份无法核验')
            artifact_state = 'unreadable'
        return BlueprintRunProgress(
            total_chapters=len(ordinals), artifact_state=artifact_state, review=review,
            generated_chapters=(review.generated_chapters if review else generated if generation_known else None),
            checkpoint_chapters=checkpoint_count if generation_known else None,
            submitted_chapters=review.generated_chapters if review else 0 if artifact_state == 'missing' else None,
            chapters=chapters, unavailable_reason=reason)
    except (TerminalState, OSError, ValueError) as exc:
        return missing.model_copy(update={'unavailable_reason': str(exc)})
