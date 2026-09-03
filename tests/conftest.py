#!/usr/bin/env python3
"""Repository-wide pytest acceptance options."""
from __future__ import annotations

import functools
import json
import os
import shutil
import subprocess
import tempfile
import warnings
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from contracts.schema import CorpusManifest
from tests.corpus_manifest import verify as verify_corpus_manifest


_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_MANIFEST = _ROOT / 'docs' / 'corpus' / 'current_schema_manifest.json'
_SEEN_SKIPS: list[dict[str, str]] = []
_TOUCHED_TEST_PATHS: set[Path] = set()
_REQUIRE = False
_TEST_CODE_VERSION = 'test-code-version'
_WINDOWS_MAX_PATH = 260
# 含线程协调、跨进程写入或进程取消生命周期断言的模块。它们在 xdist 下共享一个
# worker，避免原本用于发现竞态的短 timeout 被测试框架自己的并发制造成假红。
# `tests/test_layering.py::test_concurrency_and_process_lifecycle_modules_are_registered_serial`
# 反向扫描调用面，新增模块漏登记会直接失败。
_SERIAL_TEST_MODULES = frozenset({
    'tests/test_agent.py',
    'tests/test_agent_drivers.py',
    'tests/test_agent_supervisor.py',
    'tests/test_api_agent.py',
    'tests/test_appendlog.py',
    'tests/test_commander_admission.py',
    'tests/test_commander_drivers.py',
    'tests/test_final_approval_workpacks.py',
    'tests/test_final_review_tools.py',
    'tests/test_human_actions.py',
    'tests/test_llm.py',
    'tests/test_orchestrator_jobs.py',
    'tests/test_orchestrator_queue.py',
    'tests/test_provider_probe.py',
    'tests/test_stage2_generation.py',
    'tests/test_stage2_recovery_runs.py',
    'tests/test_stage4_design_session_pool.py',
    'tests/test_stage4_plan_chapters.py',
    'tests/test_stage5_closure.py',
    'tests/test_stage5_generation.py',
    'tests/test_stage6_prompt_experiments.py',
    'tests/test_stage6_semantic_agent.py',
    'tests/test_stage6_semantic_agent_boundary.py',
    'tests/test_trash.py',
    'tests/test_write_admission.py',
})
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
    xdist_suffix: str | None = None,
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
    effective = base / xdist_suffix if xdist_suffix else base
    longest = effective / _STAGE0_TMP_SUFFIX
    length = len(str(longest))
    if length < _WINDOWS_MAX_PATH:
        return None
    # ⚠ 建议必须**算过**才给得出。此前这里无条件推荐 `--basetemp=.test_runtime`，
    #   而在仓库路径较长的 clone / worktree 里那个值自己就超标（实测本仓的
    #   `Novel_rewriting_stage01` worktree 恰好 260）⇒ 预检推荐了一个它自己会拒绝
    #   的值，照做的人原地打转。改成按剩余预算分诊：仓内短名还放得下就报它，
    #   放不下就明说仓内已无解、往仓外走。
    anchor = Path(os.path.normpath(Path(root) if root is not None else Path.cwd()))
    candidate = anchor / '.tr'
    if xdist_suffix:
        candidate /= xdist_suffix
    candidate /= _STAGE0_TMP_SUFFIX
    candidate_fits = len(str(candidate)) < _WINDOWS_MAX_PATH
    advice = (
        '请改用仓库内固定短路径，例如 --basetemp=.tr'
        if candidate_fits else
        f'⚠ 本仓所在路径 {anchor} 太长，仓内放不下固定短根 .tr——请用**仓外**短路径，'
        f'例如 --basetemp={anchor.drive or "C:"}/_tr'
    )
    suffix_note = f'（已计入 xdist 子目录 {xdist_suffix}）' if xdist_suffix else ''
    return (
        f'Windows --basetemp is too deep: {base}；Stage0 最长临时归档路径约 '
        f'{length} 字符{suffix_note}，达到/超过 MAX_PATH({_WINDOWS_MAX_PATH})。'
        f'{advice}；'
        '或一次性解除这台机器的限制（管理员设 '
        'HKLM\\SYSTEM\\CurrentControlSet\\Control\\FileSystem\\LongPathsEnabled=1），'
        '解除后本预检自动不再拦'
    )


def _xdist_worker_suffix(config: pytest.Config) -> str | None:
    """返回 controller 必须提前为 worker 保留的最长目录名。"""
    raw = getattr(getattr(config, 'option', None), 'numprocesses', None)
    if raw in (None, 0, '0'):
        return None
    try:
        highest = max(int(raw) - 1, 0)
        digits = max(len(str(highest)), 3)
    except (TypeError, ValueError):
        # ``auto`` / ``logical`` 在 configure 时未必已解析成整数；三位 worker
        # 编号远高于正常开发机规模，宁可在极深仓根上保守拒绝。
        digits = 3
    return 'popen-gw' + '9' * digits


def _configured_pytest_temp_dirs(config: pytest.Config) -> tuple[Path, ...]:
    """返回本次 pytest 真正使用的 basetemp/cache 路径，不另造第三个落点。"""
    root = Path(getattr(config, 'rootpath', _ROOT))
    option = getattr(config, 'option', None)
    raw_values: list[str | os.PathLike[str] | None] = [
        getattr(option, 'basetemp', None),
    ]
    try:
        raw_values.append(config.getini('cache_dir'))
    except (AttributeError, ValueError):
        pass

    paths: list[Path] = []
    for raw in raw_values:
        if raw is None:
            continue
        expanded = os.path.expandvars(os.path.expanduser(str(raw)))
        path = Path(expanded)
        if not path.is_absolute():
            path = root / path
        path = Path(os.path.abspath(path))
        if path not in paths:
            paths.append(path)
    return tuple(paths)


def _is_within(path: Path, root: Path) -> bool:
    """Return whether an already-normalised path is root itself or its child."""
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _pytest_temp_dirs_to_restore(
    config: pytest.Config,
    touched: set[Path] | None = None,
) -> tuple[Path, ...]:
    """只返回**本次进程触碰过**的 basetemp 与 controller cache。

    pytest 会在下一次真正取用 basetemp 时清空旧内容。若本次只做 collection、或在
    fixture 建立前就失败，递归修整整个陈旧 basetemp 不但没有收益，还会把
    一次秒级定向测试拖成分钟级。xdist worker 只修自己的 ``popen-gw*`` 子根；cache
    由 controller 统一处理，避免多个进程同时递归同一路径。
    """
    configured = _configured_pytest_temp_dirs(config)
    option = getattr(config, 'option', None)
    has_basetemp = getattr(option, 'basetemp', None) is not None
    basetemp = configured[0] if configured and has_basetemp else None
    observed = _TOUCHED_TEST_PATHS if touched is None else touched

    selected: list[Path] = []
    if basetemp is not None:
        for raw in observed:
            path = Path(os.path.abspath(raw))
            if _is_within(path, basetemp) and path not in selected:
                selected.append(path)

    if not hasattr(config, 'workerinput'):
        for path in configured:
            if path != basetemp and path not in selected:
                selected.append(path)
    return tuple(selected)


@contextmanager
def _redirect_stdlib_temp(root: Path) -> Iterator[Path]:
    """把遗留 ``tempfile.mkdtemp()`` 收进可清理的 pytest 临时根。

    这是现存调用点的迁移桥；新增测试应直接使用 ``tmp_path`` /
    ``tmp_path_factory``。直接改 ``tempfile.tempdir`` 是标准库支持的进程级入口，因而
    ``TemporaryDirectory``、``NamedTemporaryFile`` 等同样不会继续污染 ``%TEMP%``。
    """
    root.mkdir(parents=True, exist_ok=True)
    previous = tempfile.tempdir
    tempfile.tempdir = os.fspath(root)
    try:
        yield root
    finally:
        tempfile.tempdir = previous
        try:
            shutil.rmtree(root)
        except FileNotFoundError:
            pass
        except OSError as exc:
            warnings.warn(pytest.PytestWarning(
                f'pytest 标准库临时根未能自动清理：{root}：{exc}'))


@pytest.fixture(scope='session', autouse=True)
def _managed_stdlib_temp(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """让每个 pytest 进程只在自己本次的 basetemp 下创建标准库临时项。"""
    basetemp = Path(os.path.abspath(tmp_path_factory.getbasetemp()))
    _TOUCHED_TEST_PATHS.add(basetemp)
    with _redirect_stdlib_temp(basetemp / '_stdlib_temp'):
        yield


def _uses_real_code_version(node: pytest.Item, *, require: bool) -> bool:
    """真实 provenance、corpus 与显式 marker 不得使用测试代码身份。"""
    return (
        require
        or node.get_closest_marker('corpus') is not None
        or node.get_closest_marker('real_code_version') is not None
    )


@pytest.fixture(autouse=True)
def _stable_code_version(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """普通测试使用确定代码身份，避免每次 provenance 构造都启动三次 Git。"""
    if _uses_real_code_version(request.node, require=_REQUIRE):
        return
    from novel import prov

    monkeypatch.setattr(prov, 'code_version', lambda: _TEST_CODE_VERSION)


def _restore_windows_test_acls(
    paths: tuple[Path, ...],
    *,
    os_name: str = os.name,
    env: Mapping[str, str] | None = None,
    runner=subprocess.run,
) -> list[str]:
    """在创建者进程退出前恢复 pytest 临时目录的普通用户 ACL。

    agent 文件沙箱会给新目录写入只对沙箱 SID 可用的显式 ACL。等进程退出后，普通用户
    已无权修它，只能 UAC ``takeown``；但创建目录的这个进程仍是 owner，可以在收尾时
    开启父目录继承并把当前登录用户写回。只处理本轮登记的 worker/controller 根与
    controller cache，不扫描陈旧根，也不删除文件；异常退出仍须按 AGENTS.md 的
    一次性管理员恢复说明处理。
    """
    if os_name != 'nt':
        return []
    environment = os.environ if env is None else env
    username = environment.get('USERNAME', '').strip()
    domain = environment.get('USERDOMAIN', '').strip()
    if not username:
        return [str(path) for path in paths]
    principal = f'{domain}\\{username}' if domain else username

    failed: list[str] = []
    for path in paths:
        try:
            path.stat()
        except FileNotFoundError:
            continue
        except OSError:
            # 正是待修的 ACL 可能让 stat 失败；仍交给 icacls 尝试。
            pass
        try:
            result = runner(
                [
                    'icacls.exe', str(path), '/inheritance:e',
                    '/grant:r', f'{principal}:(OI)(CI)F', '/T', '/C', '/Q',
                ],
                cwd=_ROOT,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                check=False,
            )
        except OSError:
            failed.append(str(path))
            continue
        if result.returncode != 0:
            failed.append(str(path))
    return failed


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
    _TOUCHED_TEST_PATHS.clear()
    # xdist 会在 controller 放行之后给每个 worker 追加 ``popen-gwN``。只在
    # controller 预检，并提前把最长后缀算进去；worker 再按派生路径检查会变成
    # “主进程建议某个短根，worker 却因同一路径更深而拒绝”的自相矛盾。
    if not hasattr(config, 'workerinput'):
        if problem := _windows_basetemp_problem(
                getattr(config.option, 'basetemp', None),
                xdist_suffix=_xdist_worker_suffix(config)):
            raise pytest.UsageError(problem)
    _REQUIRE = bool(config.getoption('--require-corpus'))
    if _REQUIRE and getattr(config.option, 'numprocesses', None) not in (None, 0, '0'):
        raise pytest.UsageError(
            '--require-corpus must run serially；请移除 -n，或显式使用 -n 0')
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


def pytest_sessionstart(session: pytest.Session) -> None:
    """登记 xdist controller 本轮必然创建/清空的 basetemp 根。"""
    config = session.config
    if hasattr(config, 'workerinput') or _xdist_worker_suffix(config) is None:
        return
    factory = getattr(config, '_tmp_path_factory', None)
    if factory is not None:
        _TOUCHED_TEST_PATHS.add(Path(os.path.abspath(factory.getbasetemp())))


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """xdist 下按文件分组，并把并发/进程生命周期模块收进同一串行组。

    ``load`` / ``loadgroup`` 默认会把同一文件拆给多个 worker，导致模块级扫描 fixture
    重复执行；单纯 ``loadfile`` 又无法合并跨文件的 lifecycle 用例。这里给普通文件
    一个稳定组名、给 ``serial`` 模块同一个组名，再由 ``--dist loadgroup`` 同时满足
    两条边界。
    """
    if not config.pluginmanager.hasplugin('xdist'):
        return
    for item in items:
        try:
            rel = item.path.relative_to(_ROOT).as_posix()
        except ValueError:
            rel = str(item.path)
        if (item.get_closest_marker('serial') is not None
                or rel in _SERIAL_TEST_MODULES):
            group = 'serial'
        else:
            group = f'file:{rel}'
        item.add_marker(pytest.mark.xdist_group(name=group))


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


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config: pytest.Config) -> None:
    """pytest 正常退出时，把沙箱创建者 ACL 还原为登录用户可维护的目录。"""
    failed = _restore_windows_test_acls(_pytest_temp_dirs_to_restore(config))
    if failed:
        warnings.warn(pytest.PytestWarning(
            'pytest 临时目录 ACL 未能自动恢复：'
            f'{failed}；若当前进程曾被强制终止，请按 AGENTS.md 用 UAC 一次性清理'))
