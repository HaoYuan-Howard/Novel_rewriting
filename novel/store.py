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
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, NamedTuple, TypeVar, cast

from pydantic import BaseModel, ValidationError

from contracts.artifacts import (
    ARTIFACT_PATHS,
    STAGE0_CLEAN_TEXT, STAGE0_REVISIONS,
    STAGE1_HOOK_COPY, STAGE1_MAIN_TEXT,
    STAGE2_ANALYZE_RECEIPT, STAGE2_BLUEPRINT_CHARACTER_PROJECTION,
    STR06_DELIVERY_MANIFEST, STR06_HOOK_RECEIPT, STR06_HOOK_TEXT,
    STR06_STAGE5_BODY_RECEIPT, artifact_rel,
)
from contracts.invariants import NODES
from contracts.schema import (
    ARTIFACT_SPECS, ArtifactVersionError, InputEdge, Provenance,
    artifact_spec, decode_artifact, encode_artifact,
)
from contracts.schema.workflow import CommitOutputRef
from novel import paths, prov
from novel.consumption import (
    canonical_rel, context_inputs_for,
    producer_node, project_consumed,
)
from novel.errors import (
    ArtifactUnreadable, ByteFormRefused, PathRefused,
    ProvenanceRefused, TerminalState, UnsealedArtifact, UpstreamMissing,
    UpstreamStale,
)
from novel.prov import FreshnessReport
from novel.run import RunContext

M = TypeVar('M', bound=BaseModel)
T = TypeVar('T')


def _rel_pattern(rel: str) -> re.Pattern[str]:
    """把产物路径模板编译成匹配式：`{nn}` = 两位章序，`{title}` = 单个路径段。"""
    parts = re.split(r'(\{nn\}|\{title\})', rel)
    regex = ''.join(r'\d{2}' if p == '{nn}' else r'[^/]+' if p == '{title}'
                    else re.escape(p) for p in parts)
    return re.compile(f'^{regex}$')


def candidate_artifact(rel: str) -> str | None:
    """按路径取已登记的产物模板：先精确匹配，再按 `{nn}`／`{title}` 模式匹配。

    未登记返回 None——调用方据此**不记来源边、不复算新鲜度、不铸来源账**，而不是
    猜一个家族落账。F8c 前本函数住在 `contracts/schema/str06/candidates.py`（读
    候选产物表）；节点表与产物表已在 F8a 激活进 `contracts/invariants.py::NODES`
    与 `contracts/artifacts.py`，本函数随之落到 repository 层，匹配规则一字未改
    （章序仍必须两位）。
    """
    normalized = str(rel).replace('\\', '/')
    if normalized in ARTIFACT_PATHS:
        return normalized
    for template in ARTIFACT_PATHS:
        if '{' in template and _rel_pattern(template).match(normalized):
            return template
    return None


def _writer_of(template: str) -> str:
    """已登记产物模板的唯一执行 writer（I-25）：`NODES` 里 `produces` 含该模板的节点。

    入参是 `candidate_artifact` 解析出的**模板**（如 `stage5/body/ch{nn}.txt`），
    不是具体章路径——writer 按模板登记，逐章产物共享同一个执行 writer。
    """
    for node_id, node in NODES.items():
        if template in node.produces:
            return node_id
    raise ProvenanceRefused(f'已登记产物无执行 writer：{template}')


def _artifact_model(rel: str | None):
    """已登记 JSON 产物的契约模型；自由形态派生记录与正文返回 None。"""
    if rel is None or rel.endswith('.txt'):
        return None
    return ARTIFACT_SPECS[rel].model if rel in ARTIFACT_SPECS else None


# ── rc12/rc13→rc14 登记迁移：仅正式模型读取口；inspect 保留原字节 ─────────
# 删除条件见 REQUIREMENTS：历史运行根全部重建或退役后删除此兼容层。
# rc17（CS-H H3）：brief／writer_book_ir 形状再升（H3 新字段全部可缺省读入），
# rc12／rc13 载荷的迁移目标直接钉 rc17。⚠ 盘上 rc14～rc16 载荷不在迁移白名单内：
# 模型字面量只收 rc17，按契约表漂移拒读（ChapterIR.display_prefix 语义已变，
# 不能把旧值当新字段缺省读入）；旧根续跑同样按候选契约表拒收。
_RC14_SITES = {
    'stage4/design/body_design.json', 'stage4/design/presentation_graph.json',
    'stage4/compiled/execution_plan.json',
}
_RC17_SITES = {
    'stage4/grounding/reconstruction_brief.json',
    'stage4/ir/writer_book_ir.json',
}
_ADDRESS_FIELDS = {'domain', 'key', 'subjects', 'holder', 'at', 'op', 'value',
                   'scope', 'knowledge_mode', 'modality', 'confirms',
                   'established_by', 'status'}


def _upgrade_rc12_payload(spec: str | None, data: dict) -> dict:
    """仅做无损、确定性迁移；丢失的认知模式/事实关联显式拒绝，交免费恢复。

    地址、读取、Hook 地址和变化使用同一 reader 归一。StateEntry 读者条目
    转为 KnowledgeEntry，并移动到相应知情容器；不修改输入或历史封存字节。
    """
    if spec not in _RC14_SITES | _RC17_SITES or data.get('contract_version') not in (
            'str06-rc12', 'str06-rc13'):
        return data
    out = copy.deepcopy(data)
    if spec == 'stage4/grounding/reconstruction_brief.json':
        if out.get('knowledge_locks') and any(
                'knowledge_changes' not in req for req in out.get('requirements') or []):
            raise ProvenanceRefused('旧 Brief 缺逐次认知分母；请免费重放 preserve 重建 Brief')
        for req in out.get('requirements') or []:
            if req.get('realize_holders'):
                raise ProvenanceRefused(
                    '旧 Brief 已丢失逐义务认知模式；请免费重放 preserve '
                    '（--check-only，必要时 --adopt-root）重建 Brief，禁止猜模式')
            req.pop('realize_holders', None)
        constraints = out.get('book_constraints')
        if isinstance(constraints, list):
            out['book_constraints'] = [
                {'constraint_id': f'wc_{i:02d}', 'statement': c} if isinstance(c, str) else c
                for i, c in enumerate(constraints, start=1)]

    def knowledge_entry(row: dict) -> dict:
        if row.get('status', 'set') != 'set' or row.get('value') is None:
            raise ProvenanceRefused('旧读者状态未建立，不能补造命题与模式；请免费重编译 WriterBookIR')
        return {'holder': 'reader', 'key': row['key'], 'subjects': [],
                'proposition': row['value'],
                'established_by': row['established_by'],
                'status': row.get('status', 'set'), 'mode': 'known'}

    def walk(value, path: str = ''):
        if isinstance(value, list):
            return [walk(item, f'{path}[{i}]') for i, item in enumerate(value)]
        if not isinstance(value, dict):
            return value
        if {'fact', 'position', 'in_scene', 'address'} <= set(value):
            address = value['address']
            if isinstance(address, dict) and address.get('domain') == 'knowledge' \
                    and address.get('holder') == 'reader':
                raise ProvenanceRefused('旧 IR 的确认目标已丢失；请免费重编译 WriterBookIR')
        # 只匹配 StateAddress/StateRead/StateChange/StateEntry 的登记字段闭集。
        # ConstraintSpec.domain 是类别标签（带 expected），不属于地址，保留原值。
        if (value.get('domain') == 'reader' and 'key' in value
                and set(value) <= _ADDRESS_FIELDS):
            value.update(domain='knowledge', holder='reader', subjects=[])
            if 'op' in value:
                value['knowledge_mode'] = 'known' if value['op'] == 'set' else None
        if value.get('contract_version') in ('str06-rc12', 'str06-rc13'):
            value['contract_version'] = 'str06-rc14'
        # 已折叠的建立项不是 TargetDelta，不保留揭示关联。
        if 'established_by' in value and 'op' in value:
            value.pop('confirms', None)
        elif (value.get('domain') == 'knowledge' and value.get('holder') == 'reader'
              and value.get('op') == 'set' and value.get('knowledge_mode') == 'known'
              and not isinstance(value.get('confirms'), dict)):
            raise ProvenanceRefused(
                f'{spec}{path} 的旧读者确认已丢失事实关联；请免费 owner patch '
                'design 补齐 v7 know/confirms 并复编译，禁止按 key 猜同义')
        for field in ('entry_state', 'background_state'):
            rows = value.get(field)
            if isinstance(rows, list):
                reader = [r for r in rows if r.get('domain') == 'reader']
                value[field] = [r for r in rows if r.get('domain') != 'reader']
                dest = field.replace('_state', '_knowledge')
                value.setdefault(dest, []).extend(knowledge_entry(r) for r in reader)
        if isinstance(value.get('reads'), list):
            value['reads'] = [knowledge_entry(r) if isinstance(r, dict)
                              and r.get('domain') == 'reader' and 'status' in r else r
                              for r in value['reads']]
        return {key: walk(item, f'{path}.{key}') for key, item in value.items()}

    result = walk(out)
    if spec in _RC17_SITES:
        result['contract_version'] = 'str06-rc17'
    return result




#: 产物里那个来源账字段的**盘上键名**。模型侧字段叫 `provenance`（`_` 开头在 Pydantic 里
#: 是私有属性），靠 `Field(alias='_provenance')` 对上；这里按 alias 找，不按字段名猜。
PROVENANCE_KEY = '_provenance'

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
        # Late-bound paths (round archives, delivery retirement) are registered
        # lazily by `_track_history_path`; this avoids scanning/deleting another
        # artifact's concurrent history.
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
        matching numbered rounds.
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
                    '请勿继续下游，先人工核对 live/_rounds') from exc
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
        而 live INVALID 仍是一份尚待处理的独立问题稿。若复用 :meth:`write`，
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

        ⚠ 稿子**没有丢，也没有再花钱**：它留在 `.INVALID` 孪生上，恢复靠重跑所属
        owner；合格的新稿落正式位时，同名 `.INVALID` 退休进 `_rounds/`（`retire_invalid`）。
        """
        rel = paths.check_rel_deep(rel)
        inv_rel = paths.invalid_of(rel)
        data = self._prepare_invalid(rel, artifact, gates_skipped=gates_skipped)
        p = self.path(inv_rel)
        with self.history_transaction(inv_rel):
            if self.exists(inv_rel):
                self.archive_round(inv_rel)
            os.makedirs(_filesystem_path(p.parent), exist_ok=True)
            self._replace_atomically(p, data)
        return p

    def _prepare_invalid(self, rel: str, artifact: M, *,
                         gates_skipped: list[str]) -> bytes:
        """渲染并预校验将落盘的 `.INVALID` 字节；任何一步失败都发生在第一个字节落盘之前。"""
        inv_rel = paths.invalid_of(paths.check_rel_deep(rel))
        src = self._provenance_of(artifact)
        if src is None:
            raise ProvenanceRefused(
                f'{type(artifact).__name__} 没有 `_provenance` 字段，落不了 `.INVALID`：'
                f'`gates_skipped` 只有来源账这一个落点，没有它就等于没记过闸门缺料')

        pending = src.model_copy(update={'gates_skipped': list(gates_skipped)})
        data = self._render(inv_rel, artifact, prov_override=pending)
        self._verify_round_trip(inv_rel, type(artifact), data)
        self._validate_payload(type(artifact), inv_rel, self._loads(inv_rel, data))
        return data

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
        return dest

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
            raise UpstreamMissing(f'{self.ctx.node}: 要判新鲜度的 {rel} 不在盘上')

        # 这六份确定性/字节形态产物没有通用 `_provenance.inputs` 槽位，但盘上已经有
        # 同等强度的 40 位指纹。先走兼容桥，既让历史产物可判，也不改 RootModel/纯文本
        # 的盘上形状。新增来源事实仍只记在原有权威字段里。
        deterministic = self._deterministic_freshness(rel)
        if deterministic is not None:
            return deterministic

        # 候选链（str06）盘面（F8d 桥）：产物不带版本信封、`root_clean_sha1` 记的是
        # 正式 Stage1 章数组字节——正式信封与 stage0 root 语义都覆盖不到它，改走
        # 候选桥；判据仍是同一把 `prov.freshness`（见 `candidate_freshness`）。
        if candidate_artifact(rel) is not None:
            return self.candidate_freshness(rel)

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
                if root_now != root_was and not self.equivalent_body_root(rel, root_was):
                    edge = self._content_edge(STAGE0_CLEAN_TEXT, root_was, root_now)
                    return self._content_report((edge,))
            recorded = (block or {}).get('inputs') if isinstance(block, dict) else None
            if block is not None and recorded is None:
                recorded = {}                # 有来源账但没有 `inputs` ⇒ `no_inputs`，不是 `no_provenance`
        report = self.freshness_of_inputs(rel, recorded)
        if isinstance(data, dict) and (data.get(PROVENANCE_KEY) or {}).get('shared_analysis_source'):
            report = self._shared_analysis_freshness(data, report)
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
        from novel.source_identity import body_owner
        node = body_owner(rel) or producer_node(rel, fallback=self.ctx.node)
        return prov.freshness(
            recorded,
            file_now=self._file_fp_of_key,
            consumed_now=lambda key: self._consumed_fp_of_key(node, key),
        )

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
        # ⚠ 新链 Hook 成文是字节产物（没有 `_provenance` 槽位），身份由章级回执族的
        #   `receipt.json::prose_sha256` 承载。这条分支此前一不小心写在
        #   `_stage1_freshness` 的 return 之后（不可达），`stage5/hook/hook.txt`
        #   于是恒 `unknown/no_provenance`——`test_every_produced_artifact_without_
        #   provenance_reaches_the_deterministic_bridge` 当场抓住了它。
        if rel == STR06_HOOK_TEXT:
            return self._hook_prose_freshness()
        # ⚠ 成书的 rel 带书名 ⇒ **不能与退役表里的 `stage7.book` 模板直接比**
        #   （`RETIRED_ARTIFACT_PATHS['stage7.book']`，别名常量已删）。
        #   拿模板比出来的恒是 False，而后果是 `_deterministic_freshness` 返回 None
        #   ⇒ 成书的 freshness 变成「没有来源账」，那道对账**静默失效**。
        if rel == paths.book_txt_rel(self.ctx.novel_dir):
            return self._book_freshness()
        return None

    def _hook_prose_freshness(self) -> FreshnessReport:
        """`stage5/hook/hook.txt` 的身份由章级回执族的 `receipt.json::prose_sha256`
        （绑定改名前正文）承载；字节产物本身没有 `_provenance` 槽位。"""
        receipt = self._receipt_payload(STR06_HOOK_RECEIPT)
        expected = receipt.get('prose_sha256') if receipt is not None else None
        if not isinstance(expected, str) or not expected:
            return FreshnessReport('unknown', 'no_provenance')
        edge = self._content_edge(
            STR06_HOOK_TEXT, expected, self._content_sha256_or_none(STR06_HOOK_TEXT))
        return self._content_report((edge,))

    def _receipt_payload(self, rel: str) -> dict[str, Any] | None:
        """回执类 companion 的盘上载荷：正式信封读不了就按候选格式读（F8d 桥）。

        章级/Hook 回执是候选链产物（不带版本信封），正式 `decode_artifact` 会以
        `ArtifactVersionError` 拒读；同一份字节换候选读路即可，不为它补信封。
        """
        try:
            payload = self._freshness_payload(rel)
        except ArtifactUnreadable:
            if candidate_artifact(rel) is None:
                raise
            payload = self.candidate_payload(rel)
        return payload if isinstance(payload, dict) else None

    # ─── 候选链（str06）盘面的完成度桥（F8d） ─────────────────────────────
    #
    # 【为什么需要这座桥】正式读路覆盖不到候选链盘面，两处语义差：
    #   ① 候选产物不带版本信封（`Str06Repository.write` 只落逻辑载荷＋内联
    #      `_provenance`）⇒ `decode_artifact` 一律 `ArtifactVersionError`；
    #   ② 候选链的根输入是**正式 Stage1 章数组＋章前文字**（F7c 起 `--book-sid`
    #      生产路径；C 批起 hook_copy 进 Stage2 请求），`root_clean_sha1` 记那两份
    #      字节的组合 SHA-1，而正式 store 只认 stage0/clean_text.txt。
    # 后果不是报错而是静默：一本跑完的书在完成度上恒 broken/stale，operator 与
    # 队列据此把每个节点判成 REDO/UNKNOWN 并关闭式停住——真实产品入口的全链因此
    # 跑不通（F8d 实测）。
    #
    # 【桥的形状】不迁移盘面、不加信封：按候选格式直读盘上 JSON，用**同一把**
    # `novel/prov.py::freshness` 判 `_provenance.inputs`，root 边按产物自己记的
    # 源身份对照当前盘面（Stage1 章数组是候选链的规范身份；正式 store 写盘的候选
    # 路径记 stage0/clean_text，回落正式尺子）——与 `Str06Repository._refuse_stale`
    # 是同一把尺子的两个调用点，不为完成度另造第二份算法。**删除条件**：候选产物
    # 统一走正式信封与 root 身份（F8c 分流收口）后，本段整段消失。

    def candidate_payload(self, rel: str) -> Any:
        """候选链产物的盘上逻辑载荷：不带版本信封，`_provenance` 内联在顶层。"""
        return json.loads(_read_file_bytes(self.path(rel)).decode('utf-8'))

    def candidate_readable(self, rel: str) -> bool:
        """候选产物读得回来吗：JSON 可解析（正文按 UTF-8 可解码），且（已登记模型的）
        载荷过当前 schema。"""
        if str(rel).endswith('.txt'):
            try:
                _read_file_bytes(self.path(rel)).decode('utf-8')
            except (OSError, UnicodeDecodeError):
                return False
            return True
        try:
            payload = self.candidate_payload(rel)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return False
        if not isinstance(payload, dict):
            return False
        model = _artifact_model(candidate_artifact(rel))
        if model is None:                       # 自由形态派生记录：JSON 对象即算可读
            return True
        try:
            model.model_validate(
                {key: value for key, value in payload.items() if key != PROVENANCE_KEY})
        except (ValidationError, ValueError):
            return False
        return True

    def candidate_freshness(self, rel: str) -> FreshnessReport:
        """候选链产物的新鲜度：root 边对照当前源身份，inputs 走同一把尺子。

        【两种 root 身份，两把尺子】候选链的**规范**源身份是正式 Stage1 章数组＋
        章前文字（F7c 起 `--book-sid` 生产路径；C 批起 hook_copy 也是 Stage2 的
        正式输入，root 身份是两份字节的组合摘要，见 `_stage1_root_identity`）：
        产物记的 root 是那一份时，root 边就是完整输入声明——源抽取一类节点的唯一
        输入就是它，`inputs` 为空也认当前（`Str06Repository._refuse_stale` 同样只认
        这把尺子）。产物记的是 stage0/clean_text.txt（正式 store 写盘的候选路径，
        存量夹具与正式 producer 都走它）时回落正式尺子：root 边对照 clean_text、
        inputs 照 `freshness_of_inputs` 判——**空 inputs 保持 unknown**，写盘闸的
        freshness 后置条件一个比特都不放宽。
        """
        try:
            payload = self.candidate_payload(rel)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            # 字节产物（章正文）没有 JSON 载荷：身份由绑定它的回执承载。
            deterministic = self._candidate_deterministic_freshness(rel)
            if deterministic is not None:
                return deterministic
            raise ArtifactUnreadable(
                f'{rel} 不是合法 JSON，判不了新鲜度：{exc!r}') from exc
        block = payload.get(PROVENANCE_KEY) if isinstance(payload, dict) else None
        if not isinstance(block, dict):
            # 没有来源账的候选产物：身份由绑定它的回执／成书字节承载。
            deterministic = self._candidate_deterministic_freshness(rel)
            if deterministic is not None:
                return deterministic
            return FreshnessReport('unknown', 'no_provenance')
        root_was = block.get('root_clean_sha1')
        if not isinstance(root_was, str) or not root_was:
            return FreshnessReport('unknown', 'no_provenance')
        recorded = block.get('inputs')
        stage1_edge = self._content_edge(
            STAGE1_MAIN_TEXT, root_was, self._stage1_root_identity())
        if stage1_edge.state != 'fresh':
            clean_edge = self._content_edge(
                STAGE0_CLEAN_TEXT, root_was,
                self._content_sha1_or_none(STAGE0_CLEAN_TEXT))
            if clean_edge.state != 'fresh':
                return self._content_report((clean_edge,))
            report = self.freshness_of_inputs(rel, recorded)
            if report.state != 'fresh':
                return report
            return self._content_report((clean_edge, *report.edges))
        if not recorded:
            return self._content_report((stage1_edge,))
        # 候选链的来源边多数只记文件级指纹（`CandidateRunContext.record_edge` 的
        # `consumed` 恒空：投影注册表不覆盖候选链）⇒ 走正式 `freshness_of_inputs`
        # 同一把尺子（声明了投影的边照旧按 consumed 判），只把「没声明读了什么、
        # 但字节未变」的边按文件身份落成 fresh：`consumed_undeclared` 的前置条件
        # 就是 `was_file == now_file`，不把它升级成 unknown——那会让每个已跑完的
        # 节点都停在人工裁决面。字节变了照旧 stale，不放过。
        report = self.freshness_of_inputs(rel, recorded)
        edges = tuple(
            replace(edge, state='fresh', reason='match')
            if edge.reason == 'consumed_undeclared' else edge
            for edge in report.edges)
        return self._content_report((stage1_edge, *edges))

    def _candidate_deterministic_freshness(self, rel: str) -> FreshnessReport | None:
        """没有 `_provenance` 的候选产物：身份由绑定它的回执／manifest 承载。"""
        normalized = str(rel).replace('\\', '/')
        prose = re.fullmatch(r'stage5/body/ch(\d+)\.txt', normalized)
        if prose is not None:
            # 章正文的账在绑定它的章回执上（与 Hook 成文同一形状）。
            receipt_rel = artifact_rel(STR06_STAGE5_BODY_RECEIPT,
                                       nn=int(prose.group(1)))
            receipt = self._receipt_payload(receipt_rel)
            if receipt is None:
                return None
            expected = receipt.get('prose_sha256')
            if not isinstance(expected, str) or not expected:
                return FreshnessReport('unknown', 'no_provenance')
            edge = self._content_edge(
                normalized, expected, self._content_sha256_or_none(normalized))
            return self._content_report((edge,))
        if normalized == STR06_DELIVERY_MANIFEST:
            # 交付 manifest 是就绪记录：**没有来源账的旧形态**用它自己记的成书字节
            # （与 `_book_freshness` 同一枚指纹的两个方向）。带 `_provenance` 的
            # 现行形态走通用来源账路（assemble 节点的 inputs 才是它的账）。
            book_rel = paths.book_txt_rel(self.ctx.novel_dir)
            manifest = self._raw_json_payload(STR06_DELIVERY_MANIFEST)
            if manifest is None:
                return None
            if isinstance(manifest.get(PROVENANCE_KEY), dict):
                return None
            expected = manifest.get('final_text_sha256')
            if not isinstance(expected, str) or not expected:
                return FreshnessReport('unknown', 'no_provenance')
            return self._content_report((self._content_edge(
                book_rel, expected, self._content_sha256_or_none(book_rel)),))
        return None

    def _stage0_revisions_freshness(self) -> FreshnessReport:
        """`stage0/revisions.json`：这份 transform 是照盘上**现在这份原文**建的吗？

        ⚠ **不给它补 `_provenance`。** 本层是全链源头；「照什么产的」由
        `source.source_ref` / `normalized_source_sha1` 答完。

        ⚠⚠ **只有原文这一条上游边。** `clean_text.txt` 是本格的物化投影；
        投影完整性由判据 `s0_revision_chain`（R-0.5）核对。
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
        clean_was = chain.get('clean_sha1')
        if not isinstance(source, dict) or not isinstance(clean_was, str):
            return FreshnessReport('unknown', 'no_provenance')
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
        from contracts.schema.stage0 import Stage0Transform
        from contracts.schema.stage1 import HookCopy, MainText
        from novel.stages.stage0 import revisions as stage0_revisions
        from novel.core.ingest_audit import audit_surface

        try:
            transform, _ = self.read_own(Stage0Transform, STAGE0_REVISIONS)
            stage0_revisions.verify_transform(transform)
            stage0 = stage0_revisions.snapshot_of(transform)
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

    def _book_freshness(self) -> FreshnessReport:
        # ⚠ F8b-3：新链成书身份由 assembly 的交付清单承载（manifest 最后提交；
        # `final_text_sha256` 绑定成书字节）。旧 `stage7/name_map.json` 的
        # book_txt 桥随旧链退役（F8a 后该路径已拒读）。
        manifest = self._raw_json_payload(STR06_DELIVERY_MANIFEST)
        expected = manifest.get('final_text_sha256') if manifest is not None else None
        if not isinstance(expected, str) or not expected:
            return FreshnessReport('unknown', 'no_provenance')
        # 边的 key 用**这本书的**那条具体路径：报告里要点名的就是它。
        book_rel = paths.book_txt_rel(self.ctx.novel_dir)
        edge = self._content_edge(
            book_rel, expected, self._content_sha256_or_none(book_rel))
        return self._content_report((edge,))

    def _raw_json_payload(self, rel: str) -> dict[str, Any] | None:
        """读一份**未登记 spec** 的 companion JSON（如交付清单）；缺席 = 没有账。"""
        p = self.path(rel)
        if not p.is_file():
            return None
        try:
            raw = json.loads(_read_file_bytes(p).decode('utf-8'))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
            raise ArtifactUnreadable(f'{rel} 不是可用的来源指纹载体：{e!r}') from e
        return raw if isinstance(raw, dict) else None

    def _content_sha256_or_none(self, rel: str) -> str | None:
        p = self.path(rel)
        if not p.is_file():
            return None
        try:
            return hashlib.sha256(_read_file_bytes(p)).hexdigest()
        except OSError as e:
            raise ArtifactUnreadable(f'{self.ctx.node}: 读不了 {rel} 的内容指纹：{e!r}') from e

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
            raise ArtifactUnreadable(f'{self.ctx.node}: 读不了 {rel} 的内容指纹：{e!r}')

    def _stage1_root_identity(self) -> str | None:
        """候选链规范源身份：Stage1 **main_text＋hook_copy** 两份字节的组合 SHA-1。

        章前文字（源引子）自质量改进批次 C 起是 Stage2 的正式输入，
        `chain.load_stage1_chapters` 的 `root_sha1` 随之覆盖两份字节；完成度面的
        root 边必须用同一把尺子，否则每个候选节点都会因为「引子也参与输入」被误判
        成 stale（F8d 桥的实测形状）。

        ⚠ 批次 C 之前落的候选根只记 main_text 一份 SHA-1：那种根在新代码下连
        workflow 复校都过不了（源身份不匹配，见
        `init_or_resume_candidate_workflow`）⇒ 这里判 stale 是如实答案，
        不是误报；续跑按 REQUIREMENTS 用新独立根。
        """
        main = self.path(STAGE1_MAIN_TEXT)
        if not main.is_file():
            return None
        hook = self.path(STAGE1_HOOK_COPY)
        hook_raw = _read_file_bytes(hook) if hook.is_file() else b''
        try:
            return prov.combined_content_sha1(_read_file_bytes(main), hook_raw)
        except OSError as e:
            raise ArtifactUnreadable(
                f'{self.ctx.node}: 读不了 Stage1 源身份：{e!r}') from e

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

    def equivalent_body_root(self, rel: str, root: str, *, source: Provenance | None = None) -> bool:
        """只承认已审源钩子修订下的相同正文；原来源字节、费用和审批均不重挂。"""
        from novel.source_identity import BODY_OWNERS, body_owner, same_body_source
        owner = body_owner(rel)
        if owner not in BODY_OWNERS:
            return False
        with self.ctx.validation_reads():
            if source is None:
                spec = artifact_spec(rel)
                if spec is None:
                    return False
                try:
                    artifact = self.read_own(spec.model, rel)[0]
                    source = self._provenance_of(artifact)
                except (TerminalState, ProvenanceRefused, ValueError, OSError):
                    return False
            return (source is not None and source.root_clean_sha1 == root
                    and same_body_source(self, root, node=owner, inputs=source.inputs))

    def _refuse_bad_provenance(self, rel: str, src: Provenance) -> None:
        """写盘口的闸。**拒绝，不是打印一句警告然后照写。**

        ① **`prompts` 非空而 `inputs` 为空 ⇒ 拒。** 一个跑过 LLM 的节点不可能没有上游；
           `inputs` 空就是**某处绕过了 `store.read`**。原型这里是 `print` 一句，
           于是三份产物的来源账各漏各的，而没有任何一层出声。

        ⚠⚠ **另一道闸（「0 成本节点不许记 `model`」）不在这里，在铸造口**
        （`RunContext.llm_provenance`）。这是本轮实测改掉的一处：
        放在写盘口会误伤若干 0 成本入口——`--recheck`
        写回的是一份**继承来的**来源账，它记的 `model` 属于当初那次付费产出，
        不是本次跑的新宣称；`stage6.approve` / `stage7.approve` 同理
        （它们是 0 成本节点，改写的却是付费节点产的那份产物）。
        ⇒ 该拦的是「**0 成本的跑现造一份声称模型产了它的账**」，而现造只有一个入口。
        """
        prompts = getattr(src, 'prompts', None)
        current_root = self.ctx.root_clean_sha1
        if src.root_clean_sha1 != current_root and not self.equivalent_body_root(
                rel, src.root_clean_sha1, source=src):
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

        ⚠ 抽出来是为了「先知道将要写什么」：调用方要在**动归档之前**拿到字节并过预校验
        （`write_invalid`／`write_round`），失败才会发生在第一个字节落盘之前。
        """
        name = provenance_field(type(artifact))
        payload = artifact.model_dump(
            by_alias=True, mode='json', exclude_unset=exclude_unset)
        if name is not None:
            src = prov_override if prov_override is not None else getattr(artifact, name)
            self._refuse_bad_provenance(rel, src)
            # ⚠ 三态只在 `to_disk()` 里兑现：`origin: null` 与「没有这个键」
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


class PublishItem(NamedTuple):
    """输出组的一项（R6 统一发布权威）：JSON 产物（`value`）或文本产物（`text`）。

    恰给其一：`.txt` 走 `text`（账在绑定它的回执上，字节仍进组 seal 的输出
    摘要）；其余登记产物走 `value`，落盘即带来源账。`refs`／`claim_usage`
    语义同 `Str06Repository.write`。
    """

    rel: str
    value: Any = None
    text: str | None = None
    owners: tuple[str, ...] = ()
    refs: tuple = ()
    claim_usage: bool = True


class Str06Repository:
    """隔离 RC 产物的持久化口（F7a-2 起接正式 Store 机制；F8 分流删除）。

    【接的是正式 Store 的机制，不是它的注册表】
    家族路径准入与完整路径来源账键走 `paths.check_rel_family`／
    `family_input_key`；provenance 用 `contracts.schema.common` 的同一套
    canonical 模型（`FreeProvenance`／`LlmProvenance`／`InputEdge`／`Usage`），
    由 `CandidateRunContext` 按候选节点表铸造；freshness 用
    `novel.prov.freshness` 对产物自记的 inputs 复算，明确 stale 在生产路径
    拒绝；INVALID 孪生名与 `_rounds/` 归档走 `paths.invalid_of`／`rounds_dir_of`／
    `round_rel`／`archived_rounds`；落盘一律 OS 原子替换 + 读回核对。
    ⇒ 不新建领域模型（I-11）、不建第二本账（I-17），也不进现役
    ARTIFACT_SPECS／NODES（S-04）。

    【与正式 ArtifactStore 的边界】正式 store 由 RunContext 造、吃现役注册表
    与 consumption 投影；本类由候选上下文造、吃候选表
    （`STR06_CANDIDATE_ARTIFACTS`）。两侧共用 schema 与 prov 原语。盘上
    `_provenance` 键与正式产物同形，F8 激活时可直接进正式解码路径。

    【context 是显式依赖】构造后必须 `attach_context` 附上候选运行上下文再写
    正式产物；未附着时写已登记产物**关闭式拒绝**——不存在「无来源账的正式
    产物」这条静默路径。读取旧根／外部文件不依赖上下文。

    【R6 统一发布权威】正式产物按**输出组**发布（`publish`）：组内产物先落盘、
    再写组 seal；免费产物同样封存（不另立付费/免费两套发布语义）。
    `read_model` 对正式输入校验 seal 与摘要——未封存、seal 损坏或摘要不符都
    不是正式输入（AUD-05），恢复靠重跑所属 owner 免费补尾；诊断面 `inspect`
    可展示未发布证据，但不反向放行正式消费。
    """

    def __init__(self, root: Path, context=None):
        self.root = Path(root).resolve()
        self.context = context

    def attach_context(self, context) -> None:
        self.context = context

    def path(self, rel: str) -> Path:
        return paths.str06_artifact_path(self.root, rel)

    @staticmethod
    def read_external(path: Path) -> Any:
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError(f'JSON 重复键：{key}')
                result[key] = value
            return result
        return json.loads(path.read_text(encoding='utf-8-sig'), object_pairs_hook=unique)

    @staticmethod
    def file_sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def read(self, rel: str) -> Any:
        path = self.path(rel)
        raw = path.read_bytes()
        data = self.read_external(path)
        if self.context is not None:
            spec = candidate_artifact(rel)
            if spec is not None:
                # 读到的上游才记边（手工列举的来源清单会烂；内存传递不产生边）。
                self.context.record_edge(paths.family_input_key(rel),
                                         prov.fingerprint_bytes(raw))
                if not self.context.readonly:
                    self._refuse_stale(rel, data)
        return data

    def _refuse_stale(self, rel: str, data: Any) -> None:
        """对产物自记的 inputs 复算新鲜度；只有可复算且明确 stale 才阻断。

        无 provenance（旧根产物、自由形态记录）保持 unknown 放行——不把「不知道」
        升级成 fresh，也不把旧根证据冒充成已验。
        """
        provenance = data.get('_provenance') if isinstance(data, dict) else None
        recorded = provenance.get('inputs') if isinstance(provenance, dict) else None

        def file_now(key: str) -> str | None:
            try:
                return prov.fingerprint_bytes(self.path(key).read_bytes())
            except (FileNotFoundError, NotADirectoryError):
                return None

        report = prov.freshness(recorded, file_now=file_now)
        if report.state != 'stale':
            return
        changed = ', '.join(edge.key for edge in report.edges) or report.reason
        raise UpstreamStale(
            f'候选产物 {rel} 的上游已过期（{changed}）；在生成或付费请求前拒绝消费。'
            '请先从对应 owner 重建当前候选根上的产物')

    def write(self, rel: str, value: Any, *, owners: tuple[str, ...] | None = None,
              refs: tuple = (), claim_usage: bool = True) -> Path:
        """落盘一份产物（单项输出组）：带来源账并**封存发布证明**（R6）。

        `refs` 是 B1 的**显式采用引用**（`AdoptionRef`）：provenance 由纯函数按
        引用构造，付费产物须带显式引用。**免费产物（`refs` 为空）同样封存**
        CommitBundle（输入清单＋采用证据＋输出摘要＋seal，落 `runtime/commit/`，
        采用状态从 seal 派生；免费 bundle 的 `adoptions` 为空）——不另立
        付费/免费两套发布语义。`owners` 保留作调用方语义标注，不充当采用窗口
        的过滤键。``claim_usage=False`` 用于同一份付费产出的第二个 key（生成
        事实继承，用量不重复认领）。多产物同组发布走 `publish`（组内产物先
        落盘、再写组 seal）。
        """
        return self.publish((PublishItem(
            rel=rel, value=value, owners=owners or (), refs=refs,
            claim_usage=claim_usage),))[0]

    def publish(self, items: tuple['PublishItem', ...] | list['PublishItem']
                ) -> tuple[Path, ...]:
        """按输出组发布（R6 统一发布权威）：先写组内全部产物，再写组 seal。

        组是同一 writer 的一次正式提交单位——`NODES.produces` 上同组的产物
        （Stage2 双真源＋fold、Stage4 免费编译的一组派生输出、每章正文＋章回执、
        Hook＋Hook 回执、最终 TXT＋delivery manifest）一次成形、一个 seal；
        不把不同 semantic owner 捆成整书事务。纪律：

        · 铸账、渲染全部字节**完成后才开始落盘**（中途拒绝不留半组字节）；
        · 组内产物逐字回读核对后**才写 seal**：封存写失败或封存前崩溃时，组内
          产物已在盘上但不是正式输入（`read_model` 校验 seal 与摘要，AUD-05），
          重跑所属 owner 免费补尾——付费段按请求身份重放耐久响应，免费段确定
          性重编译——重新发布并封存（`I-30` 幂等重跑、零新购）；
        · 组内全部已登记 JSON 产物必须同 writer（一个输出组一个唯一 writer，
          `I-25`）；跨 writer 的编排错误在此 fail fast。
        """
        if not items:
            raise ProvenanceRefused('publish 拒绝空输出组：没有产物就没有发布事实')
        prepared: list[tuple['PublishItem', bytes, Any]] = []
        writers: list[str] = []
        inputs: dict[str, InputEdge] = {}
        refs: list = []
        for item in items:
            data, provenance = self._prepare_item(item)
            prepared.append((item, data, provenance))
            spec = candidate_artifact(item.rel)
            if provenance is not None:
                writer = _writer_of(spec)
                if writer not in writers:
                    writers.append(writer)
                for key, edge in provenance.inputs.items():
                    if key in inputs and inputs[key].file != edge.file:
                        raise ProvenanceRefused(f'输出组内输入边指纹冲突：{key}')
                    inputs[key] = edge
            for ref in item.refs:
                if ref not in refs:
                    refs.append(ref)
        if len(writers) > 1:
            raise ProvenanceRefused(
                f'输出组跨 writer 发布（{writers}）：一个输出组只有一个唯一 writer')
        written: list[Path] = []
        for item, data, _provenance in prepared:
            path = self.path(item.rel)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._atomic_write(path, data)
            if path.read_bytes() != data:
                raise ValueError(f'RC 产物读回字节不一致：{item.rel}')
            written.append(path)
        if writers:
            # 组 seal：全部产物落盘并核对后才封存（免费产物同样封存）。
            primary_index = next(index for index, (_item, _data, provenance)
                                 in enumerate(prepared) if provenance is not None)
            primary, primary_data, _p = prepared[primary_index]
            bundle = self.context.commit_bundle(
                node=writers[0], rel=primary.rel,
                artifact_sha256=hashlib.sha256(primary_data).hexdigest(),
                inputs=inputs, refs=tuple(refs),
                run_id=self.context.ledger.run_id,
                created_at=int(time.time()),
                outputs=tuple(CommitOutputRef(
                    key=item.rel, sha256=hashlib.sha256(data).hexdigest())
                    for item, data, _provenance in prepared))
            self._write_commit_bundle(bundle)
        return tuple(written)

    def _prepare_item(self, item: 'PublishItem') -> tuple[bytes, Any]:
        """渲染输出组一项的落盘字节并铸来源账（纯准备，不落盘）。

        文本产物（`.txt`）不带来源账：正文（text）的账在绑定它的回执上——同组
        回执的 seal 以输出摘要覆盖正文字节。序列化保序（同 `write`，不
        sort_keys）：盘上字符典序即 wire 序，prompt 与已购请求身份按字节复算。
        """
        if (item.text is None) == (item.value is None):
            raise ProvenanceRefused(
                f'{item.rel} 输出组项必须恰给 value（JSON）或 text（文本）之一')
        spec = candidate_artifact(item.rel)
        if item.text is not None:
            if spec is not None and not spec.endswith('.txt'):
                raise ProvenanceRefused(f'{item.rel} 是已登记 JSON 产物，不能按文本发布')
            return item.text.encode('utf-8'), None
        payload = (item.value.model_dump(mode='json')
                   if isinstance(item.value, BaseModel) else item.value)
        provenance = None
        if spec is not None and not spec.endswith('.txt'):
            if self.context is None:
                raise ProvenanceRefused(
                    f'{item.rel} 是已登记候选产物，但 repository 未附加上下文：'
                    '拒绝在无来源账的情况下写正式产物')
            # 已登记产物落盘即带来源账；正文（text）的账在绑定它的回执上。
            provenance = self.context.provenance_for(
                _writer_of(spec), item.rel, refs=item.refs,
                claim_usage=item.claim_usage)
            payload = {**payload, '_provenance': provenance.to_disk()}
        return (json.dumps(payload, ensure_ascii=False, indent=2,
                           allow_nan=False).encode('utf-8'), provenance)

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        """OS 原子替换落盘（同目录临时文件→`os.replace`）；失败清理临时文件。"""
        # RC 与现役共用 OS 原子替换语义，不调用现役 Stage 实现或私有产物编排。
        fd, temporary = tempfile.mkstemp(prefix='.rc-', suffix='.tmp', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _write_commit_bundle(self, bundle: dict) -> Path:
        """CommitBundle 原子落盘：``runtime/commit/<node>/<seal16>_<slug>.json``。"""
        slug = Path(bundle['artifact']).stem
        rel = f"runtime/commit/{bundle['node']}/{bundle['seal'][:16]}_{slug}.json"
        path = self.path(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(bundle, ensure_ascii=False, indent=2,
                          allow_nan=False).encode('utf-8')
        self._atomic_write(path, data)
        return path

    def write_evidence_bytes(self, rel: str, data: bytes) -> Path:
        """运行证据按原字节耐久保存；相同内容地址不可被不同字节覆盖。"""
        if rel != f'runtime/revisions/evidence/{hashlib.sha256(data).hexdigest()}.json':
            raise ValueError('修订字节证据只允许写 runtime/revisions/evidence/')
        path = self.path(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_bytes() != data:
            raise ValueError('修订证据内容地址已有不同字节，拒绝覆盖')
        self._atomic_write(path, data)
        if path.read_bytes() != data:
            raise ValueError('修订证据读回不一致')
        return path

    def write_text(self, rel: str, text: str) -> Path:
        """正文/文本产物的原子写（UTF-8）+ 回读核对；与 write 同一套语义。"""
        path = self.path(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = text.encode('utf-8')
        self._atomic_write(path, data)
        if path.read_bytes() != data:
            raise ValueError(f'RC 产物读回字节不一致：{rel}')
        return path

    def read_text(self, rel: str) -> str:
        """正文/文本产物读取；文件不存在抛 FileNotFoundError，由调用方归类。"""
        return self.path(rel).read_text(encoding='utf-8')

    def inspect(self, rel: str) -> Any:
        """只读检查：不记来源边、不判新鲜度（断言、视图与 `--check-only` 用）。

        与 `read` 的分工同正式 store 的 `full_sha256_or_none`：生产消费走 `read`
        （记账 + 过期拒绝），检查走本口——单进程 pipeline 里「谁读的」必须能
        区分生产消费与检查，否则一次断言读会写进下一份产物的来源账。
        """
        return self.read_external(self.path(rel))

    def read_model(self, rel: str):
        """按候选产物表读一份已登记产物并校验为契约模型（owner runner 的输入口）。

        剥掉盘上 `_provenance` 键再校验：来源账是盘上事实，不进计算用的模型
        （模型不声明该字段时 `extra='forbid'` 会拒读；声明的模型也不在此路上
        消费它）。读路径仍走 `read`——记账与过期拒绝照常。

        R6 统一发布权威：正式输入另须通过**发布校验**（`_require_sealed`，先于
        记账与过期判定——未发布的产物根本不是输入）——没有覆盖当前字节的
        已封存 CommitBundle（seal＋摘要双校验）即拒绝消费（AUD-05）；诊断面走
        `inspect` 不受此限。
        """
        spec = candidate_artifact(rel)
        model = _artifact_model(spec)
        if spec is None or model is None:
            raise ProvenanceRefused(f'{rel} 未登记契约模型，不能按模型读')
        self._require_sealed(rel)
        data = self.read(rel)
        if isinstance(data, dict):
            data = {k: v for k, v in data.items() if k != '_provenance'}
            data = _upgrade_rc12_payload(spec, data)
        return model.model_validate(data)

    def read_graph_hook(self):
        """免费 Design 修复只读已封存 graph 的独立 Hook 组件。

        旧正文丢失 confirms 时不消费或补猜它；Hook 仍须经过正式地址迁移与
        schema 校验，修复后的新 graph 再由 compile 做引用闭合。
        """
        from contracts.schema.hook import HookDesign
        rel = 'stage4/design/presentation_graph.json'
        self._require_sealed(rel)
        data = self.read(rel)
        component = _upgrade_rc12_payload(rel, {
            'contract_version': data['contract_version'], 'hook': data['hook']})
        return HookDesign.model_validate(component['hook'])

    def _require_sealed(self, rel: str) -> None:
        """正式输入的发布校验（R6）：无覆盖当前字节的已封存组即拒绝消费。

        封存记录损坏（seal 不符）由 `sealed_commit_bundles` 关闭式上抛，不是
        「当它不存在」；组内缺文件、摘要不符与本条同走 `UnsealedArtifact`
        （fail fast，`I-34`），恢复入口免费：重跑所属 owner 重新发布并封存。
        """
        from novel.work_units import sealed_commit_bundles

        digest = hashlib.sha256(self.path(rel).read_bytes()).hexdigest()
        for _path, bundle in sealed_commit_bundles(self.root):
            if any(output.key == rel and output.sha256 == digest
                   for output in bundle.outputs):
                return
        raise UnsealedArtifact(
            f'{rel} 没有覆盖当前字节的已封存 CommitBundle（统一发布权威，AUD-05）：'
            '未封存或摘要不符的产物不是正式输入。恢复入口：重跑所属 owner 节点'
            '（付费段按请求身份免费重放、免费段确定性重编译，零新购），'
            '重新发布并封存后再消费')

    # ── INVALID 孪生与 _rounds 归档（F7a-2：路径与写入事务接正式机制） ──────

    def invalid_rel(self, rel: str) -> str:
        return paths.invalid_of(paths.check_rel_family(rel))

    def round_rel(self, rel: str, round_no: int) -> str:
        return paths.round_rel(paths.check_rel_family(rel), round_no)

    def archived_rounds(self, rel: str) -> set[int]:
        formal = paths.check_rel_family(rel)
        return paths.archived_rounds(
            self.path(paths.rounds_dir_of(formal)), paths.stem_of(formal))

    def write_invalid(self, rel: str, value: Any, *,
                      problems: tuple[str, ...], owners: tuple[str, ...] | None = None,
                      refs: tuple = ()) -> Path:
        """被机械闸拦下的稿进 `.INVALID` 孪生：正式位不写，`gates_skipped` 留痕。

        先归档孪生上已有的历史轮次再覆写——归档是出事之后唯一能回去的那份，
        不允许第二次被拦的稿静默覆盖第一次（对齐正式 `_rounds` 纪律）。
        本方法只保证「拦下的稿不丢、正式位不被污染」；恢复靠重跑所属 owner，
        孪生不能被原样提升为正式位。
        """
        invalid = self.invalid_rel(rel)
        seen = self.archived_rounds(invalid)
        if self.path(invalid).exists():
            # 孪生上已有历史：先归档成下一轮再写新孪生——归档是出事之后唯一
            # 能回去的那份，不允许第二次被拦的稿静默覆盖第一次。
            self._archive(invalid, prov.next_round(seen))
        spec = candidate_artifact(paths.formal_of(invalid))
        if spec is not None and not spec.endswith('.txt'):
            if self.context is None:
                raise ProvenanceRefused(
                    f'{rel} 是已登记候选产物，但 repository 未附加上下文：'
                    '拒绝在无来源账的情况下写 INVALID 孪生')
            provenance = self.context.provenance_for(_writer_of(spec), invalid, refs=refs)
            provenance = provenance.model_copy(update={'gates_skipped': list(problems)})
            payload = value.model_dump(mode='json') if isinstance(value, BaseModel) else value
            payload = {**payload, '_provenance': provenance.to_disk()}
            return self._write_payload(invalid, payload)
        return self.write(invalid, value, owners=owners, refs=refs)

    def _archive(self, rel: str, round_no: int) -> Path:
        destination = self.path(self.round_rel(rel, round_no))
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(self.path(rel), destination)
        return destination

    def _write_payload(self, rel: str, payload: Any) -> Path:
        path = self.path(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(payload, ensure_ascii=False, indent=2,
                          allow_nan=False).encode('utf-8')
        fd, temporary = tempfile.mkstemp(prefix='.rc-', suffix='.tmp', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        if path.read_bytes() != data:
            raise ValueError(f'RC 产物读回字节不一致：{rel}')
        return path
