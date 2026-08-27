#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from contracts.artifacts import (
    STAGE1_MAIN_TEXT, STAGE2_CHAPTER_BLUEPRINTS, STAGE2_GLOBAL_GROUNDING,
    STAGE2_GLOBAL_REVIEW, STAGE2_GLOBAL_REVIEW_DECISIONS,
)
from contracts.schema.evaluation import (
    EvidenceRef, GlobalReview, GlobalReviewDecision, GlobalReviewDecisionSet,
)
from contracts.schema import encode_artifact
from contracts.schema.stage2 import GlobalGrounding
from contracts.schema.stage1 import MainText

from novel import paths
from novel.core.evidence import iter_global_evidence_slots, without_global_evidence
from novel.core.spans import exact_span
from novel.errors import (
    ApprovalRequired, BlockingFindingsUnresolved, GateBlocked, PathRefused,
    TerminalState, UpstreamMissing, UpstreamStale, UpstreamUnproven,
)
from novel.orchestrator.jobs import service_dir
from novel.quality.global_review import apply_decision_set, require_current, review_status
from novel.run import RunContext
from novel.store import ArtifactStore


REL = STAGE2_GLOBAL_REVIEW
DEFAULT_DECISIONS = STAGE2_GLOBAL_REVIEW_DECISIONS


def review_template(ctx: RunContext) -> GlobalReviewDecisionSet:
    """返回绑定当前逐项 hash/evidence 的安全人工复核骨架。

    每一项默认 `UNVER`，所以原样应用也**不会打开 s2 gate**；执行者只需把已核实项
    改成 PASS/FAIL、替换 actor/reason，不再手抄乐观锁与证据坐标。骨架读取正式
    `global_review.json`，不从 grounding 重拼第二份 item 身份。
    """
    store = ArtifactStore(ctx)
    if not store.exists(REL):
        raise UpstreamMissing(f'{REL} 不在盘上；先生成/重建 stage2.global')
    current = store.read_own(GlobalReview, REL)[0]
    return GlobalReviewDecisionSet(
        actor='待填·actor',
        decisions=[GlobalReviewDecision(
            item_id=item.item_id,
            expected_subject_sha256=item.subject_sha256,
            state='UNVER',
            reason='待人工核验：请替换本句并将已核实项改为 PASS 或 FAIL',
            evidence=list(item.evidence),
        ) for item in current.items],
    )


def review_template_json(ctx: RunContext) -> str:
    """stdout 用注册表版本化 JSON；相对/绝对/API 三条输入路吃同一形状。"""
    payload = review_template(ctx).model_dump(mode='json', by_alias=True)
    return json.dumps(
        encode_artifact(DEFAULT_DECISIONS, payload),
        ensure_ascii=False, indent=2, sort_keys=True)


def _subject_at(root: object, subject_path: str) -> object:
    """按评测项路径解引用；list 同时接受数字索引与现役 ``*_id``。"""
    current = root
    for segment in subject_path.split('.'):
        if isinstance(current, dict):
            if segment not in current:
                raise KeyError(segment)
            current = current[segment]
        elif isinstance(current, list):
            if segment.isdigit():
                current = current[int(segment)]
            else:
                current = next(
                    item for item in current
                    if isinstance(item, dict)
                    and segment in {
                        str(value) for key, value in item.items() if key.endswith('_id')
                    })
        else:
            raise KeyError(segment)
    return current


def _strings(value: object) -> list[str]:
    out: list[str] = []
    if isinstance(value, str) and len(value.strip()) >= 2:
        out.append(value.strip())
    elif isinstance(value, dict):
        for child in value.values():
            out.extend(_strings(child))
    elif isinstance(value, list):
        for child in value:
            out.extend(_strings(child))
    return list(dict.fromkeys(out))


def _text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _formal_materials(
    slots: list[tuple[str, object]],
    chapters: dict[int, object],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """把正式 FactEvidence 转成人工可提交的 hash-bound 证据候选。

    缺失或无效的 source 显式留在第二个返回值里；调用方尤其不能用 claim 字面
    搜索命中替换它。
    """
    materials: list[dict[str, object]] = []
    unresolved: list[dict[str, object]] = []
    seen: set[tuple[str, int, int, int]] = set()
    for claim_path, evidence in slots:
        if not isinstance(evidence, dict):
            unresolved.append({
                'claim_path': claim_path,
                'chapter_ordinal': None,
                'reason': 'formal evidence 缺失',
            })
            continue
        ordinal = evidence.get('chapter_ordinal')
        source = evidence.get('source')
        if not isinstance(ordinal, int) or source is None:
            unresolved.append({
                'claim_path': claim_path,
                'chapter_ordinal': ordinal if isinstance(ordinal, int) else None,
                'reason': 'formal evidence source=None',
            })
            continue
        chapter = chapters.get(ordinal)
        text = getattr(chapter, 'text', None)
        if not isinstance(text, str):
            unresolved.append({
                'claim_path': claim_path,
                'chapter_ordinal': ordinal,
                'reason': 'formal evidence 指向不存在的正文章',
            })
            continue
        start, end, problem = exact_span(source, text)
        if problem:
            unresolved.append({
                'claim_path': claim_path,
                'chapter_ordinal': ordinal,
                'reason': problem,
            })
            continue
        identity = claim_path, ordinal, start, end
        if identity in seen:
            continue
        seen.add(identity)
        ref = EvidenceRef(
            artifact=f'{STAGE1_MAIN_TEXT}#chapter/{ordinal}',
            subject_sha256=_text_sha256(text),
            start=start,
            end=end,
        )
        lo = max(0, start - 60)
        hi = min(len(text), end + 60)
        materials.append({
            'claim_path': claim_path,
            'evidence_ref': ref.model_dump(mode='json'),
            'chapter_ordinal': ordinal,
            'literal': text[start:end],
            'start': start,
            'end': end,
            'context': text[lo:hi],
        })
    return materials, unresolved


#: 必须通读全书才能裁的那几类：集合完整性问「有没有漏收」，解读层问「这个划分/
#: 这条弧线成不成立」——两者的答案都不在某一段原文里，**精确命中 0 条是常态**。
#: ⚠ 判成另一类会给出一句「命中只是缩小阅读范围」的提示，读的人容易误以为
#: 「找不到命中 ⇒ 没问题」，而这几类恰恰是 0 命中最需要人真的去读的。
_WHOLE_BOOK_KINDS = frozenset({
    'character_set_complete', 'relation_set_complete',
    'world_rule_set_complete', 'secret_set_complete',
    'conflict_partition', 'arc_analysis',
})

# 这些主体的问题不是“这一条事实有没有出处”，不能把其下所有正式 evidence
# 拼起来冒充集合完整性或阶段划分的证据。arc_analysis 例外：它自己的 trajectory
# 有逐条证据，虽仍需整体阅读判断，workpack 应优先展示那些正式坐标。
_NO_FORMAL_EVIDENCE_KINDS = frozenset({
    'character_set_complete', 'relation_set_complete',
    'world_rule_set_complete', 'secret_set_complete', 'conflict_partition',
})


def review_workpack(ctx: RunContext) -> dict[str, object]:
    """只读人工核验材料；精确命中是证据候选，不是自动裁决。"""
    store = ArtifactStore(ctx)
    grounding = store.read_own(GlobalGrounding, STAGE2_GLOBAL_GROUNDING)[0]
    review = store.read_own(GlobalReview, REL)[0]
    main = store.read(MainText, STAGE1_MAIN_TEXT)
    payload = grounding.model_dump(mode='json', by_alias=True)
    claim_payload = without_global_evidence(payload)
    assert isinstance(claim_payload, dict)
    evidence_slots = [
        (path, owner.get(field))
        for path, owner, field in iter_global_evidence_slots(payload)
    ]
    chapters = {chapter.chapter_ordinal: chapter for chapter in main.root}
    rows: list[dict[str, object]] = []
    for item in review.items:
        subject_path = item.subject_path
        lookup_path = subject_path
        if item.kind == 'address_context' and lookup_path.endswith('.context'):
            lookup_path = lookup_path.rsplit('.', 1)[0]
        elif item.kind == 'name_distinctiveness':
            lookup_path = lookup_path.rsplit('.', 1)[0]
        elif item.kind == 'secret_setup':
            # `setup_chapters` 恒是 `list[int]` ⇒ `_strings` 恒空、命中恒 0。
            # 回退到整条 secret，人读到的是那条秘密的 `fact` 在原文里的位置——
            # 「这些章到底埋没埋」正要拿它去对。⚠ 与 `address_context` /
            # `name_distinctiveness` 同一形状：`subject` 是给人看的上下文，
            # 而 `subject_path` / `subject_sha256` 仍严格绑在被裁决的那个字段上。
            lookup_path = lookup_path.rsplit('.', 1)[0]
        try:
            # 人读的 subject 与 claim 搜索面都剥掉正式 evidence；否则
            # SourceSpan.text/normalized/recovery 会被递归当成三条“claim”再搜一遍。
            subject = _subject_at(claim_payload, lookup_path)
            literals = _strings(subject)
        except (KeyError, IndexError, StopIteration):
            subject = None
            literals = []
        relevant_slots: list[tuple[str, object]] = []
        if item.kind not in _NO_FORMAL_EVIDENCE_KINDS:
            prefix = f'{item.subject_path}.'
            relevant_slots = [
                (path, evidence) for path, evidence in evidence_slots
                if path == item.subject_path or path.startswith(prefix)
            ]
        formal, unresolved = _formal_materials(relevant_slots, chapters)
        hits: list[dict[str, object]] = []
        if relevant_slots:
            # 正式 evidence 是权威坐标；source=None / 坐标无效时保持空，不得再拿
            # claim 字面搜索制造一条看起来合法的“证据”。
            hits = [{
                'chapter_ordinal': row['chapter_ordinal'],
                'literal': row['literal'],
                'start': row['start'],
                'end': row['end'],
                'context': row['context'],
                'origin': 'formal_evidence',
                'claim_path': row['claim_path'],
            } for row in formal]
        else:
            for literal in literals:
                if len(hits) >= 24:
                    break
                for chapter in main.root:
                    start = 0
                    while len(hits) < 24:
                        found = chapter.text.find(literal, start)
                        if found < 0:
                            break
                        lo = max(0, found - 60)
                        hi = min(len(chapter.text), found + len(literal) + 60)
                        hits.append({
                            'chapter_ordinal': chapter.chapter_ordinal,
                            'literal': literal,
                            'start': found,
                            'end': found + len(literal),
                            'context': chapter.text[lo:hi],
                            'origin': 'claim_literal',
                        })
                        start = found + max(1, len(literal))
        base_note = ('集合完整性项必须通读全书核对；0 个精确命中不等于 PASS。'
                     if item.kind in _WHOLE_BOOK_KINDS else
                     '这些命中只缩小人工阅读范围，不产生 PASS/FAIL/UNVER。')
        if unresolved:
            note = ('正式 evidence 含 source=None 或无效坐标；必须维持 UNVER，'
                    '不能用 claim 字面命中代替证据。' + base_note)
        elif formal:
            note = ('已优先把正式 evidence 转为 hash-bound EvidenceRef 与正文上下文；'
                    '它仍不自动产生裁决。' + base_note)
        else:
            note = base_note
        rows.append({
            'item_id': item.item_id, 'kind': item.kind,
            'subject_path': item.subject_path,
            'subject_sha256': item.subject_sha256,
            'current_state': item.state,
            'subject': subject, 'registered_evidence': [
                evidence.model_dump(mode='json') for evidence in item.evidence],
            'formal_evidence': formal,
            'unresolved_formal_evidence': unresolved,
            'exact_literal_hits': hits,
            'review_note': note,
        })
    return {
        'kind': 'stage2-global-review-workpack-v1',
        'sid': ctx.sid,
        'read_only': True,
        'auto_decisions': False,
        'items': rows,
    }


def review_workpack_json(ctx: RunContext) -> str:
    return json.dumps(review_workpack(ctx), ensure_ascii=False, indent=2, sort_keys=True)


def apply_file(ctx: RunContext, decision_path: str = '') -> GlobalReview:
    """Apply audited human decisions with per-item optimistic subject hashes."""
    store = ArtifactStore(ctx)
    if not store.exists(REL):
        raise UpstreamMissing(f'{REL} 不在盘上；先生成/重建 stage2.global')
    supplied = Path(decision_path) if decision_path else None
    if supplied is not None and supplied.is_absolute():
        candidate = supplied.resolve()
        allowed = (service_dir(ctx.novel_dir.parent) / 'edits' / ctx.sid).resolve()
        book = Path(ctx.novel_dir).resolve()
        try:
            candidate.relative_to(allowed)
        except ValueError:
            # 与 `paths.edit_rel` 同一口径：绝对路径落在当前书目录内同样合法，
            # 报错文案承诺的正是「当前书或 <allowed>」两个根。
            try:
                candidate.relative_to(book)
            except ValueError as exc:
                raise PathRefused(
                    f'复核决策文件必须在当前书或 {allowed} 内：{candidate}') from exc
        if not candidate.is_file():
            raise TerminalState(f'{candidate} 不在盘上')
        decisions = store.read_external(
            GlobalReviewDecisionSet, DEFAULT_DECISIONS, candidate)
    else:
        rel = paths.edit_rel(ctx.novel_dir, decision_path, DEFAULT_DECISIONS)
        if not store.exists(rel):
            raise TerminalState(
                f'{rel} 不在盘上；放入 GlobalReviewDecisionSet（actor + decisions）')
        if rel == DEFAULT_DECISIONS:
            decisions = store.read(GlobalReviewDecisionSet, rel)
        else:
            # ⚠ **自定义书内相对路径没在 `ARTIFACT_SPECS` 里登记**，而
            #   `--emit-review-template` 交出来的模板是**带版本信封**的
            #   （`review_template_json` 调 `encode_artifact`）。按未注册路径解码
            #   会把 `_schema_version` 原样留在 payload 里，StrictModel 当场报
            #   「多了一个字段」——一份刚由本仓生成的模板，换个文件名就读不回来。
            #   ⇒ 与上面那条绝对路径分支同一把尺子：路径由调用方授权，
            #   **schema 口径恒取注册的 `DEFAULT_DECISIONS`**。
            decisions = store.read_external(
                GlobalReviewDecisionSet, DEFAULT_DECISIONS,
                Path(ctx.novel_dir) / rel)
    grounding = store.read_own(GlobalGrounding, STAGE2_GLOBAL_GROUNDING)[0]
    grounding_payload = grounding.model_dump(by_alias=True)
    current = store.read_own(GlobalReview, REL)[0]
    require_current(grounding_payload, current)
    updated = apply_decision_set(current, decisions)
    require_current(grounding_payload, updated)
    store.write_round(REL, updated)
    return updated


def is_approved(ctx: RunContext, review: GlobalReview) -> bool:
    """Bind approval to the complete, current Stage2 subject."""
    grounding = ArtifactStore(ctx).read_own(
        GlobalGrounding, STAGE2_GLOBAL_GROUNDING)[0]
    return review_status(
        grounding.model_dump(by_alias=True), review)[2]


def approval_status(ctx: RunContext) -> tuple[bool, bool, dict[str, int]]:
    """返回 ``(评测面当前, 全部PASS, 四态计数)``；闸门与终验共用这一处。"""
    store = ArtifactStore(ctx)
    grounding = store.read_own(GlobalGrounding, STAGE2_GLOBAL_GROUNDING)[0]
    review = store.read_own(GlobalReview, REL)[0]
    _subject, current, approved = review_status(
        grounding.model_dump(by_alias=True), review)
    counts = {state: 0 for state in ('pending', 'PASS', 'FAIL', 'UNVER')}
    for item in review.items:
        counts[item.state] += 1
    return current, approved, counts


def require_approved(ctx: RunContext) -> None:
    """所有 Stage2 下游真实执行与成书读取共用的完整收口闸。

    人工决定只回答 global 事实是否可信；它不能替 blueprint 的存在、freshness 或
    联合 HARD 背书。把后半段留给各下游自己碰，会让 API 先创建一个注定失败的作业，
    也会让已有交付物在 blueprint 后来缺失/过期时只凭旧 review 继续下载。

    这里不造第二座 Stage2 判官：动态导入 ``extract.run_check_only``，仍走
    ``stage2.blueprint --check-only`` 使用的那一份完整联合判据。只另行把报告第一节
    的 freshness/INVALID 翻成 typed 终态，因为 ``CheckReport.passed`` 按契约只回答
    判据 badge，不会把来源当前性偷算进去。
    """
    store = ArtifactStore(ctx)
    global_invalid = paths.invalid_of(STAGE2_GLOBAL_GROUNDING)
    if store.exists(global_invalid):
        raise GateBlocked(
            'Stage2 global 有一份比旧正式稿更新的问题稿；'
            '不能拿旧 review 冒充当前全局事实',
            invalid_path=str(store.path(global_invalid)),
            hint='stage2.global --revalidate --actor <id> --reason <why>（0 成本）')

    current, approved, counts = approval_status(ctx)
    if not approved:
        raise ApprovalRequired(
            'stage2 全局复核未打开：必须是当前完整评测面且逐项 PASS；'
            f'current={current}，counts={counts}',
            hint=('先运行 `stage2.global --emit-review-template`，逐项核验后用 '
                  '`stage2.global --review-decisions <书内json>` 提交；'
                  'FAIL/UNVER 不会被 Agent 退出码覆盖'))

    global_freshness = store.freshness(STAGE2_GLOBAL_GROUNDING)
    if global_freshness.state == 'stale':
        raise UpstreamStale(
            f'{STAGE2_GLOBAL_GROUNDING} 已不是当前 main_text/root 上的结果；'
            '旧 review 与它一致也不能打开下游',
            hint='先从 producer 重建 stage2.global，再复核新的完整主体')
    if global_freshness.state != 'fresh':
        raise UpstreamUnproven(
            f'{STAGE2_GLOBAL_GROUNDING} 证明不了自己来自当前 Stage2 输入'
            f'（{global_freshness.state}/{global_freshness.reason}）',
            hint='从 producer 重建 stage2.global')

    blueprint_invalid = paths.invalid_of(STAGE2_CHAPTER_BLUEPRINTS)
    if (not store.exists(STAGE2_CHAPTER_BLUEPRINTS)
            and not store.exists(blueprint_invalid)):
        raise UpstreamMissing(
            f'{STAGE2_CHAPTER_BLUEPRINTS} 不在盘上；Stage2 尚未完成联合检查',
            hint='stage2.blueprint（付费；可在 global 人工阅读期间先运行）')

    # ``run_check_only`` 的报告 node 必须是 stage2.blueprint；下游 ctx 若直接传入，
    # Stage4/7 的规则注册表会与 Stage2 outcomes 错配。只读子 context 不记账、不写盘。
    from novel.orchestrator import state
    from novel.stages.stage2 import extract

    joint_ctx = state.readonly_ctx(
        ctx.novel_dir, 'stage2.blueprint', ctx.code)
    report = extract.run_check_only(joint_ctx)
    if report.provenance.invalid:
        raise GateBlocked(
            'Stage2 逐章蓝图仍有未收口的问题稿；联合检查未通过',
            invalid_path=str(store.path(blueprint_invalid)),
            hint='stage2.blueprint --revalidate --actor <id> --reason <why>（0 成本）')
    if report.provenance.freshness == 'stale':
        raise UpstreamStale(
            f'{STAGE2_CHAPTER_BLUEPRINTS} 已不是当前 global/main_text 上的结果；'
            'Stage2 联合检查不能沿用旧结论',
            hint='重跑 stage2.blueprint；若是 typed global edit，先看人物称名消费投影')
    if report.provenance.freshness != 'fresh':
        raise UpstreamUnproven(
            f'{STAGE2_CHAPTER_BLUEPRINTS} 证明不了自己来自当前 Stage2 输入'
            f'（{report.provenance.freshness}/{report.provenance.reason}）',
            hint='从 producer 重建 stage2.blueprint')
    if not report.passed:
        hard = tuple(
            row.rule for row in report.rules
            if row.sev == 'hard' and row.status in ('hit', 'skipped'))
        raise BlockingFindingsUnresolved(
            'Stage2 global + blueprint 的联合 HARD 尚未通过；拒绝进入下游',
            rules=hard or ('stage2_joint_check',),
            hint='stage2.blueprint --check-only（0 成本）')
