#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from contracts.artifacts import (
    STAGE1_MAIN_TEXT, STAGE2_BLUEPRINT_CHARACTER_PROJECTION,
    STAGE2_CHAPTER_BLUEPRINTS, STAGE2_GLOBAL_GROUNDING, STAGE2_GLOBAL_REVIEW,
    STAGE2_GLOBAL_REVIEW_DECISIONS,
)
from contracts.invariants import review_effect_of, review_generation_impact_of
from contracts.schema.evaluation import (
    EvidenceRef, GlobalReview, GlobalReviewBlockingScopeDecision,
    GlobalReviewDecision, GlobalReviewDecisionSet, GlobalReviewItem,
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
from novel.quality.global_review import (
    apply_decision_set, blocking_scope_sha256, require_current,
    GenerationSafety, generation_safety, review_effect_counts,
    review_group_key, review_status, subject_sha256, UNBOUND_WORKPACK_SHA256,
)
from novel.run import RunContext
from novel.store import ArtifactStore


REL = STAGE2_GLOBAL_REVIEW
DEFAULT_DECISIONS = STAGE2_GLOBAL_REVIEW_DECISIONS


#: `--review-scope` 的取值面与默认值。默认只出能挡 Stage4 的那一面：
#: 人的注意力是稀缺资源，把 95 项高影响事实和 50 项披露项混成一张 145 行的表，
#: 结果是两边都读得潦草。⚠ **它只裁剪只读输出，不裁剪评测面**——
#: `global_review.json` 仍枚举全部主体，`current` 仍要求集合完全一致。
REVIEW_SCOPES: Final = ('blocking', 'all')
DEFAULT_REVIEW_SCOPE: Final = 'blocking'
_TEMPLATE_ACTOR: Final = '待填·actor'
_TEMPLATE_SCOPE_REASON: Final = (
    '待人工核验完整 blocking workpack 与所列全部正文章后改为 PASS')
_TEMPLATE_ITEM_REASON: Final = (
    '待人工核验：请替换本句并将已核实项改为 PASS 或 FAIL')


def _scoped_items(items: list[GlobalReviewItem], scope: str) -> list[GlobalReviewItem]:
    if scope not in REVIEW_SCOPES:
        raise TerminalState(
            f'--review-scope 只接受 {list(REVIEW_SCOPES)}，收到 {scope!r}')
    if scope == 'all':
        return list(items)
    return [item for item in items if review_effect_of(item.kind) == 'blocking']


def review_template(
    ctx: RunContext, *, scope: str = DEFAULT_REVIEW_SCOPE,
) -> GlobalReviewDecisionSet:
    """返回绑定当前逐项 hash/evidence 的安全人工复核骨架。

    默认 blocking 模板只交一个完整 scope：状态为 `UNVER`，所以原样应用也**不会打开
    s2 gate**；执行者核完 workpack 后须手工复制该包的 SHA，再把状态改成 PASS，
    FAIL/UNVER 例外放进 ``decisions``。服务端按 scope hash 展开回逐项决定，不让 Commander 重复输出几十份
    同义 PASS 理由。`scope='all'` 仍给逐项骨架，供披露项单独裁决。

    ⚠ `scope='blocking'`（默认）只交出能挡 Stage4 的那一面。**这不会让披露项被
    自动 PASS**：没进模板的项在盘上仍是 `pending`，仍逐条出现在 inbox 与终审包。
    要整份就传 `--review-scope all`。
    """
    if scope == 'blocking':
        workpack = review_workpack(ctx, scope=scope)
        binding = workpack['binding']
        if not isinstance(binding, dict):
            raise TerminalState('blocking workpack 缺少可提交的 scope binding')
        return GlobalReviewDecisionSet(
            actor=_TEMPLATE_ACTOR,
            decisions=[],
            blocking_scope=GlobalReviewBlockingScopeDecision(
                expected_source_sha256=str(binding['expected_source_sha256']),
                expected_blocking_scope_sha256=str(
                    binding['expected_blocking_scope_sha256']),
                # 模板生成发生在人工阅读之后；若这里替执行者现场刷新成当前
                # workpack hash，旧包 A 读完后再生成新模板 B，就能不读 B 而把
                # B 的阅读凭证一起带走。全零是不可提交占位，必须从实际读过的
                # workpack 顶层手工复制真实 hash。
                expected_workpack_sha256=UNBOUND_WORKPACK_SHA256,
                expected_item_count=int(binding['expected_item_count']),
                reviewed_chapter_ordinals=list(
                    binding['reviewed_chapter_ordinals']),
                state='UNVER',
                reason=_TEMPLATE_SCOPE_REASON,
                evidence=[],
            ),
        )
    store = ArtifactStore(ctx)
    if not store.exists(REL):
        raise UpstreamMissing(f'{REL} 不在盘上；先生成/重建 stage2.global')
    current = store.read_own(GlobalReview, REL)[0]
    return GlobalReviewDecisionSet(
        actor=_TEMPLATE_ACTOR,
        decisions=[GlobalReviewDecision(
            item_id=item.item_id,
            expected_subject_sha256=item.subject_sha256,
            state='UNVER',
            reason=_TEMPLATE_ITEM_REASON,
            evidence=list(item.evidence),
        ) for item in _scoped_items(current.items, scope)],
    )


def review_template_json(
    ctx: RunContext, *, scope: str = DEFAULT_REVIEW_SCOPE,
) -> str:
    """stdout 用注册表版本化 JSON；相对/绝对/API 三条输入路吃同一形状。"""
    payload = review_template(ctx, scope=scope).model_dump(mode='json', by_alias=True)
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


#: v3 把旧 ``review_note`` 里混在一句话中的两条正交语义拆开：是否必须通读
#: 全书，与当前展示的是哪一类证据，互不覆盖。legend 只在顶层出现一次；每行只交
#: 两个闭集标签，避免 100 多行重复同一段安全说明。
_WORKPACK_LEGEND: Final = {
    'reading_scope': {
        'whole_book': '必须通读全书核对；0 个命中不等于 PASS。',
        'localized': '所列正文上下文只缩小阅读范围，不产生自动裁决。',
    },
    'evidence_mode': {
        'unresolved_formal_evidence': (
            '正式 evidence 含 source=None、缺失或无效坐标；不得用 claim 字面命中替代。'),
        'formal_evidence': (
            '优先展示 hash-bound EvidenceRef、正式坐标与上下文；不重复写入字面命中。'),
        'claim_literal': (
            '没有正式 evidence 槽；展示 claim 的正文精确字面命中，仅供定位。'),
        'none': '没有可展示的正式证据或 claim 字面命中；不得据此自动 PASS。',
    },
    'generation_impact': {
        'must_fix': '不能安全从写作视图删除；FAIL/UNVER 必须修正或显式人工裁决。',
        'quarantine_or_fix': 'UNVER 可整组隔离，FAIL 仍须修正；不会改名为 PASS。',
        'disclose': '不进入写作事实面，不拦下游，但保留审计与最终披露。',
    },
    'fate_companion': (
        'blocking role 行附带同人物 fate 的只读主体、状态与正式证据，'
        '只供成对阅读，不增加或代替任何 decision item。'),
    'decision': 'workpack 只提供只读材料；PASS/FAIL/UNVER 必须由人显式提交。',
    'whole_book_text': (
        '只要本包含 reading_scope=whole_book，main_text_chapters 就逐章给出完整正文；'
        '每章只出现一次，行级证据仍只负责定位，不能代替通读。'),
}


def _ordered_workpack_rows(
    rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    """按主体稳定成卡；blocking 卡先行，卡内保持原 review 顺序。

    ``rows`` 来自 ``GlobalReview.items`` 的一次顺序扫描。普通 ``dict`` 的插入序就是
    每组首次出现的位置，因此同一优先级不另造按字面排序的第二套顺序。
    """
    groups: dict[str, list[dict[str, object]]] = {}
    has_blocking: dict[str, bool] = {}
    for row in rows:
        group_key = str(row['group_key'])
        groups.setdefault(group_key, []).append(row)
        has_blocking[group_key] = (
            has_blocking.get(group_key, False) or row['effect'] == 'blocking')
    keys = [key for key in groups if has_blocking[key]]
    keys.extend(key for key in groups if not has_blocking[key])
    return [row for key in keys for row in groups[key]]


def _attach_blocking_role_fate_companions(
    rows: list[dict[str, object]], *, review: GlobalReview,
    claim_payload: dict[str, object],
    evidence_slots: list[tuple[str, object]], chapters: dict[int, object],
) -> None:
    """给 blocking role 行附上同人物 fate，只供成对阅读，不增加决定项。

    ``enumerate_required`` 保证每个人物各有一条 role 与 fate；这里仍按生产
    ``review_group_key`` 关闭式核对唯一配对，避免路径截字符串另造第二套人物归属尺子。
    companion 不复制 claim-literal、registered decision evidence 或 group 元数据，只带
    fate 主体身份、当前状态及正式证据材料。
    """
    fate_by_group: dict[str, list[GlobalReviewItem]] = {}
    for item in review.items:
        if item.kind == 'fate':
            fate_by_group.setdefault(
                review_group_key(item.subject_path), []).append(item)
    for row in rows:
        if row['kind'] != 'role' or row['effect'] != 'blocking':
            continue
        group_key = str(row['group_key'])
        matches = fate_by_group.get(group_key, [])
        if len(matches) != 1:
            raise TerminalState(
                f'blocking role 卡 {group_key!r} 必须恰有一条同人物 fate 复核项，'
                f'实得 {len(matches)} 条')
        fate = matches[0]
        prefix = f'{fate.subject_path}.'
        relevant_slots = [
            (path, evidence) for path, evidence in evidence_slots
            if path == fate.subject_path or path.startswith(prefix)
        ]
        formal, unresolved = _formal_materials(relevant_slots, chapters)
        try:
            subject = _subject_at(claim_payload, fate.subject_path)
        except (KeyError, IndexError, StopIteration) as exc:
            raise TerminalState(
                f'blocking role 卡 {group_key!r} 的 fate 主体无法从当前 Global 回取') from exc
        row['fate_companion'] = {
            'read_only': True,
            'kind': fate.kind,
            'item_id': fate.item_id,
            'subject_path': fate.subject_path,
            'subject_sha256': fate.subject_sha256,
            'current_state': fate.state,
            'current_reason': fate.reason,
            'generation_disposition': (
                None if fate.generation_disposition is None
                else fate.generation_disposition.model_dump(mode='json')),
            'effect': review_effect_of(fate.kind),
            'generation_impact': review_generation_impact_of(fate.kind),
            'subject': subject,
            'formal_evidence': formal,
            'unresolved_formal_evidence': unresolved,
        }


GLOBAL_REVIEW_TOKEN_PREFIX: Final = 's2gr1_'


def review_identity(
    grounding_payload: dict[str, object], review: GlobalReview,
    main_text_subject_sha256: str,
) -> dict[str, str]:
    """``seen.global`` 与终审席位共用的无正文阅读身份。

    四枚 SHA 都由盘上当前主体确定性重算：Global 主体、review 完整字节（状态、
    理由、actor 都在内）、MainText 主体与 blocking scope。任何一格变化都换
    token，所以旧阅读事实不可能配新主体提交；同时它不含 workpack 的逐项命中
    材料，审批投影在每次 GET 时重算也不必重建整份 workpack。
    """
    subject = subject_sha256(grounding_payload)
    review_sha256 = subject_sha256(review.model_dump(mode='json'))
    identity = {
        'source_sha256': review.source_sha256,
        'current_subject_sha256': subject,
        'review_sha256': review_sha256,
        'main_text_subject_sha256': main_text_subject_sha256,
        'blocking_scope_sha256': blocking_scope_sha256(review),
    }
    token = GLOBAL_REVIEW_TOKEN_PREFIX + hashlib.sha256(json.dumps(
        {'kind': 'stage2-global-review-seen-v1', **identity},
        ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')).hexdigest()
    return {**identity, 'review_token': token}


def review_token(ctx: RunContext) -> str:
    """现算当前 Global 审阅面的 opaque token；主体不 current 时按现役异常拒绝。"""
    store = ArtifactStore(ctx)
    grounding = store.read_own(
        GlobalGrounding, STAGE2_GLOBAL_GROUNDING, require_current=True)[0]
    review = store.read_own(GlobalReview, REL, require_current=True)[0]
    main = store.read_own(MainText, STAGE1_MAIN_TEXT, require_current=True)[0]
    grounding_payload = grounding.model_dump(mode='json', by_alias=True)
    require_current(grounding_payload, review)
    return review_identity(
        grounding_payload, review,
        subject_sha256(main.model_dump(mode='json', by_alias=True)),
    )['review_token']


def review_workpack(
    ctx: RunContext, *, scope: str = DEFAULT_REVIEW_SCOPE,
    replaced_input_keys: tuple[str, ...] = (),
) -> dict[str, object]:
    """只读人工核验材料；精确命中是证据候选，不是自动裁决。

    ⚠ 与 `review_template` 同一条 `scope` 纪律：默认只出 blocking 面，
    裁剪的是**阅读范围**而不是评测面；披露项没进 workpack 不等于被放行。

    ``replaced_input_keys`` 只供 reviewed GlobalEdit 被 HARD 拒绝后的中断恢复：
    正式 Global 仍是该次审阅的原主体，只是书内 edit 输入已经换成失败动作。
    普通工作包不给这个参数，不能把任意 stale Global 伪装成 current。
    """
    store = ArtifactStore(ctx)
    grounding = store.read_own(
        GlobalGrounding, STAGE2_GLOBAL_GROUNDING, require_current=True,
        replaced_input_keys=replaced_input_keys)[0]
    review = store.read_own(GlobalReview, REL, require_current=True)[0]
    main = store.read_own(MainText, STAGE1_MAIN_TEXT, require_current=True)[0]
    main_text_subject_sha256 = subject_sha256(
        main.model_dump(mode='json', by_alias=True))
    payload = grounding.model_dump(mode='json', by_alias=True)
    claim_payload = without_global_evidence(payload)
    assert isinstance(claim_payload, dict)
    evidence_slots = [
        (path, owner.get(field))
        for path, owner, field in iter_global_evidence_slots(payload)
    ]
    chapters = {chapter.chapter_ordinal: chapter for chapter in main.root}
    rows: list[dict[str, object]] = []
    for item in _scoped_items(review.items, scope):
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
        if not relevant_slots:
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
        reading_scope = (
            'whole_book' if item.kind in _WHOLE_BOOK_KINDS else 'localized')
        if unresolved:
            evidence_mode = 'unresolved_formal_evidence'
        elif formal:
            evidence_mode = 'formal_evidence'
        elif hits:
            evidence_mode = 'claim_literal'
        else:
            evidence_mode = 'none'
        effect = review_effect_of(item.kind)
        rows.append({
            'item_id': item.item_id, 'kind': item.kind,
            'subject_path': item.subject_path,
            'subject_sha256': item.subject_sha256,
            'current_state': item.state,
            'current_reason': item.reason,
            'generation_disposition': (
                None if item.generation_disposition is None
                else item.generation_disposition.model_dump(mode='json')),
            'reading_scope': reading_scope,
            'evidence_mode': evidence_mode,
            'effect': effect,
            'generation_impact': review_generation_impact_of(item.kind),
            'group_key': review_group_key(item.subject_path),
            'subject': subject, 'registered_evidence': [
                evidence.model_dump(mode='json') for evidence in item.evidence],
            'formal_evidence': formal,
            'unresolved_formal_evidence': unresolved,
            'exact_literal_hits': hits,
        })
    if scope == 'blocking':
        _attach_blocking_role_fate_companions(
            rows, review=review, claim_payload=claim_payload,
            evidence_slots=evidence_slots, chapters=chapters)
    rows = _ordered_workpack_rows(rows)
    needs_whole_book = any(
        row.get('reading_scope') == 'whole_book' for row in rows)
    payload: dict[str, object] = {
        'kind': 'stage2-global-review-workpack-v4',
        'sid': ctx.sid,
        'scope': scope,
        'read_only': True,
        'auto_decisions': False,
        'legend': copy.deepcopy(_WORKPACK_LEGEND),
        # ``seen.global`` / 终审席位提交时回填的 opaque 阅读凭证；它绑定 review
        # 完整字节而不是本包的逐项命中材料，因而与 ``workpack_sha256`` 是两枚。
        'review_token': review_identity(
            payload, review, main_text_subject_sha256)['review_token'],
        # 绑定完整 typed MainText 主体，不只绑定章号或最多 24 个展示命中；
        # 未展示上下文之外改一个字，也必须让旧 workpack/scope 失效。
        'main_text_subject_sha256': main_text_subject_sha256,
        # v3 曾把六类行标成 whole_book，却只交 hash 与局部命中；Commander
        # 正确地把这种材料不足判成 UNVER，随后误入 GlobalEdit，形成“模型成功、
        # 交付失败”。v4 对 whole-book 包把 current MainText 只放一遍；行仍引用
        # 同一主体/证据，不复制正文，workpack SHA 也会绑定实际读到的全部字节。
        'main_text_chapters': ([{
            'chapter_ordinal': int(chapter.chapter_ordinal),
            'text': chapter.text,
        } for chapter in main.root] if needs_whole_book else []),
        'binding': ({
            'expected_source_sha256': review.source_sha256,
            'expected_blocking_scope_sha256': blocking_scope_sha256(review),
            'main_text_subject_sha256': main_text_subject_sha256,
            'expected_item_count': len(rows),
            'reviewed_chapter_ordinals': [
                int(chapter.chapter_ordinal) for chapter in main.root],
        } if scope == 'blocking' else None),
        'items': rows,
    }
    # 哈希故意不含它自己；模板与应用口都调本函数现算，
    # 不从 grounding/review 另拼一份“看起来相同”的阅读面。
    payload['workpack_sha256'] = subject_sha256(payload)
    return payload


def review_workpack_json(
    ctx: RunContext, *, scope: str = DEFAULT_REVIEW_SCOPE,
) -> str:
    return json.dumps(
        review_workpack(ctx, scope=scope),
        ensure_ascii=False, indent=2, sort_keys=True)


def apply_file(ctx: RunContext, decision_path: str = '') -> GlobalReview:
    """Apply audited per-item or complete-scope decisions with optimistic hashes."""
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
                f'{rel} 不在盘上；放入 GlobalReviewDecisionSet '
                '（actor + blocking_scope/decisions）')
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
    from novel.llm import stage2_global_runs

    # review 是 Global commit journal 的伴生 touched path；若绕过 prepared
    # reservation 单独改它，原 producer 的 frozen baseline/expected 都无法再恢复。
    # 决策文件先按路径/schema 读入，当前 grounding/review/workpack 则必须在同一
    # 所有权窗口内读取、校验并写回，避免锁前快照覆盖刚 committed 的新 bundle。
    with stage2_global_runs.mutation_ownership(ctx):
        grounding = store.read_own(
            GlobalGrounding, STAGE2_GLOBAL_GROUNDING)[0]
        grounding_payload = grounding.model_dump(by_alias=True)
        current = store.read_own(GlobalReview, REL)[0]
        require_current(grounding_payload, current)
        main_text = store.read_own(MainText, STAGE1_MAIN_TEXT)[0]
        blocking_workpack = (
            review_workpack(ctx, scope='blocking')
            if decisions.blocking_scope is not None else None
        )
        updated = apply_decision_set(
            current,
            decisions,
            expected_chapter_ordinals=tuple(
                int(section.chapter_ordinal) for section in main_text.root),
            expected_workpack_sha256=(
                str(blocking_workpack['workpack_sha256'])
                if blocking_workpack is not None else None),
        )
        require_current(grounding_payload, updated)
        store.write_round(REL, updated)
        return updated


def is_approved(ctx: RunContext, review: GlobalReview) -> bool:
    """Bind approval to the complete, current Stage2 subject (blocking surface)."""
    grounding = ArtifactStore(ctx).read_own(
        GlobalGrounding, STAGE2_GLOBAL_GROUNDING)[0]
    return review_status(
        grounding.model_dump(by_alias=True), review)[2]


def is_generation_safe(ctx: RunContext, review: GlobalReview) -> bool:
    """是否可把当前 review 经确定性隔离后交给写作消费方。"""
    grounding = ArtifactStore(ctx).read_own(
        GlobalGrounding, STAGE2_GLOBAL_GROUNDING)[0]
    return generation_safety(
        grounding.model_dump(by_alias=True), review).safe


def _counts(review: GlobalReview) -> dict[str, int]:
    counts = {state: 0 for state in ('pending', 'PASS', 'FAIL', 'UNVER')}
    for item in review.items:
        counts[item.state] += 1
    for effect, states in review_effect_counts(review).items():
        counts.update({f'{effect}.{state}': n for state, n in states.items()})
    return counts


def approval_status(ctx: RunContext) -> tuple[bool, bool, dict[str, int]]:
    """返回 ``(评测面当前, blocking 面逐项 PASS, 计数)``；闸门与终验共用这一处。

    ⚠ 计数是**两轴并存**的一张平表，不是两张：四个裸状态键（全书合计，供旧消费方
    与终审包读）加上 ``blocking.*`` / ``disclosure.*`` 八个分效果键。合计与分效果
    **不互相推导**——把 disclosure 从合计里减掉，就再也说不清「一共有多少项被人看过」。
    """
    store = ArtifactStore(ctx)
    grounding = store.read_own(GlobalGrounding, STAGE2_GLOBAL_GROUNDING)[0]
    review = store.read_own(GlobalReview, REL)[0]
    _subject, current, approved = review_status(
        grounding.model_dump(by_alias=True), review)
    return current, approved, _counts(review)


def generation_status(ctx: RunContext) -> tuple[GenerationSafety, dict[str, int]]:
    """返回写作安全结论与完整四态计数；不把隔离过的 UNVER 改名为 PASS。"""
    store = ArtifactStore(ctx)
    grounding = store.read_own(GlobalGrounding, STAGE2_GLOBAL_GROUNDING)[0]
    review = store.read_own(GlobalReview, REL)[0]
    return (
        generation_safety(grounding.model_dump(by_alias=True), review),
        _counts(review),
    )


def generation_grounding(store: ArtifactStore) -> GlobalGrounding:
    """读取下游唯一可消费的 Stage2 视图，并如实记录 review 输入边。

    正式 ``global_grounding.json`` 仍保留完整审计事实；这里不迁移、不覆写它。
    对已逐项处置的 UNVER，只从本次下游内存视图删除所属 secret/relation/world-rule
    完整组，再以 ``GlobalGrounding`` schema 复验。这样终审仍看得到原事实与决定，
    写作模型却不可能收到被隔离的未知事实。
    """
    grounding = store.read(GlobalGrounding, STAGE2_GLOBAL_GROUNDING)
    review = store.read(GlobalReview, REL)
    safety = generation_safety(
        grounding.model_dump(by_alias=True), review)
    if not safety.safe:
        raise ApprovalRequired(
            'Stage2 写作视图未收口：仍有 pending/FAIL、不可隔离 UNVER，'
            f'或 review 已过期；current={safety.current}，'
            f'blocking_items={list(safety.blocking_item_ids)}')
    if not safety.quarantined_groups:
        return grounding

    payload = grounding.model_dump(mode='json', by_alias=True)
    entities = payload['entities']
    canon = payload['canon']
    secret_ids = {
        group.split(':', 1)[1] for group in safety.quarantined_groups
        if group.startswith('secret:')
    }
    relation_ids = {
        group.split(':', 1)[1] for group in safety.quarantined_groups
        if group.startswith('relation:')
    }
    world_rule_ids = {
        group.split(':', 1)[1] for group in safety.quarantined_groups
        if group.startswith('world_rule:')
    }
    canon['secrets'] = [
        row for row in canon['secrets']
        if row['secret_id'] not in secret_ids
    ]
    canon['world_rules'] = [
        row for row in canon['world_rules']
        if row['rule_id'] not in world_rule_ids
    ]
    # 一条 via_secret relation 在秘密被隔离后也必须一起消失；留下悬空引用
    # 既过不了 schema，也会从关系通道把同一未知事实重新泄给写手。
    entities['relations'] = [
        row for row in entities['relations']
        if row['relation_id'] not in relation_ids
        and not (
            row['visibility']['kind'] == 'via_secret'
            and row['visibility']['secret_id'] in secret_ids
        )
    ]
    try:
        return GlobalGrounding.model_validate(payload)
    except ValidationError as exc:  # 校验细节不应变成一条模糊的继续路径
        raise GateBlocked(
            'Stage2 UNVER 隔离后的写作视图不再符合 GlobalGrounding；'
            '该组不能安全降级，必须用 GlobalEdit 修复') from exc


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

    safety, counts = generation_status(ctx)
    if not safety.safe:
        raise ApprovalRequired(
            'stage2 全局复核未打开：必须是当前完整评测面，且 blocking 项要么逐项 '
            'PASS，要么是带 hash-bound quarantine_group 的可隔离 UNVER；'
            f'current={safety.current}，blocking_items='
            f'{list(safety.blocking_item_ids)}，counts={counts}',
            hint=('先运行 `stage2.global --emit-review-template`（默认只出 blocking 面，'
                  '要整份加 `--review-scope all`），逐项核验后用 '
                  '`stage2.global --review-decisions <书内json>` 提交；'
                  'FAIL 必须经 typed GlobalEdit 修复；UNVER 只有可确定性隔离的 '
                  'secret/relation/world_rule 组能显式 quarantine，不能改名为 PASS。'
                  'disclosure 项（名字区分度 / 称谓场合 / 结局）不挡门，'
                  '但仍必须在 inbox 与终审包里逐条披露，不许自动 PASS'))

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

    if not store.exists(STAGE2_BLUEPRINT_CHARACTER_PROJECTION):
        raise UpstreamMissing(
            f'{STAGE2_BLUEPRINT_CHARACTER_PROJECTION} 不在盘上；'
            'Stage4 不能让 Blueprint 拿一份未绑当前 global 的旧称名表过门',
            hint='stage2.global --renormalize（0 LLM）重建称名投影')
    projection_freshness = store.freshness(
        STAGE2_BLUEPRINT_CHARACTER_PROJECTION)
    if projection_freshness.state == 'stale':
        raise UpstreamStale(
            f'{STAGE2_BLUEPRINT_CHARACTER_PROJECTION} 已不是当前最新 '
            'global + MainText 的确定性投影；拒绝让旧 Blueprint 过门',
            hint='stage2.global --renormalize（0 LLM）重建称名投影')
    if projection_freshness.state != 'fresh':
        raise UpstreamUnproven(
            f'{STAGE2_BLUEPRINT_CHARACTER_PROJECTION} 证明不了自己来自当前'
            f'最新 global + MainText（{projection_freshness.state}/'
            f'{projection_freshness.reason}）',
            hint='stage2.global --renormalize（0 LLM）重建称名投影')

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
            f'{STAGE2_CHAPTER_BLUEPRINTS} 已不是当前称名投影/main_text 上的结果；'
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
