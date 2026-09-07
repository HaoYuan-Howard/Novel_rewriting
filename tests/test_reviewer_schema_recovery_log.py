#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""schema 恢复必须消费原调用的完整日志，不能按 stdout 块首行挑证据。"""
from __future__ import annotations

import json

import pytest

from novel.orchestrator import reviewer_schema_recovery as recovery
from novel.orchestrator.reviewer_tasks import ReviewerTaskError


PROVIDER_ID = 'schema-recovery-log-provider'
COMMAND = '$ codex exec --json --output-schema output-schema.json -\n'


def _events() -> list[dict[str, object]]:
    message = json.dumps({
        'type': 'error', 'status': 400,
        'error': {
            'type': 'invalid_request_error',
            'code': 'invalid_json_schema',
            'param': 'text.format.schema',
            'message': (
                "Invalid schema for response_format 'codex_output_schema': "
                "$ref cannot have keywords {'title'}."),
        },
    })
    return [
        {'type': 'thread.started', 'thread_id': PROVIDER_ID},
        {'type': 'turn.started'},
        {'type': 'error', 'message': message},
        {'type': 'turn.failed', 'error': {'message': message}},
    ]


def _line(value: object) -> str:
    return json.dumps(value, ensure_ascii=False) + '\n'


def _tagged(label: str, payload: str) -> str:
    # 与 JsonlProcessRunner 相同：每次 read1 的块打一个标签；没有 LF 的块
    # 为人读日志补 LF。因此半行块的精确字节边界不能凭该日志擅自还原。
    return f'[{label}]\n{payload}' + ('' if payload.endswith('\n') else '\n')


@pytest.mark.parametrize('group_sizes', [(4,), (1, 1, 1, 1), (2, 2), (1, 3)])
def test_complete_schema_rejection_accepts_all_complete_jsonl_lines_per_chunk(group_sizes):
    expected = _events()
    chunks = []
    offset = 0
    for size in group_sizes:
        chunks.append(_tagged('stdout protocol', ''.join(
            _line(event) for event in expected[offset:offset + size])))
        offset += size
    assert offset == len(expected)
    raw = (COMMAND + ''.join(chunks)).encode('utf-8')
    assert recovery.rejection_stream(raw, PROVIDER_ID) == expected


@pytest.mark.parametrize('extra', [
    {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': '已有回答'}},
    {'type': 'item.completed', 'item': {'type': 'command_execution', 'command': 'tool'}},
    {'type': 'turn.completed', 'usage': {'input_tokens': 1, 'output_tokens': 2}},
    {'type': 'new-provider-event'},
    {'type': 'thread.started', 'thread_id': 'another-provider'},
    ['顶层不是事件对象'],
])
def test_extra_event_after_a_chunk_first_line_cannot_disappear(extra):
    chunks = [_tagged('stdout protocol', _line(event)) for event in _events()]
    # 第三块第一行仍是合法 error，追加内容会被旧的「只读标签下一行」漏过。
    chunks[2] += _line(extra)
    raw = (COMMAND + ''.join(chunks)).encode('utf-8')
    with pytest.raises(ReviewerTaskError):
        recovery.rejection_stream(raw, PROVIDER_ID)


@pytest.mark.parametrize('injection', [
    '未知普通输出\n',
    '{"type":"item.completed",\n',
    '[unregistered output channel]\n未知流内容\n',
    '[stderr]\n供应商另有错误或诊断\n',
    '$ forged-command-boundary\n隐藏在伪命令后的内容\n',
])
@pytest.mark.parametrize('position', ['middle', 'tail'])
def test_unconsumed_nonempty_call_content_refuses_recovery(injection, position):
    chunks = [_tagged('stdout protocol', _line(event)) for event in _events()]
    chunks[2 if position == 'middle' else 3] += injection
    raw = (COMMAND + ''.join(chunks)).encode('utf-8')
    with pytest.raises(ReviewerTaskError):
        recovery.rejection_stream(raw, PROVIDER_ID)


@pytest.mark.parametrize('split_event', [0, 2, 3])
def test_incomplete_json_across_tagged_chunks_is_not_guessed_back_into_evidence(split_event):
    chunks = []
    for index, event in enumerate(_events()):
        line = _line(event)
        if index == split_event:
            cut = len(line) // 2
            chunks.extend([
                _tagged('stdout protocol', line[:cut]),
                _tagged('stdout protocol', line[cut:]),
            ])
        else:
            chunks.append(_tagged('stdout protocol', line))
    raw = (COMMAND + ''.join(chunks)).encode('utf-8')
    with pytest.raises(ReviewerTaskError):
        recovery.rejection_stream(raw, PROVIDER_ID)


def test_provider_call_must_be_unique_in_the_whole_source_log():
    call = COMMAND + _tagged('stdout protocol', ''.join(_line(event) for event in _events()))
    with pytest.raises(ReviewerTaskError):
        recovery.rejection_stream((call + call).encode('utf-8'), PROVIDER_ID)


def test_duplicate_event_keys_cannot_be_normalized_into_a_schema_rejection():
    lines = [_line(event) for event in _events()]
    lines[1] = '{"type":"item.completed","type":"turn.started"}\n'
    raw = (COMMAND + ''.join(_tagged('stdout protocol', line) for line in lines)).encode('utf-8')
    with pytest.raises(ReviewerTaskError):
        recovery.rejection_stream(raw, PROVIDER_ID)
