#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_f7e_retirement —— F7e 补充退役登记的双向对账。

【两个方向都必须闭合】
① 登记项必须真实存在：目录、注册键、投影键、测试文件、grandfather 边的 importer
   与深层 import 目标——登记错了对象的清单比没有清单更危险（F8c 当天才发现删不动）。
② 现役旧链面必须全部在册：旧 stage 目录下每个引擎文件、旧链 NODES／ARTIFACT_PATHS
   键、consumption 投影键、tests/ 下每个 test_*.py（keep／rewrite／delete 三桶加
   str06 候选与 F6d 已登记，四类合起来必须逐一覆盖）、88 条 grandfather 边。

本测试只对账，不删除、不改写任何现役面（删除归 F8c 一次合入）。
与 `test_str06_retirement.py`（F6d 表）分工：那张盯 Stage6，本张盯旧 Stage2/4/5/7
与跨层面；两表合并才是 F8c 的完整删除清单。

【F8a 执行了注册键那一格】NODES／ARTIFACT_PATHS／CONSUMED_PROJECTIONS 的旧链键已随
契约翻转删除（新链 11 节点、22 产物、12 投影键上位）——那些 `remove_entry` 登记项
**已经兑现**，不再要求它们存在；方向 ① 现在只查尚未执行的 file／符号／字面目标，
方向 ② 翻转为「现役键一律不得在退役册里」。

【F8b/F8c 执行了删除那一格】Stage6 服务面（F8b）与旧 Stage2/4/5/7 整链
（F8c：四个 stage 引擎包、判官、旧链 views、approvals 路由、delete 桶测试与
grandfather 边）已按登记删除——方向 ① 对这两个批次放开（`_EXECUTED_BY_F8B`／
`_EXECUTED_BY_F8C`／从 delete 桶机械派生的 `_F8C_DELETED_TESTS`），「不在盘上」
正是删除成功的证据；旧 stage 包与旧链 views 的方向 ② 随之翻转为「必须已删」。
前端旧屏（read/global/stage4/stage5/inbox）尚未删除，仍要求在册且存在。
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

from contracts.artifacts import ARTIFACT_PATHS
from contracts.invariants import NODES
from contracts.schema.retirement import (
    STR06_F6D_RETIREMENT, STR06_F7E_RED_ACCOUNT, STR06_F7E_RETIREMENT,
    STR06_F7E_TEST_KEEP,
)

ROOT = Path(__file__).resolve().parent.parent

#: 旧链 stage 包（F7e 目录级登记的四个对象）。
#: ⚠ F8c（2026-09-25）：这四个目录**被新链 per-stage 包原地重建**——
#: `novel/stages/stage2|4|5/7/` 现在住的是 STR-06 新链的确定性引擎，旧链整层
#: （blueprint/analyze/characters/knowledge/plot_*/stage6/…）随迁移包分流一并
#: 删除。因此「目录不在盘上」这条执行证据改为**逐模块名**核对：旧链引擎模块
#: 一个都不许回来（`tests/test_layering.py::test_stage2_owner_shims_cannot_return`
#: 另立同向守卫），新链模块必须全部落位。
OLD_STAGE_DIRS = ('novel/stages/stage2', 'novel/stages/stage4',
                  'novel/stages/stage5', 'novel/stages/stage7')

#: 旧链引擎模块（F7e delete_file 登记在册、F8c 必须已删除的那一批；逐条见
#: `contracts/schema/retirement.py::STR06_F7E_RETIREMENT`）。
OLD_STAGE_MODULES = (
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

#: 新链 per-stage 引擎模块（F8c 分流后的落点；必须全部在盘上）。
NEW_STAGE_MODULES = (
    'novel/stages/stage2/__init__.py', 'novel/stages/stage2/source.py',
    'novel/stages/stage2/names.py',
    'novel/stages/stage4/__init__.py', 'novel/stages/stage4/design.py',
    'novel/stages/stage4/grounding.py', 'novel/stages/stage4/compile.py',
    'novel/stages/stage4/guard.py', 'novel/stages/stage4/fold.py',
    'novel/stages/stage4/book_ir.py',
    'novel/stages/stage5/__init__.py', 'novel/stages/stage5/body.py',
    'novel/stages/stage5/body_checks.py', 'novel/stages/stage5/hook.py',
    'novel/stages/stage5/hook_writer.py', 'novel/stages/stage5/hook_context.py',
    'novel/stages/stage7/__init__.py', 'novel/stages/stage7/naming.py',
    'novel/stages/stage7/assembly.py',
    'novel/stages/common.py', 'novel/stages/chain.py',
)

#: 这三类键不进退役登记：stage6 归 F6d 表，stage0/1 整层重构延期（范围外）。
OUT_OF_SCOPE_KEY_PREFIXES = ('stage6.', 'stage0.', 'stage1.')


def _file_of(target: str) -> Path | None:
    head = target.split('::')[0].split('#')[0].split('@')[0]
    if '/' not in head:
        return None
    candidate = ROOT / head
    return candidate if candidate.exists() else None


def _symbols(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding='utf-8'))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(t := node.target, ast.Name):
            names.add(t.id)
    return names


def _dict_keys(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding='utf-8'))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    keys.add(k.value)
    return keys


# ───────────────────────── 方向①：登记项必须真实存在 ─────────────────────────

#: F8a 已执行（旧链注册键随契约翻转删除）的登记项目标前缀：那些 `remove_entry`
#: 项已经兑现——键不在现役表里正是删除成功的证据，不再要求它们存在。
_EXECUTED_BY_F8A = ('contracts/invariants.py#', 'contracts/artifacts.py#',
                    'novel/consumption.py#',
                    # F8A 文档分流：str06.md 现役内容迁入正式 schema 文档后，
                    # stage6.md／plot_blueprint.md 两份旧文档删除。
                    'docs/schema/plot_blueprint.md', 'docs/schema/stage6.md')

#: F8b 已执行的登记项：Stage6 服务面按登记切除（F6d 表三条 delete_file 经本表
#: 的 grandfather 边对账），`novel/views/stage6.py` 已随 routes 注销删除——
#: 文件不存在正是删除成功的证据；`tests/test_layering.py` 的冻结边清单同批缩减。
#: `web/src/types/prepare.ts` 同批删除：Stage6 prepare 视图类型随整层退役
#: （take_over「退役无接管」），仍被 ApprovalDoc.prepare 消费的 PrepareState 等
#: 并入 web/src/types/approvals.ts，不为退役面保留第二个类型落点。
#: `novel/views/stage7.py`（delete_file，take_over「F7d 候选 views
#: （naming/delivery）」）同批执行：该视图经已退役的 `stage7.rename` 节点建
#: readonly_ctx，F8a 后一调用即 ValueError——不是「能跑但过期」，是按登记
#: 提前删除；其 3 条 grandfather 边随 `tests/test_layering.py` 冻结清单缩减
#: （88→83，含 views/stage6.py 两条）。`tests/test_views_stage7.py` 随主题删除。
_EXECUTED_BY_F8B = ('novel/views/stage6.py@', 'web/src/types/prepare.ts',
                    'novel/views/stage7.py', 'tests/test_views_stage7.py',
                    'novel/views/stage7.py@',
                    'web/src/screens/stage7/',
                    # corpus 付费预览（原 rewrite 登记）改判 F8b 提前删除：
                    # F8a 已删其派生源（NODES 旧键、产物 spec、HOOK 常量），
                    # import 与运行双死；修复需手抄冻结节点表，违反工具自身的
                    # 「从现役契约派生」纪律。接管：新链付费边界由
                    # PaidRequestSession 工作单元／整书总闸／ledger 守卫（F7c
                    # 已迁反例）；corpus 线重启时按当期现役契约重建预览面。
                    'tests/test_current_corpus_paid_preview.py',
                    'tools/current_corpus_paid_preview.py',
                    # edits 路由族（旧节点编辑端点整族退役，F8b 提前执行）：
                    # 文件保留空 router 维持 app 模块面，两条 stage2 深导入
                    # grandfather 边随路由删除消失。
                    'novel/api/routes/edits.py@novel.stages.stage2')

#: F8c 已执行的登记项（2026-09-25 旧链整层删除）：文件/目录不在盘上、
#: importer 不再深导入，正是删除成功的证据，不再要求它们存在。与
#: `_EXECUTED_BY_F8B` 同格式、同纪律（登记条目保留作历史台账）。分组：
#: ① 四个旧链 stage 引擎包（目录级 delete_file；目录前缀同时覆盖包内模块
#:    作 importer 的 grandfather 边，如 stage4/actors.py@…、stage6/hook.py@…）；
#: ② 旧链判官、旧链 views 与 approvals 路由文件；
#: ③ importer 仍在盘上、但旧链 stage 深导入已随旧链消失的 grandfather 边，
#:    按 importer 整文件覆盖——这些文件今天一条 `novel.stages.` 深边都不剩。
#:    「该 importer 无旧链深边」这个假设由 `test_grandfather_edges_all_registered`
#:    反向钉住：它哪天新增一条深边，那条边不在 F7e 登记表里，当场红。
#: ⚠ 不含 store.py→stage1.audit 与 views/ingest.py 两条**范围外边**
#: （stage0/stage1 整层重构延期，边仍在，必须继续真实存在）——store.py 的
#: 四条旧链边因此逐条登记，不用整文件前缀。
_EXECUTED_BY_F8C = (
    'novel/stages/stage2/',
    'novel/stages/stage4/',
    'novel/stages/stage5/',
    'novel/stages/stage6/',
    'novel/stages/stage7/',
    'contracts/judges/stage2.py',
    'contracts/judges/stage4.py',
    'contracts/judges/stage5.py',
    'contracts/judges/stage7.py',
    'novel/views/stage2.py',
    'novel/views/stage4.py',
    'novel/views/stage5.py',
    'novel/views/blueprint_progress.py',
    'novel/api/routes/approvals.py',
    # ── F8c 前端退役批次（旧链屏目录、旧链类型与旧链夹具整族删除）──
    # 前端旧屏目录（read/global/stage4/stage5/inbox 的 delete_file 登记项，
    # 以及 stage7 屏目录：F8b 只提前删了后端 views/stage7.py，前端屏随本批删除）
    # 与它们的 rewrite_consumer 类型/夹具项：文件不在盘上正是删除成功的证据，
    # 「必须已删」的翻转断言在 test_old_chain_views_and_frontend_registered。
    'web/src/screens/read/',
    'web/src/screens/global/',
    'web/src/screens/stage4/',
    'web/src/screens/stage5/',
    'web/src/screens/inbox/',
    'web/src/screens/stage7/',
    'web/src/types/plot.ts',
    'web/src/types/characters.ts',
    'web/src/types/approvals.ts',
    'web/src/fixtures/plot_v1/',
    'novel/api/errors.py@novel.stages.',
    'novel/cli/main.py@novel.stages.',
    'novel/llm/client.py@novel.stages.',
    'novel/llm/stage2_global_runs.py',
    'novel/llm/stage2_runs.py',
    'novel/llm/stage4_runs.py',
    'novel/orchestrator/agent.py@novel.stages.',
    'novel/orchestrator/approvals.py@novel.stages.',
    'novel/orchestrator/inspection.py@novel.stages.',
    'novel/orchestrator/queue.py@novel.stages.',
    'novel/orchestrator/state.py@novel.stages.',
    'novel/orchestrator/workflow_transition.py@novel.stages.',
    'novel/store.py@novel.stages.stage2.knowledge.grounding',
    'novel/store.py@novel.stages.stage2.shared.character_refs',
    'novel/store.py@novel.stages.stage4.binding_input_contract',
    'novel/store.py@novel.stages.stage4.phase_binding',
)

#: F8c 随旧链整批删除的旧链功能测试：从 F7e 表 delete 桶**机械派生**
#: （不手抄清单——登记表是真相源）。delete 桶里每个文件已不在盘上，
#: 「不存在」即删除成功的证据。
_F8C_DELETED_TESTS = frozenset(
    item.target for item in STR06_F7E_RETIREMENT
    if item.action.value == 'delete_file' and item.target.startswith('tests/'))


def test_registered_items_all_exist():
    problems: list[str] = []
    for item in STR06_F7E_RETIREMENT:
        target = item.target
        if (target.startswith(_EXECUTED_BY_F8A) or target.startswith(_EXECUTED_BY_F8B)
                or target.startswith(_EXECUTED_BY_F8C) or target in _F8C_DELETED_TESTS):
            continue
        file = _file_of(target)
        if file is None:
            problems.append(f'{target}: 无法解析文件部分')
            continue
        if not file.exists():
            problems.append(f'{target}: 文件不存在 {file}')
            continue
        if '::' in target:
            if target.split('::', 1)[1] not in _symbols(file):
                problems.append(f'{target}: 符号未定义')
        elif '#' in target:
            if target.split('#', 1)[1] not in _dict_keys(file):
                problems.append(f'{target}: dict 键不存在')
        elif '@' in target:
            needle = target.split('@', 1)[1].strip('"')
            if needle not in file.read_text(encoding='utf-8'):
                problems.append(f'{target}: 字面/深层 import 串不存在')
    assert not problems, 'F7e 退役登记指向不存在的对象：\n' + '\n'.join(problems)


def test_registry_items_well_formed():
    for item in STR06_F7E_RETIREMENT:
        assert item.take_over.strip(), f'{item.target}: 缺接管者'
        assert item.note.strip(), f'{item.target}: 缺说明'


def test_keep_bucket_reasons_are_explicit():
    for name, reason in STR06_F7E_TEST_KEEP.items():
        assert name.startswith('test_') and name.endswith('.py'), name
        assert reason.strip(), f'{name}: keep 桶必须写明理由'
        assert (ROOT / 'tests' / name).is_file(), f'{name}: keep 桶指向不存在的文件'


def test_red_account_rows_are_complete():
    assert STR06_F7E_RED_ACCOUNT, '红账清单为空'
    for row in STR06_F7E_RED_ACCOUNT:
        for field in ('item', 'record', 'overlap', 'owner', 'disposition', 'evidence'):
            assert getattr(row, field).strip(), f'{row.item}: {field} 为空'
    # 重叠口径必须显式：不直接相加是纪律，不是省略。
    joined = '；'.join(row.overlap for row in STR06_F7E_RED_ACCOUNT)
    assert '包含' in joined or '无' in joined


# ───────────────────────── 方向②：现役旧链面必须全部在册 ─────────────────────────

def test_old_stage_packages_fully_registered():
    """旧链 stage 引擎：delete_file 在册 **且已真的删掉**（F8c 执行证据）。

    F8c 前本条查「目录登记之外，包内每个引擎文件都必须真实存在（登记的不是
    空目录）」；旧链整层删除后方向翻转为——旧链引擎模块必须**不在盘上**（连
    只剩 `__pycache__` 的半删除态也不算）。删除范围被静默缩小（留一个模块）
    都当场红。⚠ F8c 把 `novel/stages/stage2|4|5/7/` **原地重建**为新链
    per-stage 包，因此本条不再断言「目录不存在」，改为逐模块名核对旧链引擎
    已删、新链模块已落位——目录回来不等于旧链回来，模块名才说了算。
    包内引擎文件删除前「先迁独有反例」的义务按家族登记在 REQUIREMENTS，
    不随本条的翻转缩分母。
    """
    targets = {item.target for item in STR06_F7E_RETIREMENT}
    delete_dirs = {item.target for item in STR06_F7E_RETIREMENT
                   if item.action.value == 'delete_file'}
    problems = []
    for directory in OLD_STAGE_DIRS:
        if f'{directory}/' not in targets:
            problems.append(f'旧链包未登记：{directory}/')
            continue
        if f'{directory}/' not in delete_dirs:
            problems.append(f'旧链包登记动作不是 delete_file：{directory}/')
    # F8c 执行证据 ①：旧链引擎模块一个都不许回来。
    for module in OLD_STAGE_MODULES:
        if (ROOT / module).exists():
            problems.append(f'旧链引擎模块仍在盘上（F8c 删除未执行）：{module}')
    # F8c 执行证据 ②：新链 per-stage 模块必须全部落位（分流没做完也红）。
    for module in NEW_STAGE_MODULES:
        if not (ROOT / module).is_file():
            problems.append(f'新链模块未落位（F8c 分流未完成）：{module}')
    # 守卫的守卫：新链包里不得混进旧链模块名（包回来了但装的是旧代码）。
    for directory in OLD_STAGE_DIRS:
        present = sorted(p.name for p in (ROOT / directory).glob('*.py'))
        for name in ('extract.py', 'checks.py', 'plot_design.py', 'plan.py',
                     'write.py', 'review.py', 'rename.py', 'assemble.py',
                     'prompts.py', 'measure.py', 'beats.py', 'actors.py',
                     'reading.py', 'closure.py', 'subs.py'):
            if name in present:
                problems.append(f'{directory}/ 混进旧链模块名：{name}')
    assert not problems, '\n'.join(problems)


def test_no_live_registry_key_is_registered_for_retirement():
    """F8a 翻转后的方向 ②：现役注册键一律不得在退役册里。

    旧链键已随契约翻转删除（`remove_entry` 登记项兑现）；现在活着的是新链 11 个
    `str06.` 节点与 22 份三级产物——把它们登记进退役册意味着切换把现役面删了，
    那正是本测试要拦的事。
    """
    registered = {item.target for item in STR06_F7E_RETIREMENT}
    problems = [
        f'现役 NODES 键被登记退役：{key}'
        for key in NODES if f'contracts/invariants.py#{key}' in registered]
    problems += [
        f'现役 ARTIFACT_PATHS 键被登记退役：{key}'
        for key in ARTIFACT_PATHS if f'contracts/artifacts.py#{key}' in registered]
    assert not problems, '\n'.join(problems)
    # 旧链键必须真的已经不在了（F8a 执行证据）。
    assert not any(key.startswith(('stage2.', 'stage4.', 'stage5.', 'stage7.', 'stage6.'))
                   for key in NODES), '旧链 NODES 键仍在现役表里'


def test_consumption_projection_keys_registered():
    text = (ROOT / 'novel' / 'consumption.py').read_text(encoding='utf-8')
    match = re.search(
        r"CONSUMED_PROJECTIONS: Final\[dict\[str, dict\[str, Projection\]\]\] = \{(.*?)\n\}",
        text, re.S)
    assert match, 'consumption 投影表形状变化，对账测试需同步'
    live = set(re.findall(r"^    '([a-z0-9.]+)': \{", match.group(1), re.M))
    registered = {item.target for item in STR06_F7E_RETIREMENT}
    # F8a 翻转后：活着的是新链投影键（`str06.` 前缀）与范围外的 stage1.partition；
    # 旧链投影键已删。方向翻转为「现役键不得在册」（范围外保留项除外——
    # stage1.partition 登记的是「范围外保留」，不是删除）。
    out_of_scope = {'novel/consumption.py#stage1.partition'}
    problems = [f'现役 consumption 键被登记退役：{key}' for key in sorted(live)
                if f'novel/consumption.py#{key}' in registered - out_of_scope]
    assert not problems, '\n'.join(problems)
    assert all(key.startswith('str06.') or key == 'stage1.partition' for key in live), (
        f'consumption 表里出现旧链键：{sorted(live)}')


def test_every_test_file_is_bucketed():
    """tests/ 下每个 test_*.py 必须落入且只落入一个桶。"""
    f6d_files = {t.target.split('/')[-1] for t in STR06_F6D_RETIREMENT
                 if t.target.startswith('tests/') and t.target.endswith('.py')}
    delete = {t.target.split('/')[-1] for t in STR06_F7E_RETIREMENT
              if t.action.value == 'delete_file' and t.target.startswith('tests/')}
    rewrite = {t.target.split('/')[-1] for t in STR06_F7E_RETIREMENT
               if t.action.value == 'rewrite_consumer' and t.target.startswith('tests/')}
    keep = set(STR06_F7E_TEST_KEEP)
    candidate = {p.name for p in (ROOT / 'tests').glob('test_str06_*.py')}
    buckets = {'keep': keep, 'rewrite': rewrite, 'delete': delete,
               'str06': candidate, 'f6d': f6d_files}
    all_tests = {p.name for p in (ROOT / 'tests').glob('test_*.py')}
    covered: dict[str, list[str]] = {}
    for name in all_tests:
        hits = [bucket for bucket, members in buckets.items() if name in members]
        if not hits:
            raise AssertionError(f'测试文件未入桶：{name}')
        if len(hits) > 1:
            raise AssertionError(f'测试文件入多个桶（{hits}）：{name}')
        covered[name] = hits
    assert len(covered) == len(all_tests)
    # 删除桶只收旧链功能测试：不删守卫、不删范围外、不删候选。
    leaked = delete & (keep | candidate | f6d_files)
    assert not leaked, f'删除桶混入非旧链文件：{sorted(leaked)}'


def test_grandfather_edges_all_registered():
    sys.path.insert(0, str(ROOT / 'tests'))
    try:
        import test_layering as layering
    finally:
        sys.path.pop(0)
    live = layering._CROSS_STAGE_DEEP_GRANDFATHER
    registered = {item.target for item in STR06_F7E_RETIREMENT}
    problems = [f'grandfather 边未登记：{imp} → {mod}'
                for imp, mod in sorted(live)
                if f'{imp}@{mod}' not in registered]
    assert not problems, '\n'.join(problems)


def test_old_chain_views_and_frontend_registered():
    """旧链 views 与前端旧屏必须在册（F7d 候选监控面是接管者，不是删除对象）。

    F8c 后方向整体翻转：views 与前端旧屏目录都要求**已不在盘上**——删除正是
    退役执行的证据；半删一个文件（留个空目录或只剩测试）就是旧链监控面还在。
    前端批次（F8c）执行内容：read/global/stage4/stage5/inbox/stage7 六个屏目录
    连同测试删除，`types/plot.ts`、`types/characters.ts`、`types/approvals.ts`
    与 `fixtures/plot_v1/` 同批退役（stage7 屏目录 F8b 只提前删了后端视图，
    前端屏随本批删除）；保留屏 stage0/stage1/corpus/settings/run/str06 与
    `types/stages.ts`（改写成 Stage0/1 + CheckReport）继续在册且真实存在。
    """
    targets = {item.target for item in STR06_F7E_RETIREMENT}
    problems = []
    for rel in ('novel/views/stage2.py', 'novel/views/stage4.py',
                'novel/views/stage5.py',
                'novel/views/blueprint_progress.py'):
        if rel not in targets:
            problems.append(f'旧链视图未登记：{rel}')
        if (ROOT / rel).exists():
            problems.append(f'旧链视图仍在盘上（F8c 删除未执行）：{rel}')
    for directory in ('web/src/screens/read', 'web/src/screens/global',
                      'web/src/screens/stage4', 'web/src/screens/stage5',
                      'web/src/screens/inbox', 'web/src/screens/stage7'):
        if f'{directory}/' not in targets:
            problems.append(f'旧链前端屏未登记：{directory}/')
        elif (ROOT / directory).exists():
            problems.append(f'旧链前端屏仍在盘上（F8c 前端批次未执行）：{directory}/')
    assert not problems, '\n'.join(problems)


def test_f6d_and_f7e_tables_do_not_double_register():
    """两表目标不重复（F6d 的 stage6 项不与 F7e 旧链项重叠）。"""
    f6d = {t.target for t in STR06_F6D_RETIREMENT}
    f7e = {t.target for t in STR06_F7E_RETIREMENT}
    overlap = f6d & f7e
    assert not overlap, f'两表重复登记：{sorted(overlap)}'


@pytest.mark.parametrize('why', ['import 时无业务副作用'], ids=lambda v: v)
def test_registry_module_has_no_import_side_effects(why):
    """登记是纯数据：import 本模块不得碰盘面（与激活表同纪律）。

    ⚠ 在**子进程**里验，不在本进程删 `sys.modules` 再重导入：那会把
    contracts 层载成第二套类对象，之后任何持旧类引用的
    测试都因 isinstance 分家而假红（全量回归里实测过，症状是
    「Input should be a valid dictionary or instance of BindingEntry」）。
    """
    import subprocess

    script = (
        'import sys;'
        'sys.path.insert(0, sys.argv[1]);'
        'before = {p for p in __import__("pathlib").Path(sys.argv[1]).rglob("*")'
        ' if p.is_file()};'
        'import contracts.schema.retirement as r;'
        'after = {p for p in __import__("pathlib").Path(sys.argv[1]).rglob("*")'
        ' if p.is_file()};'
        'assert before == after, "import 写了盘面";'
        'assert r.STR06_F7E_RETIREMENT and r.STR06_F7E_RED_ACCOUNT'
    )
    result = subprocess.run([sys.executable, '-c', script, str(ROOT)],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f'{why}：{result.stderr[-500:]}'
