#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TODO-19：备份分级和从空目录恢复的可执行守卫。"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from novel import skill_contract
from tools.backup_snapshot import (
    BackupRefused, _code_allowed, code_files, create_archive, private_files,
    restore_archive,
)


def test_code_snapshot_restores_exact_bytes_and_refuses_nonempty_target():
    root = Path(tempfile.mkdtemp(prefix='backup-source-'))
    (root / 'tests' / 'fixtures').mkdir(parents=True)
    (root / '.env.example').write_bytes(b'DEEPSEEK_API_KEY=\n')
    (root / 'tests' / 'fixtures' / 'sample.json').write_bytes(b'{"synthetic":true}\n')
    archive = root.parent / (root.name + '.tar.gz')
    manifest = create_archive('code', [
        ('.env.example', root / '.env.example'),
        ('tests/fixtures/sample.json', root / 'tests' / 'fixtures' / 'sample.json'),
    ], archive)
    restored = Path(tempfile.mkdtemp(prefix='backup-restore-parent-')) / 'empty'
    got = restore_archive(archive, restored)
    assert got == manifest
    assert (restored / '.env.example').read_bytes() == b'DEEPSEEK_API_KEY=\n'
    assert (restored / 'tests/fixtures/sample.json').read_bytes() == b'{"synthetic":true}\n'
    assert not (restored / '.env').exists()
    with pytest.raises(BackupRefused, match='不是空目录'):
        restore_archive(archive, restored)


def test_restore_rejects_path_traversal_before_writing():
    from tools.backup_snapshot import _safe_rel

    for bad in ('../secret', '/absolute', 'a/../../secret'):
        with pytest.raises(BackupRefused, match='越界'):
            _safe_rel(bad)


def test_code_profile_keeps_public_config_but_excludes_secrets_and_local_settings():
    assert _code_allowed('.env.example')
    assert _code_allowed('web/.env')
    assert not _code_allowed('.env')
    assert not _code_allowed('.env.local')
    assert not _code_allowed('web/.env.production')
    assert not _code_allowed('.claude/settings.local.json')
    assert not _code_allowed('outputs/book/stage1/main_text.json')


def test_live_code_profile_contains_required_public_config_and_no_private_roots():
    repo = Path(__file__).resolve().parents[1]
    rels = {rel for rel, _path in code_files(repo)}
    assert {
        '.env.example', 'pyproject.toml', 'web/.env', 'web/package.json',
        'web/package-lock.json', 'tests/fixtures/manifest.json',
        '.workflow/skills/novel-pipeline-operator/SKILL.md',
        '.agents/skills/novel-pipeline-operator/SKILL.md',
        '.claude/skills/novel-pipeline-operator/SKILL.md',
    } <= rels
    contract_rels = {
        path.relative_to(repo).as_posix()
        for path in skill_contract.contract_files(repo)
    }
    assert contract_rels <= rels
    canonical_prefix = '.workflow/skills/novel-pipeline-operator/'
    assert any(rel.startswith(canonical_prefix + 'references/') for rel in rels)
    assert any(rel.startswith(canonical_prefix + 'scripts/') for rel in rels)
    assert any(rel.startswith(canonical_prefix + 'assets/') for rel in rels)
    for host in ('.agents', '.claude'):
        shim_prefix = f'{host}/skills/novel-pipeline-operator/'
        assert {rel for rel in rels if rel.startswith(shim_prefix)} == {
            shim_prefix + 'SKILL.md',
        }
    assert '.env' not in rels
    assert '.claude/settings.local.json' not in rels
    assert not any(rel.split('/', 1)[0] in {
        'outputs', 'Output', 'Novel_data', 'Test_data', '.llm_cache',
    } for rel in rels)


def test_private_profile_excludes_all_env_variants_and_regenerable_data():
    root = Path(tempfile.mkdtemp(prefix='backup-private-source-'))
    (root / 'book' / '_rounds').mkdir(parents=True)
    (root / '.env.production').write_text('SECRET=do-not-copy', encoding='utf-8')
    (root / 'book' / '_rounds' / 'ch01.json').write_text('{}', encoding='utf-8')
    (root / 'book' / '.llm_cache').mkdir()
    (root / 'book' / '.llm_cache' / 'response.json').write_text('{}', encoding='utf-8')

    rels = {rel for rel, _path in private_files(root)}
    assert rels == {'book/_rounds/ch01.json'}
