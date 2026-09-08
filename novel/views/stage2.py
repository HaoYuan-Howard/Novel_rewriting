#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stage2.py —— S-05「全局设定」那一屏（`GET /api/novels/:sid/stage2/global`）。

【与已退役机械基线页的历史分界】旧页曾有「左半边」（实测量的是 stage1，
产物在不在都算得出来）；当前机械测量已归 Stage5/6 即时计算。
**本屏没有左半边**：stage2 没跑就没有任何东西可看 ⇒ 产物不在盘上是 **404**
（`UpstreamMissing`），读不回来是 409（`ArtifactUnreadable`）。

【view 只做一件新事：解引用】产物里的「谁」是 id（`char_052` / `rel_05` / `reader`），
人读的是名字。**解析不动时 `resolved=False` 且 `display == raw`**——
view 不编名字：编出来的名字比一个看得出来的 id 更坏（它看不出来）。
解引用失败的总数进 `unresolved_refs`（读数，不是判定）。
"""
from __future__ import annotations

import hashlib
import json
import os

from contracts.artifacts import (
    STAGE1_MAIN_TEXT, STAGE2_CHAPTER_BLUEPRINTS, STAGE2_GLOBAL_GROUNDING,
)
from contracts.invariants import BLUEPRINT_REVIEW_CONTRACT
from contracts.schema.api import (
    ArcTrackLine, GlobalAddressLine, GlobalArcView, GlobalCharacterLine,
    GlobalKnowledgeEventLine, GlobalRelationLine, GlobalSecretLine, GlobalView, NameRef,
    BlueprintReviewBeat, BlueprintReviewBinding, BlueprintReviewCausalLink,
    BlueprintReviewChapter, BlueprintReviewCoverage,
    BlueprintReviewCoverageSegment, BlueprintReviewFact,
    BlueprintReviewNarrativeRelation, BlueprintReviewObserves,
    BlueprintReviewResponseTo, BlueprintReviewSameScene,
    BlueprintReviewStoryBefore, BlueprintReviewWorkpack,
    CompactApprovalCheck,
    ReadingBeatLine, ReadingFactLine, ReadingHinge, ReadingNarrationLine,
    ReadingSegment, ReadingView,
)
from contracts.schema.stage1 import MainText
from contracts.schema.stage2 import Blueprint, ChapterBlueprints, GlobalGrounding
from novel import paths, prov
from novel.errors import ArtifactUnreadable, UpstreamMissing
from novel.orchestrator.state import artifact_state, readonly_ctx
from novel.stages.stage2.checks import HOLE_MIN_CHARS
from novel.stages.stage2.coverage import walk_segments
from novel.store import ArtifactStore

NODE = 'stage2.global'
REL = STAGE2_GLOBAL_GROUNDING

BP_NODE = 'stage2.blueprint'
BP_REL = STAGE2_CHAPTER_BLUEPRINTS
MAIN_TEXT = STAGE1_MAIN_TEXT


def global_view(base: str | os.PathLike[str], sid: str) -> GlobalView:
    """S-05 的一屏。**纯读**：不写一个字节，不发一个请求，不产生任何判定。

    ⚠ 不带 `badge` / `passed` / `counts`——结论只能来自 `/check/stage2`（`R-11.4`）。
    ⚠ 剧透遮蔽不在这里做：`fate` / `secrets` / `arc.climax` / `arc.ending` 全量下发，
    开关是前端的纯展示行为（`R-11.13`，那句话作为 `spoiler_disclosure.notice`
    随 `/api/enums` 整份下发——**不单独发一个标量**，免得一句话与结构化契约分家）。
    """
    novel_dir = paths.resolve_novel_dir(base, sid)
    store = ArtifactStore(readonly_ctx(novel_dir, NODE, prov.code_version()))

    # 产物不在 ⇒ 404；在而读不回 ⇒ `read_own` 抛 `ArtifactUnreadable` ⇒ 409。
    # （`artifact_state` 不抛，所以「不在」这一态要自己升成终态。）
    st = artifact_state(store, REL)
    art, _raw = store.read_own(GlobalGrounding, REL)

    misses = _Misses()
    chars = {cid: c for cid, c in art.entities.characters.items()}

    def ref(raw: str) -> NameRef:
        """`char_id` / `narration` / `public` / `reader` / `rel_XX` → 人话。"""
        if raw in chars:
            return NameRef(raw=raw, display=chars[raw].canonical, resolved=True)
        if raw == 'narration':
            return NameRef(raw=raw, display='叙述', resolved=True)
        if raw == 'public':
            return NameRef(raw=raw, display='众人', resolved=True)
        if raw == 'reader':
            return NameRef(raw=raw, display='读者', resolved=True)
        if raw.startswith('rel_'):
            rel = next((r for r in art.entities.relations if r.relation_id == raw), None)
            if rel is not None and rel.subject_id in chars and rel.object_id in chars:
                return NameRef(
                    raw=raw, resolved=True,
                    display=f'{chars[rel.subject_id].canonical}—{rel.predicate}—'
                            f'{chars[rel.object_id].canonical}')
        return misses.unresolved(raw)

    characters = [
        GlobalCharacterLine(
            char_id=cid, canonical=c.canonical,
            name_distinctiveness=c.name_distinctiveness, role=c.role, fate=c.fate,
            role_evidence=c.role_evidence, fate_evidence=c.fate_evidence,
            address=[GlobalAddressLine(surface=a.surface, by=ref(a.by),
                                       context=a.context, from_chapter=a.from_chapter)
                     for a in c.address])
        for cid, c in chars.items()]
    relations = [
        GlobalRelationLine(
            relation_id=r.relation_id, subject=ref(r.subject_id),
            predicate=r.predicate, predicate_evidence=r.predicate_evidence,
            object=ref(r.object_id), visibility=r.visibility,
            valid_from_chapter=r.valid_from_chapter,
            valid_through_chapter=r.valid_through_chapter)
        for r in art.entities.relations]

    def knowledge_subject(event) -> NameRef:
        if event.subject.kind == 'character':
            return ref(event.subject.char_id)
        return NameRef(raw=event.subject.label, display=event.subject.label, resolved=True)

    secrets = [
        GlobalSecretLine(
            secret_id=s.secret_id, fact=s.fact, fact_evidence=s.fact_evidence,
            knowledge_events=[GlobalKnowledgeEventLine(
                subject_kind=e.subject.kind,
                subject=knowledge_subject(e),
                known_from_chapter=e.known_from_chapter,
                evidence=e.evidence,
            ) for e in s.knowledge_events],
            setup_kind=s.setup.kind,
            setup_chapters=list(getattr(s.setup, 'chapters', None) or []),
            first_reveal_chapter=s.first_reveal_chapter)
        for s in art.canon.secrets]
    arc = GlobalArcView(
        conflict_stages=art.arc.conflict_stages,
        climax=art.arc.climax, ending=art.arc.ending,
        arc_tracks=[ArcTrackLine(subject=ref(t.subject), trajectory=t.trajectory,
                                 trajectory_evidence=t.trajectory_evidence)
                    for t in art.arc.arc_tracks])

    return GlobalView(
        sid=paths.derive_source_meta(novel_dir.name)[0],
        title=paths.title_of(novel_dir),
        setting=art.canon.setting,
        characters=characters,
        relations=relations,
        world_rules=art.canon.world_rules,
        secrets=secrets,
        arc=arc,
        unresolved_refs=misses.count,
        invalid_exists=st.invalid,
        freshness=st.freshness,
        reason=st.reason,
        ghosts=list(st.ghosts),
        _provenance=art.provenance,
    )


class _Misses:
    """解引用失败的计数器。**`display == raw`**——不编名字，只数没解开的。"""

    def __init__(self) -> None:
        self.count = 0

    def unresolved(self, raw: str) -> NameRef:
        self.count += 1
        return NameRef(raw=raw, display=raw, resolved=False)


# ─────────────────────────────────────────────────────────────────────────────
# S-04 原文 ↔ 骨架（`GET /api/novels/:sid/reading/:o`）
# ─────────────────────────────────────────────────────────────────────────────

def reading_view(base: str | os.PathLike[str], sid: str, ordinal: int) -> ReadingView:
    """S-04 的一章。**纯读**。左半边是 stage1 该章原文，右半边是 stage2 逐章蓝图。

    ⚠ **stage2 没跑（或蓝图里没有这一章）⇒ 200 且 `blueprint=false`**：
    整章一个 hole、beats 空——左半边量的是 stage1，与 stage2 无关（S-06 同款分治）。
    蓝图在但**读不回** ⇒ 也是 `blueprint=false`，两种来路由 `blueprint_state` 分
    （`exists` / `readable` 两格）。
    stage1 不在 / `:o` 越界 ⇒ `UpstreamMissing`（404）。
    """
    novel_dir = paths.resolve_novel_dir(base, sid)
    store = ArtifactStore(readonly_ctx(novel_dir, BP_NODE, prov.code_version()))

    main = store.read(MainText, MAIN_TEXT)
    text = next((s.text or '' for s in main.root if s.chapter_ordinal == ordinal), None)
    if text is None:
        raise UpstreamMissing(
            f'第 {ordinal} 章不在 stage1 的章集里'
            f'（1..{max((s.chapter_ordinal for s in main.root), default=0)}）')

    st = artifact_state(store, BP_REL)
    bp, bp_prov = (_blueprint_of(store, ordinal)
                   if (st.exists and st.readable) else (None, None))

    spans = [(b.beat_id, b.span.start, b.span.end)
             for b in (bp.beats if bp is not None else []) if b.span is not None]
    segments = [
        ReadingSegment(start=s, end=e, chars=e - s, text=text[s:e], beat_id=bid,
                       flagged=bid is None and e - s >= HOLE_MIN_CHARS)
        for bid, s, e in walk_segments(len(text), spans)]
    facts_by_beat: dict[str, list[ReadingFactLine]] = {}
    for atom in (bp.fact_atoms if bp is not None else []):
        facts_by_beat.setdefault(atom.source_beat_ref, []).append(ReadingFactLine(
            fact_id=atom.fact_id,
            claim=atom.claim,
            kind=atom.kind,
            obligation=atom.obligation,
            evidence_text=atom.source.text if atom.source is not None else None,
            participant_char_ids=list(atom.participant_char_ids),
        ))
    beats = [
        ReadingBeatLine(
            beat_id=b.beat_id, located=b.span is not None,
            start=b.span.start if b.span is not None else None,
            end=b.span.end if b.span is not None else None,
            time_layer=b.time_layer, place=b.place,
            present_char_ids=list(b.present_char_ids),
            fact_atoms=facts_by_beat.get(b.beat_id, []),
            hinges=[ReadingHinge(kind=h.kind,
                                 text=h.source.text if h.source is not None else None)
                    for h in b.hinges])
        for b in (bp.beats if bp is not None else [])]
    narration = [
        ReadingNarrationLine(
            grammatical_person=n.grammatical_person,
            narrator_char_id=n.narrator_char_id,
            focalizer_char_id=n.focalizer_char_id,
            pronoun_surfaces=list(n.pronoun_surfaces))
        for n in (bp.narration if bp is not None else [])]

    return ReadingView(
        sid=paths.derive_source_meta(novel_dir.name)[0],
        title=paths.title_of(novel_dir),
        chapter_ordinal=ordinal,
        text_chars=len(text),
        blueprint=bp is not None,
        segments=segments,
        beats=beats,
        narration=narration,
        loose=sum(s.chars for s in segments if s.beat_id is None),
        uncovered_min_chars=HOLE_MIN_CHARS,
        blueprint_state=st,
        _provenance=bp_prov if bp is not None else None,
    )


def _blueprint_of(store: ArtifactStore, ordinal: int):
    """这一章的蓝图条目与整份来源账。**仍然 `try`**（防 TOCTOU：同一请求中盘面可能变化）。

    返回 `(Blueprint|None, BlueprintProvenance|None)`——条目不在时来源账也不发
    （半份产物比没有更误导）。
    """
    try:
        art, _raw = store.read_own(ChapterBlueprints, BP_REL)
    except (UpstreamMissing, ArtifactUnreadable):
        return None, None
    entry: Blueprint | None = next(
        (b for b in art.blueprints if b.chapter_ordinal == ordinal), None)
    return entry, art.provenance if entry is not None else None


# ──────────────────────────────────────────────────────────────────────────
# 四、Blueprint 只读审阅工作包（book-level）
# ───────────────────────────────────────────────────────────────────────

# 这些字段仍在当前正式产物与生产 CheckReport 里被完整校验，
# 只是不在紧凑人读面里重复铺开。名单必须随包发出：「没展示」不能
# 被误读成「没检查」。
_BLUEPRINT_MECHANICAL_FIELDS_HIDDEN = (
    'blueprints[].beats[].span.start/end',
    'blueprints[].fact_atoms[].source_beat_ref',
    'blueprints[].fact_atoms[].source.start/end',
    'blueprints[].beats[].hinges[].hinge_id',
    'blueprints[].beats[].hinges[].source.start/end',
    'chapter_blueprints._provenance',
)


def _canonical_sha256(value: object) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
        allow_nan=False,
    ).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def _compact_check(report) -> CompactApprovalCheck:
    """只裁剪生产 ``CheckReport``，不再跑或重拼任何判据。

    views 层有结构守卫，不许直接 import ``novel.report``；因而这里
    只做与通用终审工作包同形的无损投影：非 ok 行全留、正常 metric
    全留，其余绿行由四态计数与扫描总分母证明执行过。
    """
    return CompactApprovalCheck(
        rules_scanned=len(report.rules),
        counts=report.counts,
        provenance=report.provenance,
        attention=[row for row in report.rules if row.status != 'ok'],
        metrics=[
            row for row in report.rules
            if row.sev == 'metric' and row.status == 'ok'
        ],
    )


def _chapter_attention(report) -> dict[int, list[str]]:
    """只使用 Finding 的 typed ``output_ordinal``，不从 detail/evidence 散文猜章号。"""
    by_ordinal: dict[int, list[str]] = {}
    for row in report.rules:
        if row.status == 'ok':
            continue
        for item in row.findings:
            if item.output_ordinal is None:
                continue
            rules = by_ordinal.setdefault(item.output_ordinal, [])
            if row.rule not in rules:
                rules.append(row.rule)
    return by_ordinal


def _review_chapter(
    section,
    blueprint: Blueprint,
    *,
    attention_rules: list[str],
) -> BlueprintReviewChapter:
    text = section.text
    fact_by_id = {atom.fact_id: atom for atom in blueprint.fact_atoms}
    facts_by_beat: dict[str, list[BlueprintReviewFact]] = {}
    for atom in blueprint.fact_atoms:
        facts_by_beat.setdefault(atom.source_beat_ref, []).append(
            BlueprintReviewFact(
                fact_id=atom.fact_id,
                claim=atom.claim,
                kind=atom.kind,
                obligation=atom.obligation,
                evidence_text=(
                    atom.source.text if atom.source is not None else None),
                participant_char_ids=list(atom.participant_char_ids),
            ))

    beat_order = {
        beat.beat_id: index for index, beat in enumerate(blueprint.beats, 1)
    }
    spans = [
        (beat_order[beat.beat_id], beat.span.start, beat.span.end)
        for beat in blueprint.beats
        if (beat.span is not None
            and 0 <= beat.span.start < beat.span.end <= len(text))
    ]
    coverage_segments = [
        BlueprintReviewCoverageSegment(
            beat_order=order,
            chars=end - start,
            text=text[start:end],
            flagged=(order is None and end - start >= HOLE_MIN_CHARS),
        )
        for order, start, end in walk_segments(len(text), spans)
    ]
    covered_chars = sum(
        row.chars for row in coverage_segments if row.beat_order is not None)
    loose_chars = sum(
        row.chars for row in coverage_segments if row.beat_order is None)

    beats = [
        BlueprintReviewBeat(
            beat_id=beat.beat_id,
            beat_order=index,
            located=(
                beat.span is not None
                and 0 <= beat.span.start < beat.span.end <= len(text)
            ),
            time_layer=beat.time_layer,
            place=beat.place,
            present_char_ids=list(beat.present_char_ids),
            facts=facts_by_beat.get(beat.beat_id, []),
            hinges=[
                ReadingHinge(
                    kind=hinge.kind,
                    text=(hinge.source.text if hinge.source is not None else None),
                )
                for hinge in beat.hinges
            ],
        )
        for index, beat in enumerate(blueprint.beats, 1)
    ]

    relations: list[BlueprintReviewNarrativeRelation] = []
    for relation in blueprint.narrative_relations:
        common = {
            'kind': relation.kind,
            'enforcement': relation.enforcement,
        }
        if relation.kind == 'story_before':
            relations.append(BlueprintReviewStoryBefore(
                **common,
                before_claim=fact_by_id[relation.before_fact_ref].claim,
                after_claim=fact_by_id[relation.after_fact_ref].claim,
            ))
        elif relation.kind == 'response_to':
            relations.append(BlueprintReviewResponseTo(
                **common,
                stimulus_claim=fact_by_id[relation.stimulus_fact_ref].claim,
                response_claim=fact_by_id[relation.response_fact_ref].claim,
            ))
        elif relation.kind == 'same_scene':
            relations.append(BlueprintReviewSameScene(
                **common,
                left_claim=fact_by_id[relation.left_fact_ref].claim,
                right_claim=fact_by_id[relation.right_fact_ref].claim,
            ))
        else:
            relations.append(BlueprintReviewObserves(
                **common,
                observer_char_id=relation.observer_char_id,
                observed_claim=fact_by_id[relation.observed_fact_ref].claim,
                observation_claim=fact_by_id[relation.observation_fact_ref].claim,
            ))

    return BlueprintReviewChapter(
        chapter_ordinal=blueprint.chapter_ordinal,
        anomalous=bool(attention_rules),
        attention_rules=list(attention_rules),
        chapter_function=blueprint.chapter_function,
        coverage=BlueprintReviewCoverage(
            text_chars=len(text),
            covered_chars=covered_chars,
            loose_chars=loose_chars,
            uncovered_min_chars=HOLE_MIN_CHARS,
            segments=coverage_segments,
        ),
        beats=beats,
        causal_links=[
            BlueprintReviewCausalLink(
                before_claim=fact_by_id[edge.before].claim,
                after_claim=fact_by_id[edge.after].claim,
            )
            for edge in blueprint.causal_edges
        ],
        narrative_relations=relations,
        entry_state_claims=[
            fact_by_id[ref].claim for ref in blueprint.entry_state_refs
        ],
        ending_state_claims=[
            fact_by_id[ref].claim for ref in blueprint.ending_state_refs
        ],
        narration=[
            ReadingNarrationLine(
                grammatical_person=row.grammatical_person,
                narrator_char_id=row.narrator_char_id,
                focalizer_char_id=row.focalizer_char_id,
                pronoun_surfaces=list(row.pronoun_surfaces),
            )
            for row in blueprint.narration
        ],
    )


def _blueprint_review_snapshot(
    store: ArtifactStore,
    *,
    title: str,
) -> BlueprintReviewWorkpack:
    """从同一次严格读取的主体、字节摘要与生产报告组一份快照。"""
    from novel.stages.stage2 import extract
    from novel.stages.stage2 import global_review as global_review_ops

    invalid_rel = paths.invalid_of(BP_REL)
    blueprint_rel = invalid_rel if store.exists(invalid_rel) else BP_REL
    main, main_sha256 = store.read_own_with_sha256(
        MainText, MAIN_TEXT, require_current=True)
    blueprints, blueprint_sha256 = store.read_own_with_sha256(
        ChapterBlueprints, blueprint_rel, require_current=True)
    report = extract.run_check_only(store.ctx)
    global_review = global_review_ops.review_workpack(store.ctx, scope='all')
    # 全文已由 coverage.segments 完整携带；交叉材料引用同一 MainText 身份。
    global_review.pop('main_text_chapters', None)
    if report.provenance.invalid != (blueprint_rel == invalid_rel):
        raise ArtifactUnreadable(
            'Blueprint 工作包读取期间 formal/INVALID 主体发生切换；'
            '请刷新后重试')

    main_by_ordinal = {
        section.chapter_ordinal: section for section in main.root
    }
    blueprint_by_ordinal = {
        row.chapter_ordinal: row for row in blueprints.blueprints
    }
    if (len(main_by_ordinal) != len(main.root)
            or len(blueprint_by_ordinal) != len(blueprints.blueprints)
            or set(main_by_ordinal) != set(blueprint_by_ordinal)):
        # 章覆盖正确与否仍由 report 中的 ``chapter_coverage`` 判；
        # 这里只拒绝构造一份悠然少章的「完整工作包」。
        raise ArtifactUnreadable(
            'Blueprint 审阅工作包要求 MainText/ChapterBlueprints 章集'
            '完全相等；当前存在缺章、多章或重号，请先查看生产 '
            'CheckReport.chapter_coverage')

    attention = _chapter_attention(report)
    chapters = [
        _review_chapter(
            main_by_ordinal[ordinal], blueprint_by_ordinal[ordinal],
            attention_rules=attention.get(ordinal, []),
        )
        for ordinal in sorted(main_by_ordinal)
    ]
    chapters.sort(key=lambda row: (not row.anomalous, row.chapter_ordinal))
    report_payload = report.model_dump(by_alias=True, mode='json')
    blueprint_subject = blueprints.model_dump(
        by_alias=True, mode='json', exclude={'provenance'})
    subject_sha256 = _canonical_sha256(blueprint_subject)
    check = _compact_check(report)
    accounting = list(report.accounting)
    review_identity_sha256 = _canonical_sha256({
        'review_contract_version': BLUEPRINT_REVIEW_CONTRACT,
        'sid': store.ctx.sid,
        'subject_kind': 'invalid' if blueprint_rel == invalid_rel else 'formal',
        'subject_sha256': subject_sha256,
        'check': check.model_dump(mode='json'),
        'accounting': [row.model_dump(mode='json') for row in accounting],
        'mechanical_fields_hidden': list(_BLUEPRINT_MECHANICAL_FIELDS_HIDDEN),
        'chapters': [row.model_dump(mode='json') for row in chapters],
        'global_review': global_review,
    })
    rules_fingerprint = prov.rules_fingerprint('stage2')
    validation_seal_sha256 = _canonical_sha256({
        'code_version': store.ctx.code,
        'rules_fingerprint': rules_fingerprint,
        'main_text_artifact_sha256': main_sha256,
        'blueprint_artifact_sha256': blueprint_sha256,
        'check_report_sha256': _canonical_sha256(report_payload),
    })
    binding = BlueprintReviewBinding(
        blueprint_rel=blueprint_rel,
        subject_kind='invalid' if blueprint_rel == invalid_rel else 'formal',
        main_text_artifact_sha256=main_sha256,
        blueprint_artifact_sha256=blueprint_sha256,
        check_report_sha256=_canonical_sha256(report_payload),
        chapter_ordinals=sorted(main_by_ordinal),
    )
    base = BlueprintReviewWorkpack(
        sid=store.ctx.sid,
        title=title,
        review_identity_sha256=review_identity_sha256,
        validation_seal_sha256=validation_seal_sha256,
        review_token='bpr4_' + '0' * 64,
        global_review=global_review,
        subject_sha256=subject_sha256,
        binding_sha256='0' * 64,
        binding=binding,
        check=check,
        accounting=accounting,
        mechanical_fields_hidden=list(_BLUEPRINT_MECHANICAL_FIELDS_HIDDEN),
        chapters=chapters,
    )
    payload = base.model_dump(by_alias=True, mode='json')
    payload.pop('binding_sha256')
    payload.pop('review_token')
    # 差异只是相对已接受材料的导航，接受后 baseline 前移不能使同一正式
    # 主体的 token 自行过期；各行仍携带当前/原接受的精确业务摘要。
    payload.pop('review_delta')
    binding_sha256 = _canonical_sha256(payload)
    from novel.orchestrator.approvals import stage2_cross_review_scope
    from novel.stages.stage2.review_delta import compare, snapshot
    delta = compare(stage2_cross_review_scope(store.ctx.novel_dir.parent, store.ctx.sid),
                    snapshot(base))
    return base.model_copy(update={
        'binding_sha256': binding_sha256,
        'review_token': f'bpr4_{binding_sha256}',
        'review_delta': delta,
    })


def blueprint_review_workpack(
    base: str | os.PathLike[str], sid: str, *, code: str | None = None,
) -> BlueprintReviewWorkpack:
    """生成 book-level 只读 Blueprint 审阅包，并用首尾重读拒绝混合快照。

    本函数不产生 approval 记录、不改 gate，也不写任何 artifact。
    它只读 current MainText/Global/GlobalReview/ChapterBlueprints，并原样消费
    ``stage2.blueprint --check-only`` 的生产 CheckReport。

    普通 API/CLI 调用省略 ``code`` 并现场取当前版本；已经建立只读快照的
    operator 必须显式传入 epoch code，避免构包时再采一个实时代码时点。
    """
    novel_dir = paths.resolve_novel_dir(base, sid)
    snapshot_code = prov.code_version() if code is None else code
    ctx = readonly_ctx(novel_dir, BP_NODE, snapshot_code)
    store = ArtifactStore(ctx)
    title = paths.title_of(novel_dir)

    first = _blueprint_review_snapshot(store, title=title)
    second = _blueprint_review_snapshot(store, title=title)
    if first != second:
        raise ArtifactUnreadable(
            'MainText、ChapterBlueprints 或生产 CheckReport 在 Blueprint '
            '工作包构造期间发生变化；请刷新后重试')
    return first
