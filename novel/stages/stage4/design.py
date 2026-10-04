"""Body Design wire 的确定性组装与派生面生成（B3 前半重写；R8b 升 v3；a4 升 v5）。

【compiler 生成 ID／归章／权重（I-21，B3 前半）】
Design wire（`stage4-design-v5`）只表达选择、结构与相对篇幅重点——模型不输出
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
（`contracts/schema/design.py::DesignWireV5`）**一次校验、合并报告**——
初始态与拍上 target_delta 分开建模（拍上 scope 必填、无默认；初始态恒 book），
逐字段错误按位置摊开，不再逐层早退；pov 只收字符串且闭集外直拒（单元素
数组折叠与 canonical 前缀匹配删除，I-21）。

【协议与键白名单】wire 顶层 `protocol_version` 必须逐字等于
`STAGE4_DESIGN_PROTOCOL`（I-13：漏带/错带即拒，无隐式迁移）；顶层与各嵌套层
键白名单外的键拒绝，不静默丢弃（Q-1）。v2 起 standing_goals／ongoing_actions／
open_questions／promises 四键在白名单外——四个静态跨章列表已退役（AUD-04），
跨章语义走拍上的 threads。

【rc14 读者认知】knowledge(holder=reader) 保留统一域；known 的 set 必填
confirms。事实地址与时序闭合由 read_closure 共用判据验证，pending 投影进入
事实建立/读取场和确认场。v7 know 为逐次认知变化标拍，real 保留最终验收位。
"""
from __future__ import annotations

from typing import Any, Mapping

from pydantic import ValidationError

from contracts.schema.design import BodyDesign, ChapterEntry
from contracts.schema.grounding import OutputConstraints, ReconstructionBrief
from contracts.schema.state import EffectDomain
from contracts.schema.workflow import STAGE4_DESIGN_PROTOCOL
from contracts.schema.source import NameProjection, NameStructure
from novel.core.validation import ValidationIssue
from novel.stages.stage4.design_paths import design_issue
from novel.stages.common import expand_surface_releases
from novel.stages.stage4.read_closure import confirmation_closure_errors, resolve_design_reads
from novel.stages.stage4.materials import validate_design_materials
from novel.stages.stage4.slim import (
    FactLockMark, design_fact_marks, design_placements, expand_design_wire,
)

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
_ENTRY_KEYS = frozenset({'transition', 'carryover', 'recap', 'anchor'})


def _wire_real_ids(wire: dict[str, Any]) -> set[str]:
    """v6 wire 里出现过的义务标注号（覆盖闸用；纯读）。"""
    return {str(ob) for scene in wire.get('scenes') or ()
            if isinstance(scene, dict)
            for beat in scene.get('beats') or () if isinstance(beat, dict)
            for ob in beat.get('real') or ()}


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
            if entry_raw and entry_raw.get('anchor') is not None:
                anchor = entry_raw['anchor']
                entry_raw['anchor'] = f'{_scene_id(anchor["scene"])}#{_beat_id(anchor["beat"])}'
            entry = (ChapterEntry.model_validate(entry_raw)
                     if entry_raw is not None else None)
        except ValidationError as exc:
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
    return [f'{loc}：{msg}（按 wire 形状逐处修正）'
            for loc, _kind, msg in entries] or [str(exc)]


def _wire_shape_issues(exc: Exception, prefix: str) -> list:
    """v8 瘦 wire 的 pydantic 错误：位置 `loc` 本身就是 wire 路径（局部修订的编辑范围）。

    与 `_wire_model_errors` 同一套去噪（子项已报错的容器级 too_short 不单列），文案一致，
    只是每条问题多带规则码与路径。仅用于直接校验 v8 wire 的那一次——中间 v5 形状的
    错误位置不是 wire 路径，仍走 `_wire_model_errors`。
    """
    entries = [(tuple(err.get('loc') or ()), str(err.get('type') or ''), err.get('msg'))
               for err in exc.errors()]    # type: ignore[union-attr]
    if any(kind != 'too_short' for _loc, kind, _msg in entries):
        entries = [item for item in entries if item[1] != 'too_short']
    return [design_issue(
        f'{prefix}：{".".join(str(p) for p in loc) or "<顶层>"}：{msg}（按 wire 形状逐处修正）',
        code='design_wire_shape', location=loc, paths=(loc,) if loc else ())
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


def _check_expression_refs(body: BodyDesign, expression_requirements,
                           errors: list[str], *, locatable: bool = False) -> None:
    """a4：拍的 expression_refs 闭集——只能引用 Brief.expression 的 ex_id，
    narration 是书级机制不上拍（正向表达任务落具体拍；书级表达只写机制）。
    `locatable`＝输入是 v8 wire：按画面序下标给出 `ex` 的 wire 路径。"""
    kinds = {item.ex_id: item.kind for item in expression_requirements}
    for scene_i, scene in enumerate(body.scenes):
        for beat_j, beat in enumerate(scene.beats):
            where = ('scenes', scene_i, 'beats', beat_j, 'ex')
            paths = (where,) if locatable else ()
            for ex_id in beat.expression_refs:
                if ex_id not in kinds:
                    errors.append(design_issue(
                        f'{scene.scene_id}#{beat.beat_id} 的 expression_refs 引用'
                        f'闭集外 ex_id：{ex_id!r}（只能取 Brief.expression 给出的项）',
                        code='design_expression_ref', location=(*where, ex_id), paths=paths))
                elif kinds[ex_id] == 'narration':
                    errors.append(design_issue(
                        f'{scene.scene_id}#{beat.beat_id} 的 expression_refs 引用'
                        f'narration（{ex_id}）——书级表达只写机制，不上具体拍',
                        code='design_expression_ref', location=(*where, ex_id), paths=paths))


_TEMPORAL_LOCKS = ('planned', 'expected', 'conditional')


def _lock_issue(message: str, code: str, location: tuple, *paths: tuple) -> ValidationIssue:
    """事实锁接受问题：规则码与 wire 路径随判据生成，局部修订按路径定位而不解析文案。

    `location` 是稳定位置（身份的一部分，与措辞无关）；`wire_paths` 是责任方可编辑的
    最小完整单元（承载状态行，或未兑现时承载该义务的拍）。
    """
    return design_issue(message, code=code, location=location, paths=paths)


def _mark_where(mark: FactLockMark) -> str:
    return f'第 {mark.scene + 1} 场第 {mark.beat + 1} 拍 state[{mark.state}]'


def _check_fact_marks(marks: tuple[FactLockMark, ...], fact_locks: Mapping[str, Any],
                      errors: list[str]) -> set[str]:
    """事实锁标注的结构接受（CS-F2）：句柄闭集、一锁一承载、模式与操作同锁一致。

    返回已被**合法**标注兑现的锁集合。只证明结构——标注的状态内容是否在语义上
    满足锁（例如把「暂缓和离」绑到「赏花」）不在此判，那是需要标定的语义核验，
    未运行即保持 unver（I-42）；这里也不因状态文本含源命题字面而改写它。
    """
    realized: set[str] = set()
    first: dict[str, FactLockMark] = {}
    for mark in marks:
        where = _mark_where(mark)
        lock = fact_locks.get(mark.kf_id)
        if lock is None:
            errors.append(_lock_issue(
                f'{where} 的 lock {mark.kf_id} 未在 Brief 登记'
                '（lock 只能取输入事实锁清单给出的句柄）',
                'fact_lock_unregistered', mark.path, mark.path))
            continue
        if mark.kf_id in first:
            errors.append(_lock_issue(
                f'{where} 重复承载事实锁 {mark.kf_id}（首个承载位在 '
                f'{_mark_where(first[mark.kf_id])}）：一个锁只由一条已选状态承载，'
                '不要为同一事实再写一条状态', 'fact_lock_duplicate', mark.path,
                mark.path))
            continue
        first[mark.kf_id] = mark
        if lock.kind == 'unanswered':
            errors.append(_lock_issue(
                f'{where} 标注了原文未回答锁 {mark.kf_id}：它是禁区、不承载任何状态，'
                '正文不得给出答案（去掉该 lock，且不要写出答案）',
                'fact_lock_unanswered', mark.path, mark.path))
        elif lock.kind in _TEMPORAL_LOCKS:
            if mark.op != 'set' or mark.modality != lock.modality:
                errors.append(_lock_issue(
                    f'{where} 承载的事实锁 {mark.kf_id} modality 与锁不一致：锁＝'
                    f'{lock.modality}（尚未发生），实得 op={mark.op}/modality='
                    f'{mark.modality}（计划不得写成已发生，C2）',
                    'fact_lock_modality', mark.path, mark.path))
            else:
                realized.add(mark.kf_id)
        elif mark.op != 'unknown':
            errors.append(_lock_issue(
                f'{where} 承载的事实锁 {mark.kf_id} 是值未知锁（源文未给值）：须标在 '
                f'op=unknown 的状态上，不得补值（C9），实得 op={mark.op}',
                'fact_lock_value', mark.path, mark.path))
        else:
            realized.add(mark.kf_id)
    return realized


def _check_lock_acceptance(body: BodyDesign, brief: ReconstructionBrief,
                           errors: list[str], *, marks: tuple[FactLockMark, ...] = (),
                           obligations: Mapping[str, Any] | None = None,
                           real_sites: Mapping[str, list[tuple[int, int]]] | None = None) -> None:
    """a4 接受校验：kp 键规则、kf 标注与必保锁兑现（锁注册表来自 Brief，revise_self）。

    · kp 键（knowledge 域）：持有者须有锁且模式被授权——授权集＝该 (holder,
      kp) 的全部锁模式（known 只能来自 known 锁，C1）；
    · kp 键（reader 域 set＝读者确认）：须有 reader 的 known 锁（线索/怀疑
      不是确认，C6）；
    · kf 标注（CS-F2）：句柄闭集、一锁一承载；时态锁须标在同 modality 的 set 上
      （预计不得写成已发生，C2），值未知锁须标在 op=unknown 上（不得补值，C9），
      原文未回答锁是禁区、不承载状态；kf 句柄作状态键一律拒；
    · 必保义务的知情/时态锁须被对应键/标注兑现（knowledge_changes/realize_locks）。
    位置顺序（知情升级/确认不早于授权承载位）归 a5 的 compile Guard，不在此。
    `marks` 来自 v8 wire（`design_fact_marks`）；无 wire 的直接 v5 输入没有标注通道，
    必保事实锁因此恒未兑现——v5 只是内部中间形状，生产链不直接收它。
    """
    propositions = {p.kp_id: p for p in brief.propositions}
    fact_locks = {f.kf_id: f for f in brief.fact_locks}
    authorized: dict[tuple[str, str], set[str]] = {}
    for lock in brief.knowledge_locks:
        authorized.setdefault((lock.holder, lock.kp), set()).add(lock.mode)

    realized_kp: set[tuple[str, str, str]] = set()
    delta_sites: list[tuple[str, Any]] = [
        ('initial_state', delta) for delta in body.initial_state]
    for scene in body.scenes:
        for beat in scene.beats:
            delta_sites.extend(
                (f'{scene.scene_id}#{beat.beat_id}', delta)
                for delta in beat.target_delta)
    for where, delta in delta_sites:
        key = delta.key
        if key.startswith('kp_'):
            if key not in propositions:
                errors.append(f'{where} 的 kp 键未在 Brief 登记：{key}'
                              '（kp 句柄只能取 Brief.propositions 给出的项）')
                continue
            if delta.domain is EffectDomain.KNOWLEDGE:
                if delta.op == 'set':
                    # D5 统一后读者揭示也是 knowledge(holder=reader)——授权
                    # 判据同一把尺：known 只能来自 known 锁（线索、误导与
                    # 怀疑不是确认，C6）。
                    allowed = authorized.get((delta.holder, key), set())
                    if delta.knowledge_mode not in allowed:
                        errors.append(
                            f'{where} 的 kp 键 {key} 持有者 {delta.holder} 无锁'
                            f'或模式越权：{delta.knowledge_mode}（授权：'
                            f'{sorted(allowed) or "无"}；known 只能来自 known 锁）')
                    realized_kp.add((delta.holder, key, delta.knowledge_mode))
        elif key.startswith('kf_'):
            errors.append(
                f'{where} 的状态键 {key} 是事实锁句柄：kf 句柄不是状态键，'
                '事实锁在已选状态上用 lock 标注（stage4-design-v8）')
    realized_kf = _check_fact_marks(marks, fact_locks, errors)
    ob_of = {req.core_id: ob for ob, req in (obligations or {}).items()}
    for req in brief.requirements:
        label = ob_of.get(req.core_id, req.core_id)
        for change in req.knowledge_changes if req.must_preserve else ():
            if (change.holder, change.kp, change.mode) not in realized_kp:
                errors.append(
                    f'必保义务 {label} 的知情锁未兑现：{change.kp} 需由持有者 '
                    f'{change.holder} 以模式 {change.mode} 承载')
        for kf in req.realize_locks:
            if kf not in realized_kf:
                lock = fact_locks.get(kf)
                expected = (f'op=set 且 modality={lock.modality}'
                            if lock is not None and lock.kind in _TEMPORAL_LOCKS
                            else 'op=unknown（不得补值）'
                            if lock is not None and lock.kind == 'unknown_value'
                            else '该锁不可承载')
                sites = [('scenes', s, 'beats', b)
                         for s, b in (real_sites or {}).get(label, ())]
                errors.append(_lock_issue(
                    f'必保义务 {label} 的事实锁未兑现：{kf}——在承载该义务的拍上，让表达'
                    f'该事实的那条状态带上 lock（{expected}）；该拍还没有这样的状态就写这一条'
                    '并标注，不要为同一事实写两条',
                    'fact_lock_unrealized', ('fact_lock', kf), *sites))


def assemble_body_design(
    wire: dict[str, Any], *, known_char_ids: set[str],
    constraints: OutputConstraints | None = None,
    registered_surfaces: Mapping[str, set[str]] | None = None,
    name_projection: NameProjection | None = None,
    name_structure: NameStructure | None = None,
    expression_requirements,
    source_narration,
    brief: ReconstructionBrief | None = None,
    obligations: Mapping[str, Any] | None = None,
) -> tuple[BodyDesign | None, list[str]]:
    """正式组装出口保持失败关闭；候选检查另由 inspect_body_design 提供。"""
    body, errors = inspect_body_design(
        wire, known_char_ids=known_char_ids, constraints=constraints,
        registered_surfaces=registered_surfaces, name_projection=name_projection,
        name_structure=name_structure, expression_requirements=expression_requirements,
        source_narration=source_narration, brief=brief, obligations=obligations)
    return (None, errors) if errors else (body, [])


def inspect_body_design(
    wire: dict[str, Any], *, known_char_ids: set[str],
    constraints: OutputConstraints | None = None,
    registered_surfaces: Mapping[str, set[str]] | None = None,
    name_projection: NameProjection | None = None,
    name_structure: NameStructure | None = None,
    expression_requirements,
    source_narration,
    brief: ReconstructionBrief | None = None,
    obligations: Mapping[str, Any] | None = None,
) -> tuple[BodyDesign | None, list[str]]:
    """Design wire（stage4-design-v5／v8）→ canonical BodyDesign。

    纯函数：不落盘、不调 LLM、不改输入 wire（deepcopy 后组装）。**一次校验、
    合并报告**（R8b）：wire 形状由 pydantic 模型族一次判，逐字段错误按位置
    摊开；闭集（人物／称谓）与语义闭合面在通过形状后同一份报告聚合，不再
    逐层早退遮住后层（B6-A canary 四轮修订实证）。可构造的候选即使有错也
    返回，供 Binding 与实际写手材料继续预检；调用者不得据此发布。
    正式组装出口 assemble_body_design 仍有错即不返回产物（I-34）。
    a4：`brief` 传入时追加锁定项接受校验（kp/kf 键规则＋必保锁兑现＋
    expression_refs 闭集，`_check_lock_acceptance`）；生产链
    （`chain.run_body_design`）恒传入——缺省 None 仅供不持 Brief 的离线
    工具，跳过的是锁面，不是全书校验。
    CS-3（2026-10-03）：wire 按协议字面量路由——瘦面（stage4-design-v8）
    先经 `expand_design_wire` 派生成 v5 形状再进同一管道（obligations＝
    ob 编号→需求映射，v6 必传：real 标注闭集与知情行展开依赖它）；另加
    必保义务的 real 标注覆盖闸（未标注的 must 需求当场拦下——承载位是
    v6 的新语义面，漏标即缺承载）。
    """
    from contracts.schema.design import (
        DesignWireV5, DesignWireV8, STAGE4_DESIGN_WIRE_PROTOCOL,
    )

    errors: list[str] = []
    coverage_errors: list[str] = []
    marks: tuple[FactLockMark, ...] = ()
    real_sites: dict[str, list[tuple[int, int]]] = {}
    # 只有直接来自 v8 wire 的候选，下标才是 wire 路径（局部修订的编辑范围）；
    # 直接给 v5 中间形状的离线输入没有对应的可编辑 wire，问题不带路径。
    locatable = wire.get('protocol_version') == STAGE4_DESIGN_WIRE_PROTOCOL
    v8_wire = wire
    if locatable:
        try:
            expanded, expand_issues = expand_design_wire(
                wire, brief=brief, obligations=obligations)
        except ValidationError as exc:
            return None, _wire_shape_issues(exc, '设计瘦 wire 协议校验失败')
        if expand_issues:
            return None, [design_issue(message, code='design_wire_expand',
                                       location=tuple(path or ()),
                                       paths=(tuple(path),) if path else ())
                          for path, message in expand_issues]
        assert expanded is not None
        # 标注与承载位只在 v8 wire 上存在（不进中间 v5 形状与 BodyDesign）；
        # expand 已通过同一份模型校验。
        wire_model = DesignWireV8.model_validate(wire)
        marks = design_fact_marks(wire_model)
        real_sites = design_placements(wire_model)
        if obligations is not None:
            # 覆盖闸按 wire 判（real 标注不进 v5 dict——canonical 无此面）。
            annotated = _wire_real_ids(wire)
            missing = sorted(
                ob for ob, requirement in obligations.items()
                if getattr(requirement, 'must_preserve', True) and ob not in annotated)
            if missing:
                coverage_errors.append(f'必保义务未被任何拍标注承载（real）：{missing}'
                                       '——每条 must_preserve 需求至少标注一拍')
        wire = expanded
    if wire.get('protocol_version') != STAGE4_DESIGN_PROTOCOL:
        errors.append(f'protocol_version 必须是 {STAGE4_DESIGN_PROTOCOL}，'
                      f'实得 {wire.get("protocol_version")!r}（I-13 无隐式迁移）')
    try:
        model = DesignWireV5.model_validate(wire)
    except ValidationError as exc:
        # 形状失败不遮闭集面（R8b：canary 第 1 份实证——pov 描述句与 scope
        # 缺失同报，修订一轮收掉全部三类，而不是逐层早退打四轮）。
        _check_pov_closed_set(wire, known_char_ids, errors)
        return None, coverage_errors + errors + _wire_model_errors(exc)
    data = model.model_dump(mode='json')
    expressions = {item.ex_id: item.statement for item in expression_requirements}
    decisions = data['expression_decisions']
    ids = [item['ex_id'] for item in decisions]
    if len(ids) != len(set(ids)) or set(ids) != set(expressions):
        errors.append('expression_decisions 必须对输入每个 ex 恰好决定一次，不得遗漏或新增')
    narration_ids = {item.ex_id for item in expression_requirements if item.kind == 'narration'}
    if (any(item['action'] == 'keep' and item['ex_id'] in narration_ids for item in decisions)
            and model.narration != source_narration):
        errors.append('保留源叙述表达时 narration 必须与输入一致；改变须显式 change 并说明理由')
    if errors:
        return None, coverage_errors + errors
    data['expression_decisions'] = [
        {'ex_id': item['ex_id'], 'action': item['action'], 'reason': item['reason'],
         'statement': expressions[item['ex_id']] if item['action'] == 'keep' else item['target_statement']}
        for item in decisions]
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
        return None, coverage_errors + errors
    try:
        body = BodyDesign.model_validate(data)
    except (ValidationError, TypeError) as exc:
        shape_errors = (_wire_model_errors(exc)
                        if isinstance(exc, ValidationError) else [str(exc)])
        return None, coverage_errors + [f'book_design 构造失败：{entry}'
                                        for entry in shape_errors]
    errors.extend(coverage_errors)

    def issue(message: str, code: str, location: tuple, *paths: tuple):
        """带规则码与可编辑 wire 路径的问题；非 v8 候选没有可局部修订的位置。"""
        return design_issue(message, code=code, location=location,
                            paths=paths if locatable else ())

    scene_index = {s.scene_id: i for i, s in enumerate(body.scenes)}
    beat_index = {(s.scene_id, b.beat_id): j
                  for s in body.scenes for j, b in enumerate(s.beats)}

    def delta_path(scene_i: int, beat_j: int, delta_i: int):
        """canonical 拍增量序 → 产生它的 wire 行：state 行在前、know 行在后（展开顺序）。"""
        try:
            rows = v8_wire['scenes'][scene_i]['beats'][beat_j].get('state') or []
        except (KeyError, IndexError, TypeError):
            return None
        if delta_i < len(rows):
            return ('scenes', scene_i, 'beats', beat_j, 'state', delta_i)
        return ('scenes', scene_i, 'beats', beat_j, 'know')

    def listed(key: str) -> list:
        value = v8_wire.get(key) if isinstance(v8_wire, dict) else None
        return value if isinstance(value, list) else []

    def site_path(site: tuple):
        """Design 文本站点 → wire 路径；上游派生的字段（世界规则、pov 派生声线……）无路径。"""
        kind = site[0]
        if kind == 'book_style':
            return ('book_style',)
        if kind in ('chapter', 'entry'):
            return ('chapters', site[1] - 1, 'purpose' if kind == 'chapter' else 'entry')
        if kind == 'voice':
            hit = next((i for i, v in enumerate(listed('character_voices'))
                        if isinstance(v, dict) and v.get('char_id') == site[1]), None)
            return ('character_voices', hit) if hit is not None else None
        if kind == 'expression':
            hit = next((i for i, v in enumerate(listed('expression_overrides'))
                        if isinstance(v, dict) and v.get('ex_id') == site[1]), None)
            return ('expression_overrides', hit) if hit is not None else None
        if kind == 'scene' and site[2] in ('brief', 'driver') and site[1] in scene_index:
            return ('scenes', scene_index[site[1]], site[2])
        if kind == 'beat' and site[3] == 'event' and (site[1], site[2]) in beat_index:
            return ('scenes', scene_index[site[1]], 'beats', beat_index[(site[1], site[2])], 'ev')
        if kind == 'delta' and (site[1], site[2]) in beat_index:
            return delta_path(scene_index[site[1]], beat_index[(site[1], site[2])], site[3])
        if kind == 'thread' and (site[1], site[2]) in beat_index:
            return ('scenes', scene_index[site[1]], 'beats', beat_index[(site[1], site[2])],
                    'threads', site[3])
        return None

    if constraints and len(body.chapters) != constraints.target_chapter_count:
        errors.append('目标章数与 OutputConstraints 不一致')
    addressed = [(delta, ('initial_state', k)) for k, delta in enumerate(body.initial_state)]
    for cid in body.narration.focalizers:
        if cid != 'narrator' and cid not in known_char_ids:
            errors.append(f'narration 引用未知人物 {cid}')
    surfaces = [(intro, ('initial_surfaces', k))
                for k, intro in enumerate(body.initial_surfaces)]
    for scene_i, scene in enumerate(body.scenes):
        for field, cid in (('driver', scene.driver.focal_actor), ('pov', scene.delivery.pov)):
            if cid not in known_char_ids and cid != 'narrator':
                errors.append(issue(f'{scene.scene_id} 引用未知 char_id {cid}',
                                    'design_unknown_char', ('scenes', scene_i, field, cid),
                                    ('scenes', scene_i, field)))
        # 读取定位闭合（B3 后半）：拍读取的 at 必须是本场真实拍（先读旧值、
        # 后写新值的定位面；时序闭合归 compile 的章闭包证明）。
        beat_ids = {b.beat_id for b in scene.beats}
        for read_k, read in enumerate(scene.state_reads):
            if read.at is not None and read.at not in beat_ids:
                errors.append(f'{scene.scene_id} 的读取定位 {read.at!r} 不是本场真实拍')
            addressed.append((read, ('scenes', scene_i, 'reads', read_k)))
        for beat_j, beat in enumerate(scene.beats):
            for cid in beat.participants:
                if cid not in known_char_ids:
                    errors.append(issue(
                        f'{scene.scene_id}#{beat.beat_id} participants 引用未知 char_id {cid}',
                        'design_unknown_char', ('scenes', scene_i, 'beats', beat_j, 'who', cid),
                        ('scenes', scene_i, 'beats', beat_j, 'who')))
            for delta_i, delta in enumerate(beat.target_delta):
                addressed.append((delta, delta_path(scene_i, beat_j, delta_i)))
            surfaces.extend((intro, ('scenes', scene_i, 'beats', beat_j, 'release', r))
                            for r, intro in enumerate(beat.surface_introductions))
    for item, path in addressed:
        for cid in (*item.subjects, *((item.holder,) if item.holder else ())):
            # holder 的 'reader' 是保留字（D5 统一后读者揭示走
            # knowledge(holder=reader)），与 'narrator' 同级放行。
            if cid != 'reader' and cid not in known_char_ids:
                errors.append(issue(f'状态 {item.key} 引用未知 char_id {cid}',
                                    'design_unknown_char', (*(path or ('state',)), cid),
                                    *((path,) if path else ())))
    for intro, path in surfaces:
        if intro.char_id not in known_char_ids:
            errors.append(issue(f'称谓释放引用未知 char_id {intro.char_id}',
                                'design_unknown_char', (*path, intro.char_id), path))
            continue
        if registered_surfaces is None:
            continue
        # 称谓面闭合：放行的每个字面都必须是 Characters 登记过的 surface。
        # 未登记字面进替换表会找不到对应新名（F7e 评估实证：Design 放行的称谓
        # 没和登记面核对）。在生产端尽早失败，不走交付端残留检查兜底。
        allowed = set(expand_surface_releases(registered_surfaces, name_structure)
                      .get(intro.char_id, ()))
        for surface in intro.surfaces:
            if surface not in allowed:
                errors.append(issue(
                    f'称谓释放引用未登记面 {surface!r}（{intro.char_id} 已登记：'
                    f'{sorted(allowed)}）', 'design_unregistered_surface',
                    (*path, surface), path))
    # 结构化跨章条目的人物闭集（R2：书级声线与线程主体必须是本书人物；漏报
    # 会让写手拿到不存在人物的声线或线程）。
    for voice_k, item in enumerate(body.character_voices):
        if item.char_id not in known_char_ids:
            errors.append(issue(f'CharacterVoice 引用未知 char_id {item.char_id}',
                                'design_unknown_char', ('character_voices', voice_k),
                                ('character_voices', voice_k)))
    for scene_i, scene in enumerate(body.scenes):
        for beat_j, beat in enumerate(scene.beats):
            for thread_t, thread in enumerate(beat.threads):
                if thread.op == 'open' and thread.subject not in known_char_ids:
                    where = ('scenes', scene_i, 'beats', beat_j, 'threads', thread_t)
                    errors.append(issue(
                        f'ThreadAction 引用未知 char_id {thread.subject}'
                        f'（{scene.scene_id}#{beat.beat_id} 的 {thread.ref}）',
                        'design_unknown_char', where, where))
    _check_expression_refs(body, expression_requirements, errors, locatable=locatable)
    errors.extend(confirmation_closure_errors(body))
    if brief is not None:
        _check_lock_acceptance(body, brief, errors, marks=marks,
                               obligations=obligations, real_sites=real_sites)
    errors.extend(resolve_design_reads(body).errors)
    if name_projection is not None:
        errors.extend(validate_design_materials(
            body, projection=name_projection, known_char_ids=known_char_ids,
            structure=name_structure, resolve=site_path if locatable else None))
    return body, errors
