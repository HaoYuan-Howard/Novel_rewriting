"""只读确认 Reviewer 的精确节点批准已由正式 producer 落盘。

此口只补 canonical 批准落盘到工作单元 mark_adopted 之间的尾窗。它不授予审批、
不把当前 gate 当作原动作证明，也不重建旧工作包。原 token 对照批准时保存的
workpack SHA，当前适用性仍由 producer 的主体与 stable review identity 判据复验。
"""
from __future__ import annotations

from pathlib import Path

from contracts.invariants import slot_of
from contracts.schema.stage6 import Hook
from contracts.schema.stage7 import NameMap
from novel import paths
from novel.errors import ArtifactUnreadable
from novel.orchestrator.state import readonly_ctx
from novel.store import ArtifactStore


def _values(flags: tuple[str, ...], keys: frozenset[str]) -> dict[str, str] | None:
    """关闭式读取 canonical flag 对；缺失、重复或额外参数均不能认领。"""
    if len(flags) != len(keys) * 2:
        return None
    values: dict[str, str] = {}
    for key, value in zip(flags[::2], flags[1::2], strict=True):
        if key not in keys or key in values or not isinstance(value, str):
            return None
        if not value.strip():
            return None
        values[key] = value
    return values if frozenset(values) == keys else None


def approval_action_landed(
    base: str | Path, sid: str, *, node: str, flags: tuple[str, ...], code: str,
) -> bool:
    """原动作与正式批准逐字段一致才返回 True；未知/损坏不转成已落盘。

不支持的节点、缺产物或历史批准缺持久身份返回 False，由原恢复流程继续处理。
读盘、schema、producer 校验错误原样上抛，调用方须保留工作单元与原回答证据。
    """
    keys = {
        'stage0.approve': frozenset({'--expected-revision-id', '--actor', '--reason'}),
        'stage6.approve': frozenset({'--pick', '--actor', '--reason', '--review-token'}),
        'stage7.approve': frozenset({'--actor', '--reason', '--review-token'}),
    }.get(node)
    if keys is None:
        return False
    values = _values(flags, keys)
    if values is None:
        return False
    book = paths.find_novel_dir(base, sid)
    if book is None:
        return False
    store = ArtifactStore(readonly_ctx(book, node, code))
    actor, reason = values['--actor'].strip(), values['--reason'].strip()
    if node == 'stage0.approve':
        from novel.stages.stage0 import removal, revisions

        log = revisions.read(store)
        if log is None or log.current.approval.status != 'approved':
            return False
        approval = log.current.approval
        if (log.current.revision_id != values['--expected-revision-id']
                or approval.by != actor or approval.reason != reason):
            return False
        removal._require_stage0_fresh(store)
        if revisions.read(store) != log:
            raise ArtifactUnreadable('Stage0 批准在已落查询期间变化，不能认领原决定')
        return True

    if node == 'stage6.approve':
        from novel.stages.stage6 import hook

        if values['--pick'] not in {'1', '2', '3'} or not store.exists(hook.REL):
            return False
        art, _ = store.read_own(Hook, hook.REL)
        approved = art.approved
        if approved is None or approved.slot != slot_of(int(values['--pick'])):
            return False
        resolution = approved.resolution
        if (resolution.actor != actor or resolution.reason != reason
                or resolution.workpack_sha256 is None
                or resolution.review_identity_sha256 is None
                or values['--review-token'] != f's6r4_{resolution.workpack_sha256}'):
            return False
        pack = hook.approval_workpack(store)
        selected = next((row for row in pack.candidates if row.slot == approved.slot), None)
        if (selected is None or selected.text != approved.text
                or selected.subject_sha256 != resolution.subject_sha256
                or selected.mech != approved.mech or selected.findings != approved.findings
                or pack.current_approval != resolution
                or not hook.approval_is_current(store, art)):
            return False
        rebound, _ = store.read_own(Hook, hook.REL)
    else:
        from novel.stages.stage7 import rename

        if not store.exists(rename.REL):
            return False
        art, _ = store.read_own(NameMap, rename.REL)
        approved = art.approved
        if (approved is None or approved.actor != actor or approved.reason != reason
                or approved.workpack_sha256 is None
                or approved.review_identity_sha256 is None
                or values['--review-token'] != f's7r3_{approved.workpack_sha256}'):
            return False
        pack = rename.approval_workpack(store)
        if (pack.current_approval != approved
                or pack.decision_subject_sha256 != approved.subject_sha256
                or not rename.approval_is_current(store, art)):
            return False
        rebound, _ = store.read_own(NameMap, rename.REL)
    if rebound != art:
        raise ArtifactUnreadable(f'{node} 批准在已落查询期间变化，不能认领原决定')
    return True
