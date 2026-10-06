"""CS-F1 局部修订（RC-46／RC-47）：Design 的错误在判定处带 wire 路径，条目返修只发最小完整父项。

分两层：
· 定位策略（纯函数）：叶子标量升到完整对象、场景标量原地、耦合字段合并到拍、替换不重叠、
  缺省字段升到现存祖先、任一错误无路径即退整份——返回的是传入的同一批错误对象；
· 真实 stage（fake transport，零付费）：模型真的回 `{"patches":[…]}` 时，请求只含出问题的拍、
  未涉及的拍字节不变、合并后重跑**完整**候选预检才采纳；patch 无进展按现役规则停止；
  存在无法定位的问题时退整份修订。

CS-F1 收口（读取/确认闭合＋Hook）：读取闭合错误带 read 行路径、确认闭合带初始行／
know 行路径（序数不受非确认 know 行穿插影响），不再一律整份退回；Hook wire 的判据
错误（材料 withheld 越界等）同样接条目返修——Hook 与 Design 共用同一构造约定与定位器。
"""
import json
import re
from copy import deepcopy

import pytest

from novel.core.validation import validation_identity
from novel.llm.paid_requests import ValidationStop
from novel.llm.transport import ChatResponse
from novel.stages.chain import run_body_design
from novel.stages.stage4 import locate_design_patches
from novel.stages.stage4.design_paths import design_issue, wire_paths_of
from tests.test_str06_body import _RoutingTransport
from tests.test_str06_candidate_preflight import _bad_design, _prepare


# ───────────────────────── 定位策略（纯函数） ─────────────────────────

WIRE = {
    'book_style': '风格', 'character_voices': [{'char_id': 'a', 'voice': 'v'}],
    'scenes': [
        {'driver': {'focal_actor': 'a'}, 'brief': '场景简报',
         'beats': [{'ev': '拍一', 'state': [{'at': 'a', 'key': 'k', 'val': 'x'}], 'release': []},
                   {'ev': '拍二', 'state': []}]},
        {'driver': {'focal_actor': 'a'}, 'brief': '二场',
         'beats': [{'ev': '拍三', 'state': [{'at': 'a', 'key': 'm', 'val': 'y'}]}]}],
    'chapters': [{'purpose': '章目的', 'scenes': 2}],
}


def issue(message, *paths):
    return design_issue(message, code='t', location=paths[0] if paths else (), paths=paths)


def targets(*errors):
    located = locate_design_patches(deepcopy(WIRE), list(errors))
    return None if located is None else {path: items for path, items in located}


def test_leaf_scalars_are_promoted_to_the_complete_element_they_belong_to():
    state_val = issue('state 值', ('scenes', 0, 'beats', 0, 'state', 0, 'val'))
    located = targets(state_val)
    assert list(located) == [('scenes', 0, 'beats', 0, 'state', 0)], '叶子升到整条 state'
    assert located[('scenes', 0, 'beats', 0, 'state', 0)] == [state_val]
    ev = issue('事件文本', ('scenes', 0, 'beats', 1, 'ev'))
    assert list(targets(ev)) == [('scenes', 0, 'beats', 1)], '拍内标量升到整拍'
    assert list(targets(issue('章目的', ('chapters', 0, 'purpose')))) == [('chapters', 0)]


def test_scene_level_scalars_and_top_level_scalars_stay_in_place():
    """场景是容纳全部拍的大容器：brief／pov 原地替换，不为一句措辞重发整场。"""
    assert list(targets(issue('简报', ('scenes', 1, 'brief')))) == [('scenes', 1, 'brief')]
    assert list(targets(issue('风格', ('book_style',)))) == [('book_style',)]
    assert list(targets(issue('驱动', ('scenes', 0, 'driver')))) == [('scenes', 0, 'driver')]


def test_coupled_fields_in_one_beat_merge_to_the_whole_beat():
    """事实绑定、认知拍位、称谓释放与事件文本彼此耦合：同拍两处及以上待修 ⇒ 整拍替换。"""
    a = issue('state 问题', ('scenes', 0, 'beats', 0, 'state', 0))
    b = issue('释放问题', ('scenes', 0, 'beats', 0, 'release'))
    located = targets(a, b)
    assert list(located) == [('scenes', 0, 'beats', 0)]
    assert located[('scenes', 0, 'beats', 0)] == [a, b]
    # 不同拍的问题互不牵连：各自最小父项，替换不重叠。
    c = issue('另一拍', ('scenes', 1, 'beats', 0, 'ev'))
    located = targets(a, c)
    assert sorted(located) == [('scenes', 0, 'beats', 0, 'state', 0), ('scenes', 1, 'beats', 0)]


def test_nested_paths_fold_into_the_outer_replacement_without_overlap():
    outer = issue('整拍', ('scenes', 0, 'beats', 0))
    inner = issue('内层', ('scenes', 0, 'beats', 0, 'state', 0, 'val'))
    located = targets(outer, inner)
    assert list(located) == [('scenes', 0, 'beats', 0)]
    assert located[('scenes', 0, 'beats', 0)] == [outer, inner]


def test_missing_keys_climb_to_the_nearest_existing_ancestor_and_unlocatable_means_full_revision():
    missing = issue('缺省字段', ('scenes', 0, 'beats', 1, 'real'))
    assert list(targets(missing)) == [('scenes', 0, 'beats', 1)]
    # 任一错误没有路径、路径指向不存在的顶层结构：本轮退整份修订，不静默漏项。
    assert targets(issue('全局问题')) is None
    assert targets(missing, 'plain string error') is None
    assert targets(issue('越界', ('scenes', 9, 'beats', 0, 'ev'))) is None
    assert targets(issue('顶层缺省', ('expression_overrides', 0))) is None
    assert locate_design_patches(WIRE, []) is None and locate_design_patches('x', [missing]) is None


def test_every_error_object_is_returned_so_the_ask_loop_can_check_full_coverage():
    errors = [issue('1', ('scenes', 0, 'beats', 0, 'ev')),
              issue('2', ('scenes', 0, 'beats', 1, 'ev')),
              issue('3', ('chapters', 0, 'purpose'))]
    located = locate_design_patches(deepcopy(WIRE), errors)
    assert {id(e) for _p, items in located for e in items} == {id(e) for e in errors}
    assert wire_paths_of(errors[0]) == (('scenes', 0, 'beats', 0, 'ev'),)
    assert wire_paths_of('plain') is None and wire_paths_of(issue('无路径')) is None


def test_optional_routed_errors_without_paths_get_the_beat_closed_set():
    """RC-58：无路径但登记 optional 的规则码 → 候选拍闭集（第三元素 True）。

    必保义务漏标（design_carrier_missing）与 series 接受拍互斥（series_collapsed）
    的责任位置须由模型决定：定位器给 wire 全部拍作闭集，模型只覆盖选中的子集。
    """
    carrier = design_issue('必保义务未被任何拍标注承载（real）：[ob01]',
                           code='design_carrier_missing', location=('coverage', ('ob01',)))
    beats = [('scenes', 0, 'beats', 0), ('scenes', 0, 'beats', 1), ('scenes', 1, 'beats', 0)]
    located = locate_design_patches(deepcopy(WIRE), [carrier])
    assert located == [(beat, [carrier], True) for beat in beats]
    series = design_issue('series_collapsed：成员接受拍互斥', code='series_collapsed',
                          location=('series_collapsed', 'sc01#b02', ('ob01', 'ob02')))
    assert locate_design_patches(deepcopy(WIRE), [series]) == \
        [(beat, [series], True) for beat in beats]
    # 未登记 optional 的无路径错误仍退整份：字符串、无码问题、非 optional 码。
    assert targets(issue('全局问题')) is None
    unregistered = design_issue('x', code='not_a_route', location=())
    assert locate_design_patches(deepcopy(WIRE), [unregistered]) is None
    assert locate_design_patches({'beats': []}, [carrier]) is None, \
        '登记 optional 却给不出候选闭集（无拍）＝退整份并如实暴露'


def test_optional_closed_set_promotes_required_targets_inside_the_same_beat():
    """闭集拍内已有必选目标：升格整拍且保持必选；其余拍可选；拍外目标原样保留。

    不让「拍内必选条目」与「整拍可选条目」同时在场——重叠替换的合并次序会
    静默吞掉一侧的修复。
    """
    carrier = design_issue('必保义务漏标', code='design_carrier_missing',
                           location=('coverage', ('ob01',)))
    leak = issue('拍内必选', ('scenes', 0, 'beats', 0, 'ev'))
    located = locate_design_patches(deepcopy(WIRE), [leak, carrier])
    out = {entry[0]: (entry[1], len(entry) > 2) for entry in located}
    assert out[('scenes', 0, 'beats', 0)] == ([leak, carrier], False), '必选升格整拍'
    assert out[('scenes', 0, 'beats', 1)] == ([carrier], True)
    assert out[('scenes', 1, 'beats', 0)] == ([carrier], True)
    top = issue('章目的', ('chapters', 0, 'purpose'))
    located = locate_design_patches(deepcopy(WIRE), [top, carrier])
    out = {entry[0]: (entry[1], len(entry) > 2) for entry in located}
    assert out[('chapters', 0)] == ([top], False), '拍外必选目标不被闭集吞并'
    assert out[('scenes', 0, 'beats', 0)] == ([carrier], True)


# ───────────────────────── 真实 stage：模型真的回 patches ─────────────────────────

def _parse_path(text):
    path = []
    for key, index in re.findall(r'([A-Za-z_]+)|\[(\d+)\]', text):
        path.append(int(index) if index else key)
    return tuple(path)


def _walk(document, path):
    for part in path:
        document = document[part]
    return document


class PatchingTransport:
    """所配 owner 首答给坏稿；收到条目返修请求时按请求的路径回 `{"patches":[…]}`
    （值取自好稿或 `patch_value`），整份修订请求回好稿——只在 JSON 面上施加，
    其余 owner 走原路由。`owner_requests` 按发送序记录该 owner 的 user 面。

    路径解析取全部 `- 路径 X：` 行（必选行带「当前值」、可选目标行在窄形状下
    不带——闭集当前值随依赖切片送达），闭集全覆盖取好稿值即合法子集语义。"""

    def __init__(self, wires, bad, *, patch_value=None, owner='design',
                 patch_paths=None):
        self._inner = _RoutingTransport(wires)
        self._good = wires[owner]
        self._bad = bad
        self._owner_name = owner
        self._patch_value = patch_value        # 自定义 patch 取值（无进展用例）
        self._patch_paths = patch_paths        # 只覆盖给定子集（可选目标闭集用例）
        self.owner_requests: list[str] = []

    @property
    def calls(self):
        return self._inner.calls

    def send(self, request):
        if self._inner._owner(request) != self._owner_name:
            return self._inner.send(request)
        self._inner.calls.append(self._owner_name)
        self.owner_requests.append(request.user)
        if '【条目返修】' in request.user:
            listed = [(text, _parse_path(text)) for text in
                      re.findall(r'- 路径 ([^\s：]+)：', request.user)]
            if self._patch_paths is not None:
                allowed = set(self._patch_paths)
                listed = [pair for pair in listed if pair[1] in allowed]
            patches = [{'path': text,
                        'value': (self._patch_value(path) if self._patch_value
                                  else _walk(self._good, path))}
                       for text, path in listed]
            body = {'patches': patches}
        elif '【修订要求】' in request.user:
            body = self._good
        else:
            body = self._bad
        return ChatResponse(content=json.dumps(body, ensure_ascii=False), finish_reason='stop',
                            usage={'prompt_tokens': 100, 'completion_tokens': 40}, model='fake')


def _prepare_with(tmp_path, make_transport):
    ctx, wires, _transport, _args = _prepare(tmp_path)
    return ctx, wires


def test_item_patch_round_resends_only_the_failing_beats_and_merges_to_a_full_green_design(tmp_path):
    from novel.llm.paid_requests import PaidRequestSession  # noqa: F401 (import 面自检)
    from tests.test_str06_body import _make_session
    from tests.str06_facilities import json_ask, text_ask
    from novel.llm.paid_requests import digest
    from novel.stages.chain import ChainContext, run_preserve, run_workflow
    from tests.test_str06_hook import _full_chain_wires
    from tests.test_str06_body import _chapters

    wires = _full_chain_wires()
    bad = deepcopy(wires['design'])
    _bad_design(bad)                  # 第1场两拍的事件文本提前使用未释放的『陆大哥』
    transport = PatchingTransport(wires, bad)
    repo, session, _ = _make_session(tmp_path, wires, transport=transport)

    def no_body(*args):
        raise AssertionError('不调用正文写手')

    ctx = ChainContext(repository=repo, ask=json_ask(session), ask_text=text_ask(session),
                       ask_body=no_body, chapters=_chapters(), budget=2000, digest=digest)
    run_workflow(repository=repo, chapters=ctx.chapters, ask=ctx.ask, ask_text=ctx.ask_text,
                 ask_body=ctx.ask_body, budget=ctx.budget, through='stage2')
    run_preserve(ctx)
    run_body_design(ctx)

    assert transport.calls.count('design') == 2, '首答＋一次条目返修'
    patch_request = transport.owner_requests[1]
    assert '【条目返修】' in patch_request and '【修订要求】' not in patch_request
    listed = re.findall(r'- 路径 (.+?)：当前值', patch_request)
    assert listed == ['scenes[0].beats[0]', 'scenes[0].beats[1]'], '只请求出问题的两拍，替换不重叠'
    assert '陆大哥' in patch_request.split('【条目返修】', 1)[1]
    # 合并后重跑完整预检才采纳：发布的 Design 与好稿等价（事件文本已改对），其余拍字节不变。
    body = repo.read_model('stage4/design/body_design.json')
    assert [b.event for b in body.scenes[0].beats[:2]] == ['拜堂成亲', '洞房夜夫君立约']
    assert not any('陆大哥' in b.event for s in body.scenes for b in s.beats)
    assert repo.read_model('stage4/grounding/semantic_binding.json').contract_version == 'str06-rc16'
    # 成本记账：原轮＋条目返修各一次实付，不是整份重写两遍的另一种计法。
    from novel.llm.ledger import read_cost_snapshot
    rows, _ = read_cost_snapshot(repo.root.parent)
    assert sum(1 for r in rows if not r['cached'] and r['purpose'] == 'design') == 2


def test_patch_that_does_not_fix_the_leak_is_stopped_by_the_existing_no_progress_rule(tmp_path):
    from tests.test_str06_body import _chapters, _make_session
    from tests.str06_facilities import json_ask, text_ask
    from novel.llm.paid_requests import digest
    from novel.stages.chain import ChainContext, run_preserve, run_workflow
    from tests.test_str06_hook import _full_chain_wires

    wires = _full_chain_wires()
    bad = deepcopy(wires['design'])
    _bad_design(bad)
    # 粘性：patch 回来的值仍带着同一个泄漏。
    transport = PatchingTransport(wires, bad, patch_value=lambda path: _walk(bad, path))
    repo, session, _ = _make_session(tmp_path, wires, transport=transport)
    ctx = ChainContext(repository=repo, ask=json_ask(session), ask_text=text_ask(session),
                       ask_body=lambda *a: (_ for _ in ()).throw(AssertionError('stub')),
                       chapters=_chapters(), budget=2000, digest=digest)
    run_workflow(repository=repo, chapters=ctx.chapters, ask=ctx.ask, ask_text=ctx.ask_text,
                 ask_body=ctx.ask_body, budget=ctx.budget, through='stage2')
    run_preserve(ctx)
    with pytest.raises(ValidationStop) as stopped:
        run_body_design(ctx)
    assert stopped.value.code == 'validation_no_progress'
    assert transport.calls.count('design') == 2, '同一问题位置集合重复即停，不无限付费'
    assert not repo.path('stage4/design/body_design.json').exists()
    assert not repo.path('stage4/grounding/semantic_binding.json').exists()


def test_carrier_missing_gets_one_optional_closed_set_round_instead_of_full_rewrite(tmp_path):
    """判据③：必保义务漏标（此前整份重写）→ 一轮可选目标返修。

    首答漏标 real：定位器给全部拍作候选闭集（必选两处泄漏拍升格整拍）；模型
    覆盖闭集子集（含恢复 real）→ 合并过完整预检发布。同轮还有可定位泄漏时
    必选与可选共存、各自语义保持。"""
    from tests.test_str06_body import _chapters, _make_session
    from tests.str06_facilities import json_ask, text_ask
    from novel.llm.paid_requests import digest
    from novel.stages.chain import ChainContext, run_preserve, run_workflow
    from tests.test_str06_hook import _full_chain_wires

    wires = _full_chain_wires()
    bad = deepcopy(wires['design'])
    _bad_design(bad)
    for scene in bad['scenes']:
        for beat in scene['beats']:
            beat.pop('real', None)
    transport = PatchingTransport(wires, bad)
    repo, session, _ = _make_session(tmp_path, wires, transport=transport)
    ctx = ChainContext(repository=repo, ask=json_ask(session), ask_text=text_ask(session),
                       ask_body=lambda *a: (_ for _ in ()).throw(AssertionError('stub')),
                       chapters=_chapters(), budget=2000, digest=digest)
    run_workflow(repository=repo, chapters=ctx.chapters, ask=ctx.ask, ask_text=ctx.ask_text,
                 ask_body=ctx.ask_body, budget=ctx.budget, through='stage2')
    run_preserve(ctx)
    run_body_design(ctx)
    assert transport.calls.count('design') == 2, '首答＋一轮可选目标返修，不整份重写'
    patch_request = transport.owner_requests[1]
    assert '【条目返修】' in patch_request and '【修订要求】' not in patch_request
    assert '可选目标闭集' in patch_request, '漏标 real 以可选目标发回（RC-58）'
    # 必选行带当前值；可选行不带（当前值随依赖切片送达，M3 返修宽度）。
    required = re.findall(r'- 路径 (.+?)：当前值', patch_request)
    assert required == ['scenes[0].beats[0]', 'scenes[0].beats[1]'], \
        '两处泄漏拍升格整拍保持必选'
    optional = [p for p in re.findall(r'- 路径 ([^\s：]+)：', patch_request)
                if p not in required]
    assert optional == ['scenes[0].beats[2]', 'scenes[1].beats[0]', 'scenes[1].beats[1]'], \
        '闭集＝候选全部拍，泄漏拍已是必选不重复登记'
    assert '（当前值见依赖切片）' in patch_request
    assert 'stage4-design-patch-slice-v1' in patch_request, '窄请求带依赖切片'
    assert 'stage4-design-input-v1' not in patch_request, '不带整份原请求输入投影'
    body = repo.read_model('stage4/design/body_design.json')
    assert [b.event for b in body.scenes[0].beats[:2]] == ['拜堂成亲', '洞房夜夫君立约']
    binding = repo.read_model('stage4/grounding/semantic_binding.json')
    assert [e.obligation_id for e in binding.entries] == ['ob01'], 'real 恢复后承载闭合'


def test_optional_round_adopts_a_subset_of_the_closed_set_and_keeps_other_beats_intact(tmp_path):
    """判据②语义（承载位由模型在闭集里选）：只覆盖选中子集即合法；未选拍的
    字节不变（补丁只替换选中条目，无 real 的拍本就与好稿逐字相同）。"""
    from tests.test_str06_body import _chapters, _make_session
    from tests.str06_facilities import json_ask, text_ask
    from novel.llm.paid_requests import digest
    from contracts.schema.grounding import OutputConstraints
    from novel.stages.chain import ChainContext, run_preserve, run_workflow
    from novel.stages.stage4 import (
        design_obligation_index, inspect_design_candidate, project_brief,
    )
    from tests.test_str06_hook import _full_chain_wires

    wires = _full_chain_wires()
    bad = deepcopy(wires['design'])
    for scene in bad['scenes']:
        for beat in scene['beats']:
            beat.pop('real', None)
    # 模型只选中前两拍作承载位（好稿 real 所在的闭集子集）。
    transport = PatchingTransport(
        wires, bad,
        patch_paths=[('scenes', 0, 'beats', 0), ('scenes', 0, 'beats', 1)])
    repo, session, _ = _make_session(tmp_path, wires, transport=transport)
    ctx = ChainContext(repository=repo, ask=json_ask(session), ask_text=text_ask(session),
                       ask_body=lambda *a: (_ for _ in ()).throw(AssertionError('stub')),
                       chapters=_chapters(), budget=2000, digest=digest)
    run_workflow(repository=repo, chapters=ctx.chapters, ask=ctx.ask, ask_text=ctx.ask_text,
                 ask_body=ctx.ask_body, budget=ctx.budget, through='stage2')
    run_preserve(ctx)
    run_body_design(ctx)
    assert transport.calls.count('design') == 2, '一轮可选目标返修即采纳'
    # 发布体与「好稿直接过闸」的编译结果一致：选中的两拍拿到好稿值（含 real），
    # 未选拍与坏稿逐字相同（本就无 real）⇒ 合并版＝好稿。
    chars = repo.read_model('stage2/characters/characters.json')
    checkpoint = repo.read_model('runtime/preserve_checkpoint.json')
    blueprint = repo.read_model('stage2/blueprints/chapter_blueprints.json')
    brief, _ = project_brief(checkpoint, chars.core_view(), blueprint)
    expected = inspect_design_candidate(
        wires['design'], checkpoint=checkpoint, brief=brief,
        obligations=design_obligation_index(checkpoint, brief), chars=chars,
        constraints=OutputConstraints(target_chapter_count=len(ctx.chapters),
                                      total_length_budget=ctx.budget),
        length_band=ctx.length_band)
    assert expected.passed, expected.report()
    published = repo.read_model('stage4/design/body_design.json')
    assert published.model_dump(mode='json') == expected.body.model_dump(mode='json')


def test_a_globally_unlocatable_problem_sends_the_whole_design_back(tmp_path):
    """协议字面量不符（design_protocol_literal，整份出口闭集）没有条目编辑位置：
    整份退回——可选目标只收登记 optional 的规则码，不越权扩闭集。"""
    from tests.test_str06_body import _chapters, _make_session
    from tests.str06_facilities import json_ask, text_ask
    from novel.llm.paid_requests import digest
    from novel.stages.chain import ChainContext, run_preserve, run_workflow
    from tests.test_str06_hook import _full_chain_wires

    wires = _full_chain_wires()
    bad = deepcopy(wires['design'])
    bad['protocol_version'] = 'stage4-design-v7'   # 协议字面量不符（I-13 无隐式迁移）
    transport = PatchingTransport(wires, bad)
    repo, session, _ = _make_session(tmp_path, wires, transport=transport)
    ctx = ChainContext(repository=repo, ask=json_ask(session), ask_text=text_ask(session),
                       ask_body=lambda *a: (_ for _ in ()).throw(AssertionError('stub')),
                       chapters=_chapters(), budget=2000, digest=digest)
    run_workflow(repository=repo, chapters=ctx.chapters, ask=ctx.ask, ask_text=ctx.ask_text,
                 ask_body=ctx.ask_body, budget=ctx.budget, through='stage2')
    run_preserve(ctx)
    run_body_design(ctx)
    assert '【修订要求】' in transport.owner_requests[1]
    assert '【条目返修】' not in transport.owner_requests[1], '无法定位就退整份，不局部漏项'
    assert repo.path('stage4/design/body_design.json').is_file()


# ───────────── 读取/确认闭合（CS-F1 收口）：错误带 wire 路径 ─────────────

def _confirm_wire():
    """v8 wire＋扩展 Brief：第 1 拍两个 know 行——先普通知情行、后带悬空 confirms
    的读者确认行（检验序数→wire 行号的映射不受非确认行穿插影响）。"""
    from contracts.schema.grounding import ReconstructionBrief
    from tests.test_str06_design_slim import _brief, _v8_wire

    payload = _brief().model_dump(mode='json')
    payload['requirements'].append({
        'core_id': 'req_eeee', 'summary': '读者确知借种之计', 'kind': 'reveal',
        'must_preserve': True, 'satisfaction': '读者确认', 'participants': [],
        'requires_cores': [], 'locks': [],
        'knowledge_changes': [{'change_id': 'kc_2222222222222222', 'holder': 'reader',
                               'kp': 'kp_2222222222222222', 'mode': 'known'}],
        'realize_locks': [], 'series_group': None, 'series_mechanism': None})
    payload['propositions'].append({'kp_id': 'kp_2222222222222222',
                                    'statement': '借种之计确有其事',
                                    'publicly_claimed_by': []})
    payload['knowledge_locks'].append({'holder': 'reader', 'kp': 'kp_2222222222222222',
                                       'mode': 'known'})
    brief = ReconstructionBrief.model_validate(payload)
    by_core = {r.core_id: r for r in brief.requirements}
    obligations = {'ob01': by_core['req_aaaa'], 'ob02': by_core['req_bbbb'],
                   'ob03': by_core['req_cccc'], 'ob04': by_core['req_eeee']}
    wire = _v8_wire()
    beat = wire['scenes'][0]['beats'][0]
    beat['real'] = ['ob01', 'ob04']
    beat['know'] = [
        {'ref': 'ob01.k01'},
        {'ref': 'ob04.k01',
         'confirms': {'domain': 'character', 'subjects': ['lu_fujun'],
                      'holder': None, 'key': 'ghost_fact'}}]
    return wire, brief, obligations


def test_read_and_confirm_closure_errors_carry_wire_paths_for_item_revision():
    """读取闭合→read 行；确认闭合→初始行／know 行（含非确认行穿插）；
    多处违例各自独立定位；混入无路径错误仍整份退回。"""
    from novel.stages.stage4.design import assemble_body_design
    from tests.test_str06_design_slim import _brief, _obligations, _v8_wire

    def assemble(wire, brief, obligations):
        return assemble_body_design(
            wire, known_char_ids={'lu_fujun', 'yun_niang', 'pei_shuyuan'},
            expression_requirements=brief.expression,
            source_narration=brief.source_narration, brief=brief,
            obligations=obligations)

    brief = _brief()
    wire = _v8_wire()
    wire['scenes'][1]['reads'] = [{'domain': 'character', 'subjects': ['yun_niang'],
                                   'holder': None, 'key': 'ghost_state'}]
    body, errors = assemble(wire, brief, _obligations(brief))
    assert body is None
    (read_issue,) = [e for e in errors if getattr(e, 'code', '') == 'design_read_unestablished']
    assert wire_paths_of(read_issue) == (('scenes', 1, 'reads', 0),)
    located = locate_design_patches(wire, list(errors))
    assert [(path, len(items)) for path, items in located] == [(('scenes', 1, 'reads', 0), 1)]

    wire, brief, obligations = _confirm_wire()
    body, errors = assemble(wire, brief, obligations)
    assert body is None
    (confirm_issue,) = [e for e in errors
                        if getattr(e, 'code', '') == 'design_confirm_unestablished']
    assert wire_paths_of(confirm_issue) == (('scenes', 0, 'beats', 0, 'know', 1),)
    located = locate_design_patches(wire, list(errors))
    assert [path for path, _items in located] == [('scenes', 0, 'beats', 0, 'know', 1)]

    # 初始态违例路径指向 initial_state 的读者确认行（confirms 只能随 reader known
    # 知情行声明），与拍内违例互不牵连、各自成条目。
    wire['initial_state'].append({
        'domain': 'knowledge', 'subjects': [], 'holder': 'reader',
        'key': 'kp_2222222222222222', 'value': 'true', 'knowledge_mode': 'known',
        'confirms': {'domain': 'character', 'subjects': ['lu_fujun'], 'holder': None,
                     'key': 'ghost_fact'}})
    body, errors = assemble(wire, brief, obligations)
    assert body is None
    by_path = {tuple(p): e for e in errors
               if getattr(e, 'code', '') == 'design_confirm_unestablished'
               for p in wire_paths_of(e)}
    assert set(by_path) == {('initial_state', 1), ('scenes', 0, 'beats', 0, 'know', 1)}
    located = locate_design_patches(wire, list(errors))
    assert sorted(path for path, _items in located) == [
        ('initial_state', 1), ('scenes', 0, 'beats', 0, 'know', 1)]
    # 位置集合身份分开：初始行与 know 行是两个不同问题，不是同一处重复。
    assert len({validation_identity(e) for e in errors
                if getattr(e, 'code', '') == 'design_confirm_unestablished'}) == 2


def test_unestablished_read_gets_an_item_patch_round_and_publishes(tmp_path):
    """真实 stage（fake transport）：坏 read 只返修该行，改对地址后合并过完整预检发布。"""
    from tests.test_str06_body import _chapters, _make_session
    from tests.str06_facilities import json_ask, text_ask
    from novel.llm.paid_requests import digest
    from novel.stages.chain import ChainContext, run_preserve, run_workflow
    from tests.test_str06_hook import _full_chain_wires

    wires = _full_chain_wires()
    bad = deepcopy(wires['design'])
    bad['scenes'][1]['reads'] = [{'domain': 'character', 'subjects': ['yun_niang'],
                                  'holder': None, 'key': 'marital_status_typo'}]
    fixed_row = {'domain': 'character', 'subjects': ['yun_niang'], 'holder': None,
                 'key': 'marital_status'}
    transport = PatchingTransport(wires, bad, patch_value=lambda _path: fixed_row)
    repo, session, _ = _make_session(tmp_path, wires, transport=transport)
    ctx = ChainContext(repository=repo, ask=json_ask(session), ask_text=text_ask(session),
                       ask_body=lambda *a: (_ for _ in ()).throw(AssertionError('stub')),
                       chapters=_chapters(), budget=2000, digest=digest)
    run_workflow(repository=repo, chapters=ctx.chapters, ask=ctx.ask, ask_text=ctx.ask_text,
                 ask_body=ctx.ask_body, budget=ctx.budget, through='stage2')
    run_preserve(ctx)
    run_body_design(ctx)

    assert transport.calls.count('design') == 2, '首答＋一次条目返修'
    patch_request = transport.owner_requests[1]
    listed = re.findall(r'- 路径 (.+?)：当前值', patch_request)
    assert listed == ['scenes[1].reads[0]'], '只返修坏 read 行，其余 wire 字节不动'
    assert 'marital_status_typo' in patch_request
    body = repo.read_model('stage4/design/body_design.json')
    assert body.scenes[1].state_reads[0].key == 'marital_status'
    assert repo.read_model('stage4/grounding/semantic_binding.json').contract_version
    from novel.llm.ledger import read_cost_snapshot
    rows, _ = read_cost_snapshot(repo.root.parent)
    assert sum(1 for r in rows if not r['cached'] and r['purpose'] == 'design') == 2


def test_hook_design_item_patch_round_fixes_only_the_failing_material(tmp_path):
    """Hook 设计接局部修订（CS-F1）：材料 withheld 越界只返修该材料，合并后
    经完整 hook 判据（context 编译＋材料校验）发布 presentation_graph。"""
    from tests.test_str06_body import _chapters, _make_session
    from tests.str06_facilities import json_ask, text_ask
    from novel.llm.paid_requests import digest
    from novel.stages.chain import ChainContext, run_hook_design, run_preserve, run_workflow
    from tests.test_str06_hook import _full_chain_wires

    wires = _full_chain_wires()
    bad = deepcopy(wires['hook_design'])
    bad['materials'][0]['withheld'] = [
        {'domain': 'character', 'subjects': ['yun_niang'], 'holder': None,
         'key': 'marital_status'}]
    transport = PatchingTransport(wires, bad, owner='hook_design')
    repo, session, _ = _make_session(tmp_path, wires, transport=transport)
    ctx = ChainContext(repository=repo, ask=json_ask(session), ask_text=text_ask(session),
                       ask_body=lambda *a: (_ for _ in ()).throw(AssertionError('stub')),
                       chapters=_chapters(), budget=2000, digest=digest)
    run_workflow(repository=repo, chapters=ctx.chapters, ask=ctx.ask, ask_text=ctx.ask_text,
                 ask_body=ctx.ask_body, budget=ctx.budget, through='stage2')
    run_preserve(ctx)
    run_body_design(ctx)
    run_hook_design(ctx)

    assert transport.calls.count('hook_design') == 2, '首答＋一次条目返修'
    patch_request = transport.owner_requests[1]
    assert '【条目返修】' in patch_request and '【修订要求】' not in patch_request
    # CS-H H2 ⑦：Hook 与 Design 同法接入窄返修（RC-57）——不带整份原请求，
    # 只带协议规则＋失败条目＋依赖切片（被引用拍与释放前缀）＋合法取值。
    assert '【窄返修】' in patch_request, 'Hook 返修走窄形状，原请求不作前缀'
    assert 'stage4-hook-patch-slice-v1' in patch_request, 'Hook 窄请求带依赖切片'
    listed = re.findall(r'- 路径 (.+?)：当前值', patch_request)
    assert listed == ['materials[0]'], '只返修越界材料，其余条目字节不动'
    graph = repo.read_model('stage4/design/presentation_graph.json')
    assert graph.hook.materials[0].withheld == ()
    from novel.llm.ledger import read_cost_snapshot
    rows, _ = read_cost_snapshot(repo.root.parent)
    assert sum(1 for r in rows if not r['cached'] and r['purpose'] == 'hook_design') == 2
