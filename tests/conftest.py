#!/usr/bin/env python3
"""Repository-wide pytest acceptance options."""
from __future__ import annotations

import functools
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pytest

from contracts.schema import CorpusManifest
from tests.corpus_manifest import verify as verify_corpus_manifest


_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_MANIFEST = _ROOT / 'docs' / 'corpus' / 'current_schema_manifest.json'
_SEEN_SKIPS: list[dict[str, str]] = []
_REQUIRE = False
_WINDOWS_MAX_PATH = 260
# pytest truncates a tmp_path node prefix to 30 characters and adds a numeric
# suffix.  Stage0 then creates an Output/per-book directory before appending
# its longest content-addressed archive filename.  Reserve a deliberately
# generous 64-character book component (the two current acceptance books use
# 29 and 24); a future fixture with a longer component must choose an even
# shorter basetemp rather than silently consuming this safety margin.
_STAGE0_TMP_SUFFIX = (
    Path('x' * 30 + '0') / 'Output' / ('book-' + 'x' * 59)
    / 'stage0' / '_rounds'
    / ('merge_candidate.' + 'f' * 64 + '.json')
)


@functools.lru_cache(maxsize=1)
def _long_paths_usable() -> bool:
    """这台机器上，**不带 `\\\\?\\` 前缀**的路径能否越过 MAX_PATH。**实测，不推断。**

    ⚠ **不读注册表、也不查解释器 manifest。** 那是两个各自都会变的间接条件
    （系统 `LongPathsEnabled` + 可执行文件的 `longPathAware`），**两个都成立才有效**；
    拿其中一个当答案就会在另一个不成立时给出反向结论。直接问文件系统
    「这条路径建不建得出来」，答案才是这次跑真正会遇到的那一个。

    ⚠ **只探一层超长目录名**（单段 < 255，整条 > MAX_PATH）：多层探测在未启用的
    机器上会留下一串**删不掉**的中间目录——清理自己也受 MAX_PATH 限制。
    ⚠ 探测根取系统 temp，因为它短。放在深目录里探，探的是那个深目录而不是这台机器。

    ⚠⚠ 刻意**不**用 `\\\\?\\` 前缀探测：前缀总能成功，但前缀建出来的文件
    用普通路径**读不回来**（实测 `FileNotFoundError`）。那会把「明确报错」换成
    「一部分代码看得见、一部分看不见」的静默不一致，比撞墙坏得多。
    """
    if os.name != 'nt':
        return True
    root = Path(tempfile.gettempdir())
    pad = _WINDOWS_MAX_PATH + 8 - len(str(root)) - len(os.sep)
    # ⚠ 名字里必须带 pid：探测目录名若是固定的，`-p xdist` 下两个 worker 同时探测，
    #   后一个会撞 `FileExistsError`——它是 `OSError` 的子类,于是被当成「不支持长路径」
    #   而返回一个**假 False**，把一台已解除限制的机器判成没解除。
    tag = f'lp-{os.getpid():x}-'
    if not len(tag) + 1 <= pad <= 250:   # temp 本身过长/过短 ⇒ 保守当作没启用
        return False
    probe = root / (tag + 'x' * (pad - len(tag)))
    try:
        probe.mkdir()
    except OSError:
        return False
    probe.rmdir()                        # 建得出来的路径，同样删得掉
    return True


def _windows_basetemp_problem(
    configured: str | os.PathLike[str] | None,
    *,
    os_name: str = os.name,
    long_paths: bool | None = None,
    root: str | os.PathLike[str] | None = None,
) -> str | None:
    """Return an actionable preflight error before a deep Windows path explodes.

    ⚠ `long_paths` 是**注入口**，`None` 表示实测这台机器。守卫必须能确定性地
    测「已解除」与「未解除」两种状态：把结论交给宿主机器的注册表，等于让这条
    断言随环境改变，而其中一侧在另一种机器上根本测不到。

    ⚠ `root` 是**第二个注入口**（`None` = 按真实 cwd 解析相对 basetemp），理由与
    `long_paths` 同构。这条预检算的是**绝对路径长度**，而一个相对 basetemp 的绝对
    形态取决于仓库自己住在哪儿：`Novel_rewriting_stage01` 这个 worktree 名比主仓
    长 8 个字符，同一个 `.test_runtime` 于是从 252 变成 260。**把仓库路径长度当成
    被测对象，等于让断言随 clone 的位置改变**——`docs/LESSONS.md` §21 的形状。
    ⇒ 测试必须传一个固定的假仓根；只有真跑（`pytest_configure`）才用真 cwd。
    """
    if os_name != 'nt' or configured is None:
        return None
    if long_paths is None:
        long_paths = _long_paths_usable()
    if long_paths:
        # ⚠ 限制已解除 ⇒ 这一格**不许再拦**。继续按 260 判就是 LESSONS 第 21 条那个
        # 形状：守卫自己漂了，把一个实际能跑的 basetemp 判成假红。
        return None
    base = Path(configured).expanduser()
    if root is None:
        base = base.resolve(strict=False)
    elif not base.is_absolute():
        base = Path(os.path.normpath(Path(root) / base))
    longest = base / _STAGE0_TMP_SUFFIX
    length = len(str(longest))
    if length < _WINDOWS_MAX_PATH:
        return None
    # ⚠ 建议必须**算过**才给得出。此前这里无条件推荐 `--basetemp=.test_runtime`，
    #   而在仓库路径较长的 clone / worktree 里那个值自己就超标（实测本仓的
    #   `Novel_rewriting_stage01` worktree 恰好 260）⇒ 预检推荐了一个它自己会拒绝
    #   的值，照做的人原地打转。改成按剩余预算分诊：仓内短名还放得下就报它，
    #   放不下就明说仓内已无解、往仓外走。
    budget = _WINDOWS_MAX_PATH - 1 - len(str(_STAGE0_TMP_SUFFIX))
    anchor = Path(os.path.normpath(Path(root) if root is not None else Path.cwd()))
    spare = budget - len(str(anchor)) - 1
    advice = (
        '请改用仓库内短路径，例如 --basetemp=.test_runtime'
        if spare >= len('.test_runtime') else
        f'⚠ 本仓所在路径 {anchor} 太长，仓内放不下 .test_runtime'
        f'（前缀预算只剩 {spare} 字符）——请用**仓外**短路径，'
        f'例如 --basetemp={anchor.drive or "C:"}/_tr'
    )
    return (
        f'Windows --basetemp is too deep: {base}；Stage0 最长临时归档路径约 '
        f'{length} 字符，达到/超过 MAX_PATH({_WINDOWS_MAX_PATH})。'
        f'{advice}；'
        '或一次性解除这台机器的限制（管理员设 '
        'HKLM\\SYSTEM\\CurrentControlSet\\Control\\FileSystem\\LongPathsEnabled=1），'
        '解除后本预检自动不再拦'
    )


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup('current corpus')
    group.addoption(
        '--require-corpus', action='store_true', default=False,
        help=(
            'fail closed unless NOVEL_OUTPUT and the exact current-schema manifest '
            'are present; include both corpus and non-corpus tests'),
    )
    group.addoption(
        '--record-corpus-skips', metavar='PATH', default=None,
        help='write the exact observed skip list as JSON for manifest finalisation',
    )


def _manifest_path() -> Path:
    configured = os.environ.get('NOVEL_CORPUS_MANIFEST')
    return Path(configured).expanduser() if configured else _DEFAULT_MANIFEST


def pytest_configure(config: pytest.Config) -> None:
    global _REQUIRE
    _SEEN_SKIPS.clear()
    if problem := _windows_basetemp_problem(
            getattr(config.option, 'basetemp', None)):
        raise pytest.UsageError(problem)
    _REQUIRE = bool(config.getoption('--require-corpus'))
    if not _REQUIRE:
        return
    # `pyproject.toml` deliberately keeps ordinary local runs cheap.  This
    # explicit mode is the opposite contract: it must exercise both halves.
    config.option.markexpr = 'corpus or not corpus'
    configured = os.environ.get('NOVEL_OUTPUT')
    if not configured:
        raise pytest.UsageError('--require-corpus requires NOVEL_OUTPUT')
    base = Path(configured).expanduser()
    manifest_path = _manifest_path()
    if not manifest_path.is_file():
        raise pytest.UsageError(
            f'--require-corpus manifest missing: {manifest_path}')
    try:
        manifest = CorpusManifest.model_validate(
            json.loads(manifest_path.read_text(encoding='utf-8')))
        verify_corpus_manifest(base, manifest)
    except Exception as exc:
        raise pytest.UsageError(f'current corpus refused: {exc}') from exc
    setattr(config, '_current_corpus_manifest', manifest)


def _reason(longrepr: Any) -> str:
    raw = longrepr[2] if isinstance(longrepr, tuple) and len(longrepr) >= 3 else str(longrepr)
    raw = str(raw).strip()
    return raw.removeprefix('Skipped: ').strip()


def _remember_skip(report: pytest.TestReport | pytest.CollectReport) -> None:
    # pytest implements xfail as an internal skipped report, but exposes it as
    # a distinct terminal category.  The manifest's allowlist is for actual
    # skips only; otherwise adding/removing an xfail silently changes it.
    if report.skipped and not getattr(report, 'wasxfail', False):
        row = {'nodeid': report.nodeid, 'reason': _reason(report.longrepr)}
        if row not in _SEEN_SKIPS:
            _SEEN_SKIPS.append(row)


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    _remember_skip(report)


def pytest_collectreport(report: pytest.CollectReport) -> None:
    _remember_skip(report)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    actual = sorted(_SEEN_SKIPS, key=lambda item: item['nodeid'])
    record = session.config.getoption('--record-corpus-skips')
    if record:
        output = Path(record)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(actual, ensure_ascii=False, indent=2) + '\n',
            encoding='utf-8', newline='\n')

    if not _REQUIRE:
        return
    manifest: CorpusManifest = getattr(session.config, '_current_corpus_manifest')
    expected = [item.model_dump(mode='json') for item in manifest.skip_allowlist]
    terminal = session.config.pluginmanager.get_plugin('terminalreporter')
    corpus_collected = sum(
        item.get_closest_marker('corpus') is not None
        for item in getattr(session, 'items', ())
    )
    if corpus_collected == 0:
        message = (
            'current corpus acceptance collected 0 corpus tests；'
            '不能用 non-corpus tests 或 skip allowlist 冒充语料验收'
        )
        if terminal is not None:
            terminal.write_sep('=', message, red=True)
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
        return
    if actual == expected:
        return
    message = (
        'current corpus skip allowlist mismatch\n'
        f'expected={json.dumps(expected, ensure_ascii=False, sort_keys=True)}\n'
        f'actual={json.dumps(actual, ensure_ascii=False, sort_keys=True)}'
    )
    if terminal is not None:
        terminal.write_sep('=', message, red=True)
    session.exitstatus = pytest.ExitCode.TESTS_FAILED
