"""Load and bind a calibrated evaluator profile to the exact runtime surface."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable

from pydantic import BaseModel

from contracts.schema.evaluation import (
    Ability, EvaluatorFingerprint, EvaluatorProfile, GroundTruthManifest,
)
from novel.errors import TerminalState
from novel.quality.evaluator import capability_enabled

PROFILE_ENV = 'NOVEL_EVALUATOR_PROFILE'
MANIFEST_ENV = 'NOVEL_GROUND_TRUTH_MANIFEST'


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


ADVISORY_DATASET_SHA256 = canonical_sha256({
    'mode': 'advisory',
    'ground_truth_dataset': None,
})


def _read_model(path: str | None, model_cls: type[BaseModel], label: str) -> BaseModel:
    if not path:
        raise TerminalState(f'{label} 未配置；设置对应本地路径后才可启用自动语义裁决')
    target = Path(path)
    if not target.is_file():
        raise TerminalState(f'{label} 不存在：{target}')
    try:
        return model_cls.model_validate_json(target.read_text(encoding='utf-8'))
    except Exception as exc:
        raise TerminalState(f'{label} 不符合当前契约：{target}: {exc}') from exc


def runtime_fingerprint(
    *,
    manifest: GroundTruthManifest,
    model: str,
    prompts: Iterable[str],
    schemas: Iterable[type[BaseModel]],
    decoder_identity: Any,
) -> EvaluatorFingerprint:
    return EvaluatorFingerprint(
        dataset_sha256=manifest.dataset_sha256,
        model=model,
        prompt_sha256=canonical_sha256(list(prompts)),
        schema_sha256=canonical_sha256([
            schema.model_json_schema(mode='validation') for schema in schemas]),
        decoder_sha256=canonical_sha256(decoder_identity),
    )


def advisory_fingerprint(
    *,
    model: str,
    prompts: Iterable[str],
    schemas: Iterable[type[BaseModel]],
    decoder_identity: Any,
) -> EvaluatorFingerprint:
    """Bind an uncalibrated advisory run to its exact request surface.

    Advisory evidence has no ground-truth dataset and can therefore never be
    admitted as an automated verdict.  A stable sentinel keeps the runtime
    fingerprint honest and structurally complete without inventing a profile
    or borrowing a test manifest.
    """
    return EvaluatorFingerprint(
        dataset_sha256=ADVISORY_DATASET_SHA256,
        model=model,
        prompt_sha256=canonical_sha256(list(prompts)),
        schema_sha256=canonical_sha256([
            schema.model_json_schema(mode='validation') for schema in schemas]),
        decoder_sha256=canonical_sha256(decoder_identity),
    )


def require_enabled(
    ability: Ability,
    *,
    model: str,
    prompts: Iterable[str],
    schemas: Iterable[type[BaseModel]],
    decoder_identity: Any,
) -> tuple[EvaluatorProfile, EvaluatorFingerprint]:
    """Fail before any paid request when profile/manifest/capability does not match."""
    profile = _read_model(os.getenv(PROFILE_ENV), EvaluatorProfile, PROFILE_ENV)
    manifest = _read_model(
        os.getenv(MANIFEST_ENV), GroundTruthManifest, MANIFEST_ENV)
    assert isinstance(profile, EvaluatorProfile)
    assert isinstance(manifest, GroundTruthManifest)
    dataset_payload = {
        'dataset_id': manifest.dataset_id,
        'rubric_sha256': manifest.rubric_sha256,
        'cases': [case.model_dump(mode='json') for case in manifest.cases],
    }
    actual_dataset_sha = canonical_sha256(dataset_payload)
    if actual_dataset_sha != manifest.dataset_sha256:
        raise TerminalState(
            'ground-truth manifest dataset_sha256 与逐例内容不一致；'
            '拒绝用被篡改或未重算身份的数据启用自动裁决')
    current = runtime_fingerprint(
        manifest=manifest, model=model, prompts=prompts, schemas=schemas,
        decoder_identity=decoder_identity)
    enabled, why = capability_enabled(profile, ability, current)
    if not enabled:
        raise TerminalState(
            f'{ability} 自动语义裁决未启用：{why}。本能力保持人工-only；未发起模型调用')
    return profile, current
