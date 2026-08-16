from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from contracts.schema import WriterContext, encode_artifact
from tests.factories import stage5_book
from tools import restricted_fc08 as F


def _base(tmp_path: Path, *, sid: str = 'sid', chapters: int = 4) -> Path:
    base = tmp_path / 'base'
    book_dir = base / sid
    (book_dir / 'stage0').mkdir(parents=True)
    (book_dir / 'stage0' / 'clean_text.txt').write_text('same root', encoding='utf-8')
    source = stage5_book().ctx
    first = source['chapters'][0]
    rows = []
    for ordinal in range(1, chapters + 1):
        row = copy.deepcopy(first)
        row['output_ordinal'] = ordinal
        row['from_source_ordinals'] = [ordinal]
        rows.append(row)
    provenance = {
        'code': '0' * 10,
        'root_clean_sha1': '0' * 40,
        'inputs': {
            'stage4/plan_chapters.json': {'file': '1' * 10, 'consumed': '2' * 10},
        },
    }
    context = WriterContext.model_validate({
        'world_facts': source['world_facts'], 'chapters': rows,
        '_provenance': provenance,
    })
    target = book_dir / 'stage4' / 'writer_context.json'
    target.parent.mkdir()
    target.write_text(json.dumps(
        encode_artifact('stage4/writer_context.json', context.model_dump(
            by_alias=True, mode='json')),
        ensure_ascii=False), encoding='utf-8')
    return base


def test_validation_freezes_composite_arm_targets_and_cap(tmp_path):
    experiment = F.validate('sid', _base(tmp_path))
    assert experiment.chapters == experiment.logical_minimum == 4
    assert experiment.wire_attempt_limit == 24
    assert experiment.cache_dir == (
        experiment.base / '_service' / 'paid_experiment_cache' / 'fc08' / 'sid')
    assert F.production_argv(experiment) == [
        'run', 'stage5.write', 'sid', '--base', str(experiment.base),
        '--deep', '1,2,3', '--shallow-rest', '--yes',
    ]


def test_validation_rejects_existing_selected_target_and_nonempty_cache(tmp_path):
    base = _base(tmp_path)
    target = base / 'sid' / 'stage5' / 'deep' / 'ch01.json'
    target.parent.mkdir(parents=True)
    target.write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='stage5 已有文件'):
        F.validate('sid', base)
    target.unlink()
    cache = base / '_service' / 'paid_experiment_cache' / 'fc08' / 'sid'
    cache.mkdir(parents=True)
    (cache / 'old').write_text('x', encoding='utf-8')
    with pytest.raises(ValueError, match='缓存不是空'):
        F.validate('sid', base)


@pytest.mark.parametrize('rel', [
    'stage5/shallow/ch01.json',       # selected chapter in the wrong arm
    'stage5/deep/ch04.INVALID.json',  # selected chapter in the wrong arm
    'stage5/review_1.json',
    'stage5/semantic_1.json',
    'stage5/trim_audit_ch01.json',
])
def test_validation_rejects_any_stage5_output_not_only_expected_arm(
        tmp_path, rel):
    base = _base(tmp_path)
    target = base / 'sid' / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='仅含 stage0～4'):
        F.validate('sid', base)


def test_run_sets_fixed_environment_and_calls_real_cli_dispatch_shape(
        tmp_path, monkeypatch):
    experiment = F.validate('sid', _base(tmp_path))
    seen = []
    from novel.cli import main as cli_main

    def fake_main(argv):
        seen.append({
            'argv': list(argv),
            'model': os.environ.get('DEEPSEEK_MODEL'),
            'thinking': os.environ.get('DEEPSEEK_THINKING'),
            'retry': os.environ.get('DEEPSEEK_MAX_RETRIES'),
            'cache': os.environ.get('NOVEL_CACHE_DIR'),
        })
        return 1

    monkeypatch.setattr(cli_main, 'main', fake_main)
    monkeypatch.setenv('DEEPSEEK_MODEL', 'ambient-model')
    monkeypatch.setenv('DEEPSEEK_MAX_RETRIES', '99')
    assert F.run(experiment) == 1
    assert seen == [{
        'argv': F.production_argv(experiment),
        'model': 'deepseek-v4-pro',
        'thinking': 'on',
        'retry': '0',
        'cache': str(experiment.cache_dir),
    }]
    assert os.environ['DEEPSEEK_MODEL'] == 'ambient-model'
    assert os.environ['DEEPSEEK_MAX_RETRIES'] == '99'
    assert '--effort' not in seen[0]['argv']


def test_frozen_command_is_self_contained_wrapper_with_execute_and_yes(tmp_path):
    experiment = F.validate('sid', _base(tmp_path))
    command = F.command_of(experiment)
    assert str(Path(F.__file__).resolve()) in command
    assert command[-2:] == ['--execute', '--yes']
    assert '--deep' not in command  # the wrapper owns this invariant

    from novel.cli import main as cli_main
    with pytest.raises(SystemExit):
        cli_main._parser().parse_args([
            'run', 'stage5.write', 'sid', '--base', str(experiment.base),
            '--effort', 'medium', '--yes'])


def test_preview_never_dispatches_and_execute_requires_yes(
        tmp_path, monkeypatch, capsys):
    base = _base(tmp_path)
    monkeypatch.setattr(F, 'run', lambda *_: pytest.fail('preview must not run'))
    assert F.main(['--sid', 'sid', '--base', str(base)]) == 0
    text = capsys.readouterr().out
    assert 'DEEPSEEK' not in text  # no key/value leak; fixed facts are human labels
    assert 'retry=0' in text and 'frozen_command=' in text
    with pytest.raises(SystemExit):
        F.main(['--sid', 'sid', '--base', str(base), '--execute'])
