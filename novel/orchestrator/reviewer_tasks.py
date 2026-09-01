#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Commander reviewer 的闭集任务与单动作 typed 输出。

这里不调模型，也不执行流水线节点。它只在 Supervisor 已经取得一份
``OperatorNext`` 后，生成该 blocker 的 immutable workpack，并严格读取 Commander
写下的一份外部 typed request。表外 blocker 返回 ``None``，调用方必须停下，不能把
一个通用 Commander 重新扩成第二套编排器。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import TypeAdapter, ValidationError

from contracts.invariants import (
    DESIGN_CHAPTER_DECISIONS,
    DESIGN_REPAIR_CODES,
    DESIGN_REPAIR_CONSTRAINT_ARITY,
    DESIGN_STRATEGY_DECISIONS,
    DESIGN_STRATEGY_REPLAN_CONSTRAINTS,
    STAGE5_CLOSURE_GROUPS,
    STAGE5_MUST_APPEAR_RULINGS,
    STRUCTURE_APPROVAL_DECISIONS,
)
from contracts.schema.api import (
    BlueprintSeenRequest,
    DesignReviewRequest,
    DesignReviewTemplate,
    OperatorNext,
    Stage5ClosureRequest,
    Stage5ClosureTemplate,
    StructureApprovalRequest,
    WritingApprovalRequest,
)
from contracts.schema.evaluation import GlobalReviewDecisionSet
from novel import paths
from novel.orchestrator import queue


REQUEST_SCHEMA = 'novel-reviewer-action-v1'
_ACTOR = 'agent:novel-pipeline-operator'
# 这是 prompt 内的不可提交 sentinel。简单审批（尤其 writing）没有业务
# decision 枚举可替它关闭式兜底，因此 loader 也必须拒绝原样复制。
_REASON = '<逐项审阅并跨项复核后填写精确理由>'
_JSON_OBJECT = TypeAdapter(dict[str, Any])


class ReviewerTaskError(RuntimeError):
    """工作包或 reviewer typed request 无法关闭式证明。"""


@dataclass(frozen=True)
class ReviewerTask:
    waiting_on: str
    surface_seal: str
    request_path: Path
    prompt: str


@dataclass(frozen=True)
class ReviewerNodeAction:
    kind: Literal['node']
    node: str
    flags: tuple[str, ...]
    input_payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class ReviewerApprovalAction:
    kind: Literal['approval']
    surface: Literal['design', 'structure', 'writing', 'stage5', 'blueprint_seen']
    payload: dict[str, Any]


ReviewerAction = ReviewerNodeAction | ReviewerApprovalAction


_NODE_FOR_BLOCKER: dict[str, frozenset[str]] = {
    'stage0.approve': frozenset({'stage0.approve'}),
    queue.WAITING_ON_STAGE2_REVIEW: frozenset({'stage2.global'}),
    'stage6.approve': frozenset({'stage6.approve'}),
    'stage7.approve': frozenset({'stage7.approve'}),
}

_APPROVAL_FOR_BLOCKER: dict[str, frozenset[str]] = {
    queue.WAITING_ON_STAGE2_REVIEW: frozenset({'blueprint_seen'}),
    queue.WAITING_ON_STAGE4_DESIGN_REVIEW: frozenset({'design'}),
    queue.WAITING_ON_STAGE4_STRUCTURE_REVIEW: frozenset({'structure'}),
    queue.WAITING_ON_WRITING_APPROVAL: frozenset({'writing'}),
    queue.WAITING_ON_STAGE5_CLOSURE: frozenset({'stage5'}),
}

_REGISTERED = frozenset(_NODE_FOR_BLOCKER) | frozenset(_APPROVAL_FOR_BLOCKER)


def is_registered(waiting_on: str) -> bool:
    return waiting_on in _REGISTERED


def _json_command(argv: list[str]) -> Any:
    """运行一条只读工作包生产命令；stderr/坏 JSON 都不能进模型输入。"""
    completed = subprocess.run(
        argv, cwd=str(Path(__file__).resolve().parents[2]),
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding='utf-8',
        errors='replace', check=False, shell=False, timeout=120)
    if completed.returncode != 0:
        detail = completed.stderr.strip().splitlines()[-1:] or ['无 stderr']
        raise ReviewerTaskError(
            f'只读 reviewer workpack 命令 exit {completed.returncode}：{detail[0]}')
    try:
        return _JSON_OBJECT.validate_json(completed.stdout)
    except ValidationError as exc:
        raise ReviewerTaskError('只读 reviewer workpack 不是合法 JSON') from exc


def _approval_view(cli: list[str], base: str, sid: str, key: str) -> Any:
    """只取 approval show 中当前 surface，不把其他审批及历史塞进 prompt。"""
    shown = _json_command([
        *cli, 'approval', 'show', sid, '--base', base])
    if not isinstance(shown, dict) or key not in shown:
        raise ReviewerTaskError(f'approval show 缺少 {key} 视图')
    value = shown[key]
    if not isinstance(value, dict):
        raise ReviewerTaskError(f'approval show.{key} 必须是 object')
    # history 是审计面，不是当前 Commander 的阅读面。
    return {name: item for name, item in value.items()
            if name not in {'history', 'current_decision'}}


def _workpack(
    base: str,
    sid: str,
    waiting_on: str,
    *,
    review_run_id: str,
) -> dict[str, Any]:
    py = sys.executable
    root = Path(__file__).resolve().parents[2]
    cli = [py, '-B', '-m', 'novel.cli']
    if waiting_on == 'stage0.approve':
        script = (
            root / '.workflow' / 'skills' / 'novel-pipeline-operator'
            / 'scripts' / 'stage0_review_workpack.py')
        return {'stage0': _json_command([
            py, '-B', str(script), '--base', base, '--sid', sid])}
    if waiting_on == queue.WAITING_ON_STAGE2_REVIEW:
        return {
            'global': _json_command([
                *cli, 'run', 'stage2.global', sid, '--base', base,
                '--emit-review-workpack', '--review-scope', 'blocking']),
            'global_template': _json_command([
                *cli, 'run', 'stage2.global', sid, '--base', base,
                '--emit-review-template', '--review-scope', 'blocking']),
            'blueprint': _json_command([
                *cli, 'run', 'stage2.blueprint', sid, '--base', base,
                '--emit-review-workpack']),
        }
    if waiting_on == queue.WAITING_ON_STAGE4_DESIGN_REVIEW:
        return {
            'design': _json_command([
                *cli, 'approval', 'workpack', 'design', sid, '--base', base]),
            # 只供确定性封装层投影 compact POST；不再进 prompt
            # 重复 workpack 已有的整本 subject。
            '_design_template': _json_command([
                *cli, 'approval', 'template', 'design', sid, '--base', base,
                '--actor', _ACTOR, '--reason', _REASON,
                '--reviewer-kind', 'delegated_operator',
                '--review-run-id', review_run_id,
            ]),
        }
    if waiting_on == queue.WAITING_ON_STAGE4_STRUCTURE_REVIEW:
        return {
            'structure': _json_command([
                *cli, 'approval', 'workpack', 'structure', sid, '--base', base]),
            '_structure_template': _json_command([
                *cli, 'approval', 'template', 'structure', sid, '--base', base,
                '--actor', _ACTOR, '--reason', _REASON,
            ]),
        }
    if waiting_on == queue.WAITING_ON_WRITING_APPROVAL:
        return {
            'writing': _approval_view(cli, base, sid, 'writing_approval'),
            '_writing_template': _json_command([
                *cli, 'approval', 'template', 'writing', sid, '--base', base,
                '--actor', _ACTOR, '--reason', _REASON]),
        }
    if waiting_on == queue.WAITING_ON_STAGE5_CLOSURE:
        return {
            'stage5': _approval_view(cli, base, sid, 'closure'),
            '_stage5_template': _json_command([
                *cli, 'approval', 'template', 'stage5', sid, '--base', base,
                '--actor', _ACTOR, '--reason', _REASON]),
        }
    if waiting_on == 'stage6.approve':
        return {'stage6': _json_command([
            *cli, 'approval', 'workpack', 'stage6', sid, '--base', base])}
    if waiting_on == 'stage7.approve':
        return {'stage7': _json_command([
            *cli, 'approval', 'workpack', 'stage7', sid, '--base', base])}
    raise ReviewerTaskError(f'未登记 reviewer blocker：{waiting_on}')


def _request_wrapper(
    current: OperatorNext,
    waiting_on: str,
    action: dict[str, Any],
) -> dict[str, Any]:
    """产出 loader 真正接受的完整 wrapper，不再给混合伪 schema。"""
    return {
        'schema': REQUEST_SCHEMA,
        'surface_seal': current.surface_seal,
        'waiting_on': waiting_on,
        'action': action,
    }


def _option(
    option_id: str,
    request: dict[str, Any],
    *,
    editable_fields: list[str],
    binding_fields: list[str],
    allowed_values: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        'id': option_id,
        'exact_request': request,
        'editable_fields': editable_fields,
        'binding_fields': binding_fields,
        'allowed_values': allowed_values or {},
    }


def _compact_design_payload(template: Any) -> dict[str, Any]:
    """用外部 POST model 从生产 template 投影单 token 紧凑载荷。

    ``from_attributes`` 使目标 model 只读自己的字段：完整
    subject / preflight / 多枚 SHA 不会进 Commander 的输出脚手架；
    Pydantic 仍会当场抓住生产 template 与 POST 形状的漂移。
    """
    request = DesignReviewRequest.model_validate(template, from_attributes=True)
    payload = request.model_dump(mode='json')
    payload['actor'] = _ACTOR
    payload['general_reason'] = _REASON
    payload['strategy']['reason'] = _REASON
    for row in payload['chapters']:
        row['reason'] = _REASON
    return payload


def _compact_stage5_payload(template: Stage5ClosureTemplate) -> dict[str, Any]:
    """只投影 closure POST 的 token/targets/rulings，不复制 finding SHA。"""
    # 先用真正写入 model 铸出一份合法键集，再把人审值
    # 换成 loader 会拒绝的明示 sentinel。
    valid = Stage5ClosureRequest.model_validate({
        'actor': _ACTOR,
        'reason': '待审阅',
        'review_token': template.review_token,
        'must_appear': [{
            'chapter_ordinal': row.chapter_ordinal,
            'evidence_key': row.evidence_key,
            'ruling': row.ruling,
            'reason': row.reason,
        } for row in template.must_appear],
        'group_acks': [{
            'group': row.group,
            'acknowledged': True,
        } for row in template.group_acks],
    }).model_dump(mode='json')
    valid['reason'] = _REASON
    for row in valid['must_appear']:
        row['ruling'] = '<confirmed_present 或 accepted_exception>'
        row['reason'] = _REASON
    for row in valid['group_acks']:
        row['acknowledged'] = '<逐组阅读后改为 true>'
    return valid


def _visible_materials(pack: dict[str, Any]) -> dict[str, Any]:
    """下划线材料只用来铸 skeleton，不重复进模型阅读面。"""
    return {key: value for key, value in pack.items()
            if not key.startswith('_')}


def _submission_options(
    pack: dict[str, Any],
    current: OperatorNext,
    waiting_on: str,
) -> list[dict[str, Any]]:
    """把当前 blocker 收成一份（Stage2 为两步）可达 typed 请求。"""
    bind = ['schema', 'surface_seal', 'waiting_on', 'action.kind']
    if waiting_on == 'stage0.approve':
        flags = list(pack['stage0'].get('approval_flags') or [])
        if len(flags) != 6:
            raise ReviewerTaskError('Stage0 workpack approval_flags 形状漂移')
        flags = [
            _ACTOR if value == '<ACTOR>' else
            _REASON if value.startswith('<') and value.endswith('>') else value
            for value in flags
        ]
        request = _request_wrapper(current, waiting_on, {
            'kind': 'node', 'node': 'stage0.approve', 'flags': flags,
        })
        return [_option(
            'node:stage0.approve', request,
            editable_fields=['action.flags[5] (reason)'],
            binding_fields=bind + [
                'action.node', 'action.flags[0:5] (revision/actor binding)'],
        )]

    if waiting_on == queue.WAITING_ON_STAGE2_REVIEW:
        template = GlobalReviewDecisionSet.model_validate(
            pack['global_template']).model_dump(mode='json')
        template['actor'] = _ACTOR
        scope = template.get('blocking_scope')
        if scope is not None:
            workpack_sha = pack['global'].get('workpack_sha256')
            if not isinstance(workpack_sha, str):
                raise ReviewerTaskError('Stage2 global workpack 缺 workpack_sha256')
            scope['expected_workpack_sha256'] = workpack_sha
            scope['reason'] = _REASON
        for row in template['decisions']:
            row['reason'] = _REASON
        # 补上真正 workpack SHA 后再过一次生产写入 model。
        global_payload = GlobalReviewDecisionSet.model_validate(
            template).model_dump(mode='json')
        global_request = _request_wrapper(current, waiting_on, {
            'kind': 'node', 'node': 'stage2.global',
            'flags': ['--review-decisions', '@reviewer-input'],
            'input_payload': global_payload,
        })
        seen = BlueprintSeenRequest.model_validate({
            'review_token': pack['blueprint'].get('review_token'),
        }).model_dump(mode='json')
        seen_request = _request_wrapper(current, waiting_on, {
            'kind': 'approval', 'surface': 'blueprint_seen',
            'payload': seen,
        })
        return [
            _option(
                'node:stage2.global', global_request,
                editable_fields=[
                    'action.input_payload.blocking_scope.state/reason/evidence',
                    'action.input_payload.decisions[].state/reason/evidence',
                ],
                binding_fields=bind + [
                    'action.node', 'action.flags', 'action.input_payload.actor',
                    'all expected_* identities and item/chapter ids',
                ],
                allowed_values={
                    'blocking_scope.state': ['PASS', 'UNVER'],
                    'decisions[].state': ['PASS', 'FAIL', 'UNVER'],
                }),
            _option(
                'approval:blueprint_seen', seen_request,
                editable_fields=[],
                binding_fields=bind + [
                    'action.surface', 'action.payload.review_token'],
            ),
        ]

    if waiting_on == queue.WAITING_ON_STAGE4_DESIGN_REVIEW:
        source = DesignReviewTemplate.model_validate(pack['_design_template'])
        payload = _compact_design_payload(source)
        request = _request_wrapper(current, waiting_on, {
            'kind': 'approval', 'surface': 'design', 'payload': payload,
        })
        relation_effects = {
            'story_before': {
                'kind': 'story_before', 'enforcement': 'hard',
                'before_alias': '<workpack alias>',
                'after_alias': '<workpack alias>',
            },
            'response_to': {
                'kind': 'response_to', 'enforcement': 'hard',
                'stimulus_alias': '<workpack alias>',
                'response_alias': '<workpack alias>',
            },
            'same_scene': {
                'kind': 'same_scene', 'enforcement': 'hard',
                'left_alias': '<workpack alias; 必须小于 right>',
                'right_alias': '<workpack alias>',
            },
            'observes': {
                'kind': 'observes', 'enforcement': 'hard',
                'observer_alias': '<workpack participant alias>',
                'observed_alias': '<workpack fact alias>',
                'observation_alias': '<workpack fact alias>',
            },
        }
        return [_option(
            'approval:design', request,
            editable_fields=[
                'action.payload.general_reason',
                'action.payload.strategy.decision/reason/replan_constraints',
                'action.payload.chapters[].decision/reason/constraints',
            ],
            binding_fields=bind + [
                'action.surface', 'action.payload.actor/reviewer_kind/review_run_id',
                'action.payload.review_token',
                'action.payload.chapters[].output_ordinal',
            ],
            allowed_values={
                'strategy.decision': list(DESIGN_STRATEGY_DECISIONS),
                'strategy.replan_constraints[].kind': list(
                    DESIGN_STRATEGY_REPLAN_CONSTRAINTS),
                'chapters[].decision': list(DESIGN_CHAPTER_DECISIONS),
                'chapters[].constraints[].code': list(DESIGN_REPAIR_CODES),
                'chapters[].constraints[].minimum_refs': {
                    code: {
                        'scene_refs': DESIGN_REPAIR_CONSTRAINT_ARITY[code][0],
                        'opaque_aliases': DESIGN_REPAIR_CONSTRAINT_ARITY[code][1],
                    }
                    for code in DESIGN_REPAIR_CODES
                },
                'narrative_relation.effect_by_kind': relation_effects,
            },
        )]

    if waiting_on == queue.WAITING_ON_STAGE4_STRUCTURE_REVIEW:
        payload = StructureApprovalRequest.model_validate(
            pack['_structure_template']).model_dump(mode='json')
        payload['actor'] = _ACTOR
        payload['reason'] = _REASON
        for row in payload['chapters']:
            row['reason'] = _REASON
        request = _request_wrapper(current, waiting_on, {
            'kind': 'approval', 'surface': 'structure', 'payload': payload,
        })
        return [_option(
            'approval:structure', request,
            editable_fields=[
                'action.payload.reason',
                'action.payload.chapters[].decision/reason',
            ],
            binding_fields=bind + [
                'action.surface', 'action.payload.actor/review_token',
                'action.payload.chapters[].output_ordinal',
            ],
            allowed_values={
                'chapters[].decision': list(STRUCTURE_APPROVAL_DECISIONS),
            },
        )]

    if waiting_on == queue.WAITING_ON_WRITING_APPROVAL:
        payload = WritingApprovalRequest.model_validate(
            pack['_writing_template']).model_dump(mode='json')
        payload['actor'] = _ACTOR
        payload['reason'] = _REASON
        request = _request_wrapper(current, waiting_on, {
            'kind': 'approval', 'surface': 'writing', 'payload': payload,
        })
        return [_option(
            'approval:writing', request,
            editable_fields=['action.payload.reason'],
            binding_fields=bind + [
                'action.surface', 'action.payload.actor/review_token'],
        )]

    if waiting_on == queue.WAITING_ON_STAGE5_CLOSURE:
        template = Stage5ClosureTemplate.model_validate(pack['_stage5_template'])
        payload = _compact_stage5_payload(template)
        request = _request_wrapper(current, waiting_on, {
            'kind': 'approval', 'surface': 'stage5', 'payload': payload,
        })
        return [_option(
            'approval:stage5', request,
            editable_fields=[
                'action.payload.reason',
                'action.payload.must_appear[].ruling/reason',
                'action.payload.group_acks[].acknowledged',
            ],
            binding_fields=bind + [
                'action.surface', 'action.payload.actor/review_token',
                'action.payload.must_appear[].chapter_ordinal/evidence_key',
                'action.payload.group_acks[].group',
            ],
            allowed_values={
                'must_appear[].ruling': list(STAGE5_MUST_APPEAR_RULINGS),
                'group_acks[].group': list(STAGE5_CLOSURE_GROUPS),
                'group_acks[].acknowledged': [True],
            },
        )]

    if waiting_on == 'stage6.approve':
        token = pack['stage6'].get('review_token')
        if not isinstance(token, str):
            raise ReviewerTaskError('Stage6 workpack 缺 review_token')
        request = _request_wrapper(current, waiting_on, {
            'kind': 'node', 'node': 'stage6.approve',
            'flags': [
                '--pick', '<1、2 或 3>', '--actor', _ACTOR,
                '--reason', _REASON, '--review-token', token,
            ],
        })
        return [_option(
            'node:stage6.approve', request,
            editable_fields=['action.flags[1] (pick)', 'action.flags[5] (reason)'],
            binding_fields=bind + [
                'action.node', 'action.flags 的 flag 名/actor/review-token'],
            allowed_values={'pick': ['1', '2', '3']},
        )]

    if waiting_on == 'stage7.approve':
        token = pack['stage7'].get('review_token')
        if not isinstance(token, str):
            raise ReviewerTaskError('Stage7 workpack 缺 review_token')
        request = _request_wrapper(current, waiting_on, {
            'kind': 'node', 'node': 'stage7.approve',
            'flags': [
                '--actor', _ACTOR, '--reason', _REASON,
                '--review-token', token,
            ],
        })
        return [_option(
            'node:stage7.approve', request,
            editable_fields=['action.flags[3] (reason)'],
            binding_fields=bind + [
                'action.node', 'action.flags 的 flag 名/actor/review-token'],
        )]
    raise ReviewerTaskError(f'未登记 reviewer blocker：{waiting_on}')


def build_task(
    base: str | os.PathLike[str],
    sid: str,
    current: OperatorNext,
    request_path: str | os.PathLike[str],
    *,
    epoch_id: str,
) -> ReviewerTask | None:
    """冻结当前 blocker 的阅读包；不为表外 blocker 构造通用任务。"""
    if current.state != 'blocked' or current.action is None:
        raise ReviewerTaskError('reviewer task 只接受带 action 的 blocked next')
    waiting_on = current.action.waiting_on
    if waiting_on is None or not is_registered(waiting_on):
        return None
    base_abs = os.path.abspath(os.fspath(base))
    request = Path(request_path).resolve()
    request_root = paths.scratch_session_dir(base_abs, sid, epoch_id).resolve()
    if not request.is_relative_to(request_root):
        raise ReviewerTaskError(
            f'reviewer request 只能落在当前服务 scratch：{request_root}')
    if request.exists() or request.is_symlink():
        raise ReviewerTaskError(
            f'reviewer request 必须是当前 turn 的全新落点：{request}')
    review_run_id = f'supervisor-{current.action.action_sha256[:20]}'
    pack = _workpack(
        base_abs, sid, waiting_on, review_run_id=review_run_id)
    options = _submission_options(pack, current, waiting_on)
    if not options:
        raise ReviewerTaskError(f'{waiting_on} 没有 typed submission option')
    envelope = {
        'schema': 'novel-reviewer-workpack-v1',
        'base': base_abs,
        'sid': sid,
        'surface_seal': current.surface_seal,
        'waiting_on': waiting_on,
        'action_sha256': current.action.action_sha256,
        'materials': _visible_materials(pack),
        'submission_options': options,
    }
    instructions = f"""你是本轮唯一 Commander/Reviewer，只审阅下面这份不可变工作包。
先逐项审阅，再做一次跨项复核；两遍完成后只提交一个 typed 判断。你不是流水线调度器：
不得启动任何小说节点，不得启动 Executor，不得维护 Markdown 报告，不得调用遥测，
不得修改正式产物或审批账。

若证据不足，保留 UNVER 或拒绝；不得猜 PASS。最终只把一份 UTF-8 JSON 写到：
{request}
不得写别的请求文件。审阅完成后，从 submission_options 选择恰好一项，
完整复制它的 exact_request；不得新增、删除或改名 wrapper/payload 字段。
只能修改该项 editable_fields 列出的人工判断值；binding_fields 已由确定性
Supervisor 绑定，必须逐字保留。allowed_values 是可提交闭集；工作包中的
scene/alias/token 必须原样使用。exact_request 里任何完整尖括号值都是不可提交
sentinel，必须在阅读后改成具体 typed 值。

一次只提交一个动作。Stage2 是唯一的两步闸：当前 global 决策未闭合时先选
node:stage2.global；global 已闭合、只缺完整 Blueprint 阅读事实时才选
approval:blueprint_seen。Supervisor 会在盘面变化后用新工作包继续。
"""
    # 只守我们自己的静态能力边界；正文/证据是不可信数据，不能因其合法出现
    # 工具名就让 reviewer 主路假红。
    forbidden = ('run_step', 'operator next', 'finalizer')
    if any(token in instructions for token in forbidden):
        raise AssertionError('reviewer 静态指令意外包含调度/收尾指令')
    prompt = f"""{instructions}
<immutable-workpack>
{json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=True)}
</immutable-workpack>
"""
    return ReviewerTask(
        waiting_on=waiting_on, surface_seal=current.surface_seal,
        request_path=request, prompt=prompt)


def _strict_object(value: Any, *, keys: frozenset[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or frozenset(value) != keys:
        raise ReviewerTaskError(
            f'{label} 字段必须恰为 {sorted(keys)}')
    return value


def _placeholder_paths(value: Any, path: str = '$') -> list[str]:
    """完整 ``<...>`` 只是 reviewer skeleton sentinel，永远不是业务值。"""
    if isinstance(value, str):
        stripped = value.strip()
        return [path] if stripped.startswith('<') and stripped.endswith('>') else []
    if isinstance(value, list):
        return [
            found
            for index, item in enumerate(value)
            for found in _placeholder_paths(item, f'{path}[{index}]')
        ]
    if isinstance(value, dict):
        return [
            found
            for key, item in value.items()
            for found in _placeholder_paths(item, f'{path}.{key}')
        ]
    return []


def load_action(path: str | os.PathLike[str], current: OperatorNext) -> ReviewerAction:
    """严格读取并绑定当前 blocker；业务 schema 继续复用现役 typed model。"""
    request_path = Path(path)
    if request_path.is_symlink() or not request_path.is_file():
        raise ReviewerTaskError('reviewer request 必须是当前 scratch 内的普通文件')
    try:
        raw = _JSON_OBJECT.validate_json(request_path.read_bytes())
    except (OSError, ValidationError) as exc:
        raise ReviewerTaskError(
            f'reviewer request 无法读取：{type(exc).__name__}') from exc
    placeholders = _placeholder_paths(raw)
    if placeholders:
        raise ReviewerTaskError(
            'reviewer request 仍有未填写 skeleton sentinel：'
            f'{placeholders[:8]}')
    doc = _strict_object(
        raw,
        keys=frozenset({'schema', 'surface_seal', 'waiting_on', 'action'}),
        label='reviewer request')
    if doc['schema'] != REQUEST_SCHEMA:
        raise ReviewerTaskError(f'reviewer request schema 必须是 {REQUEST_SCHEMA}')
    if current.state != 'blocked' or current.action is None:
        raise ReviewerTaskError('reviewer request 只能应用到当前 blocked next')
    waiting_on = current.action.waiting_on
    if (doc['surface_seal'] != current.surface_seal
            or doc['waiting_on'] != waiting_on):
        raise ReviewerTaskError('reviewer request 与当前 blocker/surface 不一致')
    action = doc['action']
    if not isinstance(action, dict):
        raise ReviewerTaskError('reviewer request.action 必须是 object')
    if action.get('kind') == 'node':
        keys = frozenset(action)
        allowed_keys = (
            frozenset({'kind', 'node', 'flags'}),
            frozenset({'kind', 'node', 'flags', 'input_payload'}),
        )
        if keys not in allowed_keys:
            raise ReviewerTaskError('node action 字段不符合 typed request')
        body = action
        allowed = _NODE_FOR_BLOCKER.get(waiting_on or '', frozenset())
        node = body['node']
        flags = body['flags']
        if node not in allowed:
            raise ReviewerTaskError(
                f'blocker={waiting_on!r} 不允许 node={node!r}')
        if (not isinstance(flags, list)
                or any(not isinstance(flag, str) or not flag for flag in flags)):
            raise ReviewerTaskError('node action.flags 必须是非空字符串数组')
        input_payload = body.get('input_payload')
        if input_payload is not None and not isinstance(input_payload, dict):
            raise ReviewerTaskError('node action.input_payload 必须是 object')
        if node == 'stage2.global':
            if '--review-decisions' not in flags or input_payload is None:
                raise ReviewerTaskError(
                    'stage2.global reviewer action 必须携带 review-decisions payload')
            marker = flags.index('--review-decisions') + 1
            if marker >= len(flags) or flags[marker] != '@reviewer-input':
                raise ReviewerTaskError(
                    '--review-decisions 的值必须是 @reviewer-input 占位符')
            try:
                input_payload = GlobalReviewDecisionSet.model_validate(
                    input_payload).model_dump(mode='json')
            except ValidationError as exc:
                raise ReviewerTaskError(
                    'stage2.global review-decisions 不符合 typed 契约') from exc
        elif input_payload is not None:
            raise ReviewerTaskError(f'{node} 不接受 reviewer input_payload')
        return ReviewerNodeAction(
            kind='node', node=node, flags=tuple(flags),
            input_payload=input_payload)
    if action.get('kind') == 'approval':
        body = _strict_object(
            action, keys=frozenset({'kind', 'surface', 'payload'}),
            label='approval action')
        surface = body['surface']
        allowed = _APPROVAL_FOR_BLOCKER.get(waiting_on or '', frozenset())
        if surface not in allowed:
            raise ReviewerTaskError(
                f'blocker={waiting_on!r} 不允许 approval surface={surface!r}')
        models = {
            'design': DesignReviewRequest,
            'structure': StructureApprovalRequest,
            'writing': WritingApprovalRequest,
            'stage5': Stage5ClosureRequest,
            'blueprint_seen': BlueprintSeenRequest,
        }
        try:
            payload = models[surface].model_validate(body['payload']).model_dump(
                mode='json')
        except (KeyError, ValidationError) as exc:
            raise ReviewerTaskError(
                f'{surface} reviewer payload 不符合 typed 契约') from exc
        return ReviewerApprovalAction(
            kind='approval', surface=surface, payload=payload)
    raise ReviewerTaskError('reviewer request.action.kind 只能是 node 或 approval')


__all__ = [
    'ReviewerAction', 'ReviewerApprovalAction', 'ReviewerNodeAction',
    'ReviewerTask', 'ReviewerTaskError', 'build_task', 'is_registered',
    'load_action',
]
