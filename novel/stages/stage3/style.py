#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
style.py —— 本层的**节点函数**。`argv` 那一头留给 Phase D（`build_argv` / `build_steps`）。

四条 0 成本路先落地，付费路最后接：
  · `run_check_only(ctx)`    复检一份已有产物 → `CheckReport`
  · `run_measure_only(ctx)`  只跑确定性实测并报出来，**不写盘**
  · `run_remeasure(ctx)`     照盘上 stage1 重算 `measurable` 写回（**0 成本**）
  · `run_revalidate(ctx)`    按**当前**判据重判 `.INVALID`，合格即提升

⚠ 四条都**不发一次请求**。付费路（`--dry-run` 先验 prompt）见 `prompts.py`。
⚠ `build_prompt(ctx)` 是 `--dry-run` 那一条，与真跑**共用同一个构造入口**（`R-11.8`）。
"""
from __future__ import annotations

from contracts.artifacts import (
    STAGE1_HOOK_COPY, STAGE1_MAIN_TEXT, STAGE2_GLOBAL_GROUNDING,
    STAGE3_STYLE_EDIT, STAGE3_STYLE_GROUNDING,
)

import copy
import json

from contracts.schema.report import CheckReport
from contracts.schema.stage1 import HookCopy, MainText
from contracts.schema.stage2 import GlobalGrounding
from contracts.schema.stage3 import (
    ChapterStyle, ChapterStyleResponse, HabitRepairResponse, Measurable, StyleAttempt,
    StyleEdit, StyleGrounding, StyleProvenance, StyleResponse,
)
from novel import paths
from novel.core.naming import names_of, spoiler_names_of
from novel.core.text import norm
from novel.errors import GateBlocked, TerminalState, UpstreamMissing
from novel.llm.client import LlmClient
from novel.llm.ledger import combine_usage
from novel.prov import fingerprint
from novel.report import build
from novel.run import RunContext
from novel.stages.stage3 import checks, prompts
from novel.stages.stage3.measure import corpus_of, measure_all
from novel.store import ArtifactStore

REL = STAGE3_STYLE_GROUNDING
MAIN_TEXT = STAGE1_MAIN_TEXT
HOOK_COPY = STAGE1_HOOK_COPY
GLOBAL_GROUNDING = STAGE2_GLOBAL_GROUNDING

#: `--apply-edit` 的缺省决策集路径。形状是契约（`ARTIFACTS` 里登记着 `StyleEdit`）。
EDIT = STAGE3_STYLE_EDIT

#: 被闸门拦下的那一份。**从 `REL` 派生，不手写第二个字符串**——
#: 手写的那份会在改文件名时安静地指向一个不存在的路径。
_INVALID = paths.invalid_of(REL)


# 本节点的 consumed 投影统一登记在 `novel.consumption`；读写与 freshness 共用同一函数。


def _upstreams(store: ArtifactStore) -> tuple[MainText, HookCopy, GlobalGrounding]:
    """读取当前契约要求的三份上游。"""
    main = store.read(MainText, MAIN_TEXT)
    hook = store.read(HookCopy, HOOK_COPY)
    gg = store.read(GlobalGrounding, GLOBAL_GROUNDING)
    return main, hook, gg


def run_check_only(ctx: RunContext) -> CheckReport:
    """0 成本复检一份已有产物。**不写盘、不发请求。**

    ⚠ 它**恒会算** `measured_now`，所以 `measurable_drift` 每次都真的跑
    （落盘闸门那一趟才不排它——那时两边必然相等）。
    """
    store = ArtifactStore(ctx)
    judged = REL if store.exists(REL) else _INVALID
    if not store.exists(judged):
        raise UpstreamMissing(
            f'{REL} 与 {_INVALID} 都不在盘上——先跑 stage3.style')

    main, hook, gg = _upstreams(store)
    _artifact, data = store.read_own(StyleGrounding, judged)
    outcomes = judge_with(data, main, hook, gg, measured_now=measure_all(main, hook))
    prov = data.get('_provenance') or {}
    return build(
        sid=ctx.sid, node=ctx.node, freshness=store.freshness(judged),
        outcomes=outcomes, accounting=_accounting_of(prov),
        # `.INVALID` 在场是**上一次跑被闸门拦下来的痕迹**。留着不说等于静默：
        # 盘上那份正式稿可能是**更早**的一版，而人会以为它就是最近这次跑出来的。
        invalid=store.exists(_INVALID),
        revalidated=prov.get('revalidated'), origin=prov.get('origin'),
    )


def _accounting_of(prov: dict) -> dict:
    """`_provenance` 里那三个记账键 → 报告的记账节。

    ⚠⚠ **这是在还 `stage3.md` §五 的那笔账**：实跑三本 `--check-only`，报告里
    `attempts` / `revalidated` / `gates_skipped` **一行都没有**，而它们记的恰好是
    最该被人看见的三件事。`gates_skipped` 尤其要紧——它是判据 36 那整套设计的**唯一痕迹**，
    而「闸门没跑成」与「闸门跑过没事」正是 `Q-2` 要求必须能区分的两件事。
    ⚠ **只放产物里真有的键**：键不在 ⇒ 报告打 `[????] 未记录`，那与「记了 0 次」不是一回事。
    """
    return {k: prov[k] for k in ('attempts', 'revalidated', 'gates_skipped') if k in prov}


# ── 付费路 ──────────────────────────────────────────────────────────────────
#
# 付费预览与执行共用以下构造口；是否允许付费由 CLI 的 `--yes` 契约统一控制。

def _redaction_terms(gg: GlobalGrounding) -> frozenset[str]:
    """抽取专名表比告警名表更激进：脱敏宁可多，不向逐章风格模型泄漏。"""
    return frozenset(
        s for char in gg.entities.characters.values()
        for s in ([char.canonical] + [a.surface for a in char.address])
        if s and len(s.strip()) >= 2)


# 模型即使只见过脱敏正文，也会从排版推断出常见内容机制；真实小说实验里它在
# ch06/ch07 自行写回了“系统”。这些替换只把内容名还原成功能类别，不改句段/排版结论。
# 最长项在前，避免“系统数值反馈”先被拆成不自然的半截短语。
_STYLE_MECHANISM_ABSTRACTIONS = (
    ('系统数值反馈', '量化反馈'),
    ('系统数值', '量化反馈'),
    ('系统弹窗', '非人称提示框'),
    ('系统提示', '非人称提示'),
    ('任务面板', '结构化任务提示界面'),
    ('穿越身份', '身份错位信息'),
    ('真实身世', '隐藏身份信息'),
    ('隐藏能力', '未揭示能力信息'),
    ('弹幕', '群体屏幕化评论'),
    ('系统', '非人称提示机制'),
)


def _abstract_style_content(response: ChapterStyleResponse | StyleResponse,
                            surfaces: frozenset[str], *, title: str
                            ) -> tuple[ChapterStyleResponse | StyleResponse, int]:
    """把模型风格响应里的作品内容名确定性抽象掉，并返回实际替换次数。

    输入正文脱敏只能阻止“看见”；不能阻止模型根据方括号、量化反馈等形态猜出一个
    常见机制名。这里的替换面严格闭合在 Stage 2 专名、书名和固定功能机制词上；不会
    改写任意同义句，也不会让违规响应绕过其他风格判据。
    """
    data = response.model_dump(mode='python')
    count = 0

    # 整个书名先替换，避免标题里的“系统”等短词把标题切成半截。
    replacement_map: dict[str, str] = {}
    if title and len(title.strip()) >= 2:
        replacement_map[title.strip()] = '〔作品标题〕'
    mechanism_words = tuple(source for source, _target in _STYLE_MECHANISM_ABSTRACTIONS)
    for surface in sorted({s.strip() for s in surfaces if s and len(s.strip()) >= 2},
                          key=lambda value: (-len(value), value)):
        if surface == title.strip():
            continue
        target = next((category for marker, category in _STYLE_MECHANISM_ABSTRACTIONS
                       if marker in surface), '〔作品专名〕')
        replacement_map[surface] = target
    # 固定功能机制映射比同字面的笼统“作品专名”更有信息，且长短语必须先于短词。
    replacement_map.update(_STYLE_MECHANISM_ABSTRACTIONS)
    replacements = sorted(replacement_map.items(),
                          key=lambda item: (-len(item[0]), item[0]))

    def visit(value):
        nonlocal count
        if isinstance(value, str):
            got = value
            for source, target in replacements:
                hits = got.count(source)
                if hits:
                    got = got.replace(source, target)
                    count += hits
            return got
        if isinstance(value, list):
            return [visit(item) for item in value]
        if isinstance(value, dict):
            return {key: visit(item) for key, item in value.items()}
        return value

    # 保留具体响应类型，聚合响应的 hook_style 也在同一闭合扫描面内。
    cleaned = type(response).model_validate(visit(data))
    leftovers = sorted({surface for surface in surfaces
                        if surface and surface in cleaned.model_dump_json()})
    mechanism_leftovers = sorted({source for source in mechanism_words
                                  if source in cleaned.model_dump_json()})
    if leftovers or mechanism_leftovers:
        raise TerminalState(
            f'逐章风格确定性抽象后仍残留作品内容名：{(leftovers + mechanism_leftovers)[:8]}')
    return cleaned, count


def build_prompt(ctx: RunContext) -> tuple[str, str, Measurable]:
    """`--dry-run`：把这次**要发出去的** `(system, user)` 原样交出来。**0 成本。**

    ⚠ 它与真跑共用同一个构造入口——展示与执行必须共用同一份构造函数（`R-11.8`），
    否则「预览里看到的」与「真发出去的」是两件事，而那种分家没有任何东西会报错。
    """
    chapter_system, users, meas, _hook_text, _surfaces = build_chapter_prompts(ctx)
    # dry-run 摊开第一个真实章请求；真跑逐章也调同一个构造口。
    return chapter_system, users[0][1], meas


def build_chapter_prompts(ctx: RunContext
                          ) -> tuple[str, list[tuple[int, str]], Measurable, str,
                                     frozenset[str]]:
    """生产输入面：一个请求只含一个源章。"""
    store = ArtifactStore(ctx)
    main, hook, gg = _upstreams(store)
    meas = measure_all(main, hook)
    terms = _redaction_terms(gg) | frozenset({ctx.title})
    system = prompts.chapter_style_system()
    users = [
        (section.chapter_ordinal,
         prompts.build_chapter_user(
             meas, chapter_ordinal=section.chapter_ordinal,
             chapter_text=prompts.redact_style_entities(section.text, terms)))
        for section in main.root
    ]
    output_surfaces = names_of(gg)
    return (system, users, meas,
            prompts.redact_style_entities((hook.text or '').strip(), terms),
            output_surfaces)


def request_chapter_styles(
    ctx: RunContext,
    client: LlmClient,
) -> tuple[str, str, Measurable, list[ChapterStyle], StyleResponse, list[StyleAttempt]]:
    """逐章抽取后再聚合。章请求可并发，但返回与落盘始终按源章序。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from novel.config import MAX_CONCURRENCY

    chapter_system, users, measurable, hook_text, surfaces = build_chapter_prompts(ctx)
    results: dict[int, tuple[str, ChapterStyleResponse, ChapterStyleResponse, int]] = {}
    workers = min(
        MAX_CONCURRENCY,
        max(1, int(getattr(client, 'concurrency', MAX_CONCURRENCY))),
        len(users),
    )
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {
            pool.submit(
                client.ask_json, chapter_system, user,
                model_cls=ChapterStyleResponse,
                purpose=f'{ctx.node}.chapter.{ordinal}',
            ): (ordinal, user)
            for ordinal, user in users
        }
        for future in as_completed(pending):
            ordinal, user = pending[future]
            raw = future.result()
            cleaned, abstractions = _abstract_style_content(
                raw, surfaces, title=ctx.title)
            results[ordinal] = (user, raw, cleaned, abstractions)

    chapter_styles = [
        ChapterStyle(source_ordinal=ordinal, voice=response.voice,
                     surface_habits=response.surface_habits)
        for ordinal, (_user, _raw, response, _count) in sorted(results.items())
    ]
    aggregate_user = prompts.build_aggregate_user(
        measurable,
        chapter_styles=[style.model_dump() for style in chapter_styles],
        hook_text=hook_text,
    )
    aggregate_system = prompts.chapter_aggregate_system()
    aggregate_raw = client.ask_json(
        aggregate_system, aggregate_user, model_cls=StyleResponse,
        purpose=f'{ctx.node}.aggregate')
    aggregate, aggregate_abstractions = _abstract_style_content(
        aggregate_raw, surfaces, title=ctx.title)
    attempts = [
        StyleAttempt(
            kind='chapter_local', chapter_ordinal=ordinal,
            user_prompt=fingerprint(user),
            output=fingerprint(raw.model_dump_json()),
            cache_policy=ctx.cache.name,
            abstractions=abstractions,
        )
        for ordinal, (user, raw, _response, abstractions) in sorted(results.items())
    ] + [StyleAttempt(
        kind='chapter_aggregate',
        user_prompt=fingerprint(aggregate_user),
        output=fingerprint(aggregate_raw.model_dump_json()),
        cache_policy=ctx.cache.name,
        abstractions=aggregate_abstractions,
    )]
    return (chapter_system, aggregate_system, measurable, chapter_styles,
            aggregate, attempts)


_QUOTE_PAIRS = (('「', '」'), ('“', '”'), ('‘', '’'), ('『', '』'), ('"', '"'))


def _surface_habits(data: dict) -> list[str]:
    """全局与逐章块中的习惯清单，保持盘上遍历顺序。"""
    return [habit for _path, habits in checks._surface_habit_groups(data)
            for habit in habits if isinstance(habit, str)]


def _replace_habit_literals(data: dict, replacements: dict[str, str]) -> dict:
    """只替换成对引号及其内部字面；引号外字节、条目数与顺序不交给模型。"""
    got = copy.deepcopy(data)

    def visit(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key == 'surface_habits' and isinstance(value, list):
                    for index, habit in enumerate(value):
                        if not isinstance(habit, str):
                            continue
                        replaced = habit
                        for literal, category in sorted(
                                replacements.items(), key=lambda item: (-len(item[0]), item[0])):
                            for opening, closing in _QUOTE_PAIRS:
                                replaced = replaced.replace(
                                    f'{opening}{literal}{closing}', category)
                        value[index] = replaced
                else:
                    visit(value)
        elif isinstance(obj, list):
            for item in obj:
                visit(item)

    visit(got)
    return got


def _outcome_evidence(outcomes, *, exclude: frozenset[str] = frozenset()) -> dict[str, set[str]]:
    return {
        outcome.rule: {finding.evidence_key for finding in outcome.findings}
        for outcome in outcomes if outcome.rule not in exclude and outcome.findings
    }


def _habit_repair_candidate(
    ctx: RunContext,
) -> tuple[ArtifactStore, StyleGrounding, dict, list, list[str], Measurable]:
    """证明 `.INVALID` 的全部阻断问题都能由这一条窄修复触及。"""
    store = ArtifactStore(ctx)
    if not store.exists(_INVALID):
        raise UpstreamMissing(f'{_INVALID} 不在盘上——没有待定向返工的问题稿')
    main, hook, _gg = _upstreams(store)
    artifact, data = store.read_own(
        StyleGrounding, _INVALID, require_current=True)
    measured_now = measure_all(main, hook)
    outcomes = _judge(store, data, measured_now=measured_now)
    hard = _hard_rules(outcomes)
    if hard != {'habit_quote'}:
        raise TerminalState(
            'stage3 定向返工只在唯一阻断问题为 habit_quote 时准入；'
            f'当前阻断集是 {sorted(hard) or "（空）"}。'
            '空集不该花钱，其他问题必须走各自的人审/修复路径')
    literals = checks.habit_literals(data)
    if not literals:
        raise TerminalState('habit_quote 命中却提取不到字面 evidence；拒绝无目标付费返工')
    return store, artifact, data, outcomes, literals, measured_now


def build_habit_repair_prompt(ctx: RunContext) -> tuple[str, str, list[str]]:
    """显式返工预览；与真实第二轮共用同一份候选准入与 user builder。"""
    _store, _artifact, data, _outcomes, literals, _measured = _habit_repair_candidate(ctx)
    return (prompts.habit_repair_system(),
            prompts.build_habit_repair_user(_surface_habits(data), literals), literals)


def _replacement_contract_error(replacements: dict[str, str],
                                expected: set[str]) -> str | None:
    """窄修复响应的纯边界；真实模型返回后、改任何产物字节前执行。"""
    if set(replacements) != expected:
        return (f'replacements 键集合必须与 evidence 完全相同：'
                f'缺 {sorted(expected - set(replacements))}，'
                f'多 {sorted(set(replacements) - expected)}')
    quote_chars = frozenset('「」“”‘’『』"')
    bad_values = [literal for literal, value in replacements.items()
                  if any(char in value for char in quote_chars)
                  or '\n' in value or len(value) > 40 or norm(literal) in norm(value)]
    if bad_values:
        return f'{bad_values} 的类别替换含引号/换行、过长或仍复述原字面'
    return None


def run_repair_surface_habits(
    ctx: RunContext,
    client: LlmClient,
) -> tuple[StyleGrounding, CheckReport]:
    """第二轮只生成类别替换；程序原位应用并用完整判据做回归护栏。"""
    from novel.llm.cache import CachePolicy

    store, previous, data, before_outcomes, literals, measured_now = \
        _habit_repair_candidate(ctx)
    habits = _surface_habits(data)
    system = prompts.habit_repair_system()
    user = prompts.build_habit_repair_user(habits, literals)
    expected = set(literals)
    before_evidence = _outcome_evidence(
        before_outcomes, exclude=frozenset({'habit_quote'}))

    def validate(response: HabitRepairResponse, _wire_response) -> str | None:
        replacements = response.replacements
        if why := _replacement_contract_error(replacements, expected):
            return why
        candidate = _replace_habit_literals(data, replacements)
        if left := checks.habit_literals(candidate):
            return f'确定性应用后仍有具体词例：{left[:5]}'
        after_outcomes = _judge(store, candidate, measured_now=measured_now)
        if hard := _hard_rules(after_outcomes):
            return f'替换引入或保留阻断问题：{sorted(hard)}'
        after_evidence = _outcome_evidence(
            after_outcomes, exclude=frozenset({'habit_quote'}))
        added = {rule: sorted(keys - before_evidence.get(rule, set()))
                 for rule, keys in after_evidence.items()
                 if keys - before_evidence.get(rule, set())}
        if added:
            return f'替换引入新的非目标 finding：{added}'
        return None

    # 定向重采样不能读回首轮同问题缓存，但仍写入新缓存与账本。
    ctx.cache = CachePolicy.refresh()
    response = client.ask_json(
        system, user, model_cls=HabitRepairResponse,
        purpose=f'{ctx.node}.surface_habits_targeted_repair',
        max_tokens=16_000, validator=validate)
    repaired_data = _replace_habit_literals(data, response.replacements)
    after_outcomes = _judge(store, repaired_data, measured_now=measured_now)

    attempt = StyleAttempt(
        kind='surface_habits_targeted_repair',
        input=fingerprint(json.dumps(habits, ensure_ascii=False)),
        evidence=fingerprint(json.dumps(literals, ensure_ascii=False)),
        evidence_count=len(literals),
        user_prompt=fingerprint(user),
        output=fingerprint(response.model_dump_json()),
        cache_policy=ctx.cache.name,
        model=client.model,
        code=ctx.code,
    )
    fresh = ctx.llm_provenance(
        StyleProvenance, prompts={'HABIT_LITERAL_REPAIR_SYS': system}, model=client.model,
        attempts=[attempt])
    old = previous.provenance
    merged = old.model_copy(update={
        'code': fresh.code,
        'inputs': fresh.inputs,
        'prompts': {**old.prompts, **fresh.prompts},
        # 顶层 model 仍说明首轮主产物；第二轮模型逐次记在 attempt 上。
        'model': old.model,
        'usage': combine_usage(old.usage, fresh.usage),
        'attempts': [*old.attempts, attempt],
        'gates_skipped': None,
        'revalidated': None,
        'origin': None,
        'input_basis': None,
        'partial_rewrite': None,
    })
    repaired_data['_provenance'] = merged.model_dump(by_alias=True, mode='json')
    artifact = StyleGrounding.model_validate(repaired_data)

    # 新正式稿先落稳，再收起第一轮问题稿；中途崩溃时至少总有一份可恢复稿在活路径。
    if store.exists(REL):
        store.archive_round(REL)
    store.write(REL, artifact)
    if store.exists(_INVALID):
        store.archive_round(_INVALID)
    prov = artifact.model_dump(by_alias=True).get('_provenance') or {}
    report = build(
        sid=ctx.sid, node=ctx.node, freshness=store.freshness(REL),
        outcomes=after_outcomes, accounting=_accounting_of(prov), invalid=False,
        revalidated=prov.get('revalidated'), origin=prov.get('origin'))
    return artifact, report


def _write_initial_result(
    ctx: RunContext,
    client: LlmClient,
    store: ArtifactStore,
    artifact: StyleGrounding,
    outcomes,
    *,
    label: str,
) -> StyleGrounding:
    """首轮产物先过闸；仅当唯一阻断项有窄 LLM 路由时自动进入第二轮。"""
    hard = _hard_rules(outcomes)
    if hard:
        if store.exists(_INVALID):
            store.archive_round(_INVALID)
        path = store.write_invalid(REL, artifact, gates_skipped=[])
        if hard == {'habit_quote'}:
            repaired, _report = run_repair_surface_habits(ctx, client)
            return repaired
        raise GateBlocked(
            f'{label}落盘闸门拦下：{sorted(hard)}。问题稿在 {path}；'
            '报告的 remediation/fix 会区分 LLM 可修与必须人审的问题',
            invalid_path=str(path))
    if store.exists(REL):
        store.archive_round(REL)
    store.write(REL, artifact)
    if store.exists(_INVALID):
        store.archive_round(_INVALID)
    return artifact


def run_style(ctx: RunContext, client: LlmClient) -> StyleGrounding:
    """逐章脱敏抽取并聚合；这是 Stage 3 唯一生产实现。"""
    store = ArtifactStore(ctx)
    (chapter_system, aggregate_system, meas, chapter_styles,
     aggregate, attempts) = request_chapter_styles(ctx, client)
    artifact = StyleGrounding(
        voice=aggregate.voice,
        surface_habits=aggregate.surface_habits,
        hook_style=aggregate.hook_style,
        measurable=meas,
        chapter_styles=chapter_styles,
        **{'_provenance': ctx.llm_provenance(
            StyleProvenance,
            prompts={'STYLE_CHAPTER_SYS': chapter_system,
                     'STYLE_AGGREGATE_SYS': aggregate_system},
            model=client.model,
            attempts=attempts,
        )},
    )
    outcomes = _judge(store, artifact.model_dump(by_alias=True), measured_now=None)
    return _write_initial_result(
        ctx, client, store, artifact, outcomes, label='stage3 逐章风格')


def judge_with(data: dict, main: MainText, hook: HookCopy, gg: GlobalGrounding, *,
               measured_now: Measurable | None):
    """判据的**唯一调度点**（不摸盘的那一半）：本层四条路与测试台都走它。

    ⚠⚠ **它必须是唯一的一份，而「唯一」这件事已经破过一次。**
    `_judge` 的 docstring 原本就写着「唯一调度点」，而 `run_check_only` 与
    `run_revalidate` 各自又把这几个实参**拼了一遍**——三份拼装同时存在。
    随后 `expected_chapters` 被加进 `checks.check`：三份生产拼装跟着改了，
    `tests/test_stage3.py` 里的第四份没有 ⇒ `required` 在那边**恒报**
    「校验期拿不到预期源章节集合」，三本书 3 条红，而真跑的 `--check-only` 是绿的。
    **那条红说的不是产物有问题，是测试台漂了。**
    ⇒ 参数从盘上读的那一半留给 `_judge`，拼装这一半收成本函数，
    测试台直接调它——形参一变，四个调用点一起报错。
    """
    names = names_of(gg)
    return checks.check(
        data, corpus=corpus_of(main, hook), names=names,
        spoiler_names=spoiler_names_of(gg, names) if names else frozenset(),
        measured_now=measured_now,
        expected_chapters=tuple(section.chapter_ordinal for section in main.root))


def _judge(store: ArtifactStore, data: dict, *, measured_now: Measurable | None):
    """`judge_with` 的摸盘版：付费路的落盘闸门走它。"""
    main, hook, gg = _upstreams(store)
    return judge_with(data, main, hook, gg, measured_now=measured_now)


def remeasured(ctx: RunContext) -> tuple[StyleGrounding, list[tuple[str, object, object]]]:
    """照盘上 stage1 重算 `measurable`，返回 `(新产物, 逐项差异)`。**纯算，不写盘。**

    ⚠ 拆成「算」与「写」两半，是为了让 `--remeasure` 的**对账**可以零成本跑
    （盘上三本 × 23 项逐值比对），而不必先落一次盘。
    """
    store = ArtifactStore(ctx)
    main, hook, _gg = _upstreams(store)
    artifact, _data = store.read_own(StyleGrounding, REL)
    now = measure_all(main, hook)
    from novel.core.measure import drift
    diff = [d for scope in ('body', 'hook')
            for d in drift(getattr(artifact.measurable, scope), getattr(now, scope))]
    return artifact.model_copy(update={'measurable': now}), diff


# ── 另外两条 0 成本路（骨架 D 补齐） ────────────────────────────────────────

def run_measure_only(ctx: RunContext) -> Measurable:
    """`--measure-only`：只跑确定性实测并报出来，**不写盘**。

    ⚠ 它与 `--remeasure` **是两件事**（`FREE_ENTRIES` 自己写着这句）：
    本条连产物都不必存在——它量的是 stage1，不是 stage3。
    ⇒ 它是「改了实测口径之后，先看看新数是多少」那一步，
    而 `--remeasure` 是「把新数写回去」。混成一个 flag，人就没法先看后写。
    """
    store = ArtifactStore(ctx)
    main, hook, _gg = _upstreams(store)
    return measure_all(main, hook)


def _hard_rules(outcomes) -> set[str]:
    """这一趟命中了哪几条**阻断级**判据。**按 rule key 记，不按 detail 字符串记。**

    ⚠ 原型这里踩过一个具体的坑，理由一起搬：它比的是**格式化后的字符串**，
    而 `--remeasure` 这一趟做的事恰恰是改 `measurable` 里的那些数——
    于是同一条旧问题会被认成「本次新引入」并入终态，
    **判据 15 那条「旧违约照报但不挡路」的保护会在自己的实现细节上失效**。
    """
    return {o.rule for o in outcomes
            if any(f.sev == 'hard' for f in (o.findings or []))}


def run_remeasure(ctx: RunContext) -> tuple[StyleGrounding, list[tuple[str, object, object]],
                                            CheckReport]:
    """`--remeasure`：把重算的 `measurable` 写回已有产物，**其余字段不动**。0 成本。

    【为什么必须有这条】`measurable` 全由代码算出，改了口径 / 修了 bug 却只能靠
    重跑 LLM 才能落盘，等于**为自己的 bug 付钱**（`Q-3`）。

    【来源账怎么记】走 `ctx.rewrite_provenance(prev, by='--remeasure')`：
    继承 `prompts`/`model`，换掉 `inputs`，标 `partial_rewrite`（`_common.md` §5.1b）。
    沿用旧账会让「这份 `measurable` 照哪份原文算的」永远说不出口——
    **而那正是本条存在的理由**；整份覆盖又会把 `prompts` 说成本次产的。

    ⚠⚠ **先校验后落盘，而闸门只拦「本次新引入」的**（`REVALIDATE_ONLY_NEW_ERRORS`）：
    旧违约照报但不挡路，否则一条不相干的旧问题会把重算这条路**永久堵死**。
    ⚠ 这一点上本实现比原型严一格：原型是**先写回、再重判**，
    于是新引入错误的那一趟已经把产物盖掉了。这里判在写之前（`Q-5`）。
    """
    store = ArtifactStore(ctx)
    if not store.exists(REL):
        raise UpstreamMissing(f'{REL} 不在盘上——先跑一次 stage3.style')

    _artifact, before = store.read_own(StyleGrounding, REL)
    was = _hard_rules(_judge(store, before, measured_now=None))

    updated, diff = remeasured(ctx)
    data = updated.model_dump(by_alias=True)
    # `measured_now=None`：这一趟正要把 `measurable` 覆盖掉，漂移那一条无从谈起。
    outcomes = _judge(store, data, measured_now=None)
    fresh_errors = sorted(_hard_rules(outcomes) - was)
    if fresh_errors:
        raise GateBlocked(
            f'stage3 --remeasure **本次新引入**了 {len(fresh_errors)} 条阻断级错误：'
            f'{fresh_errors}。**没有覆盖已有产物**——'
            f'多半是描述文本里抄了旧数字，需要重跑 stage3 对齐。'
            f'（重算前就有的 {len(was)} 条与本次无关，它们照报但不挡路）',
            invalid_path=str(store.path(REL)))

    written = updated.model_copy(update={
        'provenance': ctx.rewrite_provenance(updated.provenance, by='--remeasure')})
    store.write(REL, written)

    prov = written.model_dump(by_alias=True).get('_provenance') or {}
    report = build(
        sid=ctx.sid, node=ctx.node, freshness=store.freshness(REL),
        outcomes=outcomes, accounting=_accounting_of(prov),
        invalid=store.exists(_INVALID),
        revalidated=prov.get('revalidated'), origin=prov.get('origin'))
    return written, diff, report


def run_revalidate(ctx: RunContext) -> tuple[bool, CheckReport]:
    """`--revalidate`：按**当前**判据重判被闸门拦下的 `.INVALID` 稿，合格即提升。0 成本。

    【为什么必须有这条】落盘闸门把问题稿存进 `.INVALID` 之后，**纯人工修订需要一条
    0 成本提升路**。没有它，修一条判错的判据要为每一章再付一次费，
    而重掷回来的是另一份稿，**修没修好无从对照**。

    ⚠ **合格的判准是「一条 hard 都没有」，不是「没有新引入的」。**
    与 `--remeasure` 相反，这里不适用 `REVALIDATE_ONLY_NEW_ERRORS`：
    提升的语义就是「它现在过得了这道闸」——带着一条旧的阻断错误被提升上去，
    等于把闸门本身作废掉。⚠ 那个常量的名字里有 `revalidate` 而消费方在
    `--remeasure` 这一侧，是一处**命名与落点分家**，登记在这里（`Q-3`）。

    ⚠ 提升走 `store.promote_invalid`——它的签名里没有 `prompts`/`model`/`inputs`，
    **没有参数就没有可以传错的东西**（`_common.md` §5.2 记着全链四处实测伪造/丢失）。
    """
    store = ArtifactStore(ctx)
    if not store.exists(_INVALID):
        raise UpstreamMissing(
            f'{_INVALID} 不在盘上——没有被闸门拦下的稿可提升。'
            f'（要复检正式产物用 `--check-only`，那是另一件事）')

    main, hook, gg = _upstreams(store)
    _cand, data = store.read_own(StyleGrounding, _INVALID)
    # 恒算漂移：这一份是当初付费那次产的，它的 `measurable` 可能已经跟不上 stage1 了。
    outcomes = judge_with(data, main, hook, gg, measured_now=measure_all(main, hook))

    prov = data.get('_provenance') or {}
    promoted = not _hard_rules(outcomes)
    if promoted:
        store.promote_invalid(REL, StyleGrounding)
        prov = (store.read_own(StyleGrounding, REL)[1].get('_provenance') or {})

    report = build(
        sid=ctx.sid, node=ctx.node,
        freshness=store.freshness(REL if promoted else _INVALID),
        outcomes=outcomes, accounting=_accounting_of(prov),
        # 提升成功之后 `.INVALID` 已经归档进 `_rounds/`，那一格才回到 False。
        invalid=store.exists(_INVALID),
        revalidated=prov.get('revalidated'), origin=prov.get('origin'))
    return promoted, report


def run_apply_edit(ctx: RunContext, edit_path: str = ''
                   ) -> tuple[StyleGrounding, CheckReport]:
    """`--apply-edit`：吃人手写的画像补丁，走与生成稿**同一道**判定与闸门。0 成本。

    三条纪律（BE-02 的完成标准，与 stage5/stage7 的人工路同款）：
      · **同一判定落点**（`_judge`）——没有免检写口；HARD ⇒ `.INVALID` 不覆盖正式稿；
      · **轮次归档**——旧轮进 `_rounds/`，覆盖前有处可回；
      · **来源账不伪造**——`rewrite_provenance(prev, by='--apply-edit')`：
        继承 `prompts`/`model` 一个字不动，换 `inputs`，标 `partial_rewrite`
        （⚠ 不走 `to_human_edit()`：它返回裸 `FreeProvenance`，会被本层收窄成
        `StyleProvenance` 的载体当场拒收——BE-05 登记的实况）。

    ⚠ **`measurable` 一律照盘上 stage1 重算**（复用 `measure_all`）：人只许改
    LLM 写的三组散文；实测数字是代码的账，跟着旧稿走会让 `measurable_drift` 说不清。
    ⚠ **无前稿（正式或 `.INVALID` 皆无）⇒ 显式终态**：这是修订路，不是生成路。
    ⚠ **不自动触发付费自救**：`habit_quote` 命中同样落 `.INVALID`——人工路花钱
    需要人自己发起（`--repair-surface-habits`），不由一次 0 成本编辑代扣。
    """
    store = ArtifactStore(ctx)
    rel = paths.edit_rel(ctx.novel_dir, edit_path, EDIT)
    if not store.exists(rel):
        raise TerminalState(
            f'{rel} 不在盘上。⚠ `--apply-edit` 吃的是**人写的决策集**，'
            f'把它放到这本书的 `stage3/` 下（缺省文件名 `style_edit.json`）'
            f'——它的形状是契约的一部分（`StyleEdit`）')
    edit = store.read(StyleEdit, rel)

    prev_rel = REL if store.exists(REL) else (_INVALID if store.exists(_INVALID) else None)
    if prev_rel is None:
        raise TerminalState(
            '盘上既无正式画像也无 `.INVALID` 问题稿——`--apply-edit` 是修订路，'
            '不是生成路；先跑一次 stage3.style（付费）')
    prev, _raw = store.read_own(StyleGrounding, prev_rel)

    main, hook, _gg = _upstreams(store)
    now = measure_all(main, hook)
    patched: dict = {'measurable': now,
                     'provenance': ctx.rewrite_provenance(prev.provenance,
                                                          by='--apply-edit')}
    if edit.voice is not None:
        patched['voice'] = edit.voice
    if edit.surface_habits is not None:
        patched['surface_habits'] = list(edit.surface_habits)
    if edit.hook_style is not None:
        patched['hook_style'] = edit.hook_style
    artifact = prev.model_copy(update=patched)

    data = artifact.model_dump(by_alias=True)
    # `measured_now=now`：刚重算过，漂移应恒为零——非零说明重算链本身坏了，该红。
    outcomes = _judge(store, data, measured_now=now)
    hard = _hard_rules(outcomes)
    if hard:
        if store.exists(_INVALID):
            store.archive_round(_INVALID)
        path = store.write_invalid(REL, artifact, gates_skipped=[])
        raise GateBlocked(
            f'人工画像补丁被落盘闸门拦下：{sorted(hard)}。问题稿在 {path}，'
            f'**正式产物没有被覆盖**；改完编辑文件重跑 `--apply-edit`，'
            f'或直接改 `.INVALID` 后 `--revalidate` 提升（都是 0 成本）',
            invalid_path=str(path))

    if store.exists(REL):
        store.archive_round(REL)
    store.write(REL, artifact)
    if store.exists(_INVALID):
        store.archive_round(_INVALID)

    prov = artifact.model_dump(by_alias=True).get('_provenance') or {}
    report = build(
        sid=ctx.sid, node=ctx.node, freshness=store.freshness(REL),
        outcomes=outcomes, accounting=_accounting_of(prov),
        invalid=store.exists(_INVALID),
        revalidated=prov.get('revalidated'), origin=prov.get('origin'))
    return artifact, report
