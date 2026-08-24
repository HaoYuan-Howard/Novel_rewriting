#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把一组当前契约产物复制到临时书目录，供写盘测试使用。"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from contracts.artifacts import (
    STAGE2_GLOBAL_EDIT, STAGE4_PLAN_EDIT, STAGE6_HOOK_EDIT,
    STAGE7_NAME_MAP_EDIT,
)
from contracts.schema import decode_artifact, encode_artifact
from tests import corpus

STAGE3_INPUTS = (
    'stage1/main_text.json',
    'stage1/hook_copy.json',
    'stage2/global_grounding.json',
    'stage3/style_grounding.json',
)


def books_with_stage3() -> list[str]:
    return sorted({
        artifact.book_dir
        for artifact in corpus.iter_artifacts()
        if artifact.rel == 'stage3/style_grounding.json'
    })


def current(book: str, rel: str):
    artifact = next(
        (item for item in corpus.iter_artifacts()
         if item.book_dir == book and item.rel == rel),
        None,
    )
    return corpus.load_current(artifact) if artifact is not None else None


# 调用侧沿用这个短名；返回值已经是严格验证过的当前契约，不做裁剪。
cut = current


#: 全链根。**它不是一份「输入选择」，是这本书的身份**——所以由沙箱无条件搬，
#: 不由每个调用侧各自记得写进 `rels`。
CHAIN_ROOT = 'stage0/clean_text.txt'

#: 人工决策集（`--apply-edit` 的输入）。**它们是「必要的可选项」**：
#: 一本走过 apply-edit 的书，被编辑的那份产物把决策集记成了一条**真实上游边**
#: （`novel/consumption.py::CONSUMED_PROJECTIONS` 给它们各登记了一格投影）。
#: 沙箱不带 ⇒ 幽灵边 ⇒ 那份产物判 `unknown/ghost`；而「明确当前」是审批凭据
#: （`novel/orchestrator/approvals.py::_proof_for_seen`）与 stage7 交付闸的**硬前提**
#: ⇒ 缺一份 `stage2/global_edit.json` 会让 `s2` 填不出，再由 `_GATE_REQUIREMENTS`
#: 级联堵死 `s4`→`s5`→`s6`→`s7`，表现成一批与它毫无字面关系的审批用例红。
#:
#: ⚠ **必须条件搬**：没走过 apply-edit 的书没有这一份，无条件写进 `rels` 会撞
#: `Sandbox.__init__` 的 assert。用 `edits_of(book)`。
#: ⚠ **名单从 `contracts/artifacts.py` 派生，不在这里手抄第二份**——
#:   「沙箱漏一条上游边」这个形状到 2026-08-18 已经出现三次：
#:   `stage0/clean_text.txt`（17 条红）、`stage7/name_map_edit.json`、`stage2/global_edit.json`。
HUMAN_EDITS = (STAGE2_GLOBAL_EDIT, STAGE4_PLAN_EDIT,
               STAGE6_HOOK_EDIT, STAGE7_NAME_MAP_EDIT)


def edits_of(book: str) -> tuple[str, ...]:
    """这本书盘上**真有**的那几份人工决策集（没有就不返回，交给调用侧直接拼进 `rels`）。"""
    return tuple(rel for rel in HUMAN_EDITS if current(book, rel) is not None)


def decide_global_review(box: 'Sandbox', *, actor: str = 'reviewer',
                         reason: str = '测试前置：逐条已核实') -> None:
    """把这本书的 stage2 审阅面**逐条判 PASS** —— `s2` 审批闸的前置事实。

    ⚠⚠ 现役语料三本的审阅项**全是 `pending`**（从没有人做过决策），而 `s2` 要求
    `global_review_status(...)` 的 `approved` 为真（当前 **且**逐条 PASS，见
    `novel/orchestrator/approvals.py::_require_typed_gate_decision`）
    ⇒ 不摆这条前置事实，`s2` 永远填不出，再由 `_GATE_REQUIREMENTS` 级联堵死
    `s4`→`s5`→`s6`→`s7`，表现成一批与「审阅决策」毫无字面关系的红。
    人工决策是本层被测行为的**输入**不是它的结论，摆它是前置事实不是绕守卫
    （同 `tests/test_api_edits.py::_stamp_chain` 的自述）。

    ⚠⚠ **审阅面从 `build_review(grounding)` 现算，只改 `state`/`actor`/`reason` 三格**，
    身份四元组一个字不动：那三格恰恰是新鲜度**不看**的（理由写在
    `novel/store.py::_global_review_freshness`），而手搓一份 item 会让它按构造
    永远算不出 `fresh`，`stage2/global_review.json` 又在 `SELF_FRESH_DETERMINISTIC` 里
    ⇒ 写盘口当场 `ProvenanceRefused`。这个坑本仓踩过一次，见 `tests/test_views_inbox.py`。
    """
    from contracts.artifacts import STAGE2_GLOBAL_GROUNDING, STAGE2_GLOBAL_REVIEW
    from novel import prov
    from novel.orchestrator.state import readonly_ctx
    from novel.quality.global_review import build_review
    from novel.store import ArtifactStore

    expected = build_review(box.read(STAGE2_GLOBAL_GROUNDING))
    decided = expected.model_copy(update={'items': [
        item.model_copy(update={
            'state': 'PASS', 'actor': actor, 'reason': reason,
            'decided_at': '2026-08-18T00:00:00+00:00'})
        for item in expected.items
    ]})
    store = ArtifactStore(readonly_ctx(box.dir, 'stage2.global', prov.code_version()))
    store.write(STAGE2_GLOBAL_REVIEW, decided)


def source_resolves_in_a_sandbox(book: str) -> bool:
    """这本书的 `stage0` 新鲜度在**沙箱里**答得出吗（记录的源路径自己还在盘上）。

    ⚠⚠ `stage0/removal_report.json` 的新鲜度**不走通用来源账**：它拿记录里的
    `original_path` 去比 `normalized_source_sha1`（`novel/store.py::_stage0_freshness`）。
    那条路径失效时才由 `novel/core/source.py::resolve_recorded_source` 按
    `<base>/_service/uploads/<sha256>/<name>` 回落重锚——**而沙箱的 base 里没有那一层**
    ⇒ 该产物判 `unknown/ghost`，`novel/orchestrator/approvals.py::_proof_for_seen`
    当场抛 `ApprovalRequired`，`s2` 填不出，再由 `_GATE_REQUIREMENTS` 级联堵死
    `s4`→`s5`→`s6`→`s7`。报出来的是「预置审批链失败」，与「源文件」三个字毫无字面关系。

    ⚠⚠ **不要改成「把源 txt 拷进沙箱的 `_service/uploads/`」。** 试过，它会当场撞两条守卫：
    `tests/test_api_http.py::test_the_service_writes_nothing_at_all` 与
    `tests/test_approvals.py` 的「GET 不许落服务状态」都断言沙箱里**根本没有 `_service/`**。
    那两条守的是 `R-11.3`，比这里的方便重要得多 ⇒ 改成**选一本答得出的书**。
    """
    from novel.core.source import resolve_recorded_source

    recorded = (current(book, 'stage0/removal_report.json') or {}).get('original_path')
    #: `base=None` = 只问记录的那条路径本身，不猜——沙箱里能读到的正是这一支。
    return bool(recorded) and resolve_recorded_source(recorded, None) is not None


class Sandbox:
    """一本书的临时副本。**恒带全链根**，`rels` 里的字节形态产物按字节原样拷。

    ⚠⚠ `stage0/clean_text.txt` 是全链根：任何一次 `free_provenance` /
    `llm_provenance` / `rewrite_provenance` 都要拿它算 `root_clean_sha1`
    （`RunContext._require_single_root`），写盘口那道闸还要拿它比对
    （`ArtifactStore._refuse_bad_provenance`）⇒ **一个会写盘的沙箱没有它就一定抛
    `ProvenanceRefused`**，而 CLI 那一侧会把它渲染成退出码 3「结构性拒绝（这是 bug）」
    ——一条纯粹的环境缺料被报成代码自相矛盾。

    ⚠ 曾经的形态是「各调用侧自己记得把它写进 `rels`」：两处手抄了拷贝代码，
    其余七个文件漏掉，于是 2026-08-16 那次全量语料跑里 17 条红在同一句话上。
    ⇒ 收进构造函数，**只有一个落点**。
    """

    def __init__(self, book: str, rels: tuple[str, ...] = STAGE3_INPUTS) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.dir = self.root / book
        self.book = book
        self.sid = book.rsplit('_', 1)[-1]
        for rel in (CHAIN_ROOT, *rels):
            path = self.dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            if not rel.endswith('.json'):
                source = corpus.corpus_root() / book / rel
                assert source.is_file(), f'{book} 少了 {rel}，沙箱无法建立'
                # 按字节拷：文本模式会把 LF 翻成 CRLF，而这三份产物是 LF-only 契约。
                path.write_bytes(source.read_bytes())
                continue
            data = current(book, rel)
            assert data is not None, f'{book} 少了 {rel}，沙箱无法建立'
            path.write_text(
                json.dumps(encode_artifact(rel, data), ensure_ascii=False),
                encoding='utf-8',
            )

    def read(self, rel: str) -> dict:
        raw = json.loads((self.dir / rel).read_text(encoding='utf-8'))
        return decode_artifact(rel, raw).payload

    def write(self, rel: str, data: dict) -> None:
        path = self.dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(encode_artifact(rel, data), ensure_ascii=False),
            encoding='utf-8',
        )

    def close(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
