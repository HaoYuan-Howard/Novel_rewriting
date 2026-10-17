#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""成书与源章的 20 字窗重合读数；不设阈值，不改变流水线判定。

度量使用 Stage5 已接受的改名前纯正文，避免改名打断窗口造成假的低重合。
采用前逐章核对当前 ChapterReceipt 的章序、状态、正文原始字节 hash、协议、
机械检查项及人物/普通义两表，再经 Stage7 的身份绑定改名重放。只有重放结果
与成书该章逐字节一致才度量。缺证据或存在表外改动时明确“量不了”。

同一称谓可属于多人、分别改成不同名字；普通同形文字保持原样。因此不存在
全局正反替换表，也不从最终姓名逆猜人物。检查只证明本次读数对应这份成书，
不替代当前章包新鲜度、交付 manifest 或文学质量验收。重合采用 window_overlap，
不扣逐字豁免，与旧链 overlap_excl_verbatim 不逐位可比。

用法：python tools/book_overlap.py --base <outputs> --sid <书 sid> [--json-out <路径>]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from pathlib import Path
from typing import Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts.artifacts import str06_body_receipt_rel, str06_body_rel  # noqa: E402
from contracts.invariants import BOOK_BLOCK_SEP  # noqa: E402
from contracts.schema import decode_artifact  # noqa: E402
from contracts.schema.naming import NameMapV5  # noqa: E402
from contracts.schema.receipt import (  # noqa: E402
    BODY_MECHANICAL_CHECKS, BODY_VALIDATOR_VERSION, ChapterReceipt,
    RENDERER_VERSION, WRITER_PROTOCOL_VERSION,
)
from contracts.schema.source import CharactersArtifactV6  # noqa: E402
from contracts.schema.stage1 import MainText  # noqa: E402
from novel.core.name_materials import name_candidate_literals  # noqa: E402
from novel.core.name_text import validate_resolved_name_text  # noqa: E402
from novel.core.text import window_overlap  # noqa: E402
from novel.paths import artifact_path, book_txt_rel, resolve_novel_dir  # noqa: E402
from novel.stages.stage7 import apply_bound_names, compile_bound_replacements  # noqa: E402


def split_book(text: str) -> dict[int, str]:
    """按现役装配格式“第N章\\n正文”分章，保留正文全部字节对应的字符。

    一章可有多个空行段落；裸数字行不是章标记。只接受连续章序，不把旧链
    数字块当作当前交付；不 strip 正文，避免把表外空白改动伪装成字节相等。
    """
    out: dict[int, str] = {}
    current: int | None = None
    chunks: list[str] = []
    for block in text.split(BOOK_BLOCK_SEP):
        head, newline, rest = block.partition('\n')
        following = 1 if current is None else current + 1
        if newline and head == f'第{following}章':
            if current is not None:
                out[current] = BOOK_BLOCK_SEP.join(chunks)
            current, chunks = following, [rest]
        elif current is not None:
            chunks.append(block)
    if current is not None:
        out[current] = BOOK_BLOCK_SEP.join(chunks)
    return out


def _read_payload(novel_dir: Path, rel: str):
    raw = json.loads(artifact_path(novel_dir, rel).read_bytes().decode('utf-8'))
    return decode_artifact(rel, raw).payload


def _verified_original(
    novel_dir: Path, ordinal: int, delivered: str,
    replacements: Mapping[str, Mapping[str, str]], candidates: tuple[str, ...],
) -> tuple[str, int]:
    """同一接受正文及两表正向重放；不做逆向姓名匹配或工具私有改名。"""
    body_bytes = artifact_path(novel_dir, str06_body_rel(ordinal)).read_bytes()
    original = body_bytes.decode('utf-8')
    raw = _read_payload(novel_dir, str06_body_receipt_rel(ordinal))
    if not isinstance(raw, dict) or type(raw.get('chapter_ordinal')) is not int:
        raise ValueError('回执没有有效的整数章序')
    receipt = ChapterReceipt.model_validate(raw)
    if receipt.chapter_ordinal != ordinal or receipt.status != 'accepted':
        raise ValueError('回执章序不匹配或状态并非 accepted')
    if (receipt.protocol_version != WRITER_PROTOCOL_VERSION
            or receipt.renderer_version != RENDERER_VERSION
            or receipt.validator_version != BODY_VALIDATOR_VERSION
            or receipt.mechanical_checks != BODY_MECHANICAL_CHECKS):
        raise ValueError('回执不是当前写手/渲染/机械校验协议，不能证明当前身份声明')
    if hashlib.sha256(body_bytes).hexdigest() != receipt.prose_sha256:
        raise ValueError('Stage5 正文字节与回执 prose_sha256 脱钩')
    problems = validate_resolved_name_text(
        original, name_mentions=receipt.name_mentions, non_name_spans=receipt.non_name_spans,
        surfaces={char_id: tuple(table) for char_id, table in replacements.items()},
        candidate_literals=candidates)
    if problems:
        raise ValueError('回执身份声明不成立：' + '；'.join(str(problem) for problem in problems))
    replayed, errors = apply_bound_names(original, receipt.name_mentions, replacements)
    if errors:
        raise ValueError('身份绑定改名重放失败：' + '；'.join(errors))
    if replayed.encode('utf-8') != delivered.encode('utf-8'):
        raise ValueError('身份绑定改名重放与成书该章不一致，存在表外改动或版本不匹配')
    changed = sum(replacements[mention.char_id][mention.surface] != mention.surface
                  for mention in receipt.name_mentions)
    return original, changed


def measure(base: Path, sid: str) -> dict:
    """证据完整才给读数；未知不记为零，也不静默缩小章节分母。"""
    novel_dir = resolve_novel_dir(base, sid)
    book_path = artifact_path(novel_dir, book_txt_rel(novel_dir))
    try:
        chapters = split_book(book_path.read_bytes().decode('utf-8'))
        if not chapters:
            raise ValueError('成书没有可识别的现役章节；旧格式不能冒充当前交付')
        source = MainText.model_validate(_read_payload(novel_dir, 'stage1/main_text.json'))
        characters = CharactersArtifactV6.model_validate(
            _read_payload(novel_dir, 'stage2/characters/characters.json'))
        names = NameMapV5.model_validate(_read_payload(novel_dir, 'stage7/naming/name_map.json'))
        replacements, errors = compile_bound_replacements(characters, names)
        if errors:
            raise ValueError('身份改名决定不闭合：' + '；'.join(errors))
    except (OSError, ValueError, TypeError) as exc:
        raise SystemExit(f'无法度量：正式成书、源章或身份改名证据缺失/不合法：{exc}') from exc
    source_text = {chapter.chapter_ordinal: chapter.text for chapter in source.root}
    candidates = name_candidate_literals(characters.name_projection(), characters.name_structure)
    rows = []
    for ordinal in sorted(set(source_text) | set(chapters)):
        row = {'output_ordinal': ordinal, 'overlap': None}
        if ordinal not in source_text:
            row['why'] = '源文没有这一章'
        elif ordinal not in chapters:
            row['why'] = '成书缺少这一章'
        else:
            try:
                original, changed = _verified_original(
                    novel_dir, ordinal, chapters[ordinal], replacements, candidates)
            except (OSError, ValueError, TypeError) as exc:
                row['why'] = str(exc)
            else:
                row.update(overlap=window_overlap(original, source_text[ordinal]),
                           chars=len(original), name_changes_verified=changed)
        rows.append(row)
    return {'sid': sid, 'novel_dir': str(novel_dir), 'book': str(book_path), 'chapters': rows}


def main() -> int:
    parser = argparse.ArgumentParser(description='成书 vs 源章的 20 字窗重合率（只读度量）')
    parser.add_argument('--base', required=True)
    parser.add_argument('--sid', required=True)
    parser.add_argument('--json-out')
    args = parser.parse_args()
    result = measure(Path(args.base), args.sid)
    rows = result['chapters']
    print(f"成书：{result['book']}")
    print('口径：核对当前回执与身份改名重放，使用已接受的改名前 Stage5 正文。')
    print(f"{'ch':>4}{'重合':>9}{'字数':>8}  说明")
    for row in rows:
        value = row['overlap']
        shown = '量不了' if value is None else f'{value:.3f}'
        print(f"{row['output_ordinal']:>4}{shown:>9}{row.get('chars', 0):>8}  {row.get('why', '')}")
    measured = [row['overlap'] for row in rows if row['overlap'] is not None]
    if measured:
        print(f'逐章：中位 {statistics.median(measured):.3f} · '
              f'最高 {max(measured):.3f} · 最低 {min(measured):.3f} · n={len(measured)}')
    print(f"量不了：{[row['output_ordinal'] for row in rows if row['overlap'] is None] or '无'}")
    print('这是度量，不设阈值；全量 20 字窗重合，不扣逐字豁免。')
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
