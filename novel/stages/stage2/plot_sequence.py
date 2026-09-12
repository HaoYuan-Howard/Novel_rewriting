"""按持续状态依赖推进章节；每章仍只有一个逻辑请求和独立 session。

后章不能引用模型从未见过的前章 State ID。并发额度仍由现有执行器控制，
同书有依赖的章节按阅读序解锁；不增加额外抽取/聚合模型工位。
"""
from __future__ import annotations

import json
from typing import Any

from contracts.schema.plot import PlotBlueprintResponse
from novel import paths
from novel.errors import ProvenanceRefused
from novel.observability import ChapterProgressTracker
from novel.stages.stage2.plot_compile import compile_chapter


def with_entry_states(user: str, states: list[dict[str, Any]]) -> str:
    metadata, source = user.split('\n', 1)
    payload = json.loads(metadata)
    payload['entry_states'] = states
    return json.dumps(payload, ensure_ascii=False, separators=(',', ':')) + '\n' + source


def request_compiler(user: str, source: str):
    """首次生成与同请求 Agent 返修共用冻结的可引用主体和证据边界。"""
    metadata = json.loads(user.split('\n', 1)[0])
    ordinal = metadata['chapter_ordinal']
    characters = frozenset(metadata['character_refs'])
    states = frozenset(row['state']['state_id'] for row in metadata['entry_states'])
    subjects = frozenset((row['kind'], row['ref']) for row in metadata['global_subjects'])

    def compile_current(response):
        if response.chapter_ordinal != ordinal:
            raise ValueError(f'本轮必须只返回阅读序第 {ordinal} 章')
        return compile_chapter(response, source, allowed_char_ids=characters,
            allowed_entry_state_refs=states, allowed_global_refs=subjects)

    return compile_current


def request_validator(user: str, source: str):
    compile_current = request_compiler(user, source)

    def validate(response, _wire):
        if len(response.blueprints) != 1:
            return '每次请求必须只返回一个完整章节'
        try:
            compile_current(response.blueprints[0])
        except (TypeError, ValueError) as exc:
            return str(exc)
        return None

    return validate


def execute(ctx, client, requests, *, main_text, character_inventory,
            global_projections, checkpoints: bool, restore_checkpoints: bool):
    from novel.stages.stage2.extract import _run_chapter_requests

    sources = {row['chapter_ordinal']: row['text'] for row in main_text}
    ordinals = [ordinal for ordinal, _system, _user in requests]
    if ordinals != sorted(sources):
        raise ProvenanceRefused('完整剧情首次抽取必须覆盖全部源章；定向修订使用已有蓝图的编辑/返修入口')
    if restore_checkpoints:
        gap = False
        for ordinal in ordinals:
            exists = paths.stage2_blueprint_checkpoint_path(
                ctx.novel_dir, ctx.run_id, 'blueprint', ordinal).exists()
            if exists and gap:
                raise ProvenanceRefused('持续状态依赖链中间缺检查点，后章来源无法核验；不能先购买缺章')
            gap = gap or not exists
    raw, violations, errors, actual_requests = [], [], [], []
    tracker = ChapterProgressTracker(ctx.observe, ordinals)
    states: dict[str, dict] = {}
    carried: frozenset[str] = frozenset()
    for ordinal, system, seed_user in requests:
        entry = [states[ref] for ref in sorted(carried)]
        user = with_entry_states(seed_user, entry)
        actual_requests.append((ordinal, system, user))

        compile_current = request_compiler(user, sources[ordinal])
        validate = request_validator(user, sources[ordinal])

        rows, problems, failures = _run_chapter_requests(ctx, client,
            [(ordinal, system, user)], model_cls=PlotBlueprintResponse,
            validator_of=lambda _ordinal: validate, phase='blueprint',
            architecture='plot-blueprint-v1', checkpoints=checkpoints,
            restore_checkpoints=restore_checkpoints, progress_tracker=tracker)
        raw.extend(rows)
        violations.extend(problems)
        errors.extend(failures)
        if failures or not rows:
            break
        chapter = compile_current(PlotBlueprintResponse.model_validate({'blueprints': rows}).blueprints[0])
        anchors = {row.evidence_id: row.model_dump(mode='json') for row in chapter.evidence_anchors}
        for state in chapter.states:
            states[state.state_id] = {
                'chapter_ordinal': ordinal, 'state': state.model_dump(mode='json'),
                'source_sha256': chapter.source_sha256,
                'evidence_anchors': [anchors[ref] for ref in state.evidence_refs],
            }
        carried = frozenset(chapter.carry_out_refs)
        states = {ref: states[ref] for ref in carried}
    return raw, violations, errors, actual_requests
