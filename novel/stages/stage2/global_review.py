#!/usr/bin/env python3
from __future__ import annotations

from novel.stages.stage2 import grounding as grounding_io

import copy
import hashlib
import json
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from contracts.artifacts import (
    STAGE1_MAIN_TEXT, STAGE2_CHARACTERS, STAGE2_BLUEPRINT_CHARACTER_PROJECTION,
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
from contracts.schema.characters import CharacterRegistry

from novel import paths
from novel.errors import (
    ApprovalRequired, BlockingFindingsUnresolved, GateBlocked, PathRefused,
    TerminalState, UpstreamMissing, UpstreamStale, UpstreamUnproven,
)
from novel.orchestrator.jobs import service_dir
from novel.quality.global_review import (
    alias_review_key, apply_decision_set, blocking_scope_sha256, require_current,
    GenerationSafety, generation_safety, review_effect_counts,
    review_group_key, review_status, review_source, review_evidence_slots,
    without_review_evidence, subject_sha256, UNBOUND_WORKPACK_SHA256,
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
                    and (segment in {
                        str(value) for key, value in item.items() if key.endswith('_id')
                    } or (isinstance(item.get('surface'), str)
                          and segment == alias_review_key(item['surface']))))
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


def _characters_payload(store: ArtifactStore, *, require_current: bool = False) -> dict[str, object]:
    return store.read_own(CharacterRegistry, STAGE2_CHARACTERS,
                          require_current=require_current)[0].model_dump(mode='json', by_alias=True)


def _formal_materials(
    slots: list[tuple[str, object]], chapters: dict[int, object],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """本地产物 anchors 编译成阅读定位；正文只取自当前 MainText。"""
    materials = []
    unresolved = []
    seen = set()
    for claim_path, anchor in slots:
        ordinal = anchor.get('chapter_ordinal') if isinstance(anchor, dict) else None
        text = getattr(chapters.get(ordinal), 'text', None)
        start = anchor.get('start') if isinstance(anchor, dict) else None
        end = anchor.get('end') if isinstance(anchor, dict) else None
        valid = (isinstance(text, str) and type(start) is int and type(end) is int
                 and 0 <= start < end <= len(text)
                 and _text_sha256(text[start:end]) == anchor.get('text_sha256'))
        if not valid:
            unresolved.append({'claim_path': claim_path, 'chapter_ordinal': ordinal,
                               'reason': '正式 anchor 缺失、坐标非法或正文摘要不一致'})
            continue
        identity = ordinal, start, end
        if identity in seen:
            continue
        seen.add(identity)
        ref = EvidenceRef(artifact=f'{STAGE1_MAIN_TEXT}#chapter/{ordinal}',
                          subject_sha256=_text_sha256(text), start=start, end=end)
        materials.append({'evidence_ref': ref.model_dump(mode='json'),
                          'chapter_ordinal': ordinal, 'literal': text[start:end],
                          'start': start, 'end': end,
                          'context': text[max(0, start - 60):min(len(text), end + 60)]})
    return materials, unresolved


_WHOLE_BOOK_KINDS = frozenset({'character_set_complete', 'relation_set_complete',
                              'world_rule_set_complete', 'secret_set_complete'})
_NO_FORMAL_EVIDENCE_KINDS = _WHOLE_BOOK_KINDS
_WORKPACK_LEGEND: Final = {
    'reading_scope': {'whole_book': '通读完整正文反查遗漏，空集合也必须明确审阅。',
                      'localized': '证据只定位主体；支持性、归属与披露时间仍须语义判断。'},
    'evidence_mode': {'unresolved_formal_evidence': '证据不完整；由对应 owner 免费订正。',
                      'formal_evidence': '共享 anchor 回取的正式正文定位。',
                      'claim_literal': '字面命中仅供定位，不替代正式证据。',
                      'none': '无局部定位材料，不等于 PASS。'},
    'generation_impact': {'must_fix': '未知或否定保持待审，由对应 owner 修订。',
                         'quarantine_or_fix': '只有显式 UNVER 整组处置才可隔离；不是 PASS。'},
    'identity': 'canonical 是内部登记名；role 只允许首次公开身份。identity_revealed_from 只约束称谓与实体的映射，不禁止称谓先出现。',
    'time': 'effective_until 包含该章。生效、失效、读者揭示与证据位置独立；允许先透露未来事实，也允许结束后揭示。',
    'arc_secret': '同场产生并当场揭晓的惊喜不进入 Global。默认跨章；同章例外须说明独立情节单元间的持续约束，不能凭段落数冒充已验分场。冷揭示不虚构铺垫。',
    'whole_book_text': '完整正文按章只提供一次，行级证据不能代替反向漏抽检查。',
    'decision': '所有语义决定必须由 Commander 明确提交。机械 lint 不是语义通过。',
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


GLOBAL_REVIEW_TOKEN_PREFIX: Final = 's2gr2_'


def review_identity(
    grounding_payload: dict[str, object], review: GlobalReview,
    main_text_subject_sha256: str, *, characters: dict[str, object] | None = None,
) -> dict[str, str]:
    """``seen.global`` 与终审席位共用的无正文阅读身份。

    四枚 SHA 都由盘上当前主体确定性重算：Global 主体、review 完整字节（状态、
    理由、actor 都在内）、MainText 主体与 blocking scope。任何一格变化都换
    token，所以旧阅读事实不可能配新主体提交；同时它不含 workpack 的逐项命中
    材料，审批投影在每次 GET 时重算也不必重建整份 workpack。
    """
    subject = subject_sha256(review_source(grounding_payload, characters))
    review_sha256 = subject_sha256(review.model_dump(mode='json'))
    identity = {
        'source_sha256': review.source_sha256,
        'current_subject_sha256': subject,
        'review_sha256': review_sha256,
        'main_text_subject_sha256': main_text_subject_sha256,
        'blocking_scope_sha256': blocking_scope_sha256(review),
    }
    token = GLOBAL_REVIEW_TOKEN_PREFIX + hashlib.sha256(json.dumps(
        {'kind': 'stage2-global-review-seen-v2', **identity},
        ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')).hexdigest()
    return {**identity, 'review_token': token}


def review_token(ctx: RunContext) -> str:
    """现算当前 Global 审阅面的 opaque token；主体不 current 时按现役异常拒绝。"""
    return str(review_workpack(ctx)['review_token'])


def _material_rows(payload, review, main, characters_payload, scope):
    """当前与原归档材料使用同一语义/证据展开器，历史不由另一套投影猜测。"""
    review_payload = review_source(payload, characters_payload)
    claim_payload = without_review_evidence(review_payload)
    evidence_slots = review_evidence_slots(review_payload)
    require_current(payload, review, characters=characters_payload)
    chapters = {chapter.chapter_ordinal: chapter for chapter in main.root}
    rows: list[dict[str, object]] = []
    for item in _scoped_items(review.items, scope):
        subject_path = item.subject_path
        lookup_path = subject_path
        if item.kind in {'name_distinctiveness', 'secret_setup', 'reveal'}:
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
        reading_scope = 'whole_book' if item.kind in _WHOLE_BOOK_KINDS else 'localized'
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
            'resolution_required': item.resolution_required,
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
    rows = _ordered_workpack_rows(rows)
    return rows


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
    grounding, global_sha = grounding_io.read_own_with_sha256(store, STAGE2_GLOBAL_GROUNDING,
        require_current=True, replaced_input_keys=replaced_input_keys)
    review, review_sha = store.read_own_with_sha256(GlobalReview, REL, require_current=True)
    main, main_sha = store.read_own_with_sha256(MainText, STAGE1_MAIN_TEXT, require_current=True)
    registry, characters_sha = store.read_own_with_sha256(CharacterRegistry, STAGE2_CHARACTERS, require_current=True)
    main_text_subject_sha256 = subject_sha256(
        main.model_dump(mode='json', by_alias=True))
    payload = grounding.model_dump(mode='json', by_alias=True)
    characters_payload = registry.model_dump(mode='json', by_alias=True)
    rows = _material_rows(payload, review, main, characters_payload, scope)
    needs_whole_book = any(
        row.get('reading_scope') == 'whole_book' for row in rows)
    payload: dict[str, object] = {
        'kind': 'stage2-global-review-workpack-v6',
        'sid': ctx.sid,
        'scope': scope,
        'read_only': True,
        'auto_decisions': False,
        'legend': copy.deepcopy(_WORKPACK_LEGEND),
        # ``seen.global`` / 终审席位提交时回填的 opaque 阅读凭证；它绑定 review
        # 完整字节而不是本包的逐项命中材料，因而与 ``workpack_sha256`` 是两枚。
        'review_token': review_identity(
            payload, review, main_text_subject_sha256, characters=characters_payload)['review_token'],
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
        'material_source': {'characters_sha256': characters_sha, 'global_sha256': global_sha,
                            'review_sha256': review_sha, 'main_text_sha256': main_sha},
        'core_edit_binding': {
            'expected_characters_sha256': store.full_sha256_or_none(STAGE2_CHARACTERS),
            'expected_global_sha256': store.full_sha256_or_none(STAGE2_GLOBAL_GROUNDING),
        },
    }
    # 哈希故意不含它自己；模板与应用口都调本函数现算，
    # 不从 grounding/review 另拼一份“看起来相同”的阅读面。
    # 覆盖导航不进入材料身份；保存接受本身不能令同一份包自行过期。
    # Global 节点接受的义务是 blocking 面，披露项继续沿原 typed UNVER 账展示。
    from novel.orchestrator.approvals import stage2_review_history
    from novel.stages.stage2 import review_obligations
    commander_pack = {**payload, 'items': [row for row in rows if row['effect'] == 'blocking']}
    history = stage2_review_history(ctx.novel_dir.parent, ctx.sid)
    coverage = review_obligations.coverage(
        'stage2.global', review_obligations.global_catalog(commander_pack),
        history)
    removed = _removed_problem_materials(store, coverage, history)
    if removed:
        payload['removed_problem_materials'] = removed
        # 已明确接受的删除义务仍保留原材料身份，避免接受自己令 token 过期。
        payload['review_token'] = GLOBAL_REVIEW_TOKEN_PREFIX + subject_sha256({
            'current_token': payload['review_token'], 'removed_problem_materials': removed})
    payload['workpack_sha256'] = subject_sha256(payload)
    payload['review_coverage'] = coverage.model_dump(mode='json')
    return payload


def _removed_problem_materials(store, coverage, history) -> list[dict]:
    """按原否定义务与四份归档字节恢复旧主张；不从当前稿或名称猜回被删内容。"""
    from novel.errors import NovelError
    from novel.stages.stage2 import review_obligations
    result = []
    for obligation in coverage.scope.obligations:
        if obligation.kind != 'removed_problem':
            continue
        origin = obligation.problem_subject_sha256
        original = next((decision for decision in reversed(history)
            if decision.node == 'stage2.global' and decision.obligation_receipt is not None
            and any(unit.obligation.obligation_id == obligation.obligation_id
                    and unit.obligation.kind != 'removed_problem'
                    and unit.obligation.subject_sha256 == origin
                    and unit.decision != 'accept' for unit in decision.obligation_receipt.units)), None)
        item = {'obligation_id': obligation.obligation_id, 'problem_subject_sha256': origin,
                'source_decision_sha256': None if original is None else review_obligations.sha(original.model_dump(mode='json')),
                'source': None if original is None or original.material_source is None
                          else original.material_source.model_dump(mode='json'),
                'available': False, 'row': None, 'context_rows': [], 'unavailable_reason': None}
        try:
            if original is None or original.material_source is None:
                raise ValueError('原否定未绑定可核对的历史材料')
            source = original.material_source
            chars = store.read_own_by_sha256(CharacterRegistry, STAGE2_CHARACTERS, source.characters_sha256)
            global_ = store.read_own_by_sha256(GlobalGrounding, STAGE2_GLOBAL_GROUNDING, source.global_sha256)
            old_review = store.read_own_by_sha256(GlobalReview, REL, source.review_sha256)
            old_main = store.read_own_by_sha256(MainText, STAGE1_MAIN_TEXT, source.main_text_sha256)
            rows = _material_rows(global_.model_dump(mode='json', by_alias=True), old_review, old_main,
                                  chars.model_dump(mode='json', by_alias=True), 'blocking')
            restored = next(row for row in rows if review_obligations.global_key(row) == obligation.obligation_id)
            old_catalog = review_obligations.global_catalog({'items': rows,
                'main_text_subject_sha256': subject_sha256(old_main.model_dump(mode='json', by_alias=True))})
            proven = next(row for row in old_catalog if row.obligation_id == obligation.obligation_id)
            if proven.subject_sha256 != origin:
                raise ValueError('原归档材料与被删问题义务 SHA 不一致')
            context = review_obligations._global_material_context(rows,
                {row.obligation_id: row for row in old_catalog}, {obligation.obligation_id})
            item.update(available=True, row=restored, context_rows=[
                row for row in rows if review_obligations.global_key(row) in context
                and row['reading_scope'] != 'whole_book'])
        except (NovelError, ValueError, StopIteration) as exc:
            item['unavailable_reason'] = (f'原删除问题材料不可验证：{exc}。恢复所列完整 SHA 的原归档后免费刷新审阅；'
                                           '在原主张可核对前保持不确定，不能接受删除。')
        result.append(item)
    return result


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
        grounding = grounding_io.read_own(store, STAGE2_GLOBAL_GROUNDING)[0]
        grounding_payload = grounding.model_dump(by_alias=True)
        current = store.read_own(GlobalReview, REL)[0]
        require_current(grounding_payload, current, characters=_characters_payload(store))
        main_text = store.read_own(MainText, STAGE1_MAIN_TEXT)[0]
        from novel.orchestrator.approvals import (
            prepare_stage2_patch_receipt, stage2_patch_receipt,
        )
        request_sha = subject_sha256(decisions.model_dump(mode='json'))
        receipt = stage2_patch_receipt(ctx.novel_dir.parent, ctx.sid, request_sha)
        if (receipt is not None
                and receipt.result_subject_sha256 == subject_sha256(current.model_dump(mode='json'))
                and receipt.global_sha256 == subject_sha256(grounding_payload)):
            updated = current
        else:
            from datetime import datetime, timezone
            # 回执已经写入但正式 review 尚未提交时，重放必须还原第一次的
            # 决定时间；重新取当前时间会让同一请求产生不同结果身份。
            decision_timestamp = (
                receipt.decision_timestamp if receipt is not None
                else datetime.now(timezone.utc).isoformat())
            blocking_workpack = (
                review_workpack(ctx, scope='blocking')
                if decisions.blocking_scope is not None else None)
            updated = apply_decision_set(
                current, decisions,
                now=decision_timestamp,
                expected_chapter_ordinals=tuple(
                    int(section.chapter_ordinal) for section in main_text.root),
                expected_workpack_sha256=(
                    str(blocking_workpack['workpack_sha256'])
                    if blocking_workpack is not None else None))
            require_current(grounding_payload, updated, characters=_characters_payload(store))
            prepare_stage2_patch_receipt(
                ctx.novel_dir.parent, ctx.sid, request_sha256=request_sha,
                before_token=review_token(ctx),
                result_subject_sha256=subject_sha256(updated.model_dump(mode='json')),
                global_sha256=subject_sha256(grounding_payload),
                decision_timestamp=decision_timestamp)
            store.write_round(REL, updated)
        if generation_safety(grounding_payload, updated, characters=_characters_payload(store)).safe:
            from contracts.schema.api import Stage2NodeReviewRequest
            from novel.orchestrator.approvals import approve_stage2_review
            from novel.errors import ArtifactSubmittedReviewPending, NovelError
            if pending_removed_problems(ctx):
                # 本回答只决定当前 formal items；已删除的问题必须由统一 C 以
                # 含原主张材料的当前 token 明确裁决，重放旧 PASS 不授予该权限。
                return updated
            try:
                approve_stage2_review(ctx.novel_dir.parent, ctx.sid, Stage2NodeReviewRequest(
                    node='stage2.global', review_token=review_token(ctx),
                    decision='accept', actor=decisions.actor,
                    reason=(decisions.blocking_scope.reason if decisions.blocking_scope
                            else '逐项 typed 决定已闭合当前全部 blocking 主体')))
            except (NovelError, OSError, ValueError) as exc:
                raise ArtifactSubmittedReviewPending(str(exc), hint=(
                    '逐项决定已落盘；重放同一 --review-decisions 文件零模型补提交。')) from exc
        return updated


def submitted_decisions_is_current(
        base: str, sid: str, payload: dict[str, object], *,
        require_action_complete: bool = False) -> bool:
    """确认 exact 决定已落盘；需要退休回答时也核对合法提交后继。

    FAIL/UNVER 是合法的已提交语义结论，不能因节点未接受而无限重放。
    全部安全的决定仍须补齐节点接受，不能丢弃其尚未完成的免费后继。
    """
    from contracts.schema import decode_artifact
    from novel.orchestrator.approvals import stage2_patch_receipt
    from novel.orchestrator.state import readonly_ctx
    from novel import prov
    request = GlobalReviewDecisionSet.model_validate(
        decode_artifact(DEFAULT_DECISIONS, payload).payload)
    receipt = stage2_patch_receipt(base, sid, subject_sha256(request.model_dump(mode='json')))
    if receipt is None:
        return False
    ctx = readonly_ctx(paths.resolve_novel_dir(base, sid), 'stage2.global', prov.code_version())
    store = ArtifactStore(ctx)
    grounding = grounding_io.read_own(store, STAGE2_GLOBAL_GROUNDING, require_current=True)[0]
    current = store.read_own(GlobalReview, REL, require_current=True)[0]
    payload = grounding.model_dump(by_alias=True)
    if (receipt.global_sha256 != subject_sha256(payload)
            or receipt.result_subject_sha256 != subject_sha256(current.model_dump(mode='json'))):
        return False
    if require_action_complete and generation_safety(payload, current, characters=_characters_payload(store)).safe:
        if pending_removed_problems(ctx):
            return True  # 当前事实决定已落；下一步是同一 C 的删除义务，不重放本回答。
        from novel.errors import ApprovalRequired
        from novel.orchestrator.approvals import require_stage2_node_accepted
        try:
            require_stage2_node_accepted(base, sid, 'stage2.global')
        except ApprovalRequired:
            return False
    return True


def pending_removed_problems(ctx: RunContext) -> bool:
    pack = review_workpack(ctx)
    coverage = pack['review_coverage']
    pending = set(coverage['pending_obligation_ids'])
    return any(row['kind'] == 'removed_problem' and row['obligation_id'] in pending
               for row in coverage['scope']['obligations'])


def is_approved(ctx: RunContext, review: GlobalReview) -> bool:
    """Bind approval to the complete, current Stage2 subject (blocking surface)."""
    grounding = grounding_io.read_own(ArtifactStore(ctx))[0]
    return review_status(
        grounding.model_dump(by_alias=True), review, characters=_characters_payload(ArtifactStore(ctx)))[2]


def is_generation_safe(ctx: RunContext, review: GlobalReview) -> bool:
    """是否可把当前 review 经确定性隔离后交给写作消费方。"""
    grounding = grounding_io.read_own(ArtifactStore(ctx))[0]
    return generation_safety(
        grounding.model_dump(by_alias=True), review, characters=_characters_payload(ArtifactStore(ctx))).safe


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
    grounding = grounding_io.read_own(store, STAGE2_GLOBAL_GROUNDING)[0]
    review = store.read_own(GlobalReview, REL)[0]
    _subject, current, approved = review_status(
        grounding.model_dump(by_alias=True), review, characters=_characters_payload(ArtifactStore(ctx)))
    return current, approved, _counts(review)


def generation_status(ctx: RunContext) -> tuple[GenerationSafety, dict[str, int]]:
    """返回写作安全结论与完整四态计数；不把隔离过的 UNVER 改名为 PASS。"""
    store = ArtifactStore(ctx)
    grounding = grounding_io.read_own(store, STAGE2_GLOBAL_GROUNDING)[0]
    review = store.read_own(GlobalReview, REL)[0]
    return (
        generation_safety(grounding.model_dump(by_alias=True), review, characters=_characters_payload(store)),
        _counts(review),
    )


def generation_grounding(store: ArtifactStore) -> GlobalGrounding:
    """读取下游唯一可消费的 Stage2 视图，并如实记录 review 输入边。

    正式 ``global_grounding.json`` 仍保留完整审计事实；这里不迁移、不覆写它。
    对已逐项处置的 UNVER，只从本次下游内存视图删除所属 secret/relation/world-rule
    完整组，再以 ``GlobalGrounding`` schema 复验。这样终审仍看得到原事实与决定，
    写作模型却不可能收到被隔离的未知事实。
    """
    grounding = grounding_io.read(store)
    review = store.read(GlobalReview, REL)
    safety = generation_safety(
        grounding.model_dump(by_alias=True), review, characters=_characters_payload(store))
    if not safety.safe:
        raise ApprovalRequired(
            'Stage2 写作视图未收口：仍有 pending/FAIL、不可隔离 UNVER，'
            f'或 review 已过期；current={safety.current}，'
            f'blocking_items={list(safety.blocking_item_ids)}')
    if not safety.quarantined_groups:
        return grounding

    payload = grounding.model_dump(mode='json', by_alias=True)
    secret_ids = {group.split(':', 1)[1] for group in safety.quarantined_groups if group.startswith('secret:')}
    relation_ids = {group.split(':', 1)[1] for group in safety.quarantined_groups if group.startswith('relation:')}
    rule_ids = {group.split(':', 1)[1] for group in safety.quarantined_groups if group.startswith('world_rule:')}
    payload['relations'] = [row for row in payload['relations'] if row['relation_id'] not in relation_ids]
    payload['world_rules'] = [row for row in payload['world_rules'] if row['rule_id'] not in rule_ids]
    payload['arc_secrets'] = [row for row in payload['arc_secrets']
                              if row['secret_id'] not in secret_ids and not (
                                  row.get('fact_ref') is not None and (
                                      (row['fact_ref']['kind'] == 'relation' and row['fact_ref']['relation_id'] in relation_ids)
                                      or (row['fact_ref']['kind'] == 'world_rule_version' and row['fact_ref']['rule_id'] in rule_ids)))]
    try:
        return GlobalGrounding.model_validate(payload)
    except ValidationError as exc:  # 校验细节不应变成一条模糊的继续路径
        raise GateBlocked(
            'Stage2 UNVER 隔离后的写作视图不再符合 GlobalGrounding；'
            '该组不能安全降级，必须用 GlobalEdit 修复') from exc


def require_global_valid(ctx: RunContext) -> None:
    """Global 自身当前、机械有效且逐项决定闭合；不反向要求 Blueprint 存在。"""
    store = ArtifactStore(ctx)
    characters_invalid = paths.invalid_of(STAGE2_CHARACTERS)
    if store.exists(characters_invalid):
        raise GateBlocked(
            '人物登记存在更新的问题稿；不能用旧人物与 Global 接受覆盖当前身份问题',
            invalid_path=str(store.path(characters_invalid)),
            hint='stage2.characters --revalidate（0 成本），或提交人物 typed 编辑')
    _characters_payload(store, require_current=True)
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
                  '人物身份、公开角色与称谓归属必须明确接受，不许自动 PASS'))

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

    from novel.stages.stage2 import extract
    grounding = grounding_io.read_own(store, STAGE2_GLOBAL_GROUNDING)[0]
    main = store.read_own(MainText, STAGE1_MAIN_TEXT)[0]
    hard = extract._hard_hits(extract._judge_candidate(
        grounding.model_dump(by_alias=True, mode='json'), None,
        main.model_dump(mode='json'), characters=_characters_payload(store)), ignore={'upstream_missing'})
    if hard:
        raise BlockingFindingsUnresolved(
            'Global 当前正式稿仍违反生产 HARD；不能授予或消费语义接受',
            rules=tuple(hard), hint='stage2.global --check-only（0 成本）')

def require_global_accepted(ctx: RunContext) -> None:
    """Blueprint 的唯一语义前置；不反向要求尚未生产的 Blueprint。"""
    require_global_valid(ctx)
    from novel.orchestrator.approvals import require_stage2_node_accepted
    require_stage2_node_accepted(
        ctx.novel_dir.parent, ctx.sid, 'stage2.global')


def require_approved(ctx: RunContext, *, require_acceptance: bool = True) -> None:
    """联合生产闸；Stage4 起还必须持有两位 Commander 的当前接受。"""
    require_global_valid(ctx)
    store = ArtifactStore(ctx)
    if require_acceptance:
        require_global_accepted(ctx)
    if not store.exists(STAGE2_BLUEPRINT_CHARACTER_PROJECTION):
        raise UpstreamMissing(
            f'{STAGE2_BLUEPRINT_CHARACTER_PROJECTION} 不在盘上；'
            'Stage4 不能让 Blueprint 拿一份未绑当前人物的旧称名表过门',
            hint='stage2.characters --revalidate --actor <id> --reason <why>（0 LLM）重建称名投影')
    projection_freshness = store.freshness(
        STAGE2_BLUEPRINT_CHARACTER_PROJECTION)
    if projection_freshness.state == 'stale':
        raise UpstreamStale(
            f'{STAGE2_BLUEPRINT_CHARACTER_PROJECTION} 已不是当前最新 '
            'characters + MainText 的确定性投影；拒绝让旧 Blueprint 过门',
            hint='stage2.characters --revalidate --actor <id> --reason <why>（0 LLM）重建称名投影')
    if projection_freshness.state != 'fresh':
        raise UpstreamUnproven(
            f'{STAGE2_BLUEPRINT_CHARACTER_PROJECTION} 证明不了自己来自当前'
            f'最新 characters + MainText（{projection_freshness.state}/'
            f'{projection_freshness.reason}）',
            hint='stage2.characters --revalidate --actor <id> --reason <why>（0 LLM）重建称名投影')

    blueprint_invalid = paths.invalid_of(STAGE2_CHAPTER_BLUEPRINTS)
    if (not store.exists(STAGE2_CHAPTER_BLUEPRINTS)
            and not store.exists(blueprint_invalid)):
        raise UpstreamMissing(
            f'{STAGE2_CHAPTER_BLUEPRINTS} 不在盘上；Stage2 尚未完成联合检查',
            hint='Global 已接受后运行 stage2.blueprint（付费）')

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
    if require_acceptance:
        from novel.orchestrator.approvals import require_stage2_node_accepted
        require_stage2_node_accepted(
            ctx.novel_dir.parent, ctx.sid, 'stage2.blueprint')
        from novel.stages.stage2.knowledge_links import require_current
        require_current(ctx)
