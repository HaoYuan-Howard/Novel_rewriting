#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""owner_revision —— R3 跨节点回派：typed issue 记录、有界修订与复验清账。

【只修 typed、可定位的机械问题（2026-09-28 裁决 1）】
  • 下游确定性节点（`run_compile`／`run_compile_stage4`）发现路由表登记的问题
    时，经 :func:`discover_owner_issues` 写 ``runtime/issues/<owner>.json`` 并抛
    :class:`OwnerRevisionRequired`；无进展（同问题集指纹原样重现）或修订轮
    额度耗尽时抛 :class:`OwnerRevisionStop`——按 item 停、不无限付费。
  • 上游 owner 重跑时经 :func:`open_issue_record` 读 open 记录，把
    （原请求＋上一版＋typed 问题）并入修订 prompt——形状与
    `novel/llm/paid_requests.py::ask_json` 的同节点修订内容轮一致；修订要求进
    prompt 即进请求身份（修订轮是新付费调用，崩溃后按身份免费重放）。
  • 下游复验通过后经 :func:`resolve_owner_issues` 置 ``status='resolved'``；
    ``rounds_total`` 留账不复位（「每单元最多 2 轮」是终身上限，耗尽停给人）。

【R8a（2026-09-29 canary 实证）：记录增 ``against_sha256``，写读同批升 v2】
  • 每次发现都记下判定时 owner 正式产物（`OWNER_PRODUCT_RELS`）的字节摘要；
    调度门控（`resume_verdict` 的 owner_revision 分支）只在当前摘要仍等于它时
    判修订 REDO——owner 修订写回后 issue 仍 open，若照旧 REDO 会在「改退出码
    后反复重买 owner」里打转（B6-A canary 根实证）。
  • v1 盘上记录经 :func:`read_owner_issue_file` 集中迁移（迁移登记与删除条件
    见 `contracts/schema/workflow.py::owner_issue_record_of_payload`）。
  • 报错与监控里的三类上限各自分名（R8a-4），不再共用「修订轮」一个词：
    **节点内校验重问**（`ask_json` 同 wire 修订，≤OWNER_REVISION_MAX_ROUNDS）、
    **跨节点回派修订轮**（本模块，每 owner 终身）、**工作单元内容轮额度**
    （`novel/work_units.py` 的 reserve，耗尽走 grant 追加）。

【边界】路由闭集（code 轴与 unbound.owner 轴）唯一登记在
`contracts/schema/workflow.py`；本模块不判断文学质量、不修复语义、不猜测
路由——未知 code／未映射 owner 一律停。issue 记录是 runtime 状态，不是版本化
书产物；成本权威仍是 `<base>/_service/ledger.jsonl`（修订轮经付费边界外发，
逐 attempt 入账，I-17）。
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import NoReturn

from contracts.invariants import CASCADE_REWIND_LIMIT, OWNER_REVISION_MAX_ROUNDS
from contracts.schema.workflow import (
    OWNER_PRODUCT_RELS, OwnerIssueFileV2, OwnerIssueV1,
    owner_issue_record_of_payload, owner_issues_rel,
)
from novel.llm.paid_requests import ExecutionError, revision_user_prompt
from contracts.invariants import NODES

__all__ = [
    'MechanicalDefectStop', 'OwnerRevisionRequired', 'OwnerRevisionStop',
    'discover_owner_issues', 'open_issue_record', 'open_issue_targets_node',
    'owner_product_digest', 'owner_product_digest_at', 'read_owner_issue_file',
    'resolve_owner_issues', 'revision_gate', 'revision_prompt_for_owner',
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

    与 :class:`OwnerRevisionStop` 的区别：这类缺陷（如首现闸
    ``participant_before_first_appearance``，R8c）不登记回派 owner——
    恢复走该章观察重问或 owner patch（人工修订 wire），不自动重跑。
    CLI 把它翻译成 exit 1（typed negative：查出确定性缺陷＝跑完且结论
    落盘不通过），stderr 全文随 `_service/logs/<job>.log` 留证，编排器按
    landed_red 停给人。B6-A canary 20260930 实证：裸 ``ValueError`` 穿透成
    exit 70（「结论未知」），把「查出了确定性缺陷」记成「压根没跑成」。
    """

    def __init__(self, message: str, *, node: str, issues: tuple[str, ...], item: str = 'book'):
        super().__init__(message, code='mechanical_defect', stage=NODES[node].stage,
                         node=node, item=item, phase='accept',
                         context={'issues': list(issues)})
        self.issues = tuple(issues)


def _messages(issues: tuple[OwnerIssueV1, ...] | list[OwnerIssueV1]) -> list[str]:
    return [f'{issue.code}[{issue.item}]：{issue.message}' if issue.item
            else f'{issue.code}：{issue.message}' for issue in issues]


def _fingerprint(issues: tuple[OwnerIssueV1, ...] | list[OwnerIssueV1]) -> str:
    """问题集摘要：sorted (code, item, message) 的规范化 SHA-256（纯函数）。"""
    import hashlib
    import json

    material = sorted((i.code, i.item, i.message) for i in issues)
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
                  rounds_total: int, no_progress: bool = False) -> tuple[str, str]:
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
        if rounds_total < OWNER_REVISION_MAX_ROUNDS and not no_progress:
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
    for owner in OWNER_PRODUCT_RELS:
        record = read_owner_issue_file(repository, owner)
        if record is not None and record.status == 'open' \
                and record.node == node:
            return True
    return False


def read_owner_issue_file(repository, owner: str) -> OwnerIssueFileV2 | None:
    """读该 owner 的回派账（统一升 v2 形状）；无记录返回 None，损坏即显式拒绝。

    v1 盘上记录按登记迁移补 ``against_sha256``（迁移语义与删除条件见
    `contracts/schema/workflow.py::owner_issue_record_of_payload`）。
    """
    import json as _json

    path = repository.path(owner_issues_rel(owner))
    if not path.is_file():
        return None
    payload = _json.loads(path.read_text(encoding='utf-8'))
    try:
        return owner_issue_record_of_payload(
            payload, lambda record_owner:
                owner_product_digest(repository, record_owner))
    except ValueError as exc:
        raise ValueError(
            f'{owner} 的回派账 {owner_issues_rel(owner)} 形状损坏：{exc}；'
            '它是修订路由的输入，拒绝当作「没有问题」继续') from exc


def open_issue_record(repository, owner: str) -> OwnerIssueFileV2 | None:
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
    issues = tuple(issues)
    existing = read_owner_issue_file(repository, owner)
    fingerprint = _fingerprint(issues)
    against_sha256 = owner_product_digest(repository, owner)
    updated_at = _now()
    if existing is not None and existing.status == 'open' \
            and existing.fingerprint == fingerprint:
        repository.write(owner_issues_rel(owner), OwnerIssueFileV2(
            owner=owner, node=node, raised_by=raised_by, status='open',
            rounds_total=existing.rounds_total, fingerprint=fingerprint,
            against_sha256=against_sha256, no_progress=True, issues=issues,
            updated_at=updated_at))
        raise OwnerRevisionStop(
            f'跨节点回派修订轮·{raised_by} 的修订要求没有进展：{owner} 修订后'
            f'同一问题集原样重现，按 item 停（同一问题集重复即停）：{_messages(issues)}',
            node=node, owner=owner, issues=issues, raised_by=raised_by,
            rounds=existing.rounds_total, no_progress=True)
    rounds = existing.rounds_total if existing is not None else 0
    if rounds >= OWNER_REVISION_MAX_ROUNDS:
        repository.write(owner_issues_rel(owner), OwnerIssueFileV2(
            owner=owner, node=node, raised_by=raised_by, status='open',
            rounds_total=rounds, fingerprint=fingerprint,
            against_sha256=against_sha256, no_progress=False, issues=issues,
            updated_at=updated_at))
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
    repository.write(owner_issues_rel(owner), OwnerIssueFileV2(
        owner=owner, node=node, raised_by=raised_by, status='open',
        rounds_total=rounds + 1, fingerprint=fingerprint,
        against_sha256=against_sha256, issues=issues,
        updated_at=updated_at))
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
    repository.write(rel, {'count': current,
                           'limit': CASCADE_REWIND_LIMIT})
    return current


def resolve_owner_issues(repository, *, owner: str, raised_by: str) -> None:
    """下游复验通过：open 记录置 resolved（轮数、指纹与 against 摘要留账，不复位）。"""
    record = read_owner_issue_file(repository, owner)
    if record is None or record.status != 'open':
        return
    repository.write(owner_issues_rel(owner), OwnerIssueFileV2(
        owner=record.owner, node=record.node, raised_by=raised_by,
        status='resolved', rounds_total=record.rounds_total,
        fingerprint=record.fingerprint,
        against_sha256=record.against_sha256,
        no_progress=record.no_progress, issues=record.issues,
        updated_at=_now()))


def revision_prompt_for_owner(repository, owner: str, base_prompt: str,
                              previous_rel: str) -> str:
    """上游 owner 的修订 prompt：open 记录在案时并入上一版与 typed 问题。

    ``previous_rel`` 是该 owner 自己的正式产物（修订对象上一版）；无 open 记录
    时原样返回 base_prompt（普通首跑/重放路径逐字节不变，请求身份不漂移）。
    嵌入的上一版**剥掉存储信封**（`_provenance`／`_schema_version`）：信封是
    工程元数据，喂给模型会被原样回声，wire 校验 extra_forbidden 拒收烧轮
    （R10c c1 ch05 实证：第 4 轮因此耗尽）。
    """
    record = open_issue_record(repository, owner)
    if record is None:
        return base_prompt
    previous = _stored_payload_text(repository, previous_rel)
    return revision_user_prompt(base_prompt, previous, _messages(record.issues))


def _stored_payload_text(repository, rel: str) -> str:
    """读存储产物并剥信封，返回模型应当看到的上一版正文（损坏即原样拒绝）。"""
    import json as _json
    raw = repository.path(rel).read_text(encoding='utf-8')
    try:
        envelope = _json.loads(raw)
    except ValueError:
        raise RuntimeError(f'修订上一版不是 JSON：{rel}')
    if isinstance(envelope, dict) and 'payload' in envelope:
        payload = {k: v for k, v in envelope['payload'].items()
                   if not k.startswith('_')}
        return _json.dumps(payload, ensure_ascii=False, indent=2)
    stripped = {k: v for k, v in envelope.items() if not k.startswith('_')}
    return _json.dumps(stripped, ensure_ascii=False, indent=2)


def _now() -> int:
    import time

    return int(time.time())
