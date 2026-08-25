#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
store.py —— 产物的**唯一**读写口。`ArtifactStore(ctx)`，一次跑一个实例。

【为什么必须只有一条路】来源账的「结构上不可能漏」只在**所有人都走这条路**时成立。
原型立过同一条纪律，实测被**十几处裸读绕过**——原因很简单：**绕过更省事**。
于是 `chapter_blueprints` 漏记 global、`style_grounding` 与 `fidelity_reference`
的 `inputs` 干脆是空的。一份自称能证明来源的记录悄悄漏掉一个来源，
**比没有这个记录更坏**，因为下游会拿它当证据。

⇒ 本轮换成**让绕过变成更费事的那条路**，四道叠起来：

  1. **只有 `store.read` 返回带类型的模型**，而所有下游函数签名都吃模型
     ⇒ 绕过意味着同时放弃校验和类型。**这是主杠杆**，另外三条是补漏。
  2. `tests/test_layering.py` 的 AST 测试：`novel/**` 里 `json.load(` 只许出现在本文件。
  3. **写盘口拒绝**（不是像原型那样 `print` 一句警告就照写）——见 `_refuse_bad_provenance`。
  4. **没有模块级 `save_json`**：写盘只经 store 实例，而 store 只由 `RunContext` 造。

【落盘一律原子 + 读回验证】
`open(p,'w')` 是「先截断再写」：在它写完之前，盘上那份是**半截 JSON**。
原型踩过——两个节点并发时，后者正好在前者落盘的瞬间去读它，
而半截 JSON 的坏处不是读失败，是**读失败会被当成「上游还没产出」**，
于是一次**成功的付费跑**被报成失败。
⚠ 读回不是多余的：一份**读不出来的产物**与一份不存在的产物，在需要它的那一刻是同一件事，
而前者还会让人以为自己有备份。**这里读回还要过一遍模型**——
只 `json.load` 一下只能证明「是合法 JSON」，证明不了「下次还读得回来」。
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, TypeVar

from pydantic import BaseModel, ValidationError

from contracts.artifacts import (
    STAGE0_CLEAN_TEXT, STAGE0_REVISIONS,
    STAGE1_HOOK_COPY, STAGE1_MAIN_TEXT, STAGE1_PARTITION_REPORT,
    STAGE2_GLOBAL_GROUNDING, STAGE2_GLOBAL_REVIEW, STAGE4_WRITER_CONTEXT,
    STAGE5_BOOK_DRAFT,
    STAGE7_NAME_MAP,
)
from contracts.schema import (
    ArtifactVersionError, HumanApproval, InputEdge, Provenance, RevalidationAudit,
    artifact_spec, decode_artifact, encode_artifact,
)
from novel import paths, prov
from novel.consumption import producer_node, projection_for, render_stage5_draft
from novel.errors import (
    ApprovalRequired, ArtifactUnreadable, ByteFormRefused, PathRefused,
    ProvenanceRefused, UpstreamMissing, UpstreamStale,
)
from novel.prov import FreshnessReport
from novel.run import RunContext

M = TypeVar('M', bound=BaseModel)

#: 产物里那个来源账字段的**盘上键名**。模型侧字段叫 `provenance`（`_` 开头在 Pydantic 里
#: 是私有属性），靠 `Field(alias='_provenance')` 对上；这里按 alias 找，不按字段名猜。
PROVENANCE_KEY = '_provenance'
REVALIDATION_DIRNAME = '_revalidation'

# These no-provenance artifacts have a deterministic freshness bridge and are
# written only after the companion facts needed by that bridge are available.
# stage0 clean_text and stage1 main/hook are deliberately absent: their freshness
# depends on companion authority and, for Stage0, the external source edge.
# ⚠ **stage0 整层都不在这里**（`revisions.json` 与 `clean_text.txt` 一并），
#   那是有理由的、不是又漏了一条：它们的确定性边要去 resolve 真实原文，而
#   「换了一份原文」在 stage0 是一条**合法的、由判据去拒**的路（`R-0.14`），
#   写盘口再拒一次就把它变成写不进去。判别力落在判据那一侧，不在这道后置条件。
SELF_FRESH_DETERMINISTIC = frozenset({
    STAGE1_PARTITION_REPORT,
    STAGE2_GLOBAL_REVIEW,
})


def _canonical_json_bytes(value: Any) -> bytes:
    """Canonical logical JSON used only for audit comparison and hashing."""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
        allow_nan=False,
    ).encode('utf-8')


def _filesystem_path(path: Path) -> str:
    """Return an explicit Windows extended-length filesystem path."""
    value = os.path.abspath(os.fspath(path))
    if os.name != 'nt' or value.startswith('\\\\?\\'):
        return value
    if value.startswith('\\\\'):
        return f'\\\\?\\UNC\\{value[2:]}'
    return f'\\\\?\\{value}'


def _read_file_bytes(path: Path) -> bytes:
    with open(_filesystem_path(path), 'rb') as stream:
        return stream.read()


def _is_file(path: Path) -> bool:
    return os.path.isfile(_filesystem_path(path))


def _path_exists(path: Path) -> bool:
    return os.path.exists(_filesystem_path(path))


def _is_dir(path: Path) -> bool:
    return os.path.isdir(_filesystem_path(path))


def _directory_paths(path: Path) -> Iterator[Path]:
    """Iterate a directory while retaining the caller's unprefixed Path shape."""
    for name in os.listdir(_filesystem_path(path)):
        yield path / name


@dataclass
class _HistoryFrame:
    files: dict[Path, bytes | None]
    absent_dirs: set[Path] = field(default_factory=set)


def _json_pointer_diff(before: Any, after: Any, pointer: str = '') -> tuple[str, ...]:
    """Return deterministic RFC 6901 pointers whose logical values changed."""

    def child(base: str, token: object) -> str:
        escaped = str(token).replace('~', '~0').replace('/', '~1')
        return f'{base}/{escaped}'

    changed: list[str] = []
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after)):
            at = child(pointer, key)
            if key not in before or key not in after:
                changed.append(at)
            else:
                changed.extend(_json_pointer_diff(before[key], after[key], at))
    elif isinstance(before, list) and isinstance(after, list):
        common = min(len(before), len(after))
        for index in range(common):
            changed.extend(_json_pointer_diff(
                before[index], after[index], child(pointer, index)))
        for index in range(common, max(len(before), len(after))):
            changed.append(child(pointer, index))
    elif type(before) is not type(after) or before != after:
        changed.append(pointer)
    return tuple(sorted(set(changed)))


def provenance_field(model: type[BaseModel]) -> str | None:
    """这个模型里哪个字段是来源账。没有就返回 `None`。

    ⚠ **五份产物按契约就没有通用来源账**（stage0 revision 链 · stage1 的三份 ·
    `name_map_edit.json` 那份**人产的输入**）。那不是漏，别替它们补一份
    ——补出来的是一条没人验证过的记录。
    """
    for name, f in model.model_fields.items():
        if f.alias == PROVENANCE_KEY:
            return name
    return None


class ArtifactStore:
    """一次跑的读写口。**它只由 `RunContext` 造**，因为记账要落在那个实例上。"""

    def __init__(self, ctx: RunContext) -> None:
        self.ctx = ctx
        # Each active history transaction records exact paths it may mutate.
        # Content-addressed paths are registered lazily by `_write_immutable`;
        # this avoids scanning/deleting another artifact's concurrent baseline.
        self._history_transactions: list[_HistoryFrame] = []

    # ── 定位 ──────────────────────────────────────────────────────────────

    def path(self, rel: str) -> Path:
        return paths.artifact_path(self.ctx.novel_dir, rel)

    def exists(self, rel: str) -> bool:
        return _path_exists(self.path(rel))

    # ── 读 ────────────────────────────────────────────────────────────────

    def read(self, model: type[M], rel: str) -> M:
        """读一份上游产物，**顺手把它记进本次跑的来源账**。

        本节点真正读到的字段投影从 `novel.consumption` 按 `(node, rel)` 取得。
        这里不再接受调用点临时传一个函数：写来源账与 freshness 重算必须共用同一对象。
        未登记仍诚实地写 `consumed=null`，不会把「不知道」偷换成 fresh。

        ⚠ 投影吃的是**盘上那份原始 dict**，不是校验后的模型。理由是新鲜度那一侧：
        判过期时要对一份**可能已经不合当前 schema** 的上游重算同一个投影，
        而模型化会先炸在校验上。同一个函数、同一种输入，两侧才对得上。
        """
        obj, _digest = self.read_with_sha256(model, rel)
        if not self.ctx.readonly:
            self._refuse_stale_upstream(rel)
        return obj

    def read_with_sha256(self, model: type[M], rel: str) -> tuple[M, str]:
        """Read an upstream artifact and return its exact on-disk SHA-256.

        The digest binds decisions to the complete versioned artifact bytes,
        while the ordinary provenance edge remains the repository's shorter
        freshness fingerprint.  Keeping both in this one read prevents a
        hash-bound decision from bypassing input-edge accounting.
        """
        key = paths.input_key(rel)          # ← 两级键；三级路径在这里就被拒（见 `PathRefused`）
        project = projection_for(self.ctx.node, key)
        raw = self._read_bytes(rel)
        data = self._loads(rel, raw)
        obj, data = self._validate_payload(model, rel, data)
        self._record_artifact_root(rel, obj)
        self.ctx.record_edge(key, InputEdge(
            file=prov.fingerprint_bytes(raw),
            consumed=prov.projection_fingerprint(project(data)) if project else None,
        ))
        return obj, hashlib.sha256(raw).hexdigest()

    def read_judged(self, model: type[M], rel: str, *,
                    project: Callable[[Any], Any] | None = None,
                    require_current: bool = False) -> tuple[M, str, str | None]:
        """读一份**成稿**。记进 `judged`，**不记进 `inputs`**（`_common.md` §3.2）。

        返回 `(模型, 整份指纹, 投影指纹|None)`——调用方要拿它们去填 `judged[]` 那三格。

        ⚠⚠ **为什么成稿必须走另一条路**：`inputs` 的键是两级的，
        成稿是被审判的主体，不是 producer 上游；若记进 `inputs`，多级历史路径还会
        塌成一个**反查不到文件**的键。因此无论当前 canonical 路径有几级，都逐份记
        **完整相对路径** + 指纹，并与普通上游边分开。
        """
        paths.check_rel_deep(rel)
        raw = self._read_bytes(rel)
        data = self._loads(rel, raw)
        obj, data = self._validate_payload(model, rel, data)
        self._record_artifact_root(rel, obj)
        if require_current and not self.ctx.readonly:
            self._refuse_stale_upstream(rel)
        fp = prov.fingerprint_bytes(raw)
        consumed = prov.projection_fingerprint(project(data)) if project else None
        self.ctx.record_judged(paths.check_rel_deep(rel), fp)
        return obj, fp, consumed

    def _refuse_stale_upstream(self, rel: str, *,
                               replaced_input_keys: Iterable[str] = ()) -> None:
        """在生产节点拿到载体之前拒绝已知过期的上游。

        ``unknown`` 仍按既有兼容契约放行：它表示旧产物没有足够来源事实，不能伪装成
        ``fresh``，但也不能凭空断言内容已变。只有可复算且明确为 ``stale`` 的事实会
        阻断。只读视图由 ``RunContext.readonly`` 绕过本闸，仍能展示旧产物及过期原因。
        """
        report = self.freshness(rel)
        if report.state != 'stale':
            return
        replaced = frozenset(replaced_input_keys)
        if replaced:
            blocking = tuple(
                edge for edge in report.edges
                if edge.state == 'stale' and edge.key not in replaced
            )
            if not blocking:
                return
            changed = ', '.join(edge.key for edge in blocking) or report.reason
        else:
            changed = ', '.join(edge.key for edge in report.edges) or report.reason
        producer = producer_node(rel, fallback=self.ctx.node) or self.ctx.node
        # ⚠ `UpstreamStale` 而不是 `ProvenanceRefused`：这条拒绝自带恢复命令
        # ⇒ 它是显式终态（退出码 2），不是「代码自相矛盾」（退出码 3）。见该类的 docstring。
        raise UpstreamStale(
            f'{self.ctx.node}: 上游 {rel} 已过期（{changed}）；在生成或付费请求前拒绝消费。'
            f'请先从 {producer} 重建当前根上的产物')

    def read_own(self, model: type[M], rel: str, *,
                 require_current: bool = False,
                 replaced_input_keys: Iterable[str] = ()) -> tuple[M, Any]:
        """读**本节点自己产的那一份**（复检 / 重算 / 提升前要先看看它）。
        返回 `(模型, 盘上原始 dict)`。

        ⚠⚠ **它刻意不记来源边，这是本方法存在的全部理由。**
        `inputs` 回答的是「我这次产出照哪些**上游**算的」——
        把产物自己记成自己的上游是**假账**，而且那条边永远显示对得上。

        ⚠ 它**不是**「绕过唯一读路」的口子：字节读取、解码、JSON 解析、模型校验
        走的还是同一条路（`_read_bytes` / `_loads` / `_validate`），
        少的只有 `record_edge` 那一行。原型这里是**裸 `json.load`**，
        于是解码与校验那两道也一起绕过去了。

        ⚠ **同时返回原始 dict** 是有用的：判据跑在**模型之前**的那份 dict 上
        （新契约把八维做成了 `Field(min_length=1)`，模型化之后 `required` 必然全绿，
        而按「判据 vs 载体按失败成本划」它该留在判据侧——载体拒收要再花一次钱，
        判据拦下是 0 成本自救）。见 `novel/stages/stage3/checks.py` 开头那段。

        ``replaced_input_keys`` 只服务 typed 人工修订：问题稿会把**上一份**编辑决策集
        记成输入，而人的下一步恰恰是修改同一个文件。此时只放行被明确替换的那条边；
        正文、设定等其余上游仍关闭式拒绝。调用方不给键时与普通 ``require_current``
        完全同义，不能把它当成「问题稿一律跳过 freshness」的口子。
        """
        raw = self._read_bytes(rel)
        data = self._loads(rel, raw)
        obj, payload = self._validate_payload(model, rel, data)
        self._record_artifact_root(rel, obj)
        if require_current:
            self._refuse_stale_upstream(rel, replaced_input_keys=replaced_input_keys)
        return obj, payload

    def schema_version(self, rel: str) -> int | None:
        """只读盘上 envelope 的版本标记，不把旧产物解码成当前 payload。

        legacy/stale 状态必须在**当前模型拒读之前**仍能区分“明确是旧版本”与“文件损坏”。
        这里仍走 store 唯一的字节/UTF-8/JSON 入口，但刻意不调用 ``decode_artifact``：
        调用它会把本方法最需要识别的旧版本先抛成一个无结构的终态。缺标记返回 ``None``，
        非整数标记也返回 ``None``；它们不是某个可声称的历史契约版本。
        """
        data = self._loads(rel, self._read_bytes(paths.check_rel_deep(rel)))
        if not isinstance(data, dict):
            return None
        marker = data.get('_schema_version')
        return marker if isinstance(marker, int) and not isinstance(marker, bool) else None

    def read_external(self, model: type[M], contract_rel: str,
                      path: str | os.PathLike[str]) -> M:
        """按注册产物契约读取一个已由调用方完成路径授权的外部 JSON 文件。

        一次性 API 编辑文件住在 `_service/edits`，不能用 ``self.path(rel)`` 定位；
        但它的 UTF-8、JSON、schema version 与 Pydantic 校验不能另写第二套。本方法
        不记来源边，也不决定路径是否合法——那两个职责分别属于产物消费与调用方。
        """
        candidate = Path(path)
        try:
            raw = _read_file_bytes(candidate)
        except FileNotFoundError:
            raise UpstreamMissing(f'{candidate} 不在盘上') from None
        except OSError as exc:
            raise ArtifactUnreadable(f'读不了外部输入 {candidate}：{exc!r}') from exc
        data = self._loads(str(candidate), raw)
        obj, _payload = self._validate_payload(model, contract_rel, data)
        return obj

    def read_text(self, rel: str) -> str:
        """读一份**字节形态**产物（`clean_text.txt` / `draft.md` / 成书）。记边。

        ⚠ **按字节读再自己解码**，不用文本模式：文本模式会把 `\\r\\n` 悄悄翻成 `\\n`，
        于是「盘上那份带 CRLF」这件事在读的一侧**看不见**，
        而写的一侧刚刚为它立了一道闸（`write_text`）。两侧口径必须一致。

        ⚠ **先解码，再记边。** 反过来的话，一份解不出来的文件会在本次跑的来源账里
        留下一条边——那条边说的是「我读过它」，而我们其实一个字都没读到。
        """
        key = paths.input_key(rel)
        raw = self._read_bytes(rel)
        text = self._decode(rel, raw)
        project = projection_for(self.ctx.node, key)
        self.ctx.record_edge(key, InputEdge(
            file=prov.fingerprint_bytes(raw),
            consumed=prov.projection_fingerprint(project(text)) if project else None,
        ))
        return text

    def read_own_text(self, rel: str) -> str:
        """Read this node's byte artifact/history without recording a fake input edge.

        This is the text counterpart of :meth:`read_own`.  In particular,
        ``stage0/_rounds/*.txt`` is a three-level recovery archive and cannot be
        represented by the two-level provenance input-key contract.
        """
        raw = self._read_bytes(paths.check_rel_deep(rel))
        return self._decode(rel, raw)

    def _read_bytes(self, rel: str) -> bytes:
        p = self.path(rel)
        try:
            return _read_file_bytes(p)
        except FileNotFoundError:
            raise UpstreamMissing(
                f'{self.ctx.node}: 上游产物不在盘上：{rel}（{p}）——先跑产它的那一层') from None
        except OSError as e:
            raise ArtifactUnreadable(f'{self.ctx.node}: 读不了 {rel}：{e!r}') from e

    @staticmethod
    def _decode(rel: str, raw: bytes) -> str:
        """字节 → 文本。**解不出来是显式终态**，不是一条裸 `UnicodeDecodeError`。

        ⚠ `_loads` 早就把 `UnicodeDecodeError` 兜成 `ArtifactUnreadable` 了，
        而 `read_text` 那一侧原来是裸 `raw.decode('utf-8')`——同一件事在两条读路上
        一条有词汇、一条没有。`errors.py` 明写「**不设默认异常**，每一处终态都要选一个具体的类」，
        而只有 `novel/cli/` 那一层会把这些类翻译成退出码：
        漏网的那一条到了 CLI 就是一段栈，说不出「哪个节点、要跑什么、盘上哪份坏了」。
        """
        try:
            return raw.decode('utf-8')
        except UnicodeDecodeError as e:
            raise ArtifactUnreadable(
                f'{rel} 不是 UTF-8：{e!r}。'
                f'⚠ 三份字节形态产物（`clean_text.txt` / `draft.md` / 成书）盘上一律 UTF-8；'
                f'别把它当成「上游还没产出」——那会把一次成功的付费跑报成失败') from e

    @classmethod
    def _loads(cls, rel: str, raw: bytes) -> Any:
        try:
            return json.loads(cls._decode(rel, raw))
        except json.JSONDecodeError as e:
            raise ArtifactUnreadable(
                f'{rel} 不是合法 JSON：{e!r}。'
                f'⚠ 别把它当成「上游还没产出」——那会把一次成功的付费跑报成失败') from e

    @staticmethod
    def _validate_payload(model: type[M], rel: str, data: Any) -> tuple[M, Any]:
        try:
            decoded = decode_artifact(rel, data)
        except ArtifactVersionError as e:
            raise ArtifactUnreadable(str(e)) from e
        try:
            return model.model_validate(decoded.payload), decoded.payload
        except ValidationError as e:
            raise ArtifactUnreadable(
                f'{rel} 的 schema v{decoded.version} 不合 {model.__name__} 的当前契约：\n{e}') from e

    # ── 写 ────────────────────────────────────────────────────────────────

    @staticmethod
    def _round_file_matches(path: Path, stem: str) -> bool:
        number = paths.round_no_of(path.name)
        return number is not None and path.name == f'{stem}.r{number}.json'

    def _history_scope(
        self, rels: Iterable[str],
    ) -> tuple[set[Path], set[tuple[Path, str]]]:
        """Resolve the exact live/history byte surfaces touched by a write."""
        live: set[Path] = set()
        rounds: set[tuple[Path, str]] = set()
        for value in rels:
            rel = paths.check_rel_deep(value)
            expanded = {rel}
            if not paths.is_invalid(rel):
                expanded.add(paths.invalid_of(rel))
            for candidate in expanded:
                live.add(self.path(candidate))
                rounds.add((
                    self.path(paths.rounds_dir_of(candidate)),
                    paths.stem_of(candidate),
                ))
        return live, rounds

    def _history_files(
        self,
        live: set[Path],
        rounds: set[tuple[Path, str]],
    ) -> set[Path]:
        files = {path for path in live if _is_file(path)}
        for directory, stem in rounds:
            if not _is_dir(directory):
                continue
            files.update(
                path for path in _directory_paths(directory)
                if _is_file(path) and self._round_file_matches(path, stem)
            )
        return files

    def _track_history_path(self, path: Path) -> None:
        """Register an exact late-bound mutation with every active transaction."""
        for frame in self._history_transactions:
            tracked = frame.files
            if path in tracked:
                continue
            try:
                tracked[path] = _read_file_bytes(path) if _is_file(path) else None
            except OSError as exc:
                raise ArtifactUnreadable(
                    f'事务写入前读不了待变历史 {path}：{exc!r}') from exc
            self._track_absent_parents(frame, path)

    def _track_absent_parents(self, frame: _HistoryFrame, path: Path) -> None:
        """Remember transaction-created directories without crossing the book root."""
        root = self.ctx.novel_dir
        parent = path.parent
        while parent != root and parent != parent.parent and not _is_dir(parent):
            frame.absent_dirs.add(parent)
            parent = parent.parent

    @contextmanager
    def history_transaction(self, *rels: str) -> Iterator[None]:
        """Rollback live artifacts and their immutable history as one byte set.

        The scope includes each formal artifact, its INVALID sibling, their
        matching numbered rounds, and content-addressed revalidation baselines.
        Nested transactions let a producer widen a safe single-artifact helper
        to a companion review/context without duplicating compensation logic.
        """
        if not rels:
            raise PathRefused('history_transaction 必须显式列出至少一份产物')
        live, rounds = self._history_scope(rels)
        try:
            before_paths = self._history_files(live, rounds)
            tracked: dict[Path, bytes | None] = {
                path: _read_file_bytes(path) for path in before_paths}
            # Missing live paths are part of the byte set too.  Recording them
            # explicitly lets rollback delete only paths this transaction may
            # have created; it never has to sweep a matching directory and
            # risk deleting another writer's history.
            for path in live:
                tracked.setdefault(path, None)
        except OSError as exc:
            raise ArtifactUnreadable(
                f'事务开始前读不全历史字节：{exc!r}；一个字节都没动') from exc

        frame = _HistoryFrame(files=tracked)
        for path in live:
            self._track_absent_parents(frame, path)
        self._history_transactions.append(frame)
        try:
            yield
        except BaseException as exc:
            rollback_errors: list[BaseException] = []
            absent_before = {
                path for path, data in tracked.items()
                if data is None and _is_file(path)
            }
            for path in sorted(absent_before, key=lambda item: str(item), reverse=True):
                try:
                    os.unlink(_filesystem_path(path))
                except FileNotFoundError:
                    pass
                except BaseException as rollback_exc:
                    rollback_errors.append(rollback_exc)

            for path, data in tracked.items():
                if data is None:
                    continue
                try:
                    if not _is_file(path) or _read_file_bytes(path) != data:
                        os.makedirs(_filesystem_path(path.parent), exist_ok=True)
                        self._replace_atomically(path, data)
                except BaseException as rollback_exc:
                    rollback_errors.append(rollback_exc)

            # Directory presence is not artifact content, but removing empty
            # transaction-created scaffolding keeps a failed first write truly
            # side-effect free.  A non-empty directory may now contain another
            # writer's file and is therefore deliberately retained.
            for directory in sorted(
                    frame.absent_dirs,
                    key=lambda item: len(item.parts), reverse=True):
                try:
                    os.rmdir(_filesystem_path(directory))
                except FileNotFoundError:
                    pass
                except OSError:
                    if not _is_dir(directory):
                        rollback_errors.append(ArtifactUnreadable(
                            f'事务回滚时无法核对目录 {directory}'))

            if rollback_errors:
                detail = '; '.join(repr(error) for error in rollback_errors)
                raise ProvenanceRefused(
                    f'多产物事务失败且回滚未完整完成：{detail}。'
                    '请勿继续下游，先人工核对 live/_rounds/_revalidation') from exc
            raise
        finally:
            popped = self._history_transactions.pop()
            if popped is not frame:
                raise ProvenanceRefused('写盘事务栈顺序损坏；请停止下游')

    def write_round(self, rel: str, artifact: BaseModel) -> Path:
        """Archive the previous formal round and commit its replacement safely."""
        rel = paths.check_rel_deep(rel)
        if paths.is_invalid(rel):
            raise PathRefused(f'{rel} 是 `.INVALID`；问题稿走 write_invalid')
        # Refuse carrier/render errors before the first archive mutation.
        data = self._render(rel, artifact)
        self._verify_round_trip(rel, type(artifact), data)
        with self.history_transaction(rel):
            if self.exists(rel):
                # Copy first: a process crash between archive and formal
                # replacement must still leave the old live artifact reachable.
                self.archive_round(rel, keep=True)
            return self.write(rel, artifact)

    def write(self, rel: str, artifact: BaseModel) -> Path:
        """落一份正式产物。**先校验后落盘**——校验在 `artifact` 被构造的那一刻就发生过了。

        ⚠ `.INVALID` 不走这条（走 `write_invalid`）：两条路的语义不同，
        合成一条之后「这份合格」与「这份被拦下了」就只剩一个调用点参数的差别，
        而那种差别是会被传错的。

        ⚠⚠ **新正式稿完整落稳且自证 fresh 之后，才把同名 `.INVALID` 送进归档。** 一份合格的新稿
        就是对同一份产物的旧问题稿的终结；把它留在盘上，`.INVALID` 就成了一个
        **谁也不会去清的永久标记**，而全仓有四处只看「兄弟文件在不在」：
        `state.artifact_state`（→ 语料库把一本已出书的书报成 `broken`）·
        `state.resume_verdict`（→ 队列**重跑并重新付费**一个已经有新鲜正式稿的节点，
        实测发生过）· `approvals._require_artifacts`（→ 人定稿盖不上章）·
        `queue._upstream_invalid`（→ 下游整条链被挡）。
        ⚠ **归档不是删除**（`Q-8`）：那份稿花过钱，而且可能还被别人的 `inputs` 指着。
        替换正式稿、freshness 后置条件、退休 `.INVALID` 视为一个事务：任何一步失败，
        正式路径恢复为调用前的逐字节内容，问题稿也恢复在原位。
        """
        rel = paths.check_rel_deep(rel)
        if paths.is_invalid(rel):
            raise PathRefused(
                f'{rel} 是 `.INVALID` 形态，不走 `write`。'
                f'被闸门拦下的稿走 `write_invalid`（它强制你交代 `gates_skipped`）')
        data = self._render(rel, artifact)
        self._verify_round_trip(rel, type(artifact), data)
        p = self.path(rel)
        os.makedirs(_filesystem_path(p.parent), exist_ok=True)
        previous = _read_file_bytes(p) if _is_file(p) else None
        replacement_attempted = False
        retired: Path | None = None
        try:
            replacement_attempted = True
            self._replace_atomically(p, data)
            self._assert_self_fresh(rel, artifact)
            # 只有正式路径已经通过 freshness 后置条件，问题稿才算真正被取代。
            retired = self.retire_invalid(rel)
        except BaseException as exc:
            rollback_errors: list[BaseException] = []
            if retired is not None and _is_file(retired):
                invalid = self.path(paths.invalid_of(rel))
                os.makedirs(_filesystem_path(invalid.parent), exist_ok=True)
                if not _path_exists(invalid):
                    try:
                        os.replace(_filesystem_path(retired), _filesystem_path(invalid))
                    except BaseException as rollback_exc:
                        rollback_errors.append(rollback_exc)
            if replacement_attempted:
                try:
                    if previous is None:
                        if _is_file(p):
                            os.unlink(_filesystem_path(p))
                    else:
                        self._replace_atomically(p, previous)
                except BaseException as rollback_exc:
                    rollback_errors.append(rollback_exc)
            if rollback_errors:
                detail = '; '.join(repr(error) for error in rollback_errors)
                raise ProvenanceRefused(
                    f'{rel} 写入失败且回滚未完整完成：{detail}。'
                    '请勿继续运行下游，先按错误中列出的正式/INVALID 路径人工恢复') from exc
            raise
        return p

    def schema_version_of(self, rel: str) -> int | None:
        """只读返回盘上 JSON 信封版本；文件不存在返回 ``None``。"""
        rel = paths.check_rel_deep(rel)
        p = self.path(rel)
        if not _is_file(p):
            return None
        raw = self._loads(rel, self._read_bytes(rel))
        marker = raw.get('_schema_version') if isinstance(raw, dict) else None
        if isinstance(marker, bool) or not isinstance(marker, int) or marker < 1:
            raise ArtifactUnreadable(
                f'{rel} 的 _schema_version={marker!r} 非法，不能判定 shape-break 重建策略')
        return marker

    def replace_shape_break(self, rel: str, artifact: BaseModel) -> Path:
        """以当前 schema 原位替换旧正式稿，并丢弃退役 INVALID，二者不进轮次史。

        这条口只供已在 ``ARTIFACT_SPECS`` 显式升版的 producer 重建。低版本字节不是
        当前业务轮次；把它们塞进 ``_rounds`` 会让 current corpus 扫到一份明知读不回的
        产物。正式稿替换、自证 fresh 与退役 INVALID 在同一 history transaction 内：
        任一步失败，调用前两份字节都会逐字恢复。
        """
        rel = paths.check_rel_deep(rel)
        if paths.is_invalid(rel):
            raise PathRefused(f'{rel} 是 `.INVALID`，不能作为 shape-break 正式落点')
        spec = artifact_spec(rel)
        if spec is None or spec.current_version <= 1:
            raise PathRefused(
                f'{rel} 没有登记过 schema shape break，不能走旧字节替换口')
        versions = {
            candidate: self.schema_version_of(candidate)
            for candidate in (rel, paths.invalid_of(rel))
        }
        present = {candidate: version for candidate, version in versions.items()
                   if version is not None}
        if not present or not all(version < spec.current_version
                                  for version in present.values()):
            raise PathRefused(
                f'{rel} shape-break 替换口只接受全为旧版本的 live 字节；'
                f'当前版本 v{spec.current_version}，盘上={present or "（无）"}')
        data = self._render(rel, artifact)
        self._verify_round_trip(rel, type(artifact), data)
        formal = self.path(rel)
        invalid = self.path(paths.invalid_of(rel))
        with self.history_transaction(rel):
            os.makedirs(_filesystem_path(formal.parent), exist_ok=True)
            self._replace_atomically(formal, data)
            self._assert_self_fresh(rel, artifact)
            try:
                os.unlink(_filesystem_path(invalid))
            except FileNotFoundError:
                pass
        return formal

    def retire_invalid(self, rel: str) -> Path | None:
        """同名 `.INVALID` 若在盘上，按它自己记的那一轮归档；不在就什么都不做。

        返回归档路径或 `None`（`None` = 本来就没有问题稿，不是「归档失败」）。
        """
        inv_rel = paths.invalid_of(paths.check_rel_deep(rel))
        return self.archive_round(inv_rel) if self.exists(inv_rel) else None

    def write_invalid(self, rel: str, artifact: BaseModel, *,
                      gates_skipped: list[str]) -> Path:
        """落一份被闸门拦下的稿。**不覆盖已有正式产物**（`Q-5`：先校验后落盘）。

        `gates_skipped` **没有默认值**，调用方必须回答「有没有闸门缺料」。
        ⚠ **空列表也要写。** 空列表 = 这次没有闸门缺料；键不在 = 这份产物早于这套机制。
        它是判据 36 那整套设计的**唯一痕迹**，而「闸门没跑成」与「闸门跑过没事」
        正是 `Q-2` 要求必须能区分的两件事。

        ⚠ 稿子**没有丢，也没有再花钱**：0 成本提升路是 `promote_invalid`。
        """
        rel = paths.check_rel_deep(rel)
        inv_rel = paths.invalid_of(rel)
        src = self._provenance_of(artifact)
        if src is None:
            raise ProvenanceRefused(
                f'{type(artifact).__name__} 没有 `_provenance` 字段，落不了 `.INVALID`：'
                f'`gates_skipped` 只有来源账这一个落点，没有它就等于没记过闸门缺料')

        # 定位键不能把自己算进自己的 hash。先渲染一次「没有定位键」的完整字节，
        # 再把其 raw SHA-256 写回来源账。最终 INVALID 与不可变快照逐字节相同，
        # 因而 raw hash 能如实区分「只改了排版」与「逻辑 JSON 也改了」。
        pending = src.model_copy(update={
            'gates_skipped': list(gates_skipped),
            'invalid_baseline_sha256': None,
        })
        preliminary = self._render(inv_rel, artifact, prov_override=pending)
        # Locator binds the complete deterministic pre-locator bytes (including
        # provenance), not the business-content canonical hash used by the audit.
        # Two generations with identical business JSON but different source facts
        # must never collide onto one immutable snapshot path.
        locator = hashlib.sha256(preliminary).hexdigest()
        final_src = pending.model_copy(update={'invalid_baseline_sha256': locator})
        data = self._render(inv_rel, artifact, prov_override=final_src)
        self._verify_round_trip(inv_rel, type(artifact), data)

        baseline = self.path(self._revalidation_baseline_rel(inv_rel, locator))
        p = self.path(inv_rel)
        # Replacing an INVALID is a new failed round.  Its old live bytes, the
        # numbered archive, the immutable baseline and the replacement are one
        # transaction; callers must never pre-archive the sibling themselves.
        with self.history_transaction(inv_rel):
            if self.exists(inv_rel):
                self.archive_round(inv_rel)
            self._write_immutable(baseline, data)
            os.makedirs(_filesystem_path(p.parent), exist_ok=True)
            self._replace_atomically(p, data)
        return p

    def write_text(self, rel: str, text: str) -> Path:
        """落一份**字节形态**产物。**显式按字节写 `\\n`，不带 BOM。**

        ⚠ 盘上三份字节形态产物（`clean_text.txt` / `draft.md` / 成书）**全是 LF-only**，
        而 Windows 上 `open(p, 'w')` 默认吐 CRLF——同一份代码在两台机器上产出两种字节，
        而本项目有一条逐字节等价的验收线。

        ⚠⚠ **拿到 `\\r` 一律拒收，不静默归一。** 归一是**内容决策**，属产地
        （它要为「我改了什么」负责，可能还要记账）；写盘口只保证**平台不掺东西**。
        替产地做一次它自己没记账的改写，正是本项目要消灭的那种「安静地改了点什么」。
        """
        rel = paths.check_rel_deep(rel)
        if '\r' in text:
            n = text.count('\r')
            raise ByteFormRefused(
                f'{rel}: 文本里有 {n} 个 `\\r`。三份字节形态产物盘上全是 LF-only，'
                f'而归一属产地——请在产它的那一层先归一（并按 `_common.md` §九 判断要不要记账），'
                f'别让写盘口替你悄悄改内容')
        p = self.path(rel)
        os.makedirs(_filesystem_path(p.parent), exist_ok=True)
        data = text.encode('utf-8')          # ← 不加 BOM：`utf-8` 不是 `utf-8-sig`
        self._replace_atomically(p, data)
        return p

    # ── `.INVALID` 的 0 成本提升路 ────────────────────────────────────────

    def revalidation_audit(self, rel: str, model: type[M], *,
                           actor: str, reason: str) -> RevalidationAudit:
        """Compare the live ``.INVALID`` with its immutable creation snapshot.

        This is public so a caller can show the exact audit before promotion.
        It is read-only: malformed credentials, a missing/tampered baseline, or
        an unreadable current artifact all refuse without changing one byte.
        """
        _artifact, _src, audit = self._revalidation_material(
            rel, model, actor=actor, reason=reason)
        return audit

    def promote_invalid(self, rel: str, model: type[M], *,
                        actor: str, reason: str,
                        artifact_override: M | None = None) -> Path:
        """按**当前**判据重判合格之后，把 `.INVALID` 提升为正式产物。**唯一提升入口。**

        两件事，都是拿实测教训换来的：

        ① **生成来源账原样继承，并追加重验证审计**。`promoted()` 的签名里
           **没有** `prompts` / `model` / `inputs`，只有强类型审计账；生成来源没有可伪造入口。
           原型的 `--revalidate` 之所以能**伪造**，正是因为提升分支够得到那个
           会构造一份全新来源账的函数：它盖上当前 SYS 指纹 + 当前 model + 当前 inputs，
           `round` 还 +1，而**没有任何一次新落笔发生过**。

        ② **`.INVALID` 归档进 `_rounds/`，不删**（`Q-8`）。原型「读完问题稿就把它删了」
           造出的正是盘上那条幽灵边：另一份产物的 `inputs` 还指着它。
           **删一个还被别人指着的文件，会同时制造两个问题。**

        ③ **已有的正式稿也要归档，不许被覆盖掉。** 落正式稿走的是原子替换，
           而原子替换是**无条件**的：盘上那份**花过钱**的正式稿原地消失，
           `_rounds/` 里连一份副本都没有。原来只归档 `.INVALID` 那一条历史。
           对 stage5 这直接违反「上一轮正式稿进入 `_rounds`」；对别的层，
           它违反的是本模块自己那句「**归档是出事之后唯一能回去的那一份**」。

        ``artifact_override`` 只承载调用方按 live INVALID 重建的确定性派生字段。
        人工改动审计仍严格比较「不可变基线 vs live INVALID」，而最终来源账强制使用
        live 的 ``src.promoted(audit=...)``；override 自带的来源账永远不被信任。
        """
        official_rel = paths.check_rel_deep(rel)
        inv_rel = paths.invalid_of(official_rel)
        if not self.exists(inv_rel):
            raise UpstreamMissing(f'{self.ctx.node}: 没有 {inv_rel} 可提升')

        live_artifact, src, audit = self._revalidation_material(
            official_rel, model, actor=actor, reason=reason)
        artifact = artifact_override if artifact_override is not None else live_artifact
        if not isinstance(artifact, model):
            raise ProvenanceRefused(
                f'{official_rel} 的 artifact_override 必须是 {model.__name__}，'
                f'实得 {type(artifact).__name__}；一个字节都没动')

        # 【四步的顺序是挑过的】① 把两个**大概率的失败**挡在「一个字节都没动」之前
        # （轮次号撞了 · 载体层往返不过）② 上一轮正式稿进归档 ③ 落正式稿
        # ④ 把 `.INVALID` 挪进归档。
        #
        # ⚠ 剩下那个窗口（③④ 之间硬崩）**是刻意选的那一半**：崩在这里会同时留下
        #   一份提升好的正式稿和一份还没挪走的 `.INVALID`，而**再跑一次 `--revalidate`
        #   就自愈**（两份内容本来就相同，正式稿那份带 `revalidated: true`）。
        #   反过来「先归档后落稿」崩掉的话，`.INVALID` 已经从它该在的位置消失、
        #   正式稿又还没写——人必须自己去 `_rounds/` 里翻，而没有一行报告会告诉他去翻。
        # ⚠ 这一处进 `R-14.6` 那张「不保证原子的地方」的表（D 期建表时别漏了它）。
        round_no = self._round_on_disk(inv_rel) or self.next_round(inv_rel)
        dest = self.path(paths.round_rel(inv_rel, round_no))
        if _path_exists(dest):
            raise PathRefused(
                f'归档位 {dest} 已被占用，**一个字节都没动**。轮次号多半算错了')
        data = self._render(
            official_rel, artifact,
            prov_override=src.promoted(code=self.ctx.code, audit=audit),
        )
        self._verify_round_trip(official_rel, model, data)

        p = self.path(official_rel)
        # ② ⚠⚠ **挪，不是 `keep=True` 抄。** 抄一份会让归档位从此被占住，
        #   于是③④之间崩溃后的重跑撞上 `PathRefused` 卡死——那正好毁掉上面刻意选的那半个窗口。
        #   挪走之后重跑没有东西可归档，`--revalidate` 自愈。
        # ⚠⚠ **字节相同就不叫销毁。** 归档是为了保住一份**会被销毁**的版本；
        #   ③④ 之间崩过一次的话，重跑时盘上那份已经是提升好的那一份，
        #   再归档一次就是凭空往轮次账里塞一个**幻影轮**——对 stage5 就是
        #   「把一次重判记成第 N 轮写作」，正是 `promoted()` 存在要防的那件事。
        # ⚠ 正式稿与 `.INVALID` 的 stem 不同（`stem_of` 保留 `.INVALID`），
        #   两条历史各编各的号，`r1` 不会撞。
        official_before = _read_file_bytes(p) if _is_file(p) else None
        official_archive: Path | None = None
        invalid_archive: Path | None = None
        replacement_attempted = False
        try:
            if official_before is not None and official_before != data:
                official_archive = self.archive_round(official_rel)
            os.makedirs(_filesystem_path(p.parent), exist_ok=True)
            replacement_attempted = True
            self._replace_atomically(p, data)
            self._assert_self_fresh(official_rel, artifact)
            # INVALID 在正式稿通过 freshness 后置条件之前始终留在活路径。
            invalid_archive = self.archive_round(inv_rel, round_no=round_no)
        except BaseException as exc:
            rollback_errors: list[BaseException] = []
            if invalid_archive is not None and _is_file(invalid_archive) \
                    and not _path_exists(self.path(inv_rel)):
                try:
                    os.replace(
                        _filesystem_path(invalid_archive),
                        _filesystem_path(self.path(inv_rel)),
                    )
                except BaseException as rollback_exc:
                    rollback_errors.append(rollback_exc)
            try:
                if official_archive is not None and _is_file(official_archive):
                    try:
                        os.unlink(_filesystem_path(p))
                    except FileNotFoundError:
                        pass
                    os.replace(
                        _filesystem_path(official_archive), _filesystem_path(p))
                elif replacement_attempted:
                    if official_before is None:
                        if _is_file(p):
                            os.unlink(_filesystem_path(p))
                    else:
                        self._replace_atomically(p, official_before)
            except BaseException as rollback_exc:
                rollback_errors.append(rollback_exc)
            if rollback_errors:
                detail = '; '.join(repr(error) for error in rollback_errors)
                raise ProvenanceRefused(
                    f'{official_rel} 提升失败且回滚未完整完成：{detail}。'
                    '请勿继续下游，先人工恢复正式稿与 INVALID') from exc
            raise
        return p

    # ── 轮次与归档 ────────────────────────────────────────────────────────

    def seen_rounds(self, rel: str) -> set[int]:
        """这份产物在盘上见过的所有轮次：`_rounds/` 里的 + 当前那份记的。

        ⚠ **含 `.INVALID`**（`_common.md` §六）：被拦下的那一轮也是落过笔的一轮，
        不算进去会让下一轮与它同号。
        """
        rel = paths.check_rel_deep(rel)
        rounds: set[int] = set()
        directory = self.path(paths.rounds_dir_of(rel))
        stem = paths.stem_of(rel)
        if _is_dir(directory):
            for candidate in _directory_paths(directory):
                number = paths.round_no_of(candidate.name)
                if number is not None and candidate.name == f'{stem}.r{number}.json':
                    rounds.add(number)
        live = {rel} if paths.is_invalid(rel) else {rel, paths.invalid_of(rel)}
        for candidate in sorted(live):
            n = self._round_on_disk(candidate)
            if n is not None:
                rounds.add(n)
        return rounds

    def next_round(self, rel: str) -> int:
        """下一轮 = **盘上见过的最大轮次 + 1**，不是 `len(glob) + 1`。见 `prov.next_round`。"""
        return prov.next_round(self.seen_rounds(rel))

    def archive_round(self, rel: str, *, round_no: int | None = None,
                      keep: bool = False) -> Path:
        """把盘上当前这份挪进 `_rounds/<stem>.r<N>.json`。

        `round_no=None` ⇒ **按它自己记的那一轮**归档；没记就取 `next_round`。
        ⚠ **必须是「它自己的」而不是「下一个」**：写错这一处不会报错，
        它只是让轮次账**整体错位一格**（第 1 轮的内容躺在 `r2` 里、新稿标成第 3 轮），
        而等到有人去数「这是第几轮」时，已经没有别的账可以校对它了。

        `keep=True` ⇒ 抄一份而不是挪走。**这个差别是语义的，不是风格的**：
        逐章成稿归档之后当前文件必须**消失**（下一轮马上要写一份新的到同一个路径），
        而「先抄一份再原地改写」的那几层需要它留着。
        """
        rel = paths.check_rel_deep(rel)
        src = self.path(rel)
        if not _is_file(src):
            raise UpstreamMissing(f'{self.ctx.node}: 要归档的 {rel} 不在盘上')
        if round_no is None:
            own_round = self._round_on_disk(rel)
            if own_round is None and keep:
                # A hard crash may have completed the copy but not the live
                # replacement.  No-provenance companions (for example
                # global_review) cannot name their own round, so find an exact
                # prior copy before allocating a phantom next round.
                source_data = _read_file_bytes(src)
                for seen in sorted(self.seen_rounds(rel)):
                    candidate = self.path(paths.round_rel(rel, seen))
                    if _is_file(candidate) and _read_file_bytes(candidate) == source_data:
                        return candidate
            round_no = own_round or self.next_round(rel)
        dest = self.path(paths.round_rel(rel, round_no))
        if _path_exists(dest):
            if keep:
                try:
                    same = _is_file(dest) and _read_file_bytes(dest) == _read_file_bytes(src)
                except OSError as exc:
                    raise ArtifactUnreadable(
                        f'读不了归档重放的源/目标 {src} / {dest}：{exc!r}') from exc
                if same:
                    return dest
            raise PathRefused(
                f'归档目标已存在：{dest}。**归档是出事之后唯一能回去的那一份**，'
                f'不许静默覆盖——轮次号多半算错了（`len(glob)+1` 那个式子的典型症状）')
        # Round destinations are late-bound (the number depends on the live
        # provenance).  Register the exact source/destination with every outer
        # transaction before the first mutation, so rollback never sweeps the
        # whole `_rounds` directory and cannot erase an unrelated concurrent
        # writer's archive.
        self._track_history_path(src)
        self._track_history_path(dest)
        os.makedirs(_filesystem_path(dest.parent), exist_ok=True)
        if keep:
            self._replace_atomically(dest, _read_file_bytes(src))
        else:
            # ⚠ **一步 `os.replace`，不是「抄一份再删」。** 后者中间崩一次会同时留下
            #   原件和归档，而下一次归档会撞上已存在的目标、把这条路堵死。
            #   `_rounds/` 与产物同卷，`replace` 在这里是原子的。
            os.replace(_filesystem_path(src), _filesystem_path(dest))
        return dest

    def retire_bytes(self, rel: str, destination: str) -> Path:
        """Move live bytes to an immutable, content-addressed archive idempotently.

        An already-existing destination is accepted only when its bytes are exactly
        identical. This supports interrupted/repeated retirement of derived products.
        """
        source_rel = paths.check_rel_deep(rel)
        destination_rel = paths.check_rel_deep(destination)
        source = self.path(source_rel)
        dest = self.path(destination_rel)
        if not _is_file(source):
            raise UpstreamMissing(f'{self.ctx.node}: 要退休的 {source_rel} 不在盘上')
        source_data = self._read_bytes(source_rel)
        # The content-addressed destination is not discoverable by
        # ``history_transaction(rel)``.  Lazy registration makes a later
        # companion write/freshness failure restore the live bytes and remove
        # a newly-created retirement archive as one exact byte set.
        self._track_history_path(source)
        self._track_history_path(dest)
        if _path_exists(dest):
            try:
                same = _is_file(dest) and _read_file_bytes(dest) == source_data
            except OSError as exc:
                raise ArtifactUnreadable(
                    f'{self.ctx.node}: 读不了退休目标 {dest}：{exc!r}') from exc
            if not same:
                raise PathRefused(
                    f'退休目标 {dest} 已存在且字节不同；不可覆盖历史交付物')
            os.unlink(_filesystem_path(source))
            return dest
        os.makedirs(_filesystem_path(dest.parent), exist_ok=True)
        os.replace(_filesystem_path(source), _filesystem_path(dest))
        return dest

    def _round_on_disk(self, rel: str) -> int | None:
        """盘上那份 `_provenance.round`；没有文件 / 没有这个键 ⇒ `None`。"""
        p = self.path(rel)
        if not p.is_file():
            return None
        try:
            raw_data = json.loads(p.read_bytes().decode('utf-8'))
            data = decode_artifact(rel, raw_data).payload
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ArtifactVersionError):
            return None
        if not isinstance(data, dict):
            return None
        n = ((data.get(PROVENANCE_KEY) or {}) or {}).get('round')
        return int(n) if isinstance(n, int) and n >= 1 else None

    # ── 新鲜度 ────────────────────────────────────────────────────────────

    def freshness(self, rel: str) -> FreshnessReport:
        """这份产物是拿**盘上现在这几份**上游产的吗？算法在 `novel/prov.py::freshness`。

        ⚠ **读原始 dict，不过模型。** 一份陈旧产物很可能已经不合当前 schema
        （新契约裁了一批字段之后，原型产的 3/3 本全部加载失败），
        而「它是不是陈旧的」恰恰是这时候最该答得出来的问题。
        让新鲜度依赖校验通过，等于在最需要它的那一刻把它关掉。
        """
        rel = paths.check_rel_deep(rel)
        p = self.path(rel)
        if not _is_file(p):
            raise UpstreamMissing(f'{self.ctx.node}: 要判新鲜度的 {rel} 不在盘上')

        # 这六份确定性/字节形态产物没有通用 `_provenance.inputs` 槽位，但盘上已经有
        # 同等强度的 40 位指纹。先走兼容桥，既让历史产物可判，也不改 RootModel/纯文本
        # 的盘上形状。新增来源事实仍只记在原有权威字段里。
        deterministic = self._deterministic_freshness(rel)
        if deterministic is not None:
            return deterministic

        try:
            raw_data = json.loads(_read_file_bytes(p).decode('utf-8'))
            data = decode_artifact(rel, raw_data).payload
        except (UnicodeDecodeError, json.JSONDecodeError, ArtifactVersionError) as e:
            raise ArtifactUnreadable(f'{rel} 不是合法 JSON，判不了新鲜度：{e!r}') from e

        recorded = None
        if isinstance(data, dict):
            block = data.get(PROVENANCE_KEY)
            if isinstance(block, dict):
                root_was = block.get('root_clean_sha1')
                root_now = self._content_sha1_or_none(STAGE0_CLEAN_TEXT)
                if not isinstance(root_was, str):
                    return FreshnessReport('unknown', 'no_provenance')
                if root_now != root_was:
                    edge = self._content_edge(STAGE0_CLEAN_TEXT, root_was, root_now)
                    return self._content_report((edge,))
            recorded = (block or {}).get('inputs') if isinstance(block, dict) else None
            if block is not None and recorded is None:
                recorded = {}                # 有来源账但没有 `inputs` ⇒ `no_inputs`，不是 `no_provenance`
        return self.freshness_of_inputs(rel, recorded)

    def freshness_of_inputs(self, rel: str, recorded: dict | None) -> FreshnessReport:
        """同 `freshness`，但**来源账由调用方给**——用于还没落盘的那一份。

        ⚠⚠ **它存在的理由是「不许有第二把尺子」。** `freshness()` 读盘上那份，
        而判定路径上有一批产物是**在内存里刚重建、还没写**的
        （stage7 的 `--recheck` / `--apply-edit` / `--adopt-residual` 都是这个形状）。
        让调用方自己 `prov.freshness(...)` 拼一遍，两处必然在某次改动后分家；
        让它去读盘，判的就成了**上一份**产物的来源。
        ⇒ 同一个算法开两个入口，`recorded` 从哪来是唯一的区别。

        ⚠ `rel` 只用来定位 `producer_node`（决定 `consumed` 那一半怎么投影），
        **不读它的字节**。
        """
        node = producer_node(rel, fallback=self.ctx.node)
        return prov.freshness(
            recorded,
            file_now=self._file_fp_of_key,
            consumed_now=lambda key: self._consumed_fp_of_key(node, key),
        )

    def _deterministic_freshness(self, rel: str) -> FreshnessReport | None:
        """没有通用来源块的产物，用其既有确定性指纹回答同一个问题。

        stage0 的权威格 `revisions.json` 用根部原文身份；唯一物化正文从 current revision
        取 clean_text；stage1 三份共用 partition
        report 的 input 身份；成书（`stage7/<书名>_rewriting.txt`）的身份由
        name_map.book_txt 承载。
        这些本来就是对应关卡使用的权威比对，完成度只复用，不另造一份账。

        ⚠ **这张表漏一条的后果不是报错，是那份产物在完成度里恒 `unknown/no_provenance`**
        ——盘上明明有一份判得出新鲜度的产物，视图却说「没有来源账」。
        `stage0/revisions.json` 2026-08-22 落地时就漏在这里（8 本语料 8/8 复现），
        新增没有通用 `_provenance` 槽位的产物时必须同批接进来。
        """
        if rel == STAGE0_REVISIONS:
            return self._stage0_revisions_freshness()
        if rel == STAGE0_CLEAN_TEXT:
            return self._stage0_clean_freshness()
        if rel in (STAGE1_MAIN_TEXT, STAGE1_HOOK_COPY, STAGE1_PARTITION_REPORT):
            return self._stage1_freshness()
        if rel == STAGE2_GLOBAL_REVIEW:
            return self._global_review_freshness()
        if rel == STAGE5_BOOK_DRAFT:
            return self._stage5_draft_freshness()
        # ⚠ 成书的 rel 带书名 ⇒ **不能与 `STAGE7_BOOK` 直接比**（那是模板）。
        #   拿模板比出来的恒是 False，而后果是 `_deterministic_freshness` 返回 None
        #   ⇒ 成书的 freshness 变成「没有来源账」，那道对账**静默失效**。
        if rel == paths.book_txt_rel(self.ctx.novel_dir):
            return self._book_freshness()
        return None

    def _stage0_revisions_freshness(self) -> FreshnessReport:
        """`stage0/revisions.json`：这条链是照盘上**现在这份原文**建的吗？

        ⚠ **不给它补 `_provenance`。** 本层是全链源头、`inputs` 恒空，「这份产物照什么
        产的」由链根 `source_ref` / `normalized_source_sha1` 答完（理由写在
        `contracts/schema/stage0.py` 模块头）。
        补一个 `inputs` 恒空的来源块只是为了让通用那条路走通，等于给同一件事记第二本账。

        ⚠⚠ **只有原文这一条上游边。** `clean_text.txt` 是本格的物化投影，
        在它下游——把投影当成自己的上游，就是让权威格拿自己的影子自证新鲜，
        那种边永远 match，判别力是零。「clean live 是不是 `current` 的投影」由判据
        `s0_revision_chain`（`R-0.5`）逐条复算，完成度只读它、不另造一份。

        原文身份只在链根保存一次，不从任一 revision 反推。
        """
        chain = self._freshness_payload(STAGE0_REVISIONS)
        if chain is None:
            return FreshnessReport('unknown', 'no_provenance')
        source = chain.get('source')
        if not isinstance(source, dict):
            return FreshnessReport('unknown', 'no_provenance')
        edge = self._stage0_source_edge(
            source.get('source_ref'), source.get('normalized_source_sha1'))
        if edge is None:
            return FreshnessReport('unknown', 'no_provenance')
        return self._content_report((edge,))

    def _stage0_clean_freshness(self) -> FreshnessReport:
        chain = self._freshness_payload(STAGE0_REVISIONS)
        if chain is None:
            return FreshnessReport('unknown', 'no_provenance')
        source = chain.get('source')
        current = chain.get('current')
        revisions = chain.get('revisions')
        current_id = current.get('revision_id') if isinstance(current, dict) else None
        if not isinstance(source, dict) or not isinstance(revisions, list) \
                or not isinstance(current_id, str):
            return FreshnessReport('unknown', 'no_provenance')
        content = next(
            (item.get('content') for item in revisions
             if isinstance(item, dict) and item.get('revision_id') == current_id), None)
        clean_text = content.get('clean_text') if isinstance(content, dict) else None
        if not isinstance(clean_text, str):
            return FreshnessReport('unknown', 'no_provenance')
        clean_was = prov.content_sha1(clean_text.encode('utf-8'))
        clean_now = self._content_sha1_or_none(STAGE0_CLEAN_TEXT)
        clean_edge = self._content_edge(STAGE0_CLEAN_TEXT, clean_was, clean_now)
        source_edge = self._stage0_source_edge(
            source.get('source_ref'), source.get('normalized_source_sha1'))
        if source_edge is None:
            return FreshnessReport('unknown', 'no_provenance')
        return self._content_report((source_edge, clean_edge))

    def _stage0_source_edge(
            self, raw_ref: Any, source_was: Any) -> prov.EdgeVerdict | None:
        """「盘上那份原文还是当初那份吗」——stage0 三份产物共用的**同一条**边。

        答不出来时返回 `None`（调用方据此报 `unknown/no_provenance`）：
        「读不到原文」与「原文变了」是两件事，不许压成一个值。
        """
        if not isinstance(raw_ref, dict) or not isinstance(source_was, str) or not source_was:
            return None
        from contracts.schema.stage0 import SourceRef
        from pydantic import TypeAdapter
        try:
            source_ref = TypeAdapter(SourceRef).validate_python(raw_ref)
        except Exception:
            return None

        # ⚠ 这里曾有一条「记录的那条路径优先、重锚只是回落，顺序不许反」的纪律。
        #   2026-08-22 `source_ref` 换成判别联合之后**没有回落这回事了**：每一支只有
        #   一条路，`external_file` 认那一条绝对路径、`managed_upload` 认当前 base 下的
        #   内容地址目录。那条纪律守的性质（`_require_stage0_fresh` 必须拿到能反映
        #   「世界变了」的读数，而不是一份永远自洽的答案）现在由类型保证。
        from novel.core.source import describe_source, resolve_recorded_source
        source = resolve_recorded_source(source_ref, self.ctx.novel_dir.parent)
        if source is None:
            # ⚠ 边的 key 用记录里那条：读不到时要点名的就是它（`R-5.10`）。
            return self._content_edge(describe_source(source_ref), source_was, None)
        try:
            from novel.core.ingest import DecodeProblem, decode_source_bytes
            source_now = decode_source_bytes(source.read_bytes()).normalized_source_sha1
        except (DecodeProblem, OSError):
            return None
        # ⚠ key 用**真正读到的那个位置**：点一条其实读到了的旧路径，
        #   会把人送去找一个并不存在的问题。
        return self._content_edge(str(source), source_was, source_now)

    def _stage1_freshness(self) -> FreshnessReport:
        report = self._freshness_payload(STAGE1_PARTITION_REPORT)
        expected = report.get('input_sha1') if report is not None else None
        if not isinstance(expected, str) or not expected:
            return FreshnessReport('unknown', 'no_provenance')
        edge = self._content_edge(
            STAGE0_CLEAN_TEXT, expected, self._content_sha1_or_none(STAGE0_CLEAN_TEXT))
        return self._content_report((edge,))

    def _global_review_freshness(self) -> FreshnessReport:
        """Exact Stage2 review surface freshness, independent of approval state."""
        source = self._freshness_payload(STAGE2_GLOBAL_GROUNDING)
        if source is None:
            edge = prov.EdgeVerdict(
                key=STAGE2_GLOBAL_GROUNDING, state='unknown', reason='ghost',
                recorded_file=None, now_file=None,
                recorded_consumed=None, now_consumed=None,
            )
            return FreshnessReport('unknown', 'ghost', (edge,))
        review_payload = self._freshness_payload(STAGE2_GLOBAL_REVIEW)
        if review_payload is None:
            return FreshnessReport('unknown', 'no_provenance')

        from contracts.schema.evaluation import GlobalReview
        from novel.quality.global_review import build_review, review_status
        try:
            review = GlobalReview.model_validate(review_payload)
        except ValidationError as exc:
            raise ArtifactUnreadable(
                f'{STAGE2_GLOBAL_REVIEW} 不合 GlobalReview 契约，判不了新鲜度：{exc}') from exc

        expected_subject, exact_current, _approved = review_status(source, review)
        # Approval completeness is a gate/UI fact, not freshness.  Pending/FAIL/
        # UNVER decisions remain fresh as long as they describe the exact surface.
        expected = build_review(source)

        def identity_rows(value: Any) -> list[tuple[str, str, str, str]]:
            return [
                (item.item_id, item.kind, item.subject_path, item.subject_sha256)
                for item in value.items
            ]

        recorded_surface = prov.projection_fingerprint(identity_rows(review))
        current_surface = prov.projection_fingerprint(identity_rows(expected))
        reason = (
            'match' if exact_current else
            'file_changed' if review.source_sha256 != expected_subject else
            'consumed_changed'
        )
        state = 'fresh' if exact_current else 'stale'
        edge = prov.EdgeVerdict(
            key=STAGE2_GLOBAL_GROUNDING,
            state=state,
            reason=reason,
            recorded_file=review.source_sha256,
            now_file=expected_subject,
            recorded_consumed=recorded_surface,
            now_consumed=current_surface,
        )
        return FreshnessReport(state, reason, (edge,))

    def _stage5_draft_freshness(self) -> FreshnessReport:
        """``draft.md`` 是否逐字等于当前 canonical 章集合的纯投影。

        这份字节产物没有 ``_provenance`` 槽位；它的权威身份就是当前
        ``writer_context.chapters`` 分母与 ``stage5/chNN.json`` 集合经生产同一把
        renderer 算出的 UTF-8 字节。缺分母时只能回答 unknown，绝不把「算不了」
        伪装成 fresh。
        """
        if not _is_file(self.path(STAGE5_BOOK_DRAFT)):
            return FreshnessReport('unknown', 'no_provenance')

        from contracts.schema.stage4 import WriterContext
        try:
            context, _raw = self.read_own(WriterContext, STAGE4_WRITER_CONTEXT)
        except UpstreamMissing:
            return FreshnessReport('unknown', 'no_provenance')

        # 动态 import 避免 store 模块初始化时反向 import stage；运行时复用 canonical
        # loader，不能在这里再写一份 chNN 选择/校验规则。
        from novel.stages.stage5.measure import load_drafts
        by_ordinal, _invalids, _judged = load_drafts(self, require_current=False)
        expected = render_stage5_draft(
            (chapter.output_ordinal for chapter in context.chapters), by_ordinal,
        ).encode('utf-8')
        current = _read_file_bytes(self.path(STAGE5_BOOK_DRAFT))
        self._decode(STAGE5_BOOK_DRAFT, current)
        edge = self._content_edge(
            STAGE5_BOOK_DRAFT,
            prov.content_sha1(expected),
            prov.content_sha1(current),
        )
        return FreshnessReport(edge.state, edge.reason, (edge,))

    def _book_freshness(self) -> FreshnessReport:
        name_map = self._freshness_payload(STAGE7_NAME_MAP)
        book_txt = name_map.get('book_txt') if name_map is not None else None
        expected = book_txt.get('sha1') if isinstance(book_txt, dict) else None
        if not isinstance(expected, str) or not expected:
            return FreshnessReport('unknown', 'no_provenance')
        # 边的 key 用**这本书的**那条具体路径：报告里要点名的就是它。
        book_rel = paths.book_txt_rel(self.ctx.novel_dir)
        edge = self._content_edge(
            book_rel, expected, self._content_sha1_or_none(book_rel))
        return self._content_report((edge,))

    def _freshness_payload(self, rel: str) -> dict[str, Any] | None:
        """为新鲜度读一份 companion；缺席表示没有账，坏 JSON 仍是显式不可读。"""
        p = self.path(rel)
        if not p.is_file():
            return None
        try:
            raw = json.loads(p.read_bytes().decode('utf-8'))
            payload = decode_artifact(rel, raw).payload
        except (OSError, UnicodeDecodeError, json.JSONDecodeError,
                ArtifactVersionError) as e:
            raise ArtifactUnreadable(f'{rel} 不是可用的来源指纹载体：{e!r}') from e
        return payload if isinstance(payload, dict) else None

    def _content_sha1_or_none(self, rel: str) -> str | None:
        p = self.path(rel)
        if not p.is_file():
            return None
        try:
            return prov.content_sha1(p.read_bytes())
        except OSError as e:
            raise ArtifactUnreadable(f'{self.ctx.node}: 读不了 {rel} 的内容指纹：{e!r}') from e

    @staticmethod
    def _content_edge(key: str, recorded: str, now: str | None) -> prov.EdgeVerdict:
        reason = 'ghost' if now is None else 'match' if recorded == now else 'file_changed'
        state = {'ghost': 'unknown', 'match': 'fresh', 'file_changed': 'stale'}[reason]
        return prov.EdgeVerdict(
            key=key, state=state, reason=reason,
            recorded_file=recorded, now_file=now,
            recorded_consumed=None, now_consumed=None,
        )

    @staticmethod
    def _content_report(edges: tuple[prov.EdgeVerdict, ...]) -> FreshnessReport:
        rank = {'fresh': 0, 'unknown': 1, 'stale': 2}
        worst = max(edges, key=lambda edge: rank[edge.state])
        return FreshnessReport(worst.state, worst.reason, edges)

    def _file_fp_of_key(self, key: str) -> str | None:
        """来源账里那个键 → 盘上现在的整份指纹。**取不到一律 `None`（幽灵边）。**

        ⚠ 键是存量产物里记下来的，可能是任何字符串。不合法的一律当成「反查不到」——
        **别让一条坏键抛异常**，那会把「有一条边指向已删文件」变成「整份报告出不来」。
        """
        parts = str(key).replace('\\', '/').split('/')
        if not parts or any(p in ('', '.', '..') for p in parts):
            return None
        p = self.ctx.novel_dir.joinpath(*parts)
        try:
            return prov.fingerprint_bytes(p.read_bytes())
        except OSError:
            return None

    # ── 内部：写盘口的两道闸 + 原子落盘 ───────────────────────────────────

    @staticmethod
    def _approval(actor: str, reason: str) -> HumanApproval:
        try:
            return HumanApproval(actor=actor, reason=reason)
        except ValidationError as exc:
            raise ApprovalRequired(
                '提升 `.INVALID` 必须显式给非空 actor 与 reason；'
                '空白凭据不能形成可问责的人工审计账') from exc

    @staticmethod
    def _business_payload(payload: Any) -> Any:
        """The editable artifact content, excluding all provenance bookkeeping."""
        if not isinstance(payload, dict):
            return payload
        return {key: value for key, value in payload.items() if key != PROVENANCE_KEY}

    @staticmethod
    def _revalidation_baseline_rel(inv_rel: str, locator: str) -> str:
        inv_rel = paths.check_rel_deep(inv_rel)
        if len(locator) != 64 or any(ch not in '0123456789abcdef' for ch in locator):
            raise ProvenanceRefused(
                f'{inv_rel} 的 invalid_baseline_sha256 不是完整小写 SHA-256：{locator!r}')
        parent = os.path.dirname(inv_rel).replace('\\', '/')
        # The locator is the identity.  Repeating a long artifact stem bought no
        # safety and made the real acceptance path exceed Windows MAX_PATH.
        return f'{parent}/{REVALIDATION_DIRNAME}/b.{locator}.snapshot'

    # ⚠ 这里曾有 `_legacy_revalidation_baseline_rel()`：短名 `b.<locator>.snapshot` 之前
    #   用的是 `<长 stem>.<locator>.snapshot`，读侧留了一条只读回落。删除那天的实测是
    #   **盘上 8 份快照 8/8 都是短名，长名 0 份**，唯一走过那条分支的是它自己的测试
    #   ——一个靠自己的测试活着的兼容层。产物已整批清空，连那 8 份也不在了。
    #   ⚠ 长名正是当初撞 Windows MAX_PATH 的原因（见上面 `_revalidation_baseline_rel`
    #   的注释）：真要恢复它，等于把那个已知缺陷一起恢复。

    def _write_immutable(self, destination: Path, data: bytes) -> None:
        """Create a content-addressed audit snapshot; never replace history."""
        self._track_history_path(destination)
        if os.path.exists(_filesystem_path(destination)):
            try:
                same = _is_file(destination) and _read_file_bytes(destination) == data
            except OSError as exc:
                raise ArtifactUnreadable(
                    f'读不了既有重验证基线 {destination}：{exc!r}') from exc
            if not same:
                raise ProvenanceRefused(
                    f'不可变重验证基线 {destination} 已存在但字节不同；'
                    '拒绝覆盖审计历史（可能是文件被改动或发生 hash 冲突）')
            return
        os.makedirs(_filesystem_path(destination.parent), exist_ok=True)
        self._replace_atomically(destination, data)

    def _revalidation_material(self, rel: str, model: type[M], *,
                               actor: str, reason: str
                               ) -> tuple[M, Provenance, RevalidationAudit]:
        approval = self._approval(actor, reason)
        official_rel = paths.check_rel_deep(rel)
        inv_rel = paths.invalid_of(official_rel)
        if not self.exists(inv_rel):
            raise UpstreamMissing(f'{self.ctx.node}: 没有 {inv_rel} 可重验证')

        current_raw = self._read_bytes(inv_rel)
        artifact, current_payload = self._validate_payload(
            model, inv_rel, self._loads(inv_rel, current_raw))
        src = self._provenance_of(artifact)
        if src is None:
            raise ProvenanceRefused(
                f'{inv_rel} 没有来源账，提升上来就是一份来历不明的正式产物')
        locator = src.invalid_baseline_sha256
        if locator is None:
            raise ProvenanceRefused(
                f'{inv_rel} 没有 write_invalid 记录的不可变基线定位键；'
                '无法分辨原样重判与人工改字节，拒绝洗掉来源')

        baseline_rel = self._revalidation_baseline_rel(inv_rel, locator)
        baseline_path = self.path(baseline_rel)
        if not _is_file(baseline_path):
            raise ProvenanceRefused(
                f'{inv_rel} 声明的不可变基线不存在：{baseline_rel}；'
                '没有对照物就不能形成重验证审计账')
        try:
            baseline_raw = _read_file_bytes(baseline_path)
        except OSError as exc:
            raise ArtifactUnreadable(f'读不了重验证基线 {baseline_rel}：{exc!r}') from exc
        baseline_encoded = self._loads(inv_rel, baseline_raw)
        baseline_artifact, baseline_payload = self._validate_payload(
            model, inv_rel, baseline_encoded)
        baseline_src = self._provenance_of(baseline_artifact)
        if baseline_src is None or baseline_src.invalid_baseline_sha256 != locator:
            raise ProvenanceRefused(
                f'不可变基线 {baseline_rel} 与 INVALID 声明的定位键不一致；拒绝提升')

        # 定位键钉的是**当时那一份带版本信封的原始字节**。核验必须拿 raw encoded
        # object 去比，不能拿解码后的 payload：后者不含 `_schema_version`，而将来任何
        # 一次 `current_version` +1 都会让 `encode_artifact` 吐出不同的字节
        # ⇒ 一份完好的基线会仅仅因为版本号变了就被判成「被改过」。
        # ⚠ 与迁移无关，删掉迁移机制之后这条依然成立——版本号本身就在被签名的字节里。
        if not isinstance(baseline_encoded, dict):
            raise ProvenanceRefused(f'{baseline_rel} 的原始 JSON 不是 object，无法核验定位键')
        subject_encoded = dict(baseline_encoded)
        block = subject_encoded.get(PROVENANCE_KEY)
        if not isinstance(block, dict):
            raise ProvenanceRefused(f'{baseline_rel} 的来源账不是 object，无法核验定位键')
        subject_block = dict(block)
        subject_block.pop('invalid_baseline_sha256', None)
        subject_encoded[PROVENANCE_KEY] = subject_block
        subject_raw = (
            json.dumps(subject_encoded, ensure_ascii=False, indent=2) + '\n'
        ).encode('utf-8')
        actual_locator = hashlib.sha256(subject_raw).hexdigest()
        if actual_locator != locator:
            raise ProvenanceRefused(
                f'不可变基线 {baseline_rel} 内容校验失败：声明 {locator}，'
                f'按逻辑内容重算为 {actual_locator}；拒绝用被改过的基线提升')

        baseline_business = self._business_payload(baseline_payload)
        candidate_business = self._business_payload(current_payload)
        baseline_canonical = hashlib.sha256(
            _canonical_json_bytes(baseline_business)).hexdigest()
        candidate_canonical = hashlib.sha256(
            _canonical_json_bytes(candidate_business)).hexdigest()
        baseline_raw_sha = hashlib.sha256(baseline_raw).hexdigest()
        candidate_raw_sha = hashlib.sha256(current_raw).hexdigest()
        pointers = _json_pointer_diff(baseline_business, candidate_business)
        content_changed = baseline_canonical != candidate_canonical
        audit = RevalidationAudit(
            actor=approval.actor,
            reason=approval.reason,
            baseline_locator_sha256=locator,
            baseline_raw_sha256=baseline_raw_sha,
            candidate_raw_sha256=candidate_raw_sha,
            baseline_canonical_sha256=baseline_canonical,
            candidate_canonical_sha256=candidate_canonical,
            raw_changed=baseline_raw_sha != candidate_raw_sha,
            content_changed=content_changed,
            edited_by_human=content_changed,
            changed_json_pointers=pointers,
        )
        return artifact, src, audit

    def _assert_self_fresh(self, rel: str, artifact: BaseModel) -> None:
        """A provenance-bearing formal artifact must be fresh immediately after write."""
        if self._provenance_of(artifact) is None and rel not in SELF_FRESH_DETERMINISTIC:
            return
        report = self.freshness(rel)
        if report.state == 'fresh':
            return
        edges = ', '.join(
            f'{edge.key}:{edge.reason}' for edge in report.edges) or report.reason
        raise ProvenanceRefused(
            f'{self.ctx.node} 刚写完 {rel}，freshness 后置条件却是 '
            f'{report.state}/{report.reason}（{edges}）。正式路径已回滚；'
            '这通常表示写盘方 ctx 与真正产地方不一致，或来源投影没有登记')

    def _consumed_fp_of_key(self, node: str | None, key: str) -> str | None:
        if node is None or (project := projection_for(node, key)) is None:
            return None
        return self._project_current(key, project)

    def _project_current(self, key: str, project: Callable[[Any], Any]) -> str | None:
        parts = str(key).replace(chr(92), '/').split('/')
        if not parts or any(part in ('', '.', '..') for part in parts):
            return None
        p = self.ctx.novel_dir.joinpath(*parts)
        try:
            raw = p.read_bytes()
            data: object = (decode_artifact(key, json.loads(raw.decode('utf-8'))).payload
                            if p.suffix.lower() == '.json' else raw.decode('utf-8'))
            return prov.projection_fingerprint(project(data))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError,
                AttributeError, KeyError, IndexError, TypeError, ValueError,
                ArtifactVersionError):
            return None

    @staticmethod
    def _provenance_of(artifact: BaseModel) -> Provenance | None:
        name = provenance_field(type(artifact))
        return getattr(artifact, name) if name else None

    @classmethod
    def _root_of(cls, artifact: BaseModel) -> str | None:
        src = cls._provenance_of(artifact)
        return src.root_clean_sha1 if src is not None else None

    def _record_artifact_root(self, rel: str, artifact: BaseModel) -> None:
        record = getattr(self.ctx, 'record_artifact_root', None)
        if callable(record):
            record(rel, self._root_of(artifact))

    def _refuse_bad_provenance(self, rel: str, src: Provenance) -> None:
        """写盘口的闸。**拒绝，不是打印一句警告然后照写。**

        ① **`prompts` 非空而 `inputs` 为空 ⇒ 拒。** 一个跑过 LLM 的节点不可能没有上游；
           `inputs` 空就是**某处绕过了 `store.read`**。原型这里是 `print` 一句，
           于是三份产物的来源账各漏各的，而没有任何一层出声。

        ⚠⚠ **另一道闸（「0 成本节点不许记 `model`」）不在这里，在铸造口**
        （`RunContext.llm_provenance`）。这是本轮实测改掉的一处：
        放在写盘口会误伤若干 0 成本入口——`--revalidate` / `--recheck`
        写回的是一份**继承来的**来源账，它记的 `model` 属于当初那次付费产出，
        不是本次跑的新宣称；`stage6.approve` / `stage7.approve` 同理
        （它们是 0 成本节点，改写的却是付费节点产的那份产物）。
        ⇒ 该拦的是「**0 成本的跑现造一份声称模型产了它的账**」，而现造只有一个入口。
        """
        prompts = getattr(src, 'prompts', None)
        current_root = self.ctx.root_clean_sha1
        if src.root_clean_sha1 != current_root:
            raise ProvenanceRefused(
                f'{self.ctx.node} 要往 {rel} 写 root_clean_sha1={src.root_clean_sha1} 的产物，'
                f'但当前 stage0/clean_text.txt 是 {current_root}；拒绝混根或伪造重挂')
        if prompts and not src.inputs:
            raise ProvenanceRefused(
                f'{self.ctx.node} 要往 {rel} 写一份 `prompts` 非空而 `inputs` 为空的来源账。'
                f'跑过 LLM 的节点不可能没有上游 ⇒ 多半有一处绕过了 `store.read`（它是唯一入口，'
                f'记账在那里发生）。⚠ 原型这里只 print 一句警告然后照写，'
                f'代价是三份产物的来源账各漏各的，而没有任何一层出声')

    def _render(self, rel: str, artifact: BaseModel, *,
                prov_override: Provenance | None = None) -> bytes:
        """产物 → **将要落盘的那串字节**。过写盘口那道闸，但一个字节都不写。

        ⚠ 抽出来是给 `promote_invalid` 用的：它要在**动归档之前**知道自己将要写什么，
        才能回答「盘上那份会不会被销毁」。见那边第②步。
        """
        name = provenance_field(type(artifact))
        payload = artifact.model_dump(by_alias=True, mode='json')
        if name is not None:
            src = prov_override if prov_override is not None else getattr(artifact, name)
            self._refuse_bad_provenance(rel, src)
            # ⚠ 三态只在 `to_disk()` 里兑现：`revalidated: null` 与「没有这个键」
            #   在 JSON 里必须是两份不同的字节，而 `model_dump` 会把 `None` 写成 `null`。
            payload[PROVENANCE_KEY] = src.to_disk()
        payload = encode_artifact(rel, payload)
        return (json.dumps(payload, ensure_ascii=False, indent=2) + '\n').encode('utf-8')

    def _write_json(self, rel: str, artifact: BaseModel, *,
                    prov_override: Provenance | None = None) -> Path:
        data = self._render(rel, artifact, prov_override=prov_override)
        p = self.path(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        self._verify_round_trip(rel, type(artifact), data)
        self._replace_atomically(p, data)
        return p

    @staticmethod
    def _verify_round_trip(rel: str, model: type[BaseModel], data: bytes) -> None:
        """**落盘之前**先证明这堆字节读得回来。

        ⚠ 只 `json.load` 一下证明不了什么——它只说「是合法 JSON」。
        真正会咬人的是**模型这一侧**：某个 `None` 被 `exclude_none` 顺手抹掉之后，
        下一次 `model_validate` 会因为「必填键不在」当场失败，
        而那时产物已经落了盘、上游也已经花过钱。
        """
        try:
            raw = json.loads(data.decode('utf-8'))
            decoded = decode_artifact(rel, raw)
            model.model_validate(decoded.payload)
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError,
                ArtifactVersionError) as e:
            raise ProvenanceRefused(
                f'{rel} 序列化之后读不回 {model.__name__}，**没有落盘**：\n{e}\n'
                f'→ 这是载体层的 bug（多半是某个三态键在落盘形态里被抹掉了），不是数据问题'
            ) from e

    @staticmethod
    def _replace_atomically(p: Path, data: bytes) -> None:
        """写临时文件 → `os.replace`。**同目录**，否则 `replace` 跨卷就不是原子的了。"""
        # Do not repeat ``p.name`` in the temporary name.  Content-addressed
        # destinations are long already; ``<target>.<pid>.tmp`` crossed
        # MAX_PATH even when its parent directory was perfectly usable.
        fd = -1
        tmp: str | None = None
        try:
            fd, tmp = tempfile.mkstemp(
                prefix='.atomic-', suffix='.tmp',
                dir=_filesystem_path(p.parent),
            )
            stream = os.fdopen(fd, 'wb')
            fd = -1  # ownership transferred to ``stream``
            with stream:
                stream.write(data)
            os.replace(tmp, _filesystem_path(p))
        finally:
            if fd >= 0:
                os.close(fd)
            # `replace` 成功之后 tmp 已经不在了；只有失败路径才真的删到东西。
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except FileNotFoundError:
                    pass
