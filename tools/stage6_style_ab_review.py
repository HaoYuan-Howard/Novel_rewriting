#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage6 定性文风包消融的匿名出包、人工复核与冻结裁决。

本工具不调用模型、不联网，也不替人工评文案。它只做四件事：

1. 从付费 runner 已经落下的 10 组 A/B 产物生成只含六候选的 phase-1 排名包；
2. 把解盲映射先写到 blind 目录之外，并用 canonical JSON SHA-256 封存；
3. 强制先封完整 1~6 排名，成功后才生成带共同参考材料的 phase-2 复核包；
4. 按预登记阈值计算 ``keep_and_localize`` / ``delete_qualitative`` /
   ``inconclusive``，输出不含候选正文的可复验 manifest。

所有带正文、映射或人工逐候选意见的输出路径都必须由调用方显式提供；正常应放在
``outputs/`` 下的受控实验目录，绝不能提交 Git。mapping 路径不得位于 blind 目录内。

典型流程::

  python -X utf8 tools/stage6_style_ab_review.py pack \
    --protocol protocol.json --runs runs.json \
    --mapping outputs/.../private/mapping.json --blind-dir outputs/.../blind
  python -X utf8 tools/stage6_style_ab_review.py seal-ranking \
    --protocol protocol.json --runs runs.json \
    --mapping outputs/.../private/mapping.json \
    --blind-manifest outputs/.../blind/manifest.json \
    --draft ranking.draft.json --out outputs/.../private/ranking.json \
    --review-dir outputs/.../review
  python -X utf8 tools/stage6_style_ab_review.py seal-review \
    --review-manifest outputs/.../review/manifest.json \
    --ranking outputs/.../private/ranking.json \
    --draft review.draft.json --out outputs/.../private/review.json
  python -X utf8 tools/stage6_style_ab_review.py decide \
    --protocol protocol.json --runs runs.json \
    --mapping outputs/.../private/mapping.json \
    --blind-manifest outputs/.../blind/manifest.json \
    --review-manifest outputs/.../review/manifest.json \
    --ranking outputs/.../private/ranking.json \
    --review outputs/.../private/review.json --out decision.json
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from typing import Any, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 导入判据表是有意的：最终 HARD 的严重度只能从唯一注册表读取，不能在实验工具里
# 手抄第二份 ``{'hook_empty', ...}``。
from contracts.judges import stage6 as _stage6_rules  # noqa: E402,F401
from contracts.artifacts import STAGE6_HOOK  # noqa: E402
from contracts.invariants import HOOK_SLOTS  # noqa: E402
from contracts.rules import REGISTRY  # noqa: E402
from contracts.schema import decode_artifact  # noqa: E402
from contracts.schema.stage6 import Hook  # noqa: E402


SCHEMA_VERSION = 1
REPO_ROOT = Path(__file__).resolve().parent.parent
ARMS = ('A', 'B')
LABELS = tuple(f'T{index}' for index in range(1, 7))
SAFETY_METRICS = (
    'severe_fact_errors',
    'reuse_candidates',
    'final_hard_findings',
    'all_dirty_runs',
    'retry_attempts',
)
_SHA256_RE = r'^[0-9a-f]{64}$'
_ID_RE = r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'
_UTC_RE = r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$'

Arm = Literal['A', 'B']
FactSafety = Literal['clear', 'minor', 'severe', 'unknown']
Adoptability = Literal['as_is', 'light_edit', 'reject']


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class BenefitThresholds(StrictModel):
    min_global_a_wins: Literal[8]
    min_a_wins_per_book: Literal[3]
    min_mean_advantage_a: Literal[1.0]


class DeletionThresholds(StrictModel):
    max_global_a_wins: Literal[5]
    max_a_wins_per_book: Literal[3]
    max_mean_advantage_a: Literal[0.5]


class ExperimentProtocol(StrictModel):
    schema_version: Literal[1]
    protocol_id: str = Field(pattern=_ID_RE)
    declared_at: str = Field(pattern=_UTC_RE)
    thresholds_locked_before_evaluation: Literal[True]
    expected_books: Literal[2]
    rounds_per_book: Literal[5]
    candidates_per_arm: Literal[3]
    max_candidate_retries: Literal[6]
    quality_statistic: Literal['panel_best_quality_a_minus_b']
    safety_metrics: list[str]
    benefit: BenefitThresholds
    deletion: DeletionThresholds

    @model_validator(mode='after')
    def _the_predeclared_surface_is_exact(self) -> 'ExperimentProtocol':
        if tuple(self.safety_metrics) != SAFETY_METRICS:
            raise ValueError(
                f'safety_metrics 必须逐项等于预登记顺序 {SAFETY_METRICS!r}')
        return self


class InventoryRow(StrictModel):
    rel: str = Field(min_length=1)
    sha256: str = Field(pattern=_SHA256_RE)


class BookRun(StrictModel):
    book_key: str = Field(pattern=_ID_RE)
    source_sha256: str = Field(pattern=_SHA256_RE)
    sid: str | None = None
    source_book: str | None = None
    upstream_inventory: list[InventoryRow] = Field(default_factory=list)
    # 测试或离线复核可显式提供一份已经冻结的共同参考；真实 runner 走上面三格，
    # phase-2 时才从 Stage6 的生产 Material 投影生成，不在 phase-1 提前读取。
    review_reference_path: str | None = None
    review_reference_sha256: str | None = Field(default=None, pattern=_SHA256_RE)

    @model_validator(mode='after')
    def _has_one_reference_source(self) -> 'BookRun':
        explicit = self.review_reference_path is not None
        if explicit != (self.review_reference_sha256 is not None):
            raise ValueError('显式 review reference 的 path/sha256 必须成对')
        production = bool(self.sid and self.source_book and self.upstream_inventory)
        if not explicit and not production:
            raise ValueError('book 必须给显式 review reference，或给冻结 source_book/inventory')
        return self


class ArmRun(StrictModel):
    run_id: str = Field(pattern=_ID_RE)
    artifact_path: str = Field(min_length=1)
    artifact_sha256: str = Field(pattern=_SHA256_RE)
    artifact_state: Literal['live', 'invalid_all_dirty']
    model: str = Field(min_length=1)
    reasoning_effort: str = Field(min_length=1)
    initial_prompt_sha256: str = Field(pattern=_SHA256_RE)
    shared_material_sha256: str = Field(pattern=_SHA256_RE)
    cache_namespace_sha256: str = Field(pattern=_SHA256_RE)
    initial_calls: Literal[1]
    candidate_retries: int = Field(ge=0, le=3)


class PanelRun(StrictModel):
    panel_key: str = Field(pattern=_ID_RE)
    book_key: str = Field(pattern=_ID_RE)
    round: int = Field(ge=1, le=5)
    run_order: list[Arm]
    arms: dict[Arm, ArmRun]

    @model_validator(mode='after')
    def _paired_run_is_single_variable(self) -> 'PanelRun':
        if self.run_order not in (['A', 'B'], ['B', 'A']):
            raise ValueError('run_order 必须恰好是 A/B 或 B/A')
        if set(self.arms) != set(ARMS):
            raise ValueError('每个 panel 必须恰有 A/B 两臂')
        left, right = self.arms['A'], self.arms['B']
        if left.model != right.model:
            raise ValueError(f'{self.panel_key}: A/B model 不同')
        if left.reasoning_effort != right.reasoning_effort:
            raise ValueError(f'{self.panel_key}: A/B effort 不同')
        if left.shared_material_sha256 != right.shared_material_sha256:
            raise ValueError(f'{self.panel_key}: A/B 共享材料指纹不同')
        if left.initial_prompt_sha256 == right.initial_prompt_sha256:
            raise ValueError(f'{self.panel_key}: A/B 初始 prompt 完全相同，消融没有发生')
        return self


class RunManifest(StrictModel):
    schema_version: Literal[1]
    experiment_id: str = Field(pattern=_ID_RE)
    protocol_sha256: str = Field(pattern=_SHA256_RE)
    books: list[BookRun]
    panels: list[PanelRun]

    @model_validator(mode='after')
    def _balanced_complete_design(self) -> 'RunManifest':
        if len(self.books) != 2:
            raise ValueError(f'实验必须恰有两本书，实得 {len(self.books)}')
        book_keys = [book.book_key for book in self.books]
        if len(book_keys) != len(set(book_keys)):
            raise ValueError('book_key 必须唯一')
        source_hashes = [book.source_sha256 for book in self.books]
        if len(source_hashes) != len(set(source_hashes)):
            raise ValueError('两本书的 source_sha256 必须不同')
        if len(self.panels) != 10:
            raise ValueError(f'实验必须恰有 10 个配对 panel，实得 {len(self.panels)}')
        panel_keys = [panel.panel_key for panel in self.panels]
        if len(panel_keys) != len(set(panel_keys)):
            raise ValueError('panel_key 必须唯一')
        expected = set(book_keys)
        if {panel.book_key for panel in self.panels} != expected:
            raise ValueError('panel 的 book_key 必须恰好覆盖 books')
        for book_key in book_keys:
            rounds = sorted(
                panel.round for panel in self.panels if panel.book_key == book_key)
            if rounds != [1, 2, 3, 4, 5]:
                raise ValueError(f'{book_key}: rounds 必须恰好是 1..5，实得 {rounds}')
        order_counts = Counter(tuple(panel.run_order) for panel in self.panels)
        if order_counts != {('A', 'B'): 5, ('B', 'A'): 5}:
            raise ValueError(f'A/B 先后必须 5/5 平衡，实得 {dict(order_counts)}')
        runs = [run for panel in self.panels for run in panel.arms.values()]
        for name, values in (
            ('run_id', [run.run_id for run in runs]),
            ('artifact_path', [run.artifact_path for run in runs]),
            ('cache_namespace_sha256', [run.cache_namespace_sha256 for run in runs]),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f'20 次运行的 {name} 必须逐次唯一')
        return self


class PrivateCandidateMap(StrictModel):
    label: str = Field(pattern=r'^T[1-6]$')
    display_position: int = Field(ge=1, le=6)
    arm: Arm
    slot: str
    approach: str = Field(min_length=1)
    run_id: str = Field(pattern=_ID_RE)
    run_order: int = Field(ge=1, le=2)
    artifact_sha256: str = Field(pattern=_SHA256_RE)
    candidate_sha256: str = Field(pattern=_SHA256_RE)

    @model_validator(mode='after')
    def _label_is_the_display_position(self) -> 'PrivateCandidateMap':
        if self.label != f'T{self.display_position}':
            raise ValueError('T 标签必须与展示位置逐字一致')
        if self.slot not in HOOK_SLOTS:
            raise ValueError(f'未知 Stage6 slot {self.slot!r}')
        return self


class PrivatePanelMap(StrictModel):
    panel_id: str = Field(pattern=r'^P(?:0[1-9]|10)$')
    panel_key: str = Field(pattern=_ID_RE)
    book_key: str = Field(pattern=_ID_RE)
    round: int = Field(ge=1, le=5)
    candidates: list[PrivateCandidateMap]

    @model_validator(mode='after')
    def _six_candidates_are_balanced(self) -> 'PrivatePanelMap':
        if [row.label for row in self.candidates] != list(LABELS):
            raise ValueError(f'{self.panel_id}: candidates 必须按 T1..T6 排列')
        if Counter(row.arm for row in self.candidates) != {'A': 3, 'B': 3}:
            raise ValueError(f'{self.panel_id}: 必须恰有 A/B 各三条')
        for arm in ARMS:
            slots = [row.slot for row in self.candidates if row.arm == arm]
            if set(slots) != set(HOOK_SLOTS):
                raise ValueError(f'{self.panel_id}/{arm}: 必须恰好覆盖三个 slot')
        return self


class PrivateMapping(StrictModel):
    schema_version: Literal[1]
    experiment_id: str = Field(pattern=_ID_RE)
    protocol_sha256: str = Field(pattern=_SHA256_RE)
    panels: list[PrivatePanelMap]
    mapping_sha256: str = Field(pattern=_SHA256_RE)

    @model_validator(mode='after')
    def _global_positions_are_balanced(self) -> 'PrivateMapping':
        if [panel.panel_id for panel in self.panels] != [
                f'P{index:02d}' for index in range(1, 11)]:
            raise ValueError('mapping 必须恰有按展示顺序排列的 P01..P10')
        for position in range(1, 7):
            counts = Counter(
                panel.candidates[position - 1].arm for panel in self.panels)
            if counts != {'A': 5, 'B': 5}:
                raise ValueError(
                    f'展示位置 T{position} 的 A/B 必须 5/5，实得 {dict(counts)}')
        return self


class BlindCandidate(StrictModel):
    label: str = Field(pattern=r'^T[1-6]$')
    candidate_sha256: str = Field(pattern=_SHA256_RE)


class BlindPanel(StrictModel):
    panel_id: str = Field(pattern=r'^P(?:0[1-9]|10)$')
    file: str = Field(pattern=r'^P(?:0[1-9]|10)\.md$')
    file_sha256: str = Field(pattern=_SHA256_RE)
    candidates: list[BlindCandidate]


class BlindManifest(StrictModel):
    schema_version: Literal[1]
    experiment_id: str = Field(pattern=_ID_RE)
    protocol_sha256: str = Field(pattern=_SHA256_RE)
    mapping_sha256: str = Field(pattern=_SHA256_RE)
    panels: list[BlindPanel]
    output_sha256: str = Field(pattern=_SHA256_RE)


class ReviewPackPanel(BlindPanel):
    reference_sha256: str = Field(pattern=_SHA256_RE)


class ReviewPackManifest(StrictModel):
    schema_version: Literal[1]
    experiment_id: str = Field(pattern=_ID_RE)
    protocol_sha256: str = Field(pattern=_SHA256_RE)
    mapping_sha256: str = Field(pattern=_SHA256_RE)
    ranking_sha256: str = Field(pattern=_SHA256_RE)
    blind_manifest_sha256: str = Field(pattern=_SHA256_RE)
    panels: list[ReviewPackPanel]
    output_sha256: str = Field(pattern=_SHA256_RE)


class RankingPanel(StrictModel):
    panel_id: str = Field(pattern=r'^P(?:0[1-9]|10)$')
    ranking: list[str]


class RankingDraft(StrictModel):
    schema_version: Literal[1]
    experiment_id: str = Field(pattern=_ID_RE)
    mapping_sha256: str = Field(pattern=_SHA256_RE)
    reviewer: str = Field(min_length=1)
    completed_at: str = Field(pattern=_UTC_RE)
    panels: list[RankingPanel]

    @model_validator(mode='after')
    def _reviewer_is_not_blank(self) -> 'RankingDraft':
        if not self.reviewer.strip():
            raise ValueError('reviewer 不能只含空白')
        return self


class SealedRanking(RankingDraft):
    ranking_sha256: str = Field(pattern=_SHA256_RE)


class CandidateAssessment(StrictModel):
    label: str = Field(pattern=r'^T[1-6]$')
    fact_safety: FactSafety
    body_voice_match: int = Field(ge=0, le=10)
    adoptability: Adoptability
    quality_score: int = Field(ge=0, le=10)
    fact_reason: str | None

    @model_validator(mode='after')
    def _unclear_fact_has_a_reason(self) -> 'CandidateAssessment':
        if self.fact_safety == 'clear':
            if self.fact_reason is not None and not self.fact_reason.strip():
                raise ValueError('fact_reason 若填写就不能只含空白')
        elif self.fact_reason is None or not self.fact_reason.strip():
            raise ValueError('minor/severe/unknown 必须填写 fact_reason')
        return self


class AssessmentPanel(StrictModel):
    panel_id: str = Field(pattern=r'^P(?:0[1-9]|10)$')
    candidates: list[CandidateAssessment]


class ReviewDraft(StrictModel):
    schema_version: Literal[1]
    experiment_id: str = Field(pattern=_ID_RE)
    mapping_sha256: str = Field(pattern=_SHA256_RE)
    ranking_sha256: str = Field(pattern=_SHA256_RE)
    review_pack_sha256: str = Field(pattern=_SHA256_RE)
    reviewer: str = Field(min_length=1)
    completed_at: str = Field(pattern=_UTC_RE)
    panels: list[AssessmentPanel]

    @model_validator(mode='after')
    def _reviewer_is_not_blank(self) -> 'ReviewDraft':
        if not self.reviewer.strip():
            raise ValueError('reviewer 不能只含空白')
        return self


class SealedReview(ReviewDraft):
    review_sha256: str = Field(pattern=_SHA256_RE)


class DecisionManifest(StrictModel):
    schema_version: Literal[1]
    experiment_id: str = Field(pattern=_ID_RE)
    protocol_sha256: str = Field(pattern=_SHA256_RE)
    run_manifest_sha256: str = Field(pattern=_SHA256_RE)
    mapping_sha256: str = Field(pattern=_SHA256_RE)
    ranking_sha256: str = Field(pattern=_SHA256_RE)
    review_sha256: str = Field(pattern=_SHA256_RE)
    panels: int
    candidates_per_arm: int
    arm_wins: dict[str, int]
    per_book_arm_wins: dict[str, dict[str, int]]
    mean_advantage_a: float
    safety: dict[str, dict[str, int]]
    diagnostics: dict[str, Any]
    benefit_gates: dict[str, bool]
    deletion_gates: dict[str, bool]
    decision: Literal['keep_and_localize', 'delete_qualitative', 'inconclusive']
    output_sha256: str = Field(pattern=_SHA256_RE)


def _canonical(value: Any) -> bytes:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode='json')
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
        allow_nan=False,
    ).encode('utf-8')


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _bytes_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _model_payload(model: BaseModel, *, hash_field: str) -> dict[str, Any]:
    return model.model_dump(mode='json', exclude={hash_field})


def _check_self_hash(model: BaseModel, *, hash_field: str) -> None:
    expected = getattr(model, hash_field)
    actual = _sha256(_model_payload(model, hash_field=hash_field))
    if actual != expected:
        raise ValueError(f'{hash_field} mismatch: stored={expected} actual={actual}')


def _normal_path(path: str | os.PathLike[str]) -> Path:
    """路径身份恒用普通绝对路径；extended-length 只可活在 I/O 调用栈内。"""
    raw = os.fspath(path)
    if os.name == 'nt':
        if raw.startswith('\\\\?\\UNC\\'):
            raw = '\\\\' + raw[8:]
        elif raw.startswith('\\\\?\\'):
            raw = raw[4:]
    return Path(raw).expanduser().resolve(strict=False)


def _io_path(path: str | os.PathLike[str]) -> str:
    """给 Windows open/os.walk 临时加长路径前缀，不回传给 schema/manifest。"""
    raw = os.path.abspath(os.fspath(_normal_path(path)))
    if os.name != 'nt' or raw.startswith('\\\\?\\'):
        return raw
    if raw.startswith('\\\\'):
        return '\\\\?\\UNC\\' + raw[2:]
    return '\\\\?\\' + raw


def _read_file_bytes(path: str | os.PathLike[str]) -> bytes:
    with open(_io_path(path), 'rb') as stream:
        return stream.read()


def _file_sha256_io(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _stage_inventory(root: Path) -> list[tuple[str, str]]:
    """按 runner 同口径遍历 Stage0..5；返回值绝不泄漏 ``\\?\\`` 路径。"""
    normal_root = _normal_path(root)
    io_root = _io_path(normal_root)
    rows: list[tuple[str, str]] = []
    for stage in range(6):
        directory = normal_root / f'stage{stage}'
        io_directory = _io_path(directory)
        if not os.path.isdir(io_directory):
            raise ValueError(f'{normal_root} 缺 stage{stage}/，不能生成 phase-2 参考')
        for current, directories, files in os.walk(io_directory):
            directories.sort()
            files.sort()
            for name in files:
                full = os.path.join(current, name)
                rel = os.path.relpath(full, io_root).replace(os.sep, '/')
                rows.append((rel, _file_sha256_io(full)))
    return sorted(rows, key=lambda row: row[0])


def _load_json(path: Path, model: type[StrictModel]) -> Any:
    return model.model_validate(json.loads(_read_file_bytes(path).decode('utf-8')))


def _resolve_private(path: str, *, relative_to: Path) -> Path:
    value = Path(path).expanduser()
    return _normal_path(value if value.is_absolute() else relative_to / value)


def _require_ignored_output(path: Path, *, label: str) -> None:
    """正文/映射/人工意见只许落在 Git 工作树外或已 ignore 的路径。"""
    resolved = _normal_path(path)
    root = _normal_path(REPO_ROOT)
    try:
        relative = resolved.relative_to(root)
    except ValueError:
        # 工作树外没有被本仓 Git 收进去的可能，等价满足私有输出边界。
        return
    completed = subprocess.run(
        [
            'git', '-c', f'safe.directory={root.as_posix()}',
            'check-ignore', '--no-index', '--quiet', '--', relative.as_posix(),
        ],
        cwd=root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError(
            f'{label} 必须位于调用方指定的 Git ignored 目录或工作树外：{path}')


def _require_sibling_private(mapping_path: Path, public_dir: Path) -> None:
    mapping = _normal_path(mapping_path)
    public = _normal_path(public_dir)
    if mapping.parent.name != 'private' or mapping.parent.parent != public.parent:
        raise ValueError(
            'mapping 必须放在 blind/review 目录的 sibling private/ 中，'
            '不能与交给评审者的包同目录')


def _require_private_file(path: Path, *, label: str) -> None:
    resolved = _normal_path(path)
    if resolved.parent.name != 'private':
        raise ValueError(f'{label} 必须写入实验根的 private/ 目录')
    _require_ignored_output(resolved, label=label)


def _write_text_once(path: Path, text: str) -> None:
    if path.exists():
        if path.read_text(encoding='utf-8') == text:
            return
        raise FileExistsError(f'拒绝覆盖不同内容：{path}')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    temporary.write_text(text, encoding='utf-8', newline='\n')
    temporary.replace(path)


def _write_json_once(path: Path, value: BaseModel | dict[str, Any]) -> None:
    payload = value.model_dump(mode='json') if isinstance(value, BaseModel) else value
    if path.exists():
        current = json.loads(path.read_text(encoding='utf-8'))
        if _canonical(current) == _canonical(payload):
            return
        raise FileExistsError(f'拒绝覆盖不同 JSON：{path}')
    rendered = json.dumps(
        payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + '\n'
    _write_text_once(path, rendered)


@dataclass(frozen=True)
class LoadedCandidate:
    slot: str
    approach: str
    text: str
    sha256: str
    hard_findings: int
    reuses_source: bool


@dataclass(frozen=True)
class LoadedArm:
    spec: ArmRun
    candidates: tuple[LoadedCandidate, ...]
    retry_attempts: int
    all_dirty: bool


@dataclass(frozen=True)
class LoadedPanel:
    spec: PanelRun
    book: BookRun
    arms: dict[str, LoadedArm]


def _read_hook(run: ArmRun, *, manifest_dir: Path) -> LoadedArm:
    path = _resolve_private(run.artifact_path, relative_to=manifest_dir)
    raw = _read_file_bytes(path)
    actual = _bytes_sha256(raw)
    if actual != run.artifact_sha256:
        raise ValueError(
            f'{run.run_id}: artifact_sha256 mismatch stored={run.artifact_sha256} '
            f'actual={actual}')
    doc = json.loads(raw.decode('utf-8'))
    # 与 runner 的 ``_artifact_of`` 走同一个正式版本口。Hook 是普通 object
    # envelope：``_schema_version`` 在顶层，不是 ``payload`` wrapper；INVALID
    # 只是落点不同，仍必须按 STAGE6_HOOK 的当前 schema 解码。
    decoded = decode_artifact(STAGE6_HOOK, doc)
    artifact = Hook.model_validate(decoded.payload)
    candidates: list[LoadedCandidate] = []
    texts: list[str] = []
    for index, candidate in enumerate(artifact.candidates, 1):
        hard = sum(REGISTRY.get(finding.rule).sev == 'hard'
                   for finding in candidate.findings)
        text = candidate.text
        texts.append(text)
        candidates.append(LoadedCandidate(
            slot=HOOK_SLOTS[index - 1],
            approach=candidate.approach,
            text=text,
            sha256=_bytes_sha256(text.encode('utf-8')),
            hard_findings=hard,
            reuses_source=any(
                finding.rule == 'hook_reuses_source'
                for finding in candidate.findings),
        ))
    if len(set(texts)) != len(texts):
        raise ValueError(f'{run.run_id}: 三条候选正式字节重复，完整排名没有定义')
    all_dirty = all(candidate.hard_findings > 0 for candidate in candidates)
    expected_state = 'invalid_all_dirty' if all_dirty else 'live'
    if run.artifact_state != expected_state:
        raise ValueError(
            f'{run.run_id}: artifact_state={run.artifact_state!r}，'
            f'候选实际状态应为 {expected_state!r}')
    retry_attempts = len(artifact.provenance.candidate_retries)
    if retry_attempts != run.candidate_retries:
        raise ValueError(
            f'{run.run_id}: manifest candidate_retries={run.candidate_retries}，'
            f'artifact provenance={retry_attempts}')
    return LoadedArm(
        spec=run,
        candidates=tuple(candidates),
        retry_attempts=retry_attempts,
        all_dirty=all_dirty,
    )


def _adapt_runner_manifest(
    raw: dict[str, Any],
    *,
    protocol: ExperimentProtocol,
    protocol_raw_sha256: str,
) -> RunManifest:
    """把付费 runner 的审计宽表收成 review 所需的严格最小接口。"""
    if raw.get('_schema_version') != 1:
        raise ValueError('runner run-manifest _schema_version 必须是 1')
    experiment = raw.get('experiment') or {}
    if experiment.get('status') != 'complete' or raw.get('failure') is not None:
        raise ValueError('runner 尚未 complete 或带 failure，不能进入盲评')
    protocol_row = raw.get('protocol') or {}
    if protocol_row.get('machine_sha256') != protocol_raw_sha256:
        raise ValueError(
            'runner 没有绑定当前 machine protocol 原始字节 SHA-256')
    if protocol_row.get('name') != protocol.protocol_id:
        raise ValueError('runner protocol.name 与 machine protocol_id 不一致')
    books_raw = raw.get('books') or []
    books: list[dict[str, Any]] = []
    panels: list[dict[str, Any]] = []
    arm_name = {'full': 'A', 'no-stage3-qual': 'B'}
    for book in books_raw:
        book_key = str(book.get('book_key') or '')
        rounds = book.get('rounds') or []
        inventories: list[list[dict[str, str]]] = []
        upstream_hashes: list[str] = []
        for round_row in rounds:
            for arm_row in (round_row.get('arms') or {}).values():
                upstream = arm_row.get('upstream') or {}
                inventories.append(list(upstream.get('inventory') or []))
                upstream_hashes.append(str(upstream.get('stage0_to_stage5_sha256') or ''))
        if not inventories or any(value != inventories[0] for value in inventories[1:]):
            raise ValueError(f'{book_key}: 20 cells 的 upstream inventory 不一致')
        if len(set(upstream_hashes)) != 1:
            raise ValueError(f'{book_key}: 20 cells 的 Stage0..5 总指纹不一致')
        first_arm = next(
            iter((rounds[0].get('arms') or {}).values()), None) if rounds else None
        first_artifact = Path(str((first_arm or {}).get('artifact_path') or ''))
        reference_book = str(first_artifact.parent.parent) if first_artifact.name else ''
        books.append({
            'book_key': book_key,
            'source_sha256': upstream_hashes[0],
            'sid': str(book.get('sid') or ''),
            # 用实验 cell 内冻结的 Stage0..5 副本，不依赖稍后会被 archive 的 live 书目录。
            'source_book': reference_book,
            'upstream_inventory': inventories[0],
            'review_reference_path': None,
            'review_reference_sha256': None,
        })
        for round_row in rounds:
            source_arms = round_row.get('arms') or {}
            if set(source_arms) != set(arm_name):
                raise ValueError(
                    f'{book_key}/round-{round_row.get("round")}: 必须恰有 full 与 '
                    'no-stage3-qual 两臂')
            arms: dict[str, Any] = {}
            orders: list[tuple[int, str]] = []
            for source_name, target_name in arm_name.items():
                arm = source_arms[source_name]
                ledger = arm.get('ledger') or {}
                metadata = arm.get('run_metadata') or {}
                prompt = arm.get('prompt') or {}
                upstream = arm.get('upstream') or {}
                initial_fps = ledger.get('initial_request_fingerprints') or []
                if len(initial_fps) != 1:
                    raise ValueError(
                        f'{book_key}/round-{round_row.get("round")}/{source_name}: '
                        '必须恰有一次 initial request')
                if ledger.get('cached_rows') != 0 or metadata.get('local_cache_hits') != 0:
                    raise ValueError('实验 cell 出现 cache hit，拒绝把恢复跑当独立样本')
                order = int(metadata.get('order') or 0)
                orders.append((order, target_name))
                hook_rel = str(arm.get('hook_rel') or '')
                if hook_rel == 'stage6/hook.json':
                    state = 'live'
                elif hook_rel == 'stage6/hook.INVALID.json':
                    state = 'invalid_all_dirty'
                else:
                    raise ValueError(f'未知 Stage6 实验 artifact：{hook_rel!r}')
                effort = metadata.get('node_effort') or 'high'
                arms[target_name] = {
                    'run_id': f'{book_key}-r{int(round_row.get("round")):02d}-{target_name}',
                    'artifact_path': str(arm.get('artifact_path') or ''),
                    'artifact_sha256': str(arm.get('artifact_sha256') or ''),
                    'artifact_state': state,
                    'model': str(ledger.get('model') or metadata.get('model') or ''),
                    'reasoning_effort': str(effort),
                    'initial_prompt_sha256': str(prompt.get('combined_sha256') or ''),
                    'shared_material_sha256': str(
                        upstream.get('stage0_to_stage5_sha256') or ''),
                    'cache_namespace_sha256': _sha256({
                        'cache_dir': str(arm.get('cache_dir') or ''),
                    }),
                    'initial_calls': 1,
                    'candidate_retries': int(ledger.get('candidate_retries') or 0),
                }
            ordered = [arm for _order, arm in sorted(orders)]
            if sorted(order for order, _arm in orders) != [1, 2]:
                raise ValueError(f'{book_key}: paired run order 必须恰好为 1/2')
            panels.append({
                'panel_key': f'{book_key}-r{int(round_row.get("round")):02d}',
                'book_key': book_key,
                'round': int(round_row.get('round')),
                'run_order': ordered,
                'arms': arms,
            })
    totals = raw.get('totals') or {}
    if totals.get('completed_cells') != 20 or totals.get('initial_call_limit') != 20:
        raise ValueError('runner manifest 没有完成预登记的 20 个 cell')
    if int(totals.get('candidate_retries') or 0) > protocol.max_candidate_retries:
        raise ValueError('runner manifest 已超过候选自动重试暂停线')
    return RunManifest.model_validate({
        'schema_version': 1,
        'experiment_id': str(experiment.get('experiment_id') or ''),
        'protocol_sha256': _sha256(protocol),
        'books': books,
        'panels': panels,
    })


def _validate_runner_complete_seal(
    raw: dict[str, Any],
    *,
    runs_path: Path,
    runs_bytes: bytes,
) -> None:
    """runner 完成态必须由 sibling COMPLETE.json 绑定原始 manifest 字节。"""
    complete_path = _normal_path(runs_path).parent / 'COMPLETE.json'
    if not complete_path.is_file():
        raise ValueError('runner run-manifest 缺 sibling COMPLETE.json 完成封条')
    complete = json.loads(_read_file_bytes(complete_path).decode('utf-8'))
    if not isinstance(complete, dict):
        raise ValueError('COMPLETE.json 必须是 object')
    expected_keys = {
        'experiment_id', 'plan_sha256', 'run_manifest',
        'run_manifest_sha256', 'output_sha256',
    }
    if set(complete) != expected_keys:
        raise ValueError('COMPLETE.json 字段面与 runner v1 封条不一致')
    unsigned = {
        key: value for key, value in complete.items() if key != 'output_sha256'}
    if complete.get('output_sha256') != _sha256(unsigned):
        raise ValueError('COMPLETE.json output_sha256 自校验失败')
    experiment = raw.get('experiment') or {}
    if complete.get('experiment_id') != experiment.get('experiment_id'):
        raise ValueError('COMPLETE.json experiment_id 与 run-manifest 不一致')
    if complete.get('plan_sha256') != experiment.get('plan_sha256'):
        raise ValueError('COMPLETE.json plan_sha256 与 run-manifest 不一致')
    actual_sha = _bytes_sha256(runs_bytes)
    if complete.get('run_manifest_sha256') != actual_sha:
        raise ValueError(
            'COMPLETE.json run_manifest_sha256 与当前 run-manifest 原始字节不一致')
    named = Path(str(complete.get('run_manifest') or '')).expanduser()
    if not named.is_absolute():
        named = complete_path.parent / named
    if _normal_path(named) != _normal_path(runs_path):
        raise ValueError('COMPLETE.json run_manifest 没有指向当前输入文件')


def load_experiment(
    protocol_path: Path,
    runs_path: Path,
) -> tuple[ExperimentProtocol, RunManifest, list[LoadedPanel]]:
    protocol = _load_json(protocol_path, ExperimentProtocol)
    runs_bytes = _read_file_bytes(runs_path)
    raw_runs = json.loads(runs_bytes.decode('utf-8'))
    if isinstance(raw_runs, dict) and '_schema_version' in raw_runs:
        _validate_runner_complete_seal(
            raw_runs, runs_path=runs_path, runs_bytes=runs_bytes)
        runs = _adapt_runner_manifest(
            raw_runs,
            protocol=protocol,
            protocol_raw_sha256=_bytes_sha256(_read_file_bytes(protocol_path)),
        )
    else:
        runs = RunManifest.model_validate(raw_runs)
    protocol_sha = _sha256(protocol)
    if runs.protocol_sha256 != protocol_sha:
        raise ValueError(
            f'run manifest 绑定的 protocol_sha256={runs.protocol_sha256}，'
            f'当前协议是 {protocol_sha}')
    by_book = {book.book_key: book for book in runs.books}
    manifest_dir = _normal_path(runs_path).parent
    loaded: list[LoadedPanel] = []
    total_retries = 0
    for panel in runs.panels:
        book = by_book[panel.book_key]
        arms = {
            arm: _read_hook(panel.arms[arm], manifest_dir=manifest_dir)
            for arm in ARMS
        }
        # 跨臂同文同样使六候选完整排名无定义。不能静默去重成五条，也不能擅自付费补样。
        hashes = [candidate.sha256 for arm in ARMS for candidate in arms[arm].candidates]
        if len(hashes) != len(set(hashes)):
            raise ValueError(f'{panel.panel_key}: A/B 六候选中存在正式字节重复')
        total_retries += sum(arm.retry_attempts for arm in arms.values())
        loaded.append(LoadedPanel(
            spec=panel, book=book, arms=arms))
    if total_retries > protocol.max_candidate_retries:
        raise ValueError(
            f'自动候选重试累计 {total_retries} 次，超过协议上限 '
            f'{protocol.max_candidate_retries}；实验应暂停，不能进入盲评')
    return protocol, runs, loaded


def _new_mapping(
    protocol: ExperimentProtocol,
    runs: RunManifest,
    loaded: list[LoadedPanel],
    *,
    rng: Any,
) -> PrivateMapping:
    shuffled = list(loaded)
    rng.shuffle(shuffled)
    panels: list[dict[str, Any]] = []
    # 五组互补 block：每个展示位置在相邻两 panel 中由 A/B 各占一次；十张合计 5/5。
    position_arms: list[tuple[set[int], set[int]]] = []
    for _ in range(5):
        first_a = set(rng.sample(range(1, 7), 3))
        position_arms.append((first_a, set(range(1, 7)) - first_a))
    for display_index, panel in enumerate(shuffled, 1):
        first_a, second_a = position_arms[(display_index - 1) // 2]
        a_positions = first_a if display_index % 2 else second_a
        slots_by_arm: dict[str, list[str]] = {}
        for arm in ARMS:
            slots = list(HOOK_SLOTS)
            rng.shuffle(slots)
            slots_by_arm[arm] = slots
        next_slot = {'A': 0, 'B': 0}
        rows: list[dict[str, Any]] = []
        for position in range(1, 7):
            arm = 'A' if position in a_positions else 'B'
            slot = slots_by_arm[arm][next_slot[arm]]
            next_slot[arm] += 1
            slot_index = HOOK_SLOTS.index(slot)
            candidate = panel.arms[arm].candidates[slot_index]
            run = panel.spec.arms[arm]
            rows.append({
                'label': f'T{position}',
                'display_position': position,
                'arm': arm,
                'slot': slot,
                'approach': candidate.approach,
                'run_id': run.run_id,
                'run_order': panel.spec.run_order.index(arm) + 1,
                'artifact_sha256': run.artifact_sha256,
                'candidate_sha256': candidate.sha256,
            })
        panels.append({
            'panel_id': f'P{display_index:02d}',
            'panel_key': panel.spec.panel_key,
            'book_key': panel.spec.book_key,
            'round': panel.spec.round,
            'candidates': rows,
        })
    payload: dict[str, Any] = {
        'schema_version': SCHEMA_VERSION,
        'experiment_id': runs.experiment_id,
        'protocol_sha256': _sha256(protocol),
        'panels': panels,
    }
    payload['mapping_sha256'] = _sha256(payload)
    return PrivateMapping.model_validate(payload)


def _loaded_index(loaded: list[LoadedPanel]) -> dict[tuple[str, int], LoadedPanel]:
    return {(panel.spec.book_key, panel.spec.round): panel for panel in loaded}


def _validate_mapping(
    mapping: PrivateMapping,
    protocol: ExperimentProtocol,
    runs: RunManifest,
    loaded: list[LoadedPanel],
) -> None:
    _check_self_hash(mapping, hash_field='mapping_sha256')
    if mapping.experiment_id != runs.experiment_id:
        raise ValueError('mapping experiment_id 与 run manifest 不一致')
    if mapping.protocol_sha256 != _sha256(protocol):
        raise ValueError('mapping protocol_sha256 与当前协议不一致')
    index = _loaded_index(loaded)
    seen: set[tuple[str, int]] = set()
    for panel in mapping.panels:
        key = (panel.book_key, panel.round)
        if key in seen or key not in index:
            raise ValueError(f'{panel.panel_id}: mapping 指到重复或不存在的书/轮 {key}')
        seen.add(key)
        source = index[key]
        if panel.panel_key != source.spec.panel_key:
            raise ValueError(f'{panel.panel_id}: panel_key 失配')
        for row in panel.candidates:
            arm = source.arms[row.arm]
            candidate = arm.candidates[HOOK_SLOTS.index(row.slot)]
            run = source.spec.arms[row.arm]
            expected = {
                'approach': candidate.approach,
                'run_id': run.run_id,
                'run_order': source.spec.run_order.index(row.arm) + 1,
                'artifact_sha256': run.artifact_sha256,
                'candidate_sha256': candidate.sha256,
            }
            got = {name: getattr(row, name) for name in expected}
            if got != expected:
                raise ValueError(
                    f'{panel.panel_id}/{row.label}: mapping 与当前产物不一致')
    if seen != set(index):
        raise ValueError('mapping 没有覆盖全部十个书/轮')


def _candidate_sections(panel: PrivatePanelMap, source: LoadedPanel) -> list[str]:
    by_identity = {
        (arm, candidate.slot): candidate
        for arm, loaded_arm in source.arms.items()
        for candidate in loaded_arm.candidates
    }
    parts: list[str] = []
    for row in panel.candidates:
        candidate = by_identity[(row.arm, row.slot)]
        parts.extend([
            '',
            '---',
            '',
            f'## 候选 {row.label}',
            '',
            candidate.text,
        ])
    return parts


def _ranking_panel_markdown(panel: PrivatePanelMap, source: LoadedPanel) -> str:
    # Phase-1 故意不收共同参考：先看参考再排名会让“排名”混入后见事实核对，
    # 也无法机械证明用户真的按计划先做了完整排序。
    parts = [
        f'# 匿名盲评 {panel.panel_id}',
        '',
        '这里只做第一相：独立给六条候选排出完整 1~6 名。此目录不含共同参考材料；'
        '排名封存后，工具才会生成第二相复核包。不要从措辞猜测实验臂。',
    ]
    parts.extend(_candidate_sections(panel, source))
    return '\n'.join(parts).rstrip() + '\n'


def _review_panel_markdown(
    panel: PrivatePanelMap,
    source: LoadedPanel,
    reference: str,
) -> str:
    parts = [
        f'# 匿名复核 {panel.panel_id}',
        '',
        '完整排名已经封存。现在才可结合共同核对材料，逐候选复核事实安全、正文声线、'
        '可采用性与 0~10 质量分。这里仍不显示实验臂、槽位、策略或运行顺序。',
        '',
        '## 共同核对材料',
        '',
        reference.rstrip(),
    ]
    parts.extend(_candidate_sections(panel, source))
    return '\n'.join(parts).rstrip() + '\n'


_REVIEW_REFERENCE_START = '## 共同核对材料\n\n'
_REVIEW_REFERENCE_END = '\n\n---\n\n## 候选 T1\n\n'


def _embedded_reference_sha256(raw: bytes, *, panel_id: str) -> str:
    """从冻结 phase-2 面板本身复算共同参考指纹。

    ``build_review_pack`` 生成面板时会把参考材料收成 ``rstrip() + "\\n"``；
    后续验证必须读这份已经交给评审者的冻结字节，不能再让当前生产消费投影重建
    一份“理论上相同”的参考。否则 Stage3/5 契约演进会让历史裁决失去可复验性。
    """
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise ValueError(f'{panel_id}: phase-2 panel 不是 UTF-8') from exc
    if text.count(_REVIEW_REFERENCE_START) != 1:
        raise ValueError(f'{panel_id}: phase-2 共同参考起始标记缺失或重复')
    _prefix, rest = text.split(_REVIEW_REFERENCE_START, 1)
    if rest.count(_REVIEW_REFERENCE_END) != 1:
        raise ValueError(f'{panel_id}: phase-2 共同参考结束标记缺失或重复')
    reference, _candidates = rest.split(_REVIEW_REFERENCE_END, 1)
    frozen = reference.rstrip() + '\n'
    return _bytes_sha256(frozen.encode('utf-8'))


def _ranking_template(manifest: BlindManifest) -> dict[str, Any]:
    return {
        'schema_version': SCHEMA_VERSION,
        'experiment_id': manifest.experiment_id,
        'mapping_sha256': manifest.mapping_sha256,
        'reviewer': '',
        'completed_at': 'YYYY-MM-DDTHH:MM:SSZ',
        'panels': [{
            'panel_id': panel.panel_id,
            'ranking': ['', '', '', '', '', ''],
        } for panel in manifest.panels],
    }


def _review_template(manifest: ReviewPackManifest) -> dict[str, Any]:
    return {
        'schema_version': SCHEMA_VERSION,
        'experiment_id': manifest.experiment_id,
        'mapping_sha256': manifest.mapping_sha256,
        'ranking_sha256': manifest.ranking_sha256,
        'review_pack_sha256': manifest.output_sha256,
        'reviewer': '',
        'completed_at': 'YYYY-MM-DDTHH:MM:SSZ',
        'panels': [{
            'panel_id': panel.panel_id,
            'candidates': [{
                'label': candidate.label,
                'fact_safety': '<clear|minor|severe|unknown>',
                'body_voice_match': None,
                'adoptability': '<as_is|light_edit|reject>',
                'quality_score': None,
                'fact_reason': None,
            } for candidate in panel.candidates],
        } for panel in manifest.panels],
    }


def build_pack(
    protocol_path: Path,
    runs_path: Path,
    mapping_path: Path,
    blind_dir: Path,
    *,
    rng: Any | None = None,
) -> BlindManifest:
    _require_sibling_private(mapping_path, blind_dir)
    _require_ignored_output(mapping_path, label='mapping')
    _require_ignored_output(blind_dir, label='phase-1 blind 包')
    protocol, runs, loaded = load_experiment(protocol_path, runs_path)
    if mapping_path.exists():
        mapping = _load_json(mapping_path, PrivateMapping)
    else:
        mapping = _new_mapping(
            protocol, runs, loaded, rng=rng or secrets.SystemRandom())
        # 封映射必须先于任何 blind 文件；中途崩溃时可凭同一 mapping 幂等续出。
        _write_json_once(mapping_path, mapping)
    _validate_mapping(mapping, protocol, runs, loaded)
    by_key = _loaded_index(loaded)
    panel_rows: list[dict[str, Any]] = []
    for panel in mapping.panels:
        source = by_key[(panel.book_key, panel.round)]
        rendered = _ranking_panel_markdown(panel, source)
        panel_path = blind_dir / f'{panel.panel_id}.md'
        _write_text_once(panel_path, rendered)
        panel_rows.append({
            'panel_id': panel.panel_id,
            'file': panel_path.name,
            'file_sha256': _bytes_sha256(rendered.encode('utf-8')),
            'candidates': [{
                'label': candidate.label,
                'candidate_sha256': candidate.candidate_sha256,
            } for candidate in panel.candidates],
        })
    payload: dict[str, Any] = {
        'schema_version': SCHEMA_VERSION,
        'experiment_id': runs.experiment_id,
        'protocol_sha256': _sha256(protocol),
        'mapping_sha256': mapping.mapping_sha256,
        'panels': panel_rows,
    }
    payload['output_sha256'] = _sha256(payload)
    manifest = BlindManifest.model_validate(payload)
    _write_json_once(blind_dir / 'manifest.json', manifest)
    _write_json_once(blind_dir / 'ranking.template.json', _ranking_template(manifest))
    return manifest


def validate_pack(
    protocol_path: Path,
    runs_path: Path,
    mapping_path: Path,
    blind_manifest_path: Path,
) -> BlindManifest:
    protocol, runs, loaded = load_experiment(protocol_path, runs_path)
    mapping = _load_json(mapping_path, PrivateMapping)
    _validate_mapping(mapping, protocol, runs, loaded)
    manifest = _load_json(blind_manifest_path, BlindManifest)
    _check_self_hash(manifest, hash_field='output_sha256')
    if manifest.experiment_id != runs.experiment_id:
        raise ValueError('blind manifest experiment_id 失配')
    if manifest.protocol_sha256 != _sha256(protocol):
        raise ValueError('blind manifest protocol_sha256 失配')
    if manifest.mapping_sha256 != mapping.mapping_sha256:
        raise ValueError('blind manifest mapping_sha256 失配')
    if [row.panel_id for row in manifest.panels] != [
            row.panel_id for row in mapping.panels]:
        raise ValueError('blind manifest panel 顺序与 mapping 不一致')
    blind_dir = _normal_path(blind_manifest_path).parent
    for blind, private in zip(manifest.panels, mapping.panels):
        expected_candidates = [
            (row.label, row.candidate_sha256) for row in private.candidates]
        got_candidates = [
            (row.label, row.candidate_sha256) for row in blind.candidates]
        if got_candidates != expected_candidates:
            raise ValueError(f'{blind.panel_id}: 候选标签/指纹与 mapping 不一致')
        raw = _read_file_bytes(blind_dir / blind.file)
        if _bytes_sha256(raw) != blind.file_sha256:
            raise ValueError(f'{blind.panel_id}: panel 文件 SHA-256 失配')
    return manifest


def _validate_rankings(draft: RankingDraft, manifest: BlindManifest) -> None:
    if draft.experiment_id != manifest.experiment_id:
        raise ValueError('ranking experiment_id 失配')
    if draft.mapping_sha256 != manifest.mapping_sha256:
        raise ValueError('ranking mapping_sha256 失配')
    expected_panels = {panel.panel_id: {
        candidate.label for candidate in panel.candidates
    } for panel in manifest.panels}
    got_ids = [panel.panel_id for panel in draft.panels]
    if set(got_ids) != set(expected_panels) or len(got_ids) != len(set(got_ids)):
        raise ValueError('ranking 必须恰好覆盖 blind manifest 的十个 panel')
    for panel in draft.panels:
        if (len(panel.ranking) != 6
                or len(panel.ranking) != len(set(panel.ranking))
                or set(panel.ranking) != expected_panels[panel.panel_id]):
            raise ValueError(f'{panel.panel_id}: ranking 必须无重无漏完整排列六个标签')


def seal_ranking(
    blind_manifest_path: Path,
    draft_path: Path,
    out_path: Path,
) -> SealedRanking:
    _require_private_file(out_path, label='封存排名')
    manifest = _load_json(blind_manifest_path, BlindManifest)
    _check_self_hash(manifest, hash_field='output_sha256')
    draft = _load_json(draft_path, RankingDraft)
    _validate_rankings(draft, manifest)
    payload = draft.model_dump(mode='json')
    payload['ranking_sha256'] = _sha256(payload)
    sealed = SealedRanking.model_validate(payload)
    _write_json_once(out_path, sealed)
    return sealed


def load_sealed_ranking(path: Path, manifest: BlindManifest) -> SealedRanking:
    sealed = _load_json(path, SealedRanking)
    _check_self_hash(sealed, hash_field='ranking_sha256')
    _validate_rankings(
        RankingDraft.model_validate(
            sealed.model_dump(mode='json', exclude={'ranking_sha256'})),
        manifest,
    )
    return sealed


def _review_reference(book: BookRun, *, manifest_dir: Path) -> tuple[str, str]:
    """只在 phase-2 调用；phase-1 的加载链不得触碰共同参考正文。"""
    if book.review_reference_path is not None:
        path = _resolve_private(book.review_reference_path, relative_to=manifest_dir)
        raw = _read_file_bytes(path)
        actual = _bytes_sha256(raw)
        if actual != book.review_reference_sha256:
            raise ValueError(f'{book.book_key}: review reference SHA-256 失配')
        return raw.decode('utf-8'), actual

    assert book.source_book is not None and book.sid is not None
    root = _resolve_private(book.source_book, relative_to=manifest_dir)
    _validate_frozen_upstream_inventory(book, manifest_dir=manifest_dir)

    # 复用生产的唯一 Material 构造口，再投影成人工核对所需四块；历史实验的定性包
    # 刻意不进入参考，否则 phase-2 又会把被测变量提示给评审者。
    from novel.run import RunContext
    from novel.store import ArtifactStore
    from novel.stages.stage6 import hook

    ctx = RunContext.open(str(root.parent), book.sid, 'stage6.hook', paid=False)
    upstreams = hook._Upstreams(ctx, ArtifactStore(ctx))
    material = upstreams.mat
    shared = {
        '原作文案': material.source_hook,
        '完整故事事实': material.story_truth,
        '全书故事地图': list(material.story_map),
        '重写开篇正文': [
            {
                'output_ordinal': row.get('output_ordinal'),
                'text': row.get('text'),
            }
            for row in material.lead
        ],
    }
    rendered = (
        '以下四块是两条实验臂逐字共享的核对材料；不含 Stage3 定性画像。\n\n'
        + json.dumps(shared, ensure_ascii=False, indent=2, sort_keys=True)
        + '\n'
    )
    return rendered, _bytes_sha256(rendered.encode('utf-8'))


def _validate_frozen_upstream_inventory(
    book: BookRun,
    *,
    manifest_dir: Path,
) -> None:
    """只按字节核 runner 冻结副本；不得借当前 ArtifactStore 解释历史产物。"""
    if book.source_book is None or not book.upstream_inventory:
        return
    root = _resolve_private(book.source_book, relative_to=manifest_dir)
    expected = [(row.rel, row.sha256) for row in book.upstream_inventory]
    actual = _stage_inventory(root)
    if actual != expected:
        raise ValueError(
            f'{book.book_key}: source_book 的 Stage0..5 已偏离 runner 冻结 inventory，'
            '拒绝用另一版参考材料复核')


def build_review_pack(
    protocol_path: Path,
    runs_path: Path,
    mapping_path: Path,
    blind_manifest_path: Path,
    ranking_path: Path,
    review_dir: Path,
) -> ReviewPackManifest:
    """排名封存后才生成 phase-2；调用前目录中不应有任何参考材料。"""
    _require_sibling_private(mapping_path, review_dir)
    _require_ignored_output(review_dir, label='phase-2 review 包')
    if _normal_path(ranking_path).parent != _normal_path(mapping_path).parent:
        raise ValueError('封存排名与 mapping 必须同在 sibling private/ 目录')
    protocol, runs, loaded = load_experiment(protocol_path, runs_path)
    mapping = _load_json(mapping_path, PrivateMapping)
    _validate_mapping(mapping, protocol, runs, loaded)
    blind = validate_pack(
        protocol_path, runs_path, mapping_path, blind_manifest_path)
    ranking = load_sealed_ranking(ranking_path, blind)
    by_key = _loaded_index(loaded)
    manifest_dir = _normal_path(runs_path).parent
    references: dict[str, tuple[str, str]] = {}
    for book in runs.books:
        reference, _source_sha = _review_reference(
            book, manifest_dir=manifest_dir)
        # manifest 绑定的是实际嵌入并交给评审者的规范化字节；显式参考文件若有额外
        # 尾随空白，也不能让不可见字节成为日后无法从冻结 panel 复算的哈希输入。
        embedded_sha = _bytes_sha256(
            (reference.rstrip() + '\n').encode('utf-8'))
        references[book.book_key] = (reference, embedded_sha)
    rows: list[dict[str, Any]] = []
    for panel in mapping.panels:
        source = by_key[(panel.book_key, panel.round)]
        reference, reference_sha = references[panel.book_key]
        rendered = _review_panel_markdown(panel, source, reference)
        panel_path = review_dir / f'{panel.panel_id}.md'
        _write_text_once(panel_path, rendered)
        rows.append({
            'panel_id': panel.panel_id,
            'file': panel_path.name,
            'file_sha256': _bytes_sha256(rendered.encode('utf-8')),
            'reference_sha256': reference_sha,
            'candidates': [{
                'label': candidate.label,
                'candidate_sha256': candidate.candidate_sha256,
            } for candidate in panel.candidates],
        })
    payload: dict[str, Any] = {
        'schema_version': SCHEMA_VERSION,
        'experiment_id': runs.experiment_id,
        'protocol_sha256': _sha256(protocol),
        'mapping_sha256': mapping.mapping_sha256,
        'ranking_sha256': ranking.ranking_sha256,
        'blind_manifest_sha256': blind.output_sha256,
        'panels': rows,
    }
    payload['output_sha256'] = _sha256(payload)
    manifest = ReviewPackManifest.model_validate(payload)
    _write_json_once(review_dir / 'manifest.json', manifest)
    _write_json_once(review_dir / 'review.template.json', _review_template(manifest))
    return manifest


def validate_review_pack(
    protocol_path: Path,
    runs_path: Path,
    mapping_path: Path,
    blind_manifest_path: Path,
    ranking_path: Path,
    review_manifest_path: Path,
) -> ReviewPackManifest:
    protocol, runs, loaded = load_experiment(protocol_path, runs_path)
    mapping = _load_json(mapping_path, PrivateMapping)
    _validate_mapping(mapping, protocol, runs, loaded)
    blind = validate_pack(
        protocol_path, runs_path, mapping_path, blind_manifest_path)
    ranking = load_sealed_ranking(ranking_path, blind)
    manifest = _load_json(review_manifest_path, ReviewPackManifest)
    _check_self_hash(manifest, hash_field='output_sha256')
    expected_header = (
        runs.experiment_id,
        _sha256(protocol),
        mapping.mapping_sha256,
        ranking.ranking_sha256,
        blind.output_sha256,
    )
    got_header = (
        manifest.experiment_id,
        manifest.protocol_sha256,
        manifest.mapping_sha256,
        manifest.ranking_sha256,
        manifest.blind_manifest_sha256,
    )
    if got_header != expected_header:
        raise ValueError('phase-2 review manifest 的冻结输入绑定不一致')
    if [row.panel_id for row in manifest.panels] != [
            row.panel_id for row in mapping.panels]:
        raise ValueError('phase-2 panel 顺序与 mapping 不一致')
    review_dir = _normal_path(review_manifest_path).parent
    manifest_dir = _normal_path(runs_path).parent
    for book in runs.books:
        # phase-2 生成时已经用生产 Material 严格读过一次；封存后只核原始 inventory，
        # 不再要求今天的 freshness/consumed projection 接受昨天的 Stage3/5 产物。
        _validate_frozen_upstream_inventory(book, manifest_dir=manifest_dir)
    reference_by_book: dict[str, str] = {}
    for public, private in zip(manifest.panels, mapping.panels):
        expected_candidates = [
            (row.label, row.candidate_sha256) for row in private.candidates]
        got_candidates = [
            (row.label, row.candidate_sha256) for row in public.candidates]
        if got_candidates != expected_candidates:
            raise ValueError(f'{public.panel_id}: phase-2 候选标签/指纹失配')
        raw = _read_file_bytes(review_dir / public.file)
        if _bytes_sha256(raw) != public.file_sha256:
            raise ValueError(f'{public.panel_id}: phase-2 panel 文件 SHA-256 失配')
        reference_sha = _embedded_reference_sha256(
            raw, panel_id=public.panel_id)
        if public.reference_sha256 != reference_sha:
            raise ValueError(f'{public.panel_id}: phase-2 reference SHA 失配')
        prior = reference_by_book.setdefault(private.book_key, reference_sha)
        if prior != reference_sha:
            raise ValueError(
                f'{public.panel_id}: 同一本书的 phase-2 冻结参考不一致')
    return manifest


def _validate_review(
    draft: ReviewDraft,
    manifest: ReviewPackManifest,
    ranking: SealedRanking,
) -> None:
    if draft.experiment_id != manifest.experiment_id:
        raise ValueError('review experiment_id 失配')
    if draft.mapping_sha256 != manifest.mapping_sha256:
        raise ValueError('review mapping_sha256 失配')
    if draft.ranking_sha256 != ranking.ranking_sha256:
        raise ValueError('review 没有绑定已封存的 ranking_sha256')
    if draft.review_pack_sha256 != manifest.output_sha256:
        raise ValueError('review 没有绑定 phase-2 review_pack_sha256')
    if draft.reviewer != ranking.reviewer:
        raise ValueError('排名与复核必须由同一 reviewer 完成')
    expected = {panel.panel_id: {
        candidate.label for candidate in panel.candidates
    } for panel in manifest.panels}
    ids = [panel.panel_id for panel in draft.panels]
    if set(ids) != set(expected) or len(ids) != len(set(ids)):
        raise ValueError('review 必须恰好覆盖十个 panel')
    for panel in draft.panels:
        labels = [candidate.label for candidate in panel.candidates]
        if len(labels) != 6 or len(labels) != len(set(labels)) \
                or set(labels) != expected[panel.panel_id]:
            raise ValueError(f'{panel.panel_id}: review 必须逐候选无重无漏覆盖六个标签')


def seal_review(
    review_manifest_path: Path,
    ranking_path: Path,
    draft_path: Path,
    out_path: Path,
) -> SealedReview:
    _require_private_file(out_path, label='封存复核')
    if _normal_path(ranking_path).parent != _normal_path(out_path).parent:
        raise ValueError('封存排名与封存复核必须同在 private/ 目录')
    manifest = _load_json(review_manifest_path, ReviewPackManifest)
    _check_self_hash(manifest, hash_field='output_sha256')
    # SealedRanking 的结构检查只需一个同标签面的 phase-1 manifest；phase-2 已绑定
    # ranking_sha256，这里额外重算自身 hash 后即可使用。
    ranking = _load_json(ranking_path, SealedRanking)
    _check_self_hash(ranking, hash_field='ranking_sha256')
    draft = _load_json(draft_path, ReviewDraft)
    _validate_review(draft, manifest, ranking)
    payload = draft.model_dump(mode='json')
    payload['review_sha256'] = _sha256(payload)
    sealed = SealedReview.model_validate(payload)
    _write_json_once(out_path, sealed)
    return sealed


def load_sealed_review(
    path: Path,
    manifest: ReviewPackManifest,
    ranking: SealedRanking,
) -> SealedReview:
    sealed = _load_json(path, SealedReview)
    _check_self_hash(sealed, hash_field='review_sha256')
    _validate_review(
        ReviewDraft.model_validate(
            sealed.model_dump(mode='json', exclude={'review_sha256'})),
        manifest,
        ranking,
    )
    return sealed


def _decision_payload(
    protocol: ExperimentProtocol,
    runs: RunManifest,
    runs_path: Path,
    loaded: list[LoadedPanel],
    mapping: PrivateMapping,
    ranking: SealedRanking,
    review: SealedReview,
) -> dict[str, Any]:
    ranks = {panel.panel_id: panel for panel in ranking.panels}
    reviews = {panel.panel_id: {
        candidate.label: candidate for candidate in panel.candidates
    } for panel in review.panels}
    arm_wins = {'A': 0, 'B': 0}
    per_book: dict[str, dict[str, int]] = {
        book.book_key: {'A': 0, 'B': 0} for book in runs.books}
    quality_deltas: list[int] = []
    severe = {'A': 0, 'B': 0}
    unknown = {'A': 0, 'B': 0}
    voice: dict[str, list[int]] = {'A': [], 'B': []}
    quality: dict[str, list[int]] = {'A': [], 'B': []}
    adoptability: dict[str, Counter[str]] = {
        'A': Counter(), 'B': Counter(),
    }
    position_top = Counter()
    for panel in mapping.panels:
        rank = ranks[panel.panel_id].ranking
        winner_label = rank[0]
        winner = next(row for row in panel.candidates if row.label == winner_label)
        arm_wins[winner.arm] += 1
        per_book[panel.book_key][winner.arm] += 1
        position_top[winner_label] += 1
        best: dict[str, int] = {}
        for arm in ARMS:
            rows = [row for row in panel.candidates if row.arm == arm]
            scores = [reviews[panel.panel_id][row.label].quality_score for row in rows]
            best[arm] = max(scores)
        quality_deltas.append(best['A'] - best['B'])
        for row in panel.candidates:
            assessment = reviews[panel.panel_id][row.label]
            if assessment.fact_safety == 'severe':
                severe[row.arm] += 1
            if assessment.fact_safety == 'unknown':
                unknown[row.arm] += 1
            voice[row.arm].append(assessment.body_voice_match)
            quality[row.arm].append(assessment.quality_score)
            adoptability[row.arm][assessment.adoptability] += 1

    loaded_by_key = _loaded_index(loaded)
    mechanical: dict[str, dict[str, int]] = {
        arm: {
            'reuse_candidates': 0,
            'final_hard_findings': 0,
            'hard_candidates': 0,
            'all_dirty_runs': 0,
            'retry_attempts': 0,
        } for arm in ARMS
    }
    for panel in mapping.panels:
        source = loaded_by_key[(panel.book_key, panel.round)]
        for arm in ARMS:
            loaded_arm = source.arms[arm]
            mechanical[arm]['reuse_candidates'] += sum(
                candidate.reuses_source for candidate in loaded_arm.candidates)
            mechanical[arm]['final_hard_findings'] += sum(
                candidate.hard_findings for candidate in loaded_arm.candidates)
            mechanical[arm]['hard_candidates'] += sum(
                candidate.hard_findings > 0 for candidate in loaded_arm.candidates)
            mechanical[arm]['all_dirty_runs'] += int(loaded_arm.all_dirty)
            mechanical[arm]['retry_attempts'] += loaded_arm.retry_attempts

    safety: dict[str, dict[str, int]] = {
        'A': {'severe_fact_errors': severe['A'], **mechanical['A']},
        'B': {'severe_fact_errors': severe['B'], **mechanical['B']},
    }
    mean_advantage = sum(quality_deltas) / len(quality_deltas)
    known = unknown['A'] + unknown['B'] == 0
    a_no_worse = all(safety['A'][metric] <= safety['B'][metric]
                     for metric in SAFETY_METRICS)
    b_no_worse = all(safety['B'][metric] <= safety['A'][metric]
                     for metric in SAFETY_METRICS)
    benefit_gates = {
        'a_wins_at_least_8_of_10': arm_wins['A'] >= 8,
        'a_wins_at_least_3_of_5_each_book': all(
            row['A'] >= 3 for row in per_book.values()),
        'mean_advantage_a_at_least_1_0': mean_advantage >= 1.0,
        'a_safety_not_worse': a_no_worse,
        'fact_assessment_complete': known,
    }
    deletion_gates = {
        'a_wins_at_most_5_of_10': arm_wins['A'] <= 5,
        'a_wins_at_most_3_of_5_each_book': all(
            row['A'] <= 3 for row in per_book.values()),
        'mean_advantage_a_at_most_0_5': mean_advantage <= 0.5,
        'b_safety_not_worse': b_no_worse,
        'fact_assessment_complete': known,
    }
    if all(benefit_gates.values()):
        decision = 'keep_and_localize'
    elif all(deletion_gates.values()):
        decision = 'delete_qualitative'
    else:
        decision = 'inconclusive'
    return {
        'schema_version': SCHEMA_VERSION,
        'experiment_id': runs.experiment_id,
        'protocol_sha256': _sha256(protocol),
        # 对 runner 宽表绑定原始文件字节（含完整 ledger/prompt/upstream 审计格），
        # 而不是只绑定 review 适配后的最小投影。
        'run_manifest_sha256': _bytes_sha256(_read_file_bytes(runs_path)),
        'mapping_sha256': mapping.mapping_sha256,
        'ranking_sha256': ranking.ranking_sha256,
        'review_sha256': review.review_sha256,
        'panels': len(mapping.panels),
        'candidates_per_arm': len(mapping.panels) * 3,
        'arm_wins': arm_wins,
        'per_book_arm_wins': per_book,
        'mean_advantage_a': round(mean_advantage, 3),
        'safety': safety,
        'diagnostics': {
            'unknown_fact_assessments': unknown,
            'mean_body_voice_match': {
                arm: round(sum(voice[arm]) / len(voice[arm]), 3) for arm in ARMS},
            'mean_candidate_quality': {
                arm: round(sum(quality[arm]) / len(quality[arm]), 3) for arm in ARMS},
            'adoptability': {
                arm: dict(sorted(adoptability[arm].items())) for arm in ARMS},
            'display_position_top_counts': dict(sorted(position_top.items())),
            'quality_statistic': protocol.quality_statistic,
            'per_panel_best_quality_deltas_a_minus_b': quality_deltas,
            'hard_candidates': {
                arm: mechanical[arm]['hard_candidates'] for arm in ARMS},
        },
        'benefit_gates': benefit_gates,
        'deletion_gates': deletion_gates,
        'decision': decision,
    }


def decide(
    protocol_path: Path,
    runs_path: Path,
    mapping_path: Path,
    blind_manifest_path: Path,
    review_manifest_path: Path,
    ranking_path: Path,
    review_path: Path,
    out_path: Path,
) -> DecisionManifest:
    protocol, runs, loaded = load_experiment(protocol_path, runs_path)
    mapping = _load_json(mapping_path, PrivateMapping)
    _validate_mapping(mapping, protocol, runs, loaded)
    blind = validate_pack(
        protocol_path, runs_path, mapping_path, blind_manifest_path)
    ranking = load_sealed_ranking(ranking_path, blind)
    review_manifest = validate_review_pack(
        protocol_path, runs_path, mapping_path, blind_manifest_path,
        ranking_path, review_manifest_path)
    review = load_sealed_review(review_path, review_manifest, ranking)
    payload = _decision_payload(
        protocol, runs, runs_path, loaded, mapping, ranking, review)
    payload['output_sha256'] = _sha256(payload)
    decision = DecisionManifest.model_validate(payload)
    _write_json_once(out_path, decision)
    return decision


def validate_decision(
    protocol_path: Path,
    runs_path: Path,
    mapping_path: Path,
    blind_manifest_path: Path,
    review_manifest_path: Path,
    ranking_path: Path,
    review_path: Path,
    decision_path: Path,
) -> DecisionManifest:
    stored = _load_json(decision_path, DecisionManifest)
    _check_self_hash(stored, hash_field='output_sha256')
    protocol, runs, loaded = load_experiment(protocol_path, runs_path)
    mapping = _load_json(mapping_path, PrivateMapping)
    _validate_mapping(mapping, protocol, runs, loaded)
    blind = validate_pack(
        protocol_path, runs_path, mapping_path, blind_manifest_path)
    ranking = load_sealed_ranking(ranking_path, blind)
    review_manifest = validate_review_pack(
        protocol_path, runs_path, mapping_path, blind_manifest_path,
        ranking_path, review_manifest_path)
    review = load_sealed_review(review_path, review_manifest, ranking)
    expected_payload = _decision_payload(
        protocol, runs, runs_path, loaded, mapping, ranking, review)
    expected_payload['output_sha256'] = _sha256(expected_payload)
    expected = DecisionManifest.model_validate(expected_payload)
    if stored != expected:
        raise ValueError('decision manifest 与当前冻结输入重算结果不一致')
    return stored


def _common_inputs(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--protocol', required=True, type=Path)
    parser.add_argument('--runs', required=True, type=Path)
    parser.add_argument('--mapping', required=True, type=Path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    pack = commands.add_parser('pack', help='先封 mapping，再生成匿名六候选面板')
    _common_inputs(pack)
    pack.add_argument('--blind-dir', required=True, type=Path)

    validate = commands.add_parser('validate-pack', help='复算 mapping 与 blind 包全部哈希')
    _common_inputs(validate)
    validate.add_argument('--blind-manifest', required=True, type=Path)

    ranking = commands.add_parser(
        'seal-ranking', help='封存完整排名后才生成带共同参考的 phase-2 包')
    _common_inputs(ranking)
    ranking.add_argument('--blind-manifest', required=True, type=Path)
    ranking.add_argument('--draft', required=True, type=Path)
    ranking.add_argument('--out', required=True, type=Path)
    ranking.add_argument('--review-dir', required=True, type=Path)

    review = commands.add_parser('seal-review', help='绑定已封排名，封存逐候选人工复核')
    review.add_argument('--review-manifest', required=True, type=Path)
    review.add_argument('--ranking', required=True, type=Path)
    review.add_argument('--draft', required=True, type=Path)
    review.add_argument('--out', required=True, type=Path)

    decision = commands.add_parser('decide', help='按预登记阈值计算 text-free 裁决')
    _common_inputs(decision)
    decision.add_argument('--blind-manifest', required=True, type=Path)
    decision.add_argument('--review-manifest', required=True, type=Path)
    decision.add_argument('--ranking', required=True, type=Path)
    decision.add_argument('--review', required=True, type=Path)
    decision.add_argument('--out', required=True, type=Path)

    check = commands.add_parser('validate-decision', help='从全部冻结输入重算既有裁决')
    _common_inputs(check)
    check.add_argument('--blind-manifest', required=True, type=Path)
    check.add_argument('--review-manifest', required=True, type=Path)
    check.add_argument('--ranking', required=True, type=Path)
    check.add_argument('--review', required=True, type=Path)
    check.add_argument('--decision', required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == 'pack':
            result = build_pack(
                args.protocol, args.runs, args.mapping, args.blind_dir)
            summary = {
                'status': 'packed',
                'panels': len(result.panels),
                'mapping_sha256': result.mapping_sha256,
                'output_sha256': result.output_sha256,
            }
        elif args.command == 'validate-pack':
            result = validate_pack(
                args.protocol, args.runs, args.mapping, args.blind_manifest)
            summary = {
                'status': 'valid', 'panels': len(result.panels),
                'mapping_sha256': result.mapping_sha256,
                'output_sha256': result.output_sha256,
            }
        elif args.command == 'seal-ranking':
            result = seal_ranking(args.blind_manifest, args.draft, args.out)
            review_pack = build_review_pack(
                args.protocol, args.runs, args.mapping, args.blind_manifest,
                args.out, args.review_dir)
            summary = {
                'status': 'ranking_sealed',
                'ranking_sha256': result.ranking_sha256,
                'review_pack_sha256': review_pack.output_sha256,
            }
        elif args.command == 'seal-review':
            result = seal_review(
                args.review_manifest, args.ranking, args.draft, args.out)
            summary = {
                'status': 'review_sealed', 'review_sha256': result.review_sha256,
            }
        elif args.command == 'decide':
            result = decide(
                args.protocol, args.runs, args.mapping, args.blind_manifest,
                args.review_manifest, args.ranking, args.review, args.out)
            summary = {
                'status': 'decided', 'decision': result.decision,
                'output_sha256': result.output_sha256,
            }
        else:
            result = validate_decision(
                args.protocol, args.runs, args.mapping, args.blind_manifest,
                args.review_manifest, args.ranking, args.review, args.decision)
            summary = {
                'status': 'valid', 'decision': result.decision,
                'output_sha256': result.output_sha256,
            }
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, ValidationError, json.JSONDecodeError) as exc:
        print(json.dumps({
            'status': 'blocked',
            'error': f'{type(exc).__name__}: {exc}',
        }, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
