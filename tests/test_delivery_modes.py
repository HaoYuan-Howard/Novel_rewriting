"""真实干净代码副本中的人工全链与自动免费收尾，不绕过 dirty epoch 守卫。"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


pytestmark = pytest.mark.real_code_version
ROOT = Path(__file__).resolve().parents[1]


def _git(repo: Path, *args: str) -> bytes:
    return subprocess.run(
        ['git', '-c', f'safe.directory={repo.as_posix()}', *args], cwd=repo,
        capture_output=True, check=True, timeout=60).stdout


def freeze_repo(target: Path) -> str:
    """仅复制当前 Git 管理/发现的工程文件；不带环境、输出或缓存。

    副本提交自己的真实字节，生产 code_version 自行读取该提交。不能用固定 SHA
    或 monkeypatch 冒充干净身份；副本不修改开发 worktree 的 index/HEAD。
    """
    names = _git(ROOT, 'ls-files', '--cached', '--others', '--exclude-standard', '-z')
    target.mkdir(parents=True)
    for raw in sorted(set(names.split(b'\0')) - {b''}):
        rel = Path(os.fsdecode(raw))
        assert not rel.is_absolute() and '..' not in rel.parts
        assert '.git' not in rel.parts
        # 仓内登记的环境样例与前端公开模式开关属于工程输入；私有 .env 不复制。
        assert (rel.as_posix() in {'.env.example', 'web/.env'}
                or not any(part.startswith('.env') for part in rel.parts))
        source = ROOT / rel
        if not source.is_file():
            continue  # 工作树已删除项与独立嵌套仓库不属于当前运行文件。
        destination = target / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        assert destination.read_bytes() == source.read_bytes()
    _git(target, 'init', '--quiet')
    _git(target, 'add', '--all')
    _git(target, '-c', 'user.name=工程合成验收', '-c', 'user.email=test@example.invalid',
         '-c', 'commit.gpgsign=false', 'commit', '--quiet', '-m', 'test: 冻结当前两模式验收代码')
    assert _git(target, 'status', '--porcelain') == b''
    return _git(target, 'rev-parse', 'HEAD').decode().strip()


def test_manual_stages_and_real_supervisor_deliver_without_markdown(tmp_path_factory):
    root = tmp_path_factory.mktemp('delivery')
    repo = root / 'repo'
    code = freeze_repo(repo)
    env = {**os.environ, 'PYTHONPATH': str(repo), 'PYTHONDONTWRITEBYTECODE': '1'}
    env.pop('DEEPSEEK_API_KEY', None)
    result = subprocess.run(
        [sys.executable, '-X', 'utf8', '-B', '-m', 'tests.delivery_scenario', str(root / 'out')],
        cwd=repo, env=env, capture_output=True, text=True, encoding='utf-8', timeout=900)
    assert result.returncode == 0, result.stdout + result.stderr
    # 旧 BLUEPRINT_SCOPE_OK 逐 scope 审阅标记随 Stage2 语义审阅退役删除；
    # 蓝图读取面由冻结夹具（blueprint-reading-workpack?scope=…）与 schema_api 覆盖。
    assert result.stdout.count('DRAFT_OK ') == 3, result.stdout
    for marker in ('MANUAL_DELIVERY_OK', 'AUTOMATIC_DELIVERY_OK', 'RESTART_DELIVERY_OK'):
        assert marker in result.stdout, result.stdout
    assert _git(repo, 'status', '--porcelain') == b''
    assert _git(repo, 'rev-parse', 'HEAD').decode().strip() == code
