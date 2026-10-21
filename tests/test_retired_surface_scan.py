#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C5：退役 Agent CLI 表面的生产静态扫描。

生产 ``novel/`` 对下列符号的命中必须为 0；唯一允许按 ``kind == 'agent'``
做格式判别的代码是 ``novel/orchestrator/retired_formats.py``。
docs、历史 fixtures、ledger 词表与测试不在本闸范围内。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NOVEL = ROOT / 'novel'

#: 生产代码唯一允许出现 ``kind == 'agent'`` 字面判别的模块。
_ALLOWLIST = frozenset({
    'novel/orchestrator/retired_formats.py',
})

#: (标签, 正则)。对标识符类模式在去掉注释/字符串后再匹配，避免误伤
#: stage5 legacyNotice 等无关「legacy」文案，以及历史注释里的退役符号名。
_PATTERNS: tuple[tuple[str, str, bool], ...] = (
    ('novel.agents import', r'(?:from|import)\s+novel\.agents\b', False),
    ('build_node_agent', r'\bbuild_node_agent\b', True),
    ('AgentProfile', r'\bAgentProfile\b', True),
    ('AgentExecutorBackend', r'\bAgentExecutorBackend\b', True),
    ('agent_mode', r'\bagent_mode\b', True),
    ('_uses_agent_sessions', r'\b_uses_agent_sessions\b', True),
    ('SessionAgentClient', r'\bSessionAgentClient\b', True),
    ('unit_session_pool', r'\bunit_session_pool\b', True),
    ('--executor-driver', r'--executor-driver\b', False),
    ('--executor-mode', r'--executor-mode\b', False),
    # 生产分支判别：用 executor_profile.kind / ['kind'] 选择 agent 臂。
    # LegacyLlmProfile 投影字段名本身仍可存在，不在本闸。
    ('executor_profile agent branch',
     r"executor_profile(?:\.kind|\[['\"]kind['\"]\])\s*==\s*['\"]agent['\"]",
     True),
# kind == agent：对全文件（含注释）扫描，避免注释里残留字面量。
    ('kind == agent', r"kind\s*==\s*['\"]agent['\"]", False),
    # CS-S 1b（2026-10-07）：Stage2 死字段收口的「不得回来」面——
    # KnowledgeSpec／Disclosure／reader_knows／concealed_unrevealed／
    # SlimConcealment 已从契约删除；标识符类模式去噪匹配（注释里的历史
    # 记述不拦，新代码引用即红）。小写 disclosure／concealed 不在此闸——
    # 楔子隐藏身份与交付防剧透是同名不同域的现役字段。
    ('KnowledgeSpec', r'\bKnowledgeSpec\b', True),
    ('Disclosure', r'\bDisclosure\b', True),
    ('reader_knows', r'\breader_knows\b', True),
    ('concealed_unrevealed', r'\bconcealed_unrevealed\b', True),
    ('SlimConcealment', r'\bSlimConcealment\b', True),
)


def _strip_python_noise(source: str) -> str:
    """去掉 # 注释与普通/三引号字符串，保留换行以便报告行号近似。"""
    out: list[str] = []
    i = 0
    n = len(source)
    while i < n:
        ch = source[i]
        if ch == '#':
            while i < n and source[i] != '\n':
                i += 1
            continue
        if source.startswith('"""', i) or source.startswith("'''", i):
            quote = source[i:i + 3]
            j = source.find(quote, i + 3)
            if j < 0:
                break
            # 保留换行数，方便对照
            out.append('\n' * source[i:j].count('\n'))
            i = j + 3
            continue
        if ch in ('"', "'"):
            quote = ch
            i += 1
            while i < n:
                if source[i] == '\\':
                    i += 2
                    continue
                if source[i] == quote:
                    i += 1
                    break
                if source[i] == '\n':
                    out.append('\n')
                i += 1
            continue
        out.append(ch)
        i += 1
    return ''.join(out)


def _production_files() -> list[Path]:
    return sorted(
        path for path in NOVEL.rglob('*.py')
        if '__pycache__' not in path.parts
    )


def _hits(label: str, pattern: str, *, strip_noise: bool) -> list[str]:
    rx = re.compile(pattern)
    found: list[str] = []
    for path in _production_files():
        rel = path.relative_to(ROOT).as_posix()
        if rel in _ALLOWLIST:
            continue
        raw = path.read_text(encoding='utf-8')
        text = _strip_python_noise(raw) if strip_noise else raw
        for index, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                found.append(f'{rel}:{index}:{line.strip()[:160]}')
    return found


@pytest.mark.parametrize(
    'label,pattern,strip_noise',
    _PATTERNS,
    ids=[row[0] for row in _PATTERNS],
)
def test_retired_surface_absent_from_production(
        label: str, pattern: str, strip_noise: bool) -> None:
    found = _hits(label, pattern, strip_noise=strip_noise)
    assert found == [], (
        f'C5 生产仍残留退役表面 {label!r}（允许名单仅 retired_formats）：\n'
        + '\n'.join(found)
    )


def test_retired_formats_is_sole_kind_agent_discriminator() -> None:
    """``kind == 'agent'`` 字面只允许出现在 retired_formats（含其模块头说明）。"""
    rx = re.compile(r"kind\s*==\s*['\"]agent['\"]")
    elsewhere: list[str] = []
    for path in _production_files():
        rel = path.relative_to(ROOT).as_posix()
        if rel in _ALLOWLIST:
            continue
        for index, line in enumerate(
                path.read_text(encoding='utf-8').splitlines(), 1):
            if rx.search(line):
                elsewhere.append(f'{rel}:{index}:{line.strip()}')
    assert elsewhere == [], (
        'kind == \"agent\" 只能留在 retired_formats.py：\n'
        + '\n'.join(elsewhere)
    )
    allowed = (NOVEL / 'orchestrator' / 'retired_formats.py').read_text(
        encoding='utf-8')
    assert rx.search(allowed), 'retired_formats 自身必须保留 kind==agent 判别'


# ═══════════════════════════════════════════════════════════════════════════
# STR-06 关闭批（2026-10-03）：退役面不得回来（迁移自随台账删除的
# tests/test_str06_retirement.py 与 tests/test_str06_f7e_retirement.py 的
# 方向②守卫；常量自持，不依赖 contracts/schema/retirement.py 台账——
# 台账随关闭批删除，守卫的职责从「对账」变为「单向不得回来」）。
# ═══════════════════════════════════════════════════════════════════════════

#: 旧链（F8c 删除）的 stage 引擎模块名：目录被新链 per-stage 包原地重建，
#: 模块名才说了算——旧链引擎回来任何一个都当场红。
OLD_STAGE_MODULES = (
    'novel/stages/common.py', 'novel/stages/prose_contract.py',
    'novel/stages/stage2/analyze', 'novel/stages/stage2/blueprint',
    'novel/stages/stage2/characters', 'novel/stages/stage2/knowledge',
    'novel/stages/stage2/shared', 'novel/stages/stage2/extract.py',
    'novel/stages/stage2/checks.py',
    'novel/stages/stage4/plot_design.py', 'novel/stages/stage4/plan.py',
    'novel/stages/stage4/beats.py', 'novel/stages/stage4/measure.py',
    'novel/stages/stage4/reading.py', 'novel/stages/stage4/actors.py',
    'novel/stages/stage4/plot_binding.py', 'novel/stages/stage4/plot_checks.py',
    'novel/stages/stage4/plot_context.py', 'novel/stages/stage4/plot_materials.py',
    'novel/stages/stage4/plot_measure.py', 'novel/stages/stage4/plot_placements.py',
    'novel/stages/stage4/plot_refs.py', 'novel/stages/stage4/compile_ir.py',
    'novel/stages/stage4/design_rebuild.py', 'novel/stages/stage4/design_recovery.py',
    'novel/stages/stage4/design_evolution.py', 'novel/stages/stage4/plan_recovery.py',
    'novel/stages/stage4/phase_binding.py', 'novel/stages/stage4/phase_state.py',
    'novel/stages/stage4/design_response_recovery.py',
    'novel/stages/stage4/binding_input_contract.py',
    'novel/stages/stage4/source_view.py', 'novel/stages/stage4/prompts.py',
    'novel/stages/stage5/write.py', 'novel/stages/stage5/review.py',
    'novel/stages/stage5/measure.py', 'novel/stages/stage5/checks.py',
    'novel/stages/stage5/closure.py', 'novel/stages/stage5/plot_checks.py',
    'novel/stages/stage5/plot_prompts.py', 'novel/stages/stage5/prompts.py',
    'novel/stages/stage5/reviewer_rewrite.py',
    'novel/stages/stage5/trim_resolution.py',
    'novel/stages/stage7/rename.py', 'novel/stages/stage7/assemble.py',
    'novel/stages/stage7/checks.py', 'novel/stages/stage7/measure.py',
    'novel/stages/stage7/prompts.py', 'novel/stages/stage7/subs.py',
)

#: 当前 story-v1 per-stage 引擎及其共享纯边界（缺任何一个也红——
#: 「删除被静默扩大」与「旧面回来」是同一道闸的两半）。
NEW_STAGE_MODULES = (
    'novel/stages/stage2/__init__.py', 'novel/stages/stage2/story.py',
    'novel/stages/stage4/__init__.py', 'novel/stages/stage4/story.py',
    'novel/stages/stage5/__init__.py', 'novel/stages/stage5/story.py',
    'novel/stages/stage7/__init__.py', 'novel/stages/stage7/story.py',
    'novel/stages/chain.py', 'novel/core/source_index.py',
    'novel/core/story_prose.py',
)

#: STR-06 关闭批删除的存量解码面（模块级；回来即红）。
CLOSED_DECODE_MODULES = (
    'novel/orchestrator/reviewer_legacy.py',
    'novel/orchestrator/reviewer_action_evidence.py',
    'novel/orchestrator/reviewer_node_landed.py',
    'novel/orchestrator/review_inheritance.py',
    'novel/orchestrator/stage2_review_ledger.py',
    'novel/orchestrator/workflow_transition.py',
    'contracts/schema/retirement.py',
    'contracts/schema/stage6.py',
    'contracts/schema/stage6_prepare.py',
    'novel/quality/__init__.py',
)


def test_old_stage_engine_modules_stay_deleted_and_new_chain_stays_complete():
    """旧链引擎一个都不许回来；新链 per-stage 模块一个都不能缺。"""
    problems = [f'旧链引擎模块回来了：{m}' for m in OLD_STAGE_MODULES
                if (ROOT / m).exists()]
    problems += [f'新链模块未落位：{m}' for m in NEW_STAGE_MODULES
                 if not (ROOT / m).is_file()]
    # 当前模块面只允许公开包与 story，旧模块或兼容 shim 换名回流也会红。
    for directory in ('novel/stages/stage2', 'novel/stages/stage4',
                      'novel/stages/stage5', 'novel/stages/stage7'):
        present = {p.name for p in (ROOT / directory).glob('*.py')}
        problems += [f'{directory}/ 混进非现役模块：{name}'
                     for name in sorted(present - {'__init__.py', 'story.py'})]
    assert not problems, '\n'.join(problems)


def test_stage6_layer_stays_deleted():
    """Stage6 整层（F6d/F8b/F8c 删）不得以任何形状回来。"""
    problems = []
    for rel in ('novel/stages/stage6', 'contracts/schema/stage6.py',
                'contracts/schema/stage6_prepare.py',
                'novel/api/routes/stage6_prepare.py', 'novel/views/stage6.py',
                'novel/views/stage6_prepare.py', 'web/src/screens/stage6'):
        if (ROOT / rel).exists():
            problems.append(f'stage6 面回来了：{rel}')
    for node_key in ('stage6.prepare', 'stage6.hook', 'stage6.approve',
                     'stage6.semantic'):
        from contracts.invariants import NODES
        if node_key in NODES:
            problems.append(f'stage6 节点键回到现役 NODES：{node_key}')
    assert not problems, '\n'.join(problems)


def test_old_chain_views_and_frontend_screens_stay_deleted():
    """旧链 views 与前端旧屏目录（F7d/F8c 删）不得回来。"""
    problems = []
    for rel in ('novel/views/stage2.py', 'novel/views/stage4.py',
                'novel/views/stage5.py', 'novel/views/stage7.py',
                'novel/views/blueprint_progress.py',
                'web/src/screens/read', 'web/src/screens/global',
                'web/src/screens/stage4', 'web/src/screens/stage5',
                'web/src/screens/inbox', 'web/src/screens/stage7',
                'web/src/types/plot.ts', 'web/src/types/approvals.ts',
                'web/src/types/prepare.ts', 'web/src/fixtures/plot_v1'):
        if (ROOT / rel).exists():
            problems.append(f'旧链读屏面回来了：{rel}')
    assert not problems, '\n'.join(problems)


def test_closed_decode_surface_stays_deleted():
    """STR-06 关闭批删除的存量解码面不得回来；退役名册常量不得复活。

    拒识闭合不依赖这些模块：产物层 `RETIRED_ARTIFACT_PATHS` 与盘面
    stage5/deep|shallow 探测独立承担 legacy_incompatible；节点层按
    「非现役节点」通则拒识。
    """
    problems = [f'已删解码面回来了：{m}' for m in CLOSED_DECODE_MODULES
                if (ROOT / m).exists()]
    from contracts import invariants as C
    for symbol in ('RETIRED_NODES', 'RETIRED_APPROVAL_BINDINGS'):
        if hasattr(C, symbol):
            problems.append(f'退役名册常量复活：contracts.invariants.{symbol}')
    from novel.cli import main as cli_main
    from novel import work_units
    for symbol in ('validate_reviewer_record', 'commander_budget_for_record',
                   'reconcile_commander_response', 'rejected_responses'):
        if hasattr(work_units, symbol):
            problems.append(f'work_units 历史解码函数复活：{symbol}')
    if hasattr(cli_main, '_cmd_workflow'):
        problems.append('CLI workflow transition 子命令复活：_cmd_workflow')
    assert not problems, '\n'.join(problems)


# ═══════════════════════════════════════════════════════════════════════════
# 2026-10-04 清理三批（109830b7 + f1f4f623 + f6c250b1；用户裁决：不保留旧产物
# 读兼容）：删除的旧链 schema 与 novel 模块不得回来。approvals／mechanical_
# admission 另有 find_spec 守卫；edits 等路由重新挂载会撞 test_api_http 的
# 公开接口面钉扎。此处统一登记整批名册，防单点守卫被单独放宽。
# ═══════════════════════════════════════════════════════════════════════════

#: 2026-10-04 三批清理删除的模块（回来即红）。
CLEANUP_2026_10_04_MODULES = (
    # f1f4f623：18 个旧链 schema 模块与存量审批账解码链
    'contracts/schema/analyze.py', 'contracts/schema/analyze_draft.py',
    'contracts/schema/analyze_recovery.py', 'contracts/schema/characters.py',
    'contracts/schema/characters_v3.py', 'contracts/schema/core_evidence.py',
    'contracts/schema/evaluation.py', 'contracts/schema/knowledge.py',
    'contracts/schema/plot.py', 'contracts/schema/plot_context.py',
    'contracts/schema/plot_edit.py', 'contracts/schema/plot_materials.py',
    'contracts/schema/plot_planning.py', 'contracts/schema/plot_review.py',
    'contracts/schema/stage2.py', 'contracts/schema/stage4.py',
    'contracts/schema/stage4_source.py', 'contracts/schema/stage5.py',
    'novel/orchestrator/approvals.py',
    # 109830b7：零引用死代码与孤儿契约载体
    'contracts/schema/agent_wire.py', 'contracts/schema/stage4_ir.py',
    'contracts/schema/stage7.py', 'novel/api/routes/edits.py',
    'novel/core/bands.py', 'novel/core/facts.py', 'novel/core/spans.py',
    'novel/human_actions.py', 'novel/llm/aggregate_runs.py',
    'novel/llm/privacy.py', 'novel/llm/typed_failures.py',
    'novel/orchestrator/cli_probe.py',
    'novel/orchestrator/mechanical_admission.py',
    'novel/report/workpack.py', 'novel/snapshot_clone.py',
    # f6c250b1：novel/core 旧链算子残留
    'novel/core/evidence.py', 'novel/core/naming.py',
    'novel/core/normalize.py',
)


def test_2026_cleanup_batch_modules_stay_deleted():
    """2026-10-04 三批清理删除的模块不得回来（单向名册，不依赖其他守卫）。"""
    problems = [f'已删模块回来了：{m}' for m in CLEANUP_2026_10_04_MODULES
                if (ROOT / m).exists()]
    assert not problems, '\n'.join(problems)


def test_web_fixture_node_ids_stay_within_live_nodes():
    """web 演示夹具与 mock 的 node id 必须都在 `NODES` 现役登记内。

    夹具没有再生成链（enums.json 除外，那是 payload 镜像），节点退役后唯一能拦
    「后端已退役、夹具还在演示」漂移的就是这道闸——2026-10-04 复审正是这样抓到
    `str06_candidate.json`／`agent_capabilities.json` 里残留的 `str06.stage4.bind`
    （CS-3 已退役，夹具仍当有产物节点与计费行）。
    """
    node_re = re.compile(r'str06\.[a-z0-9_]+\.[a-z0-9_]+')
    from contracts.invariants import NODES
    live = {str(node) for node in NODES}
    problems = []
    for path in sorted((ROOT / 'web' / 'src' / 'fixtures' / 'backend_v1').glob('*.json')):
        found = set(node_re.findall(path.read_text(encoding='utf-8')))
        stale = sorted(found - live)
        if stale:
            problems.append(f'{path.name}：退役节点 {stale}')
    mock = ROOT / 'web' / 'src' / 'api' / 'mock.ts'
    stale = sorted(set(node_re.findall(mock.read_text(encoding='utf-8'))) - live)
    if stale:
        problems.append(f'mock.ts：退役节点 {stale}')
    assert not problems, '\n'.join(problems)

def test_revalidation_machinery_stays_deleted():
    """2026-10-05：`--revalidate` 提升路与重验证审计账整体删除（DECISIONS 同日行）。

    载体层（`Provenance` 的三个来源键与 `promoted()`）、store 层（提升／审计／基线快照）
    与新链 repository 的 `promote_invalid` 一个都不许回来。现役产物的盘上字节不含这些键，
    所以删除不升任何产物版本；一旦有人把它们加回载体，旧根产物与新根产物就又分家了。
    """
    import contracts.schema as schema
    from contracts.schema.common import Provenance
    from novel import store

    assert not hasattr(schema, 'RevalidationAudit')
    gone_keys = {'revalidated', 'invalid_baseline_sha256', 'revalidation_audit'}
    assert not gone_keys & set(Provenance.model_fields), (
        f'Provenance 又长出了 {sorted(gone_keys & set(Provenance.model_fields))}')
    assert not hasattr(Provenance, 'promoted')
    for name in ('promote_invalid', 'prepared_invalid_promotion', 'revalidation_audit',
                 'revalidation_baseline', 'prospective_invalid_sha256'):
        assert not hasattr(store.ArtifactStore, name), f'ArtifactStore.{name} 回来了'
    assert not hasattr(store.Str06Repository, 'promote_invalid')
    for name in ('REVALIDATION_DIRNAME', 'REVALIDATION_REFERENCE_VERSION',
                 'build_revalidation_audit'):
        assert not hasattr(store, name), f'novel.store.{name} 回来了'
