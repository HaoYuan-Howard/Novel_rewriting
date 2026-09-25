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
方向 ② 翻转为「现役键一律不得在退役册里」。F8c 删除旧引擎文件后，剩下的 file
类登记项才会逐一消失。
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

from contracts.artifacts import ARTIFACT_PATHS
from contracts.invariants import NODES
from contracts.schema.str06.retirement import (
    STR06_F6D_RETIREMENT, STR06_F7E_RED_ACCOUNT, STR06_F7E_RETIREMENT,
    STR06_F7E_TEST_KEEP,
)

ROOT = Path(__file__).resolve().parent.parent

#: 旧链 stage 包（F7e 目录级登记的四个对象）。
OLD_STAGE_DIRS = ('novel/stages/stage2', 'novel/stages/stage4',
                  'novel/stages/stage5', 'novel/stages/stage7')

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


def _registered_files(items) -> set[str]:
    out: set[str] = set()
    for item in items:
        target = item.target
        file = _file_of(target)
        if file is not None and file.is_file():
            out.add(file.relative_to(ROOT).as_posix())
        elif file is not None and file.is_dir():
            out.add(file.relative_to(ROOT).as_posix() + '/')
        elif '@' not in target and '::' not in target and '#' in target:
            out.add(target.split('#')[0])
    return out


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


def test_registered_items_all_exist():
    problems: list[str] = []
    for item in STR06_F7E_RETIREMENT:
        target = item.target
        if target.startswith(_EXECUTED_BY_F8A) or target.startswith(_EXECUTED_BY_F8B):
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
    registered_dirs = {t for t in _registered_files(STR06_F7E_RETIREMENT) if t.endswith('/')}
    problems = []
    for directory in OLD_STAGE_DIRS:
        if f'{directory}/' not in registered_dirs:
            problems.append(f'旧链包未登记：{directory}/')
            continue
        # 目录登记之外，包内每个引擎文件都必须真实存在（登记的不是空目录）。
        engines = [p for p in (ROOT / directory).rglob('*.py')
                   if '__pycache__' not in p.parts]
        assert engines, f'{directory}/ 下没有引擎文件——目录登记失去意义'
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
    """旧链 views 与前端旧屏必须在册（F7d 候选监控面是接管者，不是删除对象）。"""
    registered = _registered_files(STR06_F7E_RETIREMENT)
    problems = []
    # ⚠ views/stage7.py 已由 F8b 按 delete_file 登记执行删除（经已退役的
    # stage7.rename 节点建 ctx，F8a 后不可用），不再要求在册。
    for rel in ('novel/views/stage2.py', 'novel/views/stage4.py',
                'novel/views/stage5.py',
                'novel/views/blueprint_progress.py'):
        if rel not in registered:
            problems.append(f'旧链视图未登记：{rel}')
    for directory in ('web/src/screens/read', 'web/src/screens/global',
                      'web/src/screens/stage4', 'web/src/screens/stage5',
                      'web/src/screens/inbox'):
        if f'{directory}/' not in registered:
            problems.append(f'旧链前端屏未登记：{directory}/')
    assert not problems, '\n'.join(problems)


def test_f6d_and_f7e_tables_do_not_double_register():
    """两表目标不重复（F6d 的 stage6 项不与 F7e 旧链项重叠）。"""
    f6d = {t.target for t in STR06_F6D_RETIREMENT}
    f7e = {t.target for t in STR06_F7E_RETIREMENT}
    overlap = f6d & f7e
    assert not overlap, f'两表重复登记：{sorted(overlap)}'


@pytest.mark.parametrize('why', ['import 时无业务副作用'], ids=lambda v: v)
def test_registry_module_has_no_import_side_effects(why):
    """登记是纯数据：import 本模块不得碰盘面（与 candidates.py 同纪律）。

    ⚠ 在**子进程**里验，不在本进程删 `sys.modules` 再重导入：那会把
    `contracts.schema.str06` 全家载成第二套类对象，之后任何持旧类引用的
    测试都因 isinstance 分家而假红（全量回归里实测过，症状是
    「Input should be a valid dictionary or instance of BindingEntry」）。
    """
    import subprocess

    script = (
        'import sys;'
        'sys.path.insert(0, sys.argv[1]);'
        'before = {p for p in __import__("pathlib").Path(sys.argv[1]).rglob("*")'
        ' if p.is_file()};'
        'import contracts.schema.str06.retirement as r;'
        'after = {p for p in __import__("pathlib").Path(sys.argv[1]).rglob("*")'
        ' if p.is_file()};'
        'assert before == after, "import 写了盘面";'
        'assert r.STR06_F7E_RETIREMENT and r.STR06_F7E_RED_ACCOUNT'
    )
    result = subprocess.run([sys.executable, '-c', script, str(ROOT)],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f'{why}：{result.stderr[-500:]}'
