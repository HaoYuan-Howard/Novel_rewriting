#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage0 的不可变 revision 链与原子发布。全程零 LLM。

【这个模块替掉了什么】2026-08-22 阶段 C 之前，本层用四套机制去保证
`clean_text.txt` 与旧 `removal_report.json` 同进同出：不可覆盖审计链（同时充当 WAL）、
`_rounds/` 逐轮归档、`machine_snapshot.json`、`merge_candidate.json` + 码点级 diff3。
它们守的是**同一个洞**——文件系统只保证单文件原子，而正式稿是两份文件。

⇒ 现在只有一份 JSON 权威：`stage0/revisions.json`。写它一次 = 提交一次。
   `clean_text.txt` 是它唯一的**物化投影**（`materialize`，幂等纯函数）：
   崩在写完 revisions 之后、物化完成之前，下一条写路径重新物化一次即可自愈，
   不需要 WAL、不需要「唯一可恢复条目」、不需要崩溃重放状态机。

运行时报告由 `snapshot_of()` 现算，不落盘；它的 code 仍取产出 current 内容的 revision。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from contracts.artifacts import STAGE0_CLEAN_TEXT, STAGE0_REVISIONS
from contracts.schema.stage0 import (
    Stage0Snapshot,
    Stage0Current,
    Stage0Revision,
    Stage0RevisionContent,
    Stage0RevisionOrigin,
    Stage0Revisions,
)
from novel import prov
from novel.core.source import source_filename
from novel.core.warnings import stage0_warnings
from novel.errors import ArtifactUnreadable, HumanEditProtected
from novel.store import ArtifactStore

REVISIONS_REL = STAGE0_REVISIONS
CLEAN_REL = STAGE0_CLEAN_TEXT


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(value: object) -> str:
    """本仓统一的前像口径：UTF-8 · sort_keys · 紧凑分隔符 · 不做 NFC · SHA-256。

    ⚠ 与 `docs/schema/stage0.md` §2.3 的身份定义对应，golden vector 钉在
    `tests/test_stage0_revisions.py::test_revision_digest_golden_vector_pins_the_preimage`。
    """
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def revision_digest(
    *,
    parent_revision_id: str | None,
    origin: Stage0RevisionOrigin,
    actor: str | None,
    reason: str | None,
    content: Stage0RevisionContent,
) -> str:
    """一条 revision 的内容身份。**前像不含 `at`、不含 `code`。**

    这不是省事：机器重跑算出同一份内容、挂在同一个 parent 上，就必须得到同一个 id
    ——否则 `Stage0Revisions.dismissed`（「这个候选我已经否过一次」）永远认不出来，
    被否掉的候选每跑一次机器就复活一次。时间与代码版本是审计元数据，
    机器输入/输出不变时身份必须稳定（同已下线的 `snapshot_digest` 的先例）。
    """
    return _digest({
        'parent_revision_id': parent_revision_id,
        'origin': origin,
        'actor': actor,
        'reason': reason,
        'content': content.model_dump(mode='json'),
    })


def make_revision(
    *,
    parent_revision_id: str | None,
    origin: Stage0RevisionOrigin,
    actor: str | None,
    reason: str | None,
    code: str,
    content: Stage0RevisionContent,
    at: str | None = None,
) -> Stage0Revision:
    """构造一条链上的新版本。**纯函数，不碰文件系统。**"""
    return Stage0Revision(
        revision_id=revision_digest(
            parent_revision_id=parent_revision_id, origin=origin,
            actor=actor, reason=reason, content=content),
        parent_revision_id=parent_revision_id,
        origin=origin,
        actor=actor,
        reason=reason,
        at=at or now_iso(),
        code=code,
        content=content,
    )


def verify_chain(revisions: Stage0Revisions) -> None:
    """逐条复算 `revision_id`。**这是审计链下线之后唯一的防篡改闸。**

    结构约束（拓扑序、单根、指针成员资格）由 `Stage0Revisions` 的 validator 管；
    这里管的是「链上每一条的内容都还是当初那份」——改一个字，id 就对不上。
    """
    for item in revisions.revisions:
        expected = revision_digest(
            parent_revision_id=item.parent_revision_id, origin=item.origin,
            actor=item.actor, reason=item.reason, content=item.content)
        if expected != item.revision_id:
            raise ArtifactUnreadable(
                f'stage0 revision {item.revision_id[:12]}… 的 id 与内容对不上，'
                '链上这一版被改动过')


def by_id(revisions: Stage0Revisions, revision_id: str) -> Stage0Revision:
    for item in revisions.revisions:
        if item.revision_id == revision_id:
            return item
    raise ArtifactUnreadable(f'stage0 链上没有 revision {revision_id[:12]}…')


def current_revision(revisions: Stage0Revisions) -> Stage0Revision:
    return by_id(revisions, revisions.current.revision_id)


def read(store: ArtifactStore) -> Stage0Revisions | None:
    """读链；不在盘上返回 `None`（= 这本书还没摄入过）。读到就必然验过哈希。"""
    if not store.exists(REVISIONS_REL):
        return None
    revisions, _raw = store.read_own(Stage0Revisions, REVISIONS_REL)
    verify_chain(revisions)
    return revisions


def snapshot_of(revisions: Stage0Revisions) -> Stage0Snapshot:
    """`current` 的运行时投影。**纯函数**，不落盘。"""
    revision = current_revision(revisions)
    content = revision.content
    source = revisions.source
    clean_sha1 = prov.content_sha1(content.clean_text.encode('utf-8'))
    body_code_points = len(content.clean_text)
    return Stage0Snapshot(
        original_filename=source_filename(source.source_ref),
        source_ref=source.source_ref,
        code=revision.code,
        normalized_source_sha1=source.normalized_source_sha1,
        clean_sha1=clean_sha1,
        removed=content.removed,
        total_code_points=source.total_code_points,
        body_code_points=body_code_points,
        warnings=stage0_warnings(source.total_code_points, body_code_points, content.removed),
        revision_id=revision.revision_id,
        revision_origin=revision.origin,
        approval=revisions.current.approval,
    )


def materialize(store: ArtifactStore, revisions: Stage0Revisions) -> Stage0Snapshot:
    """把 `current.clean_text` 物化到唯一的正文路径。**幂等**。"""
    store.write_text(CLEAN_REL, current_revision(revisions).content.clean_text)
    return snapshot_of(revisions)


def commit(store: ArtifactStore, revisions: Stage0Revisions) -> Stage0Snapshot:
    """一次提交：原子写链，然后物化。**`revisions.json` 落稳的那一刻就是提交点。**"""
    verify_chain(revisions)
    store.write(REVISIONS_REL, revisions)
    return materialize(store, revisions)


def ensure_materialized(store: ArtifactStore) -> Stage0Revisions | None:
    """写路径的开场白：把可能停在半路的物化补完。

    盘上没有链 ⇒ `None`（还没摄入）。

    ⚠⚠ **补物化之前先分辨两件长得一样的事**：live `clean_text.txt` 与 current 不同，
    既可能是「上一条命令崩在两次 write 之间」（那要补完），也可能是**有人绕过
    `stage0.edit` 直接改了盘上的正文**（那要拦下——补物化会把他的字悄悄冲掉，
    而剔除是破坏性的，没有第二份副本）。两者的判别是干净的：中断留下的一定是
    **链上某一版**的正文；手改留下的字节链上一版都对不上。
    ⇒ 对不上就拒绝并指路，不猜。
    """
    revisions = read(store)
    if revisions is None:
        return None
    if store.exists(CLEAN_REL):
        live = prov.content_sha1(store.read_own_text(CLEAN_REL).encode('utf-8'))
        known = {
            prov.content_sha1(item.content.clean_text.encode('utf-8'))
            for item in revisions.revisions
        }
        if live not in known:
            raise HumanEditProtected(
                '盘上 stage0/clean_text.txt 的字节不是链上任何一版：有人绕过 '
                'stage0.edit 直接改了正式正文。它是 revisions.json 的物化投影，'
                '补物化会把这些字冲掉 ⇒ 拒绝继续。要保留这次改动，'
                '请把它交给 `stage0.edit --apply-edit <文件> --actor .. --reason ..`；'
                '要丢弃它，先删掉该文件再重跑任一 stage0 写路径')
    materialize(store, revisions)
    return revisions


def published(
    revisions: Stage0Revisions,
    revision: Stage0Revision,
    *,
    candidate_revision_id: str | None,
    approval: Stage0Approval | None = None,
) -> Stage0Revisions:
    """把一条新 revision 追加进链并指为当前稿，带上它的审批状态。

    ⚠ 2026-09-15 全自动裁决：stage0 的人工批准闸退役，机器稿在机械校验通过后
    由 producer 写入 auto 批准（`removal._auto_approval`）——本参数不再默认退回
    pending；`approval=None` 仅保留给历史解码路径。
    """
    from contracts.schema.stage0 import PendingApproval
    existing = [item.revision_id for item in revisions.revisions]
    entries = list(revisions.revisions)
    if revision.revision_id not in existing:
        entries.append(revision)
    return Stage0Revisions(
        source=revisions.source,
        revisions=entries,
        current=Stage0Current(
            revision_id=revision.revision_id,
            approval=approval or PendingApproval(status='pending')),
        candidate_revision_id=candidate_revision_id,
        dismissed=[item for item in revisions.dismissed
                   if item.revision_id != revision.revision_id],
    )
