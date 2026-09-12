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

import copy
import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, TypeVar, cast

from pydantic import BaseModel, ValidationError

from contracts.artifacts import (
    STAGE0_CLEAN_TEXT, STAGE0_REVISIONS,
    STAGE1_HOOK_COPY, STAGE1_MAIN_TEXT,
    STAGE2_ANALYZE_RECEIPT, STAGE2_BLUEPRINT_CHARACTER_PROJECTION, STAGE2_CHAPTER_BLUEPRINTS, STAGE2_CHARACTERS,
    STAGE2_GLOBAL_GROUNDING, STAGE2_GLOBAL_REVIEW,
    STAGE4_BLIND_CANDIDATE, STAGE4_EVIDENCE_BINDING,
    STAGE4_PLAN_CHAPTERS, STAGE4_WRITER_CONTEXT,
    STAGE7_NAME_MAP,
)
from contracts.schema import (
    ArtifactVersionError, HumanApproval, InputEdge, Provenance, RevalidationAudit,
    artifact_spec, decode_artifact, encode_artifact,
)
from contracts.schema.stage4 import EvidenceBinding
from novel import paths, prov
from novel.consumption import (
    canonical_rel, context_inputs_for,
    producer_node, project_consumed,
    projection_for,
)
from novel.errors import (
    ApprovalRequired, ArtifactUnreadable, ByteFormRefused, PathRefused,
    ProvenanceRefused, UpstreamMissing, UpstreamStale,
)
from novel.prov import FreshnessReport
from novel.run import RunContext

M = TypeVar('M', bound=BaseModel)
T = TypeVar('T')

#: 产物里那个来源账字段的**盘上键名**。模型侧字段叫 `provenance`（`_` 开头在 Pydantic 里
#: 是私有属性），靠 `Field(alias='_provenance')` 对上；这里按 alias 找，不按字段名猜。
PROVENANCE_KEY = '_provenance'
REVALIDATION_DIRNAME = '_revalidation'
REVALIDATION_REFERENCE_VERSION = 1

#: 提升时可从 ``artifact_override`` 带入的**确定性归一账**。
#: 生成来源（prompts/model/inputs/usage/round）仍强制取不可变创建基线；
#: 这张按 artifact 关闭的白名单只允许同一 normalizer 重算的派生账，
#: 否则 Stage2 ``--revalidate`` 虽然提升了新 SourceSpan，正式 provenance
#: 却会被旧 ``evidence_locator_failures`` 再次判红。
_REVALIDATION_DERIVED_PROVENANCE_FIELDS = {
    STAGE2_CHARACTERS: frozenset(),
    STAGE2_GLOBAL_GROUNDING: frozenset({
        'evidence_locator_failures',
    }),
}

# These no-provenance artifacts have a deterministic freshness bridge and are
# written only after the companion facts needed by that bridge are available.
# stage0 clean_text and stage1 main/hook are deliberately absent: their freshness
# depends on companion authority and, for Stage0, the external source edge.
# ⚠ **stage0 整层都不在这里**（`revisions.json` 与 `clean_text.txt` 一并），
#   那是有理由的、不是又漏了一条：它们的确定性边要去 resolve 真实原文，而
#   「换了一份原文」在 stage0 是一条**合法的、由判据去拒**的路（`R-0.14`），
#   写盘口再拒一次就把它变成写不进去。判别力落在判据那一侧，不在这道后置条件。
SELF_FRESH_DETERMINISTIC = frozenset({
    STAGE2_BLUEPRINT_CHARACTER_PROJECTION,
    STAGE2_GLOBAL_REVIEW,
})


@dataclass
class _ReadonlySnapshotMemo:
    """One operator snapshot's immutable read memo; discarded after its CAS.

    This is deliberately not an ``lru_cache``.  It only exists while a caller
    holds the before/after active-surface seal, and only readonly RunContexts may
    consume the parsed/freshness maps.  A later invocation therefore cannot see
    bytes from an earlier filesystem state.
    """

    novel_dir: Path
    code: str | None = None
    raw: dict[Path, bytes] = field(default_factory=dict)
    projections: dict[str, tuple[bool, object]] = field(default_factory=dict)
    consumed: dict[tuple[str, str], str | None] = field(default_factory=dict)
    freshness: dict[tuple[str, str], FreshnessReport] = field(default_factory=dict)
    validated: dict[tuple[type[BaseModel], str], bytes] = field(
        default_factory=dict)
    values: dict[tuple[object, ...], object] = field(default_factory=dict)


_READONLY_SNAPSHOT_MEMO: ContextVar[_ReadonlySnapshotMemo | None] = ContextVar(
    'novel_store_readonly_snapshot_memo', default=None)


@contextmanager
def readonly_snapshot_memo(
    novel_dir: Path, *, code: str | None = None,
) -> Iterator[None]:
    """Reuse identical readonly bytes/projections inside one sealed snapshot.

    Public operator invocations may not nest.  Reusing an outer memo whose bytes
    were captured before this invocation's surface seal would let stale memory
    survive both sides of the CAS.  Internal consumers share the map directly;
    they never open a second context manager.
    """
    root = Path(os.path.abspath(os.fspath(novel_dir)))
    current = _READONLY_SNAPSHOT_MEMO.get()
    if current is not None:
        raise ValueError(
            'readonly snapshot memo already active; a new operator invocation '
            'must start from an empty memo')
    token = _READONLY_SNAPSHOT_MEMO.set(_ReadonlySnapshotMemo(root, code=code))
    try:
        yield
    finally:
        _READONLY_SNAPSHOT_MEMO.reset(token)


def _memo_for_context(ctx: RunContext) -> _ReadonlySnapshotMemo | None:
    """Return the active memo only for its exact readonly book/epoch context."""
    if not getattr(ctx, 'readonly', False):
        return None
    memo = _READONLY_SNAPSHOT_MEMO.get()
    if memo is None:
        return None
    root = Path(os.path.abspath(os.fspath(ctx.novel_dir)))
    if root != memo.novel_dir:
        raise ValueError(
            f'readonly snapshot memo belongs to {memo.novel_dir}, not {root}')
    if memo.code is not None and ctx.code != memo.code:
        raise ValueError(
            f'readonly snapshot memo code is {memo.code}, not {ctx.code}')
    return memo


def readonly_snapshot_value(
    ctx: RunContext, key: tuple[object, ...], factory: Callable[[], T],
) -> T:
    """Memoize one pure readonly projection inside the active snapshot only."""
    memo = _memo_for_context(ctx)
    if memo is None:
        return factory()
    if key not in memo.values:
        memo.values[key] = copy.deepcopy(factory())
    return cast(T, copy.deepcopy(memo.values[key]))


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
    memo = _READONLY_SNAPSHOT_MEMO.get()
    key = Path(os.path.abspath(os.fspath(path)))
    if memo is not None:
        try:
            key.relative_to(memo.novel_dir)
        except ValueError:
            memo = None
        else:
            if key in memo.raw:
                return memo.raw[key]
    with open(_filesystem_path(path), 'rb') as stream:
        raw = stream.read()
    if memo is not None:
        memo.raw[key] = raw
    return raw


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


def build_revalidation_audit(
    *,
    actor: str,
    reason: str,
    baseline_locator_sha256: str,
    baseline_raw_sha256: str,
    baseline_payload: Any,
    candidate_raw_sha256: str,
    candidate_payload: Any,
) -> RevalidationAudit:
    """铸一份「基线 → 候选」的不可变对证账。**全仓唯一的构造口。**

    当前用于 .INVALID 提升：基线是已落盘产物的不可变快照。它回答字节、
    逻辑内容是否变化及具体 JSON pointer，不根据展示读数推断人工修改。
    edited_by_human 与 content_changed 同值：此路只允许人修改待提升的候选。
    """
    baseline_canonical = hashlib.sha256(
        _canonical_json_bytes(baseline_payload)).hexdigest()
    candidate_canonical = hashlib.sha256(
        _canonical_json_bytes(candidate_payload)).hexdigest()
    content_changed = baseline_canonical != candidate_canonical
    return RevalidationAudit(
        actor=actor,
        reason=reason,
        baseline_locator_sha256=baseline_locator_sha256,
        baseline_raw_sha256=baseline_raw_sha256,
        candidate_raw_sha256=candidate_raw_sha256,
        baseline_canonical_sha256=baseline_canonical,
        candidate_canonical_sha256=candidate_canonical,
        raw_changed=baseline_raw_sha256 != candidate_raw_sha256,
        content_changed=content_changed,
        edited_by_human=content_changed,
        changed_json_pointers=_json_pointer_diff(baseline_payload, candidate_payload),
    )


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
        from novel.output_root import require_active
        require_active(ctx.novel_dir.resolve().parent)
        if _READONLY_SNAPSHOT_MEMO.get() is not None:
            if not ctx.readonly:
                raise ValueError(
                    'writable ArtifactStore cannot run inside a readonly '
                    'operator snapshot memo')
            _memo_for_context(ctx)
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

    def full_sha256_or_none(self, rel: str) -> str | None:
        """一份两级盘上产物的完整字节 SHA-256；缺席返回 ``None``。

        这是 hash-bound 决策的只读复核口，不产生普通 provenance 输入边。调用者若
        真正消费产物，仍必须走 ``read`` / ``read_with_sha256`` 完成 schema 校验与记账。
        """
        key = paths.check_rel(rel)
        try:
            raw = _read_file_bytes(self.path(key))
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise ArtifactUnreadable(
                f'{self.ctx.node}: 读不了 {key} 的完整字节指纹：{exc!r}') from exc
        return hashlib.sha256(raw).hexdigest()

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
        obj, data, raw = self._validated_read(model, rel)
        self._record_artifact_root(rel, obj)
        self.ctx.record_edge(key, InputEdge(
            file=prov.fingerprint_bytes(raw),
            consumed=self._consumed_fingerprint(self.ctx.node, key, data),
        ))
        return obj, hashlib.sha256(raw).hexdigest()

    @classmethod
    def decode_snapshot(cls, model: type[M], rel: str, raw: bytes) -> M:
        """经现役版本与 schema 解码冻结字节；不宣称盘上来源或消费边。"""
        paths.check_rel_deep(rel)
        return cls._validate_payload(model, rel, cls._loads(rel, raw))[0]

    @contextmanager
    def preview_dependency(self, rel: str, artifact: BaseModel):
        """只读预览两相 bundle 内尚未落盘的依赖边，离开后不污染真实消费账。

        只接受当前 schema 的完整 typed 对象，经同一 renderer/decoder/消费投影
        重建预期边。调用方仍须独立证明 sealed 对象来源；此口没有写入能力。
        """
        if not self.ctx.readonly:
            raise ProvenanceRefused('预览依赖只允许只读上下文，不能用于正式产地记账')
        key = paths.input_key(rel)
        raw = self._render(rel, artifact)
        obj, data = self._validate_payload(type(artifact), rel, self._loads(rel, raw))
        with self.ctx.validation_reads():
            self._record_artifact_root(rel, obj)
            self.ctx.record_edge(key, InputEdge(
                file=prov.fingerprint_bytes(raw),
                consumed=self._consumed_fingerprint(self.ctx.node, key, data)))
            yield obj

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
        obj, data, raw = self._validated_read(model, rel)
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

        ⚠ **同时返回原始 dict** 是有用的：模型化会填入默认值、归一化容器，抹平
        「盘上缺字段」与「字段显式取默认值」的差别；需要检查原始落盘形状的调用方
        必须拿这里返回的 payload，而不能从模型再 dump 一份冒充原字节形状。

        ``replaced_input_keys`` 只服务 typed 人工修订：问题稿会把**上一份**编辑决策集
        记成输入，而人的下一步恰恰是修改同一个文件。此时只放行被明确替换的那条边；
        正文、设定等其余上游仍关闭式拒绝。调用方不给键时与普通 ``require_current``
        完全同义，不能把它当成「问题稿一律跳过 freshness」的口子。
        """
        obj, payload, _raw = self._read_own_validated(
            model,
            rel,
            require_current=require_current,
            replaced_input_keys=replaced_input_keys,
        )
        return obj, payload

    def read_own_with_sha256(
        self,
        model: type[M],
        rel: str,
        *,
        require_current: bool = False,
        replaced_input_keys: Iterable[str] = (),
    ) -> tuple[M, str]:
        """读本节点自己的产物，并返回这一次读取的完整字节 SHA-256。

        与 :meth:`read_own` 一样，这条路刻意不把本节点自己的产物记成普通
        provenance 输入；区别只在于 hash-bound 提交/复核还需要绑定**同一次读取**
        的精确盘上字节。不能先 ``read_own`` 再另读文件算 hash：两次读取之间的
        TOCTOU 会让模型与摘要可能来自两份不同内容。

        schema、根身份与可选 currentness 仍全部经过 ``read_own`` 共用的唯一口，
        不允许调用方为了拿摘要退回裸文件读取或从模型重序列化估算。
        """
        obj, _payload, raw = self._read_own_validated(
            model,
            rel,
            require_current=require_current,
            replaced_input_keys=replaced_input_keys,
        )
        return obj, hashlib.sha256(raw).hexdigest()

    def read_own_by_sha256(self, model: type[M], rel: str, sha256: str) -> M:
        """按原始字节冻结自身基线，只查该产物正式、INVALID 及已有轮次。

        用于人物修订在自身写盘后恢复原请求；不迁移旧版本，不以当前稿冒充原基线。
        """
        if re.fullmatch(r'[0-9a-f]{64}', sha256) is None:
            raise ProvenanceRefused('冻结基线需要完整 SHA-256')
        formal = canonical_rel(paths.check_rel_deep(rel))
        candidates = [formal, paths.invalid_of(formal)]
        for current in tuple(candidates):
            directory = self.path(paths.rounds_dir_of(current))
            candidates.extend(paths.round_rel(current, number) for number in sorted(
                paths.archived_rounds(directory, paths.stem_of(current))))
        for candidate in candidates:
            if not self.exists(candidate):
                continue
            raw = self._read_bytes(candidate)
            if hashlib.sha256(raw).hexdigest() != sha256:
                continue
            data = self._loads(candidate, raw)
            obj, _ = self._validate_payload(model, rel, data)
            self._record_artifact_root(rel, obj)
            return obj
        raise ProvenanceRefused(f'{rel} 没有完整 SHA 匹配的冻结基线或历史轮次')

    def _read_own_validated(
        self,
        model: type[M],
        rel: str,
        *,
        require_current: bool,
        replaced_input_keys: Iterable[str],
    ) -> tuple[M, Any, bytes]:
        """``read_own*`` 的唯一字节、解码、schema、根与 currentness 入口。"""
        obj, payload, raw = self._validated_read(model, rel)
        self._record_artifact_root(rel, obj)
        if require_current:
            self._refuse_stale_upstream(
                rel, replaced_input_keys=replaced_input_keys)
        return obj, payload, raw

    def _validated_read(
        self, model: type[M], rel: str,
    ) -> tuple[M, Any, bytes]:
        """Decode/validate one independent object from sealed snapshot bytes.

        Production contexts never use this memo.  The readonly cache deliberately
        keeps only immutable raw bytes, not a mutable model/payload prototype.  A
        hit therefore parses and validates again: every caller receives fresh
        containers without paying Python's much slower recursive ``deepcopy`` over
        the same large artifact graph.
        """
        key = paths.check_rel_deep(rel)
        memo = _memo_for_context(self.ctx)
        cache_key = (model, key)
        if memo is not None and cache_key in memo.validated:
            raw = memo.validated[cache_key]
        else:
            raw = self._read_bytes(key)
        data = self._loads(key, raw)
        obj, payload = self._validate_payload(model, key, data)
        if memo is not None:
            memo.validated[cache_key] = raw
        return obj, payload, raw

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
        """读一份**字节形态**产物（`clean_text.txt` / 成书）。记边。

        ⚠ **按字节读再自己解码**，不用文本模式：文本模式会把 `\\r\\n` 悄悄翻成 `\\n`，
        于是「盘上那份带 CRLF」这件事在读的一侧**看不见**，
        而写的一侧刚刚为它立了一道闸（`write_text`）。两侧口径必须一致。

        ⚠ **先解码，再记边。** 反过来的话，一份解不出来的文件会在本次跑的来源账里
        留下一条边——那条边说的是「我读过它」，而我们其实一个字都没读到。
        """
        key = paths.input_key(rel)
        raw = self._read_bytes(rel)
        text = self._decode(rel, raw)
        self.ctx.record_edge(key, InputEdge(
            file=prov.fingerprint_bytes(raw),
            consumed=self._consumed_fingerprint(self.ctx.node, key, text),
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
                f'⚠ 两份字节形态产物（`clean_text.txt` / 成书）盘上一律 UTF-8；'
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

    def _require_workflow_write(self) -> None:
        """所有 producer 的写准入；外层事务已经核验时不重复进入。"""
        if not self._history_transactions:
            self.ctx.require_current_workflow_write()

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
        self._require_workflow_write()
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

    def prospective_sha256(self, rel: str, artifact: BaseModel) -> str:
        """计算这份 typed artifact 经正式写盘口后将得到的完整文件 SHA-256。

        人工改稿两相账必须在写章前记录 expected result，供进程在 canonical 已落、
        committed 尚未追加时安全补账。摘要必须来自 store 的唯一渲染口，不能由
        调用方重拼 JSON；同时先做 round-trip，prepared 不得承诺一份写不回的结果。
        """
        rel = paths.check_rel_deep(rel)
        data = self._render(rel, artifact)
        self._verify_round_trip(rel, type(artifact), data)
        return hashlib.sha256(data).hexdigest()

    def prospective_sparse_sha256(self, rel: str, artifact: BaseModel) -> str:
        """计算 registered sparse input 经唯一写盘口得到的完整文件 SHA。"""
        rel = paths.check_rel_deep(rel)
        if provenance_field(type(artifact)) is not None:
            raise ProvenanceRefused(
                f'{rel} 带 provenance；不能按 sparse input 预演')
        data = self._render(rel, artifact, exclude_unset=True)
        self._verify_round_trip(rel, type(artifact), data)
        return hashlib.sha256(data).hexdigest()

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
        return self._write_formal(rel, artifact, retire_invalid=True)

    def write_sparse_input(self, rel: str, artifact: BaseModel) -> Path:
        """原子写一份字段出现性有语义的注册人工输入，不冒充正式 producer。

        ``GlobalEdit`` 一类 patch model 用 ``model_fields_set`` 区分 absent 与显式
        ``null``；普通正式产物渲染必须保留完整字段，不能全仓改成
        ``exclude_unset``。这条窄口只接受不带 provenance 的输入模型，并在落盘前
        走同一注册表 envelope 与 round-trip 校验。
        """
        self._require_workflow_write()
        key = paths.check_rel(rel)
        if paths.is_invalid(key):
            raise PathRefused(f'{key} 是 `.INVALID`，不能作为 sparse input 写入')
        if provenance_field(type(artifact)) is not None:
            raise ProvenanceRefused(
                f'{key} 带 provenance；正式 producer 产物不能走 sparse input 写口')
        data = self._render(key, artifact, exclude_unset=True)
        self._verify_round_trip(key, type(artifact), data)
        target = self.path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._replace_atomically(target, data)
        return target

    def refresh_formal(self, rel: str, artifact: BaseModel) -> Path:
        """只刷新一份**已经存在**的正式产物，不宣称它取代同名 ``.INVALID``。

        这条窄路只服务于 ``--recheck`` 一类确定性派生字段回写：正式稿正文没有产生新轮，
        而 live INVALID 仍是一份尚待 ``--revalidate`` 的独立候选。若复用 :meth:`write`，
        后者会按“新正式稿已经取代问题稿”的正确常规语义退休 INVALID，反而把恢复路藏进
        ``_rounds``。目标正式稿不存在时关闭式拒绝，避免凭这条路创造一对新的
        formal + INVALID 并存状态。
        """
        key = paths.check_rel_deep(rel)
        if paths.is_invalid(key):
            raise PathRefused(
                f'{key} 是 `.INVALID` 形态，不走 `refresh_formal`。'
                f'被闸门拦下的稿走 `write_invalid`')
        current, _raw = self.read_own(type(artifact), key, require_current=True)
        current_src = self._provenance_of(current)
        refreshed_src = self._provenance_of(artifact)
        if (current_src is None or refreshed_src is None
                or current_src.to_disk() != refreshed_src.to_disk()):
            raise ProvenanceRefused(
                f'{key} 的 refresh 必须逐字段继承既有正式稿来源账；'
                '刷新派生字段不能宣称一次新的生成来源')
        return self._write_formal(
            key, artifact, retire_invalid=False, require_existing=True)

    def _write_formal(
        self,
        rel: str,
        artifact: BaseModel,
        *,
        retire_invalid: bool,
        require_existing: bool = False,
    ) -> Path:
        """正式路径的唯一原子替换实现；公开入口决定是否终结 INVALID。"""
        self._require_workflow_write()
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
        if require_existing and previous is None:
            raise UpstreamMissing(
                f'{self.ctx.node}: 没有既有正式产物 {rel} 可刷新；'
                '首次落正式稿必须走 write')
        replacement_attempted = False
        retired: Path | None = None
        try:
            replacement_attempted = True
            self._replace_atomically(p, data)
            self._assert_self_fresh(rel, artifact)
            # 只有正式路径已经通过 freshness 后置条件，问题稿才算真正被取代。
            if retire_invalid:
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
        _final, data, locator = self._prepare_invalid(rel, artifact, gates_skipped=gates_skipped)
        baseline = self.path(self._revalidation_baseline_rel(inv_rel, locator))
        p = self.path(inv_rel)
        with self.history_transaction(inv_rel):
            if self.exists(inv_rel):
                self.archive_round(inv_rel)
            self._write_immutable(baseline, data)
            os.makedirs(_filesystem_path(p.parent), exist_ok=True)
            self._replace_atomically(p, data)
        return p

    def prospective_invalid_sha256(self, rel: str, artifact: BaseModel, *,
                                   gates_skipped: list[str]) -> str:
        """冻结将落 INVALID 的准确字节身份；与真实写口共用唯一渲染算法。"""
        _final, data, _locator = self._prepare_invalid(rel, artifact, gates_skipped=gates_skipped)
        return hashlib.sha256(data).hexdigest()

    def _prepare_invalid(self, rel: str, artifact: M, *,
                         gates_skipped: list[str]) -> tuple[M, bytes, str]:
        inv_rel = paths.invalid_of(paths.check_rel_deep(rel))
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

        final, _payload = self._validate_payload(type(artifact), inv_rel, self._loads(inv_rel, data))
        return final, data, locator

    def write_text(self, rel: str, text: str) -> Path:
        """落一份**字节形态**产物。**显式按字节写 `\\n`，不带 BOM。**

        ⚠ 盘上两份字节形态产物（`clean_text.txt` / 成书）**全是 LF-only**，
        而 Windows 上 `open(p, 'w')` 默认吐 CRLF——同一份代码在两台机器上产出两种字节，
        而本项目有一条逐字节等价的验收线。

        ⚠⚠ **拿到 `\\r` 一律拒收，不静默归一。** 归一是**内容决策**，属产地
        （它要为「我改了什么」负责，可能还要记账）；写盘口只保证**平台不掺东西**。
        替产地做一次它自己没记账的改写，正是本项目要消灭的那种「安静地改了点什么」。
        """
        self._require_workflow_write()
        rel = paths.check_rel_deep(rel)
        if '\r' in text:
            n = text.count('\r')
            raise ByteFormRefused(
                f'{rel}: 文本里有 {n} 个 `\\r`。两份字节形态产物盘上全是 LF-only，'
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
        _artifact, _baseline, _src, audit = self._revalidation_material(
            rel, model, actor=actor, reason=reason)
        return audit

    def revalidation_baseline(self, rel: str, model: type[M], *,
                              actor: str, reason: str) -> M:
        """返回并核验创建 live INVALID 时的不可变强类型主体；全程只读。

        Stage-specific producer 可用它关闭那些不允许人工改写的任务身份字段；不能只看
        live INVALID，因为后者正是允许人编辑、随后再走 ``--revalidate`` 的候选。
        """
        _artifact, baseline, _src, _audit = self._revalidation_material(
            rel, model, actor=actor, reason=reason)
        return baseline

    def prepared_invalid_promotion(self, rel: str, model: type[M], *,
                                   actor: str, reason: str,
                                   artifact_override: M | None = None) -> M:
        """只读构造 ``promote_invalid`` 将落下的 exact typed 正式目标。

        多产物 producer 先把这份目标及其 prospective SHA 写进 prepared journal，
        再逐件收敛 live companion；不能为了取得 promoted provenance 先碰正式文件。
        """
        official_rel = paths.check_rel_deep(rel)
        live_artifact, _baseline_artifact, src, audit = self._revalidation_material(
            official_rel, model, actor=actor, reason=reason)
        artifact = artifact_override if artifact_override is not None else live_artifact
        if not isinstance(artifact, model):
            raise ProvenanceRefused(
                f'{official_rel} 的 artifact_override 必须是 {model.__name__}，'
                f'实得 {type(artifact).__name__}；一个字节都没动')
        promoted_source = src.promoted(code=self.ctx.code, audit=audit)
        derived_fields = _REVALIDATION_DERIVED_PROVENANCE_FIELDS.get(
            official_rel, frozenset())
        if derived_fields:
            override_source = self._provenance_of(artifact)
            if override_source is None:
                raise ProvenanceRefused(
                    f'{official_rel} 的确定性提升候选缺 provenance')
            missing = sorted(
                field for field in derived_fields
                if field not in type(override_source).model_fields
                or field not in type(promoted_source).model_fields)
            if missing:
                raise ProvenanceRefused(
                    f'{official_rel} 提升派生账字段与当前 schema 漂移：{missing}')
            promoted_source = promoted_source.model_copy(update={
                field: copy.deepcopy(getattr(override_source, field))
                for field in derived_fields
            })
        data = self._render(
            official_rel, artifact, prov_override=promoted_source)
        self._verify_round_trip(official_rel, model, data)
        prepared, _payload = self._validate_payload(
            model, official_rel, self._loads(official_rel, data))
        return prepared

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

        ③ **已有的当前正式稿也要归档，不许被覆盖掉。** 落正式稿走的是原子替换，
           而原子替换是**无条件**的：盘上那份**花过钱**的正式稿原地消失，
           `_rounds/` 里连一份副本都没有。原来只归档 `.INVALID` 那一条历史。
           对 stage5 这直接违反「上一轮正式稿进入 `_rounds`」；对别的层，
           它违反的是本模块自己那句「**归档是出事之后唯一能回去的那一份**」。
           唯一例外是注册表已显式登记的低版本 shape-break 正式稿：它不是当前业务
           轮次，按版本契约应由 producer 重建且不得污染 current `_rounds`。

        ``artifact_override`` 只承载调用方按 live INVALID 重建的确定性派生字段。
        人工改动审计仍严格比较「不可变基线 vs live INVALID」；最终来源账的
        prompts/model/inputs/usage/round 强制使用不可变基线的
        ``src.promoted(audit=...)``。只有模块级白名单登记的 Stage2 确定性归一账
        可从已验证的 override 带入；live 中自报的生成来源仍不被信任。
        """
        self._require_workflow_write()
        official_rel = paths.check_rel_deep(rel)
        inv_rel = paths.invalid_of(official_rel)
        if not self.exists(inv_rel):
            raise UpstreamMissing(f'{self.ctx.node}: 没有 {inv_rel} 可提升')

        spec = artifact_spec(official_rel)
        formal_version = self.schema_version_of(official_rel)
        if (spec is not None and formal_version is not None
                and formal_version > spec.current_version):
            raise ArtifactUnreadable(
                f'{official_rel} 是未来 schema v{formal_version}，当前仅 v{spec.current_version}；'
                '拒绝用当前 INVALID 覆盖')
        discard_shape_break_formal = bool(
            spec is not None
            and formal_version is not None
            and formal_version < spec.current_version
        )

        artifact = self.prepared_invalid_promotion(
            official_rel, model, actor=actor, reason=reason,
            artifact_override=artifact_override)

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
        data = self._render(official_rel, artifact)
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
            if (official_before is not None and official_before != data
                    and not discard_shape_break_formal):
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
        self._require_workflow_write()
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
            self._deduplicate_archived_revalidation(rel, dest)
        return dest

    def _deduplicate_archived_revalidation(self, rel: str, archived: Path) -> None:
        """Compact a byte-identical INVALID baseline into a verified round ref.

        A live ``.INVALID`` needs its immutable ``_revalidation`` baseline so a
        later human edit can be audited.  Once that live file has been moved to
        its numbered ``_rounds`` path, an *unedited* INVALID used to leave two
        complete copies of the same bytes.  The numbered archive is the public
        recovery surface, so keep it byte-for-byte and replace only the exact,
        locator-bound duplicate baseline with a small content-addressed ref.

        Existing baselines remain a read-only compatible input to
        ``_revalidation_material``.  Edited INVALIDs deliberately keep both
        files because their creation baseline and archived candidate are
        different evidence.  No link, directory sweep, or guessed filename is
        involved: the ref binds the exact numbered path and raw SHA-256.  If a
        crash recovery later moves that round back to live INVALID, the resolver
        accepts live bytes only while the same SHA still matches.  Failure to
        compact merely retains the safe duplicate and must not fail the archive.
        """
        if not paths.is_invalid(rel):
            return
        try:
            archived_raw = _read_file_bytes(archived)
            encoded = self._loads(rel, archived_raw)
            payload = decode_artifact(rel, encoded).payload
            if not isinstance(payload, dict):
                return
            source = payload.get(PROVENANCE_KEY)
            locator = source.get('invalid_baseline_sha256') \
                if isinstance(source, dict) else None
            if not isinstance(locator, str):
                return
            baseline = self.path(self._revalidation_baseline_rel(rel, locator))
            if (not _is_file(baseline)
                    or _read_file_bytes(baseline) != archived_raw):
                return
            self._track_history_path(baseline)
            reference = json.dumps({
                '_snapshot_ref_version': REVALIDATION_REFERENCE_VERSION,
                'raw_sha256': hashlib.sha256(archived_raw).hexdigest(),
                'round_rel': archived.relative_to(
                    self.ctx.novel_dir).as_posix(),
            }, ensure_ascii=False, sort_keys=True,
                separators=(',', ':')).encode('utf-8') + b'\n'
            self._replace_atomically(baseline, reference)
        except (ArtifactUnreadable, ArtifactVersionError, OSError,
                ProvenanceRefused):
            # Deduplication is storage hygiene, never an excuse to make the
            # already-landed numbered recovery archive unavailable.
            return

    def retire_bytes(self, rel: str, destination: str) -> Path:
        """Move live bytes to an immutable, content-addressed archive idempotently.

        An already-existing destination is accepted only when its bytes are exactly
        identical. This supports interrupted/repeated retirement of derived products.
        """
        self._require_workflow_write()
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
            raw_data = json.loads(_read_file_bytes(p).decode('utf-8'))
            data = decode_artifact(rel, raw_data).payload
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ArtifactVersionError):
            return None
        if not isinstance(data, dict):
            return None
        n = ((data.get(PROVENANCE_KEY) or {}) or {}).get('round')
        return int(n) if isinstance(n, int) and n >= 1 else None

    # ── 新鲜度 ────────────────────────────────────────────────────────────

    def freshness(self, rel: str) -> FreshnessReport:
        """Return currentness, memoized only inside a sealed readonly snapshot."""
        rel = paths.check_rel_deep(rel)
        memo = _memo_for_context(self.ctx)
        key = (self.ctx.node, rel)
        if memo is not None and key in memo.freshness:
            return copy.deepcopy(memo.freshness[key])
        report = self._freshness_uncached(rel)
        if memo is not None:
            memo.freshness[key] = copy.deepcopy(report)
        return report

    def _freshness_uncached(self, rel: str) -> FreshnessReport:
        """这份产物是拿**盘上现在这几份**上游产的吗？算法在 `novel/prov.py::freshness`。

        ⚠ **读原始 dict，不过模型。** 一份陈旧产物很可能已经不合当前 schema
        （新契约裁了一批字段之后，原型产的 3/3 本全部加载失败），
        而「它是不是陈旧的」恰恰是这时候最该答得出来的问题。
        让新鲜度依赖校验通过，等于在最需要它的那一刻把它关掉。
        """
        p = self.path(rel)
        if not _is_file(p):
            # 称名 HARD 时 producer **有意不发布** projection；这不是普通 missing，
            # 而是一条必须传到 state/queue/consumer 的前置闸。其余缺失产物仍按
            # 既有契约抛 ``UpstreamMissing``，不把不存在伪装成可判 freshness。
            if rel == STAGE2_BLUEPRINT_CHARACTER_PROJECTION:
                deterministic = self._deterministic_freshness(rel)
                if (deterministic is not None
                        and deterministic.reason == 'global_naming_blocked'):
                    return deterministic
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
        report = self.freshness_of_inputs(rel, recorded)
        if isinstance(data, dict) and (data.get(PROVENANCE_KEY) or {}).get('shared_analysis_source'):
            report = self._shared_analysis_freshness(data, report)
        if canonical_rel(rel) == STAGE2_GLOBAL_GROUNDING and isinstance(data, dict):
            return self._knowledge_character_freshness(data, report)
        if canonical_rel(rel) == STAGE4_EVIDENCE_BINDING:
            return self._stage4_binding_input_freshness(data, report)
        if canonical_rel(rel) == STAGE4_PLAN_CHAPTERS and isinstance(data, dict):
            return self._stage4_plan_phase_freshness(data, report)
        return report


    def _shared_analysis_freshness(self, data: dict, ordinary: FreshnessReport) -> FreshnessReport:
        """共享来源核验不可变回执，不展开可变人物文件的全对象依赖。"""
        if ordinary.state != 'fresh':
            return ordinary
        from contracts.schema.common import SharedAnalysisSource
        try:
            shared = SharedAnalysisSource.model_validate(data[PROVENANCE_KEY]['shared_analysis_source'])
        except (ValidationError, KeyError):
            return FreshnessReport('unknown', 'consumed_unresolved', ordinary.edges)
        actual = self.full_sha256_or_none(STAGE2_ANALYZE_RECEIPT)
        if actual == shared.receipt_sha256:
            return ordinary
        edge = prov.EdgeVerdict(key=STAGE2_ANALYZE_RECEIPT,
            state='unknown', reason='ghost' if actual is None else 'consumed_unresolved',
            recorded_file=shared.receipt_sha256, now_file=actual,
            recorded_consumed=None, now_consumed=None)
        return FreshnessReport(edge.state, edge.reason, (*ordinary.edges, edge))

    def _knowledge_character_freshness(self, data: dict, ordinary: FreshnessReport) -> FreshnessReport:
        """Global 编译依赖按必要身份投影复算；生成来源单独证明。"""
        if ordinary.state != 'fresh':
            return ordinary
        from contracts.schema.characters import CharacterRegistry
        from novel.stages.stage2.grounding import knowledge_reference_sha256
        recorded = data.get('characters_reference_sha256')
        try:
            if self.exists(paths.invalid_of(STAGE2_CHARACTERS)):
                raise ProvenanceRefused('当前人物仍有 INVALID')
            registry = self.read_own(CharacterRegistry, STAGE2_CHARACTERS)[0]
            current = knowledge_reference_sha256(registry)
        except (ArtifactUnreadable, UpstreamMissing, ProvenanceRefused, ValueError):
            return FreshnessReport('unknown', 'consumed_unresolved', ordinary.edges)
        if recorded == current:
            return ordinary
        edge = prov.EdgeVerdict(key=STAGE2_CHARACTERS, state='stale', reason='consumed_changed',
            recorded_file=None, now_file=self._file_fp_of_key(STAGE2_CHARACTERS),
            recorded_consumed=recorded, now_consumed=current)
        return FreshnessReport('stale', 'consumed_changed', (*ordinary.edges, edge))

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

    @staticmethod
    def _stage4_binding_input_freshness(
        binding: object, ordinary: FreshnessReport,
    ) -> FreshnessReport:
        """仅在真实输入可证 fresh 后附加登记的章级 B 输入契约尺。

        上游已真实 consumed_changed 时沿原 REDO 权威重建；那份授权不是
        未知 receipt 带来的。只有下方 known_obsolete 才进入本次专用恢复。
        """
        if ordinary.state != 'fresh':
            return ordinary
        from novel.stages.stage4.binding_input_contract import (
            inspect_binding_input_contract,
        )
        verdict = inspect_binding_input_contract(binding)
        if verdict.state == 'known_obsolete':
            return FreshnessReport(
                'stale', 'binding_input_contract_obsolete', ordinary.edges)
        if verdict.state == 'unproven':
            return FreshnessReport(
                'unknown', 'binding_input_contract_unproven', ordinary.edges)
        return ordinary

    def _stage4_plan_phase_freshness(
        self, plan: dict[str, Any], ordinary: FreshnessReport,
    ) -> FreshnessReport:
        """把 A/B 完整字节绑定叠加到普通业务投影 freshness。

        普通边仍保留 ``recorded_consumed/now_consumed``，但 ``compiled`` 计划不能因
        业务投影相同就忽略版本化文件的字节变化。完整 SHA 不符时，用同一个真实产物
        key 覆盖该边的文件判定；human 计划没有 A/B 编译身份，原样返回普通报告。
        """
        # 动态 import 避免 store 初始化反向牵入整个 Stage4 producer；纯比较函数本身
        # 不读盘，check 与写盘后置条件也调用同一份实现。
        from novel.stages.stage4.phase_binding import verify_plan_phase_artifacts

        links = verify_plan_phase_artifacts(
            plan, sha256_now=self.full_sha256_or_none)
        if not links:
            return ordinary

        edges = list(ordinary.edges)
        positions = {edge.key: index for index, edge in enumerate(edges)}
        for link in links:
            if link.state == 'match':
                continue
            current = edges[positions[link.key]] if link.key in positions else None
            # compiled 计划额外绑定 A/B 完整字节，但不能把普通 freshness 已经
            # 证明的 declared projection 变化洗成手工 ``file_changed``：那会把
            # 本可由 producer 0 歧义重做的 consumed_changed 错送人工恢复。只有
            # 普通投影仍 match、而未消费字节单独变化时，才是真 file_changed。
            reason = (
                'ghost' if link.state == 'ghost'
                else 'consumed_changed'
                if current is not None and current.reason == 'consumed_changed'
                else 'file_changed'
            )
            state = 'unknown' if reason == 'ghost' else 'stale'
            exact = prov.EdgeVerdict(
                key=link.key,
                state=state,
                reason=reason,
                recorded_file=link.expected_sha256,
                now_file=link.now_sha256,
                recorded_consumed=(current.recorded_consumed if current else None),
                now_consumed=(current.now_consumed if current else None),
            )
            if current is None:
                positions[link.key] = len(edges)
                edges.append(exact)
            else:
                edges[positions[link.key]] = exact
        report = self._content_report(tuple(edges)) if edges else ordinary
        if report.state != 'fresh' or any(link.state != 'match' for link in links):
            return report
        # 完整 A/B SHA 已对证；Plan 只能继承这份真实 B 的输入契约，
        # 不能对另一版 B 的 receipt 作推断。human Plan 上方 links=() 已返回。
        binding, _raw = self.read_own(EvidenceBinding, STAGE4_EVIDENCE_BINDING)
        return self._stage4_binding_input_freshness(binding, report)

    def _deterministic_freshness(self, rel: str) -> FreshnessReport | None:
        """没有通用来源块的产物，用其既有确定性指纹回答同一个问题。

        stage0 的权威格 `revisions.json` 用根部原文身份；唯一物化正文从 current revision
        取 clean_text；Stage1 两份材料与当前 clean_text 的确定性复扫结果逐字对账；
        成书（`stage7/<书名>_rewriting.txt`）的身份由
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
        if rel in (STAGE1_MAIN_TEXT, STAGE1_HOOK_COPY):
            return self._stage1_freshness(rel)
        if rel == STAGE2_BLUEPRINT_CHARACTER_PROJECTION:
            return self._blueprint_character_projection_freshness()
        if rel == STAGE2_GLOBAL_REVIEW:
            return self._global_review_freshness()
        # ⚠ 成书的 rel 带书名 ⇒ **不能与 `STAGE7_BOOK` 直接比**（那是模板）。
        #   拿模板比出来的恒是 False，而后果是 `_deterministic_freshness` 返回 None
        #   ⇒ 成书的 freshness 变成「没有来源账」，那道对账**静默失效**。
        if rel == paths.book_txt_rel(self.ctx.novel_dir):
            return self._book_freshness()
        return None

    def _blueprint_character_projection_freshness(self) -> FreshnessReport:
        """称名投影只来自人物 producer 与 MainText；知识、关系和规则没有该输入边。

        live 人物 INVALID 优先，坏载体保持 unknown。语境或静态身份只有实际改变
        逐章可选称名集合时才使投影 stale，不能按整份人物文件 SHA 扩大失效。
        """
        recorded = self._freshness_payload(STAGE2_BLUEPRINT_CHARACTER_PROJECTION)
        characters_invalid = paths.invalid_of(STAGE2_CHARACTERS)
        characters_rel = (
            characters_invalid if self.exists(characters_invalid)
            else STAGE2_CHARACTERS
        )
        characters_loaded, characters_payload = self._projection_payload(characters_rel)
        main_loaded, main_payload = self._projection_payload(STAGE1_MAIN_TEXT)
        if not characters_loaded or not main_loaded:
            if recorded is None:
                return FreshnessReport('unknown', 'no_provenance')
            missing = characters_rel if not characters_loaded else STAGE1_MAIN_TEXT
            edge = prov.EdgeVerdict(
                key=missing, state='unknown', reason='ghost',
                recorded_file=None, now_file=None,
                recorded_consumed=prov.projection_fingerprint(recorded),
                now_consumed=None,
            )
            return FreshnessReport('unknown', 'ghost', (edge,))

        try:
            # ``decode_artifact`` 只核版本信封，不替业务模型证明载体完整。
            # Blueprint 可以从 ``.INVALID`` 提前启动的前提是它仍是一份完整、
            # schema-valid 的 GlobalGrounding；若人工改坏 role/relation 等未消费字段，
            # 不能仅因 ``entities.characters`` 还能读就把问题稿称为可用底座。
            from contracts.schema.stage1 import MainText
            from contracts.schema.characters import CharacterRegistry

            grounding = CharacterRegistry.model_validate(characters_payload)
            main_text = MainText.model_validate(main_payload)
            grounding_payload = grounding.model_dump(by_alias=True, mode='json')
            characters = grounding_payload['characters']
            sections = [
                section.model_dump(by_alias=True, mode='json')
                for section in main_text.root
            ]
            # Global 的非称名 HARD 可以与 Blueprint 并行；但重名、全角色无据、
            # 代词称谓等命中会让当前称名闭集本身不稳定。此时即使投影字节恰好
            # 与上一版相同，也不能把它叫作 fresh 并提前花一次 Blueprint 的钱。
            from novel.stages.stage2 import checks as stage2_checks

            naming_blockers = (
                stage2_checks.blueprint_projection_blockers(
                    global_g=grounding_payload,
                    main_text=sections,
                )
                # 这道闸只服务“从 schema-valid 问题稿提前并行 Blueprint”。
                # formal Global 按正式投影逐值比较：变化就 stale，不把已有
                # 正式产物的普通漂移泛化成另一种人工等待态。
                if characters_rel == characters_invalid else ()
            )
            if naming_blockers:
                edge = prov.EdgeVerdict(
                    key=characters_rel,
                    state='unknown',
                    reason='global_naming_blocked',
                    recorded_file=None,
                    now_file=self._file_fp_of_key(characters_rel),
                    recorded_consumed=(
                        prov.projection_fingerprint(recorded)
                        if recorded is not None else None
                    ),
                    now_consumed=None,
                )
                return FreshnessReport(
                    'unknown', 'global_naming_blocked', (edge,))
            if recorded is None:
                return FreshnessReport('unknown', 'no_provenance')
            from novel.stages.stage2.character_refs import (
                materialize_blueprint_character_projection,
            )
            expected = materialize_blueprint_character_projection(
                characters, sections).model_dump(by_alias=True, mode='json')
        except (AttributeError, KeyError, IndexError, TypeError, ValueError,
                ValidationError):
            if recorded is None:
                return FreshnessReport('unknown', 'no_provenance')
            edge = prov.EdgeVerdict(
                key=characters_rel, state='unknown', reason='consumed_unresolved',
                recorded_file=None, now_file=self._file_fp_of_key(characters_rel),
                recorded_consumed=prov.projection_fingerprint(recorded),
                now_consumed=None,
            )
            return FreshnessReport('unknown', 'consumed_unresolved', (edge,))

        recorded_fp = prov.projection_fingerprint(recorded)
        expected_fp = prov.projection_fingerprint(expected)
        reason = 'match' if recorded_fp == expected_fp else 'consumed_changed'
        state = 'fresh' if reason == 'match' else 'stale'
        edges = tuple(
            prov.EdgeVerdict(
                key=key, state=state, reason=reason,
                recorded_file=None, now_file=self._file_fp_of_key(key),
                recorded_consumed=recorded_fp, now_consumed=expected_fp,
            )
            for key in (characters_rel, STAGE1_MAIN_TEXT)
        )
        return FreshnessReport(state, reason, edges)

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

    def _stage1_freshness(self, rel: str) -> FreshnessReport:
        """Stage1 材料必须逐字等于当前 clean_text 的确定性投影。

        旧实现只核 ``partition_report.input_sha1``，所以 main/hook 被单独篡改后仍会
        显示 fresh。现在 freshness 与复检、页面、审批共用 Stage1 唯一审计口。
        """
        from contracts.schema.stage0 import Stage0Revisions
        from contracts.schema.stage1 import HookCopy, MainText
        from novel.stages.stage0 import revisions as stage0_revisions
        from novel.stages.stage1.audit import audit_surface

        try:
            revisions, _ = self.read_own(Stage0Revisions, STAGE0_REVISIONS)
            stage0_revisions.verify_chain(revisions)
            stage0 = stage0_revisions.snapshot_of(revisions)
            clean_text = self.read_own_text(STAGE0_CLEAN_TEXT)
            main_text = self.read_own(MainText, rel)[0] if rel == STAGE1_MAIN_TEXT else None
            hook_copy = self.read_own(HookCopy, rel)[0] if rel == STAGE1_HOOK_COPY else None
        except (UpstreamMissing, ArtifactUnreadable, ValidationError, ValueError):
            return FreshnessReport('unknown', 'no_provenance')

        audit = audit_surface(
            clean_text=clean_text,
            stage0=stage0,
            main_text=main_text,
            hook_copy=hook_copy,
        )
        actual_clean_sha1 = prov.content_sha1(clean_text.encode('utf-8'))
        if not audit.input_current:
            edge = self._content_edge(
                STAGE0_CLEAN_TEXT, stage0.clean_sha1, actual_clean_sha1)
            return FreshnessReport(edge.state, edge.reason, (edge,))

        if rel == STAGE1_MAIN_TEXT:
            assert main_text is not None
            current = bool(audit.main_current)
            actual = [section.model_dump() for section in main_text.root]
            expected: object = list(audit.expected_main)
        else:
            assert hook_copy is not None
            current = bool(audit.hook_current)
            actual = hook_copy.model_dump()
            expected = audit.expected_hook
        reason = 'match' if current else 'consumed_changed'
        state = 'fresh' if current else 'stale'
        edge = prov.EdgeVerdict(
            key=STAGE0_CLEAN_TEXT,
            state=state,
            reason=reason,
            recorded_file=stage0.clean_sha1,
            now_file=actual_clean_sha1,
            recorded_consumed=prov.projection_fingerprint(actual),
            now_consumed=prov.projection_fingerprint(expected),
        )
        return FreshnessReport(state, reason, (edge,))

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
        from contracts.schema.characters import CharacterRegistry
        from novel.quality.global_review import build_review, review_status
        try:
            review = GlobalReview.model_validate(review_payload)
        except ValidationError as exc:
            raise ArtifactUnreadable(
                f'{STAGE2_GLOBAL_REVIEW} 不合 GlobalReview 契约，判不了新鲜度：{exc}') from exc

        from novel.stages.stage2 import grounding as grounding_io
        try:
            source = grounding_io.read_own(self)[1]
        except (ProvenanceRefused, UpstreamMissing, UpstreamStale):
            # 人物已订正而知识尚待 producer 重绑时，不存在可审阅的当前组合视图。
            # 只读 state/API 保留 unknown，不能崩溃或临时拼接后追认旧决定。
            present = self.exists(STAGE2_CHARACTERS)
            reason = 'consumed_unresolved' if present else 'ghost'
            edge = prov.EdgeVerdict(
                key=STAGE2_CHARACTERS, state='unknown', reason=reason,
                recorded_file=None, now_file=self._file_fp_of_key(STAGE2_CHARACTERS),
                recorded_consumed=None, now_consumed=None)
            return FreshnessReport('unknown', reason, (edge,))
        character_source = self.read_own(CharacterRegistry, STAGE2_CHARACTERS)[1]
        expected_subject, exact_current, _approved = review_status(source, review, characters=character_source)
        # Approval completeness is a gate/UI fact, not freshness.  Pending/FAIL/
        # UNVER decisions remain fresh as long as they describe the exact surface.
        expected = build_review(source, characters=character_source)

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
            raw = json.loads(_read_file_bytes(p).decode('utf-8'))
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
            return prov.content_sha1(_read_file_bytes(p))
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
            return prov.fingerprint_bytes(_read_file_bytes(p))
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

    def _revalidation_snapshot_bytes(
            self, snapshot: Path, *, fallback: bytes | None = None,
    ) -> bytes:
        """Resolve a legacy full baseline or a v1 content-addressed round ref."""
        raw = _read_file_bytes(snapshot)
        try:
            reference = json.loads(raw.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return raw
        if (not isinstance(reference, dict)
                or '_snapshot_ref_version' not in reference):
            return raw
        if (set(reference) != {
                '_snapshot_ref_version', 'raw_sha256', 'round_rel'}
                or reference.get('_snapshot_ref_version')
                != REVALIDATION_REFERENCE_VERSION):
            raise ProvenanceRefused(
                f'重验证基线引用版本或字段不闭合：{snapshot}')
        round_rel = paths.check_rel_deep(str(reference.get('round_rel') or ''))
        expected_sha = str(reference.get('raw_sha256') or '')
        if (len(expected_sha) != 64
                or any(ch not in '0123456789abcdef' for ch in expected_sha)
                or '/_rounds/' not in f'/{round_rel}'):
            raise ProvenanceRefused(f'重验证基线引用身份不合法：{snapshot}')
        target = self.path(round_rel)
        archived = _read_file_bytes(target) if _is_file(target) else None
        for candidate in (archived, fallback):
            if (candidate is not None
                    and hashlib.sha256(candidate).hexdigest() == expected_sha):
                return candidate
        raise ProvenanceRefused(
            f'重验证基线引用的 numbered round 缺失/漂移，'
            f'且 live 候选不匹配：{round_rel}')

    def _write_immutable(self, destination: Path, data: bytes) -> None:
        """Create a content-addressed audit snapshot; never replace history."""
        self._require_workflow_write()
        self._track_history_path(destination)
        if os.path.exists(_filesystem_path(destination)):
            try:
                same = (_is_file(destination)
                        and self._revalidation_snapshot_bytes(
                            destination, fallback=data) == data)
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
                               ) -> tuple[M, M, Provenance, RevalidationAudit]:
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
            baseline_raw = self._revalidation_snapshot_bytes(
                baseline_path, fallback=current_raw)
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
        audit = build_revalidation_audit(
            actor=approval.actor,
            reason=approval.reason,
            baseline_locator_sha256=locator,
            baseline_raw_sha256=baseline_raw_sha,
            baseline_payload=baseline_business,
            candidate_raw_sha256=candidate_raw_sha,
            candidate_payload=candidate_business,
        )
        # live INVALID 的业务主体允许按 producer 规则编辑，但它的 provenance 不是
        # 人工输入面。最终提升必须继承不可变创建快照，而不是信任 live 文件里一块
        # 同样可被改写的 `_provenance`。
        return artifact, baseline_artifact, baseline_src, audit

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

    def _consumed_fp_of_key(
        self,
        node: str | None,
        key: str,
    ) -> str | None:
        if node is None:
            return None
        memo = _memo_for_context(self.ctx)
        memo_key = (node, key)
        if memo is not None and memo_key in memo.consumed:
            return memo.consumed[memo_key]
        loaded, data = self._projection_payload(key)
        if not loaded:
            if memo is not None:
                memo.consumed[memo_key] = None
            return None
        return self._consumed_fingerprint(node, key, data)

    def _consumed_fingerprint(
        self, node: str, key: str, data: object,
    ) -> str | None:
        """current 投影的唯一执行口；读边与 freshness 共用。"""
        memo = _memo_for_context(self.ctx)
        memo_key = (node, key)
        if memo is not None and memo_key in memo.consumed:
            return memo.consumed[memo_key]
        related: dict[str, object] = {}
        for related_key in context_inputs_for(node, key):
            loaded, value = self._projection_payload(related_key)
            if not loaded:
                if memo is not None:
                    memo.consumed[memo_key] = None
                return None
            related[related_key] = value
        resolved, value = project_consumed(
            node, key, data, related=related)
        result = prov.projection_fingerprint(value) if resolved else None
        if memo is not None:
            memo.consumed[memo_key] = result
        return result

    def _projection_payload(self, key: str) -> tuple[bool, object]:
        """为 consumed 重算读 current 载体；不记新来源边。"""
        memo = _memo_for_context(self.ctx)
        if memo is not None and key in memo.projections:
            return copy.deepcopy(memo.projections[key])
        parts = str(key).replace(chr(92), '/').split('/')
        if not parts or any(part in ('', '.', '..') for part in parts):
            return False, None
        p = self.ctx.novel_dir.joinpath(*parts)
        try:
            raw = _read_file_bytes(p)
            if p.suffix.lower() == '.json':
                decoded_json = json.loads(raw.decode('utf-8'))
                data: object = decode_artifact(key, decoded_json).payload
            else:
                data = raw.decode('utf-8')
            result = (True, data)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError,
                AttributeError, KeyError, IndexError, TypeError, ValueError,
                ArtifactVersionError):
            result = (False, None)
        if memo is not None:
            memo.projections[key] = copy.deepcopy(result)
        return result


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
                prov_override: Provenance | None = None,
                exclude_unset: bool = False) -> bytes:
        """产物 → **将要落盘的那串字节**。过写盘口那道闸，但一个字节都不写。

        ⚠ 抽出来是给 `promote_invalid` 用的：它要在**动归档之前**知道自己将要写什么，
        才能回答「盘上那份会不会被销毁」。见那边第②步。
        """
        name = provenance_field(type(artifact))
        payload = artifact.model_dump(
            by_alias=True, mode='json', exclude_unset=exclude_unset)
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
        self._require_workflow_write()
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

    def apply_workflow_projection_transition(self, transition_id: str,
                                             prepared_sha256: str) -> Path:
        """离线迁移的唯一产物例外：按冻结输入免费重编译人物投影。

        不开放普通写闸、不迁人物/Global。迁移 owner 持有全盘写锁；这里仍核验
        prepared、pending、确切根路径、现役纯编译结果及 source/target 原字节 CAS。
        原投影字节永久留在 prepared，任一窗口均可由同一免费事务补尾。
        """
        from novel import work_units
        from novel.orchestrator.workflow_transition import (
            WorkflowTransitionRefused, projection_commit_material,
        )
        path, source, target = projection_commit_material(self, transition_id, prepared_sha256)
        current = path.read_bytes() if path.exists() else None
        if current == target:
            return path
        if current != source:
            raise WorkflowTransitionRefused('人物投影已不是冻结原字节；拒绝覆盖第三方修改')
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.transition-', dir=_filesystem_path(path.parent))
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(target)
                stream.flush()
                os.fsync(stream.fileno())
            # 写临时文件期间也可能有未遵循 owner 协议的第三方；提交前再次核验。
            if (path.read_bytes() if path.exists() else None) != source:
                raise WorkflowTransitionRefused('人物投影提交前原字节变化')
            work_units._replace_snapshot(Path(temporary), path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        if path.read_bytes() != target:
            raise WorkflowTransitionRefused('人物投影提交后读回不等于冻结目标')
        return path

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
