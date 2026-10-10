"""同书源修订的窄证明；不修改根、产物或审批，也不替代业务依赖校验。

仅源钩子变化时，完整 clean 根虽变，Stage2–5 所读正文仍可逐字相同。
这不是任意跨根兼容：旧根必须来自当前 Stage0 的已校验祖先链，当前源已批准且
两份 Stage1 都是其准确物化；从首个章标记开始的全部字节必须不变。Prepare 的
完整 typed 重建另用相同来源链证明，不能借正文等价豁免旧钩子证据。
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re

from contracts.artifacts import (STAGE0_CLEAN_TEXT, STAGE0_REVISIONS, STAGE1_MAIN_TEXT,
    STAGE1_HOOK_COPY, STAGE5_TRIM_AUDIT, STAGE5_TRIM_RESOLUTION)
from contracts.schema import artifact_spec
from contracts.schema.common import InputEdge
from contracts.schema.stage1 import MainText, HookCopy
from novel import prov
from novel.consumption import canonical_rel, producer_node, projection_for
from novel.errors import ProvenanceRefused, TerminalState


BODY_OWNERS = frozenset({
    'stage2.analyze', 'stage2.characters', 'stage2.global', 'stage2.blueprint',
    'stage4.design', 'stage4.plan', 'stage4.context', 'stage5.write', 'stage5.review',
})


def body_owner(rel: str) -> str | None:
    """登记未列入 node.produces 的两个实际 Stage5 companion，不猜目录归属。"""
    rel = canonical_rel(rel)
    for template, owner in ((STAGE5_TRIM_AUDIT, 'stage5.write'),
                            (STAGE5_TRIM_RESOLUTION, 'stage5.review')):
        pattern = re.escape(template).replace(re.escape('{nn}'), r'[0-9]+')
        if re.fullmatch(pattern, rel):
            return owner
    return producer_node(rel)


@dataclass(frozen=True)
class SourceRevisionProof:
    source_root: str
    target_root: str
    source_revision_id: str
    target_revision_id: str
    main_text_sha256: str
    source_hook_copy_sha256: str
    body_unchanged: bool


def revision_proof(store, source_root: str) -> SourceRevisionProof:
    """重新验证当前源与旧根是否同一 transform；返回审计证明，绝不缓存到下次请求。

    Stage0 v3 无多修订祖先链：仅当 `source_root == 当前 clean_sha1` 时放行，
    并以 `transform_id` 同时充当 ancestor / current 身份。
    """
    from novel.stages.stage0 import revisions
    from novel.core.ingest_audit import audit_surface, scan_partition
    from novel.core.partition import partition_text

    # 所读内容仅用于来源证明，不能污染调用 owner 的业务输入。
    with store.ctx.validation_reads():
        transform = revisions.read(store)
        if transform is None or transform.admission.status != 'passed':
            raise ProvenanceRefused(
                '源修订采用要求机械 admission 已 passed 的 Stage0 transform')
        if store.freshness(STAGE0_CLEAN_TEXT).state != 'fresh':
            raise ProvenanceRefused('源修订采用的原文件或 clean 物化不符合审计链')
        clean = store.read_own_text(STAGE0_CLEAN_TEXT)
        target_root = prov.content_sha1(clean.encode('utf-8'))
        if source_root != target_root:
            raise ProvenanceRefused(
                'Stage0 v3 无多修订祖先链；旧根必须等于当前 transform 的 clean_sha1')
        main, _payload, main_raw = store._validated_read(MainText, STAGE1_MAIN_TEXT)
        hook = store.read_own(HookCopy, STAGE1_HOOK_COPY)[0]
        audit = audit_surface(
            clean_text=clean,
            stage0=revisions.snapshot_of(transform),
            main_text=main,
            hook_copy=hook,
        )
        if audit.hard_findings or not audit.main_current or not audit.hook_current:
            raise ProvenanceRefused('源修订采用要求当前 MainText / HookCopy 与已审源逐字相符')
        old_clean = clean
        old_partition = partition_text(old_clean)
        # 同时钉源切片、正式版本化 MainText 原字节和真实 payload；只比较语义
        # 摘要会漏章标记、边界或盘上格式变动，不能替代这条完整来源证明。
        same_body = (
            old_clean[scan_partition(old_clean).first_marker:] == clean[audit.partition.first_marker:]
            and old_partition.main_text == main
            and store._render(STAGE1_MAIN_TEXT, old_partition.main_text) == main_raw)
        tid = transform.transform_id
        return SourceRevisionProof(
            source_root, target_root, tid, tid,
            hashlib.sha256(main_raw).hexdigest(),
            hashlib.sha256(store._render(STAGE1_HOOK_COPY, old_partition.hook_copy)).hexdigest(),
            same_body)


def body_inputs_only(node: str | None, inputs: dict) -> bool:
    """仅现役正文 owner 的已声明实际输入；未知或钩子边不能取得源等价许可。"""
    if node not in BODY_OWNERS or not inputs:
        return False
    for rel, edge in inputs.items():
        spec = artifact_spec(canonical_rel(rel))
        if spec is None or (rel != STAGE1_MAIN_TEXT and canonical_rel(rel).split('/')[0] not in {'stage2', 'stage4', 'stage5'}):
            return False
        if projection_for(node, rel) is None:
            # Stage5 动态章节是其正式审阅的已有登记输入，projection 在专用入口。
            if not (node == 'stage5.review' and producer_node(rel) == 'stage5.write'):
                return False
        try:
            typed = edge if isinstance(edge, InputEdge) else InputEdge.model_validate(edge)
        except (TypeError, ValueError):
            return False
        if typed.consumed is None:
            return False
    return True


def same_body_source(store, source_root: str, *, node: str | None, inputs: dict) -> bool:
    """无证据保持原 mixed-root 拒绝；有效证明仍须调用方继续原 freshness。"""
    if not body_inputs_only(node, inputs):
        return False
    try:
        return revision_proof(store, source_root).body_unchanged
    except (TerminalState, ProvenanceRefused, ValueError, OSError):
        return False
