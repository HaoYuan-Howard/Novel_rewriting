"""Stage2 的唯一机械检查入口；各正式产物保持独立所有权。"""
from __future__ import annotations
import re
from typing import Any, Iterable
from contracts.artifacts import STAGE1_MAIN_TEXT, STAGE2_CHAPTER_BLUEPRINTS, STAGE2_GLOBAL_GROUNDING
from contracts.rules import REGISTRY
from contracts.schema.common import Finding
from contracts.schema.report import finding as _finding
from contracts.schema.stage2 import BlueprintScopeViolation
from novel.report.findings import Outcome, ran, unverifiable

_FIX_CHAPTER_EVIDENCE = (
    re.compile(r'\bb0*(\d+)_\d+\b'),
    re.compile(r'\bch0*(\d+)\b', re.IGNORECASE),
    re.compile(r'\b(?:missing|extra|dup):0*(\d+)\b'),
)


def finding(rule: str, *, detail: str, evidence: list[str], **kwargs) -> Finding:
    """把 producer 的 typed 章号铸成最终 CLI 实参。

    只有真正书级、但修复需要一个章节集合的旧规则才回退到机器 evidence；
    章级 finding 的 ``output_ordinal`` 永远优先，报告/view 不猜散文格式。
    推不出时故意不编值，报告会把路由标成 unavailable 并说明原因。
    """
    row = REGISTRY.get(rule)
    fix_args: dict[str, str] = {}
    if '--chapters' in row.fix_args_from_finding:
        # 章级 producer 已经知道所属章时，修复实参必须以 typed 字段为准。
        # 例如跨章引用错误的 evidence 同时含「所属章的 beat_id」与
        # 「误引到的另一章」；继续扫 evidence 会把无辜的被引章也塞进付费重跑。
        output_ordinal = kwargs.get('output_ordinal')
        chapters = ({output_ordinal} if output_ordinal is not None else {
            int(value)
            for item in evidence
            for pattern in _FIX_CHAPTER_EVIDENCE
            for value in pattern.findall(item)
        })
        if chapters:
            fix_args['--chapters'] = ','.join(str(chapter) for chapter in sorted(chapters))
    return _finding(rule, detail=detail, evidence=evidence, fix_args=fix_args, **kwargs)


def _output_ordinal(value: Any) -> int | None:
    """原始候选里的合法阅读序章号；坏形状仍由原判据报告，不让 Finding 自己崩。"""
    return value if type(value) is int and value >= 1 else None


def _scope_violation_ordinal(row: Any) -> int | None:
    """生产 scope 账是 JSON 字符串；只读其 typed requested 单章，不猜散文。"""
    try:
        requested = BlueprintScopeViolation.model_validate_json(row).requested
    except (TypeError, ValueError):
        return None
    if len(requested) != 1:
        return None
    return _output_ordinal(requested[0])

# ─────────────────────────────────────────────────────────────────────────────
# 零、只有本层判据读的几张表
# ─────────────────────────────────────────────────────────────────────────────
#
# ⚠ 这三张**刻意不进 `contracts/invariants.py`**（形态照 `stage5/checks.py::META_PATTERNS`）：
#   那张表收的是「**同时**进 prompt 与判据」的枚举，而这三份只有判据一个消费方。
#   放进去就是给它们造第二个家。
#   ⚠ **`BARE_PRONOUNS` 是反例**：它有三个消费方（人物 `characters.SYSTEM` 的禁令 ·
#   `address_bare_pronoun`/`address_has_pronoun`）
#   ⇒ 它在 `invariants` 里，本模块 import 它、**不手抄子集**。

#: `role` / `fate` / `canonical` 里的模棱语。
#: ⚠ 只收能独立表达不确定性的词。单字「或」也用于确定事实的并列选择
#: （「建议过继或让父母再生」），没有分词/句法边界时拿它做子串扫描会误报。
HEDGE_WORDS: tuple[str, ...] = (
    '可能', '似乎', '大约', '应该是', '疑似', '也许', '或许', '之类',
)

#: 「不可能」是确定性判断，不是「可能」的模棱版。但再否定一次的
#: 「并非不可能 / 不是不可能 / 未必不可能」又把可能性打开了，仍是不确定。
#: 这份前缀表只服务下方同一个匹配器，不进 prompt，不在判据调用处手拼第二份。
_UNCERTAIN_NEGATED_IMPOSSIBILITY_PREFIXES: tuple[str, ...] = (
    '并非', '不是', '未必', '未尝', '未见得', '不能说', '难说',
)


def _contains_hedge(text: str, word: str) -> bool:
    """这个字段是否真含该模棱语。

    其他词仍按完整短语扫描；「可能」额外区分确定的「不可能」与
    重新打开可能性的双重否定。「不太可能」的「可能」前一字是「太」，
    会自然落入命中分支。
    """
    if word != '可能':
        return word in text

    start = 0
    while (index := text.find(word, start)) >= 0:
        if index == 0 or text[index - 1] != '不':
            return True
        before_negation = text[:index - 1]
        if any(before_negation.endswith(prefix)
               for prefix in _UNCERTAIN_NEGATED_IMPOSSIBILITY_PREFIXES):
            return True
        start = index + len(word)
    return False


def _hedge_hits(text: str) -> list[str]:
    """一个字段内命中的词；同一词出现多次仍只报一格。"""
    return [word for word in HEDGE_WORDS if _contains_hedge(text, word)]

class _Ctx:
    """本轮 Global、独立人物、Blueprint 与 MainText；任何缺失都保持可区分。"""
    def __init__(self, global_g, blueprints, main_text, *, characters=None):
        self.gg = global_g or {}
        self.has_gg = global_g is not None
        self.bp_raw = blueprints or {}
        self.has_bp = blueprints is not None
        self.bps = list(self.bp_raw.get('blueprints') or [])
        self.prov = self.bp_raw.get('_provenance') or {}
        self.gprov = self.gg.get('_provenance') or {}
        self.registry = characters
        self.chars = (characters or {}).get('characters') or {}
        # 空正文与缺正文一样什么都判不了：逐章检查不允许在空坐标表上装作已查。
        self.has_mt = bool(main_text)
        self.text_of = {row['chapter_ordinal']: row.get('text', '') for row in main_text or []}
        self.ords = list(self.text_of)
        self.n_chapters = len(self.ords)


_NO_GLOBAL = '缺少当前 Global 四类知识载体，不能判定'
_NO_BLUEPRINT = '缺少当前完整 Blueprint，不能判定'
_NO_MAIN_TEXT = '缺少当前 MainText，不能判定'


def _cut(items, n=8):
    values = list(items)
    return str(values[:n]) + (f' …另 {len(values)-n} 条' if len(values)>n else '')


def _alignment(c: _Ctx) -> list[Outcome]:
    out: list[Outcome] = []

    # ── `upstream_missing` ────────────────────────────────────────────────
    # ⚠ **缺一份判 FAIL 不判 note**：什么都没查却打 PASS，正是
    #   「一条从不执行的判据与一条不存在的判据在报告上长得一样」那个病。
    missing = [rel for rel, ok in ((STAGE2_GLOBAL_GROUNDING, c.has_gg),
                                   (STAGE2_CHAPTER_BLUEPRINTS, c.has_bp),
                                   (STAGE1_MAIN_TEXT, c.has_mt)) if not ok]
    out.append(ran('upstream_missing', [
        finding('upstream_missing',
                detail=f'本次缺少上游产物 {rel}',
                evidence=[rel]) for rel in missing],
        scanned='3 份上游（两份 stage2 产物 + main_text）'))

    if not c.has_bp:
        for rid in ('section_id_leak', 'chapter_coverage', 'chapter_order'):
            out.append(unverifiable(rid, _NO_BLUEPRINT))
        return out

    # ── `section_id_leak` ─────────────────────────────────────────────────
    # 章的唯一键**全链只有一个**。混进 `section_id` 之后，下游按哪个键 join
    # 就成了一件要去读代码才知道的事。
    leaks = sorted({o for o in (_walk_keys(c.bp_raw, 'section_id'))})
    out.append(ran('section_id_leak',
                   [finding('section_id_leak',
                            detail=f'blueprint 含 section_id 的字段路径：{_cut(leaks)}',
                            evidence=[str(x) for x in leaks])] if leaks else [],
                   scanned=f'{len(c.bps)} 章的全部字段路径'))

    # ── `chapter_coverage` / `chapter_order` ──────────────────────────────
    if not c.has_mt:
        out.append(unverifiable('chapter_coverage', _NO_MAIN_TEXT))
        out.append(unverifiable('chapter_order', _NO_MAIN_TEXT))
        return out

    got = [x.get('chapter_ordinal') for x in c.bps]
    expected = list(c.ords)
    fs: list[Finding] = []
    if (miss := [o for o in expected if o not in got]):
        fs.extend(finding(
            'chapter_coverage', detail=f'缺章（阅读序）ch{o}',
            evidence=[f'missing:{o}'], output_ordinal=_output_ordinal(o),
        ) for o in miss)
    if (extra := [o for o in got if o not in expected]):
        fs.extend(finding(
            'chapter_coverage', detail=f'多余章（不在 main_text）ch{o}',
            evidence=[f'extra:{o}'], output_ordinal=_output_ordinal(o),
        ) for o in extra)
    if (dup := sorted({o for o in got if got.count(o) > 1})):
        fs.extend(finding(
            'chapter_coverage', detail=f'重复章 ch{o}',
            evidence=[f'dup:{o}'], output_ordinal=_output_ordinal(o),
        ) for o in dup)
    out.append(ran('chapter_coverage', fs, scanned=f'{len(got)}/{len(expected)} 章'))

    ordered = [o for o in got if o in expected]
    sorted_ordered = sorted(ordered)
    misplaced = [
        (index, actual, wanted)
        for index, (actual, wanted) in enumerate(
            zip(ordered, sorted_ordered, strict=True), start=1)
        if actual != wanted
    ]
    out.append(ran('chapter_order',
                   [finding('chapter_order',
                            detail=f'blueprint 第 {index} 项是 ch{actual}，应为 ch{wanted}；'
                                   f'完整章序 {ordered}',
                            evidence=[f'position:{index}', f'ch{actual}', f'want:ch{wanted}'],
                            output_ordinal=_output_ordinal(actual))
                    for index, actual, wanted in misplaced],
                   scanned=f'{len(ordered)} 章'))
    return out


def _walk_keys(node: Any, key: str, where: str = '') -> Iterable[str]:
    """整棵树里出现过 `key` 的路径。**递归而不是只看两层**——
    一个死字段留在第三层与留在顶层，对下游是同一件事。"""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                yield f'{where}.{k}'.lstrip('.')
            yield from _walk_keys(v, key, f'{where}.{k}'.lstrip('.'))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk_keys(v, key, f'{where}[{i}]')


GLOBAL_RULES = ('global_core_contract', 'global_temporal_contract', 'relation_dangling',
                'canon_chapter_refs_dangling', 'global_evidence_unlocated', 'secrets_absent')


def check_global(global_g: dict[str, Any] | None, main_text: list[dict[str, Any]] | None,
                 *, characters: dict[str, Any] | None = None) -> list[Outcome]:
    """Global 生成、Analyze、人工编辑与免费复验共用的机械尺子；不消费 Blueprint。"""
    from pydantic import ValidationError
    from contracts.schema.knowledge import KnowledgeCore
    from novel.stages.stage2.shared.core_evidence import validate_evidence_anchors
    if global_g is None:
        return [unverifiable(rule, _NO_GLOBAL) for rule in GLOBAL_RULES]
    problems: dict[str, list[str]] = {rule: [] for rule in GLOBAL_RULES if rule != 'secrets_absent'}
    try:
        KnowledgeCore.model_validate({key: value for key, value in global_g.items() if key != '_provenance'})
    except (ValidationError, TypeError, ValueError) as exc:
        if isinstance(exc, ValidationError):
            problems['global_core_contract'] = [
                '.'.join(map(str, row['loc'])) + ': ' + row['msg']
                for row in exc.errors(include_input=False, include_url=False)]
        else:
            problems['global_core_contract'] = [str(exc)]
    ordinals = {row['chapter_ordinal'] for row in main_text or []}
    def scan(value: Any, path=''):
        if isinstance(value, dict):
            for key, item in value.items():
                where = (path + '.' + key).strip('.')
                if key in {'effective_from', 'effective_until', 'revealed_from', 'chapter_ordinal'}:
                    if item is not None and (type(item) is not int or item not in ordinals):
                        problems['canon_chapter_refs_dangling'].append(where + ': ' + str(item))
                elif key == 'setup_chapters' and isinstance(item, list):
                    problems['canon_chapter_refs_dangling'].extend(
                        where + ': ' + str(chapter) for chapter in item if type(chapter) is not int or chapter not in ordinals)
                elif key != '_provenance':
                    scan(item, where)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                scan(item, f'{path}.{index}')
    if main_text is not None:
        scan(global_g)
        try:
            evidence = validate_evidence_anchors(global_g, global_g.get('evidence_anchors') or {}, main_text)
            problems['global_evidence_unlocated'] = [f'{path}: {reason}' for path, reason in evidence.items()]
        except (ValueError, TypeError) as exc:
            problems['global_evidence_unlocated'] = [str(exc)]
    for rule in global_g.get('world_rules') or []:
        previous_end = None
        previous_start = None
        for index, version in enumerate(rule.get('versions') or []):
            start, end = version.get('effective_from'), version.get('effective_until')
            if type(start) is not int:
                continue
            if (index and (previous_end is None or type(previous_end) is not int or start <= previous_end)):
                problems['global_temporal_contract'].append(str(rule.get('rule_id')) + ': 版本成立区间重叠或未按时间排序')
            if end is not None and (type(end) is not int or end < start):
                problems['global_temporal_contract'].append(str(rule.get('rule_id')) + ': 含末章早于起章')
            previous_start, previous_end = start, end
    if characters is not None:
        ids = set(characters.get('characters') or {})
        for row in global_g.get('relations') or []:
            for key in ('subject_id', 'object_id'):
                if row.get(key) not in ids:
                    problems['relation_dangling'].append(f"{row.get('relation_id')}.{key}: {row.get(key)}")
    result = [ran(rule, [finding(rule, detail='；'.join(details), evidence=details)] if details else [],
                  scanned='Global 四类主体与共享证据') for rule, details in problems.items()]
    if main_text is None:
        result = [unverifiable(row.rule, _NO_MAIN_TEXT) if row.rule in {
            'canon_chapter_refs_dangling', 'global_evidence_unlocated'} else row for row in result]
    if characters is None:
        result = [unverifiable(row.rule, '缺少独立人物登记，关系外键不可判')
                  if row.rule == 'relation_dangling' else row for row in result]
    result.append(unverifiable('secrets_absent', '长线秘密覆盖完整性由全书审阅核验',
                               scanned=f"{len(global_g.get('arc_secrets') or [])} 条长线秘密"))
    return result


def check_characters(registry: dict[str, Any], main_text: list[dict[str, Any]]) -> list[Outcome]:
    from novel.stages.stage2.characters.checks import check_characters as run
    return run(registry, main_text)


def blueprint_projection_blocking_hard_hits(outcomes: Iterable[Outcome]) -> tuple[str, ...]:
    # 身份闭集/证据/可用章都会改变称名材料；不按旧 address 派发字段猜消费者。
    relevant = {'character_unattested', 'address_unlocatable', 'address_from_out_of_range',
                'address_bare_pronoun', 'global_evidence_unlocated'}
    return tuple(sorted({row.rule for row in outcomes if row.rule in relevant
                         and row.unver_why is None and row.findings
                         and REGISTRY.get(row.rule).sev == 'hard'}))


def blueprint_projection_blockers(*, global_g, main_text):
    """历史参数名保留给store内部调用，值必须是独立 Characters 当前载体。"""
    return blueprint_projection_blocking_hard_hits(check_characters(global_g, main_text))


def check(*, global_g, blueprints, main_text, characters=None) -> list[Outcome]:
    from novel.stages.stage2.characters.checks import CHARACTER_RULES
    from novel.stages.stage2.blueprint.checks import check_plot
    context = _Ctx(global_g, blueprints, main_text, characters=characters)
    rows = [*_alignment(context), *check_global(global_g, main_text, characters=characters)]
    if characters is not None:
        if main_text is not None:
            rows.extend(check_characters(characters, main_text))
        else:
            # 缺 MainText 时人物判据整体不可判，但登记的规则行必须照常出现。
            rows.extend(unverifiable(rule, _NO_MAIN_TEXT) for rule in CHARACTER_RULES)
    rows.extend(check_plot(context))
    # Characters 与 Global 共用证据规则，但联合报告恰好输出一次，不吞任一侧失败。
    merged: dict[str, Outcome] = {}
    for row in rows:
        previous = merged.get(row.rule)
        if previous is None:
            merged[row.rule] = row
        elif previous.unver_why is None and row.unver_why is None:
            merged[row.rule] = ran(row.rule, [*previous.findings, *row.findings],
                                   scanned='Characters 与 Global 独立证据表')
        elif row.unver_why is None:
            merged[row.rule] = row
    return list(merged.values())
