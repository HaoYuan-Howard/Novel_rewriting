#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agent 进程结束后的交付终验（新链坐标：候选根 delivery manifest 就绪核对）。

【坐标（STR-06 关闭批改接）】旧链交付合格判定与旧坐标（base+sid 书目录直读）
过渡桩已随关闭批删除；本模块不再回答「旧链成书是否合格」。新链就绪记录是
`str06.stage7.assemble` **最后提交**的 `stage7/final/delivery_manifest.json`
（文件先落盘、manifest 最后提交），由 `novel/views/str06.py::delivery_view`
经 `GET /api/str06/candidates/{candidate}/delivery` 供人读；本终验按 agent
代跑的同一坐标（base+sid → `find_novel_dir` → 候选根 workflow 身份标记）
只读核对同一份记录。

【终验证明什么】三件事同时成立才通过：
① started 事件与当前代码指纹同一（`unknown == unknown` 不是「同一版代码」）；
② 候选根可定位且带 workflow 身份标记（`runtime/candidate_workflow.json`）；
③ delivery manifest 可读，且其 `final_text_sha256` 与当前成书字节一致
（manifest 在而成书被改/缺席都证明不了可交付）。manifest 恒带机械披露
（回执只覆盖章级机械检查，semantic=unverified 如实保留），故通过终态只有
`ready_with_disclosures`，从不裸 `ready`。任何一环证明不了都倒 `unknown`，
绝不把未知洗成通过（I-42）；代码指纹变化是 `stopped`。

⚠ 这不是另一个流水线节点，也不读取 agent 的自然语言日志；`main` 的入口
形状保持不变，供 agent 代跑终验（`novel/orchestrator/agent.py`）以子进程
调用（`-m novel.orchestrator.delivery`）。下载不经过本模块——生产下载面按
盘上路径直读（`novel/api/routes/str06.py::get_book`）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from pathlib import Path

from contracts.story_paths import MANIFEST
from contracts.schema.orchestration import AgentDeliveryOutcome
from novel import paths, prov
from novel.prov import CODE_UNKNOWN

#: 候选根 workflow 身份标记（与 `novel/views/str06.py::WORKFLOW_REL` 同一标记；
#: 候选根内运行目录的固定名同 `views/str06.py::RUN_DIRNAME`）。
_WORKFLOW_REL = 'runtime/candidate_workflow.json'
_RUN_DIRNAME = 'run'
#: 成书文件名形态（与 `novel/views/str06.py::_TITLE` 同一判据）。
_TITLE_RE = re.compile(r'^(.*)_rewriting\.txt$')


def _outcome(state: str, reason: str) -> AgentDeliveryOutcome:
    return AgentDeliveryOutcome(state=state, reason=reason, checked_at=int(time.time()))


def _candidate_run(base: str | Path, sid: str) -> Path | None:
    """base+sid → 候选根运行目录；定位不到或没有身份标记返回 None。"""
    root = paths.find_novel_dir(base, sid)
    if root is None:
        return None
    for run in (root / _RUN_DIRNAME, root):
        if (run / _WORKFLOW_REL).is_file():
            return run
    return None


def _final_text_files(run: Path) -> list[Path]:
    directory = run / 'stage7' / 'final'
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir()
                  if p.is_file() and _TITLE_RE.match(p.name))


def assess(base: str | Path, sid: str, *, start_code_version: str | None,
           ) -> AgentDeliveryOutcome:
    """只读终验；任何无法证明的部分倒向 stopped/unknown，绝不倒成 ready。"""
    if start_code_version is None:
        return _outcome('unknown', 'started 事件没有代码指纹，无法证明本次跑使用同一版代码')
    current_code = prov.code_version()
    # ⚠ `unknown == unknown` **不是「同一版代码」**。两端都问不出 git 指纹时相等只是
    #   两次都失败，而它恰好落在最需要出声的场景（不在 git 树里跑、git 不可用）。
    #   把它读成通过，就是「不知道」被压成「没问题」——五态建模禁的那一格。
    if CODE_UNKNOWN in (start_code_version, current_code):
        return _outcome(
            'unknown',
            f'代码指纹不可用（start={start_code_version}，end={current_code}）——'
            '证明不了运行期间用的是同一版代码')
    if current_code != start_code_version:
        return _outcome(
            'stopped',
            f'运行期间代码指纹变化：start={start_code_version}，end={current_code}')
    run = _candidate_run(base, sid)
    if run is None:
        return _outcome(
            'unknown',
            f'base+sid（sid={sid}）定位不到带 workflow 身份标记的候选根，'
            '无法证明本次跑可交付；新链候选根见 GET /api/str06/candidates')
    from novel.store import Str06Repository
    try:
        manifest = Str06Repository(run).inspect(MANIFEST)
    except FileNotFoundError:
        return _outcome(
            'unknown',
                f'候选根没有 delivery manifest（{MANIFEST}）：'
            '本次跑未到 str06.stage7.assemble 交付，不把盘面冒充可交付')
    except Exception as exc:  # noqa: BLE001 —— manifest 损坏也只得到 unknown
        return _outcome(
            'unknown', f'delivery manifest 不可读：{type(exc).__name__}: {exc}')
    if not isinstance(manifest, dict) or not manifest.get('final_text_sha256'):
        return _outcome('unknown', 'delivery manifest 形状不完整（缺 final_text_sha256）')
    files = _final_text_files(run)
    digests = {hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    expected = str(manifest['final_text_sha256'])
    if expected not in digests:
        return _outcome(
            'unknown',
            'delivery manifest 的 final_text_sha256 与当前成书字节不一致'
            f'（manifest={expected[:12]}…，盘上 {len(files)} 个成书文件无一匹配）——'
            '盘面在 manifest 提交后被改动或损坏，证明不了可交付')
    chapters = len(manifest.get('chapters') or ())
    detail = (
        f'候选根 delivery manifest 就绪：{chapters} 章、final_text_sha256='
        f'{expected[:12]}… 与当前成书字节一致、delivery_identity='
        f"{manifest.get('delivery_identity') or '未登记'}。")
    disclosure = manifest.get('disclosure') or ''
    if disclosure:
        detail += f'manifest 披露：{disclosure}'
    return _outcome('ready_with_disclosures', detail)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='只读交付终验，结果输出到 stdout')
    parser.add_argument('--base', required=True)
    parser.add_argument('--sid', required=True)
    parser.add_argument('--start-code-version', required=True)
    args = parser.parse_args(argv)
    outcome = assess(args.base, args.sid, start_code_version=args.start_code_version)
    print(json.dumps(outcome.model_dump(mode='json'), ensure_ascii=False, indent=2))
    return 0 if outcome.state in ('ready', 'ready_with_disclosures') else 1


if __name__ == '__main__':
    raise SystemExit(main())
