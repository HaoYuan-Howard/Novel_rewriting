#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from contracts.artifacts import STAGE1_MAIN_TEXT, STAGE1_HOOK_COPY
from contracts.invariants import (
    GLOBAL_REVIEW_EFFECTS, GLOBAL_REVIEW_KINDS, review_effect_of,
    review_generation_impact_of,
)
from contracts.schema.evaluation import (
    GlobalReview, GlobalReviewDecisionSet, GlobalReviewGenerationDisposition,
    GlobalReviewItem,
)
from novel.core.evidence import iter_global_evidence_slots, iter_hook_evidence_slots, without_global_evidence
from novel.errors import HumanEditProtected


UNBOUND_WORKPACK_SHA256 = '0' * 64

# 只有删除整组后仍能由 ``GlobalGrounding`` schema 关闭式复验、且不会删掉
# 人物/主线骨架的三类事实可以隔离。集合完整性、人物、setting 与源文案范围都不在
# 这里：它们必须经 typed GlobalEdit 修正，不能借 UNVER 处置静默越过。
QUARANTINABLE_REVIEW_GROUP_PREFIXES = ('secret:', 'relation:', 'world_rule:')


@dataclass(frozen=True)
class GenerationSafety:
    """Stage2 人审对写作消费面的独立结论；不覆盖真值审阅状态。"""

    current: bool
    safe: bool
    all_pass: bool
    quarantined_groups: tuple[str, ...]
    blocking_item_ids: tuple[str, ...]


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')


def subject_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def blocking_scope_sha256(review: GlobalReview) -> str:
    """绑定当前完整 blocking ``(item_id, subject_sha256)`` 面。

    不能只 hash ``item_id``：同一路径的事实改字后 id 虽通常也会变，但 scope 的契约是
    明说逐项主体身份，不依赖 id 生成文法的偶然性质。``source_sha256`` 同时入账，避免
    一份手写 review 搬用另一份 grounding 的同形 item 集合。
    """
    rows = [
        (item.item_id, item.subject_sha256)
        for item in review.items
        if review_effect_of(item.kind) == 'blocking'
    ]
    return subject_sha256({
        'source_sha256': review.source_sha256,
        'blocking_items': rows,
    })


def _item(
    kind: str, path: str, value: Any, *, dependency: Any | None = None,
) -> GlobalReviewItem:
    """铸一条 review identity；可选 dependency 表达会改变本判断前提的邻接事实。

    ``subject_path`` 仍只指向真正被裁决的字段，workpack 也仍展示该字段；dependency
    只参加 hash。这样一条 secret 的 reveal 改动会精确重开同 secret 卡片，却不会
    把全书每个已经成功的主体重新发送给 Commander。
    """
    digest = subject_sha256(
        value if dependency is None else {
            'subject': value, 'dependency': dependency,
        })
    return GlobalReviewItem(
        item_id=f'{kind}:{path}:{digest[:12]}', kind=kind, subject_path=path,
        subject_sha256=digest, state='pending', evidence=[])


def review_item_kind(item_id: str) -> str:
    """从 `item_id` 取回 kind。**`item_id` 的文法只有 `_item` 一个产地，本函数
    是它唯一的反向读法**——inbox 这类只拿到 `rule=item_id` 的消费方需要它，
    而在那边现拆一次 `split(':')` 就是文法的第二个落点。"""
    kind = str(item_id).split(':', 1)[0]
    if kind not in GLOBAL_REVIEW_KINDS:
        raise ValueError(f'{item_id!r} 不是一个 stage2 复核项 id')
    return kind


def _fact_item(
    kind: str,
    path: str,
    claim: Any,
    evidence: Any,
    *,
    dependency: Any | None = None,
) -> GlobalReviewItem:
    """把一条可复核自由事实同时绑定到 claim 与原文 evidence。"""
    return _item(
        kind, path, {'claim': claim, 'evidence': evidence},
        dependency=dependency)


def _whole_book_item(
    kind: str,
    path: str,
    value: Any,
    main_text_input: Any,
) -> GlobalReviewItem:
    """把必须通读全书的决定同时绑定到 Global 主体与正文输入身份。

    普通事实有自己的 claim/evidence hash，正文别处改字不该把它们全部重开；集合
    完整性与开篇范围却是在回答“这本完整正文有没有漏/范围是否足够”。若只 hash
    Global 结果，同章数的新正文重新抽出同形 Global 时，旧的全书 PASS 会被继承。
    ``main_text_input`` 取 Global provenance 里 producer 真正读到的 MainText 边，
    不另读正文、也不另造一套 freshness 算法。
    """
    return _item(kind, path, {
        'subject': value,
        'main_text_input': main_text_input,
    })


def enumerate_required(
    global_grounding: dict[str, Any], *, main_text_input: Any = None,
    source_hook_input: Any = None,
) -> list[GlobalReviewItem]:
    """从 typed Stage2 表面派生全部人工复核主体。

    ⚠⚠ **没被枚举出来的字段等于没人审。** 这份清单是 `R-2.16` 那道硬闸的评测面，
    下游全部付费执行与成书组装都压在它上面；`R-2.16` 说的「与当前 grounding 完整
    一致」指的是**这里枚举的那些主体**一致，不是「整份 grounding 都过了人眼」。
    此前只覆盖九类（枚举、规则、秘密三格），于是四处已知风险面全程无人裁决：
    `role` 的结局旁路、`setting.世界观` 的泄底、relations 这条第二剧透通道、
    以及 `setup_chapters`——它是 `_relation_gates` 隐瞒窗口与 secret 闸门第②臂
    **两道下发闸门共同的前件**。

    【粒度的分界线是「能不能被单独修正」】事实逐条、解读分组。`role` / `fate` /
    setting 的两项事实 / 每条 relation / 每条 secret 的埋点章都能单独改，各占一项；
    源文案事实逐条裁决；完整性与开篇范围需要同时核对源文案和整书，不能从局部命中
    推断通过。未确定的事实或范围必须先经 typed 修订提供可核验的主体。

    ⚠ 取值面在 `contracts/invariants.py::GLOBAL_REVIEW_KINDS`（`GlobalReviewItem.kind`
    从那里派生），交付效果在同文件的 `review_effect_of`；新增一类必须同批在两处登记。
    ⚠ 主体路径的形状同时是 `review_group_key` 的输入：新增一类主体路径要同批登记
    卡片收敛键，否则那一类没人分组，而没有兜底键会让它当场炸出来。
    """
    rows: list[GlobalReviewItem] = []
    projected = without_global_evidence(global_grounding)
    assert isinstance(projected, dict)
    evidence_by_path = {
        path: owner.get(field)
        for path, owner, field in (
            *iter_global_evidence_slots(global_grounding), *iter_hook_evidence_slots(global_grounding))
    }

    def evidence_for(path: str, *, grouped: bool = False) -> Any:
        """只按 evidence 单一路径表关联主体，不在复核层重抄字段名。"""
        if not grouped and path in evidence_by_path:
            return evidence_by_path[path]
        prefix = f'{path}.'
        matches = [
            evidence for claim_path, evidence in evidence_by_path.items()
            if claim_path.startswith(prefix)
        ]
        if not matches:
            return [] if grouped else None
        if grouped or len(matches) > 1:
            return matches
        return matches[0]

    entities = global_grounding.get('entities') or {}
    projected_entities = projected.get('entities') or {}
    # 逐条复核只能判「已经抽出来的这一条对不对」，不能发现整个人物或关系根本没被
    # 抽出来。两项集合裁决故意绑定剥掉 evidence 的完整集合：改坐标不重开，增删或
    # 改写事实则必须重新通读来源确认没有漏项。
    projected_characters = projected_entities.get('characters') or {}
    projected_relations = projected_entities.get('relations') or []
    character_members = sorted(str(key) for key in projected_characters)
    relation_members = sorted(
        str(row.get('relation_id')) for row in projected_relations
        if isinstance(row, dict))
    rows.append(_whole_book_item(
        'character_set_complete', 'entities.characters', character_members,
        main_text_input))
    rows.append(_whole_book_item(
        'relation_set_complete', 'entities.relations', relation_members,
        main_text_input))
    for cid, character in sorted((entities.get('characters') or {}).items()):
        rows.append(_item(
            'name_distinctiveness', f'entities.characters.{cid}.name_distinctiveness',
            character.get('name_distinctiveness')))
        # `role` 是一条已知的、未修的结局旁路：词表只抓转抄式结局词，实测某本上
        # 命中 0/17 而人读真违规 6/17（「后成为…的下属」差一个字避开了「后被」）
        # ⇒ 「判据没报违规」在这一维上不等于「没问题」，只能靠人裁。
        role_path = f'entities.characters.{cid}.role'
        rows.append(_fact_item(
            'role', role_path, character.get('role'), evidence_for(role_path)))
        # `fate` 与 `role` 拆成两个字段的全部意义是**它不下发给写手**；写错等于白拆。
        # ⚠ 它可以是 `null`（据实留空合法），空值同样需要一句「确实没有」的人工决定。
        fate_path = f'entities.characters.{cid}.fate'
        rows.append(_fact_item(
            'fate', fate_path, character.get('fate'), evidence_for(fate_path)))
        for index, address in enumerate(character.get('address') or []):
            rows.append(_item(
                'address_context', f'entities.characters.{cid}.address.{index}.context',
                address.get('context')))

    # relations 是 secrets 之外的**第二条剧透通道**：`valid_from_chapter` 记的是
    # 【世内生效时点】而不是【读者知情时点】，秘密类关系上两者系统性错位。
    # 下发侧那道补救闸门靠「`fact` 是否同时含两端人名」的启发式命中，
    # 判不准的那部分只能在这里由人兜住。
    projected_secret_by_id = {
        str(row.get('secret_id')): row
        for row in ((projected.get('canon') or {}).get('secrets') or [])
        if isinstance(row, dict)
    }
    for index, relation in enumerate(entities.get('relations') or []):
        rid = relation.get('relation_id') or str(index)
        path = f'entities.relations.{rid}'
        claim = projected_relations[index] if index < len(projected_relations) else relation
        visibility = claim.get('visibility') if isinstance(claim, dict) else None
        linked_secret = (
            projected_secret_by_id.get(str(visibility.get('secret_id')))
            if isinstance(visibility, dict)
            and visibility.get('kind') == 'via_secret' else None)
        rows.append(_fact_item(
            'relation', path, claim, evidence_for(path),
            dependency=linked_secret))

    canon = global_grounding.get('canon') or {}
    projected_canon = projected.get('canon') or {}
    # ⚠ 键是**中文别名**（`by_alias=True` 落盘），全链仅有的三处固定中文 JSON key 之二。
    # `世界观` 是已知泄底面：逐章字面闸门在唯一有 secrets 的那本上命中 **0/8**，
    # 其中包括一条已知的真泄漏 ⇒ 机械闸门在这一维上没有判别力。
    setting = canon.get('setting') or {}
    # ⚠ 这里只能显式列 schema 的两条事实。遍历 setting 会把新增的 evidence 键
    # 当成第三、第四条 claim，制造没有人类语义的复核主体。
    for key in ('时代背景', '世界观'):
        path = f'canon.setting.{key}'
        rows.append(_fact_item(
            'setting_fact', path, setting.get(key), evidence_for(path)))

    world_rules = canon.get('world_rules') or []
    secrets = canon.get('secrets') or []
    projected_world_rules = projected_canon.get('world_rules') or []
    projected_secrets = projected_canon.get('secrets') or []
    # 逐条核已抽出的内容，不能证明集合没有整项漏抽；即使列表为空，也必须由人明确判断
    # 原文中确实没有被漏掉的规则或秘密。
    rows.append(_whole_book_item(
        'world_rule_set_complete', 'canon.world_rules', sorted(
            str(row.get('rule_id')) for row in projected_world_rules
            if isinstance(row, dict)), main_text_input))
    rows.append(_whole_book_item(
        'secret_set_complete', 'canon.secrets', sorted(
            str(row.get('secret_id')) for row in projected_secrets
            if isinstance(row, dict)), main_text_input))
    for ri, rule in enumerate(world_rules):
        rid = rule.get('rule_id') or str(ri)
        rule_dependency = (
            projected_world_rules[ri]
            if ri < len(projected_world_rules) else rule)
        rows.append(_item(
            'world_rule_kind', f'canon.world_rules.{rid}.kind',
            rule.get('kind'), dependency=rule_dependency))
        for vi, version in enumerate(rule.get('versions') or []):
            path = f'canon.world_rules.{rid}.versions.{vi}'
            projected_version = projected_world_rules[ri]['versions'][vi]
            rows.append(_fact_item(
                'world_rule_version', path, projected_version, evidence_for(path),
                dependency=rule_dependency))
    for si, secret in enumerate(secrets):
        sid = secret.get('secret_id') or str(si)
        secret_dependency = (
            projected_secrets[si] if si < len(projected_secrets) else secret)
        fact_path = f'canon.secrets.{sid}.fact'
        rows.append(_fact_item(
            'secret', fact_path, secret.get('fact'), evidence_for(fact_path),
            dependency=secret_dependency))
        for ei, event in enumerate(secret.get('knowledge_events') or []):
            path = f'canon.secrets.{sid}.knowledge_events.{ei}'
            projected_event = projected_secrets[si]['knowledge_events'][ei]
            rows.append(_fact_item(
                'knowledge_event', path, projected_event, evidence_for(path),
                dependency=secret_dependency))
        # 埋点是 `_relation_gates` 的隐瞒窗口与 secret 闸门第②臂**两道闸门的前件**。
        # v2 的判别联合逼模型在 cold / foreshadowed 之间显式选一边，但**归一裁空
        # 降级成 cold** 的那一条与真·冷揭示仍然同形（只有
        # `_provenance.setup_chapters_pruned` 分得开）⇒ 判据看不出来，人必须看。
        rows.append(_item(
            'secret_setup', f'canon.secrets.{sid}.setup', secret.get('setup'),
            dependency=secret_dependency))
        rows.append(_item(
            'reveal', f'canon.secrets.{sid}.first_reveal_chapter',
            secret.get('first_reveal_chapter'), dependency=secret_dependency))

    blurb = global_grounding.get('source_blurb') or {}
    projected_blurb = projected.get('source_blurb') or {}
    source_dependency = {'main_text': main_text_input, 'source_hook': source_hook_input}
    rows.append(_whole_book_item(
        'source_blurb_complete', 'source_blurb.facts',
        projected_blurb.get('facts'), source_dependency))
    for fact in blurb.get('facts') or []:
        path = f"source_blurb.facts.{fact['claim_id']}"
        row = _fact_item('source_blurb_fact', path,
                         {key: value for key, value in fact.items()
                          if key not in {'evidence', 'hook_evidence'}},
                         evidence_for(path), dependency=source_dependency)
        rows.append(row.model_copy(update={'resolution_required': fact.get('kind') == 'unresolved'}))
    opening = blurb.get('opening') or {}
    row = _fact_item('opening_scope', 'source_blurb.opening',
                     projected_blurb.get('opening'), evidence_for('source_blurb.opening'),
                     dependency={'source': source_dependency, 'facts': blurb.get('facts')})
    rows.append(row.model_copy(update={'resolution_required': opening.get('kind') != 'proposed'}))
    return rows


def build_review(
    global_grounding: dict[str, Any],
    previous: GlobalReview | None = None,
) -> GlobalReview:
    """Carry decisions only when their exact subject hash survives an edit."""
    provenance = global_grounding.get('_provenance') or {}
    inputs = provenance.get('inputs') if isinstance(provenance, dict) else None
    main_text_input = (
        inputs.get(STAGE1_MAIN_TEXT) if isinstance(inputs, dict) else None)
    source_hook_input = inputs.get(STAGE1_HOOK_COPY) if isinstance(inputs, dict) else None
    source = {
        key: value for key, value in global_grounding.items()
        if key not in {'_provenance', '_schema_version'}
    }
    prior = {
        (item.item_id, item.kind, item.subject_path, item.subject_sha256): item
        for item in (previous.items if previous else [])
    }
    items = []
    for fresh in enumerate_required(source, main_text_input=main_text_input,
                                    source_hook_input=source_hook_input):
        inherited = prior.get((
            fresh.item_id, fresh.kind, fresh.subject_path, fresh.subject_sha256))
        items.append(inherited if inherited is not None else fresh)
    return GlobalReview(source_sha256=subject_sha256(source), items=items)


#: 卡片收敛键的路径文法。**闭集**：产地是 `enumerate_required` 拼出的
#: `subject_path`，两处必须同进同出，因此未知形状当场炸而不是回落成一个兜底键——
#: 兜底键会让「这一类没人分组」看起来和「分好了」一模一样。
_GROUP_PREFIXES: tuple[tuple[str, str], ...] = (
    ('entities.characters.', 'character'),
    ('entities.relations.', 'relation'),
    ('canon.world_rules.', 'world_rule'),
    ('canon.secrets.', 'secret'),
)
_GROUP_COLLECTIONS: dict[str, str] = {
    'entities.characters': 'set:characters',
    'entities.relations': 'set:relations',
    'canon.world_rules': 'set:world_rules',
    'canon.secrets': 'set:secrets',
}


def review_group_key(subject_path: str) -> str:
    """一条复核项归哪张人审卡。**同一个主体的全部项进同一张卡。**

    人不是逐条读 145 行的，他是按「这个人物 / 这条关系 / 这条秘密的时间线」读的。
    收敛键因此绑**主体**而不是 kind：`role` 与 `fate` 是同一个人的两格，
    `secret` / `setup` / `reveal` / `knowledge_event` 是同一条秘密时间线的四格。
    ⚠ 它只决定**怎么摆**，不决定**能不能提交**：批量提交仍逐项展开为 hash-bound
    decision，允许单项覆盖，且永不自动 PASS。
    """
    path = str(subject_path)
    if path in _GROUP_COLLECTIONS:
        return _GROUP_COLLECTIONS[path]
    for prefix, group in _GROUP_PREFIXES:
        if path.startswith(prefix):
            return f'{group}:{path[len(prefix):].split(".", 1)[0]}'
    if path == 'canon.setting' or path.startswith('canon.setting.'):
        return 'setting'
    if path == 'source_blurb' or path.startswith('source_blurb.'):
        return 'source_blurb'
    raise ValueError(
        f'复核项路径 {path!r} 没有登记的卡片收敛键。'
        f'`enumerate_required` 新增一类主体路径时必须同批在这里登记，'
        f'不给兜底键——兜底会让「没人分组」看起来和「分好了」一样')


def review_effect_counts(review: GlobalReview) -> dict[str, dict[str, int]]:
    """按交付效果分开的四态计数。**两条轴不合成一个数**（判据 27）。

    ``{'blocking': {四态}, 'disclosure': {四态}}``，四个状态键恒全出（0 也出）：
    「没有这个键」与「值是 0」不许混。
    """
    out = {
        effect: {state: 0 for state in ('pending', 'PASS', 'FAIL', 'UNVER')}
        for effect in GLOBAL_REVIEW_EFFECTS
    }
    for item in review.items:
        out[review_effect_of(item.kind)][item.state] += 1
    return out


def review_status(
    global_grounding: dict[str, Any],
    review: GlobalReview,
) -> tuple[str, bool, bool]:
    """Return ``(current subject hash, exact-current, blocking-approved)``.

    The persisted ``source_sha256`` is not sufficient by itself: an older or
    hand-written review could omit newly required subjects while copying that
    hash.  Gate readiness therefore recomputes both the complete Stage2 subject
    and the exact required-item surface from the current ``GlobalGrounding``.

    ⚠ 第三格是 **blocking 面**的逐项 PASS，不是整份 145 项。分级的理由与那三类
    `disclosure` 的名单在 `contracts/invariants.py::review_effect_of`；
    `disclosure` 的 pending/FAIL/UNVER **不自动 PASS、不阻断 Stage4，但必须继续
    出现在 inbox 与终审包**——「不阻断」不等于「没人看」。
    ⚠ **评测面 current 仍要求 item/path/hash 集合完全一致**，一格没松：
    分级只改「谁能拦」，不改「谁必须在场」。
    """
    expected = build_review(global_grounding)
    def identity(item: GlobalReviewItem) -> tuple[str, str, str, str, bool]:
        return (item.item_id, item.kind, item.subject_path, item.subject_sha256,
                item.resolution_required)
    current = (
        review.source_sha256 == expected.source_sha256
        and [identity(item) for item in review.items]
        == [identity(item) for item in expected.items]
    )
    blocking = [item for item in review.items
                if review_effect_of(item.kind) == 'blocking']
    approved = current and bool(blocking) and all(
        item.state == 'PASS' for item in blocking)
    return expected.source_sha256, current, approved


def quarantined_group(item: GlobalReviewItem) -> str | None:
    """返回一条当前 UNVER 决定可被确定性隔离的组；否则 ``None``。"""
    disposition = item.generation_disposition
    if item.state != 'UNVER' or disposition is None:
        return None
    if disposition.action != 'quarantine_group':  # pragma: no cover —— Literal
        return None
    if review_generation_impact_of(item.kind) != 'quarantine_or_fix':
        return None
    group = review_group_key(item.subject_path)
    if not group.startswith(QUARANTINABLE_REVIEW_GROUP_PREFIXES):
        return None
    return group


def generation_safety(
    global_grounding: dict[str, Any], review: GlobalReview,
) -> GenerationSafety:
    """判断能否继续写作，同时保留 ``all_pass`` 这一条正交真值事实。

    PASS 是可信事实；UNVER 仍是不确定事实。后者只有逐项携带处置、且所属完整
    group 可从下游视图确定性删除时才不阻断“产出一本书”。任何 pending/FAIL、
    不可隔离组、缺处置或 stale surface 都保持关闭。
    """
    _expected, current, approved = review_status(global_grounding, review)
    blocking = [
        item for item in review.items
        if review_effect_of(item.kind) == 'blocking'
    ]
    rejected: list[str] = []
    groups: set[str] = set()
    if current and blocking:
        for item in blocking:
            if item.state == 'PASS':
                continue
            group = quarantined_group(item)
            if item.state == 'UNVER' and group is not None:
                groups.add(group)
                continue
            rejected.append(item.item_id)
    else:
        rejected.extend(item.item_id for item in blocking)
    return GenerationSafety(
        current=current,
        safe=current and bool(blocking) and not rejected,
        all_pass=approved,
        quarantined_groups=tuple(sorted(groups)),
        blocking_item_ids=tuple(rejected),
    )


def require_current(global_grounding: dict[str, Any], review: GlobalReview) -> None:
    """Reject decisions against a stale or incomplete review surface."""
    _subject, current, _approved = review_status(global_grounding, review)
    if not current:
        raise HumanEditProtected(
            'stage2 global review is stale or omits required subjects; '
            'rebuild it from the current GlobalGrounding before deciding')


def decide(
    review: GlobalReview,
    *,
    item_id: str,
    expected_subject_sha256: str,
    state: str,
    actor: str,
    reason: str,
    evidence: list[dict[str, Any]] | None = None,
    generation_disposition: GlobalReviewGenerationDisposition | None = None,
    now: str | None = None,
) -> GlobalReview:
    actor = actor.strip()
    reason = reason.strip()
    if not actor or not reason:
        raise ValueError('global-review decision requires non-empty actor and reason')
    if state not in {'PASS', 'FAIL', 'UNVER'}:
        raise ValueError('decision state must be PASS, FAIL, or UNVER')
    found = False
    updated = []
    for item in review.items:
        if item.item_id != item_id:
            updated.append(item)
            continue
        found = True
        if item.resolution_required and state == 'PASS':
            raise HumanEditProtected('该事实或开篇范围仍为 unresolved，必须先提交 typed 修订')
        if item.subject_sha256 != expected_subject_sha256:
            raise HumanEditProtected(
                'global-review subject changed; stale decision rejected')
        updated.append(GlobalReviewItem.model_validate({
            **item.model_dump(),
            'state': state,
            'actor': actor,
            'reason': reason,
            'decided_at': now or datetime.now(timezone.utc).isoformat(),
            'evidence': evidence or [],
            'generation_disposition': (
                generation_disposition.model_dump(mode='json')
                if generation_disposition is not None else None),
        }))
    if not found:
        raise HumanEditProtected(
            f'global-review item is absent from the current required surface: {item_id}')
    return review.model_copy(update={'items': updated})


def apply_decision_set(
    review: GlobalReview,
    decision_set: GlobalReviewDecisionSet,
    *,
    expected_chapter_ordinals: tuple[int, ...] | None = None,
    expected_workpack_sha256: str | None = None,
    now: str | None = None,
) -> GlobalReview:
    """Apply a batch atomically in memory; any stale row/scope rejects the whole set.

    ``blocking_scope`` 是“除显式例外外，其余当前 blocking 项同一裁决”的压缩输入。
    持久 review 仍逐项展开，所以下游闸、inbox 与旧消费者完全不需要理解默认值。
    """
    if decision_set.actor.strip().startswith('待填'):
        raise HumanEditProtected(
            'global-review actor 仍是模板占位值；必须填真实审阅者身份')
    for row in decision_set.decisions:
        if row.state != 'UNVER' and row.reason.strip().startswith('待人工核验'):
            raise HumanEditProtected(
                f'{row.item_id} 的 {row.state} 仍沿用模板占位理由；'
                '拒绝把改枚举冒充审阅')

    result = review
    timestamp = now or datetime.now(timezone.utc).isoformat()
    explicit_ids = {row.item_id for row in decision_set.decisions}
    scope = decision_set.blocking_scope
    if scope is not None:
        if scope.expected_workpack_sha256 == UNBOUND_WORKPACK_SHA256:
            raise HumanEditProtected(
                'global-review blocking scope 仍未绑定实际阅读的 workpack；'
                '请从已完整读过的 workpack 顶层复制 workpack_sha256')
        if (scope.state != 'UNVER'
                and scope.reason.strip().startswith('待人工核验')):
            raise HumanEditProtected(
                f'blocking scope 的 {scope.state} 仍沿用模板占位理由；'
                '拒绝只改状态就批量开闸')
        blocking = [item for item in review.items
                    if review_effect_of(item.kind) == 'blocking']
        if scope.expected_source_sha256 != review.source_sha256:
            raise HumanEditProtected(
                'global-review source changed; stale blocking scope rejected')
        if scope.expected_blocking_scope_sha256 != blocking_scope_sha256(review):
            raise HumanEditProtected(
                'global-review blocking item/hash surface changed; stale scope rejected')
        if scope.expected_item_count != len(blocking):
            raise HumanEditProtected(
                'global-review blocking item count changed; stale scope rejected')
        if (expected_workpack_sha256 is None
                or scope.expected_workpack_sha256 != expected_workpack_sha256):
            raise HumanEditProtected(
                'global-review blocking workpack changed; stale reading claim rejected')
        if expected_chapter_ordinals is None:
            raise HumanEditProtected(
                'blocking scope decision 缺当前 main_text 章全集，拒绝把阅读声明当成已核验')
        chapters = tuple(scope.reviewed_chapter_ordinals)
        if chapters != expected_chapter_ordinals:
            raise HumanEditProtected(
                'blocking scope 的 reviewed_chapter_ordinals 未精确覆盖当前正文章全集')
        for item in blocking:
            if item.item_id in explicit_ids:
                continue
            result = decide(
                result,
                item_id=item.item_id,
                expected_subject_sha256=item.subject_sha256,
                state=scope.state,
                actor=decision_set.actor,
                reason=scope.reason,
                evidence=[ref.model_dump() for ref in scope.evidence],
                generation_disposition=None,
                now=timestamp,
            )
    for row in decision_set.decisions:
        result = decide(
            result,
            item_id=row.item_id,
            expected_subject_sha256=row.expected_subject_sha256,
            state=row.state,
            actor=decision_set.actor,
            reason=row.reason,
            evidence=[ref.model_dump() for ref in row.evidence],
            generation_disposition=row.generation_disposition,
            now=timestamp,
        )
    return result
