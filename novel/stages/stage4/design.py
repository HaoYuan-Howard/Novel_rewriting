"""Body Design wire 的确定性组装与派生面生成（B3 前半重写；R2 升 v2）。

【compiler 生成 ID／归章／权重（I-21，B3 前半）】
Design wire（`stage4-design-v2`）只表达选择、结构与相对篇幅重点——模型不输出
任何 ID、复合引用或归一权重。本模块是唯一派生点：
  ① 场景/拍 ID 按列表序派发：`sc{序:02d}`／`b{序:02d}`；wire 带 scene_id／
     beat_id 键即拒（「不写可派生字段」的机械面，Q-1 白名单）。
  ② 归章是章切片：wire 的 chapters=[{purpose, scenes:N, entry?}]，compiler 按
     场景阅读序连续切片派发 ChapterAssignment（章号连续、全覆盖、每章非空由
     构造保证）；entry（R2 章缝合，裁决 7）第 1 章可省——compiler 填固定开篇，
     带值必须逐字等于开篇形态，其余章必填（缺失即拒，不猜）。
  ③ 权重归一：wire 的 scene_weights 是与场景等长的正数数组（相对重点），
     compiler 归一成和为 1 的 {scene_id: weight}；缺省不登记（下游均分）。
  ④ 线程编号（R2）：beats[].threads 的本地标签（open 带 `tag`，advance/close
     带 `ref` 引用）由 compiler 按 open 先后派发 `th{nn}`；未开启先引用、
     重复开标签、关闭后操作精确拒。
  ⑤ initial_state 一律 book 作用域（schema 强制），wire 不带 scope，compiler 填。
  ⑧ 状态读取定位（B3 后半）：state_reads 的 "at"（1-based 拍序）解析成场内
     beat_id，缺省定位到入场——先读旧值、后写新值；定位闭合在本模块校验，
     读取时序闭合归 compile 的章闭包证明。

【确定性归一（F5e 真书实证；不补猜语义）】
  ⑥ 非 knowledge 域的 `knowledge_mode` 一律剥离——schema 只允许 knowledge 带
     模式；模型会在 reader 揭示上装饰 `'known'`，该值在其它域无语义可保留。
  ⑦ `delivery.pov` 两级形状归一（均无损）：单元素字符串数组（B6-A 实证：模型
     按「本场有谁」填 `['yun_niang']`）先折叠成该字符串；再对以某人物 canonical
     开头的字符串（F5e 实证：`苏芸娘第三人称限知`）按 canonical→char_id 唯一
     前缀匹配解析。多元素数组、空或非字符串元素不猜「主要视角是谁」——逐场
     typed 报错，交由修订轮或人工修复；无匹配或多义匹配不归一，交由 char_id
     校验报错。
【R8b／v3（D8 修订 2026-09-29）】wire 形状改由 pydantic 模型族
（`contracts/schema/design.py::DesignWireV3`）**一次校验、合并报告**——
初始态与拍上 target_delta 分开建模（拍上 scope 必填、无默认；初始态恒 book），
逐字段错误按位置摊开，不再逐层早退；pov 只收字符串且闭集外直拒（单元素
数组折叠与 canonical 前缀匹配删除，I-21）。

【协议与键白名单】wire 顶层 `protocol_version` 必须逐字等于
`STAGE4_DESIGN_PROTOCOL`（I-13：漏带/错带即拒，无隐式迁移）；顶层与各嵌套层
键白名单外的键拒绝，不静默丢弃（Q-1）。v2 起 standing_goals／ongoing_actions／
open_questions／promises 四键在白名单外——四个静态跨章列表已退役（AUD-04），
跨章语义走拍上的 threads。

【读者确认的事实地址（D4/R-6.11）】reader 域 set 的 `confirms` 必填由 schema
构造期表达；这里做引用闭合的机械证明：确认地址必须真实建立（initial_state 或
某拍 delta 的 set），且确认位置不早于建立位置。
"""
from __future__ import annotations

from typing import Any, Mapping

from pydantic import ValidationError

from contracts.schema.design import BodyDesign, ChapterEntry
from contracts.schema.grounding import OutputConstraints
from contracts.schema.workflow import STAGE4_DESIGN_PROTOCOL

#: wire 顶层键白名单（Q-1：多余键在 compile 入口报错，不静默丢弃）。v2 起四个
#: 静态跨章列表（standing_goals／ongoing_actions／open_questions／promises）
#: 不在白名单内——已退役（AUD-04），旧键当场拒读。
#: 状态读取声明（B3 后半）：地址四段＋可选读取定位 `at`（1-based 拍序，
#: compiler 解析成场内 beat_id；缺省定位到入场＝先读旧值）。
_READ_KEYS = frozenset({'domain', 'key', 'subjects', 'holder', 'at'})
#: 线程操作（R2 v2）：open 带 tag/kind/subject/text；advance 带 ref/progress；
#: close 带 ref/ending/resolution（键集按 op 收紧，多余键拒绝）。
_THREAD_KEYS = frozenset({'op', 'tag', 'ref', 'kind', 'subject', 'text',
                          'progress', 'ending', 'resolution'})
#: 章切片（R2 v2）：purpose＋场数＋可选 entry（第 1 章可省）。
_CHAPTER_KEYS = frozenset({'purpose', 'scenes', 'entry'})
_ENTRY_KEYS = frozenset({'transition', 'carryover', 'recap'})


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


def _normalize_delta(data: dict[str, Any]) -> None:
    """initial_state 统一 book 作用域（schema 强制）；wire 不带 scope 键。"""
    for item in data.get('initial_state') or []:
        if isinstance(item, dict):
            item.pop('scope', None)
            item['scope'] = 'book'


def _resolve_threads(data: dict[str, Any], errors: list[str]) -> None:
    """线程本地标签 → canonical `th{nn}`（R2 v2，I-21：编号由 compiler 派发）。

    按阅读序扫 beats[].threads：open 携带 `tag`（全书唯一本地标签）并按 open
    先后派发 `th{nn}`；advance/close 经 `ref` 引用标签，compiler 换成正式 id。
    重复开同一标签、引用未开启的标签、键集与 op 不符在此精确拒；close 后继续
    操作、结局闭集越界由 canonical schema 构造期拒绝（M8 同判据）。
    """
    dispatch: dict[str, str] = {}      # tag -> th{nn}
    counter = 0

    def _thread_id() -> str:
        nonlocal counter
        counter += 1
        return f'th{counter:02d}'

    for index, scene in enumerate(data.get('scenes') or [], start=1):
        if not isinstance(scene, dict):
            continue
        for beat_index, beat in enumerate(scene.get('beats') or [], start=1):
            if not isinstance(beat, dict):
                continue
            where = f'第 {index} 场第 {beat_index} 拍 threads'
            threads = beat.get('threads') or []
            if not isinstance(threads, list):
                errors.append(f'{where} 必须是数组')
                continue
            resolved = []
            for item in threads:
                if not isinstance(item, dict) or not _reject_unknown_keys(
                        errors, item, _THREAD_KEYS, where):
                    continue
                op = item.get('op')
                if op == 'open':
                    tag = item.get('tag')
                    if not isinstance(tag, str) or not tag.strip():
                        errors.append(f'{where} 的 open 必须带本地标签 tag')
                        continue
                    if tag in dispatch:
                        errors.append(f'{where} 重复开启标签 {tag!r}（线程只能开一次）')
                        continue
                    thread_id = _thread_id()
                    dispatch[tag] = thread_id
                    # v3：wire 模型 dump 会带出全部 None 默认字段（tag/ref/
                    # progress…）；canonical ThreadAction 闭集拒绝多余键——
                    # 组装时剥 None 与 tag，只留真实载荷。
                    resolved.append({**{k: v for k, v in item.items()
                                        if k != 'tag' and v is not None},
                                     'ref': thread_id})
                elif op in ('advance', 'close'):
                    ref = item.get('ref')
                    if not isinstance(ref, str) or ref not in dispatch:
                        errors.append(
                            f'{where} 的 {op} 引用了未开启的线程标签：{ref!r}')
                        continue
                    resolved.append({**{k: v for k, v in item.items()
                                        if k != 'ref' and v is not None},
                                     'ref': dispatch[ref]})
                else:
                    errors.append(f'{where} 的 op 必须是 open/advance/close，'
                                  f'实得 {op!r}')
            beat['threads'] = resolved


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
        if not isinstance(chapter, dict) or \
                not _reject_unknown_keys(errors, chapter, _CHAPTER_KEYS,
                                         f'第 {chapter_ordinal} 章'):
            errors.append(f'第 {chapter_ordinal} 章的切片形状非法：'
                          f'{chapter!r}（应为 {{purpose, scenes, entry?}}）')
            return data
        count = chapter.get('scenes')
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            errors.append(f'第 {chapter_ordinal} 章的切片形状非法：'
                          f'{chapter.get("scenes")!r}（应为正整数场数）')
            return data
        entry_raw = chapter.get('entry')
        if isinstance(entry_raw, dict):
            _reject_unknown_keys(errors, entry_raw, _ENTRY_KEYS,
                                 f'第 {chapter_ordinal} 章 entry')
        try:
            entry = (ChapterEntry.model_validate(entry_raw)
                     if entry_raw is not None else None)
        except Exception as exc:      # 形状错误聚合进 errors，不在此抛
            errors.append(f'第 {chapter_ordinal} 章 entry 形状非法：{exc}')
            return data
        if chapter_ordinal == 1:
            # 第 1 章固定为开篇（裁决 7 的唯一例外：不是设计选择，compiler 填）；
            # 带值必须等于开篇形态——不静默改写模型给的错值。
            if entry is not None and entry != ChapterEntry.opening():
                errors.append('第 1 章的 entry 固定为开篇（new_scene＋do_not_recap'
                              '＋无 carryover）；可省略由 compiler 填，不得另行声明')
                return data
            entry = ChapterEntry.opening()
        elif entry is None:
            errors.append(f'第 {chapter_ordinal} 章缺少 entry（章间承接由 Design '
                          '声明，编译器不猜；R2.6）')
            return data
        slices.append((chapter_ordinal, chapter.get('purpose'), count, entry))
    assigned, cursor = [], 0
    for chapter_ordinal, purpose, count, entry in slices:
        chunk = scene_ids[cursor:cursor + count]
        cursor += count
        assigned.append({'chapter_ordinal': chapter_ordinal, 'purpose': purpose,
                         'scene_ids': tuple(chunk), 'entry': entry})
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


def _wire_model_errors(exc: Exception) -> list[str]:
    """pydantic ValidationError → 按位置展开的逐字段错误（一次全报，R8b）。

    B6-A canary 实证：手写键白名单逐层早退＋末尾一次性 `str(exc)` 报错墙，
    四轮修订分别卡在 scope→avoid→pov→scope。这里把每条错误的**位置模式**
    （`scenes[2].delivery.pov`）逐条摊开，与其它面的错误合并成一份报告。
    """
    entries: list[str] = []
    for err in exc.errors():    # type: ignore[union-attr]
        loc = '.'.join(str(part) for part in err.get('loc') or ()) or '<顶层>'
        entries.append((loc, str(err.get('type') or ''), err.get('msg')))
    # 子项已报错的容器级 too_short 是连带噪声，不单列（R8b.3）。
    if any(kind != 'too_short' for _loc, kind, _msg in entries):
        entries = [item for item in entries if item[1] != 'too_short']
    return [f'{loc}：{msg}（按 wire v3 形状逐处修正）'
            for loc, _kind, msg in entries] or [str(exc)]


def _check_pov_closed_set(data: dict[str, Any], known_char_ids: set[str],
                          errors: list[str]) -> None:
    """pov 严格闭集（R8b）：只收本书 char_id 或 "narrator"。

    v2 的单元素数组折叠与 canonical 前缀匹配已删（I-21：不按名字猜人）——
    闭集外直接拒，与其它错误合并一份报告。
    """
    for index, scene in enumerate(data.get('scenes') or [], start=1):
        if not isinstance(scene, dict):
            continue
        pov = (scene.get('delivery') or {}).get('pov')
        # 非字符串 pov（数组等）由 pydantic 形状面报，这里只管字符串闭集。
        if isinstance(pov, str) and pov != 'narrator'                 and pov not in known_char_ids:
            errors.append(
                f'第 {index} 场 delivery.pov 必须是本书 char_id 或 "narrator"'
                f'（闭集），实得 {pov!r}——一场只有一个视角人物，不写数组、'
                '不写称呼或描述句；可用值只取输入身份投影给出的 char_id 字面')


def assemble_body_design(
    wire: dict[str, Any], *, known_char_ids: set[str],
    constraints: OutputConstraints | None = None,
    registered_surfaces: Mapping[str, set[str]] | None = None,
) -> tuple[BodyDesign | None, list[str]]:
    """Design wire（stage4-design-v3）→ canonical BodyDesign。

    纯函数：不落盘、不调 LLM、不改输入 wire（deepcopy 后组装）。**一次校验、
    合并报告**（R8b）：wire 形状由 pydantic 模型族（`DesignWireV3`）一次判，
    逐字段错误按位置摊开；闭集（人物／称谓）与语义闭合面在通过形状后同一份
    报告聚合，不再逐层早退遮住后层（B6-A canary 四轮修订实证）。有错不产出
    半份 canonical（I-34）。
    """
    from contracts.schema.design import DesignWireV3

    errors: list[str] = []
    if wire.get('protocol_version') != STAGE4_DESIGN_PROTOCOL:
        errors.append(f'protocol_version 必须是 {STAGE4_DESIGN_PROTOCOL}，'
                      f'实得 {wire.get("protocol_version")!r}（I-13 无隐式迁移）')
    try:
        model = DesignWireV3.model_validate(wire)
    except ValidationError as exc:
        # 形状失败不遮闭集面（R8b：canary 第 1 份实证——pov 描述句与 scope
        # 缺失同报，修订一轮收掉全部三类，而不是逐层早退打四轮）。
        _check_pov_closed_set(wire, known_char_ids, errors)
        return None, errors + _wire_model_errors(exc)
    data = model.model_dump(mode='json')
    data['graph_id'] = 'pg_main'
    _normalize(data)
    _normalize_delta(data)
    _check_pov_closed_set(data, known_char_ids, errors)
    _resolve_threads(data, errors)
    if not errors:
        _build_canonical(data, errors)
    if not errors:
        _resolve_reads(data, errors)
    if errors:
        return None, errors
    try:
        body = BodyDesign.model_validate(data)
    except (ValidationError, TypeError) as exc:
        shape_errors = (_wire_model_errors(exc)
                        if isinstance(exc, ValidationError) else [str(exc)])
        return None, [f'book_design 构造失败：{entry}'
                      for entry in shape_errors]
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
    # 结构化跨章条目的人物闭集（R2：书级声线与线程主体必须是本书人物；漏报
    # 会让写手拿到不存在人物的声线或线程）。
    for item in body.character_voices:
        if item.char_id not in known_char_ids:
            errors.append(f'CharacterVoice 引用未知 char_id {item.char_id}')
    for scene in body.scenes:
        for beat in scene.beats:
            for thread in beat.threads:
                if thread.op == 'open' and thread.subject not in known_char_ids:
                    errors.append(
                        f'ThreadAction 引用未知 char_id {thread.subject}'
                        f'（{scene.scene_id}#{beat.beat_id} 的 {thread.ref}）')
    _check_confirmation_closures(body, errors)
    return (None, errors) if errors else (body, [])
