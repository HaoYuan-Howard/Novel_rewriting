#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""当前三层跨节点回派：严格V3账、有界修订、发现节点免费复验清账。

只持久化显式定位与归属，不判断故事语义；指纹与累计轮数跨重启保留。
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable, NoReturn

from contracts.schema.source_story import STORY_VERSION
from contracts.invariants import CASCADE_REWIND_LIMIT, OWNER_REVISION_MAX_ROUNDS
from contracts.schema.workflow import (
    OWNER_PRODUCT_RELS, OwnerIssueFileV3, OwnerIssueV1,
    owner_issue_record_of_payload, owner_issues_rel, owner_issue_node,
)
from novel.llm.paid_requests import ExecutionError, revision_user_prompt
from contracts.invariants import NODES

__all__ = [
    'MechanicalDefectStop', 'OwnerRevisionRequired', 'OwnerRevisionStop',
    'discover_owner_issues', 'open_issue_record', 'open_issue_targets_node',
    'owner_product_digest', 'owner_product_digest_at', 'read_owner_issue_file',
    'resolve_owner_issues', 'complete_owner_dispatch', 'revision_gate', 'revision_prompt_for_owner',
]


class OwnerRevisionRequired(ValueError):
    """跨节点回派：下游已登记 typed 问题，要求上游 owner 修订重跑。

    ``node`` 是修订目标节点 id（必须在执行序上早于发现节点）；driver／编排
    据此把执行位置回跳到该节点。承载消息含问题清单（按 item 定位）。
    """

    def __init__(self, message: str, *, node: str, owner: str,
                 issues: tuple[OwnerIssueV1, ...]):
        super().__init__(message)
        self.node = node
        self.owner = owner
        self.issues = tuple(issues)
        # 仅用于诊断留证，不参与回派控制；完整候选报告在此存一次，
        # 不按issue重复进入后继语义请求。
        self.context: dict = {}


class OwnerRevisionStop(ExecutionError):
    """修订停止：同一问题集重复（无进展）或轮额度耗尽——按 item 停给人。"""

    def __init__(self, message: str, *, node: str, owner: str,
                 issues: tuple[OwnerIssueV1, ...], raised_by: str = '',
                 rounds: int = 0, no_progress: bool = False):
        detector = raised_by or node
        super().__init__(message,
                         code='owner_revision_no_progress' if no_progress else 'owner_revision_exhausted',
                         stage=NODES[detector].stage, node=detector, item=owner,
                         phase='accept', context={
                             'owner': owner, 'target_node': node, 'rounds': rounds,
                             'issues': [i.model_dump(mode='json') for i in issues]})
        self.target_node = node
        self.owner = owner
        self.issues = tuple(issues)


class MechanicalDefectStop(ExecutionError):
    """机械缺陷停止：确定性编译查出形状/闭集/时序缺陷，且没有回派路由。

    缺少明确修订责任或授权范围时，保留确定性失败并停止，不猜 owner 或扩大修订。
    CLI 以 typed negative 区分“已经检查且未通过”与“执行结论未知”，
    错误上下文随运行证据留存，不触发新的付费调用。
    """

    def __init__(self, message: str, *, node: str, issues: tuple[str, ...], item: str = 'book'):
        super().__init__(message, code='mechanical_defect', stage=NODES[node].stage,
                         node=node, item=item, phase='accept',
                         context={'issues': list(issues)})
        self.issues = tuple(issues)


def _messages(issues: tuple[OwnerIssueV1, ...] | list[OwnerIssueV1]) -> list[str]:
    import json
    return [f'{issue.code}[{issue.item}]：{issue.message}；context='
            + json.dumps(issue.context, ensure_ascii=False, sort_keys=True)
            for issue in issues]


def _issue_key(issue: OwnerIssueV1) -> tuple[str, str, str]:
    """问题身份取明确定位/材料身份；人读文案和坏值变化不构成修订进展。"""
    import json

    location = {key: issue.context[key] for key in ('path', 'paths', 'append_paths', 'remove_paths',
        'operation', 'component', 'event_no', 'section', 'item_no', 'material_identity',
        'validation_identity') if key in issue.context}
    detail = json.dumps(location, ensure_ascii=False, sort_keys=True)
    return issue.code, issue.item, detail


def _distinct(issues: tuple[OwnerIssueV1, ...] | list[OwnerIssueV1]) -> tuple[OwnerIssueV1, ...]:
    """按稳定身份去重（保序、留首）：同一材料问题在多处命中只算一个问题。

    此前每处命中各占一行：同一个根因的 N 处出现既写进回派账、又逐行进上游返修提示词，
    反馈越长越不聚焦（csf4 根的 Design 反馈 321 行实为一个根因）。身份与 `_fingerprint`
    同一把尺，所以去重不改变无进展判定。
    """
    seen: set[tuple[str, str, str]] = set()
    kept: list[OwnerIssueV1] = []
    for issue in issues:
        key = _issue_key(issue)
        if key not in seen:
            seen.add(key)
            kept.append(issue)
    return tuple(kept)


def _fingerprint(issues: tuple[OwnerIssueV1, ...] | list[OwnerIssueV1]) -> str:
    """问题集摘要：稳定材料身份或存量消息去重排序后的 SHA-256（纯函数）。"""
    import hashlib
    import json

    material = sorted({_issue_key(i) for i in issues})
    return hashlib.sha256(json.dumps(material, ensure_ascii=False,
                                     separators=(',', ':')).encode('utf-8')
                          ).hexdigest()


def owner_product_digest_at(root: Path, owner: str) -> str:
    """owner 正式产物（`OWNER_PRODUCT_RELS`；逐章 owner 走
    `chapter_owner_product_rel`）当前字节的 SHA-256（R8a 门控的尺子）。

    未登记 owner、产物不在盘上都是编排缺陷（issue 只能由读过该产物的下游写
    下）——显式拒绝，不猜摘要。
    """
    from contracts.schema.workflow import chapter_owner_product_rel
    rel = OWNER_PRODUCT_RELS.get(owner) or chapter_owner_product_rel(owner)
    if rel is None:
        raise ValueError(
            f'owner {owner!r} 未登记正式产物（闭集={sorted(OWNER_PRODUCT_RELS)}'
            '＋逐章 chNN）')
    path = Path(root) / rel
    if not path.is_file():
        raise ValueError(
            f'owner {owner!r} 的正式产物 {rel} 不在盘上，记不了 against 摘要')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def owner_product_digest(repository, owner: str) -> str:
    """`owner_product_digest_at` 的 repository 版（写口与读口共用同一把尺）。"""
    return owner_product_digest_at(repository.root, owner)


def revision_gate(against_sha256: str, product_sha256: str | None,
                  rounds_total: int, no_progress: bool = False, *, dispatch_pending: bool = False) -> tuple[str, str]:
    """owner_revision 门控的唯一判定（R8a）：进程内回跳与 `resume_verdict` 共用。

    返回 ``(结论, 一句话)``，结论闭集：
    · ``redo``——owner 产物未变（摘要＝against）且轮未耗尽：按 owner 修订重跑；
    · ``wait_reverify``——产物已变（修订写回或人工 patch）：不再回头重买，
      等下游按新鲜度复验清账（``product_sha256`` 为 None——产物不在盘上——
      同此档，交常规存在性分支）；
    · ``stopped``——轮额度耗尽、或同指纹重现（``no_progress``，修订被证明
      无效）且产物未变：按 item 停给人，不自动重买。
    """
    if product_sha256 == against_sha256:
        if (rounds_total < OWNER_REVISION_MAX_ROUNDS or dispatch_pending) and not no_progress:
            return 'redo', (
                f'owner 产物未变（摘要＝against），跨节点回派修订轮 '
                f'{rounds_total}/{OWNER_REVISION_MAX_ROUNDS}——按 owner 修订重跑')
        if no_progress:
            return 'stopped', (
                '修订后同一问题集原样重现（no_progress，修订被证明无效）且 '
                'owner 产物未变——按 item 停给人（owner patch 或人工重跑），'
                '不自动重买')
        return 'stopped', (
            f'跨节点回派修订轮 {rounds_total}/{OWNER_REVISION_MAX_ROUNDS} 已耗尽'
            '且 owner 产物未变——按 item 停给人（owner patch 或人工重跑），'
            '不自动重买')
    return 'wait_reverify', (
        'owner 产物摘要已变（against 之后有修订写回或人工 patch）——不再回头'
        '重买 owner，等下游按新鲜度复验（通过置 resolved，再发现按轮次规则）')


def open_issue_targets_node(repository, node: str) -> bool:
    """盘上是否有 open 的回派账指向该节点（run_step 追跑闸等编排面用，只读）。

    修订轮的请求身份含「上一版全文」：owner 写回后，自动追跑的 `--check-only`
    会推导出**未购买的新身份**（缓存必 miss）。修订循环的验证权威是提出问题的
    下游（compile 复验清账），不是 owner 自己的免费复检——open 期间跳过追跑。
    """
    directory = repository.path('runtime/issues')
    for path in sorted(directory.glob('*.json')) if directory.is_dir() else ():
        record = read_owner_issue_file(repository, path.stem)
        if record is not None and record.status == 'open' \
                and record.node == node:
            return True
    return False


def read_owner_issue_file(repository, owner: str) -> OwnerIssueFileV3 | None:
    """只读当前显式V3记录；损坏、旧版或文件owner错配一律拒绝。"""
    import json as _json

    path = repository.path(owner_issues_rel(owner))
    if not path.is_file():
        return None
    payload = _json.loads(path.read_text(encoding='utf-8'))
    try:
        record = owner_issue_record_of_payload(payload)
        if record.owner != owner:
            raise ValueError('回派账文件名与owner不一致')
        return record
    except ValueError as exc:
        raise ValueError(
            f'{owner} 的回派账 {owner_issues_rel(owner)} 形状损坏：{exc}；'
            '它是修订路由的输入，拒绝当作「没有问题」继续') from exc


def open_issue_record(repository, owner: str) -> OwnerIssueFileV3 | None:
    """当前仍 open 的回派账（上游 owner 重跑的修订上下文；resolved 不算）。"""
    record = read_owner_issue_file(repository, owner)
    if record is None or record.status != 'open':
        return None
    return record


def discover_owner_issues(repository, *, raised_by: str, owner: str, node: str,
                          issues: list[OwnerIssueV1] | tuple[OwnerIssueV1, ...],
                          ) -> NoReturn:
    """下游登记 typed 问题并要求回派（唯一写入口；必然抛错）。

    · 首见（无 open 同指纹记录）且额度未尽：写 open 记录（``rounds_total`` +1）
      并抛 :class:`OwnerRevisionRequired`；
    · 同指纹重现：修订没带来进展——按 item 停（:class:`OwnerRevisionStop`），
      记录保持原轮数（最新问题集仍写盘留证）；
    · ``rounds_total`` 已达上限：额度耗尽停给人（owner patch／人工重跑）。

    三条路径都记 ``against_sha256``＝本次判定时的 owner 产物摘要（R8a）：调度
    门控据此区分「owner 还没动」与「owner 已改稿等复验」。

    幂等对账（I-30/I-31）：崩溃在写盘后、修订前，重放会读到同一 open 记录并
    得到同一停止结论，不重复起轮。
    """
    if not issues:
        raise ValueError('discover_owner_issues 要求非空 issue 清单（空清单不是问题）')
    if owner_issue_node(owner) != node or raised_by not in NODES:
        raise ValueError('回派owner/node/raised_by不属于当前契约')
    issues = _distinct(issues)
    existing = read_owner_issue_file(repository, owner)
    fingerprint = _fingerprint(issues)
    against_sha256 = owner_product_digest(repository, owner)
    updated_at = _now()
    history = tuple(dict.fromkeys((*existing.fingerprint_history, existing.fingerprint))) if existing else ()
    retained = {'fingerprint_history': tuple(dict.fromkeys((*history, fingerprint))),
                'corrections': existing.corrections if existing else ()}
    if existing is not None and existing.status == 'open' and existing.dispatch_pending \
            and existing.fingerprint == fingerprint and existing.against_sha256 == against_sha256:
        raise OwnerRevisionRequired('重放已登记但尚未完成的回派；不重复起轮',
                                    node=node, owner=owner, issues=issues)
    if existing is not None and existing.status == 'open' \
            and fingerprint in history:
        repository.write(owner_issues_rel(owner), OwnerIssueFileV3(
            contract_version=STORY_VERSION,
            owner=owner, node=node, raised_by=raised_by, status='open',
            rounds_total=existing.rounds_total, fingerprint=fingerprint,
            against_sha256=against_sha256, no_progress=True, issues=issues,
            updated_at=updated_at, dispatch_pending=False, **retained))
        raise OwnerRevisionStop(
            f'跨节点回派修订轮·{raised_by} 的修订要求没有进展：{owner} 修订后'
            f'同一问题集原样重现，按 item 停（同一问题集重复即停）：{_messages(issues)}',
            node=node, owner=owner, issues=issues, raised_by=raised_by,
            rounds=existing.rounds_total, no_progress=True)
    rounds = existing.rounds_total if existing is not None else 0
    if rounds >= OWNER_REVISION_MAX_ROUNDS:
        repository.write(owner_issues_rel(owner), OwnerIssueFileV3(
            contract_version=STORY_VERSION,
            owner=owner, node=node, raised_by=raised_by, status='open',
            rounds_total=rounds, fingerprint=fingerprint,
            against_sha256=against_sha256, no_progress=False, issues=issues,
            updated_at=updated_at, dispatch_pending=False, **retained))
        raise OwnerRevisionStop(
            f'{owner} 的跨节点回派修订轮额度（{OWNER_REVISION_MAX_ROUNDS}）已耗尽，'
            f'按 item 停给人（剩余修复走 owner patch 或人工重跑）：'
            f'{_messages(issues)}',
            node=node, owner=owner, issues=issues, raised_by=raised_by, rounds=rounds)
    # R10c 硬化（2026-10-02）：跨节点回派每根累计上界——单 owner 轮上限防不住
    # 多 owner 互相打断对方收敛的级联身份 churn（任一修订改变下游请求身份→
    # 重购→额度尽→grant→另一 owner 再修→再 churn）。超界停给人，不隐式付费。
    rewinds = _bump_cascade_rewinds(repository)
    if rewinds > CASCADE_REWIND_LIMIT:
        raise OwnerRevisionStop(
            f'跨节点回派累计 {rewinds - 1} 次已超每根上界 '
            f'（{CASCADE_REWIND_LIMIT}）——多 owner 修订在互相打断对方收敛'
            '（级联身份 churn），按 item 停给人裁决；本记录未起轮、未付费',
            node=node, owner=owner, issues=issues, raised_by=raised_by,
            rounds=rounds)
    repository.write(owner_issues_rel(owner), OwnerIssueFileV3(
            contract_version=STORY_VERSION,
        owner=owner, node=node, raised_by=raised_by, status='open',
        rounds_total=rounds + 1, fingerprint=fingerprint,
        against_sha256=against_sha256, issues=issues,
        updated_at=updated_at, dispatch_pending=True, **retained))
    raise OwnerRevisionRequired(
        f'{raised_by} 发现 {len(issues)} 项 typed 机械问题，回派 {node} 按 owner '
        f'修订重跑（跨节点回派修订轮 {rounds + 1}/{OWNER_REVISION_MAX_ROUNDS}；'
        f'本根累计回派 {rewinds}/{CASCADE_REWIND_LIMIT}）：'
        f'{_messages(issues)}',
        node=node, owner=owner, issues=issues)


def _bump_cascade_rewinds(repository) -> int:
    """每根跨节点回派计数的持久 +1（`runtime/cascade_rewinds.json`）。

    只在实际要起一轮新回派前调用；同指纹重现／轮额耗尽的停止路径不计数。
    """
    rel = 'runtime/cascade_rewinds.json'
    try:
        current = int((repository.read(rel) or {}).get('count', 0))
    except FileNotFoundError:
        current = 0
    current += 1
    if current <= CASCADE_REWIND_LIMIT:
        repository.write(rel, {'count': current,
                               'limit': CASCADE_REWIND_LIMIT})
    return current


def resolve_owner_issues(repository, *, owner: str, raised_by: str) -> None:
    """下游复验通过：open 记录置 resolved（轮数、指纹与 against 摘要留账，不复位）。"""
    record = read_owner_issue_file(repository, owner)
    if record is None or record.status != 'open' or record.raised_by != raised_by:
        return
    repository.write(owner_issues_rel(owner), record.model_copy(update={
        'status': 'resolved', 'dispatch_pending': False, 'raised_by': raised_by, 'updated_at': _now()}))


def complete_owner_dispatch(repository, node: str, *, chapter_owners=()) -> None:
    """正式提交后关闭本轮待执行标志；同字节空转也不能在重启时冒充尚未执行。"""
    for owner in (*OWNER_PRODUCT_RELS, *chapter_owners):
        record = open_issue_record(repository, owner)
        if record is None or record.node != node or not record.dispatch_pending:
            continue
        unchanged = owner_product_digest(repository, owner) == record.against_sha256
        repository.write(owner_issues_rel(owner), record.model_copy(update={
            'dispatch_pending': False, 'no_progress': unchanged, 'updated_at': _now()}))


def revision_prompt_for_owner(repository, owner: str, base_prompt: str,
                              previous_rel: str, *,
                              previous_wire: Callable[[dict], dict] | None = None) -> str:
    """上游 owner 的修订 prompt：open 记录在案时并入上一版与 typed 问题。

    ``previous_rel`` 是该 owner 自己的正式产物（修订对象上一版）；无 open 记录
    时原样返回 base_prompt（普通首跑/重放路径逐字节不变，请求身份不漂移）。
    owner 可传纯函数 ``previous_wire`` 撤销派生项，保持原 wire 作为修订模板；
    未提供投影时正式产物仅供只读语义对照，明确禁止仿照 canonical 输出。
    嵌入的上一版**剥掉存储信封**（`_provenance`／`_schema_version`）：信封是
    工程元数据，喂给模型会被原样回声，wire 校验 extra_forbidden 拒收烧轮
    （R10c c1 ch05 实证：第 4 轮因此耗尽）。
    """
    record = open_issue_record(repository, owner)
    if record is None:
        return base_prompt
    previous = _stored_payload_text(repository, previous_rel, project=previous_wire)
    return revision_user_prompt(base_prompt, previous, _messages(record.issues),
                                previous_is_canonical=previous_wire is None,
                                revision_rules=(
                                    '只修issues.context.path明确授权的条目；原文证据与任务schema是约束。'
                                    '保持未涉问题的语义；若修复需要修改其他owner，返回该节点明示的上游问题协议。'
                                    '不要新增机械ID、hash、统计或canonical字段。'))


def _stored_payload_text(repository, rel: str, *,
                         project: Callable[[dict], dict] | None = None) -> str:
    """读存储产物并剥信封，返回模型应当看到的上一版正文（损坏即原样拒绝）。"""
    import json as _json
    raw = repository.path(rel).read_text(encoding='utf-8')
    try:
        envelope = _json.loads(raw)
    except ValueError:
        raise RuntimeError(f'修订上一版不是 JSON：{rel}')
    if not isinstance(envelope, dict):
        raise RuntimeError(f'修订上一版不是 JSON 对象：{rel}')
    if envelope.get('contract_version') != STORY_VERSION:
        raise RuntimeError(f'修订上一版不属于当前story-v3：{rel}')
    payload = envelope
    stripped = {k: v for k, v in payload.items() if not k.startswith('_')}
    if project is not None:
        stripped = project(stripped)
    return _json.dumps(stripped, ensure_ascii=False, indent=2)


def _now() -> int:
    import time

    return int(time.time())
