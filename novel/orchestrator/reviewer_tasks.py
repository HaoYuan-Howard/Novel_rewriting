#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Commander reviewer 的闭集任务与单动作 typed 输出。

这里不调模型，也不执行流水线节点。它只在 Supervisor 已经取得一份
``OperatorNext`` 后，生成该 blocker 的 immutable workpack，并严格读取 Commander
写下的一份外部 typed request。表外 blocker 返回 ``None``，调用方必须停下，不能把
一个通用 Commander 重新扩成第二套编排器。
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import TypeAdapter, ValidationError

from contracts.artifacts import STAGE2_GLOBAL_EDIT, STAGE2_GLOBAL_REVIEW_DECISIONS
from contracts.invariants import (
    DESIGN_CHAPTER_DECISIONS,
    DESIGN_REPAIR_CODE_INSTRUCTIONS,
    DESIGN_REPAIR_CODES,
    DESIGN_REPAIR_CONSTRAINT_ARITY,
    DESIGN_REPAIR_CONSTRAINT_EXAMPLES,
    DESIGN_STRATEGY_DECISIONS,
    DESIGN_STRATEGY_REPLAN_CONSTRAINT_EXAMPLES,
    DESIGN_STRATEGY_REPLAN_CONSTRAINT_INSTRUCTIONS,
    DESIGN_STRATEGY_REPLAN_CONSTRAINTS,
    REVIEWER_APPROVAL_SURFACES,
    STAGE5_CLOSURE_GROUPS,
    STAGE5_MUST_APPEAR_RULINGS,
    STRUCTURE_APPROVAL_DECISIONS,
)
from contracts.schema import ArtifactVersionError, decode_artifact, encode_artifact
from contracts.schema.api import (
    BlueprintSeenRequest,
    DesignRepairConstraint,
    DesignReviewRequest,
    DesignReviewTemplate,
    DesignStrategyReplanRuling,
    OperatorNext,
    Stage5ClosureRequest,
    Stage5ClosureTemplate,
    StructureApprovalRequest,
    WritingApprovalRequest,
)
from contracts.schema.evaluation import GlobalReviewDecisionSet
from contracts.schema.orchestration import ReviewerActionRecord
from contracts.schema.stage2 import GlobalEdit
from novel import paths
from novel.orchestrator import queue, reviewer_contract
from novel.quality.global_review import QUARANTINABLE_REVIEW_GROUP_PREFIXES


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
    business_sha256: str
    request_path: Path
    prompt: str
    submission_options: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class ReviewerNodeAction:
    kind: Literal['node']
    node: str
    flags: tuple[str, ...]
    input_payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class ReviewerApprovalAction:
    kind: Literal['approval']
    surface: Literal[REVIEWER_APPROVAL_SURFACES]
    payload: dict[str, Any]


ReviewerAction = ReviewerNodeAction | ReviewerApprovalAction


_NODE_FOR_BLOCKER: dict[str, frozenset[str]] = {
    'stage0.approve': frozenset({'stage0.approve'}),
    queue.WAITING_ON_STAGE2_REVIEW: frozenset({'stage2.global'}),
    queue.WAITING_ON_STAGE2_EDIT_CORRECTION: frozenset({'stage2.global'}),
    'stage6.approve': frozenset({'stage6.approve'}),
    'stage7.approve': frozenset({'stage7.approve'}),
}

_APPROVAL_FOR_BLOCKER: dict[str, frozenset[str]] = {
    queue.WAITING_ON_STAGE2_REVIEW: frozenset({'blueprint_seen'}),
    queue.WAITING_ON_STAGE4_DESIGN_REVIEW: frozenset({'design'}),
    queue.WAITING_ON_STAGE4_BINDING_RESOLUTION: frozenset({'design'}),
    queue.WAITING_ON_STAGE4_STRUCTURE_REPLAN: frozenset({'design'}),
    queue.WAITING_ON_STAGE4_STRUCTURE_REVIEW: frozenset({'structure'}),
    queue.WAITING_ON_WRITING_APPROVAL: frozenset({'writing'}),
    queue.WAITING_ON_STAGE5_CLOSURE: frozenset({'stage5'}),
}

_REGISTERED = frozenset(_NODE_FOR_BLOCKER) | frozenset(_APPROVAL_FOR_BLOCKER)

_STAGE4_DESIGN_DECISION_BLOCKERS = frozenset({
    queue.WAITING_ON_STAGE4_DESIGN_REVIEW,
    queue.WAITING_ON_STAGE4_BINDING_RESOLUTION,
    queue.WAITING_ON_STAGE4_STRUCTURE_REPLAN,
})


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


def _approval_current_decision(
    cli: list[str], base: str, sid: str, key: str, *, expected_state: str,
) -> dict[str, Any]:
    """只取一个当前决定；历史与其他审批面都不进入 Commander 材料。"""
    shown = _json_command([
        *cli, 'approval', 'show', sid, '--base', base])
    if not isinstance(shown, dict) or not isinstance(shown.get(key), dict):
        raise ReviewerTaskError(f'approval show 缺少 {key} 视图')
    value = shown[key]
    if value.get('state') != expected_state:
        raise ReviewerTaskError(
            f'approval show.{key}.state 已不是 {expected_state!r}')
    decision = value.get('current_decision')
    if not isinstance(decision, dict):
        raise ReviewerTaskError(
            f'approval show.{key} 缺当前 {expected_state} 决定')
    return decision


def _stage4_workpack(
    base: str,
    sid: str,
    waiting_on: str,
    *,
    review_run_id: str,
    current: OperatorNext,
) -> dict[str, Any]:
    """在一个进程、一个 operator 坐标内生成 Stage4 reviewer 全包。

    旧路径依次启动 design workpack/template、structure workpack、approval show
    四个 Python CLI；每个进程都会重新扫描代码与书面。这里复用 Supervisor 已经
    冻结的 overview/code，并用首尾 surface seal CAS 保证不是混合时点。
    """
    from novel.orchestrator import approvals, inspection, state
    from novel.stages.stage4.plan import run_check_only
    from novel.store import readonly_snapshot_memo

    base_abs = os.path.abspath(base)
    if (
        current.sid != sid
        or os.path.abspath(current.base) != base_abs
        or current.overview.sid != sid
    ):
        raise ReviewerTaskError('Stage4 reviewer 的 operator 快照不属于当前书')
    novel_dir = paths.resolve_novel_dir(base_abs, sid).resolve()
    with readonly_snapshot_memo(novel_dir):
        before = inspection.read_surface_seal(
            base_abs, sid, code=current.code_version)
        if before != current.surface_seal:
            raise ReviewerTaskError(
                'Stage4 reviewer 启动前 operator surface 已变化；请刷新 next')
        with approvals.read_projection(
                overview=current.overview, code=current.code_version):
            design = approvals.design_workpack(
                base_abs, sid, overview=current.overview,
                code=current.code_version).model_dump(mode='json')
            template = approvals.design_template(
                base_abs, sid,
                actor=_ACTOR, reason=_REASON,
                reviewer_kind='delegated_operator',
                review_run_id=review_run_id,
                overview=current.overview,
                code=current.code_version,
            ).model_dump(mode='json')
            pack: dict[str, Any] = {
                'design': design,
                # 只供确定性封装层投影 compact POST；不重复进入阅读面。
                '_design_template': template,
            }
            if waiting_on == queue.WAITING_ON_STAGE4_STRUCTURE_REPLAN:
                report = run_check_only(state.readonly_ctx(
                    novel_dir, 'stage4.plan', current.code_version))
                structure = approvals.structure_workpack(
                    base_abs, sid,
                    overview=current.overview, code=current.code_version,
                    check_report=report,
                ).model_dump(mode='json')
                approval_doc = approvals.get(
                    base_abs, sid,
                    overview=current.overview, code=current.code_version,
                    include_spawns=False, operator_projection=True)
                view = approval_doc.structure
                if (view.state != 'replan_requested'
                        or view.current_decision is None):
                    raise ReviewerTaskError(
                        'Stage4 structure 当前已不是 replan_requested')
                pack.update({
                    'structure': structure,
                    'structure_replan_decision':
                        view.current_decision.model_dump(mode='json'),
                })
        after = inspection.read_surface_seal(
            base_abs, sid, code=current.code_version)
        if after != before:
            raise ReviewerTaskError(
                'Stage4 reviewer workpack 构造期间 surface 变化；请重试')
    return pack


def _workpack(
    base: str,
    sid: str,
    waiting_on: str,
    *,
    review_run_id: str,
    current: OperatorNext | None = None,
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
    if waiting_on in queue.STAGE2_REVIEWER_WAITING_ON:
        correction: dict[str, Any] | None = None
        if waiting_on == queue.WAITING_ON_STAGE2_EDIT_CORRECTION:
            raw_correction = _json_command([
                *cli, 'run', 'stage2.global', sid, '--base', base,
                '--emit-edit-correction-workpack'])
            if not isinstance(raw_correction, dict):
                raise ReviewerTaskError('Stage2 reviewed edit correction workpack 不是 object')
            correction = raw_correction
            global_pack = correction.get('global_review_workpack')
        else:
            global_pack = _json_command([
                *cli, 'run', 'stage2.global', sid, '--base', base,
                '--emit-review-workpack', '--review-scope', 'blocking'])
        if not isinstance(global_pack, dict) or not isinstance(
                global_pack.get('items'), list):
            raise ReviewerTaskError('Stage2 global workpack 缺 items')
        states = {
            str(row.get('current_state'))
            for row in global_pack['items'] if isinstance(row, dict)
        }
        pack: dict[str, Any] = {'global': global_pack}
        if correction is not None:
            if (correction.get('kind')
                    != 'stage2-global-edit-correction-workpack-v1'
                    or not isinstance(correction.get('failed_edit'), dict)
                    or not isinstance(correction.get('hard_hits'), list)
                    or not correction['hard_hits']):
                raise ReviewerTaskError('Stage2 reviewed edit correction workpack 形状漂移')
            pack['_global_edit_template'] = correction['failed_edit']
            pack['edit_correction'] = {
                key: value for key, value in correction.items()
                if key not in {'failed_edit', 'global_review_workpack'}
            }
            return pack

        has_pending = 'pending' in states
        has_failed = 'FAIL' in states
        has_undecided_unver = any(
            isinstance(row, dict)
            and row.get('current_state') == 'UNVER'
            and row.get('generation_disposition') is None
            for row in global_pack['items'])
        if has_pending:
            pack['global_template'] = _json_command([
                *cli, 'run', 'stage2.global', sid, '--base', base,
                '--emit-review-template', '--review-scope', 'blocking'])
        elif has_failed or has_undecided_unver:
            pack['global_edit_template'] = _json_command([
                *cli, 'run', 'stage2.global', sid, '--base', base,
                '--emit-edit-template'])
        else:
            # Blueprint 工作包只在 Global 已收口、确实可能提交 seen 时读取。
            # 把它提前塞进 pending/FAIL turn 会让 Commander 重读一份此轮用不到、
            # 且 GlobalEdit 后很可能立即 stale 的整书材料。
            pack['blueprint'] = _json_command([
                *cli, 'run', 'stage2.blueprint', sid, '--base', base,
                '--emit-review-workpack'])
        return pack
    if waiting_on in _STAGE4_DESIGN_DECISION_BLOCKERS:
        if current is None:
            raise ReviewerTaskError('Stage4 reviewer workpack 缺 operator 冻结快照')
        return _stage4_workpack(
            base, sid, waiting_on, review_run_id=review_run_id,
            current=current)
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


def approval_review_token(
    submission_options: Sequence[Mapping[str, Any]],
) -> str | None:
    """当前 blocker 唯一 approval 选项绑定的 opaque review_token。

    这是 approve 侧判定人工决定是否仍适用的同一枚 token；没有 approval 选项、
    或多个选项给出不同 token 时返回 ``None``，调用方不得猜。
    """
    tokens: set[str] = set()
    for option in submission_options:
        request = (
            option.get('exact_request') if isinstance(option, Mapping) else None)
        action = request.get('action') if isinstance(request, Mapping) else None
        if not isinstance(action, Mapping) or action.get('kind') != 'approval':
            continue
        payload = action.get('payload')
        token = payload.get('review_token') if isinstance(payload, Mapping) else None
        if isinstance(token, str) and token.strip():
            tokens.add(token)
    if len(tokens) != 1:
        return None
    return tokens.pop()


def replay_request_document(
    record: ReviewerActionRecord, current: OperatorNext,
) -> dict[str, Any]:
    """把一份持久化的 approval 决定重新物化成当前 blocker 的 typed request。

    wrapper 的 surface_seal / waiting_on 是运输绑定，按当前快照重铸；业务 payload
    逐字来自记录。产出仍必须经 :func:`load_action`：schema、sentinel、all-accept
    等确定性判据以今天的代码为准，记录本身不是免检凭证。
    """
    if current.state != 'blocked' or current.action is None:
        raise ReviewerTaskError('reviewer 决定只能重放到当前 blocked next')
    waiting_on = current.action.waiting_on
    if waiting_on is None or record.waiting_on != waiting_on:
        raise ReviewerTaskError('reviewer 决定记录与当前 blocker 不一致')
    return _request_wrapper(current, waiting_on, {
        'kind': 'approval', 'surface': record.surface,
        'payload': copy.deepcopy(record.payload),
    })


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


def _canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False,
    ).encode('utf-8')).hexdigest()


def _stage4_structure_replan_materials(
    pack: dict[str, Any],
) -> dict[str, Any]:
    """把 B→A 回审压成“完整 A + 被拒 B 增量”，并绑定完整 B 哈希。

    当前 B typed 决定仍完整保留；B workpack 只保留全局证据和被点名章，已经
    accept 的逐章 coverage 不再重复发送。完整 workpack 哈希让判重与审计仍绑定
    原始材料，而不是把压缩阅读面误当成新的业务主体。
    """
    design = pack.get('design')
    structure = pack.get('structure')
    decision = pack.get('structure_replan_decision')
    if not isinstance(design, dict) or not isinstance(structure, dict) \
            or not isinstance(decision, dict):
        raise ReviewerTaskError('Stage4 structure replan 缺 A/B/typed 决定')
    rows = decision.get('chapters')
    if not isinstance(rows, list):
        raise ReviewerTaskError('Stage4 structure replan 决定缺 chapters')
    requested = sorted({
        row.get('output_ordinal') for row in rows
        if isinstance(row, dict) and row.get('decision') == 'request_replan'
        and isinstance(row.get('output_ordinal'), int)
        and not isinstance(row.get('output_ordinal'), bool)
    })
    if not requested:
        raise ReviewerTaskError('Stage4 structure replan 决定没有 request_replan 章')
    groups = structure.get('groups')
    ordinals = structure.get('chapter_ordinals')
    if not isinstance(groups, list) or not isinstance(ordinals, list):
        raise ReviewerTaskError('Stage4 structure workpack 缺 groups/chapter_ordinals')
    compact_groups: list[dict[str, Any]] = []
    kept_items = 0
    full_items = 0
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get('items'), list):
            raise ReviewerTaskError('Stage4 structure workpack group 形状漂移')
        items = group['items']
        full_items += len(items)
        kept = [
            copy.deepcopy(item) for item in items
            if isinstance(item, dict)
            and (item.get('chapter_ordinal') is None
                 or item.get('chapter_ordinal') in requested)
        ]
        kept_items += len(kept)
        compact_groups.append({
            **{key: copy.deepcopy(value) for key, value in group.items()
               if key not in {'items', 'scanned'}},
            'scanned': (
                f'仅发送 request_replan 章 {requested} 与全局项；'
                f'完整组由 full_workpack_sha256 绑定'),
            'items': kept,
        })
    accepted = sorted({
        value for value in ordinals
        if isinstance(value, int) and not isinstance(value, bool)
        and value not in requested
    })
    structure_identity = {
        key: copy.deepcopy(value) for key, value in structure.items()
        if key != 'groups'
    }
    return {
        'design': copy.deepcopy(design),
        'structure_replan': {
            'kind': 'stage4-structure-replan-delta-v1',
            'full_workpack_sha256': _canonical_json_sha256(structure),
            'structure_identity': structure_identity,
            'requested_replan_chapters': requested,
            'omitted_accepted_chapters': accepted,
            'full_item_count': full_items,
            'sent_item_count': kept_items,
            'groups': compact_groups,
            'current_decision': copy.deepcopy(decision),
        },
    }


def _review_materials(
    pack: dict[str, Any], waiting_on: str,
) -> dict[str, Any]:
    if waiting_on == queue.WAITING_ON_STAGE4_STRUCTURE_REPLAN:
        return _stage4_structure_replan_materials(pack)
    return _visible_materials(pack)


def _canonicalize_design_review_payload(value: Any) -> Any:
    """只归一集合式顺序；重复项仍交给 typed schema 关闭式拒绝。"""
    if not isinstance(value, dict):
        return value
    payload = copy.deepcopy(value)

    def canonical_rows(rows: Any) -> Any:
        if not isinstance(rows, list):
            return rows
        return sorted(rows, key=lambda row: json.dumps(
            row, ensure_ascii=False, sort_keys=True,
            separators=(',', ':'), allow_nan=False))

    strategy = payload.get('strategy')
    if isinstance(strategy, dict):
        constraints = strategy.get('replan_constraints')
        if isinstance(constraints, list):
            for constraint in constraints:
                if isinstance(constraint, dict) and isinstance(
                        constraint.get('chapter_ordinals'), list):
                    constraint['chapter_ordinals'] = sorted(
                        constraint['chapter_ordinals'],
                        key=lambda item: json.dumps(
                            item, ensure_ascii=False, sort_keys=True,
                            separators=(',', ':'), allow_nan=False))
            strategy['replan_constraints'] = canonical_rows(constraints)
    chapters = payload.get('chapters')
    if isinstance(chapters, list):
        for chapter in chapters:
            if isinstance(chapter, dict) and isinstance(
                    chapter.get('constraints'), list):
                chapter['constraints'] = canonical_rows(chapter['constraints'])

        def chapter_key(row: Any) -> tuple[int, Any]:
            ordinal = row.get('output_ordinal') if isinstance(row, dict) else None
            if isinstance(ordinal, int) and not isinstance(ordinal, bool):
                return (0, ordinal)
            return (1, json.dumps(
                row, ensure_ascii=False, sort_keys=True,
                separators=(',', ':'), allow_nan=False))

        chapters.sort(key=chapter_key)
    return payload


def _global_review_template(raw: Any) -> dict[str, Any]:
    """把生产 CLI 的版本化模板拆成可提交的业务 payload。"""
    try:
        payload = decode_artifact(
            STAGE2_GLOBAL_REVIEW_DECISIONS, raw).payload
        return GlobalReviewDecisionSet.model_validate(payload).model_dump(
            mode='json')
    except (ArtifactVersionError, ValidationError) as exc:
        raise ReviewerTaskError(
            'Stage2 global review template 不符合当前版本 typed 契约') from exc


def _global_edit_template(raw: Any) -> dict[str, Any]:
    """把生产 CLI 的版本化 GlobalEdit 骨架拆成 Commander 业务字段。"""
    try:
        payload = decode_artifact(STAGE2_GLOBAL_EDIT, raw).payload
        return GlobalEdit.model_validate(payload).model_dump(
            mode='json', by_alias=True, exclude_unset=True)
    except (ArtifactVersionError, ValidationError) as exc:
        raise ReviewerTaskError(
            'Stage2 GlobalEdit template 不符合当前版本 typed 契约') from exc


def _reviewed_global_edit_template(raw: Any, global_pack: dict[str, Any]) -> dict[str, Any]:
    """把返工轮的完整阅读身份并入同一份 GlobalEdit typed 动作。

    第二轮 Commander 既修改又自检；producer 只会把这份证明展开到该 edit
    **新打开**的 blocking 项，旧 FAIL 不变就仍会关闭式拒绝。
    """
    edit = _global_edit_template(raw)
    binding = global_pack.get('binding')
    if not isinstance(binding, dict):
        raise ReviewerTaskError('Stage2 GlobalEdit 返工缺完整 workpack binding')
    chapters = binding.get('reviewed_chapter_ordinals')
    source_sha256 = binding.get('expected_source_sha256')
    if (not isinstance(chapters, list)
            or not isinstance(source_sha256, str)):
        raise ReviewerTaskError('Stage2 GlobalEdit 返工 binding 形状漂移')
    edit['post_edit_review'] = {
        'actor': _ACTOR,
        'expected_source_sha256': source_sha256,
        'reviewed_chapter_ordinals': chapters,
        'default_state': 'PASS',
        'reason': _REASON,
        'quarantine_groups': [],
    }
    try:
        return GlobalEdit.model_validate(edit).model_dump(
            mode='json', by_alias=True, exclude_unset=True)
    except ValidationError as exc:  # pragma: no cover —— 上游两份可信产地应已闭合
        raise ReviewerTaskError('Stage2 reviewed GlobalEdit 骨架不符合 typed 契约') from exc


def _constraint_shapes() -> dict[str, dict[str, Any]]:
    """每个 active repair code 一份经现役 typed model 校验的最小合法示例。

    示例从 ``contracts/invariants.py`` 的纯 dict 铸出，再经 ``DesignRepairConstraint``
    现场校验：schema 与示例一旦漂移，build 直接失败，而不是把一份过期形状发给
    模型。它是可执行 binding 的一部分——loader 用的就是同一个 model。
    """
    return {
        code: DesignRepairConstraint.model_validate(
            DESIGN_REPAIR_CONSTRAINT_EXAMPLES[code]).model_dump(mode='json')
        for code in DESIGN_REPAIR_CODES
    }


def _replan_constraint_shapes() -> dict[str, dict[str, Any]]:
    return {
        kind: DesignStrategyReplanRuling.model_validate(
            DESIGN_STRATEGY_REPLAN_CONSTRAINT_EXAMPLES[kind]).model_dump(mode='json')
        for kind in DESIGN_STRATEGY_REPLAN_CONSTRAINTS
    }


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

    if waiting_on in queue.STAGE2_REVIEWER_WAITING_ON:
        rows = pack['global'].get('items')
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ReviewerTaskError('Stage2 global workpack.items 形状漂移')
        pending = [row for row in rows if row.get('current_state') == 'pending']
        failed = [row for row in rows if row.get('current_state') == 'FAIL']
        undecided_unver = [
            row for row in rows
            if row.get('current_state') == 'UNVER'
            and row.get('generation_disposition') is None
        ]
        safe_unver = [
            row for row in undecided_unver
            if str(row.get('group_key') or '').startswith(
                QUARANTINABLE_REVIEW_GROUP_PREFIXES)
        ]
        unsafe_unver = [row for row in undecided_unver if row not in safe_unver]

        edit_template = pack.get(
            '_global_edit_template', pack.get('global_edit_template'))

        if failed or unsafe_unver:
            if edit_template is None:
                raise ReviewerTaskError('Stage2 GlobalEdit 返工缺 typed template')
            edit_payload = _reviewed_global_edit_template(
                edit_template, pack['global'])
            edit_request = _request_wrapper(current, waiting_on, {
                'kind': 'node', 'node': 'stage2.global',
                'flags': ['--apply-edit', '@reviewer-input'],
                'input_payload': edit_payload,
            })
            return [_option(
                'node:stage2.global.apply-edit', edit_request,
                editable_fields=[
                    'action.input_payload 的现有 typed patch 值',
                    '模板已提供的 action.input_payload.*_replace 列表成员'
                    '（可增删，但必须保持 GlobalEdit schema）',
                    'action.input_payload.post_edit_review.reason/quarantine_groups',
                ],
                binding_fields=bind + [
                    'action.node', 'action.flags',
                    'patch 数组的行数、顺序、subject id 与字段集合；不得在 patch 中新增 subject',
                ],
                allowed_values={
                    'purpose': (
                        '这是 Stage2 最后一轮模型返工：一次修完模板点名项并复核完整结果；'
                        'post_edit_review 只会展开到本 edit 新打开的 blocking 项。原样模板、'
                        '未改变的旧 FAIL 与不可安全隔离的 UNVER 都会在写盘前被拒绝，不会再开第三轮。'),
                },
            )]

        if pending:
            if len(pending) == len(rows):
                template = _global_review_template(pack['global_template'])
                template['actor'] = _ACTOR
                scope = template.get('blocking_scope')
                if scope is None:
                    raise ReviewerTaskError('初轮 blocking 模板缺 scope binding')
                workpack_sha = pack['global'].get('workpack_sha256')
                if not isinstance(workpack_sha, str):
                    raise ReviewerTaskError('Stage2 global workpack 缺 workpack_sha256')
                scope['expected_workpack_sha256'] = workpack_sha
                scope['reason'] = _REASON
            else:
                # GlobalEdit 后只重审 subject hash 已变化的 pending 行；已继承的 PASS /
                # UNVER 决定不能被一个新的 scope 默认值覆盖。
                template = {
                    'actor': _ACTOR,
                    'decisions': [{
                        'item_id': row['item_id'],
                        'expected_subject_sha256': row['subject_sha256'],
                        'state': 'UNVER',
                        'reason': _REASON,
                        'evidence': [],
                    } for row in pending],
                    'blocking_scope': None,
                }
            global_payload = GlobalReviewDecisionSet.model_validate(
                template).model_dump(mode='json')
            global_request = _request_wrapper(current, waiting_on, {
                'kind': 'node', 'node': 'stage2.global',
                'flags': ['--review-decisions', '@reviewer-input'],
                'input_payload': global_payload,
            })
            return [_option(
                'node:stage2.global.review', global_request,
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
                },
            )]

        if safe_unver:
            quarantine_payload = GlobalReviewDecisionSet.model_validate({
                'actor': _ACTOR,
                'decisions': [{
                    'item_id': row['item_id'],
                    'expected_subject_sha256': row['subject_sha256'],
                    'state': 'UNVER',
                    'reason': row.get('current_reason') or _REASON,
                    'evidence': row.get('registered_evidence') or [],
                    'generation_disposition': {
                        'action': 'quarantine_group',
                        'reason': _REASON,
                    },
                } for row in safe_unver],
                'blocking_scope': None,
            }).model_dump(mode='json')
            quarantine_request = _request_wrapper(current, waiting_on, {
                'kind': 'node', 'node': 'stage2.global',
                'flags': ['--review-decisions', '@reviewer-input'],
                'input_payload': quarantine_payload,
            })
            if edit_template is None:
                raise ReviewerTaskError('Stage2 UNVER 处置缺 typed GlobalEdit template')
            edit_payload = _reviewed_global_edit_template(
                edit_template, pack['global'])
            edit_request = _request_wrapper(current, waiting_on, {
                'kind': 'node', 'node': 'stage2.global',
                'flags': ['--apply-edit', '@reviewer-input'],
                'input_payload': edit_payload,
            })
            return [
                _option(
                    'node:stage2.global.quarantine-unver', quarantine_request,
                    editable_fields=[
                        'action.input_payload.decisions[].reason/evidence',
                        'action.input_payload.decisions[].generation_disposition.reason',
                    ],
                    binding_fields=bind + [
                        'action.node', 'action.flags', 'action.input_payload.actor',
                        'item_id/expected_subject_sha256/state',
                        'generation_disposition.action',
                    ],
                    allowed_values={
                        'generation_disposition.action': ['quarantine_group'],
                    },
                ),
                _option(
                    'node:stage2.global.apply-edit-unver', edit_request,
                    editable_fields=[
                        'action.input_payload 的现有 typed patch 值',
                        '模板已提供的 action.input_payload.*_replace 列表成员',
                        'action.input_payload.post_edit_review.reason/quarantine_groups',
                    ],
                    binding_fields=bind + [
                        'action.node', 'action.flags',
                        'patch 数组的行数、顺序、subject id 与字段集合',
                    ],
                ),
            ]

        if waiting_on == queue.WAITING_ON_STAGE2_EDIT_CORRECTION:
            raise ReviewerTaskError(
                'Stage2 correction blocker 已无待修 FAIL/UNVER；不得借它批准 Blueprint')
        blueprint = pack.get('blueprint')
        if not isinstance(blueprint, dict):
            raise ReviewerTaskError('Stage2 Global 收口后缺 Blueprint workpack')
        seen = BlueprintSeenRequest.model_validate({
            'review_token': blueprint.get('review_token'),
        }).model_dump(mode='json')
        seen_request = _request_wrapper(current, waiting_on, {
            'kind': 'approval', 'surface': 'blueprint_seen',
            'payload': seen,
        })
        return [_option(
            'approval:blueprint_seen', seen_request,
            editable_fields=[],
            binding_fields=bind + [
                'action.surface', 'action.payload.review_token'],
        )]

    if waiting_on in _STAGE4_DESIGN_DECISION_BLOCKERS:
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
                # 语义与示例随包下发，reviewer 不必翻源码/测试猜字段；示例只在
                # 这里，不进 exact_request，占位 alias 仍须换成工作包里的真 alias。
                'chapters[].constraints[].semantics': dict(
                    DESIGN_REPAIR_CODE_INSTRUCTIONS),
                'chapters[].constraints[].shapes': _constraint_shapes(),
                'strategy.replan_constraints[].semantics': dict(
                    DESIGN_STRATEGY_REPLAN_CONSTRAINT_INSTRUCTIONS),
                'strategy.replan_constraints[].shapes': (
                    _replan_constraint_shapes()),
                'chapters[].constraints[].minimum_refs': {
                    code: {
                        'scene_refs': DESIGN_REPAIR_CONSTRAINT_ARITY[code][0],
                        'opaque_aliases': DESIGN_REPAIR_CONSTRAINT_ARITY[code][1],
                    }
                    for code in DESIGN_REPAIR_CODES
                },
                'narrative_relation.effect_by_kind': relation_effects,
                'resolution_requirement': (
                    '当前不是首次 A 审阅；必须至少提交一项 request_repair，或把策略'
                    '提交为 request_replan。原样 all-accept 不会关闭当前恢复 blocker。'
                    if waiting_on != queue.WAITING_ON_STAGE4_DESIGN_REVIEW
                    else '按当前 A 主体逐项审阅，可接受或提交 typed 修订决定。'
                ),
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
        base_abs, sid, waiting_on, review_run_id=review_run_id,
        current=current)
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
        'materials': _review_materials(pack, waiting_on),
        'submission_options': options,
    }
    if waiting_on in queue.STAGE2_REVIEWER_WAITING_ON:
        binding = pack['global'].get('binding')
        if not isinstance(binding, dict):
            raise ReviewerTaskError('Stage2 global workpack 缺业务 binding')
        business = {
            'waiting_on': waiting_on,
            'expected_source_sha256': binding.get('expected_source_sha256'),
            'main_text_subject_sha256': binding.get('main_text_subject_sha256'),
        }
        if waiting_on == queue.WAITING_ON_STAGE2_EDIT_CORRECTION:
            correction = pack.get('edit_correction')
            if not isinstance(correction, dict):
                raise ReviewerTaskError('Stage2 correction workpack 缺业务 binding')
            business.update({
                'failed_edit_sha256': correction.get('failed_edit_sha256'),
                'invalid_sha256': correction.get('invalid_sha256'),
            })
    elif waiting_on in {
        queue.WAITING_ON_STAGE4_BINDING_RESOLUTION,
        queue.WAITING_ON_STAGE4_STRUCTURE_REPLAN,
    }:
        # action_sha 包含运输 surface；恢复判重认的应是 A/B 业务材料本身。
        # 两种 resolution 都只提交新的 design 决定，不把 B 理由直接送 Executor。
        business = {
            'waiting_on': waiting_on,
            'materials': _visible_materials(pack),
        }
    else:
        business = {
            'waiting_on': waiting_on,
            'action_sha256': current.action.action_sha256,
        }
    business_sha256 = hashlib.sha256(json.dumps(
        business, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode('utf-8')).hexdigest()
    correction_instruction = (
        '\n当前 blocker 是进程中断后的 Stage2 最终纠正：第一次 Commander 修改已经'
        '执行并被 materials.edit_correction.hard_outcomes 精确拒绝。你现在占用第二次、'
        '也是最后一次真实 Commander 调用；只修 submission_options 中那份失败 edit，'
        '不要从头生成，不会再有第三次纠正。\n'
        if waiting_on == queue.WAITING_ON_STAGE2_EDIT_CORRECTION else '')
    stage4_resolution_instruction = (
        '\n当前不是再次审阅同一份 Stage4B。请把 materials 中已经落盘的 B '
        '不可行证据或最终结构 request_replan 决定，与当前 Stage4A design 工作包'
        '交叉阅读，形成一份新的 Stage4A typed 决定。必须至少有一章 '
        'request_repair，或策略为 request_replan；原样 all-accept 是无进展动作，'
        '会被确定性 loader 拒绝。只使用模板发布的 closed constraint、scene 与 '
        'opaque alias，不把自由文本理由改写成 Executor 指令。决定落盘后 '
        'Supervisor 会另取精确恢复许可，本轮 Commander 不启动 Executor。\n'
        if waiting_on in {
            queue.WAITING_ON_STAGE4_BINDING_RESOLUTION,
            queue.WAITING_ON_STAGE4_STRUCTURE_REPLAN,
        } else '')
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

一次只提交一个动作。Stage2 global 最多两次 Commander 调用：pending 是首轮完整审阅；
FAIL/不可隔离 UNVER 是第二轮、也是最后一轮返工。返工必须一次修完模板点名项，
并再次跨项核对本 edit 会打开的完整主体；post_edit_review 是你对这份精确修改结果的
显式自检，不会替未改变的旧 FAIL 洗绿，也不会再启动第三轮。证据仍不足的 UNVER
不得改名为 PASS：只有可整组删除的 secret/relation/world_rule 才能写进
post_edit_review.quarantine_groups，写作侧会删除完整事实组并在终审保留披露。
global 写作面已安全、只缺完整 Stage2 阅读/闸门 proof 恢复时才选
approval:blueprint_seen；Supervisor 会用你实际读过的 Blueprint token 确定性收口
其余早期 proof，你不得自行运行审批命令。
Supervisor 在两轮之间精确 resume 同一 Commander provider session；不要重新定位
已经读过的材料。

materials 已包含裁决所需的全部材料。不需要也不得读取 operator skill、仓库源码、测试
或盘上产物来补充材料；不得运行任何 shell/PowerShell/Python 命令读取材料或产物，唯一
允许的文件操作是写出上述请求文件。材料确实不足以裁决某一项时，保持模板的关闭式默认值
（request_replan / request_repair / UNVER）并在对应 reason 写明缺的是什么；不得猜 accept。
Stage4 A/B 工作包的逐章裁决材料在 materials 的 coverage 行 reading 里，顶层 reading.legend
是唯一字段说明；metric_only 组只是摘要，完整读数以逐章 reading 为准。
{correction_instruction}
{stage4_resolution_instruction}
"""
    # 只守我们自己的静态能力边界；正文/证据是不可信数据，不能因其合法出现
    # 工具名就让 reviewer 主路假红。
    forbidden = ('run_step', 'operator next', 'finalizer')
    if any(token in instructions for token in forbidden):
        raise AssertionError('reviewer 静态指令意外包含调度/收尾指令')
    # 本 blocker 的裁决判据随包下发；Commander 不再为此去读整份 operator skill。
    contract = reviewer_contract.for_blocker(waiting_on)
    if any(token in contract for token in forbidden):
        raise AssertionError('reviewer 契约摘录意外包含调度/收尾指令')
    prompt = f"""{instructions}
<reviewer-contract>
{contract}
</reviewer-contract>
<immutable-workpack>
{json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}
</immutable-workpack>
"""
    return ReviewerTask(
        waiting_on=waiting_on, surface_seal=current.surface_seal,
        business_sha256=business_sha256,
        request_path=request, prompt=prompt,
        submission_options=tuple(options))


def action_business_sha256(action: ReviewerAction) -> str:
    """忽略运输/自诱导 binding 后的一次 reviewer 业务动作身份。"""
    if isinstance(action, ReviewerApprovalAction):
        payload: dict[str, Any] = {
            'kind': action.kind, 'surface': action.surface,
            'payload': action.payload,
        }
    else:
        input_payload = action.input_payload
        if (action.node == 'stage2.global'
                and '--review-decisions' in action.flags
                and input_payload is not None):
            decoded = decode_artifact(
                STAGE2_GLOBAL_REVIEW_DECISIONS, input_payload).payload
            logical = GlobalReviewDecisionSet.model_validate(
                decoded).model_dump(mode='json')
            scope = logical.get('blocking_scope')
            if isinstance(scope, dict):
                # 决策写回会改变 review workpack 的字节摘要；它不是被审小说主体，
                # 不能让同一批决定仅凭这格变化取得第二次执行身份。
                scope['expected_workpack_sha256'] = '<business-binding>'
            input_payload = logical
        elif (action.node == 'stage2.global'
              and '--apply-edit' in action.flags
              and input_payload is not None):
            input_payload = decode_artifact(
                STAGE2_GLOBAL_EDIT, input_payload).payload
        payload = {
            'kind': action.kind, 'node': action.node,
            'flags': list(action.flags), 'input_payload': input_payload,
        }
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode('utf-8')).hexdigest()


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


_GLOBAL_EDIT_PATCH_IDENTITIES: dict[str, tuple[str, ...]] = {
    'secrets': ('secret_id',),
    'world_rules': ('rule_id',),
    'characters': ('char_id',),
    'relations': ('relation_id',),
    'arc_tracks': ('track_index',),
    'addresses': ('char_id', 'address_index'),
    # 这是“某个人物的完整称呼列表替换”，不是整个 characters 集合的 replacement；
    # char_id 仍只能取模板点名的现有 subject。
    'addresses_replace': ('char_id',),
}


def _stage2_submission_template(
    action: dict[str, Any],
    submission_options: Sequence[dict[str, Any]] | None,
) -> dict[str, Any]:
    """取出 Commander 所选动作在同一 immutable workpack 里的可信骨架。"""
    if submission_options is None:
        raise ReviewerTaskError('Stage2 reviewer action 缺 immutable submission binding')
    matches: list[dict[str, Any]] = []
    for option in submission_options:
        exact = option.get('exact_request')
        expected = exact.get('action') if isinstance(exact, dict) else None
        if not isinstance(expected, dict):
            continue
        if (
            expected.get('kind') == action.get('kind')
            and expected.get('node') == action.get('node')
            and expected.get('flags') == action.get('flags')
        ):
            matches.append(expected)
    if len(matches) != 1:
        raise ReviewerTaskError(
            '当前 immutable workpack 未唯一提供这项 Stage2 typed action')
    return matches[0]


def _row_identity(row: dict[str, Any], fields: tuple[str, ...]) -> tuple[Any, ...]:
    return tuple(row.get(field) for field in fields)


def _validate_global_edit_binding(
    submitted: dict[str, Any], expected: dict[str, Any],
) -> None:
    """patch 只能改可信模板已有值；新增/删减必须走模板显式给出的完整 replacement。"""
    if frozenset(submitted) != frozenset(expected):
        raise ReviewerTaskError(
            'Stage2 GlobalEdit 顶层字段必须逐字保留当前模板；'
            f'expected={sorted(expected)} got={sorted(submitted)}')
    for field, identity_fields in _GLOBAL_EDIT_PATCH_IDENTITIES.items():
        if field not in expected:
            continue
        expected_rows = expected[field]
        submitted_rows = submitted[field]
        if not isinstance(expected_rows, list) or not isinstance(submitted_rows, list):
            raise ReviewerTaskError(f'Stage2 GlobalEdit.{field} 必须是数组')
        expected_ids = [_row_identity(row, identity_fields) for row in expected_rows]
        submitted_ids = [_row_identity(row, identity_fields) for row in submitted_rows]
        if submitted_ids != expected_ids:
            raise ReviewerTaskError(
                f'Stage2 GlobalEdit.{field} patch subject 必须逐字保留模板顺序；'
                f'expected={expected_ids} got={submitted_ids}。'
                '新增 subject 只能等待模板明确提供完整 *_replace')
        for index, (submitted_row, expected_row) in enumerate(
                zip(submitted_rows, expected_rows, strict=True)):
            if frozenset(submitted_row) != frozenset(expected_row):
                raise ReviewerTaskError(
                    f'Stage2 GlobalEdit.{field}[{index}] 字段集合必须逐字保留模板')
    if 'setting' in expected:
        expected_setting = expected['setting']
        submitted_setting = submitted['setting']
        if not isinstance(expected_setting, dict) or not isinstance(
                submitted_setting, dict):
            raise ReviewerTaskError('Stage2 GlobalEdit.setting 必须是 object')
        if frozenset(submitted_setting) != frozenset(expected_setting):
            raise ReviewerTaskError(
                'Stage2 GlobalEdit.setting 字段集合必须逐字保留模板')
    if 'post_edit_review' in expected:
        expected_review = expected['post_edit_review']
        submitted_review = submitted['post_edit_review']
        if not isinstance(expected_review, dict) or not isinstance(
                submitted_review, dict):
            raise ReviewerTaskError('Stage2 GlobalEdit.post_edit_review 必须是 object')
        if frozenset(submitted_review) != frozenset(expected_review):
            raise ReviewerTaskError(
                'Stage2 GlobalEdit.post_edit_review 字段集合必须逐字保留模板')
        editable = frozenset({'reason', 'quarantine_groups'})
        for key, value in expected_review.items():
            if key not in editable and submitted_review.get(key) != value:
                raise ReviewerTaskError(
                    f'Stage2 GlobalEdit.post_edit_review.{key} 必须逐字保留模板')


def _validate_global_review_binding(
    submitted: dict[str, Any], expected: dict[str, Any],
) -> None:
    """人只改判断；item、subject hash、scope 与隔离动作都由 workpack 绑定。"""
    if frozenset(submitted) != frozenset(expected):
        raise ReviewerTaskError('Stage2 review-decisions 顶层字段必须逐字保留模板')
    if submitted.get('actor') != expected.get('actor'):
        raise ReviewerTaskError('Stage2 review-decisions.actor 必须逐字保留模板')
    submitted_rows = submitted.get('decisions')
    expected_rows = expected.get('decisions')
    if not isinstance(submitted_rows, list) or not isinstance(expected_rows, list):
        raise ReviewerTaskError('Stage2 review-decisions.decisions 必须是数组')
    expected_ids = [
        (row.get('item_id'), row.get('expected_subject_sha256'))
        for row in expected_rows
    ]
    submitted_ids = [
        (row.get('item_id'), row.get('expected_subject_sha256'))
        for row in submitted_rows
    ]
    if submitted_ids != expected_ids:
        raise ReviewerTaskError(
            'Stage2 review-decisions 的 item_id/expected_subject_sha256 '
            '必须逐字保留模板')
    for index, (submitted_row, expected_row) in enumerate(
            zip(submitted_rows, expected_rows, strict=True)):
        if frozenset(submitted_row) != frozenset(expected_row):
            raise ReviewerTaskError(
                f'Stage2 review-decisions.decisions[{index}] 字段集合必须逐字保留模板')
        expected_disposition = expected_row.get('generation_disposition')
        submitted_disposition = submitted_row.get('generation_disposition')
        if expected_disposition is None:
            if submitted_disposition is not None:
                raise ReviewerTaskError(
                    '当前 review 选项没有提供 generation_disposition')
        else:
            if not isinstance(submitted_disposition, dict):
                raise ReviewerTaskError('隔离选项必须保留 generation_disposition')
            if frozenset(submitted_disposition) != frozenset(expected_disposition):
                raise ReviewerTaskError('generation_disposition 字段集合必须逐字保留模板')
            if submitted_disposition.get('action') != expected_disposition.get('action'):
                raise ReviewerTaskError('generation_disposition.action 必须逐字保留模板')
            if submitted_row.get('state') != expected_row.get('state'):
                raise ReviewerTaskError('隔离选项的 UNVER state 必须逐字保留模板')
    submitted_scope = submitted.get('blocking_scope')
    expected_scope = expected.get('blocking_scope')
    if expected_scope is None:
        if submitted_scope is not None:
            raise ReviewerTaskError('当前 review 选项没有提供 blocking_scope')
        return
    if not isinstance(expected_scope, dict) or not isinstance(submitted_scope, dict):
        raise ReviewerTaskError('Stage2 blocking_scope 必须逐字保留模板形状')
    if frozenset(submitted_scope) != frozenset(expected_scope):
        raise ReviewerTaskError('Stage2 blocking_scope 字段集合必须逐字保留模板')
    editable = frozenset({'state', 'reason', 'evidence'})
    for key, value in expected_scope.items():
        if key not in editable and submitted_scope.get(key) != value:
            raise ReviewerTaskError(f'Stage2 blocking_scope.{key} 必须逐字保留模板')


def correction_prompt(task: ReviewerTask, reason: str, *, attempt: int) -> str:
    """同一 provider session 的紧凑纠错提示；不重复发送整份 workpack。"""
    return f"""上一轮 typed request 已在正式产物写入前被确定性边界拒绝，没有修改正式产物。
继续使用本会话已经读过的同一 immutable workpack，不要重新定位、不要重读仓库，
也不要启动任何节点或 Executor。只纠正被拒绝的 typed 动作，并覆盖原请求文件：
{task.request_path}

拒绝原因：{reason}
这是第 {attempt} 次纠正。仍须从原 workpack 的 submission_options 选择恰好一项，
逐字保留 binding_fields，只修改 editable_fields。Stage2 patch 不能新增、删除或改名
模板已有 subject；若需要新增事实，先完成当前 patch，之后在集合完整性复审中判 FAIL，
等待新模板显式给出完整 *_replace。只写上述 JSON 文件，完成后退出。
"""


def load_action(
    path: str | os.PathLike[str],
    current: OperatorNext,
    *,
    submission_options: Sequence[dict[str, Any]] | None = None,
) -> ReviewerAction:
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
            expected_action = _stage2_submission_template(
                body, submission_options)
            operations = [
                flag for flag in ('--review-decisions', '--apply-edit')
                if flag in flags
            ]
            if len(operations) != 1 or input_payload is None:
                raise ReviewerTaskError(
                    'stage2.global reviewer action 必须恰好携带 review-decisions '
                    '或 apply-edit typed payload')
            operation = operations[0]
            marker = flags.index(operation) + 1
            if marker >= len(flags) or flags[marker] != '@reviewer-input':
                raise ReviewerTaskError(
                    f'{operation} 的值必须是 @reviewer-input 占位符')
            if operation == '--review-decisions':
                try:
                    logical_payload = GlobalReviewDecisionSet.model_validate(
                        input_payload).model_dump(mode='json')
                except ValidationError as exc:
                    raise ReviewerTaskError(
                        'stage2.global review-decisions 不符合 typed 契约') from exc
                _validate_global_review_binding(
                    input_payload, expected_action['input_payload'])
                artifact = STAGE2_GLOBAL_REVIEW_DECISIONS
            else:
                try:
                    logical_payload = GlobalEdit.model_validate(
                        input_payload).model_dump(
                            mode='json', by_alias=True, exclude_unset=True)
                except ValidationError as exc:
                    raise ReviewerTaskError(
                        'stage2.global apply-edit 不符合 GlobalEdit typed 契约') from exc
                _validate_global_edit_binding(
                    input_payload, expected_action['input_payload'])
                artifact = STAGE2_GLOBAL_EDIT
            # Reviewer 编辑的是业务字段；真正交给 CLI 的外部文件仍须使用注册表
            # 版本信封。版本标记由可信 loader 重建，不交给 Commander 手搬或修改。
            input_payload = encode_artifact(artifact, logical_payload)
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
            source_payload = (
                _canonicalize_design_review_payload(body['payload'])
                if surface == 'design' else body['payload']
            )
            payload = models[surface].model_validate(source_payload).model_dump(
                mode='json')
        except (KeyError, ValidationError) as exc:
            raise ReviewerTaskError(
                f'{surface} reviewer payload 不符合 typed 契约') from exc
        if (
            surface == 'design'
            and waiting_on in {
                queue.WAITING_ON_STAGE4_BINDING_RESOLUTION,
                queue.WAITING_ON_STAGE4_STRUCTURE_REPLAN,
            }
            and payload['strategy']['decision'] == 'accept'
            and all(
                row['decision'] == 'accept' for row in payload['chapters']
            )
        ):
            raise ReviewerTaskError(
                'Stage4 resolution 不能原样提交 all-accept；必须形成至少一项 '
                'typed request_repair 或 request_replan')
        return ReviewerApprovalAction(
            kind='approval', surface=surface, payload=payload)
    raise ReviewerTaskError('reviewer request.action.kind 只能是 node 或 approval')


__all__ = [
    'approval_review_token', 'replay_request_document',
    'ReviewerAction', 'ReviewerApprovalAction', 'ReviewerNodeAction',
    'ReviewerTask', 'ReviewerTaskError', 'action_business_sha256',
    'build_task', 'correction_prompt', 'is_registered', 'load_action',
]
