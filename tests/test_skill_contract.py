#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Canonical operator skill 整树哈希契约。"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from novel import skill_contract


def _tree(root: Path) -> Path:
    skill = root / skill_contract.SKILL_REL
    for name in skill_contract.RESOURCE_DIRS:
        (skill / name).mkdir(parents=True, exist_ok=True)
    (skill / 'SKILL.md').write_bytes(b'---\nname: demo\n---\nbody\n')
    (skill / 'references' / 'workflow.md').write_bytes(b'workflow\n')
    (skill / 'scripts' / 'run.py').write_bytes(b'print(1)\n')
    (skill / 'assets' / 'template.md').write_bytes(b'template\n')
    return skill


def test_package_hash_covers_content_and_relative_path(tmp_path):
    skill = _tree(tmp_path)
    baseline = skill_contract.package_sha256(tmp_path)
    target = skill / 'references' / 'workflow.md'
    target.write_bytes(b'changed\n')
    assert skill_contract.package_sha256(tmp_path) != baseline

    target.write_bytes(b'workflow\n')
    target.rename(skill / 'references' / 'renamed.md')
    assert skill_contract.package_sha256(tmp_path) != baseline


def test_package_hash_ignores_mtime_creation_order_and_discovery_shim(tmp_path):
    skill = _tree(tmp_path)
    baseline = skill_contract.package_sha256(tmp_path)
    for path in reversed(skill_contract.contract_files(tmp_path)):
        os.utime(path, (1_700_000_000, 1_700_000_000))
    shim = tmp_path / '.agents' / 'skills' / 'novel-pipeline-operator' / 'SKILL.md'
    shim.parent.mkdir(parents=True)
    shim.write_text('another discovery pointer', encoding='utf-8')
    assert skill_contract.package_sha256(tmp_path) == baseline


def test_package_hash_does_not_normalize_line_endings(tmp_path):
    skill = _tree(tmp_path)
    baseline = skill_contract.package_sha256(tmp_path)
    (skill / 'references' / 'workflow.md').write_bytes(b'workflow\r\n')
    assert skill_contract.package_sha256(tmp_path) != baseline


@pytest.mark.parametrize('missing', [
    'SKILL.md', 'references', 'scripts', 'assets',
])
def test_missing_required_skill_member_is_closed(tmp_path, missing):
    skill = _tree(tmp_path)
    target = skill / missing
    if target.is_dir():
        for child in target.iterdir():
            child.unlink()
        target.rmdir()
    else:
        target.unlink()
    with pytest.raises(skill_contract.SkillContractError):
        skill_contract.package_sha256(tmp_path)


def test_symlink_cannot_escape_the_canonical_contract(tmp_path):
    skill = _tree(tmp_path)
    external = tmp_path / 'external.md'
    external.write_text('outside', encoding='utf-8')
    link = skill / 'references' / 'outside.md'
    try:
        link.symlink_to(external)
    except OSError:
        pytest.skip('当前 Windows 账户不能创建 symlink')
    with pytest.raises(skill_contract.SkillContractError, match='外指链接'):
        skill_contract.package_sha256(tmp_path)
