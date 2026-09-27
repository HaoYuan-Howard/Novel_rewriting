"""Body Design wire 的确定性组装与派生面生成（B3 前半重写）。

【compiler 生成 ID／归章／权重归一（I-21，B3 前半）】
Design wire（`stage4-design-v1`）只表达选择、结构与相对篇幅重点——模型不输出
任何 ID、复合引用或归一权重。本模块是唯一派生点：
  ① 场景/拍 ID 按列表序派发：`sc{序:02d}`／`b{序:02d}`；wire 带 scene_id／
     beat_id 键即拒（「不写可派生字段」的机械面，Q-1 白名单）。
  ② 归章是章切片：wire 的 chapters=[{purpose, scenes:N}]，compiler 按场景阅读
     序连续切片派发 ChapterAssignment（章号连续、全覆盖、每章非空由构造保证）。
  ③ 权重归一：wire 的 scene_weights 是与场景等长的正数数组（相对重点），
     compiler 归一成和为 1 的 {scene_id: weight}；缺省不登记（下游均分）。
  ④ 复合锚定引用：ongoing_actions/open_questions/promises 的 {"scene","beat"}
     1-based 分量解析成 `场景#拍`，越界精确拒。
  ⑤ initial_state 一律 book 作用域（schema 强制），wire 不带 scope，compiler 填。
  ⑧ 状态读取定位（B3 后半）：state_reads 的 "at"（1-based 拍序）解析成场内
     beat_id，缺省定位到入场——先读旧值、后写新值；定位闭合在本模块校验，
     读取时序闭合归 compile 的章闭包证明。

【确定性归一（F5e 真书实证；不补猜语义）】
  ⑥ 非 knowledge 域的 `knowledge_mode` 一律剥离——schema 只允许 knowledge 带
     模式；模型会在 reader 揭示上装饰 `'known'`，该值在其它域无语义可保留。
  ⑦ `delivery.pov` 若不是 char_id/narrator，而是以某人物 canonical 开头的字符串
     （F5e 实证：`苏芸娘第三人称限知`），按 canonical→char_id 唯一前缀匹配解析为
     该 char_id；无匹配或多义匹配不归一，交由 char_id 校验报错。
两类归一都是无损形状转换：不改任何事件、状态、数值与 brief 语义。

【协议与键白名单】wire 顶层 `protocol_version` 必须逐字等于
`STAGE4_DESIGN_PROTOCOL`（I-13：漏带/错带即拒，无隐式迁移）；顶层与各嵌套层
键白名单外的键拒绝，不静默丢弃（Q-1）。

【读者确认的事实地址（D4/R-6.11）】reader 域 set 的 `confirms` 必填由 schema
构造期表达；这里做引用闭合的机械证明：确认地址必须真实建立（initial_state 或
某拍 delta 的 set），且确认位置不早于建立位置。
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from pydantic import ValidationError

from contracts.schema.design import BodyDesign
from contracts.schema.grounding import OutputConstraints
from contracts.schema.workflow import STAGE4_DESIGN_PROTOCOL

#: wire 顶层键白名单（Q-1：多余键在 compile 入口报错，不静默丢弃）。
_WIRE_KEYS = frozenset({
    'protocol_version', 'book_style', 'character_voices', 'standing_goals',
    'ongoing_actions', 'open_questions', 'promises', 'initial_state',
    'initial_surfaces', 'scenes', 'chapters', 'scene_weights'})
_INITIAL_KEYS = frozenset({'domain', 'key', 'subjects', 'holder', 'op', 'value',
                           'knowledge_mode', 'confirms'})
_SCENE_KEYS = frozenset({'driver', 'state_reads', 'realization_brief', 'delivery', 'beats'})
_BEAT_KEYS = frozenset({'event', 'participants', 'dramatic_intent', 'delivery_hint',
                        'target_delta', 'surface_introductions'})
#: 状态读取声明（B3 后半）：地址四段＋可选读取定位 `at`（1-based 拍序，
#: compiler 解析成场内 beat_id；缺省定位到入场＝先读旧值）。
_READ_KEYS = frozenset({'domain', 'key', 'subjects', 'holder', 'at'})


def _scene_id(index: int) -> str:
    return f'sc{index:02d}'


def _beat_id(index: int) -> str:
    return f'b{index:02d}'


def _reject_unknown_keys(errors: list[str], obj: dict[str, Any], allowed: frozenset[str],
                         where: str) -> bool:
    extra = set(obj) - allowed
    if extra:
        errors.append(f'{where} 携带未知字段 {sorted(extra)}（wire 键白名单外；'
                      'ID、复合引用与归一权重由 compiler 生成，不收）')
        return False
    return True


def _normalize(data: dict[str, Any]) -> None:
    """无损形状转换：剥非 knowledge 域的知情模式装饰。"""
    for item in data.get('initial_state') or []:
        if isinstance(item, dict) and item.get('domain') != 'knowledge':
            item.pop('knowledge_mode', None)
    for scene in data.get('scenes') or []:
        for beat in scene.get('beats') or []:
            for item in beat.get('target_delta') or []:
                if isinstance(item, dict) and item.get('domain') != 'knowledge':
                    item.pop('knowledge_mode', None)


def _normalize_povs(data: dict[str, Any], char_canonicals: Mapping[str, str] | None) -> None:
    """canonical 前缀唯一匹配 → char_id；匹配不到保持原样让校验报错。"""
    if not char_canonicals:
        return
    for scene in data.get('scenes') or []:
        delivery = scene.get('delivery') or {}
        pov = delivery.get('pov')
        if not isinstance(pov, str) or pov == 'narrator':
            continue
        matches = {cid for canonical, cid in char_canonicals.items()
                   if canonical and pov.startswith(canonical)}
        if len(matches) == 1:
            delivery['pov'] = next(iter(matches))


def _normalize_delta(data: dict[str, Any]) -> None:
    """initial_state 统一 book 作用域（schema 强制）；wire 不带 scope 键。"""
    for item in data.get('initial_state') or []:
        if isinstance(item, dict):
            item.pop('scope', None)
            item['scope'] = 'book'


def _resolve_anchor(raw: Any, beats_of: dict[int, int],
                    errors: list[str], where: str) -> str | None:
    """wire 锚定分量 {"scene": i, "beat": j}（1-based）→ `sc{i:02d}#b{j:02d}`。"""
    parsed = None
    if isinstance(raw, dict) and 'scene' in raw and 'beat' in raw:
        scene, beat = raw.get('scene'), raw.get('beat')
        if (isinstance(scene, int) and not isinstance(scene, bool)
                and isinstance(beat, int) and not isinstance(beat, bool)):
            parsed = (scene, beat)
    if parsed is None:
        errors.append(f'{where} 的锚定形状非法：{raw!r}（应为 {{"scene": 序, "beat": 序}}）')
        return None
    scene, beat = parsed
    if scene not in beats_of:
        errors.append(f'{where} 的锚定引用不存在的场景：第 {scene} 场'
                      f'（全书 {len(beats_of)} 场）')
        return None
    if not 1 <= beat <= beats_of[scene]:
        errors.append(
            f'{where} 的锚定引用不存在的拍：第 {scene} 场只有 {beats_of[scene]} 拍')
        return None
    return f'{_scene_id(scene)}#{_beat_id(beat)}'


def _resolve_cross_chapter(data: dict[str, Any], errors: list[str]) -> None:
    """跨章条目的锚定分量 → 复合引用（canonical 形状）。"""
    beats_of = {index: len(scene.get('beats') or ())
                for index, scene in enumerate(data.get('scenes') or [], start=1)}
    for group, anchor_key in (('ongoing_actions', 'started_at'),
                              ('open_questions', 'planted_at'),
                              ('promises', 'made_at')):
        for item in data.get(group) or []:
            if not isinstance(item, dict):
                continue
            anchor = _resolve_anchor(item.get(anchor_key), beats_of,
                                     errors, f'{group}.{anchor_key}')
            if anchor is not None:
                item[anchor_key] = anchor


def _resolve_reads(data: dict[str, Any], errors: list[str]) -> None:
    """状态读取的定位解析（B3 后半，先读旧值、后写新值）：wire 的 `at`
    （1-based 拍序）→ 场内 beat_id；缺省定位到入场。键白名单外即拒，
    越界定位精确拒——定位错场内拍是设计者对「事实在哪一拍建立」的误解，
    不猜（Resolver 纪律）。"""
    for index, scene in enumerate(data.get('scenes') or [], start=1):
        if not isinstance(scene, dict):
            continue
        beats = len(scene.get('beats') or ())
        for read in scene.get('state_reads') or []:
            if not isinstance(read, dict):
                continue
            _reject_unknown_keys(errors, read, _READ_KEYS,
                                 f'第 {index} 场 state_reads')
            at = read.get('at')
            if at is None:
                continue
            if not isinstance(at, int) or isinstance(at, bool) or not 1 <= at <= beats:
                errors.append(f'第 {index} 场 state_reads 的读取定位非法：{at!r}'
                              f'（应为 1-{beats} 的拍序；缺省定位到入场）')
                continue
            read['at'] = _beat_id(at)


def _build_canonical(data: dict[str, Any], errors: list[str]) -> dict[str, Any]:
    """派发 ID、章切片与权重归一（I-21 的 compiler 面）；原地修改 data。"""
    data.pop('protocol_version', None)  # wire 协议版本不进 canonical
    scene_ids = []
    for index, scene in enumerate(data.get('scenes') or [], start=1):
        sid = _scene_id(index)
        scene_ids.append(sid)
        scene['scene_id'] = sid
        for beat_index, beat in enumerate(scene.get('beats') or [], start=1):
            beat['beat_id'] = _beat_id(beat_index)
    slices = []
    for chapter_ordinal, chapter in enumerate(data.get('chapters') or [], start=1):
        count = chapter.get('scenes') if isinstance(chapter, dict) else None
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            errors.append(f'第 {chapter_ordinal} 章的切片形状非法：'
                          f'{chapter.get("scenes")!r}（应为正整数场数）')
            return data
        slices.append((chapter_ordinal, chapter.get('purpose'), count))
    assigned, cursor = [], 0
    for chapter_ordinal, purpose, count in slices:
        chunk = scene_ids[cursor:cursor + count]
        cursor += count
        assigned.append({'chapter_ordinal': chapter_ordinal, 'purpose': purpose,
                         'scene_ids': tuple(chunk)})
    if cursor != len(scene_ids):
        errors.append(f'章切片总数 {cursor} 与场景总数 {len(scene_ids)} 不一致'
                      '（每章至少一场，全部场景恰好归一章）')
        return data
    data['chapters'] = assigned
    weights = data.pop('scene_weights', None)
    if weights is not None:
        if (not isinstance(weights, list) or len(weights) != len(scene_ids)
                or any(not isinstance(w, (int, float)) or isinstance(w, bool)
                       or not w > 0 for w in weights)):
            errors.append('scene_weights 必须是与场景等长的正数数组（相对篇幅重点；'
                          '归一由 compiler 完成）')
            return data
        total = float(sum(weights))
        data['chapter_weights'] = {sid: w / total
                                   for sid, w in zip(scene_ids, weights)}
    return data


def _check_confirmation_closures(body: BodyDesign, errors: list[str]) -> None:
    """D4 引用闭合的机械证明：确认地址真实建立、确认不早于建立（时序）。"""
    initial_addresses = {d.address(): (0, 0) for d in body.initial_state if d.op == 'set'}
    positions: dict[tuple, tuple[int, int]] = dict(initial_addresses)
    for i, scene in enumerate(body.scenes):
        for j, beat in enumerate(scene.beats):
            for delta in beat.target_delta:
                if delta.op == 'set':
                    positions.setdefault(delta.address(), (i, j))
    for scene_index, scene in enumerate(body.scenes):
        for beat_index, beat in enumerate(scene.beats):
            for delta in beat.target_delta:
                if delta.confirms is None:
                    continue
                key = delta.confirms.address()
                established = positions.get(key)
                if established is None:
                    errors.append(
                        f'{scene.scene_id}#{beat.beat_id} 的读者确认地址从未建立：{key}'
                        '（confirms 必须指向 initial_state 或某拍实际确立的 set）')
                    continue
                if established > (scene_index, beat_index):
                    errors.append(
                        f'{scene.scene_id}#{beat.beat_id} 的读者确认早于事实建立：'
                        f'{key} 建立于 {established}（确认不得早于建立）')


def assemble_body_design(
    wire: dict[str, Any], *, known_char_ids: set[str],
    constraints: OutputConstraints | None = None,
    char_canonicals: Mapping[str, str] | None = None,
    registered_surfaces: Mapping[str, set[str]] | None = None,
) -> tuple[BodyDesign | None, list[str]]:
    """Design wire（stage4-design-v1）→ canonical BodyDesign。

    纯函数：不落盘、不调 LLM、不改输入 wire（deepcopy 后组装）。全部错误聚合
    返回，有错不产出半份 canonical（I-34）。
    """
    errors: list[str] = []
    if wire.get('protocol_version') != STAGE4_DESIGN_PROTOCOL:
        errors.append(f'protocol_version 必须是 {STAGE4_DESIGN_PROTOCOL}，'
                      f'实得 {wire.get("protocol_version")!r}（I-13 无隐式迁移）')
    _reject_unknown_keys(errors, wire, _WIRE_KEYS, 'Design')
    for index, scene in enumerate(wire.get('scenes') or [], start=1):
        if isinstance(scene, dict):
            if not _reject_unknown_keys(errors, scene, _SCENE_KEYS, f'第 {index} 场'):
                continue
            for beat_index, beat in enumerate(scene.get('beats') or [], start=1):
                if isinstance(beat, dict):
                    _reject_unknown_keys(errors, beat, _BEAT_KEYS,
                                         f'第 {index} 场第 {beat_index} 拍')
    for item in wire.get('initial_state') or []:
        if isinstance(item, dict):
            _reject_unknown_keys(errors, item, _INITIAL_KEYS, f'初始态 {item.get("key")}')
    if errors:
        return None, errors
    data = deepcopy(wire)
    data.setdefault('graph_id', 'pg_main')
    _normalize(data)
    _normalize_povs(data, char_canonicals)
    _normalize_delta(data)
    _resolve_cross_chapter(data, errors)
    if not errors:
        _build_canonical(data, errors)
    if not errors:
        _resolve_reads(data, errors)
    if errors:
        return None, errors
    try:
        body = BodyDesign.model_validate(data)
    except (ValidationError, TypeError) as exc:
        return None, [f'book_design 构造失败：{exc}']
    if constraints and len(body.chapters) != constraints.target_chapter_count:
        errors.append('目标章数与 OutputConstraints 不一致')
    addressed = list(body.initial_state)
    surfaces = list(body.initial_surfaces)
    for scene in body.scenes:
        for cid in (scene.driver.focal_actor, scene.delivery.pov):
            if cid not in known_char_ids and cid != 'narrator':
                errors.append(f'{scene.scene_id} 引用未知 char_id {cid}')
        # 读取定位闭合（B3 后半）：拍读取的 at 必须是本场真实拍（先读旧值、
        # 后写新值的定位面；时序闭合归 compile 的章闭包证明）。
        beat_ids = {b.beat_id for b in scene.beats}
        for read in scene.state_reads:
            if read.at is not None and read.at not in beat_ids:
                errors.append(f'{scene.scene_id} 的读取定位 {read.at!r} 不是本场真实拍')
        addressed.extend(scene.state_reads)
        for beat in scene.beats:
            for cid in beat.participants:
                if cid not in known_char_ids:
                    errors.append(f'{scene.scene_id}#{beat.beat_id} participants 引用未知 char_id {cid}')
            addressed.extend(beat.target_delta)
            surfaces.extend(beat.surface_introductions)
    for item in addressed:
        for cid in (*item.subjects, *((item.holder,) if item.holder else ())):
            if cid not in known_char_ids:
                errors.append(f'状态 {item.key} 引用未知 char_id {cid}')
    for item in surfaces:
        if item.char_id not in known_char_ids:
            errors.append(f'称谓释放引用未知 char_id {item.char_id}')
            continue
        if registered_surfaces is None:
            continue
        # 称谓面闭合：放行的每个字面都必须是 Characters 登记过的 surface。
        # 未登记字面进替换表会找不到对应新名（F7e 评估实证：Design 放行的称谓
        # 没和登记面核对）。在生产端尽早失败，不走交付端残留检查兜底。
        allowed = registered_surfaces.get(item.char_id, set())
        for surface in item.surfaces:
            if surface not in allowed:
                errors.append(
                    f'称谓释放引用未登记面 {surface!r}（{item.char_id} 已登记：'
                    f'{sorted(allowed)}）')
    # 结构化跨章条目的人物闭集（book_style/voice/goal/action/promise 的主体
    # 必须是本书人物；漏报会让写手拿到不存在人物的声线或目标）。
    for group in (body.character_voices, body.standing_goals, body.ongoing_actions):
        for item in group:
            if item.char_id not in known_char_ids:
                errors.append(f'{type(item).__name__} 引用未知 char_id {item.char_id}')
    for item in body.promises:
        for cid in (item.maker, *((item.to,) if item.to else ())):
            if cid not in known_char_ids:
                errors.append(f'Promise 引用未知 char_id {cid}')
    _check_confirmation_closures(body, errors)
    return (None, errors) if errors else (body, [])
