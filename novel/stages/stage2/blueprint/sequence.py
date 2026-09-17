"""按章并发抽取；跨章持续状态由 Continuity Pass（compile 期）缝合。

首次抽取不再把前章 carry_out 注入后章 prompt。各章独立 session、空
entry_states，在既有并发上限内并行购买；正式 entry/carry/去重由
``plot_continuity.prepare_chapter_for_continuity`` + ``compile_plot_response``
按阅读序确定性落地。定向修订（plot_repair）仍可串行注入 entry 材料。
"""
from __future__ import annotations

import json
from typing import Any

from contracts.schema.plot import PlotBlueprintResponse
from novel.errors import ProvenanceRefused
from novel.observability import ChapterProgressTracker
from novel.stages.stage2.blueprint.compile import compile_chapter


def with_entry_states(user: str, states: list[dict[str, Any]]) -> str:
    """测试 / 定向修订仍可显式注入 entry_states；首次并发抽取保持空列表。"""
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
            allowed_entry_state_refs=states, allowed_global_refs=subjects,
            character_bindings=metadata['character_bindings'],
            allowed_actor_bindings={entry['binding']['presentation_id']: entry['binding']
                for row in metadata['entry_states'] for entry in row['actor_bindings']},
            global_subjects={(row['kind'], row['ref']): row for row in metadata['global_subjects']})

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
    """全书章请求一次提交给现有章并发池；Continuity 不在此串行等待。"""
    from novel.stages.stage2.extract import _run_chapter_requests

    sources = {row['chapter_ordinal']: row['text'] for row in main_text}
    ordinals = [ordinal for ordinal, _system, _user in requests]
    if ordinals != sorted(sources):
        raise ProvenanceRefused('完整剧情首次抽取必须覆盖全部源章；定向修订使用已有蓝图的编辑/返修入口')
    # 并行抽取不再依赖前缀 checkpoint 才能构造后章 prompt；缺章只影响整书提交，
    # 由 finish / Continuity 闭合，不在此因「中间洞」拒恢复已验章。
    actual_requests = list(requests)
    validators = {
        ordinal: request_validator(user, sources[ordinal])
        for ordinal, _system, user in actual_requests
    }
    tracker = ChapterProgressTracker(ctx.observe, ordinals)
    rows, problems, failures = _run_chapter_requests(
        ctx, client, actual_requests, model_cls=PlotBlueprintResponse,
        validator_of=lambda ordinal: validators[ordinal], phase='blueprint',
        architecture='plot-blueprint-v1', checkpoints=checkpoints,
        restore_checkpoints=restore_checkpoints, progress_tracker=tracker)
    return rows, problems, failures, actual_requests
