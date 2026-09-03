#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
base.py —— 可恢复 CLI agent 的供应商中立运行时边界。

本包只解决「怎样安全地拉起一个 agent 会话并把结果归一」；节点循环、代码校验、
产物落盘、成本账和 provenance 仍由各自既有层负责。尤其不要把一次 CLI 成功误当成
产物合格：这里返回的是一轮 agent 观察值，不是工作流判定。

两条安全边界写在这一层而不是散落到厂商驱动：

* Windows 的 ``.cmd`` / ``.bat`` shim 必须解析成它真正转发的 ``.exe``，解析失败
  就关闭式拒绝；绝不经 ``cmd /c``，否则多行提示词可能只送到首行而进程仍 exit 0。
* 默认 runner 只持有**本驱动当前这一棵**进程树。``cancel`` 按该 Popen
  的 PID/进程组收掉它与孙进程，不按进程名扫描系统，也不会误杀
  另一个节点或另一本书的 agent。
"""
from __future__ import annotations

import json
import os
import re
import signal
import shutil
import subprocess
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from contracts.schema.orchestration import AgentProfile


PLAIN_DELIVERY = 'plain'
CLAUDE_STRUCTURED_DELIVERY_V1 = 'claude-structured-output/v1'
AgentDeliveryProtocol = Literal[
    'plain',
    'claude-structured-output/v1',
]

#: Claude CLI 的 ``--json-schema`` 会把最终交付实现成内置 structured-output
#: tool call。footer 只说明**怎样交付最后一份对象**，不重复业务 prompt/schema；
#: 版本写进稳定标记，后续若改字必须显式换协议名并自然改变真实 stdin hash。
CLAUDE_STRUCTURED_DELIVERY_FOOTER_V1 = """\
--- 结构化交付协议 v1（claude-structured-output/v1）---
本轮已提供 JSON Schema。最终答案必须且只能通过 CLI 提供的结构化输出工具提交一次：
工具参数本身就是符合 schema 的完整响应对象；不要先在普通回答中打印 JSON，不要套
`response`、`result` 或 `structured_output` 包装键，也不要在结构化提交之后重复输出。
除 CLI 内建的 `StructuredOutput` 外，不得调用任何其他工具。"""


def agent_delivery_protocol(*, driver: str,
                            has_output_schema: bool) -> AgentDeliveryProtocol:
    """把 driver/schema 组合映射到唯一 stdin 交付协议。"""
    if driver == 'claude-code' and has_output_schema:
        return CLAUDE_STRUCTURED_DELIVERY_V1
    return PLAIN_DELIVERY


def render_agent_stdin(
    system: str | None,
    prompt: str,
    *,
    delivery_protocol: AgentDeliveryProtocol = PLAIN_DELIVERY,
) -> str:
    """渲染真正送进 CLI stdin 的唯一字节源（编码前）。"""
    if system is None:
        rendered = prompt
    else:
        rendered = f'{system}\n\n--- 本轮任务 ---\n\n{prompt}'
    if delivery_protocol == PLAIN_DELIVERY:
        return rendered
    if delivery_protocol == CLAUDE_STRUCTURED_DELIVERY_V1:
        return f'{rendered}\n\n{CLAUDE_STRUCTURED_DELIVERY_FOOTER_V1}'
    raise AgentConfigurationError(
        f'未知 agent delivery_protocol={delivery_protocol!r}')


def render_agent_stdin_bytes(
    system: str | None,
    prompt: str,
    *,
    delivery_protocol: AgentDeliveryProtocol = PLAIN_DELIVERY,
) -> bytes:
    """把最终 stdin 渲染为生产子进程真正接收的 UTF-8 字节。

    这是送达、prompt SHA、request fingerprint 与 checkpoint 的共同
    落点；生产 runner 不得再把它放回文本模式，否则 Windows 会把
    LF 静默转为 CRLF，导致账上哈希与 provider 所见字节分家。
    """
    return render_agent_stdin(
        system, prompt, delivery_protocol=delivery_protocol).encode('utf-8')


def render_structured_agent_stdin(
        system: str, prompt: str, *, driver: str) -> str:
    """checkpoint/恢复守卫复算一次结构化请求的真实首轮 stdin。"""
    return render_agent_stdin(
        system,
        prompt,
        delivery_protocol=agent_delivery_protocol(
            driver=driver, has_output_schema=True),
    )


def render_structured_agent_stdin_bytes(
        system: str, prompt: str, *, driver: str) -> bytes:
    """checkpoint/恢复守卫复算结构化请求的真实 UTF-8 stdin。"""
    return render_agent_stdin_bytes(
        system,
        prompt,
        delivery_protocol=agent_delivery_protocol(
            driver=driver, has_output_schema=True),
    )


class AgentError(RuntimeError):
    """所有 agent 驱动错误的共同父类。"""


class AgentConfigurationError(AgentError):
    """请求配置在发起子进程前就不成立。"""


class AgentBusyError(AgentError):
    """同一驱动实例已有一轮未结束；调用方必须为并行节点各建实例。"""


class AgentProtocolError(AgentError):
    """CLI 已返回，但输出不满足可恢复会话契约。"""


class AgentCommandError(AgentError):
    """CLI 没有正常完成；错误正文只保留有限诊断，不夹带完整提示词。"""

    def __init__(self, message: str, *, returncode: int | None = None,
                 partial_turn: AgentTurn | None = None,
                 usage: AgentUsage | None = None,
                 error_kind: str = 'agent_command',
                 status: int | None = None,
                 retryable: bool = False,
                 retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.partial_turn = partial_turn
        self.error_kind = error_kind
        self.status = status
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
        # 启动在供应商 session id 出现前失败时不能伪造 ``AgentTurn``，但已经发生的
        # 计量仍须能进入成本账。成功取得 partial turn 时以它为唯一事实源。
        self.usage = partial_turn.usage if partial_turn is not None else usage


@dataclass(frozen=True)
class AgentRequest:
    """一轮发给 agent 的输入。

    ``execution_policy`` 是调用方必须显式选择的执行隔离契约。Phase2 只有
    ``executor-isolated``：请求必须在一个真实存在且为空的 cwd 中执行，厂商驱动
    还必须把它映射为各自的无写入/无扩展参数。这里刻意没有默认值；Phase6 接入
    commander 时再增加独立 policy，不能让 commander 权限从 executor 配置中外溢。

    ``system`` 是可选的稳定前缀。CLI 的能力面并不都提供真正的 system role，因此
    中立层把它与本轮 ``prompt`` 组合后统一走 stdin；调用方不能假设它享有供应商 API
    的 system 消息优先级。``output_schema`` 只约束本轮最终回答，落盘后的业务 schema
    仍必须由现有 Python 校验器复验。
    """

    prompt: str
    execution_policy: Literal['executor-isolated']
    system: str | None = None
    output_schema: Mapping[str, Any] | None = None
    delivery_protocol: AgentDeliveryProtocol = PLAIN_DELIVERY
    cwd: str | os.PathLike[str] | None = None

    def __post_init__(self) -> None:
        if not self.prompt.strip():
            raise AgentConfigurationError('agent prompt 不得为空')
        if self.execution_policy != 'executor-isolated':
            raise AgentConfigurationError(
                f'不支持 execution_policy={self.execution_policy!r}；'
                '拒绝静默忽略执行隔离策略')
        if self.cwd is None:
            raise AgentConfigurationError(
                'executor-isolated 请求必须显式提供空 cwd')
        if self.system is not None and not self.system.strip():
            raise AgentConfigurationError('system 若出现就不得为空字符串')
        if self.output_schema is not None and not isinstance(
                self.output_schema, Mapping):
            raise AgentConfigurationError('output_schema 必须是 JSON object')
        if self.delivery_protocol not in (
                PLAIN_DELIVERY, CLAUDE_STRUCTURED_DELIVERY_V1):
            raise AgentConfigurationError(
                f'未知 agent delivery_protocol={self.delivery_protocol!r}')
        if (self.delivery_protocol == CLAUDE_STRUCTURED_DELIVERY_V1
                and self.output_schema is None):
            raise AgentConfigurationError(
                'claude structured delivery 必须同时提供 output_schema')

    def stdin_text(self) -> str:
        """生成跨厂商一致的 stdin；不把多行正文塞进命令行。"""
        return render_agent_stdin(
            self.system, self.prompt,
            delivery_protocol=self.delivery_protocol)

    def stdin_bytes(self) -> bytes:
        """生成真正送达子进程且用于哈希绑定的 stdin 字节。"""
        return render_agent_stdin_bytes(
            self.system, self.prompt,
            delivery_protocol=self.delivery_protocol)


@dataclass(frozen=True)
class AgentUsage:
    """供应商中立的 token 读数；``None`` 表示 CLI 没有提供，不猜 0。"""

    prompt_tokens: int | None = None
    prompt_cache_hit_tokens: int | None = None
    prompt_cache_miss_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None


def classify_agent_command_failure(
    detail: object,
    *,
    usage: AgentUsage | None = None,
    status: int | None = None,
) -> tuple[str, int | None, bool]:
    """把 CLI 的已知错误摘要归一为可执行的重试事实。

    403 不是一个统一语义：登录过期、模型权限和供应商瞬时拒绝不能共用策略。
    这里只把已实测的 ``403 Request not allowed`` 且供应商明确回显 0 token 的
    形态视为瞬态；其余 403 保守地按权限错误停止热重试。错误正文只在内存中
    参与分类，调用方仍只把稳定 kind/status 写进成本账。
    """
    text = str(detail or '').casefold()
    if status is None:
        match = re.search(r'(?:api error|status|http)\s*[:=]?\s*(\d{3})\b', text)
        if match is not None:
            status = int(match.group(1))

    usage_values = () if usage is None else (
        usage.prompt_tokens,
        usage.prompt_cache_hit_tokens,
        usage.prompt_cache_miss_tokens,
        usage.completion_tokens,
        usage.reasoning_tokens,
        usage.total_tokens,
    )
    usage_is_confirmed_zero = (
        any(value is not None for value in usage_values)
        and all(value in (None, 0) for value in usage_values)
    )

    # 账号窗口耗尽要等外部状态变化；秒级热重试只会制造四次相同失败。
    if any(marker in text for marker in (
            'usage limit', 'session limit', 'five-hour', 'five hour',
            'five_hour', '5-hour', '5 hour', 'out_of_credits',
            'resets at', 'reset at')) or re.search(
                r'\bresets?\s+(?:at\s+)?\d{1,2}:\d{2}', text):
        return 'usage_limit', status, False

    if any(marker in text for marker in (
            'oauth access token has expired', 're-authenticate',
            'reauthenticate', 'invalid api key', 'not logged in',
            'login required', 'authentication required')) or status == 401:
        return 'authentication', status, False

    if (status == 403 and 'request not allowed' in text
            and usage_is_confirmed_zero):
        return 'provider_transient', status, True
    if status == 403:
        return 'permission', status, False
    if status == 429 or 'too many requests' in text:
        return 'rate_limit', status, True
    if any(marker in text for marker in (
            'connection reset', 'connection refused', 'connection aborted',
            'network error', 'network request failed', 'failed to fetch',
            'socket hang up', 'econnreset', 'econnrefused', 'enotfound',
            'dns lookup', 'name resolution', 'tls handshake timeout')):
        return 'connection', status, True
    if 'timed out' in text or 'timeout' in text:
        return 'timeout', status, True
    if (status in (408, 425)
            or (status is not None and 500 <= status <= 599)):
        return ('timeout' if status == 408 else 'server_error'), status, True
    return 'agent_command', status, False


@dataclass(frozen=True)
class AgentTurn:
    """一次 start/resume 完成后的归一结果。

    ``session_id`` 必须来自 CLI 的 JSON/事件，绝不由本地生成后冒充供应商会话。
    ``model`` 与 ``actual_reasoning_effort`` 只记录 CLI 真正回报的值；没回报就是
    ``None``，请求档位仍可从同一次调用使用的 ``AgentProfile`` 审计。
    ``turn_count`` 是**本次 CLI invocation** 明确报告/可数出的 provider turn 数，
    不是 logical session 的累计轮号；循环层自己的严格递增轮号仍由审计账记录。
    ``raw_event_summary`` 只保留事件类型与计量字段，不复制 prompt 或完整回答。
    """

    session_id: str
    content: str
    model: str | None
    usage: AgentUsage
    turn_count: int | None
    actual_reasoning_effort: str | None = None
    raw_event_summary: tuple[dict[str, Any], ...] | None = None


@dataclass(frozen=True)
class AgentProbe:
    """0 成本本地探针结果；``available=False`` 是显式状态，不靠异常猜。"""

    driver: str
    provider: str
    executable: str
    available: bool
    version: str | None = None
    detail: str | None = None


class AgentDriver(ABC):
    """每个供应商驱动必须提供的完整生命周期。"""

    driver: str
    provider: str

    @abstractmethod
    def probe(self) -> AgentProbe:
        """只探测本地 CLI 与版本，不向模型发请求。"""

    @abstractmethod
    def start(self, profile: AgentProfile, request: AgentRequest) -> AgentTurn:
        """新建会话并完成第一轮。"""

    @abstractmethod
    def resume(self, profile: AgentProfile, session_id: str,
               request: AgentRequest) -> AgentTurn:
        """以供应商返回的原 session id 继续一轮。"""

    @abstractmethod
    def cancel(self, session_id: str | None = None) -> bool:
        """终止本实例当前进程树；指定 id 不匹配时不得误杀。"""

    @abstractmethod
    def close(self) -> None:
        """幂等释放本实例；若仍在跑，只取消这一棵进程树。"""


class Runner(Protocol):
    """可注入的命令执行缝；真实实现与测试 fake 共用这一张面。"""

    def run(self, argv: Sequence[str], *, input: bytes | None,
            cwd: str | None, timeout: float,
            session_id: str | None) -> subprocess.CompletedProcess[str]: ...

    def cancel(self, session_id: str | None = None) -> bool: ...

    def close(self) -> None: ...


RunnerCallable = Callable[..., subprocess.CompletedProcess[str]]

# Windows ``CreateProcessW`` 的 lpCommandLine 上限包含末尾 NUL。Claude CLI 目前
# 只提供内联 ``--json-schema``，所以不能等系统用含糊的 WinError 才发现 schema 太长。
_WINDOWS_COMMAND_LINE_LIMIT = 32_767


def _short_detail(value: str | None, *, limit: int = 1200) -> str:
    """错误只带有限诊断，避免把供应商回显的整本正文再写进日志。"""
    text = (value or '').strip().replace('\x00', '')
    return text[:limit] + ('…' if len(text) > limit else '')


def ensure_command_line_fits(
        argv: Sequence[str], *, platform_name: str | None = None) -> None:
    """在 Windows 拉进程前关闭式检查完整 argv 的 UTF-16 字符预算。

    prompt/正文始终走 stdin；这里主要守 Claude 只能内联的 JSON Schema。使用
    ``list2cmdline`` 与 Python ``Popen(list)`` 在 Windows 的实际 quoting 同口径，
    不自己估算每个参数的引号与反斜杠膨胀。
    """
    platform_name = os.name if platform_name is None else platform_name
    if platform_name != 'nt':
        return
    command_line = subprocess.list2cmdline(list(argv))
    utf16_units = len(command_line.encode('utf-16-le')) // 2
    if utf16_units + 1 > _WINDOWS_COMMAND_LINE_LIMIT:
        raise AgentConfigurationError(
            'agent CLI argv 超过 Windows CreateProcess 32767 字符上限；'
            '请缩小 output_schema，本次未启动模型请求')


class _CallableRunner:
    """兼容文本 ``subprocess.run`` 形 fake；生产送达仍只走字节。

    这一层只是测试适配器：它把已冻结的 UTF-8 字节还原成
    ``str`` 交给历史 fake，不参与真实 CLI 子进程的 newline 处理。
    """

    def __init__(self, call: RunnerCallable) -> None:
        self._call = call
        self._closed = False

    def run(self, argv: Sequence[str], *, input: bytes | None,
            cwd: str | None, timeout: float,
            session_id: str | None) -> subprocess.CompletedProcess[str]:
        del session_id
        if self._closed:
            raise AgentConfigurationError('runner 已关闭')
        return self._call(
            list(argv),
            input=(None if input is None else input.decode('utf-8')),
            cwd=cwd, capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=timeout, check=False,
            shell=False)

    def cancel(self, session_id: str | None = None) -> bool:
        del session_id
        return False

    def close(self) -> None:
        self._closed = True


class _SubprocessRunner:
    """默认可取消 runner；一个实例任何时刻最多拥有一棵进程树。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: tuple[subprocess.Popen[bytes], str | None] | None = None
        self._closed = False

    def run(self, argv: Sequence[str], *, input: bytes | None,
            cwd: str | None, timeout: float,
            session_id: str | None) -> subprocess.CompletedProcess[str]:
        with self._lock:
            if self._closed:
                raise AgentConfigurationError('runner 已关闭')
            if self._active is not None:
                raise AgentBusyError('同一 AgentDriver 实例不能并发执行两轮')
            process_options: dict[str, Any] = {}
            if os.name == 'nt':
                # agent 会继续拉工具/novel.cli 孙进程；独立进程组是按 PID 用
                # ``taskkill /T`` 收整棵树的前提，不能退回只杀 CLI 根进程。
                process_options['creationflags'] = (
                    subprocess.CREATE_NO_WINDOW
                    | subprocess.CREATE_NEW_PROCESS_GROUP)
            else:
                # 新 session 同时建立 pgid=pid；取消时只向这一组发信号，不碰本服务
                # 或并行节点所在的进程组。
                process_options['start_new_session'] = True
            process = subprocess.Popen(
                list(argv), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, cwd=cwd, text=False,
                shell=False, **process_options)
            self._active = (process, session_id)
        try:
            stdout, stderr = process.communicate(input=input, timeout=timeout)
        except subprocess.TimeoutExpired:
            _signal_process_tree(process, force=False)
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                _signal_process_tree(process, force=True)
                stdout, stderr = process.communicate()
            raise
        except BaseException:
            # KeyboardInterrupt / runner 线程取消也不能把一只失去所有权的 CLI 留在后台。
            if self._owns(process):
                _signal_process_tree(process, force=False)
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    _signal_process_tree(process, force=True)
                    process.communicate()
            raise
        finally:
            with self._lock:
                if self._active is not None and self._active[0] is process:
                    self._active = None
        return subprocess.CompletedProcess(
            list(argv), int(process.returncode),
            stdout.decode('utf-8', errors='replace'),
            stderr.decode('utf-8', errors='replace'))

    def cancel(self, session_id: str | None = None) -> bool:
        with self._lock:
            active = self._active
            if active is None:
                return False
            process, active_session = active
            if session_id is not None and active_session != session_id:
                return False
        # 显式取消与 Windows 既有 ``taskkill /T /F`` 同义：不等待某个工具忽略
        # SIGTERM 后拖到整轮 timeout。目标来自本实例持有的 Popen，绝不按进程名扫描。
        return _signal_process_tree(process, force=True)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            active = self._active
            if active is None:
                return
            process = active[0]
        _signal_process_tree(process, force=True)

    def _owns(self, process: subprocess.Popen[Any]) -> bool:
        """确认异常处理仍只操作本轮 Popen；避免结束/新一轮交界处误杀。"""
        with self._lock:
            return self._active is not None and self._active[0] is process


def _windows_taskkill_path() -> str:
    """使用系统目录中的 taskkill，避免工作目录里的同名程序劫持取消路径。"""
    system_root = os.environ.get('SystemRoot', r'C:\Windows')
    return str(Path(system_root) / 'System32' / 'taskkill.exe')


def _signal_process_tree(process: subprocess.Popen[Any], *, force: bool) -> bool:
    """只终止 ``process`` 建立的进程组/树；进程不存在时返回 False。

    Windows 没有可移植的 ``killpg``，沿用本仓已经实跑验证的 ``taskkill /T``；
    POSIX 则依赖 ``start_new_session=True`` 建立的 pgid。任何 fallback 也只触碰
    手里这一只 Popen 根进程，不扫描名字或系统进程表。
    """
    if os.name == 'nt':
        if process.poll() is not None:
            return False
        try:
            killed = subprocess.run(
                [_windows_taskkill_path(), '/PID', str(process.pid), '/T', '/F'],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, check=False,
                creationflags=subprocess.CREATE_NO_WINDOW)
        except OSError:
            killed = None
        if killed is not None and killed.returncode == 0:
            return True
        if process.poll() is not None:
            return False
        # taskkill 被策略阻断时仍不能放任付费根进程；这条 fallback 不声称孙进程
        # 已停，上层 communicate/timeout 会继续以未完成而失败。
        process.kill() if force else process.terminate()
        return True

    signum = signal.SIGKILL if force else signal.SIGTERM
    try:
        # ``start_new_session`` 保证 pgid 就是 leader pid；leader 已退出但仍有孙进程
        # 持有管道时，按已知 pgid 发信号仍能把余下成员收掉。
        os.killpg(process.pid, signum)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        # 极端平台若不支持 process group，最多退回当前 Popen，仍不扩大杀伤面。
        if process.poll() is not None:
            return False
        process.kill() if force else process.terminate()
        return True


# npm 生成的 Windows shim 常见形状：``"%dp0%\path\tool.exe" %*``；
# ``%~dp0`` 是同义变体。只认双引号包住的 exe，不执行 shim 中的其他命令。
_WINDOWS_SHIM_EXE = re.compile(
    r'"%(?:~)?dp0%\\?(?P<relative>[^"\r\n]+\.exe)"', re.IGNORECASE)


def _entry_from_windows_shim(shim: Path) -> str | None:
    try:
        text = shim.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return None
    match = _WINDOWS_SHIM_EXE.search(text)
    if match is None:
        return None
    target = shim.parent / match.group('relative').lstrip('\\/')
    return str(target.resolve()) if target.is_file() else None


def resolve_cli_entry(command: str, executable: str | None = None) -> str:
    """解析能被 ``CreateProcess`` 直接执行的入口，绝不构造 shell 命令。

    显式入口也要经过 ``which``：Windows 上传入 ``claude`` 时，PATH 返回的通常
    是 npm ``.cmd`` shim。解析不了的批处理文件不是「也许能跑」，而是会重新引入
    多行参数截断风险，因此在任何模型调用前就拒绝。
    """
    requested = executable or command
    found = shutil.which(requested)
    candidate = Path(found or requested)
    if candidate.suffix.lower() not in ('.cmd', '.bat'):
        return str(candidate)
    direct = _entry_from_windows_shim(candidate)
    if direct is None:
        raise AgentConfigurationError(
            f'无法从 Windows shim 解析 {command} 的原生可执行入口：{candidate}')
    return direct


def compact_schema(schema: Mapping[str, Any] | None) -> str | None:
    """把 schema 变为确定字节；不可 JSON 化时必须在拉进程前失败。"""
    if schema is None:
        return None
    try:
        return json.dumps(
            dict(schema), ensure_ascii=False, sort_keys=True,
            separators=(',', ':'))
    except (TypeError, ValueError) as exc:
        raise AgentConfigurationError(
            f'output_schema 不是合法 JSON：{type(exc).__name__}') from exc


def decode_wire_json(text: str) -> Any:
    """解析 CLI wire JSON，与 ``ArtifactStore`` 的盘上产物读取明确分界。

    分层守卫把 ``json.load(s)`` 保留给 store；这里处理的是子进程 stdout，不是上游
    artifact，因此用 decoder 的协议入口，避免两种证据源在代码形状上被混为一谈。
    """
    return json.JSONDecoder().decode(text)


def integer(value: Any) -> int | None:
    """只接收非负整数 token 读数；bool 不是 token 数。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    return None


class CliAgentDriver(AgentDriver):
    """两个 CLI 驱动共享的进程生命周期；供应商只负责 argv 与协议解析。"""

    version_argv: tuple[str, ...] = ('--version',)
    command: str
    supported_reasoning_efforts: frozenset[str] = frozenset()

    def __init__(self, *, executable: str | None = None,
                 timeout: float = 1800,
                 runner: Runner | RunnerCallable | None = None) -> None:
        if timeout <= 0:
            raise AgentConfigurationError('timeout 必须大于 0')
        self.executable = resolve_cli_entry(self.command, executable)
        self.timeout = float(timeout)
        if runner is None:
            self._runner: Runner = _SubprocessRunner()
        elif callable(runner) and not hasattr(runner, 'run'):
            self._runner = _CallableRunner(runner)
        else:
            self._runner = runner  # type: ignore[assignment]
        self._closed = False
        # 一个 driver 可以顺序服务多个 session，但每个 provider id 一旦成功绑定
        # cwd，就不能在 resume 时换目录。key 是 provider 返回值，不是本地猜出的 id。
        self._session_cwds: dict[str, str] = {}

    def probe(self) -> AgentProbe:
        if self._closed:
            return AgentProbe(
                driver=self.driver, provider=self.provider,
                executable=self.executable, available=False,
                detail='driver 已关闭')
        try:
            completed = self._runner.run(
                [self.executable, *self.version_argv], input=None, cwd=None,
                timeout=min(self.timeout, 15), session_id=None)
        except (OSError, subprocess.SubprocessError, AgentError) as exc:
            return AgentProbe(
                driver=self.driver, provider=self.provider,
                executable=self.executable, available=False,
                detail=f'{type(exc).__name__}: {_short_detail(str(exc))}')
        output = (completed.stdout or completed.stderr or '').strip()
        if completed.returncode != 0:
            return AgentProbe(
                driver=self.driver, provider=self.provider,
                executable=self.executable, available=False,
                detail=f'exit={completed.returncode}: {_short_detail(output)}')
        version = output.splitlines()[0] if output else None
        return AgentProbe(
            driver=self.driver, provider=self.provider,
            executable=self.executable, available=True, version=version)

    def _check_profile(self, profile: AgentProfile) -> None:
        if profile.driver != self.driver:
            raise AgentConfigurationError(
                f'profile.driver={profile.driver!r} 不能交给 {self.driver!r} 驱动')
        if not profile.model.strip() or not profile.reasoning_effort.strip():
            raise AgentConfigurationError('model 与 reasoning_effort 必须显式给出')
        if (self.supported_reasoning_efforts
                and profile.reasoning_effort not in self.supported_reasoning_efforts):
            raise AgentConfigurationError(
                f'{self.driver} 尚未声明支持 reasoning_effort='
                f'{profile.reasoning_effort!r}；拒绝把未知档位静默映射成别档')
        if profile.execution_mode not in (None, 'standard'):
            raise AgentConfigurationError(
                f'第 1 阶段不支持 execution_mode={profile.execution_mode!r}；'
                '拒绝忽略配置继续执行')

    @staticmethod
    def _cwd_identity(cwd: str) -> str:
        """给安全比较使用的 cwd 身份；Windows 大小写差异不应制造假换目录。"""
        return os.path.normcase(os.path.normpath(cwd))

    def _prepare_request_cwd(
            self, request: AgentRequest, *, session_id: str | None) -> str:
        """关闭式落实 ``executor-isolated``，并守住 resume 的 cwd 身份。

        provider 的 session 数据应写在各自用户配置目录，而不是工作 cwd；因此每轮
        都重新检查目录仍为空。若真实 CLI 往这里落 session 文件，这是隔离契约与 CLI
        行为冲突，必须响亮失败，不能为了 resume 悄悄放宽成「差不多空」。
        """
        if request.execution_policy != 'executor-isolated':
            # ``AgentRequest`` 的正常构造已经会挡住；保留这一层防御，避免未来新增
            # policy 后某个 driver 忘了实现却继续执行。
            raise AgentConfigurationError(
                f'{self.driver} 未实现 execution_policy='
                f'{request.execution_policy!r}，本次未启动模型请求')
        assert request.cwd is not None  # __post_init__ 已关闭式拒绝 None
        try:
            path = Path(request.cwd).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise AgentConfigurationError(
                f'executor-isolated cwd 无法解析：{type(exc).__name__}') from exc
        if not path.is_dir():
            raise AgentConfigurationError('executor-isolated cwd 必须是目录')
        try:
            with os.scandir(path) as entries:
                occupied = next(entries, None) is not None
        except OSError as exc:
            raise AgentConfigurationError(
                f'executor-isolated cwd 无法检查：{type(exc).__name__}') from exc
        if occupied:
            raise AgentConfigurationError(
                'executor-isolated cwd 必须为空；拒绝让 agent 读取工作树或遗留文件')

        resolved = str(path)
        if session_id is not None:
            known = self._session_cwds.get(session_id)
            if (known is not None
                    and self._cwd_identity(known) != self._cwd_identity(resolved)):
                raise AgentConfigurationError(
                    'resume 必须使用该 provider session 首轮绑定的同一空 cwd')
        return resolved

    def _bind_session_cwd(self, session_id: str, cwd: str) -> None:
        """成功解码后把 CLI 给出的 session id 绑定到本轮已验证 cwd。"""
        known = self._session_cwds.get(session_id)
        if (known is not None
                and self._cwd_identity(known) != self._cwd_identity(cwd)):
            raise AgentProtocolError(
                '同一 provider session_id 试图绑定两个 cwd；拒绝串接会话')
        self._session_cwds[session_id] = cwd

    def _run(self, argv: Sequence[str], request: AgentRequest, *,
             session_id: str | None) -> subprocess.CompletedProcess[str]:
        if self._closed:
            raise AgentConfigurationError('AgentDriver 已关闭，不能再次启动')
        ensure_command_line_fits(argv)
        cwd = self._prepare_request_cwd(request, session_id=session_id)
        try:
            return self._runner.run(
                argv, input=request.stdin_bytes(), cwd=cwd,
                timeout=self.timeout, session_id=session_id)
        except subprocess.TimeoutExpired as exc:
            raise AgentCommandError(
                f'{self.driver} CLI 超过 {self.timeout:g}s，已终止本实例当前进程',
                error_kind='timeout', retryable=True) from exc
        except AgentError:
            raise
        except (OSError, subprocess.SubprocessError) as exc:
            raise AgentCommandError(
                f'{self.driver} CLI 无法执行：{type(exc).__name__}: '
                f'{_short_detail(str(exc))}') from exc

    def _raise_for_exit(self, completed: subprocess.CompletedProcess[str], *,
                        partial_turn: AgentTurn | None = None) -> None:
        if completed.returncode == 0:
            return
        kind, status, retryable = classify_agent_command_failure(
            completed.stderr, usage=(
                None if partial_turn is None else partial_turn.usage))
        raise AgentCommandError(
            f'{self.driver} CLI exit={completed.returncode}：'
            f'{_short_detail(completed.stderr)}',
            returncode=completed.returncode, partial_turn=partial_turn,
            error_kind=kind, status=status, retryable=retryable)

    def cancel(self, session_id: str | None = None) -> bool:
        return self._runner.cancel(session_id)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._runner.close()

    def __enter__(self) -> CliAgentDriver:
        return self

    def __exit__(self, exc_type: object, exc: object,
                 traceback: object) -> None:
        self.close()
