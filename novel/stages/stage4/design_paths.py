"""Design／Hook wire 的局部修订定位（CS-F1，RC-46/47）：错误带 wire 路径，按最小完整父项发起条目返修。

【为什么错误要在判定处带路径】修订控制不得从展示文案猜位置（`novel/core/validation.py`）。
本模块只定义构造与消费约定：判据在发现问题的地方生成 `ValidationIssue`，规则码与稳定
位置属身份，`context['wire_paths']` 是责任方可编辑的 wire JSON 路径；定位器只读这些
结构化字段，不解析任何错误字符串。Design（v8 wire）与 Hook（stage4-hook-design-v1
wire）共用同一约定与同一定位器——Hook 错误不带拍结构，耦合合并自然不触发。

【定位策略】（纯函数、确定性、不改 wire）
· 路径只指向**最小完整父项**：叶子标量（如一条 `state` 里的 `val`）升到它所在的数组
  元素（整条 state），保证模型替换的是一个自洽的完整对象；路径在 wire 中不存在
  （缺省字段）时升到最近的现存祖先。
· **耦合字段合并**：同一拍内有两处及以上待修路径（事实绑定、认知拍位、称谓释放、事件
  文本彼此耦合），合并为这一拍整体，避免互相矛盾的局部替换。
· **替换不重叠**：任一路径若落在另一路径之内，只保留外层，内层问题并入外层。
· **可选目标**（CS-H H2，RC-58）：错误本身不带路径、但其规则码在
  `contracts/judges/str06.py::PATCH_ROUTES` 登记 `optional`（责任位置须由模型决定：
  必保义务漏标、series 成员接受拍互斥）时，给出**候选拍闭集**（wire 全部拍）——
  条目形如 ``(路径, [错误], True)``，模型只覆盖它选中的子集（至少一个），
  回答路径必须落在闭集内。
· 其余不带路径的错误（无路由登记、执行面）、路径指向不存在的结构，或定位后无法
  覆盖全部错误时返回 None——本轮退整份返修，不静默漏项、不无声缩小范围。
`ask_json` 对该协议的其余纪律不变：合并版重跑**完整**候选预检（不是只验 patch），
问题位置集合重复／回归即停，轮数与累计预算沿用现役上限。
"""
from __future__ import annotations

from typing import Any, Sequence

from contracts.judges.str06 import PATCH_ROUTES
from novel.core.validation import ValidationIssue

Path = tuple  # (str | int, ...)：自 wire 根起的 JSON 路径


def design_issue(message: str, *, code: str, location: Path | tuple,
                 paths: Sequence[Path] = (), context: dict | None = None) -> ValidationIssue:
    """Design 判据问题：规则码＋稳定位置＋可编辑 wire 路径（可为空＝无法局部定位）。

    `location` 进入身份（与措辞无关），`paths` 才是局部修订的编辑范围；二者分开是因为
    稳定位置有时是『某个锁』而不是一个路径（例如必保锁未被任何状态承载）。
    """
    merged = dict(context or {})
    merged['wire_paths'] = [list(p) for p in paths]
    return ValidationIssue(message, code=code, location=tuple(location), owner='design',
                           context=merged)


def wire_paths_of(error: object) -> tuple[Path, ...] | None:
    """错误携带的可编辑路径；不是结构化问题、或没有路径时返回 None。"""
    context = getattr(error, 'context', None)
    if not isinstance(context, dict):
        return None
    paths = context.get('wire_paths')
    if not paths:
        return None
    return tuple(tuple(p) for p in paths)


def _walk(document: Any, path: Path) -> Any:
    node = document
    for part in path:
        if isinstance(part, int):
            if not isinstance(node, list) or not 0 <= part < len(node):
                raise KeyError(path)
            node = node[part]
        else:
            if not isinstance(node, dict) or part not in node:
                raise KeyError(path)
            node = node[part]
    return node


def _complete_parent(wire: Any, path: Path) -> Path | None:
    """最小完整父项：先升到现存祖先，再把叶子标量升到所在数组元素。"""
    existing = path
    while existing:
        try:
            _walk(wire, existing)
            break
        except KeyError:
            existing = existing[:-1]
    if not existing:
        # 顶层可选键缺失允许补建（CS-H H3：追加式修复如 expression_overrides，
        # 值＝完整替换数组）；其余（中途缺键／下标越界）仍是不可定位的坏路径。
        if len(path) == 1 and isinstance(path[0], str) and isinstance(wire, dict):
            return path
        return None            # 连顶层字段都不存在：不能局部替换，退整份
    # 只允许「缺省的键」升到现存的对象祖先（如拍里没写 real）；下标越界、或要升到的祖先
    # 是整个数组，说明路径指向的条目根本不存在——升上去等于把整个数组重发，违背「最小完整
    # 父项」，也无从核验。这种情形退整份修订。
    if existing != path and isinstance(path[len(existing)], int):
        return None
    node = _walk(wire, existing)
    if existing != path and isinstance(node, list):
        return None
    if isinstance(node, (dict, list)):
        return existing
    # 叶子标量：升到最近的数组元素（path 中最后一个 int 下标处），使模型替换的是自洽的完整
    # 对象（一条 state、一个拍、一章切片……）。例外：场景是容纳全部拍的大容器，场景自身的
    # 标量字段（brief／pov）与顶层标量（book_style）原地替换，不为一句措辞重发整场。
    for index in range(len(existing) - 1, -1, -1):
        if isinstance(existing[index], int):
            candidate = existing[:index + 1]
            if candidate[0] == 'scenes' and len(candidate) == 2:
                return existing
            return candidate
    return existing


def _beat_of(path: Path) -> Path | None:
    """路径所在的拍（`scenes[i].beats[j]`）；不在拍内返回 None。"""
    if len(path) >= 4 and path[0] == 'scenes' and path[2] == 'beats' \
            and isinstance(path[1], int) and isinstance(path[3], int):
        return path[:4]
    return None


def _all_beat_paths(wire: Any) -> list[Path]:
    """wire 的全部拍路径（`scenes[i].beats[j]`）——可选目标的候选拍闭集。

    与 H1b 免费判定的 `_design_beat_paths` 同一把尺（承载位由模型在全部拍里
    决定，不预裁「相关」拍——裁剪本身是语义判断，I-20）。
    """
    beats: list[Path] = []
    scenes = wire.get('scenes') if isinstance(wire, dict) else None
    if not isinstance(scenes, list):
        return beats
    for scene_i, scene in enumerate(scenes):
        rows = scene.get('beats') if isinstance(scene, dict) else None
        for beat_j, _beat in enumerate(rows or ()):
            beats.append(('scenes', scene_i, 'beats', beat_j))
    return beats


def _within(inner: Path, outer: Path) -> bool:
    return len(inner) >= len(outer) and inner[:len(outer)] == outer


def locate_design_patches(wire: Any, errors: Sequence[object],
                          ) -> list[tuple[Path, list[str]]] | list[tuple[Path, list[str], bool]] | None:
    """`ask_json` 的 `patch` 协议实现：``[(path, [error, …]), …]``、可选目标
    ``[(path, [error, …], True), …]`` 或 None。

    返回的 error 是传入的**同一批对象**（`ask_json` 以集合相等核对覆盖，不能改写）。
    可选目标条目的第三元素＝闭集语义（模型只覆盖选中的子集，至少一个）。
    """
    if not isinstance(wire, dict) or not errors:
        return None
    resolved: list[tuple[object, list[Path]]] = []
    optional_errors: list[object] = []
    for error in errors:
        paths = wire_paths_of(error)
        if paths is None:
            # 无路径错误按登记路由处置（RC-58）：`optional` 码＝责任位置须由模型
            # 决定，给全部拍作候选闭集；其余（未登记／执行面）仍退整份返修。
            if PATCH_ROUTES.get(getattr(error, 'code', None)) == 'optional':
                optional_errors.append(error)
                continue
            return None
        parents = []
        for path in paths:
            parent = _complete_parent(wire, path)
            if parent is None:
                return None
            parents.append(parent)
        resolved.append((error, parents))
    closed_set = _all_beat_paths(wire) if optional_errors else []
    if optional_errors and not closed_set:
        return None    # 登记 optional 却给不出候选闭集：工程缺陷，退整份并如实暴露
    # 耦合字段合并：同一拍内出现多处不同路径 ⇒ 整拍替换。
    by_beat: dict[Path, set[Path]] = {}
    for _error, parents in resolved:
        for parent in parents:
            beat = _beat_of(parent)
            if beat is not None:
                by_beat.setdefault(beat, set()).add(parent)
    merged_beats = {beat for beat, inside in by_beat.items()
                    if len(inside) >= 2 or beat in inside}
    targets: dict[Path, list[object]] = {}
    for error, parents in resolved:
        for parent in parents:
            beat = _beat_of(parent)
            chosen = beat if beat in merged_beats else parent
            targets.setdefault(chosen, [])
            if error not in targets[chosen]:
                targets[chosen].append(error)
    # 替换不重叠：落在另一目标之内的路径并入外层。
    outers = [p for p in targets if not any(q != p and _within(p, q) for q in targets)]
    grouped: dict[Path, list[object]] = {p: [] for p in outers}
    for path, items in targets.items():
        outer = next(q for q in outers if _within(path, q))
        for item in items:
            if item not in grouped[outer]:
                grouped[outer].append(item)
    # 可选目标闭集（RC-58）：闭集内的拍把落在其内的必选目标**升格为整拍**——
    # 整拍已是替换单元，不让「拍内必选条目」与「整拍可选条目」同时在场产生
    # 重叠替换；必选目标不在任何闭集拍内的原样保留。
    closed = set(closed_set)
    if closed:
        promoted: dict[Path, list[object]] = {}
        for outer, items in grouped.items():
            beat = next((b for b in closed_set if _within(outer, b)), None)
            key = beat if beat is not None else outer
            promoted.setdefault(key, [])
            promoted[key].extend(e for e in items if e not in promoted[key])
        grouped = promoted
        # 替换不重叠仍须成立：必选目标若包住某个闭集拍（整场、整个 beats 数组），
        # 整体替换与逐拍可选替换互相覆盖，合并次序会静默吞掉一侧——退整份。
        if any(_within(beat, outer) for outer in grouped if outer not in closed
               for beat in closed_set):
            return None
    for beat in closed_set:
        grouped.setdefault(beat, [])
        for error in optional_errors:
            if error not in grouped[beat]:
                grouped[beat].append(error)
    located = sorted(grouped.items(), key=lambda kv: tuple(str(x).zfill(6) for x in kv[0]))
    covered = {id(e) for _p, items in located for e in items}
    if {id(e) for e in errors} != covered:
        return None
    required_ids = {id(error) for error, _parents in resolved}
    out: list[tuple[Path, list[object]]] | list[tuple[Path, list[object], bool]] = []
    for path, items in located:
        if any(id(e) in required_ids for e in items):
            out.append((path, items))
        else:
            out.append((path, items, True))
    return out
