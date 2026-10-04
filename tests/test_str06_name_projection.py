"""CS-F2 称谓投影（RC-40／RC-41／RC-42／RC-43）：确定性、可观测、只处理已证明的绑定。

边界（与 `test_str06_material_repair.py` 的归因反例分工）：
· 绑定表（compile）：只收**完整登记称谓**——登记 surface、含姓名部件、全书唯一属于一个
  人物、长度≥2、不是姓／家族构词；纯称谓不在禁用面，短姓、关系称呼、多义字面、未登记
  表达一律没有绑定；
· 渲染期投影：按本场已释放称谓替换，已释放字面先认领保护，更长的禁用字面内部的子串不被
  单独替换；人物本场没有可用称谓时不替换并如实留下泄漏；
· 原字段、人物来源与内部身份不变；留痕可观测；同输入恒同输出。
"""
from copy import deepcopy

import pytest

from contracts.schema.book_ir import ChapterIR, WithheldNameBinding
from contracts.schema.source import NameStructure
from novel.core.name_materials import withheld_name_bindings
from novel.stages.common import project_withheld_names
from tests.test_str06_hook import _characters


def _structure() -> NameStructure:
    return _characters().name_structure


# ───────────────────────── 绑定表：只收已证明的完整登记称谓 ─────────────────────────

def test_bindings_contain_only_registered_unique_name_bearing_surfaces():
    structure = _structure()
    withheld = ['陆大哥', '陆兄', '陆', '陆家', '苏芸娘', '芸娘', '苏姑娘', '苏', '陆郎']
    bound = {literal: (who, enclosing)
             for literal, who, enclosing in withheld_name_bindings(withheld, structure)}
    assert {k: v[0] for k, v in bound.items()} == {
        '陆大哥': 'fu_jun', '陆兄': 'fu_jun',
        '苏芸娘': 'yun_niang', '芸娘': 'yun_niang', '苏姑娘': 'yun_niang'}
    assert '陆' not in bound and '苏' not in bound, '短姓不推断人物'
    assert '陆家' not in bound, '家族构词不归个人'
    assert '陆郎' not in bound, '未登记表达（源文没有的称呼）不据相似性绑定'
    # 包含绑定字面的更长称谓预先算好：芸娘 ⊂ 苏芸娘；已释放的更长称谓同样计入。
    assert bound['芸娘'][1] == ('苏芸娘',)
    released_longer = {literal: enclosing for literal, _who, enclosing in
                       withheld_name_bindings(['陆大哥'], structure, known=['陆大哥夫人'])}
    assert released_longer['陆大哥'] == ('陆大哥夫人',)


def test_pure_appellations_and_ambiguous_literals_are_never_bound():
    from contracts.schema.source import NameStructure
    data = _structure().model_dump(mode='json')
    # 多义：同一字面登记给两个人物（如两位沈公子）。
    data['people']['yun_niang']['surface_forms']['陆大哥'] = '{family}大哥'
    structure = NameStructure.model_validate(data)
    assert '陆大哥' not in {b[0] for b in withheld_name_bindings(['陆大哥', '陆兄'], structure)}
    assert {b[0]: b[1] for b in withheld_name_bindings(['夫君', '陆兄'], _structure())} == {
        '陆兄': 'fu_jun'}, '纯称谓（夫君）没有姓名部件，不进绑定'
    assert withheld_name_bindings(['陆大哥'], None) == (), '没有名称结构就没有可证明的绑定'


def test_chapter_ir_rejects_duplicate_binding_literals():
    with pytest.raises(ValueError, match='重复'):
        _chapter(withheld=('陆大哥',), bindings=(WithheldNameBinding(literal='陆大哥', char_id='fu_jun'),) * 2)


def _chapter(*, withheld, bindings):
    from tests.test_str06_r10b_writer_packet import _ir
    _body, _binding, _plan, ir = _ir()
    base = ir.chapters[0].model_dump(mode='python')
    base.update(withheld_literals=withheld, withheld_bindings=bindings)
    return ChapterIR.model_validate(base)


# ───────────────────────── 渲染期投影：纯函数反例 ─────────────────────────

BINDINGS = (WithheldNameBinding(literal='陆大哥', char_id='fu_jun', enclosing=('陆大哥家',)),
            WithheldNameBinding(literal='陆兄', char_id='fu_jun'),
            WithheldNameBinding(literal='苏芸娘', char_id='yun_niang'))


def _project(text, *, bindings=BINDINGS, display=None):
    return project_withheld_names(
        text, bindings=bindings, display={'fu_jun': '夫君'} if display is None else display)


def test_full_registered_surface_becomes_the_allowed_surface_and_is_recorded():
    text, hits = _project('陆大哥是陆兄的旧识，二人同在陆大哥门前。')
    assert text == '夫君是夫君的旧识，二人同在夫君门前。'
    assert [(h.literal, h.char_id, h.replacement) for h in hits] == [
        ('陆大哥', 'fu_jun', '夫君'), ('陆兄', 'fu_jun', '夫君'), ('陆大哥', 'fu_jun', '夫君')]


def test_unbound_literals_are_reported_by_the_checker_not_rewritten():
    """RC-41：多义短姓、未登记的陆郎、家族构词不被全局替换——保持原样留给字面检查。"""
    text = '陆郎与陆家的陆在院中；陆大哥在门口。'
    projected, hits = _project(text)
    assert projected == '陆郎与陆家的陆在院中；夫君在门口。'
    assert [h.literal for h in hits] == ['陆大哥']
    assert '陆' in projected and '陆郎' in projected and '陆家' in projected


def test_released_literals_are_never_bindings_and_longer_titles_are_not_split():
    # 已释放的完整称谓本来就不在绑定表里（绑定只收本章尚未释放的）：本场已可用『陆兄』时
    # 去掉它的绑定，文本里的『陆兄』原样保留，只有仍被禁用的『陆大哥』被换成夫君。
    only_big_brother = tuple(b for b in BINDINGS if b.literal != '陆兄')
    text, hits = _project('陆兄来了，陆大哥也来了。', bindings=only_big_brother)
    assert text == '陆兄来了，夫君也来了。' and [h.literal for h in hits] == ['陆大哥']
    # 包含绑定字面的更长称谓（enclosing：陆大哥家）先被认领并原样保留，内部的绑定子串
    # 不被单独替换；渲染只依赖绑定表，不依赖验证材料字段。
    text, hits = _project('在陆大哥家门前，陆大哥来了')
    assert text == '在陆大哥家门前，夫君来了' and [h.literal for h in hits] == ['陆大哥']


def test_no_allowed_surface_in_scene_leaves_the_literal_for_the_leak_check():
    text, hits = _project('陆大哥进了门。', display={})
    assert text == '陆大哥进了门。' and hits == ()
    text, hits = _project('陆大哥进了门。', display={'fu_jun': ''})
    assert text == '陆大哥进了门。' and hits == (), '空称谓不当作可用'


def test_projection_is_pure_deterministic_and_idempotent():
    bindings = deepcopy(BINDINGS)
    first = _project('陆大哥与苏芸娘。', display={'fu_jun': '夫君', 'yun_niang': '芸娘'})
    assert first == _project('陆大哥与苏芸娘。', display={'fu_jun': '夫君', 'yun_niang': '芸娘'})
    text, _ = first
    assert text == '夫君与芸娘。'
    again, hits = _project(text, display={'fu_jun': '夫君', 'yun_niang': '芸娘'})
    assert again == text and hits == (), '已是允许称谓的文本再投影不变'
    assert BINDINGS == bindings, '不改调用方的映射'
    assert _project('', display={'fu_jun': '夫君'}) == ('', ())
    assert project_withheld_names('陆大哥', bindings=(), display={'fu_jun': '夫君'}) == (
        '陆大哥', ())


# ───────────────────────── 真实章包：书级行与场景行都按本场称谓呈现 ─────────────────────────

def test_writer_packet_projects_scene_and_book_level_lines_and_keeps_sources_original():
    from novel.stages.common import (
        build_writer_packet, project_chapter_packet, render_chapter_packet,
        writer_material_issues, writer_name_projections,
    )
    from tests.test_str06_material_repair import _conflict
    from novel.stages.stage4 import inspect_body_materials
    body, binding, brief, chars = _conflict(ch1_has_allowed_surface=True)
    view = inspect_body_materials(body, binding, brief, chars, budget=4000, length_band=(0.6, 1.3))
    packet = project_chapter_packet(view.ir, 1)
    assert [b.literal for b in packet.chapter.withheld_bindings] == sorted(
        b.literal for b in packet.chapter.withheld_bindings)
    assert {b.literal for b in packet.chapter.withheld_bindings} >= {'陆大哥', '陆兄'}
    text = render_chapter_packet(packet)
    assert '陆大哥' not in text and '陆兄' not in text
    # 留痕：分区、字面、人物、替换结果；不含行文本。
    rows = writer_name_projections(packet)
    assert rows and set(rows[0]) == {'partition', 'literal', 'char_id', 'replacement'}
    assert {(r['literal'], r['replacement']) for r in rows} == {('陆大哥', '夫君')}
    # 字面检查看到的是投影后的送达文本：没有泄漏；原正式产物不变。
    assert writer_material_issues(packet, internal_ids=set()) == ()
    assert brief.propositions[0].statement == '陆大哥在暗中查访旧案'
    assert build_writer_packet(packet) == build_writer_packet(packet)


def test_chapter_identity_binds_projection_inputs_and_rendered_text():
    """写手视图摘要含投影后的渲染字节；验证输入摘要含绑定表——任一变化都使该章回执失效。"""
    from novel.stages.common import chapter_identities, project_chapter_packet
    from tests.test_str06_material_repair import _conflict
    from novel.stages.stage4 import inspect_body_materials
    body, binding, brief, chars = _conflict(ch1_has_allowed_surface=True)
    view = inspect_body_materials(body, binding, brief, chars, budget=4000, length_band=(0.6, 1.3))
    packet = project_chapter_packet(view.ir, 1)
    base = chapter_identities(packet)
    no_binding = packet.model_copy(update={'chapter': packet.chapter.model_copy(
        update={'withheld_bindings': ()})})
    changed = chapter_identities(no_binding)
    assert changed.validation_sha256 != base.validation_sha256
    assert changed.writer_view_sha256 != base.writer_view_sha256, '不投影＝写手看到的字节不同'


# ───────────────────── RC-42／RC-43：登记闭合与释放位置不由编译器代选 ─────────────────────

def test_unregistered_alias_is_refused_for_lacking_registration_not_for_zero_occurrence(tmp_path):
    """RC-42：称谓可用性取决于登记与消费闭合；Design 放行未登记的『陆郎』按「未登记面」
    拒绝（不新增别名、不据字面出现次数判定），已登记的『陆大哥』不受影响。"""
    from novel.stages.stage4 import inspect_design_candidate
    from tests.test_str06_candidate_preflight import _prepare
    _, wires, _, args = _prepare(tmp_path)
    wires['design']['scenes'][1]['beats'][0]['release'] = [
        {'char_id': 'fu_jun', 'surfaces': ['陆郎']}]
    view = inspect_design_candidate(wires['design'], **args)
    message = next(e for e in view.errors if '陆郎' in e)
    assert '未登记面' in message and '出现' not in message
    wires['design']['scenes'][1]['beats'][0]['release'] = [
        {'char_id': 'fu_jun', 'surfaces': ['陆大哥']}]
    assert not any('未登记面' in e for e in inspect_design_candidate(
        wires['design'], **args).errors)


def test_compiler_never_releases_a_full_name_early_to_clear_a_conflict(tmp_path):
    """RC-43：Design 自有文本在第1章使用尚未释放的『陆大哥』——冲突如实报告，由 Design
    重选释放位置或改写材料；compiler 不自动提前释放，候选与已构造的释放表保持原声明。"""
    from copy import deepcopy
    from novel.stages.stage4 import chapter_surface_releases, inspect_design_candidate
    from tests.test_str06_candidate_preflight import _bad_design, _prepare
    _, wires, _, args = _prepare(tmp_path)
    wire = wires['design']
    _bad_design(wire)
    before = deepcopy(wire)
    view = inspect_design_candidate(wire, **args)
    assert any('陆大哥' in e for e in view.errors), '冲突被如实报告'
    assert wire == before, '候选 wire 不被改写'
    assert view.body is not None
    assert '陆大哥' not in chapter_surface_releases(view.body, 1, args['chars'].name_structure)
    assert '陆大哥' in chapter_surface_releases(view.body, 2, args['chars'].name_structure), \
        '释放位置保持 Design 原声明（第2章）'
