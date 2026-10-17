"""Design／Hook wire 的局部修订定位（CS-F1，RC-46/47）：错误带 wire 路径，按最小完整父项发起条目返修。

【为什么错误要在判定处带路径】修订控制不得从展示文案猜位置（`novel/core/validation.py`）。
本模块只定义构造与消费约定：判据在发现问题的地方生成 `ValidationIssue`，规则码与稳定
位置属身份，`context['wire_paths']` 是责任方可编辑的 wire JSON 路径；结构错误另外
保留 `shape_errors`，明确区分证据位置与必要替换能力。定位器只读结构化字段，不解析
错误字符串。Design 与 Hook 共用同一约定与同一定位器；Hook 不触发拍级耦合合并。

【定位策略】（纯函数、确定性、不改 wire）
· 路径只指向**最小完整父项**：叶子标量（如一条 `state` 里的 `val`）升到它所在的数组
  元素（整条 state），保证模型替换的是一个自洽的完整对象；路径在 wire 中不存在
  （缺省字段）时升到最近的现存祖先。
· **结构与语义分层**：容器身份由正式 schema 决定；非法键至少替换其父对象，不能
  只给非法键赋另一个值。Design 场内额外字段的归属判断交完整场景，模型决定删留
  或合法位置，程序不搬字段；嵌套可省略容器的整体返修交完整拥有者以支持删除键。
· **耦合字段合并**：同一拍内有两处及以上待修路径（事实绑定、认知拍位、称谓释放、事件
  文本彼此耦合），合并为这一拍整体，避免互相矛盾的局部替换。
· **替换不重叠**：任一路径若落在另一路径之内，只保留外层，内层问题并入外层。
· **可选目标**（CS-H H2，RC-58）：错误本身不带路径、但其规则码在
  `contracts/judges/str06.py::PATCH_ROUTES` 登记 `optional`（责任位置须由模型决定：
  必保义务漏标、series 成员接受拍互斥）时，给出**候选拍闭集**（wire 全部拍）——
  条目形如 ``(路径, [错误], True)``，模型只覆盖它选中的子集（至少一个），
  回答路径必须落在闭集内。
· 正式路由表先于路径生效；whole_answer／stop／未登记码不可借路径进入局部返修。
  无法覆盖全部错误时返回 None，由调用方按正式路由决定整份或停止，不缩小范围。
`ask_json` 对该协议的其余纪律不变：合并版重跑**完整**候选预检（不是只验 patch），
问题位置集合重复／回归即停，轮数与累计预算沿用现役上限。
"""
from __future__ import annotations

from typing import Any, Sequence

from contracts.judges.str06 import PATCH_ROUTES
from contracts.schema.design import DesignWireV9, DesignWireV10, DesignWireV11
from contracts.schema.hook import HookDesignWire, HookDesignWireV3
from novel.core.repair import schema_at_path
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


def _wire_schema(wire: dict) -> dict | None:
    """只按登记协议取正式形状，未知协议不根据候选字段猜模型。"""
    model = {
        'stage4-design-v9': DesignWireV9,
        'stage4-design-v10': DesignWireV10,
        'stage4-design-v11': DesignWireV11,
        'stage4-hook-design-v3': HookDesignWireV3,
        'stage4-hook-design-v4': HookDesignWire,
    }.get(wire.get('protocol_version'))
    return model.model_json_schema() if model is not None else None


def _optional_container_owner(schema: dict | None, path: Path) -> Path | None:
    """嵌套可省略容器的完整替换允许连键删除；不为具体字段写例外。

    顶层字段仍是独立编辑单位；缺省字段由现存祖先规则处理。数组内单条路径也
    不扩大，只有整个可省略容器被判据要求返修时才交回完整拥有者对象。
    """
    if schema is None or len(path) < 2 or not isinstance(path[-1], str):
        return None
    try:
        parent = schema_at_path(schema, path[:-1])
    except ValueError:
        return None
    if path[-1] not in parent.get('properties', {}) or path[-1] in parent.get('required', ()):
        return None
    if _schema_container(schema, path):
        return path[:-1]
    return None


def _schema_kinds(schema: dict, path: Path) -> frozenset[str]:
    """正式形状的类型闭集；不让坏值 [] 把字符串字段变成数组目标。"""
    child = schema_at_path(schema, path)

    def kinds(node: dict) -> set[str]:
        resolved = schema_at_path({**node, '$defs': child.get('$defs', {})}, ())
        kind = resolved.get('type')
        result = {kind} if isinstance(kind, str) else set(kind or ())
        for keyword in ('anyOf', 'oneOf', 'allOf'):
            for branch in resolved.get(keyword, ()):
                if isinstance(branch, dict):
                    result.update(kinds(branch))
        return result

    return frozenset(kinds(child))


def _schema_container(schema: dict, path: Path) -> bool:
    return bool(_schema_kinds(schema, path) & {'array', 'object'})


def _hook_repair_owner(schema: dict, path: Path) -> Path:
    """Hook 的语义边界：顶层记录、顶层数组的一条记录分别完整返修。

    视角、材料组织、事实选择与阅读期待都含同一记录内的耦合选择。结构下界只
    证明一个字段能改，不证明只改该字段有足够语义权限。按正式 schema 的顶层
    记录形状划界，统一覆盖所有 Hook 字段和错误类型；不按坏值或某个字段特判。
    数组自身的长度错误仍替换数组，不虚构不存在的条目。
    """
    top = path[:1]
    kinds = _schema_kinds(schema, top)
    if 'object' in kinds:
        return top
    if 'array' in kinds:
        return path[:2] if len(path) > 1 and isinstance(path[1], int) else top
    return path


def _complete_parent(wire: Any, path: Path, schema: dict | None = None) -> Path | None:
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
        return None            # 没有可证明的局部目标，交正式路由关闭式处置
    # 只允许「缺省的键」升到现存的对象祖先（如拍里没写 real）；下标越界、或要升到的祖先
    # 是整个数组，说明路径指向的条目根本不存在——升上去等于把整个数组重发，违背「最小完整
    # 父项」，也无从核验。这种情形不能签发局部计划。
    if existing != path and isinstance(path[len(existing)], int):
        return None
    node = _walk(wire, existing)
    if existing != path and isinstance(node, list):
        return None
    optional_owner = _optional_container_owner(schema, existing)
    if optional_owner is not None:
        return optional_owner
    try:
        is_container = (_schema_container(schema, existing) if schema is not None
                        else isinstance(node, (dict, list)))
    except ValueError:
        return None
    if is_container:
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
    schema = _wire_schema(wire)
    resolved: list[tuple[object, list[Path]]] = []
    optional_errors: list[object] = []
    for error in errors:
        route = PATCH_ROUTES.get(getattr(error, 'code', None))
        if route not in ('locate', 'optional'):
            # 有路径也不能绕过正式 whole_answer/stop 路由；未登记同样不猜。
            return None
        paths = wire_paths_of(error)
        if paths is None:
            # 无路径错误按登记路由处置（RC-58）：`optional` 码＝责任位置须由模型
            # 决定，给全部拍作候选闭集；其余交正式路由停止或明确整份出口。
            if route == 'optional':
                optional_errors.append(error)
                continue
            return None
        parents = []
        shapes = getattr(error, 'context', {}).get('shape_errors', ())
        # 结构诊断先给出可消除该错误的最小能力；额外字段的语义归属可能跨拍，
        # Design 的完整场景是该类归属判断的 owner 边界。程序不搬字段或丢内容。
        candidates = [(tuple(entry['replace_path']), entry)
                      for entry in shapes if entry.get('replace_path')]
        if shapes and len(candidates) != len(shapes):
            return None
        if not shapes:
            candidates = [(path, None) for path in paths]
        for path, shape in candidates:
            if not shape and schema is not None:
                try:
                    schema_at_path(schema, path)
                except ValueError:
                    return None
            issue_path = tuple(shape.get('issue_path') or ()) if shape else ()
            if (schema is not None and wire.get('protocol_version') in (
                    'stage4-hook-design-v3', 'stage4-hook-design-v4')):
                parent = _hook_repair_owner(schema, path)
            elif (shape and getattr(error, 'code', None) == 'design_wire_shape'
                    and shape['error_type'] == 'extra_forbidden'
                    and len(issue_path) >= 2 and issue_path[0] == 'scenes'
                    and isinstance(issue_path[1], int)):
                parent = issue_path[:2]
            else:
                parent = _complete_parent(wire, path, schema)
            if parent is None:
                return None
            try:
                _walk(wire, parent)
                if schema is not None:
                    schema_at_path(schema, parent)
            except (KeyError, ValueError):
                # 顶层可选键缺省的追加面由合并协议显式支持。
                if not (len(parent) == 1 and isinstance(parent[0], str)
                        and parent[0] not in wire and schema is not None):
                    return None
                try:
                    schema_at_path(schema, parent)
                except ValueError:
                    return None
            parents.append(parent)
        resolved.append((error, parents))
    closed_set = _all_beat_paths(wire) if optional_errors else []
    if optional_errors and not closed_set:
        return None    # 登记 optional 却给不出候选闭集：规划缺陷，不购买返修
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
        # 必选整场/数组已经覆盖的候选拍并入拥有者，不能再请求祖孙两份替换。
        # 其余候选仍交模型选择；所有候选都被覆盖时，必选拥有者本身足够承载。
        remaining = []
        for beat in closed_set:
            owner = next((outer for outer in grouped if _within(beat, outer)), None)
            if owner is None or owner == beat:
                remaining.append(beat)
            else:
                for error in optional_errors:
                    if error not in grouped[owner]:
                        grouped[owner].append(error)
        closed_set = remaining
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
