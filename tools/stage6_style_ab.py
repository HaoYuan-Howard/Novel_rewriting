#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage6 对 Stage3 定性文风包的已退役真实 A/B runner。

本工具只用于验证或复现 2026-08-24 已冻结的历史实验。生产 Stage6 已删除四块
定性入口；为了不把它们重新放回生产，本工具在自身内部封存当时的 legacy prompt 构造。

两臂唯一允许不同的产品输入是：

* ``full``：历史 A 臂，保留 ``hook_style``、书级 voice、书级 surface_habits、
  开篇逐章 style；
* ``no-stage3-qual``：删掉上述四块，以及 system prompt 中专属于它们的三句说明。

``measurable``（含 ``source_hook_shape``）、原作文案、全书事实/地图、重写开篇、
模型、effort、配方与机械重试逻辑在两臂保持不变。arm、pair、随机 nonce 和输出路径
均不写进 prompt。

安全边界：

* 不带 ``--execute`` 时只读源目录并打印计划；不建实验目录、不复制正文、不发请求；
* 真跑仍逐条使用生产 ``build_steps``（也就是 preview 的唯一 argv 构造口），付费
  argv 必须自带生产 CLI 的 ``--yes``；
* 每个 cell 使用全新的 output/ledger/cache。生产 output、生产 cache 及其祖先/子孙
  都拒绝作为实验根；
* ``--dotenv`` 只在内存中交给 ``load_config``，不复制、不打印内容或密钥；
* 20 次初始调用之外不扩样；候选自动重试累计超过 6 次时，在下一 cell 前关闭式暂停；
  65 万 prompt / 22 万 completion 只是估算，不冒充停机硬线；
* 本工具只输出严格 run manifest。候选打散、私有映射与盲评包由独立 review 工具负责。

用法（预览不会写盘）：

  python -X utf8 tools/stage6_style_ab.py \
      --source-base E:/repo/outputs \
      --experiment-root E:/repo-worktree/outputs/stage6_style_ab \
      --sid <sid-a> --sid <sid-b> --dotenv E:/repo/.env

真跑需要第二道显式确认：

  python -X utf8 tools/stage6_style_ab.py ... --execute
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts.artifacts import STAGE6_HOOK  # noqa: E402
from contracts.invariants import NODES  # noqa: E402
from contracts.schema import decode_artifact  # noqa: E402
from contracts.schema.stage6 import Hook  # noqa: E402
from novel import paths, prov  # noqa: E402
from novel.cli import exit_codes as X  # noqa: E402
from novel.config import Config, load_config  # noqa: E402
from novel.core.bands import ending_shape, hook_shape_band, sep_band_of_text  # noqa: E402
from novel.llm.client import NOTHINK_RUNGS, rungs_from_effort  # noqa: E402
from novel.llm.ledger import ledger_path  # noqa: E402
from novel.orchestrator import nodes as production_nodes  # noqa: E402
from novel.orchestrator import jobs  # noqa: E402
from novel.run import RunContext  # noqa: E402
from novel.stages.stage6 import hook, prompts  # noqa: E402


ARM_FULL = 'full'
ARM_NO_QUAL = 'no-stage3-qual'
ARMS = (ARM_FULL, ARM_NO_QUAL)
QUALITATIVE_KEYS = (
    'hook_style',
    'book_voice',
    'book_surface_habits',
    'opening_chapter_styles',
)

BOOKS = 2
REPEATS = 5
INITIAL_CALLS = BOOKS * REPEATS * len(ARMS)
RETRY_PAUSE_AFTER = 6
ESTIMATED_PROMPT_TOKENS = 650_000
ESTIMATED_COMPLETION_TOKENS = 220_000
REVIEW_PATH_LIMIT_EXCLUSIVE = 240
EXPERIMENT_ID = 'stage3-stage6-style-ab-20260824-v1'
REPO_ROOT = Path(__file__).resolve().parent.parent
PROTOCOL_FILE = REPO_ROOT / 'docs' / 'stage3-stage6-style-ab.md'
MACHINE_PROTOCOL_FILE = REPO_ROOT / 'docs' / 'stage3-stage6-style-ab.json'
RUNNER_FILE = Path(__file__).resolve()
LEGACY_FULL_SYSTEM_SHA256 = 'baf81a2805436ae6bc60757a5eda6bd0bc3a6efcbd4574d0baf169249d6fae79'

_REQUIRED_UPSTREAMS = (
    'stage0/clean_text.txt',
    'stage1/hook_copy.json',
    'stage2/global_grounding.json',
    'stage2/global_review.json',
    'stage3/style_grounding.json',
    'stage4/writer_context.json',
)

# 只移除专属于四块定性材料的陈述；机械形态、原文案和通用写作要求不动。
_SYSTEM_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    (
        '你会看到原始文案、完整故事事实、全书逐章故事地图、重写后的开篇正文，'
        '以及正文实际使用的声音和写作手法。',
        '你会看到原始文案、完整故事事实、全书逐章故事地图和重写后的开篇正文。',
    ),
    (
        '- 新文案必须与重写正文像同一本书，优先执行逐章声音与 surface_habits，'
        '不要写成编辑部剧情简介。',
        '- 新文案不要写成编辑部剧情简介。',
    ),
    (
        '新文案的最后一行照这个形态收，它就是 `hook_style.end_question` '
        '那句描述可被验证的那一面。',
        '新文案的最后一行照这个形态收。',
    ),
)

# 现役 production 在 B 臂基础上新增了机械基线与独立钩子证据说明；反构历史 A 臂时
# 与实验内的 A→B 替换不是同一张表。分开登记，避免 retired runner 反向污染生产。
_CURRENT_TO_LEGACY_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    (
        'source_blurb 中 hook_supported 表示原作者在钩子中独立给出的信息，可用于新文案；\n'
        '正文无需重复这些信息，不因正文未出现就判成编造，也不把它们改成正文必须补写的剧情。\n',
        '',
    ),
    (
        '你会看到原始文案、完整故事事实、全书逐章故事地图、重写后的开篇正文，'
        '以及原作文案的机械基线。',
        _SYSTEM_REPLACEMENTS[0][0],
    ),
    (_SYSTEM_REPLACEMENTS[1][1], _SYSTEM_REPLACEMENTS[1][0]),
    (_SYSTEM_REPLACEMENTS[2][1], _SYSTEM_REPLACEMENTS[2][0]),
)


@dataclass(frozen=True)
class Cell:
    sid: str
    book_index: int
    pair: int
    order: int
    arm: str
    source_book: Path
    output_base: Path
    cache_dir: Path
    argv: tuple[str, ...]
    check_argv: tuple[str, ...]

    @property
    def key(self) -> str:
        return f'{self.sid}/pair-{self.pair:02d}/{self.order}-{self.arm}'

    @property
    def novel_dir(self) -> Path:
        return self.output_base / self.source_book.name


@dataclass(frozen=True)
class CellResult:
    key: str
    sid: str
    pair: int
    arm: str
    artifact: Path
    hook_rel: str
    artifact_sha256: str
    ledger_sha256: str
    retries: int
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    wire_rows: int
    model: str
    efforts: tuple[str, ...]
    thinking: tuple[bool, ...]
    system_sha256: str
    user_sha256: str
    prompt_sha256: str
    generation_exit: int
    check_exit: int | None
    generation_job_id: str | None
    check_job_id: str | None
    initial_request_fingerprints: tuple[str, ...]
    retry_request_fingerprints: tuple[str, ...]


def _resolved(path: str | os.PathLike[str]) -> Path:
    raw = os.fspath(path)
    windows_spelling = raw.replace('/', '\\')
    if windows_spelling.startswith(('\\\\?\\', '\\\\.\\')):
        raise ValueError(
            '外部路径不接受显式 Windows extended/device prefix；'
            '请传普通绝对路径，由 runner 内部 I/O 自行处理长路径')
    return Path(raw).expanduser().resolve(strict=False)


def _same_or_nested(left: Path, right: Path) -> bool:
    """两条绝对路径相同或互为祖先；跨盘时 ``relative_to`` 安全返回 False。"""
    try:
        left.relative_to(right)
        return True
    except ValueError:
        pass
    try:
        right.relative_to(left)
        return True
    except ValueError:
        return False


def _io_path(path: str | os.PathLike[str]) -> str:
    """Windows 长路径 I/O 使用 extended-length 语法；manifest 仍记录普通绝对路径。"""
    raw = os.path.abspath(os.fspath(path))
    if os.name != 'nt' or raw.startswith('\\\\?\\'):
        return raw
    if raw.startswith('\\\\'):
        return '\\\\?\\UNC\\' + raw[2:]
    return '\\\\?\\' + raw


def _sha256_io(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256(path: Path) -> str:
    return _sha256_io(_io_path(path))


def _stage_files(
    root: Path, *, stages: range = range(0, 6),
) -> list[tuple[str, str]]:
    """返回 ``(POSIX 相对路径, 可直接 open 的路径)``，含超过 MAX_PATH 的文件。"""
    io_root = _io_path(root)
    rows: list[tuple[str, str]] = []
    for stage in stages:
        directory = root / f'stage{stage}'
        if not directory.is_dir():
            raise ValueError(f'{root} 缺 stage{stage}/，不能作为 Stage6 实验输入')
        for current, directories, files in os.walk(_io_path(directory)):
            directories.sort()
            files.sort()
            for name in files:
                full = os.path.join(current, name)
                rel = os.path.relpath(full, io_root).replace(os.sep, '/')
                rows.append((rel, full))
    return sorted(rows, key=lambda row: row[0])


def _git_head() -> str:
    completed = subprocess.run(
        ['git', '-c', f'safe.directory={REPO_ROOT.as_posix()}', 'rev-parse', 'HEAD'],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if completed.returncode != 0:
        raise ValueError('无法冻结 git HEAD；实验不能把代码版本记成 unknown')
    head = completed.stdout.decode('ascii', errors='strict').strip()
    if len(head) != 40:
        raise ValueError(f'git HEAD 不是完整 40 位：{head!r}')
    return head


def _tree_sha256(root: Path, *, stages: range = range(0, 6)) -> str:
    """冻结 Stage0..5 输入字节；路径与内容都入摘要。"""
    digest = hashlib.sha256()
    for rel_text, item in _stage_files(root, stages=stages):
        rel = rel_text.encode('utf-8')
        data_hash = bytes.fromhex(_sha256_io(item))
        digest.update(len(rel).to_bytes(8, 'big'))
        digest.update(rel)
        digest.update(data_hash)
    return digest.hexdigest()


def _upstream_inventory(root: Path) -> list[dict[str, str]]:
    return [
        {'rel': rel, 'sha256': _sha256_io(item)}
        for rel, item in _stage_files(root)
    ]


def _validate_source_book(book: Path) -> str:
    missing = [rel for rel in _REQUIRED_UPSTREAMS if not (book / rel).is_file()]
    drafts = list((book / 'stage5').glob('*/ch*.json')) if (book / 'stage5').is_dir() else []
    if not drafts:
        missing.append('stage5/<mode>/ch*.json')
    if missing:
        raise ValueError(f'{book} 缺 Stage6 真实生成前置：{missing}')
    return _tree_sha256(book)


def _config_for(dotenv: Path | None, cache_dir: Path) -> Config:
    """显式 dotenv 只读进 ``Config``；不把内容灌进环境，也不打印。"""
    if dotenv is not None and not dotenv.is_file():
        raise ValueError(f'--dotenv 指向的文件不存在：{dotenv}')
    configured = load_config(dotenv_path=dotenv) if dotenv is not None else load_config()
    # 只换物理 cache；模型、思考、effort 与生产 transport retry 配置均不改。
    return replace(configured, cache_dir=cache_dir)


def _cell_order(book_index: int, pair: int) -> tuple[str, str]:
    # 两本 × 五轮共十对：恰好五对 A-first、五对 B-first。
    return ARMS if (book_index + pair) % 2 else tuple(reversed(ARMS))  # type: ignore[return-value]


def _initial_rung(config: Config) -> tuple[str, bool]:
    if config.thinking_mode == 'off':
        return NOTHINK_RUNGS[0]
    return rungs_from_effort(NODES['stage6.hook'].effort)[0]


def _clone_path_peak(
    cell: Cell, inventory: Sequence[Mapping[str, str]],
) -> tuple[int, str]:
    candidates = [
        (len(str(cell.novel_dir / row['rel'])), str(row['rel']))
        for row in inventory
    ]
    return max(candidates, default=(len(str(cell.novel_dir)), ''))


def build_plan(
    *,
    source_base: str | os.PathLike[str],
    experiment_root: str | os.PathLike[str],
    sids: Sequence[str],
    dotenv: str | os.PathLike[str] | None = None,
    repeats: int = REPEATS,
) -> dict[str, Any]:
    """只读构造计划。这里与 preview 共用生产 ``build_steps``，不手拼 argv。"""
    source = _resolved(source_base)
    root = _resolved(experiment_root)
    dotenv_path = _resolved(dotenv) if dotenv is not None else None
    if not source.is_dir():
        raise ValueError(f'生产 output 不存在：{source}')
    if len(sids) != BOOKS or len(set(sids)) != BOOKS:
        raise ValueError(f'预注册实验要求恰好 {BOOKS} 个不同 sid，实得 {list(sids)}')
    if repeats < 1:
        raise ValueError('repeats 必须为正整数')
    if _same_or_nested(source, root):
        raise ValueError('实验根与生产 output 相同或互为祖先/子孙；拒绝共享 live discovery')

    # 配置只为识别生产 cache；不回显 Config（其中可能有代理凭据）。
    probe_cache = root / '__probe_cache_never_created__'
    configured = _config_for(dotenv_path, probe_cache)
    if not configured.has_key:
        raise ValueError(
            'DEEPSEEK_API_KEY 未配置；实验计划关闭式拒绝且不回显配置内容')
    production_cache = _resolved(load_config(
        dotenv_path=dotenv_path).cache_dir if dotenv_path is not None else load_config().cache_dir)
    if _same_or_nested(root, production_cache):
        raise ValueError(
            f'实验根与生产 cache 相同或互为祖先/子孙：{root} / {production_cache}')

    books: list[dict[str, Any]] = []
    cells: list[Cell] = []
    clone_peaks: dict[str, tuple[int, str]] = {}
    outputs: set[Path] = set()
    caches: set[Path] = set()
    for book_index, sid in enumerate(sids):
        source_book = paths.resolve_novel_dir(source, sid).resolve()
        source_sha = _validate_source_book(source_book)
        inventory = _upstream_inventory(source_book)
        books.append({
            'book_key': f'B{book_index + 1:02d}',
            'sid': sid,
            'source_book': str(source_book),
            'stage0_to_stage5_sha256': source_sha,
            'upstream_inventory': inventory,
        })
        for pair in range(1, repeats + 1):
            for order, arm in enumerate(_cell_order(book_index, pair), 1):
                # 物理路径不复述 sid/pair/arm；这些实验身份留在 key/manifest。
                # 20 个 cell 按冻结执行序编号，给普通 Windows rglob 留足 MAX_PATH 余量。
                cell_number = len(cells) + 1
                cell_root = root / 'c' / f'{cell_number:02d}'
                output = cell_root / 'o'
                cache = cell_root / 'k'
                if output in outputs or cache in caches:
                    raise AssertionError(f'cell 路径重复：{cell_root}')
                if _same_or_nested(output, source) or _same_or_nested(cache, production_cache):
                    raise ValueError(f'cell 试图共享生产 output/cache：{cell_root}')
                steps = production_nodes.build_steps(
                    'stage6.hook', sid, base=output, follow_check=True)
                if len(steps) != 2:
                    raise AssertionError('stage6.hook preview 应为付费生成 + 免费 check 两步')
                paid, check = steps
                if not paid.paid or '--yes' not in paid.argv:
                    raise AssertionError('生产 preview 没给 stage6.hook 付费 argv 加 --yes')
                if check.paid or '--check-only' not in check.argv:
                    raise AssertionError('生产 preview 的第二步不是免费 check-only')
                cell = Cell(
                    sid=sid, book_index=book_index, pair=pair, order=order, arm=arm,
                    source_book=source_book, output_base=output, cache_dir=cache,
                    argv=tuple(paid.argv), check_argv=tuple(check.argv),
                )
                peak, peak_rel = _clone_path_peak(cell, inventory)
                if peak >= REVIEW_PATH_LIMIT_EXCLUSIVE:
                    raise ValueError(
                        f'{cell.key} 克隆后最长路径 {peak} 字符（{peak_rel}），'
                        f'必须 < {REVIEW_PATH_LIMIT_EXCLUSIVE}；请缩短 --experiment-root')
                cells.append(cell)
                clone_peaks[cell.key] = (peak, peak_rel)
                outputs.add(output)
                caches.add(cache)

    expected = BOOKS * repeats * len(ARMS)
    if len(cells) != expected or len(outputs) != expected or len(caches) != expected:
        raise AssertionError('cell/output/cache 没有一一隔离')
    a_first = sum(cell.order == 1 and cell.arm == ARM_FULL for cell in cells)
    b_first = sum(cell.order == 1 and cell.arm == ARM_NO_QUAL for cell in cells)
    if a_first != b_first:
        raise AssertionError(f'A/B 先后不平衡：A-first={a_first}, B-first={b_first}')

    public_cells = [{
        'key': cell.key,
        'sid': cell.sid,
        'pair': cell.pair,
        'order': cell.order,
        'arm': cell.arm,
        'output_base': str(cell.output_base),
        'cache_dir': str(cell.cache_dir),
        'production_cli_argv': list(cell.argv),
        'check_cli_argv': list(cell.check_argv),
        'clone_max_path_chars': clone_peaks[cell.key][0],
        'clone_max_path_rel': clone_peaks[cell.key][1],
    } for cell in cells]
    if not PROTOCOL_FILE.is_file() or not MACHINE_PROTOCOL_FILE.is_file():
        raise ValueError(
            f'预注册协议不完整：markdown={PROTOCOL_FILE.is_file()} '
            f'machine={MACHINE_PROTOCOL_FILE.is_file()}')
    protocol_sha256 = _sha256(PROTOCOL_FILE)
    initial_effort, initial_thinking = _initial_rung(configured)
    payload = {
        'experiment_id': EXPERIMENT_ID,
        'protocol': 'stage6-stage3-qual-ab-v1',
        'protocol_file': str(PROTOCOL_FILE),
        'protocol_sha256': protocol_sha256,
        'machine_protocol_file': str(MACHINE_PROTOCOL_FILE),
        'machine_protocol_sha256': _sha256(MACHINE_PROTOCOL_FILE),
        'runner_file': str(RUNNER_FILE),
        'runner_sha256': _sha256(RUNNER_FILE),
        'git_head': _git_head(),
        'code_version': prov.code_version(),
        'source_base': str(source),
        'experiment_root': str(root),
        'config_source': {
            'dotenv_path': str(dotenv_path) if dotenv_path is not None else None,
            'precedence': 'process-environment-over-dotenv',
        },
        'books': books,
        'cells': public_cells,
        'model': NODES['stage6.hook'].model or configured.model,
        'thinking_mode': configured.thinking_mode,
        'effort': initial_effort,
        'thinking': initial_thinking,
        'node_effort_override': NODES['stage6.hook'].effort,
        'path_budget': {
            'review_safe_limit_exclusive': REVIEW_PATH_LIMIT_EXCLUSIVE,
            'max_clone_target_chars': max(
                (peak for peak, _rel in clone_peaks.values()), default=0),
        },
        'budgets': {
            'initial_calls': expected,
            'candidate_retry_pause_after': RETRY_PAUSE_AFTER,
            'estimated_prompt_tokens_not_a_stop_line': ESTIMATED_PROMPT_TOKENS,
            'estimated_completion_tokens_including_reasoning_not_a_stop_line':
                ESTIMATED_COMPLETION_TOKENS,
            'http_retries_per_rung': configured.max_retries,
        },
    }
    payload['plan_sha256'] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True,
                   separators=(',', ':')).encode('utf-8')).hexdigest()
    payload['_cells'] = cells
    payload['_dotenv'] = dotenv_path
    return payload


def _without_private(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in plan.items() if not key.startswith('_')}


def render(plan: Mapping[str, Any], *, execute: bool) -> str:
    lines = [
        '# Stage6 × Stage3 定性包 A/B',
        '',
        f'plan_sha256={plan["plan_sha256"]}',
        f'model={plan["model"]} thinking={plan["thinking_mode"]} '
        f'initial_rung=({plan["effort"]!r}, {plan["thinking"]!r}) '
        f'node_effort_override={plan["node_effort_override"]!r}',
        f'books={len(plan["books"])} cells={len(plan["cells"])} '
        f'execute={"是" if execute else "否（只读预览）"}',
        '',
        '预算：初始调用 {initial_calls}；候选重试累计 > {candidate_retry_pause_after} 暂停；'
        'prompt 估算 {estimated_prompt_tokens_not_a_stop_line:,}；completion 估算（已含 reasoning）'
        '{estimated_completion_tokens_including_reasoning_not_a_stop_line:,}；两项均不是停机硬线'
        .format(**plan['budgets']),
        '',
    ]
    for index, cell in enumerate(plan['cells'], 1):
        lines.append(
            f'[{index:02d}/{len(plan["cells"]):02d}] {cell["key"]} '
            f'cache={cell["cache_dir"]}')
        lines.append('  $ ' + production_nodes.shell_line(cell['production_cli_argv']))
    if not execute:
        lines += [
            '',
            '未加 --execute：没有创建 experiment-root/output/cache，未复制正文，未发请求。',
            'dotenv 只校验路径与配置形状；其内容和密钥没有打印。',
        ]
    return '\n'.join(lines)


def _legacy_opening_source_ordinals(ctx: dict, upto: int) -> set[int]:
    out: set[int] = set()
    for chapter in ctx.get('chapters') or []:
        if int(chapter.get('output_ordinal') or 0) <= upto:
            out.update(int(x) for x in (chapter.get('from_source_ordinals') or []) if int(x) > 0)
    return out


def _legacy_style_packet(style: dict, ctx: dict, upto: int) -> dict:
    """冻结实验时的 A 臂投影；只存在于 retired runner。"""
    source_ordinals = _legacy_opening_source_ordinals(ctx, upto)
    chapter_styles = [
        item for item in (style.get('chapter_styles') or [])
        if int(item.get('source_ordinal') or 0) in source_ordinals
    ]
    hook_meas = ((style.get('measurable') or {}).get('hook')) or None
    return {
        'hook_style': style.get('hook_style'),
        'book_voice': dict(style.get('voice') or {}),
        'book_surface_habits': style.get('surface_habits'),
        'opening_chapter_styles': chapter_styles,
        'source_hook_shape': hook_shape_band(hook_meas),
        'measurable': style.get('measurable'),
    }


def no_qual_style_packet(style: dict, ctx: dict, upto: int) -> dict:
    """从冻结 A 臂只删四块定性字段；``measurable`` 与 shape 原样保留。"""
    packet = _legacy_style_packet(style, ctx, upto)
    return {key: value for key, value in packet.items() if key not in QUALITATIVE_KEYS}


def _legacy_hook_system(
    system_builder: Callable[[], str] | None = None,
) -> str:
    """从现役无定性 system 反构历史 A 臂，并用冻结 SHA 关闭式锁定。"""
    text = (system_builder or prompts.hook_system)()
    for current, legacy in _CURRENT_TO_LEGACY_REPLACEMENTS:
        count = text.count(current)
        if count != 1:
            raise AssertionError(
                f'现役 Stage6 system 无法反构历史提示：期望 1 次，实得 {count} 次：{current!r}')
        text = text.replace(current, legacy)
    digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
    if digest != LEGACY_FULL_SYSTEM_SHA256:
        raise AssertionError(
            f'历史 A 臂 system 漂移：{digest} != {LEGACY_FULL_SYSTEM_SHA256}')
    return text


def no_qual_hook_system(
    system_builder: Callable[[], str] | None = None,
) -> str:
    """冻结 A 臂 system 的最小消融。"""
    text = _legacy_hook_system(system_builder)
    for old, new in _SYSTEM_REPLACEMENTS:
        count = text.count(old)
        if count != 1:
            raise AssertionError(f'Stage6 system 专属提示语漂移：期望 1 次，实得 {count} 次：{old!r}')
        text = text.replace(old, new)
    return text


def _historical_hook_measurement(data: object) -> dict[str, object]:
    """只复现隔离历史实验的五项测量值；不参与现役消费、freshness 或审批。"""
    measurable = data.get('measurable') if isinstance(data, dict) else None
    hook = measurable.get('hook') if isinstance(measurable, dict) else None
    hook = hook if isinstance(hook, dict) else {}
    return {key: hook.get(key) for key in (
        'chars', 'avg_sentence_chars', 'avg_paragraph_chars', 'quote_styles', 'dialogue_ratio',
    )}


def _legacy_build_material(*, arm: str, title: str, ctx: dict, style: dict,
                           grounding: dict, source_hook: str, lead: list[dict],
                           missing: list[int], upto: int) -> prompts.Material:
    """冻结实验的 user packet；生产 ``prompts`` 不再保留定性注入口。"""
    truth = {
        'entities': grounding.get('entities'),
        'canon': grounding.get('canon'),
        'arc': grounding.get('arc'),
    }
    full_map = prompts.story_map(ctx)
    styles = (_legacy_style_packet(style, ctx, upto)
              if arm == ARM_FULL else no_qual_style_packet(style, ctx, upto))
    hook_meas = ((style.get('measurable') or {}).get('hook')) or {}
    packet = {
        'title': title,
        'source_hook': source_hook,
        'source_hook_target_chars': hook_meas.get('chars'),
        'source_hook_para_sep': sep_band_of_text(source_hook),
        'source_hook_ending_shape': ending_shape(source_hook),
        'style': styles,
        'story_truth': truth,
        'whole_story_map': full_map,
        'rewritten_opening': [
            {'chapter': row.get('output_ordinal'), 'text': row.get('text') or ''}
            for row in lead
        ],
    }
    blocks = (
        '下面是本书的完整创作包。先理解原始文案为何有效，再写三条新的开篇文案。',
        json.dumps(packet, ensure_ascii=False, indent=2),
    )
    return prompts.Material(
        blocks=blocks,
        lead=tuple(lead),
        missing=tuple(missing),
        upto=upto,
        source_hook=source_hook,
        story_truth=truth,
        story_map=tuple(full_map),
        hook_mechanical=_historical_hook_measurement(style),
    )


@contextmanager
def _arm_patch(arm: str) -> Iterator[None]:
    if arm not in ARMS:
        raise ValueError(f'未知 arm={arm!r}，取值面是 {ARMS}')
    original_material = prompts.build_material
    original_system = prompts.hook_system

    def material(**kwargs) -> prompts.Material:
        return _legacy_build_material(arm=arm, **kwargs)

    def system() -> str:
        return (_legacy_hook_system(original_system) if arm == ARM_FULL
                else no_qual_hook_system(original_system))

    prompts.build_material = material
    prompts.hook_system = system
    try:
        yield
    finally:
        prompts.build_material = original_material
        prompts.hook_system = original_system


@contextmanager
def _cell_config(config: Config) -> Iterator[None]:
    """只覆盖 CLI 运行期的配置读取；密钥不进入环境、不进入日志。"""
    import novel.config as config_module

    original = config_module.load_config
    config_module.load_config = lambda **_kwargs: config
    try:
        yield
    finally:
        config_module.load_config = original


def _cli_args(argv: Sequence[str]) -> list[str]:
    expected = ['-m', production_nodes.CLI_MODULE]
    try:
        index = list(argv).index(expected[0])
    except ValueError as exc:
        raise AssertionError(f'production argv 缺 -m：{argv}') from exc
    if list(argv[index:index + 2]) != expected:
        raise AssertionError(f'production argv 的模块不是 {production_nodes.CLI_MODULE}：{argv}')
    return list(argv[index + 2:])


def _invoke_cli(
    argv: Sequence[str], *, arm: str, config: Config,
    cli_main: Callable[[Sequence[str] | None], int] | None = None,
) -> int:
    """执行 preview 的原 argv；只剥解释器前缀，不重建任何 flag。"""
    if '--yes' not in argv and '--check-only' not in argv:
        raise AssertionError('付费 production argv 缺 --yes')
    if cli_main is None:
        from novel.cli.main import main as cli_main
    with _cell_config(config), _arm_patch(arm):
        return cli_main(_cli_args(argv))


def _run_job_step(
    cell: Cell, *, check: bool, config: Config,
) -> Any:
    """经正式 jobs.run 记录 job 事件与 start/end beacon，同时保留进程内消融缝。"""
    steps = production_nodes.build_steps(
        'stage6.hook', cell.sid, base=cell.output_base, follow_check=True)
    step = steps[1 if check else 0]
    frozen_argv = cell.check_argv if check else cell.argv
    if tuple(step.argv) != frozen_argv:
        raise AssertionError(f'{cell.key} 当前 build_steps 与冻结 preview argv 漂移')

    def spawn(argv: Sequence[str], cwd: str, _env: dict, log) -> int:
        if tuple(argv) != frozen_argv or cwd != step.cwd:
            raise AssertionError('jobs.run 没有执行传入的 production Step 对象')
        stream = io.TextIOWrapper(log, encoding='utf-8', write_through=True)
        try:
            with redirect_stdout(stream), redirect_stderr(stream):
                return _invoke_cli(argv, arm=cell.arm, config=config)
        finally:
            stream.flush()
            stream.detach()

    flags = ['--check-only'] if check else []
    return jobs.run(
        cell.output_base, 'stage6.hook', cell.sid, flags,
        follow_check=False, steps=[step], spawn=spawn)


def _run_doctor(config: Config) -> None:
    """首个付费 cell 前走生产 doctor；它不发正文、不创建 cache/产物。"""
    from novel.cli.main import main as cli_main
    with _cell_config(config):
        code = cli_main(['doctor', '--llm-transport'])
    if code != X.OK:
        raise RuntimeError(f'0 成本 doctor --llm-transport 失败（exit {code}），未开始付费实验')


def _directory_nonempty(path: Path) -> bool:
    if not os.path.isdir(_io_path(path)):
        return False
    with os.scandir(_io_path(path)) as entries:
        return next(entries, None) is not None


def _preflight_copy_evidence(cell: Cell) -> list[str]:
    """复制残留只有在零付费/零运行证据时才可原地补齐。"""
    evidence: list[str] = []
    service = cell.output_base / '_service'
    if _directory_nonempty(service):
        evidence.append('_service 非空')
    if _directory_nonempty(cell.cache_dir):
        evidence.append('cache 非空')
    for stage in ('stage6', 'stage7'):
        if os.path.exists(_io_path(cell.novel_dir / stage)):
            evidence.append(f'{stage}/ 已存在')
    return evidence


def _prepare_cell(cell: Cell, *, merge_partial: bool = False) -> None:
    if not merge_partial and (cell.output_base.exists() or cell.cache_dir.exists()):
        raise ValueError(f'cell 必须从不存在的 output/cache 开始，拒绝复用：{cell.key}')
    os.makedirs(_io_path(cell.novel_dir), exist_ok=merge_partial)
    # Stage6/7 不复制：它们是本实验的结果/下游，不应混入现役旧候选或旧定稿。
    for stage in range(0, 6):
        shutil.copytree(
            _io_path(cell.source_book / f'stage{stage}'),
            _io_path(cell.novel_dir / f'stage{stage}'),
            dirs_exist_ok=merge_partial,
        )
    os.makedirs(_io_path(cell.cache_dir), exist_ok=True)
    if _tree_sha256(cell.novel_dir) != _tree_sha256(cell.source_book):
        raise AssertionError(f'{cell.key} Stage0..5 副本 SHA 不等于冻结源')


def _prompt(cell: Cell, arm: str) -> tuple[str, str]:
    ctx = RunContext.open(
        str(cell.output_base), cell.sid, 'stage6.hook', paid=False,
        write_ledger=False)
    with _arm_patch(arm):
        system, user, _material = hook.build_prompt(ctx)
    forbidden = (cell.key, str(cell.output_base), str(cell.cache_dir))
    leaked = [value for value in forbidden if value and value in system + user]
    if leaked:
        raise AssertionError(f'实验身份/路径泄漏进 prompt：{leaked}')
    return system, user


def _json_packet(user: str) -> dict[str, Any]:
    try:
        return json.loads(user.split('\n\n', 1)[1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise AssertionError('Stage6 user prompt 不再是说明段 + JSON packet') from exc


def _prompt_hashes(system: str, user: str) -> dict[str, str]:
    return {
        'system_sha256': hashlib.sha256(system.encode('utf-8')).hexdigest(),
        'user_sha256': hashlib.sha256(user.encode('utf-8')).hexdigest(),
        'combined_sha256': hashlib.sha256(
            (system + '\0' + user).encode('utf-8')).hexdigest(),
    }


def _assert_pair_prompt_contract(
    full: Cell, no_qual: Cell,
) -> tuple[dict[str, str], dict[str, str]]:
    """除四块定性数据及专属 system 语句外，两臂 prompt 必须逐字等价。"""
    system_a, user_a = _prompt(full, ARM_FULL)
    system_b, user_b = _prompt(no_qual, ARM_NO_QUAL)
    expected_system_b = system_a
    for old, new in _SYSTEM_REPLACEMENTS:
        expected_system_b = expected_system_b.replace(old, new)
    if system_b != expected_system_b:
        raise AssertionError('B 臂 system 除预注册三处替换外还有字节差')
    packet_a, packet_b = _json_packet(user_a), _json_packet(user_b)
    if not isinstance(packet_a.get('style'), dict) or not isinstance(
            packet_b.get('style'), dict):
        raise AssertionError('A/B user prompt 的 style packet 不再是 object')
    style_a = dict(packet_a['style'])
    style_b = dict(packet_b['style'])
    missing = [key for key in QUALITATIVE_KEYS if key not in style_a]
    if missing:
        raise AssertionError(f'A 臂没有完整携带四块 Stage3 定性材料：缺 {missing}')
    for key in QUALITATIVE_KEYS:
        style_a.pop(key)
    if any(key in style_b for key in QUALITATIVE_KEYS):
        raise AssertionError('B 臂仍携带 Stage3 定性材料')
    packet_a['style'] = style_a
    if packet_a != packet_b:
        raise AssertionError('A/B user prompt 除四块 Stage3 定性材料外还有字节差')
    instruction, separator, _packet_text = user_a.partition('\n\n')
    if not separator:
        raise AssertionError('A 臂 user prompt 不再是说明段 + JSON packet')
    expected_user_b = instruction + separator + json.dumps(
        packet_a, ensure_ascii=False, indent=2)
    if user_b != expected_user_b:
        raise AssertionError('B 臂 user prompt 未满足“只删四键”的逐字契约')
    return _prompt_hashes(system_a, user_a), _prompt_hashes(system_b, user_b)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ValueError(f'cell 没有成本账：{path}')
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f'{path}:{number} ledger JSONL 损坏') from exc
        if not isinstance(row, dict):
            raise ValueError(f'{path}:{number} ledger 行不是 object')
        rows.append(row)
    if not rows:
        raise ValueError(f'{path} 没有任何调用行')
    return rows


def ledger_summary(
    rows: Sequence[Mapping[str, Any]], *, sid: str, model: str,
    initial_rung: tuple[str, bool] | None = None,
) -> dict[str, int]:
    """关闭式核对一个 cell 的 wire 账；completion 已含 reasoning，绝不重复相加。"""
    prompt = 0
    completion = 0
    reasoning = 0
    main_ok = 0
    retry_purposes: set[str] = set()
    efforts: set[str] = set()
    thinking_values: set[bool] = set()
    initial_fingerprints: set[str] = set()
    retry_fingerprints: set[str] = set()
    expected_effort, expected_thinking = initial_rung or rungs_from_effort(
        NODES['stage6.hook'].effort)[0]
    for index, row in enumerate(rows, 1):
        if row.get('sid') != sid or row.get('node') != 'stage6.hook':
            raise ValueError(f'ledger row {index} 跑出实验 scope：{row.get("sid")}/{row.get("node")}')
        if row.get('model') != model:
            raise ValueError(
                f'ledger row {index} 模型漂移：{row.get("model")!r} != {model!r}')
        if row.get('cached') is not False:
            raise ValueError(f'ledger row {index} 不是新 wire 调用（cached={row.get("cached")!r}）')
        purpose = str(row.get('purpose') or '')
        is_retry = purpose.startswith('stage6.hook:retry:')
        if purpose != 'stage6.hook' and not is_retry:
            raise ValueError(f'ledger row {index} purpose 越界：{purpose!r}')
        expected_cache = 'refresh-write' if is_retry else 'default'
        if row.get('cache_policy') != expected_cache:
            raise ValueError(
                f'ledger row {index} cache_policy={row.get("cache_policy")!r}，'
                f'期望 {expected_cache!r}')
        p = row.get('prompt_tokens')
        c = row.get('completion_tokens')
        r = row.get('reasoning_tokens')
        if isinstance(p, bool) or not isinstance(p, int) or p < 0:
            raise ValueError(f'ledger row {index} prompt_tokens 未完整记账：{p!r}')
        if isinstance(c, bool) or not isinstance(c, int) or c < 0:
            raise ValueError(f'ledger row {index} completion_tokens 未完整记账：{c!r}')
        if isinstance(r, bool) or not isinstance(r, int) or r < 0:
            raise ValueError(f'ledger row {index} reasoning_tokens 未完整记账：{r!r}')
        hit, miss = row.get('prompt_cache_hit_tokens'), row.get('prompt_cache_miss_tokens')
        if hit is not None or miss is not None:
            if not isinstance(hit, int) or not isinstance(miss, int) or hit + miss != p:
                raise ValueError(
                    f'ledger row {index} provider cache 分区不闭合：hit={hit}, miss={miss}, prompt={p}')
        prompt += p
        completion += c
        reasoning += r
        effort = row.get('reasoning_effort')
        thinking = row.get('thinking')
        if not isinstance(effort, str) or not effort:
            raise ValueError(f'ledger row {index} reasoning_effort 未记录：{effort!r}')
        if not isinstance(thinking, bool):
            raise ValueError(f'ledger row {index} thinking 未记录：{thinking!r}')
        efforts.add(effort)
        thinking_values.add(thinking)
        want_effort, want_thinking = (
            (hook.CANDIDATE_RETRY_RUNGS[0][0], hook.CANDIDATE_RETRY_RUNGS[0][1])
            if is_retry else (expected_effort, expected_thinking)
        )
        if (effort, thinking) != (want_effort, want_thinking):
            raise ValueError(
                f'ledger row {index} 发生降级档/执行参数漂移：'
                f'{(effort, thinking)} != {(want_effort, want_thinking)}')
        expected_max = hook.CANDIDATE_RETRY_MAX_TOKENS if is_retry else hook.MAX_TOKENS
        if row.get('max_tokens') != expected_max or row.get('json_mode') is not True:
            raise ValueError(
                f'ledger row {index} 请求 envelope 漂移：max_tokens={row.get("max_tokens")!r}, '
                f'json_mode={row.get("json_mode")!r}')
        fingerprint = row.get('request_fingerprint')
        if not isinstance(fingerprint, str) or not fingerprint:
            raise ValueError(f'ledger row {index} request_fingerprint 未记录')
        if is_retry:
            retry_purposes.add(purpose)
            retry_fingerprints.add(fingerprint)
        else:
            initial_fingerprints.add(fingerprint)
        if row.get('outcome') == 'ok':
            if not is_retry:
                main_ok += 1
    if main_ok != 1:
        raise ValueError(f'每 cell 必须恰有一次成功的初始生成，实得 {main_ok}')
    return {
        'wire_rows': len(rows),
        'initial_ok': main_ok,
        'retry_logical': len(retry_purposes),
        'prompt_tokens': prompt,
        'completion_tokens': completion,
        'reasoning_tokens_diagnostic_subset': reasoning,
        'cached_rows': 0,
        'efforts': sorted(efforts),
        'thinking': sorted(thinking_values),
        'initial_request_fingerprints': sorted(initial_fingerprints),
        'retry_request_fingerprints': sorted(retry_fingerprints),
    }


def _artifact_of(cell: Cell) -> tuple[Path, Hook]:
    formal = cell.novel_dir / STAGE6_HOOK
    invalid = cell.novel_dir / paths.invalid_of(STAGE6_HOOK)
    found = [path for path in (formal, invalid) if path.is_file()]
    if len(found) != 1:
        raise ValueError(f'{cell.key} 必须恰有正式或 INVALID 一份 Hook，实得 {found}')
    path = found[0]
    decoded = decode_artifact(
        STAGE6_HOOK,
        json.loads(path.read_text(encoding='utf-8')),
    )
    return path, Hook.model_validate(decoded.payload)


def _finish_cell(
    cell: Cell, *, model: str, prompt_hashes: Mapping[str, str],
    generation_exit: int, check_exit: int | None,
    generation_job_id: str, check_job_id: str | None,
    initial_rung: tuple[str, bool] | None = None,
) -> CellResult:
    ledger = ledger_path(cell.output_base)
    rows = _read_jsonl(ledger)
    summary = ledger_summary(
        rows, sid=cell.sid, model=model, initial_rung=initial_rung)
    artifact_path, artifact = _artifact_of(cell)
    retries = len(artifact.provenance.candidate_retries)
    if retries != summary['retry_logical']:
        raise ValueError(
            f'{cell.key} artifact candidate_retries={retries}，'
            f'ledger 唯一 retry purpose={summary["retry_logical"]}')
    hook_rel = artifact_path.relative_to(cell.novel_dir).as_posix()
    formal = hook_rel == STAGE6_HOOK
    expected_generation_exit = X.OK if formal else X.TERMINAL_STATE
    if generation_exit != expected_generation_exit or not generation_job_id:
        raise ValueError(
            f'{cell.key} generation job 元数据不闭合：'
            f'exit={generation_exit}, job_id={generation_job_id!r}')
    if formal and (check_exit not in (X.OK, X.CHECK_FAILED) or not check_job_id):
        raise ValueError(
            f'{cell.key} check job 元数据不闭合：'
            f'exit={check_exit}, job_id={check_job_id!r}')
    if not formal and (check_exit is not None or check_job_id is not None):
        raise ValueError(f'{cell.key} INVALID Hook 不应带 check job 元数据')
    return CellResult(
        key=cell.key, sid=cell.sid, pair=cell.pair, arm=cell.arm,
        artifact=artifact_path, hook_rel=hook_rel,
        artifact_sha256=_sha256(artifact_path), ledger_sha256=_sha256(ledger),
        retries=retries,
        prompt_tokens=summary['prompt_tokens'],
        completion_tokens=summary['completion_tokens'],
        reasoning_tokens=summary['reasoning_tokens_diagnostic_subset'],
        wire_rows=summary['wire_rows'],
        model=model,
        efforts=tuple(summary['efforts']),
        thinking=tuple(summary['thinking']),
        system_sha256=prompt_hashes['system_sha256'],
        user_sha256=prompt_hashes['user_sha256'],
        prompt_sha256=prompt_hashes['combined_sha256'],
        generation_exit=generation_exit,
        check_exit=check_exit,
        generation_job_id=generation_job_id,
        check_job_id=check_job_id,
        initial_request_fingerprints=tuple(summary['initial_request_fingerprints']),
        retry_request_fingerprints=tuple(summary['retry_request_fingerprints']),
    )


def budget_problem(results: Sequence[CellResult]) -> str | None:
    retries = sum(row.retries for row in results)
    if retries > RETRY_PAUSE_AFTER:
        return f'候选自动重试累计 {retries} > {RETRY_PAUSE_AFTER}'
    return None


def _atomic_json(path: Path, value: Any) -> bytes:
    data = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
            + '\n').encode('utf-8')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_bytes(data)
    os.replace(temporary, path)
    return data


def _run_manifest(
    plan: Mapping[str, Any], results: Sequence[CellResult], *, status: str,
    failure: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """runner 与独立 blind-review 工具之间的严格、无候选正文接口。"""
    cells: list[Cell] = list(plan['_cells'])
    result_by = {(row.sid, row.pair, row.arm): row for row in results}
    cell_by = {(row.sid, row.pair, row.arm): row for row in cells}
    books: list[dict[str, Any]] = []
    for book in plan['books']:
        sid = book['sid']
        rounds: list[dict[str, Any]] = []
        for round_number in range(1, REPEATS + 1):
            arms: dict[str, Any] = {}
            for arm in ARMS:
                result = result_by.get((sid, round_number, arm))
                if result is None:
                    continue
                cell = cell_by[(sid, round_number, arm)]
                arms[arm] = {
                    'arm': arm,
                    'base': str(cell.output_base),
                    'cache_dir': str(cell.cache_dir),
                    'hook_rel': result.hook_rel,
                    'artifact_path': str(result.artifact),
                    'artifact_sha256': result.artifact_sha256,
                    'prompt': {
                        'system_sha256': result.system_sha256,
                        'user_sha256': result.user_sha256,
                        'combined_sha256': result.prompt_sha256,
                    },
                    'upstream': {
                        'stage0_to_stage5_sha256': book['stage0_to_stage5_sha256'],
                        'inventory': book['upstream_inventory'],
                    },
                    'ledger': {
                        'path': str(ledger_path(cell.output_base)),
                        'sha256': result.ledger_sha256,
                        'wire_rows': result.wire_rows,
                        'cached_rows': 0,
                        'prompt_tokens': result.prompt_tokens,
                        # DeepSeek completion 已含 reasoning；下一格只是诊断子集，不相加。
                        'completion_tokens_including_reasoning': result.completion_tokens,
                        'reasoning_tokens_diagnostic_subset': result.reasoning_tokens,
                        'model': result.model,
                        'efforts': list(result.efforts),
                        'thinking': list(result.thinking),
                        'candidate_retries': result.retries,
                        'initial_request_fingerprints': list(
                            result.initial_request_fingerprints),
                        'retry_request_fingerprints': list(
                            result.retry_request_fingerprints),
                        'generation_exit': result.generation_exit,
                        'check_exit': result.check_exit,
                        'generation_job_id': result.generation_job_id,
                        'check_job_id': result.check_job_id,
                    },
                    'run_metadata': {
                        'order': cell.order,
                        'production_cli_argv': list(cell.argv),
                        'check_cli_argv': list(cell.check_argv),
                        'model': result.model,
                        'node_effort': plan['effort'],
                        'initial_thinking': plan['thinking'],
                        'node_effort_override': plan['node_effort_override'],
                        'local_cache_hits': 0,
                        'candidate_retries': result.retries,
                    },
                }
            rounds.append({'round': round_number, 'arms': arms})
        books.append({
            'book_key': book['book_key'],
            # run manifest 是 sealed 输入，不是 blind 包；review 工具不得外发这一格。
            'sid': sid,
            'source_book': book['source_book'],
            'rounds': rounds,
        })
    manifest = {
        '_schema_version': 1,
        'experiment': {
            'experiment_id': plan['experiment_id'],
            'plan_sha256': plan['plan_sha256'],
            'status': status,
            'source_base': plan['source_base'],
            'experiment_root': plan['experiment_root'],
            'config_source': plan['config_source'],
        },
        'protocol': {
            'name': plan['protocol'],
            'file': plan['protocol_file'],
            'sha256': plan['protocol_sha256'],
            'machine_file': plan['machine_protocol_file'],
            'machine_sha256': plan['machine_protocol_sha256'],
            'runner_file': plan['runner_file'],
            'runner_sha256': plan['runner_sha256'],
            'git_head': plan['git_head'],
            'code_version': plan['code_version'],
            'arms': list(ARMS),
        },
        'books': books,
        # 独立 review 工具生成后另落自己的 manifest；runner 不抢先造映射/面板。
        'panels': [],
        'totals': {
            'completed_cells': len(results),
            'initial_call_limit': INITIAL_CALLS,
            'candidate_retries': sum(row.retries for row in results),
            'prompt_tokens': sum(row.prompt_tokens for row in results),
            'completion_tokens_including_reasoning': sum(
                row.completion_tokens for row in results),
        },
        'failure': dict(failure) if failure is not None else None,
    }
    return manifest


def _write_run_manifest(
    plan: Mapping[str, Any], results: Sequence[CellResult], *, status: str,
    failure: Mapping[str, Any] | None = None,
) -> None:
    root = Path(plan['experiment_root'])
    _atomic_json(
        root / 'run-manifest.json',
        _run_manifest(plan, results, status=status, failure=failure),
    )


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')).hexdigest()


def _write_complete_seal(plan: Mapping[str, Any]) -> dict[str, Any]:
    """COMPLETE 是 write-once 自校验封条；同内容续跑幂等，不同内容拒覆盖。"""
    root = Path(plan['experiment_root'])
    manifest_path = root / 'run-manifest.json'
    if not manifest_path.is_file():
        raise RuntimeError('不能封 COMPLETE：run-manifest.json 不存在')
    payload: dict[str, Any] = {
        'experiment_id': plan['experiment_id'],
        'plan_sha256': plan['plan_sha256'],
        'run_manifest': str(manifest_path),
        'run_manifest_sha256': _sha256(manifest_path),
    }
    payload['output_sha256'] = _canonical_sha256(payload)
    seal_path = root / 'COMPLETE.json'
    if seal_path.is_file():
        try:
            current = json.loads(seal_path.read_text(encoding='utf-8'))
        except json.JSONDecodeError as exc:
            raise RuntimeError('已有 COMPLETE.json 损坏；write-once 拒绝覆盖') from exc
        if not isinstance(current, dict) or _canonical_sha256(current) != _canonical_sha256(payload):
            raise RuntimeError('已有 COMPLETE.json 与本次封条不同；write-once 拒绝覆盖')
        return payload
    _atomic_json(seal_path, payload)
    return payload


def _assert_code_frozen(plan: Mapping[str, Any], *, where: str) -> None:
    facts = {
        'git_head': _git_head(),
        'code_version': prov.code_version(),
        'protocol_sha256': _sha256(PROTOCOL_FILE),
        'machine_protocol_sha256': _sha256(MACHINE_PROTOCOL_FILE),
        'runner_sha256': _sha256(RUNNER_FILE),
    }
    drift = {key: {'frozen': plan[key], 'current': value}
             for key, value in facts.items() if plan[key] != value}
    if drift:
        raise RuntimeError(f'{where} 代码/协议漂移，拒绝混跑：{drift}')


def _assert_config_frozen(
    plan: Mapping[str, Any], config: Config, *, where: str,
) -> None:
    """密钥只查存在性；会改变请求配方的公开配置必须与 plan 一致。"""
    if not config.has_key:
        raise RuntimeError(f'{where} API key 已缺失；不回显配置内容')
    facts = {
        'model': NODES['stage6.hook'].model or config.model,
        'thinking_mode': config.thinking_mode,
        'effort': _initial_rung(config)[0],
        'thinking': _initial_rung(config)[1],
        'http_retries_per_rung': config.max_retries,
    }
    frozen = {
        'model': plan['model'],
        'thinking_mode': plan['thinking_mode'],
        'effort': plan['effort'],
        'thinking': plan['thinking'],
        'http_retries_per_rung': plan['budgets']['http_retries_per_rung'],
    }
    drift = {key: {'frozen': frozen[key], 'current': value}
             for key, value in facts.items() if frozen[key] != value}
    if drift:
        raise RuntimeError(f'{where} 请求配置漂移，拒绝混跑：{drift}')


def _prepare_or_validate_cell(cell: Cell, *, expected_upstream_sha256: str) -> None:
    source_sha = _tree_sha256(cell.source_book)
    if source_sha != expected_upstream_sha256:
        raise ValueError(
            f'{cell.key} 的生产 Stage0..5 已偏离 plan 冻结 SHA，拒绝复制/续跑')
    if not cell.output_base.exists() and not cell.cache_dir.exists():
        _prepare_cell(cell)
    else:
        if not cell.novel_dir.is_dir():
            raise ValueError(f'{cell.key} 是半截 cell（缺 output/book），拒绝猜测恢复')
        complete = all((cell.novel_dir / f'stage{stage}').is_dir()
                       for stage in range(0, 6))
        clone_sha = _tree_sha256(cell.novel_dir) if complete else None
        if clone_sha != expected_upstream_sha256:
            if evidence := _preflight_copy_evidence(cell):
                raise ValueError(
                    f'{cell.key} Stage0..5 副本不完整且已有运行/付费证据 {evidence}；'
                    '拒绝覆盖或误判为可重试')
            _prepare_cell(cell, merge_partial=True)
        elif not cell.cache_dir.exists():
            os.makedirs(_io_path(cell.cache_dir))
        elif not cell.cache_dir.is_dir():
            raise ValueError(f'{cell.key} cache 路径不是目录')
    if _tree_sha256(cell.novel_dir) != expected_upstream_sha256:
        raise ValueError(f'{cell.key} 的 Stage0..5 副本已偏离 plan 冻结 SHA，拒绝续跑')


def _has_artifact(cell: Cell) -> bool:
    return any((cell.novel_dir / rel).is_file() for rel in (
        STAGE6_HOOK, paths.invalid_of(STAGE6_HOOK)))


def _existing_job_metadata(cell: Cell) -> dict[str, Any]:
    generations = []
    checks = []
    for record in jobs.fold(jobs.read_events(cell.output_base)):
        flags = set(record.start.flags)
        if '--check-only' in flags:
            checks.append(record)
        elif record.start.node == 'stage6.hook':
            generations.append(record)
    if len(generations) != 1 or len(checks) > 1:
        raise ValueError(
            f'{cell.key} jobs 轨迹不唯一：generation={len(generations)}, '
            f'check={len(checks)}')
    generation = generations[0]
    checked = checks[0] if checks else None
    return {
        'generation_exit': generation.exit_code,
        'generation_job_id': generation.job_id,
        'check_exit': checked.exit_code if checked is not None else None,
        'check_job_id': checked.job_id if checked is not None else None,
    }


def _pause(
    plan: Mapping[str, Any], results: Sequence[CellResult], *,
    cell: Cell | None, reason: str, detail: Mapping[str, Any] | None = None,
) -> None:
    failure = {
        'cell': cell.key if cell is not None else None,
        'reason': reason,
        **(dict(detail) if detail is not None else {}),
    }
    _write_run_manifest(plan, results, status='paused', failure=failure)
    _atomic_json(Path(plan['experiment_root']) / 'PAUSED.json', failure)


def _resume_preflight_rebase_allowed(
    root: Path, cells: Sequence[Cell], stored: Mapping[str, Any],
    current: Mapping[str, Any],
) -> bool:
    """只允许“尚未进入 doctor/作业”的复制失败吸收 runner 基础设施修复。"""
    if any((root / name).exists() for name in (
            'run-manifest.json', 'PAUSED.json', 'COMPLETE.json')):
        return False
    volatile = {'plan_sha256', 'runner_sha256', 'code_version'}
    old_stable = {key: value for key, value in stored.items() if key not in volatile}
    new_stable = {key: value for key, value in current.items() if key not in volatile}
    if old_stable != new_stable:
        return False
    return not any(_preflight_copy_evidence(cell) for cell in cells)


def _rebase_preflight_plan(
    root: Path, stored: Mapping[str, Any], current: Mapping[str, Any],
) -> None:
    old_sha = str(stored.get('plan_sha256') or '')
    archive = root / f'plan.preflight-failed.{old_sha[:12] or "unknown"}.json'
    if archive.is_file():
        archived = json.loads(archive.read_text(encoding='utf-8'))
        if archived != dict(stored):
            raise ValueError(f'既有 preflight plan 归档内容冲突：{archive}')
    else:
        _atomic_json(archive, dict(stored))
    _atomic_json(root / 'plan.json', dict(current))
    audit = {
        'reason': 'windows-long-path-copy-preflight-repair',
        'paid_attempts': 0,
        'old_plan_sha256': old_sha,
        'new_plan_sha256': current['plan_sha256'],
        'old_runner_sha256': stored.get('runner_sha256'),
        'new_runner_sha256': current['runner_sha256'],
        'archived_plan': str(archive),
    }
    audit_path = root / 'PLAN_PREFLIGHT_REBASE.json'
    if audit_path.is_file():
        if json.loads(audit_path.read_text(encoding='utf-8')) != audit:
            raise ValueError(f'既有 preflight rebase 审计内容冲突：{audit_path}')
    else:
        _atomic_json(audit_path, audit)


def execute(plan: Mapping[str, Any], *, resume: bool = False) -> list[CellResult]:
    root = Path(plan['experiment_root'])
    plan_path = root / 'plan.json'
    cells: list[Cell] = list(plan['_cells'])
    if resume:
        if not plan_path.is_file():
            raise ValueError('--resume 要求现有 plan.json；没有它无法证明 cell 属于同一实验')
        stored = json.loads(plan_path.read_text(encoding='utf-8'))
        if stored.get('plan_sha256') != plan['plan_sha256']:
            current = _without_private(plan)
            if not _resume_preflight_rebase_allowed(
                    root, cells, stored, current):
                raise ValueError(
                    f'--resume 计划漂移：stored={stored.get("plan_sha256")}, '
                    f'current={plan["plan_sha256"]}')
            _rebase_preflight_plan(root, stored, current)
    else:
        if root.exists() and any(root.iterdir()):
            raise ValueError(f'实验根必须不存在或为空；续跑请显式加 --resume：{root}')
        root.mkdir(parents=True, exist_ok=True)
        _atomic_json(plan_path, _without_private(plan))

    _assert_code_frozen(plan, where='实验开始/恢复时')
    dotenv: Path | None = plan['_dotenv']
    results: list[CellResult] = []

    by_pair: dict[tuple[str, int], list[Cell]] = {}
    upstream_by_sid = {
        book['sid']: book['stage0_to_stage5_sha256'] for book in plan['books']}
    for cell in cells:
        by_pair.setdefault((cell.sid, cell.pair), []).append(cell)

    prompt_hashes: dict[str, dict[str, str]] = {}
    for pair_cells in by_pair.values():
        for cell in pair_cells:
            _prepare_or_validate_cell(
                cell, expected_upstream_sha256=upstream_by_sid[cell.sid])
        full = next(cell for cell in pair_cells if cell.arm == ARM_FULL)
        no_qual = next(cell for cell in pair_cells if cell.arm == ARM_NO_QUAL)
        full_hash, no_qual_hash = _assert_pair_prompt_contract(full, no_qual)
        prompt_hashes[full.key] = full_hash
        prompt_hashes[no_qual.key] = no_qual_hash

    # 先从每个隔离 cell 自证恢复状态。只认“完整 artifact + 完整 ledger”；
    # 有 wire 账却没 artifact 的付费失败绝不自动重发。
    saw_pending = False
    for cell in cells:
        config = _config_for(dotenv, cell.cache_dir)
        _assert_config_frozen(plan, config, where=f'{cell.key} 恢复扫描时')
        ledger = ledger_path(cell.output_base)
        if _has_artifact(cell):
            if saw_pending:
                raise ValueError(f'{cell.key} 在未完成 cell 之后已有结果，执行顺序被外部改写')
            expected_model = NODES['stage6.hook'].model or config.model
            metadata = _existing_job_metadata(cell)
            formal = (cell.novel_dir / STAGE6_HOOK).is_file()
            expected_generation_exit = X.OK if formal else X.TERMINAL_STATE
            if metadata['generation_exit'] != expected_generation_exit:
                _pause(
                    plan, results, cell=cell,
                    reason='已有 artifact 但 generation job/end beacon 不闭合；不推测退出码',
                    detail=metadata,
                )
                raise RuntimeError(f'{cell.key} 生成 job 轨迹不闭合，已封存且不会重发')
            if formal and metadata['check_exit'] is None:
                _assert_code_frozen(plan, where=f'{cell.key} 恢复免费 check 前')
                checked = _run_job_step(cell, check=True, config=config)
                metadata['check_exit'] = checked.exit_code
                metadata['check_job_id'] = checked.job_id
            if formal and metadata['check_exit'] not in (X.OK, X.CHECK_FAILED):
                _pause(
                    plan, results, cell=cell,
                    reason='已有正式 Hook 的免费 check 没有合法退出码', detail=metadata)
                raise RuntimeError(f'{cell.key} 免费 check 未闭合；付费生成不会重发')
            try:
                results.append(_finish_cell(
                    cell, model=expected_model, prompt_hashes=prompt_hashes[cell.key],
                    initial_rung=(plan['effort'], plan['thinking']),
                    **metadata))
            except (OSError, ValueError, AssertionError) as exc:
                _pause(plan, results, cell=cell, reason='已有 cell 完整性校验失败',
                       detail={'error': str(exc)})
                raise RuntimeError(f'{cell.key} 已有付费结果不可采纳且不会重发：{exc}') from exc
            continue
        saw_pending = True
        ledger_nonempty = ledger.is_file() and ledger.stat().st_size > 0
        cache_nonempty = any(cell.cache_dir.iterdir())
        if ledger_nonempty or cache_nonempty:
            _pause(
                plan, results, cell=cell,
                reason='付费尝试留下 ledger/cache 但没有 Hook；拒绝自动重复计费',
                detail={
                    'ledger': str(ledger),
                    'ledger_sha256': _sha256(ledger) if ledger_nonempty else None,
                    'cache_nonempty': cache_nonempty,
                },
            )
            raise RuntimeError(
                f'{cell.key} 有付费失败证据但没有产物；已保留进度，需人工裁决后再授权替代 cell')

    if len(results) == len(cells):
        _write_run_manifest(plan, results, status='complete')
        _write_complete_seal(plan)
        return results

    # doctor 不发正文/密钥；每次恢复都重新探一次，避免拿旧网络状态开付费 cell。
    first_pending = cells[len(results)]
    doctor_config = _config_for(dotenv, first_pending.cache_dir)
    _assert_config_frozen(plan, doctor_config, where='doctor 前')
    _run_doctor(doctor_config)
    _write_run_manifest(plan, results, status='running')

    for index, cell in enumerate(cells[len(results):], len(results) + 1):
        if problem := budget_problem(results):
            _pause(plan, results, cell=cell, reason=problem)
            raise RuntimeError(f'实验已关闭式暂停：{problem}；未执行 {cell.key} 及其后 cell')
        _assert_code_frozen(plan, where=f'{cell.key} 执行前')
        config = _config_for(dotenv, cell.cache_dir)
        _assert_config_frozen(plan, config, where=f'{cell.key} 执行前')
        if any(cell.cache_dir.iterdir()):
            raise ValueError(f'{cell.key} cache 在执行前非空')
        print(f'[{index:02d}/{len(cells):02d}] 执行 {cell.key}')
        generation = _run_job_step(cell, check=False, config=config)
        code = generation.exit_code
        if code is None:
            raise RuntimeError(f'{cell.key} jobs.run 没有真实退出码')
        _assert_code_frozen(plan, where=f'{cell.key} 生成后')
        # GateBlocked 会保存 INVALID 后返回 2；它仍是可盲评的已付费结果。
        formal = cell.novel_dir / STAGE6_HOOK
        invalid = cell.novel_dir / paths.invalid_of(STAGE6_HOOK)
        check_code: int | None = None
        check_job_id: str | None = None
        if code == X.OK and formal.is_file():
            checked = _run_job_step(cell, check=True, config=config)
            check_code = checked.exit_code
            check_job_id = checked.job_id
            if check_code not in (X.OK, X.CHECK_FAILED):
                raise RuntimeError(f'{cell.key} 免费复检异常退出 {check_code}')
        elif not (code == X.TERMINAL_STATE and invalid.is_file()):
            ledger = ledger_path(cell.output_base)
            _pause(
                plan, results, cell=cell,
                reason='生产 CLI 未交出可采纳 Hook；拒绝自动重发',
                detail={
                    'generation_exit': code,
                    'generation_job_id': generation.job_id,
                    'ledger': str(ledger),
                    'ledger_sha256': _sha256(ledger) if ledger.is_file() else None,
                },
            )
            raise RuntimeError(f'{cell.key} production exit={code}；进度已封存，不会重复付费')
        expected_model = NODES['stage6.hook'].model or config.model
        try:
            result = _finish_cell(
                cell, model=expected_model, prompt_hashes=prompt_hashes[cell.key],
                initial_rung=(plan['effort'], plan['thinking']),
                generation_exit=code, check_exit=check_code,
                generation_job_id=generation.job_id, check_job_id=check_job_id)
        except (OSError, ValueError, AssertionError) as exc:
            _pause(
                plan, results, cell=cell,
                reason='产物/ledger/protocol 完整性校验失败；拒绝自动重发',
                detail={'error': str(exc), 'generation_job_id': generation.job_id},
            )
            raise RuntimeError(f'{cell.key} protocol violation：{exc}') from exc
        results.append(result)
        _write_run_manifest(plan, results, status='running')

    if problem := budget_problem(results):
        _pause(plan, results, cell=None, reason=problem)
        raise RuntimeError(f'20 个 cell 已完成，但候选自动重试越线：{problem}')
    _write_run_manifest(plan, results, status='complete')
    _write_complete_seal(plan)
    return results


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--source-base', required=True, help='含两本现役书的只读生产 Output 根')
    parser.add_argument('--experiment-root', required=True,
                        help='全新、隔离且应被 gitignore 的实验根')
    parser.add_argument('--sid', action='append', required=True,
                        help='恰好给两个不同 sid；每个参数写一次')
    parser.add_argument('--dotenv', default=None,
                        help='显式配置文件；只在内存读取，不复制、不打印内容')
    parser.add_argument('--execute', action='store_true',
                        help='第二道确认：真复制输入并运行 20 次付费 Stage6')
    parser.add_argument('--resume', action='store_true',
                        help='续跑同一 plan；逐 cell 自证完成态，绝不重复已成功付费 cell')
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.resume and not args.execute:
        parser.error('--resume 必须与 --execute 一起使用')
    try:
        plan = build_plan(
            source_base=args.source_base,
            experiment_root=args.experiment_root,
            sids=args.sid,
            dotenv=args.dotenv,
        )
        print(render(plan, execute=args.execute))
        if not args.execute:
            return 0
        execute(plan, resume=args.resume)
    except (OSError, ValueError, AssertionError, RuntimeError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
