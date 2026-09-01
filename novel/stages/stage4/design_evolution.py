# -*- coding: utf-8 -*-
"""Stage4A 跨轮演进的纯语义核心。

本模块故意不知道审批盘、CLI、agent run 与产物 provenance。它只回答五个问题：

* 同一份宏观重构策略属于哪个 epoch；
* 某章真正消费的「宏观策略 + 自己的 weight」身份是什么；
* 一组 typed 叙事关系如何规范化、跨轮单调合并，以及在发请求前是否自相矛盾；
* 当前完整章稿还有哪些累计关系没有闭合；
* replan 究竟影响哪些章，以及替换完整章时有没有碰到非目标章。

关系是否满足只委托 :func:`checks.narrative_relation_scene_findings`。这里不重写
第二套位置/先后/同场语义；否则 repair 的验收尺子会与 Stage4 正式 HARD 漂移。
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from novel.stages.stage4 import checks


_SHA1_RE: Final = re.compile(r'^[0-9a-f]{40}$')
_SHA256_RE: Final = re.compile(r'^[0-9a-f]{64}$')
_ALIAS_RE: Final = re.compile(r'^a_[0-9a-f]{16,64}$')

_EPOCH_CONTRACT: Final = 'stage4-design-strategy-epoch-v1'
_CHAPTER_STRATEGY_CONTRACT: Final = 'stage4-design-chapter-strategy-v1'
_RELATION_CONSTRAINT_CONTRACT: Final = 'stage4-design-relation-constraint-v1'
_RELATION_SET_CONTRACT: Final = 'stage4-design-relation-constraint-set-v1'

_BASIS_FIELDS: Final = frozenset({
    'review_contract', 'root_clean_sha1', 'truth_sha256', 'alias_algorithm',
})
_STRATEGY_FIELDS: Final = frozenset({
    'reconstruction_strategy', 'chapter_weights',
})
_WEIGHT_FIELDS: Final = frozenset({'chapter_ordinal', 'weight'})
_RELATION_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    'story_before': ('before_alias', 'after_alias'),
    'response_to': ('stimulus_alias', 'response_alias'),
    'same_scene': ('left_alias', 'right_alias'),
    'observes': ('observer_alias', 'observed_alias', 'observation_alias'),
}


class DesignEvolutionError(ValueError):
    """Stage4A 纯演进输入不闭合或累计约束不可满足。"""


@dataclass(frozen=True)
class UnclosedRelationConstraint:
    """当前完整章仍未满足的一条累计 typed relation。"""

    constraint_sha256: str
    kind: str
    findings: tuple[str, ...]


@dataclass(frozen=True)
class RelationConstraintEvaluation:
    """A 相关系判定；机器不可判的证明显式延迟。"""

    unclosed: tuple[UnclosedRelationConstraint, ...]
    needs_binding_preflight: tuple[str, ...]
    needs_story_time_review: tuple[str, ...]

    @property
    def locally_closed(self) -> bool:
        return not self.unclosed

    @property
    def fully_verified(self) -> bool:
        return (self.locally_closed and not self.needs_binding_preflight
                and not self.needs_story_time_review)


def _plain_mapping(value: object, *, where: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise DesignEvolutionError(f'{where} 必须是 object')
    return {str(key): copy.deepcopy(item) for key, item in value.items()}


def _canonical_bytes(value: object, *, where: str) -> bytes:
    try:
        rendered = json.dumps(
            value, ensure_ascii=False, sort_keys=True,
            separators=(',', ':'), allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise DesignEvolutionError(f'{where} 不是可复现 JSON：{exc}') from exc
    return rendered.encode('utf-8')


def _sha256(value: object, *, where: str) -> str:
    return hashlib.sha256(_canonical_bytes(value, where=where)).hexdigest()


def _nonblank(value: object, *, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DesignEvolutionError(f'{where} 必须是非空字符串')
    return value


def _sha(value: object, *, where: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        label = 'SHA-1' if pattern is _SHA1_RE else 'SHA-256'
        raise DesignEvolutionError(f'{where} 必须是完整小写 {label}')
    return value


def _ordinal(value: object, *, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise DesignEvolutionError(f'{where} 必须是正整数阅读序章号')
    return value


def strategy_epoch_sha256(
    basis: Mapping[str, object], reconstruction_strategy: str,
) -> str:
    """返回同一宏观策略 repair epoch 的稳定 SHA-256。

    epoch 刻意不含 chapter weights、run id 或代码指纹。只改某章 weight 会改变该章
    的请求身份，却不能洗掉同一宏观策略下已经承诺的 repair 约束与轮数。
    review contract / root / truth / alias 任一变化则必须开新 epoch。
    """
    raw = _plain_mapping(basis, where='design review basis')
    if set(raw) != _BASIS_FIELDS:
        raise DesignEvolutionError(
            'design review basis 字段必须精确等于 '
            f'{sorted(_BASIS_FIELDS)}，实得 {sorted(raw)}')
    normalized = {
        'review_contract': _nonblank(
            raw['review_contract'], where='basis.review_contract'),
        'root_clean_sha1': _sha(
            raw['root_clean_sha1'], where='basis.root_clean_sha1', pattern=_SHA1_RE),
        'truth_sha256': _sha(
            raw['truth_sha256'], where='basis.truth_sha256', pattern=_SHA256_RE),
        'alias_algorithm': _nonblank(
            raw['alias_algorithm'], where='basis.alias_algorithm'),
    }
    strategy = _nonblank(
        reconstruction_strategy, where='reconstruction_strategy')
    return _sha256({
        'contract': _EPOCH_CONTRACT,
        'basis': normalized,
        'reconstruction_strategy': strategy,
    }, where='strategy epoch')


def chapter_strategy_sha256(
    strategy_epoch: str, chapter_ordinal: int, chapter_weight: str,
) -> str:
    """返回一章实际消费的宏观策略投影身份。"""
    epoch = _sha(
        strategy_epoch, where='strategy_epoch_sha256', pattern=_SHA256_RE)
    ordinal = _ordinal(chapter_ordinal, where='chapter_ordinal')
    weight = _nonblank(chapter_weight, where='chapter_weight')
    return _sha256({
        'contract': _CHAPTER_STRATEGY_CONTRACT,
        'strategy_epoch_sha256': epoch,
        'chapter_ordinal': ordinal,
        'chapter_weight': weight,
    }, where='chapter strategy')


def _canonical_relation(
    value: Mapping[str, object], *, allow_review: bool,
) -> dict[str, str]:
    """规范化 relation；``allow_review`` 只供 blind truth 成员匹配。"""
    raw = _plain_mapping(value, where='design relation constraint')
    kind = _nonblank(raw.get('kind'), where='relation.kind')
    fields = _RELATION_FIELDS.get(kind)
    if fields is None:
        raise DesignEvolutionError(
            f'relation.kind 必须属于 {tuple(_RELATION_FIELDS)}，实得 {kind!r}')
    allowed = {'kind', 'enforcement', *fields}
    if set(raw) - allowed or not {'kind', *fields}.issubset(raw):
        raise DesignEvolutionError(
            f'{kind} relation 字段必须精确等于 {sorted(allowed)}（enforcement 可省），'
            f'实得 {sorted(raw)}')
    enforcement = raw.get('enforcement', 'hard')
    accepted_enforcements = {'hard', 'review'} if allow_review else {'hard'}
    if enforcement not in accepted_enforcements:
        raise DesignEvolutionError(
            'relation.enforcement 必须属于 '
            f'{sorted(accepted_enforcements)}，实得 {enforcement!r}')

    aliases: dict[str, str] = {}
    for field in fields:
        alias = raw[field]
        if not isinstance(alias, str) or _ALIAS_RE.fullmatch(alias) is None:
            raise DesignEvolutionError(f'{kind}.{field} 必须是正式 opaque alias')
        aliases[field] = alias

    if kind == 'same_scene' and aliases['left_alias'] >= aliases['right_alias']:
        raise DesignEvolutionError('same_scene alias 必须升序且不能自指')
    endpoint_fields = {
        'story_before': ('before_alias', 'after_alias'),
        'response_to': ('stimulus_alias', 'response_alias'),
        'same_scene': ('left_alias', 'right_alias'),
        'observes': ('observed_alias', 'observation_alias'),
    }[kind]
    if aliases[endpoint_fields[0]] == aliases[endpoint_fields[1]]:
        raise DesignEvolutionError(f'{kind} relation 不能自指')
    # repair evaluator 总把命中的 truth relation 当作本 epoch 的 HARD 要求；来源
    # enforcement 不进入 fingerprint，避免 review→repair 时制造两个语义身份。
    return {'kind': kind, 'enforcement': 'hard', **aliases}


def canonical_relation_constraint(value: Mapping[str, object]) -> dict[str, str]:
    """把一条模型无关的 repair relation 规范化为正式 HARD 形状。

    repair constraint 本身就是要求，不接受 ``enforcement=review``。调用者可以省略
    enforcement；返回值总会显式写成 ``hard``，可原样交给生产 relation 判官。
    """
    return _canonical_relation(value, allow_review=False)


def relation_constraint_fingerprint(value: Mapping[str, object]) -> str:
    """返回 relation 内容身份；truth 的 review/hard enforcement 不参与身份。"""
    relation = _canonical_relation(value, allow_review=True)
    return _sha256({
        'contract': _RELATION_CONSTRAINT_CONTRACT,
        'relation': relation,
    }, where='relation constraint fingerprint')


def _relation_order_edge(
    relation: Mapping[str, str],
) -> tuple[str, str, bool] | None:
    kind = relation['kind']
    if kind == 'story_before':
        return relation['before_alias'], relation['after_alias'], True
    if kind == 'response_to':
        return relation['stimulus_alias'], relation['response_alias'], False
    if kind == 'observes':
        return relation['observed_alias'], relation['observation_alias'], False
    return None


def _require_relation_set_consistent(
    relations: Sequence[Mapping[str, str]],
) -> None:
    """关闭必然不可满足的顺序环；不为 same_scene 发明更强语义。

    ``story_before`` 是严格 ``<``；response/observes 是 ``<=``。有向环中只要
    含一条严格边就不可满足。``same_scene`` 的生产尺子是“场景集合有交集”，并不
    等于首次可见场相同，因此这里不能把它擅自并入等价类制造假冲突。
    """
    edges = [edge for relation in relations
             if (edge := _relation_order_edge(relation)) is not None]
    reach: dict[str, set[str]] = {}
    for before, after, _strict in edges:
        reach.setdefault(before, set()).add(after)
        reach.setdefault(after, set())
    changed = True
    while changed:
        changed = False
        for before in tuple(reach):
            expanded = set().union(*(reach.get(after, set())
                                     for after in tuple(reach[before])))
            if not expanded.issubset(reach[before]):
                reach[before].update(expanded)
                changed = True
    conflicting = sorted({
        f'{before}<{after}'
        for before, after, strict in edges
        if strict and before in reach.get(after, set())
    })
    if conflicting:
        raise DesignEvolutionError(
            f'累计 repair relations 含不可满足的严格顺序环：{conflicting}')


def merge_relation_constraints(
    committed: Collection[Mapping[str, object]],
    incoming: Collection[Mapping[str, object]],
) -> tuple[dict[str, str], ...]:
    """单调合并前轮与本轮 relations，去重、规范排序并做 pre-wire 冲突检查。"""
    by_bytes: dict[bytes, dict[str, str]] = {}
    for label, rows in (('committed', committed), ('incoming', incoming)):
        if isinstance(rows, (str, bytes)) or not isinstance(rows, Collection):
            raise DesignEvolutionError(f'{label} relation constraints 必须是 collection')
        for index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                raise DesignEvolutionError(
                    f'{label}[{index}] relation constraint 必须是 object')
            canonical = canonical_relation_constraint(row)
            by_bytes[_canonical_bytes(
                canonical, where=f'{label}[{index}] relation')] = canonical
    merged = tuple(copy.deepcopy(by_bytes[key]) for key in sorted(by_bytes))
    _require_relation_set_consistent(merged)
    return merged


def accumulate_relation_constraints(
    *,
    parent_epoch_sha256: str,
    current_epoch_sha256: str,
    inherited: Collection[Mapping[str, object]],
    requested: Collection[Mapping[str, object]],
    truth_relations: Collection[Mapping[str, object]],
) -> tuple[dict[str, str], ...]:
    """按 epoch 单调累计，并要求每条约束都来自本章 blind truth 的 typed relation。

    Stage4 人审只把上游已经声明为 ``review`` 的关系升级为本 epoch repair 要求，
    不能在这里发明新的剧情关系。宏观策略 epoch 变化时旧约束退出，只保留新请求。
    """
    parent_epoch = _sha(
        parent_epoch_sha256, where='parent_epoch_sha256', pattern=_SHA256_RE)
    current_epoch = _sha(
        current_epoch_sha256, where='current_epoch_sha256', pattern=_SHA256_RE)
    truth_by_fingerprint: dict[str, str] = {}
    for index, value in enumerate(truth_relations):
        raw = _plain_mapping(value, where=f'truth_relations[{index}]')
        enforcement = raw.get('enforcement')
        if enforcement not in ('hard', 'review'):
            raise DesignEvolutionError(
                f'truth_relations[{index}].enforcement 必须是 hard/review')
        fingerprint = relation_constraint_fingerprint(raw)
        if fingerprint in truth_by_fingerprint:
            raise DesignEvolutionError('truth_relations 含重复语义 relation')
        truth_by_fingerprint[fingerprint] = str(enforcement)

    base = inherited if parent_epoch == current_epoch else ()
    merged = merge_relation_constraints(base, requested)
    for relation in merged:
        fingerprint = relation_constraint_fingerprint(relation)
        if fingerprint not in truth_by_fingerprint:
            raise DesignEvolutionError(
                f'repair relation 不属于当前 blind truth：{fingerprint}')
    for relation in requested:
        fingerprint = relation_constraint_fingerprint(relation)
        if truth_by_fingerprint.get(fingerprint) != 'review':
            raise DesignEvolutionError(
                '新 repair relation 必须精确命中 blind truth 的 review relation：'
                f'{fingerprint}')
    return merged


def relation_constraint_set_sha256(
    constraints: Collection[Mapping[str, object]],
) -> str:
    """返回规范约束集身份；重复与输入顺序不影响结果。"""
    canonical = merge_relation_constraints((), constraints)
    return _sha256({
        'contract': _RELATION_SET_CONTRACT,
        'constraints': canonical,
    }, where='relation constraint set')


def evaluate_relation_constraints(
    constraints: Collection[Mapping[str, object]],
    chapter: Mapping[str, object],
) -> RelationConstraintEvaluation:
    """用正式 Stage4 relation 判官列出未闭合约束与 B 相延期证明。"""
    canonical = merge_relation_constraints((), constraints)
    raw_chapter = _plain_mapping(chapter, where='blind candidate chapter')
    scenes = raw_chapter.get('scenes')
    if not isinstance(scenes, list) or not scenes:
        raise DesignEvolutionError('blind candidate chapter.scenes 必须是非空 array')
    plain_scenes: list[dict[str, Any]] = []
    for index, scene in enumerate(scenes):
        plain_scenes.append(_plain_mapping(
            scene, where=f'blind candidate chapter.scenes[{index}]'))
    positions = checks.causal_scene_positions(
        plain_scenes,
        land_key='land_fact_aliases', context_key='context_fact_aliases')
    unclosed: list[UnclosedRelationConstraint] = []
    needs_binding_preflight: list[str] = []
    needs_story_time_review: list[str] = []
    for relation in canonical:
        fingerprint = relation_constraint_fingerprint(relation)
        if relation['kind'] == 'observes':
            # A scene 没有 stage_refs/present_char_ids。这里能判 observed→observation
            # 的先后，却不能证明 observer 在 observation 场，必须交 A→formal/B。
            needs_binding_preflight.append(fingerprint)
        findings = checks.narrative_relation_scene_findings(
            [relation], positions, suffix='_alias')
        story_time_bad, story_time_unknown = checks.narrative_story_time_findings(
            [relation], plain_scenes,
            suffix='_alias', land_key='land_fact_aliases')
        findings.extend(story_time_bad)
        if story_time_unknown:
            needs_story_time_review.append(fingerprint)
        if findings:
            unclosed.append(UnclosedRelationConstraint(
                constraint_sha256=fingerprint,
                kind=relation['kind'],
                findings=tuple(findings),
            ))
    return RelationConstraintEvaluation(
        unclosed=tuple(unclosed),
        needs_binding_preflight=tuple(needs_binding_preflight),
        needs_story_time_review=tuple(needs_story_time_review),
    )


def _strategy_view(
    value: Mapping[str, object], *, where: str,
) -> tuple[str, dict[int, str]]:
    raw = _plain_mapping(value, where=where)
    if set(raw) != _STRATEGY_FIELDS:
        raise DesignEvolutionError(
            f'{where} 字段必须精确等于 {sorted(_STRATEGY_FIELDS)}，'
            f'实得 {sorted(raw)}')
    strategy = _nonblank(
        raw['reconstruction_strategy'], where=f'{where}.reconstruction_strategy')
    rows = raw['chapter_weights']
    if not isinstance(rows, list) or not rows:
        raise DesignEvolutionError(f'{where}.chapter_weights 必须是非空 array')
    weights: dict[int, str] = {}
    for index, value_row in enumerate(rows):
        row = _plain_mapping(
            value_row, where=f'{where}.chapter_weights[{index}]')
        if set(row) != _WEIGHT_FIELDS:
            raise DesignEvolutionError(
                f'{where}.chapter_weights[{index}] 字段必须精确等于 '
                f'{sorted(_WEIGHT_FIELDS)}')
        ordinal = _ordinal(
            row['chapter_ordinal'],
            where=f'{where}.chapter_weights[{index}].chapter_ordinal')
        if ordinal in weights:
            raise DesignEvolutionError(f'{where}.chapter_weights 章号重复：{ordinal}')
        weights[ordinal] = _nonblank(
            row['weight'], where=f'{where}.chapter_weights[{index}].weight')
    return strategy, weights


def replan_affected_chapters(
    previous: Mapping[str, object], replacement: Mapping[str, object],
) -> tuple[int, ...]:
    """按真实 prompt 消费面返回 replan 必须重跑的章节。

    宏观 reconstruction strategy 变化会影响全章；策略文字不变时，只重跑自己
    weight 变化的章。完全 no-op 返回空 tuple，供上层 dynamic validator 明确拒绝。
    """
    old_strategy, old_weights = _strategy_view(previous, where='previous strategy')
    new_strategy, new_weights = _strategy_view(replacement, where='replacement strategy')
    if set(old_weights) != set(new_weights):
        raise DesignEvolutionError(
            'replan 不得改变章集合：'
            f'previous={sorted(old_weights)}, replacement={sorted(new_weights)}')
    ordinals = tuple(sorted(old_weights))
    if old_strategy != new_strategy:
        return ordinals
    return tuple(
        ordinal for ordinal in ordinals
        if old_weights[ordinal] != new_weights[ordinal])


def _chapters_by_ordinal(
    candidate: Mapping[str, object], *, where: str,
) -> tuple[dict[str, Any], list[int], dict[int, dict[str, Any]]]:
    raw = _plain_mapping(candidate, where=where)
    rows = raw.get('output_chapters')
    if not isinstance(rows, list) or not rows:
        raise DesignEvolutionError(f'{where}.output_chapters 必须是非空 array')
    order: list[int] = []
    by_ordinal: dict[int, dict[str, Any]] = {}
    for index, value in enumerate(rows):
        row = _plain_mapping(value, where=f'{where}.output_chapters[{index}]')
        ordinal = _ordinal(
            row.get('output_ordinal'),
            where=f'{where}.output_chapters[{index}].output_ordinal')
        if ordinal in by_ordinal:
            raise DesignEvolutionError(f'{where}.output_chapters 章号重复：{ordinal}')
        order.append(ordinal)
        by_ordinal[ordinal] = row
    if order != sorted(order):
        raise DesignEvolutionError(f'{where}.output_chapters 必须按章号升序')
    return raw, order, by_ordinal


def non_target_chapter_drift(
    previous: Mapping[str, object], current: Mapping[str, object],
    affected_chapters: Collection[int],
) -> tuple[int, ...]:
    """返回发生业务字节漂移的非目标章号；章集合漂移关闭式拒绝。"""
    _old_raw, old_order, old_by = _chapters_by_ordinal(
        previous, where='previous candidate')
    _new_raw, new_order, new_by = _chapters_by_ordinal(
        current, where='current candidate')
    if old_order != new_order:
        raise DesignEvolutionError(
            f'candidate 章集合/顺序漂移：previous={old_order}, current={new_order}')
    targets = {
        _ordinal(value, where='affected_chapters') for value in affected_chapters
    }
    unknown = sorted(targets - set(old_order))
    if unknown:
        raise DesignEvolutionError(f'affected_chapters 含未知章：{unknown}')
    return tuple(
        ordinal for ordinal in old_order
        if ordinal not in targets and old_by[ordinal] != new_by[ordinal])


def replace_candidate_chapters(
    candidate: Mapping[str, object],
    replacements: Collection[Mapping[str, object]],
    *, target_chapters: Collection[int],
) -> dict[str, Any]:
    """以完整章替换精确目标集，并由构造保证根部与非目标章不漂。

    本函数不改 provenance；producer 在写盘前另铸真实的 partial-rewrite/evolution
    来源账。这里保留输入根部原样，便于在 provenance 处理之前复验业务替换。
    """
    raw, order, chapters = _chapters_by_ordinal(candidate, where='candidate')
    targets = sorted({
        _ordinal(value, where='target_chapters') for value in target_chapters
    })
    if len(targets) != len(tuple(target_chapters)):
        raise DesignEvolutionError('target_chapters 不得重复')
    unknown = sorted(set(targets) - set(order))
    if unknown:
        raise DesignEvolutionError(f'target_chapters 含未知章：{unknown}')
    replacement_by: dict[int, dict[str, Any]] = {}
    for index, value in enumerate(replacements):
        row = _plain_mapping(value, where=f'replacements[{index}]')
        ordinal = _ordinal(
            row.get('output_ordinal'), where=f'replacements[{index}].output_ordinal')
        if ordinal in replacement_by:
            raise DesignEvolutionError(f'replacements 章号重复：{ordinal}')
        replacement_by[ordinal] = row
    if sorted(replacement_by) != targets:
        raise DesignEvolutionError(
            '完整章 replacements 必须精确覆盖 target_chapters：'
            f'targets={targets}, replacements={sorted(replacement_by)}')

    result = copy.deepcopy(raw)
    result['output_chapters'] = [
        copy.deepcopy(replacement_by.get(ordinal, chapters[ordinal]))
        for ordinal in order
    ]
    drifted = non_target_chapter_drift(raw, result, targets)
    if drifted:  # 由构造不应到达；保留为未来改动的守卫。
        raise DesignEvolutionError(f'完整章替换改动了非目标章：{list(drifted)}')
    return result
