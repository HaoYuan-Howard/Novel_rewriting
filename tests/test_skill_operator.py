#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_skill_operator.py —— 把 `novel-pipeline-operator` skill 的漂移守卫接进回归套件。

【这份测试存在的理由】`.claude/skills/novel-pipeline-operator/scripts/validate_skill.py`
写得很全，却**从来没有被任何测试或 CI 调用过**——唯一记载的调用处是它自己的维护协议里
一句「每次修改后从仓根运行」。于是一次盘点在它全绿的情况下查出十三处残留与漂移，其中
四条正在损害执行质量。`docs/LESSONS.md` 第 1 条说的就是这个形状：**约束只有落到可执行
守卫上才算存在**。

【为什么按路径 import 而不是 subprocess】`.claude/skills/*/scripts` 不是包、也不在
`pyproject.toml` 的 `packages` 里，`import` 不到。subprocess 会把失败压成一个退出码加
一坨 stdout，还要处理 Windows 控制台编码；`importlib` 让 pytest 直接展示逐条 error。

【为什么不打 marker】它不读语料、不读产物、不发请求，必须进默认 `-m "not corpus"` 套件。
加 marker 等于给自己留一条「跳过就绿」的路，而那正是这份校验器过去的处境。

【分工】校验器只做「读代码 + 读文档」的静态对账；需要 pydantic 模型实例才能验的事
（`render()` 的真实输出）放这里。两边都不重拼第二份注册表。
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

from contracts.rules import REGISTRY
from contracts.schema.report import finding
from novel.prov import EdgeVerdict, FreshnessReport
from novel.report import build, render
from novel.report.findings import ran, unverifiable

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / '.claude' / 'skills' / 'novel-pipeline-operator'
VALIDATOR = SKILL_DIR / 'scripts' / 'validate_skill.py'


def _validator():
    """按路径加载校验器模块。

    ⚠ 临时关掉 bytecode 写入：否则每跑一次测试就在 skill 目录里长出一个
    `scripts/__pycache__/`，那是本次清理刚删掉的构建残留。
    """
    spec = importlib.util.spec_from_file_location(
        'novel_pipeline_skill_validator', VALIDATOR)
    assert spec and spec.loader, f'加载不了 {VALIDATOR}'
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


# ───────────────────────── 一、校验器本体 ─────────────────────────

def test_the_validator_file_is_where_the_maintenance_protocol_says_it_is():
    """守卫的入口本身要有守卫：文件改名而文档没跟上时，这一条先红。"""
    assert VALIDATOR.is_file(), f'{VALIDATOR} 不在盘上'
    protocol = (SKILL_DIR / 'references' / 'maintenance.md').read_text(encoding='utf-8')
    assert 'scripts/validate_skill.py' in protocol, \
        'maintenance.md 的「校验」小节不再指向 validate_skill.py'


def test_skill_validator_reports_no_drift():
    """skill 文档与代码/自身约定的全部对账。"""
    errors = _validator().validate({})
    assert not errors, (
        '\nnovel-pipeline-operator skill 与代码/自身约定漂移 %d 处。\n'
        '这是 **skill 文档**的问题，不是被它引用的生产代码的问题——\n'
        '不要为了让这条变绿去改 contracts/invariants.py 或 novel/report/render.py。\n'
        '逐条修完后跑：\n'
        '  <PY> -X utf8 .claude/skills/novel-pipeline-operator/scripts/validate_skill.py\n\n'
        % len(errors) + '\n'.join(f'  - {e}' for e in errors))


#: 每个扫描面的下界。**不是等值断言**——注册表本来就会增删；
#: 它防的是另一件事：正则失配、glob 变空、`NODES` 被改空时，
#: 「零个都没查」会伪装成「全部一致」。
_FLOORS = {
    'markdown_files': 8,      # SKILL + 6 reference + 1 模板
    'links': 10,
    'inbox_kinds': 8,         # INBOX_KIND 恒为八类
    'exit_codes': 5,          # 0/1/2/3/70
    'fn_entries': 10,
    'writing_flags': 30,
    'free_writing_pairs': 20,
    'write_surface_rows': 9,  # 九个有 0 成本写盘入口的节点
    'fix_routes': 4,
    'cli_subcommands': 11,
    'status_marks': 4,        # _STATUS_MARK 四态
    'template_headings': 7,
    'evidence_symbols': 5,
}


@pytest.mark.parametrize('key,floor', sorted(_FLOORS.items()))
def test_the_validator_actually_scanned_something(key, floor):
    """守卫的守卫：绿灯必须带扫描分母（`docs/LESSONS.md` 第 3 条）。"""
    counters: dict[str, int] = {}
    _validator().validate(counters)
    assert counters.get(key, 0) >= floor, (
        f'校验器只扫到 {key} = {counters.get(key)}（下界 {floor}）。'
        f'这不是「一致」，是这一面根本没查到东西——先查正则/glob 是不是失配了')


# ───────────────────────── 二、推荐读法的行为守卫 ─────────────────────────

def _documented_filter() -> re.Pattern[str]:
    text = (SKILL_DIR / 'references' / 'runtime-profiles.md').read_text(encoding='utf-8')
    match = re.search(r"Select-String -Pattern '([^']+)'", text)
    assert match, 'runtime-profiles.md 里找不到推荐过滤正则'
    return re.compile(match.group(1))


def _report_with_every_shape():
    """一份四态齐全、带 `.INVALID` 标注、幽灵边与未记录记账键的真报告。

    ⚠ 走 `novel.report.build` + `render`，**不手写样例文本**——
    重拼一份"报告长什么样"就是第二个落点，而守卫要调生产那一个
    （`docs/LESSONS.md` 第 21 条）。判据名也从注册表现取，不写死。
    """
    stage_rules = [r.rule for r in REGISTRY.of_stage('stage3')]
    assert len(stage_rules) >= 3, 'stage3 的判据少于三条，这份样例造不出四态'
    hit, unver, ok = stage_rules[0], stage_rules[1], stage_rules[2]
    ghost = EdgeVerdict(key='stage2/global_grounding.json', state='unknown',
                        reason='ghost', recorded_file='0123456789', now_file=None,
                        recorded_consumed=None, now_consumed=None)
    return build(
        sid='7615069877019300633', node='stage3.style',
        freshness=FreshnessReport('unknown', 'ghost', (ghost,)),
        outcomes=[
            ran(hit, [finding(hit, detail='示例命中', evidence=['b01_01:「证据」'])]),
            unverifiable(unver, why='示例：这一次够不着'),
            ran(ok, []),
            # 其余判据不给 outcome ⇒ 渲染成 `[--]` 没跑成
        ],
        accounting=None,  # ⇒ 常驻记账键全部渲染成 `[????] 未记录`
        invalid=True,
    )


@pytest.mark.parametrize('needle,why', [
    ('[!!]', '命中行'),
    ('[??]', '够不着'),
    ('[--]', '没跑成——压掉它等于把 skipped 洗成 ok'),
    ('[????]', '未记录——「没人说」与「没有」必须分得开'),
    ('── 来源 ──', 'freshness 的唯一出处'),
    ('⚠ 判的这一份是 .INVALID 稿', '把 .INVALID 稿的结论读成正式结论'),
    ('⚠ 幽灵边', '指向的文件已不在盘上'),
    ('合计', '四态分母'),
    ('→  FAIL', 'badge'),
])
def test_documented_report_filter_keeps_every_decisive_line(needle, why):
    """`workflow.md` 说「过滤只改变读法、不改变任何判定」——这一条让那句话是真的。

    实测过一次反例：推荐过滤只接住 `[!!]`/`[??]`/`合计`/badge/`revalidated`，
    于是 `.INVALID` 标注、freshness、幽灵边与两种非 ok 状态**全部被吞**，
    而同一 skill 的经验库正要求必读那行 `.INVALID` 标注。
    """
    text = render(_report_with_every_shape())
    source = [line for line in text.splitlines() if needle in line]
    assert source, f'样例报告里就没有 {needle}（{why}）——这份样例造得不对，先修样例'
    kept = [line for line in source if _documented_filter().search(line)]
    assert kept, (
        f'runtime-profiles.md 的推荐过滤吞掉了 {needle} 那一类行（{why}）。\n'
        f'  被吞的原文：{source[0]!r}\n'
        f'  → 改 runtime-profiles.md 里的正则，不要改 novel/report/render.py')


def test_the_documented_filter_still_filters():
    """反向自检：把正则写成 `.*` 能让上面每条都绿，所以 `[OK]` 必须被滤掉。"""
    text = render(_report_with_every_shape())
    ok_lines = [line for line in text.splitlines() if '[OK]' in line]
    assert ok_lines, '样例报告里没有 [OK] 行，这条反向自检没有被测到'
    assert not [line for line in ok_lines if _documented_filter().search(line)], \
        '推荐过滤连 [OK] 也留下了，那它就没有在过滤'


# ───────────────────────── 三、被 skill 逐字引用的生产事实 ─────────────────────────

def test_the_skill_actor_is_a_single_landing_point():
    """`--actor` 的形态生产侧只要求非空，所以它必须由 skill 定且只定一次。"""
    texts = {path: path.read_text(encoding='utf-8')
             for path in [SKILL_DIR / 'SKILL.md',
                          *sorted((SKILL_DIR / 'references').glob('*.md')),
                          SKILL_DIR / 'assets' / 'final-review-template.md']}
    # 只看**给了占位值**的那些；`--actor --reason` 这种无值列举不算一处形态声明。
    placeholders = {token
                    for body in texts.values()
                    for token in re.findall(r'--actor\s+(<[^>\s]*>)', body)}
    assert placeholders, 'skill 里一处带占位值的 `--actor` 都没有——这条断言什么也没查到'
    assert placeholders == {'<ACTOR>'}, (
        f'`--actor` 在 skill 里出现了不止一种占位形态：{sorted(placeholders)}。\n'
        f'盘上产物已经因此并存两种 actor 写法；形态只能在 SKILL.md 定一次')
