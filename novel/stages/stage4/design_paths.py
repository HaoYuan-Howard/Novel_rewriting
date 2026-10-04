"""Design wire 的局部修订定位（CS-F1，RC-46/47）：错误带 wire 路径，按最小完整父项发起条目返修。

【为什么错误要在判定处带路径】修订控制不得从展示文案猜位置（`novel/core/validation.py`）。
本模块只定义构造与消费约定：判据在发现问题的地方生成 `ValidationIssue`，规则码与稳定
位置属身份，`context['wire_paths']` 是责任方（Design）可编辑的 v8 wire JSON 路径；
定位器只读这些结构化字段，不解析任何错误字符串。

【定位策略】（纯函数、确定性、不改 wire）
· 路径只指向**最小完整父项**：叶子标量（如一条 `state` 里的 `val`）升到它所在的数组
  元素（整条 state），保证模型替换的是一个自洽的完整对象；路径在 wire 中不存在
  （缺省字段）时升到最近的现存祖先。
· **耦合字段合并**：同一拍内有两处及以上待修路径（事实绑定、认知拍位、称谓释放、事件
  文本彼此耦合），合并为这一拍整体，避免互相矛盾的局部替换。
· **替换不重叠**：任一路径若落在另一路径之内，只保留外层，内层问题并入外层。
· 存在不带路径的错误（全局覆盖闸、下游派生问题等）、路径指向不存在的结构，或定位后
  无法覆盖全部错误时返回 None——本轮退整份返修，不静默漏项、不无声缩小范围。
`ask_json` 对该协议的其余纪律不变：合并版重跑**完整**候选预检（不是只验 patch），
问题位置集合重复／回归即停，轮数与累计预算沿用现役上限。
"""
from __future__ import annotations

from typing import Any, Sequence

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


def _within(inner: Path, outer: Path) -> bool:
    return len(inner) >= len(outer) and inner[:len(outer)] == outer


def locate_design_patches(wire: Any, errors: Sequence[object],
                          ) -> list[tuple[Path, list[str]]] | None:
    """`ask_json` 的 `patch` 协议实现：``[(path, [error, …]), …]`` 或 None。

    返回的 error 是传入的**同一批对象**（`ask_json` 以集合相等核对覆盖，不能改写）。
    """
    if not isinstance(wire, dict) or not errors:
        return None
    resolved: list[tuple[object, list[Path]]] = []
    for error in errors:
        paths = wire_paths_of(error)
        if paths is None:
            return None
        parents = []
        for path in paths:
            parent = _complete_parent(wire, path)
            if parent is None:
                return None
            parents.append(parent)
        resolved.append((error, parents))
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
    located = sorted(grouped.items(), key=lambda kv: tuple(str(x).zfill(6) for x in kv[0]))
    covered = {id(e) for _p, items in located for e in items}
    if {id(e) for e in errors} != covered:
        return None
    return [(path, items) for path, items in located]
