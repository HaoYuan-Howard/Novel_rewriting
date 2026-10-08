"""CS-F2 称谓投影（RC-40／RC-41／RC-42／RC-43）：确定性、可观测、只处理已证明的绑定。

边界（与 `test_str06_material_repair.py` 的归因反例分工）：
· 绑定表（compile）：只收**完整登记称谓**——登记 surface、含姓名部件、全书唯一属于一个
  人物、长度≥2、不是姓／家族构词；纯称谓不在禁用面，短姓、关系称呼、多义字面、未登记
  表达一律没有绑定；
· 渲染期投影：按本场已释放称谓替换，已释放字面先认领保护，更长的禁用字面内部的子串不被
  单独替换；人物本场没有可用称谓时不替换并如实留下泄漏；
· 原字段、人物来源与内部身份不变；留痕可观测；同输入恒同输出；
· Hook（rc16）：释放面由 Hook Design 自选（surface_releases，不是章释放表的投影），
  绑定表随 HookContext 携带、渲染期投影；条目返修的来源归因按原字段文本核对。
"""
from copy import deepcopy

import pytest

from contracts.schema.book_ir import ChapterIR
from contracts.schema.source import NameStructure
from contracts.schema.state import WithheldNameBinding
from novel.core.name_materials import withheld_name_bindings
from novel.stages.common import (
    hook_material_issues, hook_name_projections, project_withheld_names,
    render_hook_context,
)
from novel.stages.stage4.hook_context import compile_hook_context
from novel.stages.stage5.hook_writer import build_hook_user_prompt
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


def _ambiguous_appellation_characters():
    """同一字面既是甲的化名，也是乙的纯职称；两条登记都通过正式 schema。"""
    from contracts.schema.source import CharactersArtifactV6
    data = _characters().model_dump(mode='json')
    for char_id in ('fu_jun', 'yun_niang'):
        data['characters'][char_id]['surfaces'].append({
            'surface': '方丈', 'from_chapter': 1, 'until_chapter': None,
            'source_refs': ['e01_01']})
    person = data['name_structure']['people']['fu_jun']
    person['aliases'].append({'alias_id': 'al_abbot', 'literal': '方丈'})
    person['surface_forms']['方丈'] = '{alias:al_abbot}'
    data['name_structure']['people']['yun_niang']['surface_forms']['方丈'] = None
    return CharactersArtifactV6.model_validate(data)


def test_name_bearing_surface_shared_with_a_pure_appellation_is_not_bound():
    chars = _ambiguous_appellation_characters()
    bindings = withheld_name_bindings(['方丈', '陆大哥'], chars.name_structure)
    assert {literal: char_id for literal, char_id, _ in bindings} == {
        '陆大哥': 'fu_jun'}, '唯一归属必须统计全部登记称谓，不能漏掉纯称谓的同字面'


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
    projected, hits, _blocked = project_withheld_names(
        text, bindings=bindings, display={'fu_jun': '夫君'} if display is None else display)
    return projected, hits


def _project_all(text, *, bindings=BINDINGS, display=None):
    """含无损性守卫拦截留痕的完整投影结果（CS-H H3）。"""
    return project_withheld_names(
        text, bindings=bindings, display={'fu_jun': '夫君'} if display is None else display)


def test_full_registered_surface_becomes_the_allowed_surface_and_is_recorded():
    text, hits = _project('陆大哥来了，陆大哥又走了。')
    assert text == '夫君来了，夫君又走了。'
    assert [(h.literal, h.char_id, h.replacement) for h in hits] == [
        ('陆大哥', 'fu_jun', '夫君'), ('陆大哥', 'fu_jun', '夫君')]


def test_lossless_guard_blocks_two_distinct_forms_of_one_person_in_a_sentence():
    """CS-H H3（RC-60 无损性守卫）：同一句里同一人物两个**不相邻的不同**称谓
    都换成同一显示面会压成一个——不可投影，原文保留并留拦截痕，归 Design；
    同一字面重复出现不触发守卫（上一用例）。"""
    text, hits, blocked = _project_all('陆大哥是陆兄的旧识，二人同在陆大哥门前。')
    assert text == '陆大哥是陆兄的旧识，二人同在陆大哥门前。', '整句不可投影'
    assert hits == ()
    assert {(b.literal, b.char_id) for b in blocked} == {
        ('陆大哥', 'fu_jun'), ('陆兄', 'fu_jun')}
    # 分句边界（；）两侧各自只有一个字面：两句分别投影，不互相拦。
    text, hits, blocked = _project_all('陆大哥进门；陆兄也到了。')
    assert text == '夫君进门；夫君也到了。' and blocked == ()


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
        '陆大哥', (), ())


# ───────────────────────── 真实章包：书级行与场景行都按本场称谓呈现 ─────────────────────────

def test_compiler_keeps_ambiguous_appellations_for_material_validation():
    """RC-41：真实编译与材料判据不能把多义源命题静默改为另一人物的事实。"""
    from novel.stages.common import (
        build_writer_packet, project_chapter_packet, writer_name_projections,
    )
    from novel.stages.stage4 import inspect_body_materials
    from tests.test_str06_r10b_writer_packet import _binding, _design, _requirements
    body, chars, brief = _design(), _ambiguous_appellation_characters(), _requirements()
    binding = _binding(body)
    brief = brief.model_copy(update={'propositions': (
        brief.propositions[0].model_copy(update={'statement': '方丈在佛堂念经'}),)})
    before = [item.model_dump_json() for item in (body, binding, brief, chars)]
    view = inspect_body_materials(body, binding, brief, chars,
                                 budget=4000, length_band=(0.6, 1.3))
    assert not view.errors and view.ir is not None
    packet = project_chapter_packet(view.ir, 1)
    assert '方丈' in packet.chapter.withheld_literals
    assert all(b.literal != '方丈' for b in packet.chapter.withheld_bindings)
    assert any('方丈在佛堂念经' in line for line in build_writer_packet(packet).must_realize)
    assert not any(p['literal'] == '方丈' for p in writer_name_projections(packet))
    assert any(m.literal == '方丈' and any(s.kind == 'knowledge' for s in m.sources)
               for m in view.materials), '多义材料保持原样，由原字段的字面判据如实报告'
    assert [item.model_dump_json() for item in (body, binding, brief, chars)] == before


def test_guard_only_projection_reaches_compile_report_and_survives_restart(tmp_path):
    """RC-40/41：禁区投影经真实 compiler 发布、落报告并在重启读取；禁区仍不验词。"""
    from contracts.schema.hook import HookConstraints
    from novel.stages.chain import run_workflow
    from novel.stages.common import (
        build_writer_packet, project_chapter_packet, writer_material_issues,
        writer_name_projections,
    )
    from novel.store import Str06Repository
    from tests.str06_facilities import json_ask, text_ask
    from tests.test_str06_body import _chapters, _make_session
    from tests.test_str06_hook import _full_chain_wires

    wires = _full_chain_wires()
    question = '陆大哥当年去了哪里'
    wires['ch01']['beats'][0]['loops'] = [
        {'ref': 'q_where', 'op': 'open', 'question': question}]
    wires['preserve']['obligations'].append({
        'kind': 'reveal', 'statement': '夫君当年去向之谜保留为悬念',
        'anchors': ['b01_01.l1'], 'must_preserve': True,
        'satisfaction': '原文未回答的悬念不得在正文作答', 'requires': []})
    wires['design']['scenes'][0]['beats'][2]['real'] = ['ob02']
    repo, session, _transport = _make_session(tmp_path, wires)

    def no_paid_call(*args, **kwargs):
        raise AssertionError('Stage4 验证不调用正文写手')

    run_workflow(repository=repo, chapters=_chapters(), ask=json_ask(session),
                 ask_text=text_ask(session), ask_body=no_paid_call, budget=2000,
                 through='stage4',
                 hook_constraints=HookConstraints(target_chars=10, hard_max_chars=360))
    report = repo.inspect('stage4/acceptance/guard/report.json')
    assert not report['guard_errors']
    reopened = Str06Repository(repo.root)
    ir = reopened.read_model('stage4/ir/writer_book_ir.json')
    packet = project_chapter_packet(ir, 1)
    assert any('夫君当年去了哪里' in line for line in build_writer_packet(packet).guard_only)
    expected = {'partition': 'guard_only', 'literal': '陆大哥',
                'char_id': 'fu_jun', 'replacement': '夫君'}
    assert expected in writer_name_projections(packet), '禁区实际投影同样必须留痕'
    persisted = reopened.inspect('stage4/acceptance/guard/report.json')
    assert persisted == report
    assert {'chapter_ordinal': 1, **expected} in persisted['name_projections']
    # 禁区投影缺失与禁区验词是两个边界：即使保留完整未释放称谓，也不扩大扫描面。
    no_binding = packet.model_copy(update={'chapter': packet.chapter.model_copy(
        update={'withheld_bindings': ()})})
    assert any(question in line for line in build_writer_packet(no_binding).guard_only)
    assert writer_material_issues(no_binding, internal_ids=set()) == ()


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
    """RC-43＋CS-H H3：Design 自有文本在第1章使用尚未释放的『陆大哥』——靠**显示面
    投影**（回落纯称谓『夫君』）送达，不靠提前释放；compiler 不自动提前释放，
    候选与已构造的释放表保持 Design 原声明。"""
    from copy import deepcopy
    from novel.stages.common import project_chapter_packet, render_chapter_packet
    from novel.stages.stage4 import (
        chapter_surface_releases, inspect_body_materials, inspect_design_candidate,
    )
    from tests.test_str06_candidate_preflight import _prepare
    _, wires, _, args = _prepare(tmp_path)
    wire = wires['design']
    wire['scenes'][0]['beats'][0]['ev'] = '陆大哥拜堂成亲'
    wire['scenes'][1]['beats'][0]['release'] = [
        {'char_id': 'fu_jun', 'surfaces': ['陆大哥']}]
    before = deepcopy(wire)
    view = inspect_design_candidate(wire, **args)
    assert not any('陆大哥' in e for e in view.errors), '第1章文本经投影送达，不再以泄漏报出'
    assert wire == before, '候选 wire 不被改写'
    assert view.body is not None
    assert '陆大哥' not in chapter_surface_releases(view.body, 1, args['chars'].name_structure)
    assert '陆大哥' in chapter_surface_releases(view.body, 2, args['chars'].name_structure), \
        '释放位置保持 Design 原声明（第2章）'
    packet = project_chapter_packet(inspect_body_materials(
        view.body, view.binding, args['brief'], args['chars'],
        budget=args['constraints'].total_length_budget,
        length_band=args['length_band']).ir, 1)
    prose_view = render_chapter_packet(packet)
    assert '陆大哥' not in prose_view, '第1章写手面经显示面投影（RC-60 回落）'
    assert '夫君拜堂成亲' in prose_view, '事件按显示面送达，情节不丢'


# ───────────────────── Hook：自选释放面的渲染期投影（rc16，CS-F2） ─────────────────────

def _hook_context(**hook_over):
    """经正式装配与编译链路构造带名称结构的 HookContext；覆盖项直达 Hook wire。"""
    from contracts.schema.hook import HookConstraints
    from novel.stages.stage4.design import assemble_body_design
    from novel.stages.stage4.hook import assemble_hook_design, publish_graph
    from novel.stages.stage4.hook_context import compile_hook_context
    from tests.str06_expression_inputs import sample_narration, sample_requirements
    from tests.test_str06_hook import _body_wire, _hook_wire
    chars = _characters()
    known = set(chars.characters)
    body, errors = assemble_body_design(
        _body_wire(), known_char_ids=known, expression_requirements=sample_requirements(),
        source_narration=sample_narration())
    assert body is not None and not errors, errors
    hook, errors = assemble_hook_design(
        _hook_wire(**hook_over), body=body, known_char_ids=known,
        name_projection=chars.name_projection())
    assert hook is not None and not errors, errors
    context, errors = compile_hook_context(
        publish_graph(body, hook), chars.name_projection(),
        HookConstraints(target_chars=280, hard_max_chars=360), chars.name_structure)
    assert context is not None and not errors, errors
    return context


def _released_both():
    """Hook 自选释放：yun_niang 用闺名两面、fu_jun 只释放纯称谓「夫君」。"""
    return [
        {'char_id': 'yun_niang', 'surfaces': ['苏芸娘', '芸娘']},
        {'char_id': 'fu_jun', 'surfaces': ['夫君']}]


def test_hook_bindings_follow_the_hook_own_release_selection():
    """绑定表按 hook.surface_releases（Hook 自选）推导，不是章释放表的投影。"""
    context = _hook_context(concealed=[], surface_releases=_released_both())
    assert {b.literal: b.char_id for b in context.withheld_bindings} == {
        '陆大哥': 'fu_jun', '陆兄': 'fu_jun', '苏姑娘': 'yun_niang'}
    assert '夫君' not in {b.literal for b in context.withheld_bindings}, '已释放字面不进绑定表'
    # 只释放芸娘（不释放苏芸娘）时，同一人物的显示面换成芸娘——同尺不同释放面；
    # 禁区行按原投影渲染主语（苏芸娘），同样在送达前投影（只减少暴露，不进字面检查）。
    # CS-H H3（RC-60 第 1 条）：Hook POV 是第一人称、聚焦人物正是 yun_niang——
    # 她自己的称谓按角色词「我（叙述者）」投影，不以释放面兜底。
    only_gui = _hook_context(concealed=[], surface_releases=[
        {'char_id': 'yun_niang', 'surfaces': ['芸娘']},
        {'char_id': 'fu_jun', 'surfaces': ['夫君']}])
    prompt = render_hook_context(only_gui)
    assert '苏芸娘' not in prompt, '本场未释放的完整姓名经投影隐藏'
    row = next(r for r in hook_name_projections(only_gui) if r['literal'] == '苏芸娘')
    assert row == {'field': 'forbidden_reveals/0', 'literal': '苏芸娘',
                   'char_id': 'yun_niang', 'replacement': '我（叙述者）'}


def test_hook_materials_project_bound_literals_and_keep_sources_original():
    """正向材料行渲染期投影；上下文字段与来源归因保持原字段文本。

    CS-H H3：本 Hook POV 为第一人称、聚焦 yun_niang——她自己的绑定称谓按角色词
    「我（叙述者）」投影（RC-60 第 1 条）；fu_jun 按其释放面「夫君」投影。
    """
    context = _hook_context(
        concealed=[], surface_releases=_released_both(),
        design_brief='从深夜冷开倒叙入新婚夜旧约，陆大哥的身份不露，苏姑娘只以闺名示人。')
    assert '陆大哥' in context.design_brief, '上下文字段保持原文本'
    prompt = render_hook_context(context)
    assert '陆大哥' not in prompt and '夫君的身份' in prompt
    assert '苏姑娘' not in prompt and '我（叙述者）只以闺名示人' in prompt
    rows = hook_name_projections(context)
    assert rows and set(rows[0]) == {'field', 'literal', 'char_id', 'replacement'}
    assert {'field': 'design_brief', 'literal': '陆大哥', 'char_id': 'fu_jun',
            'replacement': '夫君'} in rows
    assert {'field': 'design_brief', 'literal': '苏姑娘', 'char_id': 'yun_niang',
            'replacement': '我（叙述者）'} in rows
    assert hook_material_issues(context, internal_ids=set()) == (), '投影后无假泄漏'
    assert render_hook_context(context) == build_hook_user_prompt(context), \
        '请求与验证共用同一 renderer'


def test_hook_unbound_literals_stay_for_the_leak_check_with_original_field_sources():
    """短姓与未登记表达不投影；禁用短姓的泄漏如实报出，来源归因用原字段文本。"""
    context = _hook_context(
        concealed=[], surface_releases=_released_both(),
        design_brief='陆郎与陆家的旧事不露，陆大哥身份存疑。')
    prompt = render_hook_context(context)
    # 陆郎未登记、陆家已随释放面放开：二者原样保留；只有可证绑定的陆大哥被换成夫君。
    assert '陆郎' in prompt and '陆大哥' not in prompt
    issues = hook_material_issues(context, internal_ids=set())
    assert {i.literal for i in issues} == {'陆'}, '短姓无绑定，残留在陆郎内的陆如实报出'
    issue = next(i for i in issues if i.literal == '陆')
    assert any(s.kind == 'hook' and s.field == 'design_brief'
               and s.text == context.design_brief for s in issue.sources), \
        '来源归因依赖原字段文本，不用投影后的衍生行'


def test_hook_bound_literal_without_release_is_reported_not_masked():
    """concealed 人物不可释放面：绑定存在但无显示面，字面如实留下并报告。"""
    context = _hook_context(
        design_brief='从深夜冷开倒叙，陆大哥的身份不露。')
    assert any(b.literal == '陆大哥' and b.char_id == 'fu_jun'
               for b in context.withheld_bindings)
    assert '陆大哥' in render_hook_context(context)
    assert {i.literal for i in hook_material_issues(context, internal_ids=set())} == {
        '陆大哥'}


def test_hook_forbidden_lines_project_but_never_join_the_leak_check():
    """禁区行同样投影（减少暴露）；禁区不进字面检查，两个边界分开。"""
    from tests.test_str06_hook import _body_wire, _hook_wire
    from contracts.schema.hook import HookConstraints
    from novel.stages.stage4.design import assemble_body_design
    from novel.stages.stage4.hook import assemble_hook_design, publish_graph
    from tests.str06_expression_inputs import sample_narration, sample_requirements
    chars = _characters()
    known = set(chars.characters)
    body_wire = _body_wire()
    body_wire['scenes'][0]['beats'][0]['event'] = '陆大哥拜堂成亲'
    body, errors = assemble_body_design(
        body_wire, known_char_ids=known, expression_requirements=sample_requirements(),
        source_narration=sample_narration())
    assert body is not None and not errors, errors
    wire = _hook_wire(
        concealed=[], surface_releases=_released_both(),
        forbidden_reveals=[{'ref': {'scene': 'sc01', 'beat': 'b01'}}])
    hook, errors = assemble_hook_design(
        wire, body=body, known_char_ids=known, name_projection=chars.name_projection())
    assert hook is not None and not errors, errors
    context, errors = compile_hook_context(
        publish_graph(body, hook), chars.name_projection(),
        HookConstraints(target_chars=280, hard_max_chars=360), chars.name_structure)
    assert context is not None and not errors, errors
    assert '事件不得提前揭示：夫君拜堂成亲' in render_hook_context(context)
    assert {'field': 'forbidden_reveals/0', 'literal': '陆大哥',
            'char_id': 'fu_jun', 'replacement': '夫君'} in hook_name_projections(context)
    assert hook_material_issues(context, internal_ids=set()) == ()
    # 无显示面时（compile 期的显示面前缀也被去掉——H3 起显示面与可用称谓分开）
    # 禁区保留完整称谓，也不扩大扫描面（禁区不验词）。
    hidden = context.model_copy(update={
        'usable_surfaces': tuple(r for r in context.usable_surfaces
                                 if r.char_id != 'fu_jun'),
        'display_faces': tuple(r for r in context.display_faces
                               if r.char_id != 'fu_jun')})
    assert '事件不得提前揭示：陆大哥拜堂成亲' in render_hook_context(hidden)
    assert hook_material_issues(hidden, internal_ids=set()) == ()


def test_hook_projection_is_deterministic_and_keeps_the_context_untouched():
    context = _hook_context(
        concealed=[], surface_releases=_released_both(),
        design_brief='从深夜冷开倒叙入新婚夜旧约，陆大哥的身份不露。')
    before = context.model_dump_json()
    assert hook_name_projections(context) == hook_name_projections(context)
    assert render_hook_context(context) == render_hook_context(context)
    assert context.model_dump_json() == before
    # 绑定表清空＝不投影：同一上下文的送达字节不同，泄漏如实出现。
    no_binding = context.model_copy(update={'withheld_bindings': ()})
    assert '陆大哥' in render_hook_context(no_binding)
    leaks = hook_material_issues(no_binding, internal_ids=set())
    assert {i.literal for i in leaks} == {'陆大哥'}
    assert any(s.field == 'design_brief' and s.text == no_binding.design_brief
               for i in leaks for s in i.sources)


def test_hook_context_rejects_pre_rc16_payload_and_duplicate_bindings():
    from pydantic import ValidationError

    from contracts.schema.hook import HookContext
    context = _hook_context(concealed=[], surface_releases=_released_both())
    stored = context.model_dump(mode='json')
    stored['contract_version'] = 'str06-rc12'
    with pytest.raises(ValidationError):
        HookContext.model_validate(stored)
    duplicate = dict(stored)
    duplicate['contract_version'] = 'str06-rc17'
    duplicate['withheld_bindings'] = [
        dict(b) for b in context.withheld_bindings][:1] * 2
    with pytest.raises(ValidationError, match='重复'):
        HookContext.model_validate(duplicate)


def test_hook_projection_reaches_the_compile_guard_report(tmp_path):
    """经真实 run_workflow：楔子投影留痕进 guard report，重启读取一致（免费产物）。"""
    from contracts.schema.hook import HookConstraints
    from novel.stages.chain import run_workflow
    from novel.store import Str06Repository
    from tests.str06_facilities import json_ask, text_ask
    from tests.test_str06_body import _chapters, _make_session
    from tests.test_str06_hook import _full_chain_wires

    wires = _full_chain_wires()
    wires['hook_design'].update(
        concealed=[], surface_releases=_released_both(),
        design_brief='从深夜冷开倒叙入新婚夜旧约，陆大哥的身份不露。')

    def no_paid_call(*args, **kwargs):
        raise AssertionError('Stage4 验证不调用正文写手')

    repo, session, _transport = _make_session(tmp_path, wires)
    run_workflow(repository=repo, chapters=_chapters(), ask=json_ask(session),
                 ask_text=text_ask(session), ask_body=no_paid_call, budget=2000,
                 through='stage4',
                 hook_constraints=HookConstraints(target_chars=10, hard_max_chars=360))
    report = repo.inspect('stage4/acceptance/guard/report.json')
    assert not report['guard_errors']
    expected = {'field': 'design_brief', 'literal': '陆大哥',
                'char_id': 'fu_jun', 'replacement': '夫君'}
    assert expected in report['hook_name_projections'], '楔子投影留痕可观测'
    reopened = Str06Repository(repo.root)
    assert reopened.inspect('stage4/acceptance/guard/report.json') == report
    context = reopened.read_model('stage4/context/hook/hook.json')
    assert context.contract_version == 'str06-rc17'
    assert '陆大哥' in context.design_brief and '陆大哥' not in build_hook_user_prompt(context)
