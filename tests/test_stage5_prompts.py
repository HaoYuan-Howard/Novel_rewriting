#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 5 唯一闭卷写作契约。"""
from __future__ import annotations

import ast
import hashlib
import inspect
from pathlib import Path
import subprocess

import pytest

from novel.stages.stage5 import prompts
from tests import factories


_EVENT_LIST_V1_COMMIT = '98368925da9057b437b8a43a74b02433546b2192'
_EVENT_LIST_V1_SYS_SHA256 = 'd3303359f45dc4323e71f353b13c4ed45e40120ad1b3088d64b0b593ff25cf7e'
_EVENT_LIST_V1_USER_SHA256 = 'a186b34aca24cb0fca40ca22bc79e3fa59b2812db2e908c3cea82bd30c91d491'


@pytest.fixture(scope='module')
def book():
    """合成的当前形态 Book（不依赖盘上语料）。"""
    return factories.stage5_book()


def test_writer_uses_the_single_current_system():
    system = prompts.write_system()
    from novel.llm.prompts import with_json_shape
    assert system == with_json_shape(
        prompts.WRITE_SYS, prompts.CHAPTER_RESPONSE_HINT)
    assert system and '$' not in system


def test_stage5_production_entry_stays_selector_free_by_default():
    from novel.stages.stage5 import write

    assert list(inspect.signature(prompts.write_system).parameters) == ['revise']
    assert list(inspect.signature(prompts.build_material).parameters) == [
        'entry', 'book']
    assert list(inspect.signature(write.run_write).parameters) == [
        'ctx', 'client', 'chapters', 'write_contract']
    assert list(inspect.signature(write.run_revise).parameters) == [
        'ctx', 'client', 'chapters', 'write_contract']
    assert inspect.signature(write.run_write).parameters['write_contract'].default is None
    assert inspect.signature(write.run_revise).parameters['write_contract'].default is None
    assert not hasattr(prompts, 'SHALLOW_SYS')
    assert not hasattr(prompts, 'DEEP_SYS')
    assert not hasattr(write, 'run_selected')


def _historical_event_list_v1_namespace() -> dict[str, object]:
    """从冻结提交实际载入旧 producer；不在测试里手抄一份“看起来相同”的旧实现。"""
    root = Path(__file__).resolve().parents[1]
    shown = subprocess.run(
        [
            'git', '-c', f'safe.directory={root.as_posix()}', 'show',
            f'{_EVENT_LIST_V1_COMMIT}:novel/stages/stage5/prompts.py',
        ],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding='utf-8',
    ).stdout
    tree = ast.parse(shown)
    assignments = {
        'LAYER_RULE', 'HINGE_LABEL_RULE', 'FIDELITY_RULE', 'ORDER_RULE',
        'STYLE_WORD_RULE', 'DEEP_SYS', 'SHALLOW_SYS', 'REVISE_TAIL',
        'CHAPTER_RESPONSE_HINT', '_SYS_OF',
    }
    functions = {
        'write_system', 'prev_context', 'chapter_facts', 'dialogue_block',
        'build_material',
    }
    selected: list[ast.stmt] = []
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = {target.id for target in targets if isinstance(target, ast.Name)}
            if names & assignments:
                selected.append(node)
        elif isinstance(node, ast.ClassDef) and node.name == 'Material':
            selected.append(node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in functions:
            selected.append(node)

    namespace = dict(vars(prompts))
    namespace['DEPTH_MODES'] = ('deep', 'shallow')
    exec(compile(ast.Module(body=selected, type_ignores=[]),
                 f'{_EVENT_LIST_V1_COMMIT}:prompts.py', 'exec'), namespace)
    return namespace


def test_event_list_v1_executes_the_frozen_historical_sys_and_user_bytes(book):
    """旧臂等于历史 producer 的实际输出，不靠“只改了三块”的推测。"""
    old = _historical_event_list_v1_namespace()
    entry = book.chapters[1]

    old_system = old['write_system']('deep')
    old_material = old['build_material'](entry, book, with_source_text=False)
    old_user = '\n\n'.join(old_material.blocks)
    selected_system = prompts.experimental_write_system('event-list-v1')
    selected_user = prompts.build_user(
        prompts.experimental_build_material('event-list-v1', entry, book))

    assert selected_system == old_system
    assert selected_user == old_user
    assert len(selected_system.encode('utf-8')) == 8256
    assert len(selected_user.encode('utf-8')) == 3682
    assert hashlib.sha256(selected_system.encode()).hexdigest() == _EVENT_LIST_V1_SYS_SHA256
    assert hashlib.sha256(selected_user.encode()).hexdigest() == _EVENT_LIST_V1_USER_SHA256


def test_beat_material_v2_selector_is_byte_identical_to_no_flag_production(book):
    """实验选择器不能改动未传 flag 的正式生产字节。"""
    entry = book.chapters[1]
    assert prompts.experimental_write_system('beat-material-v2') == prompts.write_system()
    assert prompts.build_user(
        prompts.experimental_build_material('beat-material-v2', entry, book)
    ) == prompts.build_user(prompts.build_material(entry, book))


def test_unknown_experiment_contract_never_falls_back_to_production(book):
    with pytest.raises(ValueError, match='未知 Stage5 write contract'):
        prompts.experimental_write_system('typo-v9')
    with pytest.raises(ValueError, match='未知 Stage5 write contract'):
        prompts.experimental_build_material('typo-v9', book.chapters[1], book)


@pytest.mark.parametrize('fragment', [
    'HINGE_LABEL_RULE', 'FIDELITY_RULE', 'ORDER_RULE', 'STYLE_WORD_RULE',
])
def test_shared_rules_reach_the_writer(fragment):
    value = getattr(prompts, fragment)
    assert value in prompts.WRITE_SYS


def test_the_trim_surface_is_gone_and_stays_gone():
    """收缩臂整臂下线 ⇒ 本层只剩写作那一个付费 surface。

    ⚠ 这条是**反向**守卫：不是「trim prompt 还对不对」，而是「它不许回来」。
    一份没有调用者的 system 会被下一个人当成现役口径读，而它描述的那条路
    （校准双盲后采用候选）在本仓已经不存在——那正是「退役的描述以现役身份被引用」。
    要恢复就连 `TrimJudgeResponse` 与 profile 一起恢复，见 `write.py` 模块头。
    """
    for gone in ('trim_system', 'trim_judge_system', 'trim_user', 'trim_judge_user',
                 'TRIM_SYS', 'TRIM_JUDGE_SYS', 'TRIM_JUDGE_ROLE_SUFFIX'):
        assert not hasattr(prompts, gone), f'{gone} 回来了，但没有任何调用者'


def test_the_closed_book_writer_receives_the_layer_boundary():
    assert prompts.LAYER_RULE in prompts.WRITE_SYS


def test_revise_tail_has_one_producer_and_a_distinct_fingerprint():
    systems = (prompts.write_system(), prompts.write_system(revise=True))
    assert len({hashlib.sha1(s.encode()).hexdigest() for s in systems}) == 2
    revised = prompts.write_system(revise=True)
    from novel.llm.prompts import with_json_shape
    assert revised == with_json_shape(
        prompts.WRITE_SYS + prompts.REVISE_TAIL,
        prompts.CHAPTER_RESPONSE_HINT,
    )
    assert revised.count(prompts.REVISE_TAIL.strip()) == 1


def test_style_vocabulary_reaches_the_writer_whole():
    from contracts.invariants import STYLE_WORDS

    for word in STYLE_WORDS:
        assert word in prompts.STYLE_WORD_RULE
    assert prompts.STYLE_WORD_RULE in prompts.WRITE_SYS


def test_invention_boundary_contains_the_complete_contract():
    from contracts.invariants import INVENT_ALLOW, INVENT_FORBID

    for item in (*INVENT_FORBID, *INVENT_ALLOW):
        assert item in prompts.FIDELITY_RULE
    assert '判据：' in prompts.FIDELITY_RULE


def test_all_fidelity_labels_are_explained_to_the_writer():
    from contracts.invariants import HINGE_LABEL_PARTS, hinge_heading

    for kind in HINGE_LABEL_PARTS:
        assert f'【{hinge_heading(kind)}】' in prompts.HINGE_LABEL_RULE


def test_writer_system_has_no_arm_selector():
    with pytest.raises(TypeError):
        prompts.write_system('deep')


def _baseline_keys(module) -> set[str]:
    tree = ast.parse(inspect.getsource(module))
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.endswith('_per_1k')
    }


def test_every_density_baseline_used_by_checks_is_rendered_for_the_writer():
    """FC-08 guard: deleting one downsend call must make this test red."""
    from novel.stages.stage5 import checks

    judged = _baseline_keys(checks)
    downsent = _baseline_keys(prompts)
    assert judged == {
        'simile_per_1k', 'xinli_per_1k', 'dash_per_1k', 'short_run_per_1k',
    }
    assert judged <= downsent


def test_density_guidance_contains_each_measured_value_and_key():
    values = {
        'simile_per_1k': 1.11,
        'xinli_per_1k': 2.22,
        'dash_per_1k': 3.33,
        'short_run_per_1k': 4.44,
    }

    class Book:
        def baseline(self, key, _fn):
            return values[key]

    rendered = prompts.density_guidance(Book())
    for key, value in values.items():
        assert key in rendered and f'{value:.2f}/千字' in rendered


# ─────────────────────────────────────────────────────────────────────────────
# 下发面「换单位」——**events 是材料，不是句子**（2026-08-22，批 1）
#
# ⚠⚠ 本节整节是**新加的守卫，不是补测**：改之前 `chapter_facts` / `prev_context`
#   的渲染**一条断言都没有**，而它们是这一层唯一真正发给写手的东西。
#   量化根因见 `prompts._unit_rule` 的 docstring：`CtxBeat.events[]` 的信息密度是
#   源章正文的 87%，配上等于源章的预算 ⇒ 一条一句地对译是最省力的写法，
#   成书对源章的整句照搬率因此是 50.4%。
# ─────────────────────────────────────────────────────────────────────────────

def _beat(slot: str, delivery: str, events: list[str], *, budget: int = 200, **kw) -> dict:
    beat = {
        'slot': slot, 'delivery': delivery, 'events': list(events),
        'budget_chars': budget, 'source_chars': budget,
        'narrative_role': 'setup', 'transition': 'continue',
        'time_layer': 'present', 'place': '茶馆', 'present': ['沈青'],
        'verbatim': [], 'keep_value': [], 'must_appear': [], 'must_hold': [],
    }
    beat.update(kw)
    return beat


def test_a_scene_beat_is_told_to_write_one_scene_not_one_line_per_event():
    """`scene` 拍：N 条材料 ⇒ **一个连贯的场面**，不是 N 句。"""
    rendered = prompts.chapter_facts({'segments': [
        _beat('s01', 'scene', ['她发现墨迹被换过。', '他把门关上。', '雨下大了。'])]})
    assert '一个连贯的场面' in rendered
    assert '不要一条一句地各写一遍' in rendered
    assert '要写这 3 件事' not in rendered, '旧口径回来了：那句话在物理上就是「各写一句」'


def test_a_summary_beat_is_told_to_merge_and_not_to_stage_it():
    """`summary` 拍：几件事**合并交代过去**，且明说不要演成场面。

    ⚠ 这是「压缩」在本批唯一被授权的形态。实测逐写作单元：`budget/source < 0.8`
    的压缩单元重合中位 45.8%，是展开单元（18.5%）的 **2.5 倍**——
    写手要在更少的字里装下同样的材料，最省力的办法就是把关键句直接搬过来。
    ⇒ 压缩必须靠「合并交代」承载，不能靠「同样几句写短一点」。
    """
    rendered = prompts.chapter_facts({'segments': [
        _beat('s02', 'summary', ['三天过去了。', '账册送到了府里。'], budget=40)]})
    assert '合并交代过去' in rendered
    assert '不要演成场面' in rendered
    assert '不要给它们各写一句' in rendered
    assert '约 40 字' in rendered, '压缩拍必须带着它自己的那个数，不能只给章级区间'


def test_the_two_deliveries_do_not_get_the_same_instruction():
    """`scene` 与 `summary` 拿到的是**两条不同的执行规则**——分流没有退化成同一句。"""
    scene = prompts.chapter_facts({'segments': [_beat('s01', 'scene', ['甲。', '乙。'])]})
    summary = prompts.chapter_facts({'segments': [_beat('s01', 'summary', ['甲。', '乙。'])]})
    assert '一个连贯的场面' in scene and '一个连贯的场面' not in summary
    assert '合并交代过去' in summary and '合并交代过去' not in scene


def test_changing_the_unit_does_not_drop_or_merge_a_single_event():
    """⭐ **换单位不等于少给材料**：每一条 event 仍逐字、逐条、按原序到达写手。

    ⚠ 这条守的是一次真实事故的反面：合并 events 早先是在**计划层**让 LLM 做的，
    实测相似度中位 0.84、最低 0.44，既造事实又丢内容（`ctx_events_verbatim` HARD 为它而设）。
    ⇒ 下发面这一侧**永远不许**替模型合并。
    """
    events = ['她发现墨迹被换过。', '他把门关上。', '雨下大了。', '灯芯被拨亮。']
    for delivery in ('scene', 'summary'):
        rendered = prompts.chapter_facts({'segments': [_beat('s01', delivery, events)]})
        listed = [line.strip()[2:] for line in rendered.split('\n')
                  if line.startswith('   ') and line.strip()[:1] in '①②③④⑤⑥']
        assert listed == events


def test_every_fidelity_point_stays_anchored_to_its_own_beat():
    """⭐⭐ **保真点原地不动**：每一条仍落在它所属那一拍的下面，不许挪到块尾统一列。

    剥离它们的后果是确定的：闭卷写手会丢引号句 ⇒ `verbatim_missing`（HARD）
    直接把这一章打进 `.INVALID`。
    """
    rendered = prompts.chapter_facts({'segments': [
        _beat('s01', 'scene', ['甲。'], verbatim=['第一拍的原话']),
        _beat('s02', 'summary', ['乙。'], verbatim=['第二拍的原话']),
    ]})
    lines = rendered.split('\n')
    at = {needle: next(i for i, line in enumerate(lines) if needle in line)
          for needle in ('slot=s01', 'slot=s02', '第一拍的原话', '第二拍的原话')}
    assert at['slot=s01'] < at['第一拍的原话'] < at['slot=s02'] < at['第二拍的原话']


def test_the_material_block_stops_calling_events_the_content_to_write(book):
    """块头改口径：events 是【材料】，而「不得新增」这条边界**一个字没松**。

    ⚠ 「只写这些」守的是**事实的属性**（不得新增剧情），不是句子单位的属性
    ⇒ 措辞下移成「事实只有这些」，边界保留。
    """
    given = prompts.build_material(book.chapters[1], book).given
    assert '── 本章的材料' in given
    assert '本章要写的内容' not in given, '旧块头回来了'
    assert '事实只有这些' in given and '不得新增剧情' in given
    assert '【事实材料】，不是【要写的句子】' in given


def test_narration_drops_only_null_focalizer_from_the_prompt():
    rendered = prompts.chapter_facts({
        'segments': [_beat('s01', 'scene', ['她进门。'])],
        'narration': [{
            'segment': '全章', 'grammatical_person': 'third',
            'focalizer': None, 'pronoun_surfaces': None,
        }],
    })
    assert 'focalizer' not in rendered
    assert 'pronoun_surfaces' in rendered and 'null' in rendered


def test_the_beat_is_one_unit_rule_reaches_the_writer():
    """一拍 = 一个叙述单位——这条执行规则住在 `ORDER_RULE`。"""
    assert '一拍是一个叙述单位，不是一张待办清单' in prompts.ORDER_RULE
    assert '不要把整章的字数平摊到每一条事实上' in prompts.ORDER_RULE
    assert prompts.ORDER_RULE in prompts.WRITE_SYS


def test_merging_a_beat_into_one_scene_is_not_a_licence_to_reorder_inside_it():
    """⭐⭐ 「揉成一个场面」与「拍内顺序仍照编号」是**一对**，删一句就等于悄悄授权重排。

    ⚠ `checks.segment_event_findings` 有一条拍内顺序臂（「slot 内可确定性定位的事件顺序
    与 producer 顺序相反」→ `event_landing`）。批 1 只换句子单位、**不动那条判据**，
    所以 prompt 这一侧必须把拍内顺序钉住，否则「合并」会顺手把顺序也合掉。
    """
    import inspect

    from novel.stages.stage5 import checks

    assert '一拍之内，编号的先后仍是它们在正文里出现的先后' in prompts.ORDER_RULE
    assert '不要一条一句地各写一遍' in prompts.ORDER_RULE
    # 反向：那条顺序臂**还在**——它一旦被删，上面那句话就没有对手了，要一起重判
    assert 'event_order' in inspect.getsource(checks.segment_event_findings)


# ─────────────────────────────────────────────────────────────────────────────
# 拍**内部**的主次：`spine`（2026-08-22，批 3）
#
# ⚠⚠ 批 1 把句子单位从 event 上移到「拍」，但拍内部仍是一张**平权清单**——
#   而平权清单最省力的写法还是逐条对译，「一件事一句话」只是从章级退到了拍级。
#   `spine` 是这条链的下一环：主干那件演开，其余几件是它的前提、后果或背景。
# ⚠ 它同时是全批最容易被读歪的一格：「以第 3 件为主干」离「把第 3 件写到最前面」只有一步，
#   而那一步跨过去就是 `event_landing`。下面第二条守的就是那一步。
# ─────────────────────────────────────────────────────────────────────────────

def test_a_scene_beat_with_a_spine_is_told_which_event_carries_it():
    """`scene` 拍：主干那一件**演开**，其余几件融进去。"""
    rendered = prompts.chapter_facts({'segments': [
        _beat('s01', 'scene', ['她发现墨迹被换过。', '他把门关上。', '雨下大了。'], spine=2)]})
    assert '以第 2 件为主干把这个场面演开' in rendered
    assert '前提、后果或背景' in rendered
    # 换单位那条规则一个字没让位——两条是叠加的，不是二选一
    assert '不要一条一句地各写一遍' in rendered


def test_the_spine_says_which_one_matters_never_which_one_comes_first():
    """⭐⭐ **主次不是顺序。** 标了主干之后必须紧跟一句「落笔先后仍按编号」。

    ⚠ `stage5/checks.py::segment_event_findings` 那条拍内顺序臂（拍内 event 逆序
    → `event_landing`）在批 3 **一行都没动**，所以下发面这一侧必须自己钉住。
    ⚠ 它与 `ORDER_RULE` 里那句不是重复：那一句在章级规则块里，这一句紧贴着最容易
    诱发重排的那个标记——**重申的位置本身是有效载荷**。
    """
    import inspect

    from novel.stages.stage5 import checks

    for delivery, needle in (('scene', '落笔先后仍按下面的编号'),
                             ('summary', '落笔先后仍按下面的编号')):
        rendered = prompts.chapter_facts({'segments': [
            _beat('s01', delivery, ['甲。', '乙。', '丙。'], spine=3)]})
        assert needle in rendered, f'{delivery} 拍标了主干却没说「只是分量不是位置」'
    assert '主干说的是**分量**不是**位置**' in prompts.chapter_facts({'segments': [
        _beat('s01', 'scene', ['甲。', '乙。'], spine=1)]})
    # 反向：那条顺序臂**还在**——删了它，上面这几句话就没有对手了
    assert 'event_order' in inspect.getsource(checks.segment_event_findings)


def test_a_summary_beat_with_a_spine_is_told_which_fact_must_survive_the_compression():
    """`summary` 拍上主干的含义不同：不是「演开」，是「别把它压没了」。"""
    rendered = prompts.chapter_facts({'segments': [
        _beat('s02', 'summary', ['三天过去了。', '账册送到了府里。'], spine=2, budget=40)]})
    assert '第 2 件是这一拍真正要留下的那一件' in rendered
    assert '演开' not in rendered, 'summary 拍被塞进了 scene 的措辞'
    assert '合并交代过去' in rendered


@pytest.mark.parametrize('beat, why', [
    ({'spine': None}, '计划没做过主次决策（恒等基线 / 存量 ctx）'),
    ({}, '`spine` 这一格压根不在'),
])
def test_a_beat_without_a_spine_says_nothing_about_a_spine(beat, why):
    """⭐ `spine` 缺席时**一个字都不说**——不是补一句「主干是第 1 件」。

    ⚠ `None` 是合法态而不是缺陷：恒等基线按定义就没做过这个决策
    （`measure.identity_plan` 显式给 `None`）。替它补一个 1 等于让基线看起来做过一次
    主次判断，而那份基线正是 `structure_changed` 的反例来源。
    """
    rendered = prompts.chapter_facts({'segments': [
        _beat('s01', 'scene', ['甲。', '乙。'], **beat)]})
    assert '主干' not in rendered, f'{why}，下发面却替它编了一个主干'
    assert '一个连贯的场面' in rendered, '连带把换单位那条规则也弄丢了'


def test_a_single_event_beat_never_gets_a_spine_sentence():
    """只有一件事的拍：`spine=1` 是真的，但说出来是纯噪音，会稀释旁边那几条真规则。"""
    for delivery in ('scene', 'summary'):
        rendered = prompts.chapter_facts({'segments': [
            _beat('s01', delivery, ['只有这一件事。'], spine=1)]})
        assert '主干' not in rendered and '第 1 件' not in rendered


def test_prev_context_gives_goals_and_the_last_beat_not_the_whole_previous_chapter():
    """⭐ 衔接面收窄：上一章的目标 + 结尾 + **末拍**，不再逐条列它每一拍的每一条。

    ⚠ 旧形态等于**把上一章整章重讲一遍再附在本章下发面后面**（events 密度 87%）；
    全书闭卷之后那是第二个淹没下发面的地方。
    ⚠ 末拍**必须**在——它才是「从哪儿接」的那一句；只给两个 goal 是收窄过头。
    """
    from novel.stages.stage5 import measure as M

    prev = {
        'output_ordinal': 1,
        'chapter_goal': {'intent': 'advance', 'facts': ['上一章的目标事实']},
        'end_hook_goal': {'intent': 'close', 'facts': ['上一章的结尾事实']},
        'segments': [
            _beat('s01', 'scene', ['第一拍只该留在上一章的事。']),
            _beat('s02', 'scene', ['中间那一拍的事。']),
            _beat('s03', 'scene', ['上一章停在这个场面上。']),
        ],
    }
    book = M.Book(title='t', ctx={'chapters': [prev]}, style={}, source_text={}, secrets=[])
    rendered = prompts.prev_context({'output_ordinal': 2}, book)

    assert '上一章的目标事实' in rendered and '上一章的结尾事实' in rendered
    assert '上一章停在这个场面上。' in rendered, '末拍是「从哪儿接」的那一句，不许一起收掉'
    assert '第一拍只该留在上一章的事。' not in rendered
    assert '中间那一拍的事。' not in rendered
