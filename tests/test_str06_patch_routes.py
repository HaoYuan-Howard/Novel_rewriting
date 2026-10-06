#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_patch_routes —— 规则码修复路由的逐码清点契约（CS-H H2，RC-57～59）。

规格（`docs/schema/repair_convergence.md` CS-H D 表）：
  · 覆盖：全部 JSON 节点都有条目定位器；跨节点回派与节点内返修同一协议；
  · 目标：固定路径；或可选目标（候选拍闭集，回答的路径必须落在闭集内）；
  · 无法定位：视为判据缺路径的工程缺陷——停并报规则码，不进整份重生；
  · 整份出口（闭集）：JSON 回答无法解码或整体形状失败、协议字面量不符；
    文本节点的篇幅带、空回答、包装。局部称谓、单条义务、编译缺陷不得进入；
  · 契约测试保证每个现役规则码**或可定位、或有可选目标、或登记在整份闭集**。

本文件就是那条契约测试，外加它与现役判据的一一对应：
  · 枚举：五个判据模块（`stage4/design.py`／`grounding.py`／`compile.py`／
    `guard.py`／`hook_validation.py`，外加同块的 `hook.py`／`preflight.py`）
    与 `paid_requests.py` 的闭集合成码（`answer_shape`／`patch_shape`）在源码
    里出现的码字面量集合，必须与 `PATCH_ROUTES` 的键集合逐码相等；
  · 红例：每个 `locate` 码在真实判据入口产出**非空 wire 路径**；
  · 闭集：`whole_answer` 码只能是合成码或协议字面量码（不是模型内容错）。

零付费：只有源码扫描与纯函数判定，不建 session、不发请求。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from contracts.judges.str06 import PATCH_ROUTES
from novel.stages.stage4.design_paths import wire_paths_of

REPO = Path(__file__).resolve().parents[1]

#: 判据模块 → 源码里出现的码字面量（本测试按 AST 抽，正则只做兜底核对）。
#: 五个主判据模块（规格 D 表点名）＋同块的 hook/preflight/materials，加上它们的
#: 共用发射面：`read_closure.py`（读取闭合码）、`fact_bindings.py`（事实锁码）、
#: `common.py`（材料泄漏码）、`repair.py`（材料归因码）。`epistemic_lock_order` 由
#: `chain._backfill_epistemic_lock_order` 以 OwnerIssueV1 码发出（compile 回派面）。
#: `materials.py`（CS-H H3 起）是 Design 原文闸的残余三态产地——先投影再查后，
#: `design_unreleased_reference`／`design_projection_lossy` 在此发出并带站点路径。
JUDGMENT_MODULES: tuple[str, ...] = (
    'novel/stages/stage4/design.py',
    'novel/stages/stage4/grounding.py',
    'novel/stages/stage4/compile.py',
    'novel/stages/stage4/guard.py',
    'novel/stages/stage4/hook_validation.py',
    'novel/stages/stage4/hook.py',
    'novel/stages/stage4/preflight.py',
    'novel/stages/stage4/materials.py',
    'novel/stages/stage4/read_closure.py',
    'novel/stages/stage4/fact_bindings.py',
    'novel/stages/common.py',
    'novel/stages/stage4/repair.py',
)
#: 回派面码（OwnerIssueV1.code，`chain._backfill_*`／`plan_material_repairs`
#: 发出；不在判据模块的 wire 定位面里）。它们是跨节点回派/材料归因的问题身份，
#: 按 `route_owner_issue` 分派 owner——没有本节点 wire 编辑位置，登记 stop。
DISPATCH_CODES = frozenset({
    'epistemic_lock_order', 'must_keep_unbound', 'material_name_leak',
})
#: 整份出口闭集的合成码出生地（`paid_requests.py` 的接受闸，不是判据模块）。
SYNTHESIS_CODES = frozenset({'answer_shape', 'patch_shape'})

#: 材料判据码：由 `common.positive_material_issues` 产 `MaterialIssue`，
#: `materials.validate_design_materials` 按站点换成 wire 路径（CS-F1）。
MATERIAL_CODES = frozenset({'positive_material_leak', 'internal_id_leak'})

#: 由下层 NamedTuple 携带、路径在 design.py 消费点补齐的读取闭合码。
WRAPPED_CODES = frozenset({'design_read_unestablished', 'design_read_unknown'})

#: 整份出口闭集（RC-59）：协议字面量/整体形状。局部称谓、单条义务、编译缺陷
#: 不得进入——它们必须落在 locate/optional。
WHOLE_ANSWER_ALLOWED = frozenset(SYNTHESIS_CODES) | {
    'design_protocol_literal', 'hook_protocol_literal',
    'design_wire_shape', 'hook_ref_shape', 'hook_wire_key_whitelist',
    'book_design_construct', 'hook_construct',
}

#: 执行面控制码（`paid_requests.py` 的预算/账务/停止族）：它们不是「判据对内容
#: 的裁决」，没有 wire 编辑位置，按现役停止规则走（预算耗尽、账不闭合、无进展、
#: 轮数耗尽）。RC-59 的整份闭集只收内容侧形状失败，故这些码不在 `PATCH_ROUTES`
#: 内——枚举时显式排除，避免把「运行边界」误登记成「修复路由」。
EXECUTION_CONTROL_CODES = frozenset({
    'adoption_content', 'adoption_cost_link', 'adoption_identity',
    'adoption_ledger_tail', 'adoption_status', 'attempt_exhausted',
    'book_budget_exhausted', 'book_call_ceiling', 'carried_forward_attempt',
    'carried_forward_cost_link', 'carried_forward_paid_calls',
    'carried_forward_request_payload', 'carried_forward_turn',
    'delivery_exhausted', 'delivery_refused', 'delivery_unknown',
    'ledger_tail', 'purchase_required', 'revision_evidence_invalid',
    'truncated', 'turn_not_open', 'unknown_turn', 'validation_no_progress',
    'validation_rounds_exhausted',
})


# ───────────────────────── 枚举：码字面量 ↔ 路由表 ─────────────────────────

def _literal(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _code_literals(path: Path) -> set[str]:
    """源码里的规则码字面量：``code=…`` 关键字；``MaterialIssue(CODE, …)``／
    ``OwnerIssueV1(code=CODE)``／``ReadViewError(CODE, …)`` 首参；以及判据私有
    构造器（``_lock_issue(msg, CODE, …)``／``compile_issue``／``_binding_issue``）
    第二位置实参——这些是判据侧既有签名形状，不为测试改签名。"""
    tree = ast.parse(path.read_text(encoding='utf-8'))
    positional_second = ('_lock_issue', 'compile_issue', '_binding_issue')
    positional_first = ('MaterialIssue', 'ReadViewError', 'LockOrderViolation')
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg == 'code':
                value = _literal(keyword.value)
                if value:
                    found.add(value)
                # ``code: str = 'x'`` 形参默认值也是该码的出生地（`fact_bindings`
                # 的局部别名与 `_reject_unknown_keys` 的缺省码）。
                elif isinstance(keyword.value, ast.Constant):
                    found.add(keyword.value.value)
        name = getattr(node.func, 'id', '') or getattr(node.func, 'attr', '')
        if len(node.args) >= 2 and (
                name in positional_second or re.fullmatch(r'_?\w*issue', name)):
            value = _literal(node.args[1])
            if value:
                found.add(value)
        if node.args and name in positional_first:
            value = _literal(node.args[0])
            if value:
                found.add(value)
    # 函数定义的码默认值（`code: str = 'wire_key_whitelist'`）也是出生地：
    # 该私有构造器被多处调用却不显式传码。
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for default in [*node.args.kw_defaults, *node.args.defaults]:
                value = _literal(default) if default is not None else None
                if value:
                    found.add(value)
        # `code = 'x'`（判据先算码再传入构造器，如 `fact_bindings` 的分支别名）
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == 'code':
                    value = _literal(node.value)
                    if value:
                        found.add(value)
    return found


def _emitted_codes() -> set[str]:
    codes: set[str] = set()
    for rel in JUDGMENT_MODULES:
        codes |= _code_literals(REPO / rel)
    codes |= SYNTHESIS_CODES | DISPATCH_CODES
    return codes - EXECUTION_CONTROL_CODES


def test_route_table_is_one_to_one_with_live_judgment_codes():
    """PATCH_ROUTES 的键集合 == 判据模块与接受闸在源码里出现的码集合。

    判据加了码而表里没有（进不了窄修通道、只能整份重生或崩溃）或表里登记了
    已不存在的码（路由失真）都当场红——「清点做完」的可机检面。执行面控制码
    （预算/账务/停止族）显式排除：它们没有 wire 编辑位置，按现役停止规则走。
    """
    emitted = _emitted_codes()
    registered = set(PATCH_ROUTES)
    missing = sorted(emitted - registered)
    stale = sorted(registered - emitted)
    assert not missing, f'这些现役规则码没有登记修复路由（RC-59 无法定位即停的前置）：{missing}'
    assert not stale, f'PATCH_ROUTES 登记了现役判据不产出的码：{stale}'


def test_route_table_has_no_duplicate_literal_keys_and_uses_the_four_dispositions():
    """字面量去重（Python dict 静默吞重复键）＋四态取值闭集。"""
    tree = ast.parse((REPO / 'contracts/judges/str06.py').read_text(encoding='utf-8'))
    table = next(node for node in ast.walk(tree)
                 if isinstance(node, ast.AnnAssign)
                 and getattr(node.target, 'id', '') == 'PATCH_ROUTES')
    keys = [element.value for element in table.value.keys]  # type: ignore[attr-defined]
    assert len(keys) == len(set(keys)), f'PATCH_ROUTES 有重复键：{sorted(k for k in set(keys) if keys.count(k) > 1)}'
    routes = {element.value for element in table.value.values}  # type: ignore[attr-defined]
    assert routes <= {'locate', 'optional', 'whole_answer', 'stop'}, routes
    assert len(PATCH_ROUTES) == len(keys)


def test_whole_answer_route_is_confined_to_the_closed_set():
    """整份出口只有：JSON 回答无法解码/整体形状失败/协议字面量不符（RC-59）。

    局部称谓（`*_material_leak`）、单条义务（kp/kf/fact_lock 家族）、编译缺陷
    （guard_*／compile_*）即使形状像「整体」，也不得登记为整份原因。
    """
    whole = {code for code, route in PATCH_ROUTES.items() if route == 'whole_answer'}
    assert whole <= WHOLE_ANSWER_ALLOWED, sorted(whole - WHOLE_ANSWER_ALLOWED)
    banned = [code for code in whole
              if code.startswith(('guard_', 'compile_', 'design_', 'hook_'))
              and code not in WHOLE_ANSWER_ALLOWED]
    assert not banned, f'局部/编译类规则码不得进整份闭集：{banned}'
    # 合成码必须真的在整份闭集里（接受闸把不可解码回答归到它）。
    assert SYNTHESIS_CODES <= whole


def test_stop_route_covers_faces_without_a_design_wire_edit_position():
    """stop 态＝没有本节点编辑面：写前 Guard、compile 自洽性、bind 派生违例。"""
    stopped = {code for code, route in PATCH_ROUTES.items() if route == 'stop'}
    assert stopped, 'stop 态不能为空——compile/guard 面没有 wire 编辑位置'
    for code in stopped:
        assert code.startswith(('guard_', 'compile_')) or code in (
            'epistemic_lock_order', 'design_real_closed', 'design_real_anchor',
            'must_keep_unbound', 'material_name_leak'), code
    locate_or_optional = {code for code, route in PATCH_ROUTES.items()
                          if route in ('locate', 'optional')}
    assert not (stopped & locate_or_optional), '同一规则码不能既有编辑面又停机'


# ───────────────────────── 红例：locate 码真的给出 wire 路径 ─────────────────────────

def _assemble(wire, brief, obligations):
    from novel.stages.stage4.design import assemble_body_design
    return assemble_body_design(
        wire, known_char_ids={'lu_fujun', 'yun_niang', 'pei_shuyuan'},
        expression_requirements=brief.expression,
        source_narration=brief.source_narration, brief=brief,
        obligations=obligations)


def _locating_cases() -> dict[str, tuple]:
    """规则码 → 产出它的最小 v8 wire 变形（坏稿）。与 `_v8_wire` 绿底座同源。

    只收**本文件能稳定触发**的码；其余 locate 码（`design_pov_closed_set`、
    `design_unregistered_surface`、`design_knowledge_lock`、kp/kf 家族、
    `design_confirm_unestablished` 等）由各自的现役反例套件证明带路径——
    `test_str06_design_patch.py`、`test_str06_a4_design_locks.py`、
    `test_str06_target.py` 逐条断言 `wire_paths_of(issue)`。本测试钉的是
    「路由表与判据一一对应」这张网，不复制每条判据的红例。
    """
    from copy import deepcopy
    from tests.test_str06_design_slim import _brief, _obligations, _v8_wire

    def bad_unknown_char():
        wire = _v8_wire()
        wire['scenes'][0]['beats'][0]['who'] = ['ghost']
        return wire

    def bad_chapter_slice():
        wire = _v8_wire()
        wire['chapters'][0]['scenes'] = 3      # 章切片总数与场景总数不符
        return wire

    def bad_scene_weights():
        wire = _v8_wire()
        wire['scene_weights'] = [1.0]        # 与场景等长失败
        return wire

    def bad_expression_ref():
        wire = _v8_wire()
        wire['scenes'][0]['beats'][0]['ex'] = ['ex_99']
        return wire

    def bad_read_unestablished():
        wire = _v8_wire()
        wire['scenes'][1]['reads'] = [{'domain': 'character', 'subjects': ['yun_niang'],
                                       'key': 'ghost_state'}]
        return wire

    cases = {
        'design_unknown_char': bad_unknown_char,
        'design_chapter_slice': bad_chapter_slice,
        'design_scene_weights': bad_scene_weights,
        'design_expression_ref': bad_expression_ref,
        'design_read_unestablished': bad_read_unestablished,
    }
    return {code: (deepcopy(make()), _brief(), _obligations(_brief()))
            for code, make in cases.items()}


def _locate_callsites_without_paths() -> list[tuple[str, str, int]]:
    """静态扫描：登记为 locate 的码，其发射调用必须**在本调用内**给路径。

    认可的形状：``paths=…`` 关键字，或位置实参／``*paths`` 展开超过 location
    （`_issue(msg, code, location, *paths)` 家族）。走不到路径的判据是「只有
    字符串」的旧形状——按 RC-59 它不该混进窄修通道。

    同一码可以有多个发射面（`design_kp_key_closed`：`design.py` 的 delta 行
    带路径，`preflight.py` 的 IR 键扫描不带）。**至少一个发射面带路径**即算
    该码可定位；全部发射面都不带才是洞。
    """
    with_paths: set[str] = set()
    without: list[tuple[str, str, int]] = []
    entries = ('design_issue', 'issue', '_issue', '_guard_issue', '_lock_issue',
               'compile_issue', '_binding_issue')
    for rel in JUDGMENT_MODULES:
        tree = ast.parse((REPO / rel).read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, 'id', '') or getattr(node.func, 'attr', '')
            if name not in entries:
                continue
            code = None
            if len(node.args) >= 2:
                code = _literal(node.args[1])
            for keyword in node.keywords:
                if keyword.arg == 'code' and _literal(keyword.value):
                    code = _literal(keyword.value)
            if code is None or PATCH_ROUTES.get(code) != 'locate':
                continue
            keywords = {k.arg for k in node.keywords}
            # location 是第 3 个位置实参；其后还有位置实参或星展展开即带路径
            extra_positional = len(node.args) > 3 or any(
                isinstance(a, ast.Starred) for a in node.args)
            if 'paths' in keywords or extra_positional:
                with_paths.add(code)
            else:
                without.append((rel, code, node.lineno))
    return [hole for hole in without if hole[1] not in with_paths]


def test_every_locate_code_attaches_paths_at_its_emission_site():
    """locate 码至少有一个发射面带 paths（静态）——「只有字符串」的判据当场红。"""
    holes = _locate_callsites_without_paths()
    # 豁免：码由下层 NamedTuple（ReadViewError）或 MaterialIssue 携带，路径在
    # 消费点（design.py／materials.py）换算——这两个发射点是纯身份载体。
    wrapped = WRAPPED_CODES | MATERIAL_CODES
    real = [hole for hole in holes if hole[1] not in wrapped]
    assert not real, f'这些 locate 码的发射点没有给 wire 路径：{real}'


def test_locate_codes_emit_wire_paths_on_their_red_examples():
    """每个 locate 码在真实判据入口给出非空 wire 路径（定位与判定同一把尺）。

    两层证据：
    ① 本文件的最小红例直接证明（`_locating_codes` 里的码》；
    ② 结构性扫描：其余 locate 码的**发射点**必须经过带 paths 的构造器
       （`design_issue(..., paths=…)`／`_lock_issue(msg, code, loc, *paths)`），
       即「该码存在 ⇒ 它出生的那个调用点有 paths 形参」——防止新增一个码却
       只有字符串没有路径。反例套件（`test_str06_design_patch.py` 等）对各码
       的红例逐条断言实际路径值，与这里互为补充。
    """
    plan = _locating_cases()
    covered: set[str] = set()
    for code, (wire, brief, obligations) in plan.items():
        _body, errors = _assemble(wire, brief, obligations)
        matched = [e for e in errors if getattr(e, 'code', None) == code]
        assert matched, f'{code} 的红例没有触发该规则码：{[str(e)[:60] for e in errors]}'
        for issue in matched:
            assert wire_paths_of(issue), f'{code} 的问题没有 wire 路径（RC-57 只发目标路径）'
        covered.add(code)
    assert covered, '本文件至少要有一个直接红例'
    locate = {code for code, route in PATCH_ROUTES.items() if route == 'locate'}
    # 结构性扫描：发射点走过 paths 形参（`design_issue` 统一入口）。
    for code in locate:
        assert _emission_site_passes_paths(code), (
            f'{code} 登记为 locate，但发射点没有 paths 形参——判据只有字符串'
            '没有路径，按 RC-59 应停机报码或改为 optional/whole_answer')
    assert _locating_cases().keys() <= locate, '红例码必须都是 locate 态'


def _emission_site_passes_paths(code: str) -> bool:
    """该码的发射调用是否经过带 `paths` 形参的构造器。

    `design_issue` 是 Design 判据的统一入口（`paths` 恒在签名里）；`design.py`
    内的局部别名 `issue(...)` 与 `hook_validation._issue`／`guard._guard_issue`
    同型（都转调 `design_issue`）。判据只要走这些入口就具备给路径的能力——
    实际给不给由各反例断言，这里只验「没有绕过入口」。
    """
    for rel in JUDGMENT_MODULES:
        tree = ast.parse((REPO / rel).read_text(encoding='utf-8'))
        defaults = _module_local_codes(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, 'id', '') or getattr(node.func, 'attr', '')
            if name not in ('design_issue', 'issue', '_issue', '_guard_issue',
                            '_lock_issue', 'compile_issue', '_binding_issue'):
                continue
            args = [_literal(a) for a in node.args]
            keywords = {k.arg: _literal(k.value) for k in node.keywords if k.arg == 'code'}
            emitted = {v for v in args[1:2] if v} | set(keywords.values())
            # 码来自变量（`code=code`／`issue(msg, err.code, …)`）：按「走统一
            # 入口」判——变量本身在该文件里被赋成字面量（见 `_module_default_codes`
            # 与赋值扫描），路径形参入口不变。
            variable_code = any(k.arg == 'code' and isinstance(k.value, ast.Name)
                                for k in node.keywords) or (
                len(node.args) >= 2 and isinstance(node.args[1], ast.Name))
            if code in emitted or (variable_code and code in defaults):
                return True
    # 码来自下层 NamedTuple（`ReadViewError(CODE, read, msg)` in `common.py`），
    # wire 路径在 `design.py` 的消费点补齐（`issue(err.message, err.code, …)`）——
    # 上游只产身份与消息，路径由协议同序的行号换算（`read_closure` 注释同）。
    if code in WRAPPED_CODES:
        return True
    # 材料判据面：`MaterialIssue(CODE, …)` 由 `materials.validate_design_materials`
    # 按站点换成 wire 路径（CS-F1 的站点→路径解析器；CS-H H3 起残余码在
    # materials.py 就地铸 `design_issue(..., paths=…)`）。
    if code in MATERIAL_CODES:
        source = (REPO / 'novel/stages/stage4/materials.py').read_text(encoding='utf-8')
        return ('code = issue.code' in source
                or 'code=issue.code' in source)
    return False


def _module_default_codes(tree: ast.Module) -> set[str]:
    """模块级构造器默认码（`code: str = 'wire_key_whitelist'` 等）与
    `code = 'x'` 局部赋值（`fact_bindings` 的分支别名）。"""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for default in [*node.args.kw_defaults, *node.args.defaults]:
                value = _literal(default) if default is not None else None
                if value:
                    found.add(value)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == 'code':
                    value = _literal(node.value)
                    if value:
                        found.add(value)
    return found


#: 兼容名（`_emission_site_passes_paths` 用）。
_module_local_codes = _module_default_codes


def test_hook_locate_codes_emit_wire_paths():
    """Hook wire 判据（hook_validation）与 Design 共用同一构造约定与定位器。"""
    from novel.stages.stage4.hook import assemble_hook_design
    from tests.test_str06_hook import _body_wire, _hook_wire
    from novel.stages.stage4.design import assemble_body_design

    body, errors = assemble_body_design(
        _body_wire(), known_char_ids={'fu_jun', 'yun_niang'},
        expression_requirements=_requirements(), source_narration=_narration())
    assert body is not None and not errors, errors
    bad = _hook_wire()
    bad['pov'] = {'person': 'first', 'focalizers': ['ghost']}      # hook_pov_char
    bad['materials'][0]['withheld'] = [{'domain': 'character',
                                        'subjects': ['nope'], 'key': 'k'}]  # hook_withheld_ref
    _hook, found = assemble_hook_design(
        bad, body=body, known_char_ids={'fu_jun', 'yun_niang'},
        name_projection=_hook_projection(), name_structure=_hook_structure())
    matched = [e for e in found if getattr(e, 'code', None) in (
        'hook_pov_char', 'hook_withheld_ref')]
    assert matched, f'Hook 红例没有触发定位码：{[str(e)[:60] for e in found]}'
    for issue in matched:
        assert wire_paths_of(issue), 'Hook 问题没有 wire 路径'
    paths = {wire_paths_of(e)[0] for e in matched if wire_paths_of(e)}
    assert ('pov', 'focalizers', 0) in paths and ('materials', 0) in paths, paths


def _requirements():
    from tests.str06_expression_inputs import sample_requirements
    return sample_requirements()


def _narration():
    from tests.str06_expression_inputs import sample_narration
    return sample_narration()


def _hook_projection():
    from tests.test_str06_hook import _projection
    return _projection()


def _hook_structure():
    from tests.test_str06_hook import _characters
    return _characters().name_structure


def test_unlocatable_codes_stop_instead_of_rewriting_the_whole_answer():
    """无法定位的规则码（未登记/无路径）即停并报码，不进整份重生。

    现役入口的形状：`ask_json` 只在错误可定位时走 patch；`compile` 面的
    guard_*／compile_* 码没有 wire 编辑位置，按 `stop` 态由所在面回派或停机。
    这里钉住「无路径 ⇒ 不产生整份返修」这条接口约定。
    """
    from novel.stages.stage4.design_paths import design_issue

    stop_codes = {code for code, route in PATCH_ROUTES.items() if route == 'stop'}
    assert stop_codes
    for code in ('guard_m3_early_effect', 'compile_dependency_cycle'):
        issue = design_issue('示例', code=code, location=('x',))
        assert code in PATCH_ROUTES and PATCH_ROUTES[code] == 'stop'
        assert not wire_paths_of(issue), 'stop 码不带 wire 路径'
    # 整份闭集码（合成）也不带 wire 路径——它们本来就不修内容，重问整份。
    for code in SYNTHESIS_CODES:
        assert PATCH_ROUTES[code] == 'whole_answer'
