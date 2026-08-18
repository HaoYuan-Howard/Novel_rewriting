#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
assemble.py —— `stage7.assemble`：把替换后的文案与正文拼成 `stage7/book.txt`。

**它是交付物，不是审阅稿。** 里面没有地方写「缺章」抬头横幅
——所以四类残缺态**一律拒写**（`R-10.14`），而不是写一份带警告的 txt。

〔`R-10.8` 那一行「谁在查」标的代码落点：**`assemble_txt` 是唯一的组装路径**，
章分隔与换行都是常量，不是参数。〕
"""
from __future__ import annotations

from contracts.invariants import BOOK_BLOCK_SEP, BOOK_CHAPTER_SEP
from contracts.schema.report import CheckReport
from contracts.schema.stage7 import BookTxt, NameMap
from novel.errors import (
    ApprovalRequired, BlockingFindingsUnresolved, DeliveryIncomplete, GateBlocked,
    ProvenanceRefused, UpstreamMissing, UpstreamStale, UpstreamUnproven,
)
from novel.run import RunContext
from novel.stages.stage7.rename import (
    BOOK_TXT, REL, _INVALID, _Upstreams, _hook_drift, _judge, _judged_drift,
    _report, approval_subject_sha256, stored_blocking_rules,
)
from novel.stages.stage7.subs import apply_subs, sub_table
from novel.store import ArtifactStore


def assemble_txt(hook_text: str, recs: list[dict], subs: dict[str, str],
                 kept: list[str] | tuple[str, ...] | set[str]) -> str:
    """替换后的文案 + 各章正文 → `book.txt` 的**全部字节**。

    形态：`文案 \\n\\n 1 \\n\\n 第1章正文 \\n\\n 2 \\n\\n …` + 末尾一个换行。
    **不写「第 N 章」**，章分隔就是一个裸阿拉伯数字单独成行；
    **没有任何审阅元信息**（不写书名、不写章标题、不写重合度）。

    ⚠ 换行只有 LF：写盘走 `store.write_text`，它按字节写并且**拿到 `\\r` 一律拒收**
    （归一是内容决策，属产地；写盘口只保证平台不掺东西）。
    ⚠ 原子落盘也在写盘口——并发读者读不到半截文件。
    """
    pat, _table = sub_table(subs, kept)
    parts: list[str] = []
    if hook_text:
        parts.append(hook_text.strip())
    for r in recs:
        body, _cnt, _claimed = apply_subs(r.get('text') or '', subs, pat, kept)
        parts.append(BOOK_CHAPTER_SEP.format(r.get('output_ordinal')))
        parts.append(body.strip())
    return BOOK_BLOCK_SEP.join(parts) + '\n'


def _require_mapping_current(
    ctx: RunContext,
    store: ArtifactStore,
    art: NameMap,
    up: _Upstreams,
) -> None:
    """Fail closed before a delivery is written or returned.

    Approval belongs to the current formal mapping only.  Provenance freshness
    covers ordinary inputs/root; chapter and hook projections have their own
    typed bindings and are checked here as well.
    """
    if store.exists(_INVALID):
        raise GateBlocked(
            f'{_INVALID} 仍在盘上；当前人物表有未裁决问题，不能交付。'
            f'提升它跑 `stage7.rename --revalidate --actor <id> --reason <why>`，'
            f'或先改一行走 `--apply-edit <name_map_edit.json 路径>`（都 0 成本）',
            invalid_path=str(store.path(_INVALID)))
    if art.approved is None:
        raise ApprovalRequired('人物映射还没定稿——先跑 stage7.approve，再 stage7.assemble')
    freshness = store.freshness(REL)
    # ⚠ **`stale` 与 `unknown` 抛的不是同一条**：前者是一句可复算的断言（哪条边变了），
    #   后者只是「来源账证明不了」。合成一句 `!= fresh`，报告就会替一件算不出来的事
    #   编一句「它已经变了」——而两者要人做的下一件事本来就不同。
    if freshness.state == 'stale':
        raise UpstreamStale(
            f'{REL} 已过期（{freshness.reason}）；先跑 `stage7.rename --recheck`（0 成本）'
            '重建当前根上的那一份——旧批准不能给过期输入授权')
    if freshness.state != 'fresh':
        raise UpstreamUnproven(
            f'{REL} 证明不了自己出自当前根（{freshness.state}/{freshness.reason}）。'
            '交付闸不接受「不知道」：先跑 `stage7.rename --recheck`；'
            '它若仍判不出（这份产物没有足够来源事实），只能从 stage7.rename 整份重建')
    current_subject = approval_subject_sha256(art)
    if art.approved.subject_sha256 != current_subject:
        raise ApprovalRequired(
            '人物映射的人工批准不再绑定当前决策面；'
            '请刷新人物表并重新运行 stage7.approve')
    hard = stored_blocking_rules(art)
    if hard:
        raise BlockingFindingsUnresolved(
            f'人物映射仍有阻断级问题 {hard}，不能组装交付物——'
            f'`--apply-edit` 改一行或 `--recheck` 按当前判据重判（都 0 成本）',
            rules=hard)
    stale, missing = _judged_drift(art, up)
    hook_stale = _hook_drift(art, up)
    # ⚠ 与上面那条 `freshness == 'stale'` 是**同一件事的另一种量法**：这次不是来源账说的，
    #   是逐份成稿/文案的消费指纹自己对不上。⇒ 同一个类，同一条恢复命令。
    if stale or missing or hook_stale:
        raise UpstreamStale(
            '人物映射不再绑定当前正文/文案：'
            f'stale={stale}, missing={missing}, hook_stale={hook_stale}；'
            '先跑 stage7.rename --recheck（0 成本）再组装')

    if up.hook_art is None:
        raise UpstreamMissing('stage6/hook.json 不在盘上——先完成 stage6')
    from novel.stages.stage6 import semantic as hook_semantic
    hook_semantic.require_approved_clear(store, up.hook_art)

    bad: list[str] = []
    if up.dup:
        bad.append(f'同章双份：{up.dup}（哪一份是成品无从判断，删掉不要的那一份）')
    if up.missing:
        bad.append(f'计划有而成稿无：{up.missing}（先把它们写出来）')
    if not up.hook_text:
        bad.append('开篇文案还没定稿（txt 的第一段就是它：先跑 `stage6.approve --pick N`）')
    if bad:
        # ⚠ **一次报全**：三条同时成立且互不依赖，逐条失败会让人补完一样才发现还差另一样。
        #   三项各自 typed（恢复路各不相同），消费方按字段路由而不是 parse 这段人话。
        raise DeliveryIncomplete(
            '成书是交付物，不是审阅稿——txt 里没有地方写「缺章」那种抬头横幅：\n    '
            + '\n    '.join(bad),
            duplicated=up.dup, missing=up.missing, hook_unapproved=not up.hook_text)


def read_current_book(ctx: RunContext) -> str:
    """Return the delivery only when every approval/freshness binding is current."""
    store = ArtifactStore(ctx)
    if not store.exists(REL):
        raise UpstreamMissing(f'{REL} 不在盘上——先跑 stage7.rename')
    art, _raw = store.read_own(NameMap, REL)
    up = _Upstreams(ctx, store)
    _require_mapping_current(ctx, store, art, up)
    if art.book_txt is None or not store.exists(BOOK_TXT):
        raise UpstreamMissing(f'{BOOK_TXT} 没有当前组装账——先运行 stage7.assemble')
    freshness = store.freshness(BOOK_TXT)
    # ⚠ 与上面那道闸同一把尺子：交付物**证明不了当前**与**已知过期**是两条恢复路，
    #   下载端点也不许把它们说成同一句（`R-10.14`：不以「文件存在」冒充可交付）。
    if freshness.state == 'stale':
        raise UpstreamStale(
            f'{BOOK_TXT} 已不是当前组装账那一份（{freshness.reason}），拒绝下载；'
            '重跑 `stage7.assemble`（0 成本）')
    if freshness.state != 'fresh':
        raise UpstreamUnproven(
            f'{BOOK_TXT} 证明不了自己出自当前组装账'
            f'（{freshness.state}/{freshness.reason}），拒绝下载；'
            '重跑 `stage7.assemble`（0 成本）')
    text = store.read_text(BOOK_TXT)
    # ⚠ 同上：freshness 之外的第二把量尺（字数/章数），量的仍是「盘上这份还是不是
    #   当前组装账那一份」⇒ 同一个类，别在这里退回抽象基类。
    if len(text) != art.book_txt.chars or len(up.recs) != art.book_txt.chapters:
        raise UpstreamStale(
            f'{BOOK_TXT} 规模账与当前内容不一致：'
            f'chars={len(text)}/{art.book_txt.chars}, '
            f'chapters={len(up.recs)}/{art.book_txt.chapters}')
    return text


def run_assemble(ctx: RunContext) -> tuple[str, NameMap, CheckReport]:
    """`stage7.assemble`：定稿之后组装成书。**0 成本，写盘。**

    ⚠⚠ **四类残缺态在这里硬拦，而不在 `measure.collect_chapters` 里。**
    那个函数会被复检经 HTTP 工作线程调用，而 `SystemExit` 是 `BaseException`
    ——一次「这本书还没写完」就会杀掉整个服务。**这条是付过一次代价的。**
    这里是 CLI 的调用点，抛的是 `TerminalState` 那一族（显式终态，不打栈）。

    ⚠ **抛的是那一族里的具体类，不是基类本身**：`novel/api/errors.py::status_of`
    对两个抽象基类刻意不给状态码（「加一个类就得决定一次」）⇒ 抛裸 `TerminalState`
    的那条路经 HTTP 会变成 500，把「按设计走不下去了」说成「服务器内部错误」。
    七道闸各自的类见 `_require_mapping_current` 与 `read_current_book`。

    ⚠ 「文案未定稿」也是四类之一，而**正因为这道闸挡在前面**，
    「成书里没有开篇文案」这一态**结构上不可达** ⇒ `book_txt.has_hook` 已按待决④ 裁掉。
    """
    store = ArtifactStore(ctx)
    if not store.exists(REL):
        raise UpstreamMissing(f'{REL} 不在盘上——先跑 stage7.rename')
    up = _Upstreams(ctx, store)
    art, _raw = store.read_own(NameMap, REL)
    _require_mapping_current(ctx, store, art, up)

    subs = dict(art.subs)
    kept = list(art.kept)
    hook_new, _cnt, _claimed = apply_subs(up.hook_text, subs, None, kept)
    text = assemble_txt(hook_new, up.recs, subs, kept)
    from novel import prov
    new = art.model_copy(update={
        'book_txt': BookTxt(sha1=prov.content_sha1(text.encode('utf-8')),
                            # ⚠ **全链第三个 `chars` 口径**：含空白，**并且含章号行与
                            #   全部块间分隔符**。别拿它和另外两个 `chars` 做任何比较。
                            chars=len(text), chapters=len(up.recs)),
        # ⚠ **`_provenance` 一个字不动**：组装没有改任何 LLM 产的字节，
        #   它只是把已有的映射应用了一遍并把交付物的账记下来。
    })
    with store.history_transaction(REL, BOOK_TXT):
        p = store.write_text(BOOK_TXT, text)
        store.write(REL, new)
        freshness = store.freshness(BOOK_TXT)
        if freshness.state != 'fresh':
            raise ProvenanceRefused(
                f'{BOOK_TXT} was assembled but cannot prove itself current: '
                f'{freshness.state}/{freshness.reason}; map and delivery were rolled back')
    return str(p), new, _report(ctx, store, _judge(ctx, store, new, up), new)
