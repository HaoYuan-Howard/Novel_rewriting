#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage3 v2 机械基线的 schema、判据与真实节点接线。"""
from __future__ import annotations

import copy
import inspect
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts.invariants import STAGE3_MEASUREMENT_CONTRACT
from contracts.schema import ArtifactVersionError, decode_artifact, encode_artifact
from contracts.schema.stage1 import HookCopy, MainText
from contracts.schema.stage3 import (
    Measurable,
    StyleGrounding,
    StyleProvenance,
)
from novel import prov
from novel.core.measure import measure, measure_body
from novel.errors import ArtifactUnreadable
from novel.run import RunContext
from novel.stages.stage3 import checks, style
from novel.stages.stage3.measure import measure_all
from novel.store import ArtifactStore
from tests import factories

REL = 'stage3/style_grounding.json'


def _payload() -> dict:
    return copy.deepcopy(factories.stage3_artifact())


def _book(tmp_path: Path, sid: str = '303') -> tuple[Path, str]:
    book = tmp_path / f'合成机械基线_{sid}'
    stage5_book = factories.stage5_book()
    main = [
        {
            'chapter_ordinal': ordinal,
            'source_chapter_no': ordinal,
            'text': text,
        }
        for ordinal, text in sorted(stage5_book.source_text.items())
    ]
    hook = {'text': '一把银匙，一本被调换的账册，足以让旧日秘密重新开口。'}
    clean = ('\n'.join(stage5_book.source_text.values()) + '\n').encode('utf-8')
    (book / 'stage0').mkdir(parents=True)
    (book / 'stage1').mkdir()
    (book / 'stage0' / 'clean_text.txt').write_bytes(clean)
    for rel, payload in (
        ('stage1/main_text.json', main),
        ('stage1/hook_copy.json', hook),
    ):
        (book / rel).write_text(
            json.dumps(encode_artifact(rel, payload), ensure_ascii=False),
            encoding='utf-8',
        )
    return tmp_path, sid


def _ctx(base: Path, sid: str) -> RunContext:
    return RunContext.open(
        base,
        sid,
        'stage3.style',
        paid=False,
        write_ledger=False,
    )


def test_v2_top_level_is_only_measurable_and_free_provenance():
    artifact = StyleGrounding.model_validate(_payload())
    assert set(artifact.model_dump(by_alias=True)) == {'measurable', '_provenance'}
    assert set(StyleGrounding.model_fields) == {'measurable', 'provenance'}
    assert 'model' not in StyleProvenance.model_fields
    assert 'attempts' not in StyleProvenance.model_fields
    assert artifact.provenance.measurement_contract == STAGE3_MEASUREMENT_CONTRACT


def test_old_measurement_contract_stays_readable_for_freshness():
    raw = _payload()['_provenance']
    raw['measurement_contract'] = 'stage3-measurable-v0'
    assert StyleProvenance.model_validate(raw).measurement_contract.endswith('-v0')


@pytest.mark.parametrize('field', [
    'voice',
    'surface_habits',
    'hook_style',
    'chapter_styles',
])
def test_pseudo_v2_with_a_retired_qualitative_field_is_rejected(field: str):
    raw = _payload()
    raw[field] = {} if field in {'voice', 'hook_style'} else []
    wire = encode_artifact(REL, raw)
    decoded = decode_artifact(REL, wire)
    with pytest.raises(ValidationError, match='Extra inputs are not permitted'):
        StyleGrounding.model_validate(decoded.payload)


def test_v1_envelope_is_rejected_without_migration():
    raw = _payload()
    wire = {'_schema_version': 1, **raw}
    with pytest.raises(ArtifactVersionError) as caught:
        decode_artifact(REL, wire)
    assert caught.value.is_older_than_current is True


def test_v2_round_trip_preserves_the_full_mechanical_block():
    raw = _payload()
    decoded = decode_artifact(REL, encode_artifact(REL, raw))
    artifact = StyleGrounding.model_validate(decoded.payload)
    assert decoded.payload == raw
    assert artifact.measurable.model_dump() == raw['measurable']
    assert artifact.provenance.measurement_contract == (
        raw['_provenance']['measurement_contract'])


def test_body_and_hook_are_measured_independently():
    book = factories.stage5_book()
    main = MainText.model_validate([
        {
            'chapter_ordinal': ordinal,
            'source_chapter_no': ordinal,
            'text': text,
        }
        for ordinal, text in sorted(book.source_text.items())
    ])
    hook = HookCopy(text='短文案。\n\n「单独测。」')
    got = measure_all(main, hook)
    assert got.body == measure_body(main)
    assert got.hook == measure(hook.text)
    assert got.body.chars != got.hook.chars


def test_body_bands_is_the_exact_body_prefix_without_changing_full_stage3_bytes():
    from novel.core.bands import bands, body_bands

    measurable = Measurable.model_validate(_payload()['measurable'])
    body = body_bands(measurable.body)
    assert body == {
        '句子形态': '短句',
        '段落形态': '长段',
        '对白形态': '对白稀少（引号体：「」）',
        '章篇幅': '中等篇幅章',
        '章间形态差异': '各章句长形态接近',
    }
    assert bands(measurable) == {
        **body,
        '文案段形态': '中等长度句；相对正文更长',
    }


def test_checks_emit_exactly_one_mechanical_row():
    raw = _payload()
    measured = Measurable.model_validate(raw['measurable'])
    outcomes = checks.check(raw, measured_now=measured)
    assert [outcome.rule for outcome in outcomes] == ['measurable_drift']
    assert all(not outcome.findings and outcome.unver_why is None for outcome in outcomes)
    assert all(outcome.scanned for outcome in outcomes)


def test_mechanical_drift_reports_the_exact_leaf():
    raw = _payload()
    measured = Measurable.model_validate(raw['measurable'])
    raw['measurable']['hook']['chars'] += 1
    [drift_outcome] = checks.check(raw, measured_now=measured)
    assert drift_outcome.findings
    assert 'chars' in drift_outcome.findings[0].evidence


def test_incomplete_mechanical_shape_is_artifact_unreadable(tmp_path: Path):
    base, sid = _book(tmp_path)
    ctx = _ctx(base, sid)
    style.run_style(ctx)
    path = ctx.novel_dir / REL
    wire = json.loads(path.read_text(encoding='utf-8'))
    del wire['measurable']['hook']['chars']
    path.write_text(json.dumps(wire, ensure_ascii=False), encoding='utf-8')
    with pytest.raises(ArtifactUnreadable, match='schema v2'):
        style.run_check_only(_ctx(base, sid))


def test_default_run_is_free_has_no_llm_parameter_and_lands_v2(tmp_path: Path):
    base, sid = _book(tmp_path)
    ctx = _ctx(base, sid)
    assert list(inspect.signature(style.run_style).parameters) == ['ctx']
    assert not hasattr(style, 'LlmClient')

    artifact = style.run_style(ctx)
    assert isinstance(artifact, StyleGrounding)
    wire = json.loads((ctx.novel_dir / REL).read_text(encoding='utf-8'))
    assert wire['_schema_version'] == 2
    assert set(wire) == {'_schema_version', 'measurable', '_provenance'}
    source = wire['_provenance']
    assert source['measurement_contract'] == STAGE3_MEASUREMENT_CONTRACT
    assert set(source['inputs']) == {
        'stage1/main_text.json',
        'stage1/hook_copy.json',
    }
    assert 'model' not in source and 'attempts' not in source and 'prompts' not in source
    assert ctx.ledger.usage() is None

    report = style.run_check_only(_ctx(base, sid))
    assert report.passed
    assert [row.rule for row in report.rules] == ['measurable_drift']


def test_measure_only_does_not_land_an_artifact(tmp_path: Path):
    base, sid = _book(tmp_path)
    ctx = _ctx(base, sid)
    measured = style.run_measure_only(ctx)
    assert measured.body.chars > measured.hook.chars
    assert not (ctx.novel_dir / REL).exists()
    assert ctx.ledger.usage() is None


def test_default_free_rerun_repairs_mechanical_drift(tmp_path: Path):
    base, sid = _book(tmp_path)
    ctx = _ctx(base, sid)
    style.run_style(ctx)

    path = ctx.novel_dir / REL
    wire = json.loads(path.read_text(encoding='utf-8'))
    wire['measurable']['hook']['chars'] += 7
    path.write_text(json.dumps(wire, ensure_ascii=False), encoding='utf-8')

    before = style.run_check_only(_ctx(base, sid))
    assert next(row for row in before.rules if row.rule == 'measurable_drift').status == 'hit'

    written = style.run_style(_ctx(base, sid))
    after = style.run_check_only(_ctx(base, sid))
    assert after.passed
    assert written.provenance.measurement_contract == STAGE3_MEASUREMENT_CONTRACT
    disk = json.loads(path.read_text(encoding='utf-8'))
    assert set(disk) == {'_schema_version', 'measurable', '_provenance'}
    assert 'model' not in disk['_provenance']


def _write_legacy_live(ctx: RunContext, rel: str) -> bytes:
    wire = {'_schema_version': 1, **_payload()}
    raw = json.dumps(wire, ensure_ascii=False).encode('utf-8')
    path = ctx.novel_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


@pytest.mark.parametrize('with_invalid', [False, True])
def test_default_run_rebuilds_v1_without_putting_it_in_current_rounds(
    tmp_path: Path,
    with_invalid: bool,
):
    base, sid = _book(tmp_path)
    ctx = _ctx(base, sid)
    _write_legacy_live(ctx, REL)
    if with_invalid:
        _write_legacy_live(ctx, 'stage3/style_grounding.INVALID.json')

    style.run_style(ctx)

    wire = json.loads((ctx.novel_dir / REL).read_text(encoding='utf-8'))
    assert wire['_schema_version'] == 2
    assert not (ctx.novel_dir / 'stage3/style_grounding.INVALID.json').exists()
    assert not (ctx.novel_dir / 'stage3/_rounds').exists()


def test_v1_formal_and_invalid_rebuild_is_transactional(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    base, sid = _book(tmp_path)
    ctx = _ctx(base, sid)
    formal_before = _write_legacy_live(ctx, REL)
    invalid_rel = 'stage3/style_grounding.INVALID.json'
    invalid_before = _write_legacy_live(ctx, invalid_rel)

    def fail_after_replace(self, rel, artifact):
        raise RuntimeError('synthetic freshness failure')

    monkeypatch.setattr(ArtifactStore, '_assert_self_fresh', fail_after_replace)
    with pytest.raises(RuntimeError, match='synthetic freshness failure'):
        style.run_style(ctx)

    assert (ctx.novel_dir / REL).read_bytes() == formal_before
    assert (ctx.novel_dir / invalid_rel).read_bytes() == invalid_before
    assert not (ctx.novel_dir / 'stage3/_rounds').exists()


def test_current_v2_invalid_is_refused_not_archived(tmp_path: Path):
    base, sid = _book(tmp_path)
    ctx = _ctx(base, sid)
    style.run_style(ctx)
    formal = ctx.novel_dir / REL
    invalid = ctx.novel_dir / 'stage3/style_grounding.INVALID.json'
    invalid.write_bytes(formal.read_bytes())
    before = formal.read_bytes()

    with pytest.raises(ArtifactUnreadable, match='Stage3 v2 不存在'):
        style.run_style(_ctx(base, sid))

    assert formal.read_bytes() == before
    assert invalid.is_file()
    assert not (ctx.novel_dir / 'stage3/_rounds').exists()


def test_measurement_contract_participates_in_freshness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import novel.consumption as consumption

    base, sid = _book(tmp_path)
    ctx = _ctx(base, sid)
    style.run_style(ctx)
    store = ArtifactStore(_ctx(base, sid))
    assert store.freshness(REL).state == 'fresh'

    monkeypatch.setattr(
        consumption,
        'STAGE3_MEASUREMENT_CONTRACT',
        'stage3-measurable-v-next',
    )
    report = store.freshness(REL)
    assert (report.state, report.reason) == ('stale', 'consumed_changed')


def test_retired_qualitative_production_symbols_are_absent():
    import contracts.schema.stage3 as schema

    retired_schema = {
        'Voice',
        'HookStyle',
        'ChapterStyle',
        'StyleResponse',
        'ChapterStyleResponse',
        'HabitRepairResponse',
        'StyleAttempt',
        'StyleEdit',
    }
    retired_runtime = {
        'build_prompt',
        'request_chapter_styles',
        'run_repair_surface_habits',
        'run_revalidate',
        'run_apply_edit',
        'remeasured',
        'run_remeasure',
    }
    assert not (retired_schema & set(vars(schema)))
    assert not (retired_runtime & set(vars(style)))
