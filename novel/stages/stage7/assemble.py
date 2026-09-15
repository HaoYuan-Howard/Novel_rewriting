#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
assemble.py —— `stage7.assemble`：把替换后的文案与正文拼成成书 `stage7/<书名>_rewriting.txt`。

**它是交付物，不是审阅稿。** 里面没有地方写「缺章」抬头横幅
——所以缺章与未批准文案**一律拒写**（`R-10.14`），而不是写一份带警告的 txt。

〔`R-10.8` 那一行「谁在查」标的代码落点：**`assemble_txt` 是唯一的组装路径**，
章分隔与换行都是常量，不是参数。〕
"""
from __future__ import annotations

from contracts.invariants import BOOK_BLOCK_SEP, BOOK_CHAPTER_SEP
from contracts.schema.report import CheckReport
from contracts.schema.stage7 import BookTxt, NameMap
from novel.errors import (
    ApprovalRequired, ArtifactUnreadable, BlockingFindingsUnresolved,
    DeliveryIncomplete, GateBlocked, ProvenanceRefused, UpstreamMissing,
    UpstreamStale, UpstreamUnproven,
)
from novel.run import RunContext
from novel.stages.stage7.rename import (
    REL, _INVALID, _Upstreams, _hook_drift, _judge, _judged_drift,
    _commit_owned, _report, approval_proof_sha256, approval_subject_sha256,
    book_txt_rel, stored_blocking_rules,
)
from novel.stages.stage7.subs import apply_subs, sub_table
from novel.store import ArtifactStore


def assemble_txt(hook_text: str, recs: list[dict], subs: dict[str, str],
                 kept: list[str] | tuple[str, ...] | set[str]) -> str:
    """替换后的文案 + 各章正文 → 成书的**全部字节**。

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
    # ⚠ 本函数每一条拒绝都把「发生了什么」留在 message、「下一条命令」放进 `hint`
    #   （`novel/errors.py::NovelError`）。**不要把命令写回 message**：拼在一起之后，
    #   「这条终态有没有出路」在消费方那边就只能靠读中文去猜。
    if store.exists(_INVALID):
        raise GateBlocked(
            f'{_INVALID} 仍在盘上；当前人物表有未裁决问题，不能交付',
            invalid_path=str(store.path(_INVALID)),
            hint='stage7.rename --revalidate --actor <id> --reason <why>（0 成本提升），'
                 '或先 --apply-edit <name_map_edit.json 路径> 改一行')
    if art.approved is None:
        raise ApprovalRequired(
            '人物映射还没定稿',
            hint='先读取当前 Stage7 approval workpack，再运行 stage7.approve '
                 '--actor <id> --reason <why> --review-token <当前工作包 token>')
    freshness = store.freshness(REL)
    # ⚠ **`stale` 与 `unknown` 抛的不是同一条**：前者是一句可复算的断言（哪条边变了），
    #   后者只是「来源账证明不了」。合成一句 `!= fresh`，报告就会替一件算不出来的事
    #   编一句「它已经变了」——而两者要人做的下一件事本来就不同。
    if freshness.state == 'stale':
        raise UpstreamStale(
            f'{REL} 已过期（{freshness.reason}）；旧批准不能给过期输入授权',
            hint='stage7.rename --recheck（0 成本，重建当前根上的那一份）')
    if freshness.state != 'fresh':
        raise UpstreamUnproven(
            f'{REL} 证明不了自己出自当前根（{freshness.state}/{freshness.reason}）；'
            '交付闸不接受「不知道」',
            # ⚠ 这一格**不写死一条**：`--recheck` 判不出来时（这份产物没有足够来源事实）
            #   剩下的只有整份重建，而那可能是一次付费 ⇒ 说清两段，别只给便宜的那一条。
            hint='先 stage7.rename --recheck（0 成本）；仍判不出则只能从 stage7.rename '
                 '整份重建（可能付费）')
    current_subject = approval_subject_sha256(art)
    if art.approved.subject_sha256 != current_subject:
        raise ApprovalRequired(
            '人物映射的人工批准不再绑定当前决策面',
            hint='先读取当前 Stage7 approval workpack，再运行 stage7.approve '
                 '--actor <id> --reason <why> --review-token <当前工作包 token>')
    hard = stored_blocking_rules(art)
    if hard:
        raise BlockingFindingsUnresolved(
            f'人物映射仍有阻断级问题 {hard}，不能组装交付物',
            rules=hard,
            hint='stage7.rename --apply-edit <name_map_edit.json 路径> 改一行，'
                 '或 stage7.rename --recheck 按当前判据重判（都 0 成本）')
    stale, missing = _judged_drift(art, up)
    hook_stale = _hook_drift(art, up)
    # ⚠ 与上面那条 `freshness == 'stale'` 是**同一件事的另一种量法**：这次不是来源账说的，
    #   是逐份成稿/文案的消费指纹自己对不上。⇒ 同一个类，同一条恢复命令。
    if stale or missing or hook_stale:
        raise UpstreamStale(
            '人物映射不再绑定当前正文/文案：'
            f'stale={stale}, missing={missing}, hook_stale={hook_stale}',
            hint='stage7.rename --recheck（0 成本）再组装')

    if up.hook_art is None:
        raise UpstreamMissing('stage6/hook.json 不在盘上', hint='先完成 stage6')
    from novel.stages.stage6 import semantic as hook_semantic
    hook_semantic.require_approved_clear(store, up.hook_art)

    # ⚠ 两项各自「差什么」进 `bad`（message），「怎么补」进 `todo`（hint）。
    #   `hint` 是**一条时给一条、多条时逐条列全**：这道闸的全部价值就是一次说清，
    #   而人只能按其中一条去敲下一个命令。
    bad: list[str] = []
    todo: list[str] = []
    if up.missing:
        bad.append(f'计划有而成稿无：{up.missing}')
        todo.append(f'把 {up.missing} 写出来（stage5.write，可能付费）')
    if not up.hook_text:
        bad.append('开篇文案还没定稿（txt 的第一段就是它）')
        todo.append('stage6.approve --auto 自动定稿（0 成本）；人工改选用 --pick N')
    if bad:
        # ⚠ **一次报全**：两条同时成立且互不依赖，逐条失败会让人补完一样才发现还差另一样。
        #   两项各自 typed（恢复路各不相同），消费方按字段路由而不是 parse 这段人话。
        raise DeliveryIncomplete(
            '成书是交付物，不是审阅稿——txt 里没有地方写「缺章」那种抬头横幅：\n    '
            + '\n    '.join(bad),
            missing=up.missing, hook_unapproved=not up.hook_text,
            hint='；'.join(todo))

    # 工作包绑定是最后一道人工授权闸，而不是诊断遮罩：HARD、上游漂移、缺章等
    # 已有 typed 恢复路必须先把真实问题报全；所有分支通过后仍须在写盘前证明当前批准。
    if approval_proof_sha256(store, art) is None:
        raise ApprovalRequired(
            '人物映射旧批准没有绑定当前代码/规则/人物证据工作包',
            hint='刷新 Stage7 approval workpack 后重跑 stage7.approve（0 成本）')


def require_stage2_delivery_approved(ctx: RunContext) -> None:
    """组装与下载共用当前 Stage2 统一机械准入；旧根只配旧代码回滚。

    Stage2 语义审阅退役后，这里不再宣称联合语义 PASS，只要求全部必要产物
    机械可用（可读、当前、无未恢复 INVALID、机械检查通过）。
    """
    from novel.orchestrator import mechanical_admission as admission
    gate = admission.stage4_ready(ctx.novel_dir.parent, ctx.sid)
    if not gate.ok:
        from novel.errors import ApprovalRequired
        raise ApprovalRequired(
            'Stage2 机械准入未通过：' + '；'.join(gate.blocking))


def read_current_book(
    ctx: RunContext,
) -> str:
    """Return the delivery only when every approval/freshness binding is current."""
    # 下载是交付的一部分；已有 txt 不能绕过后来变红/变旧的 Stage2 人工闸。
    require_stage2_delivery_approved(ctx)
    store = ArtifactStore(ctx)
    if not store.exists(REL):
        raise UpstreamMissing(f'{REL} 不在盘上', hint='先跑 stage7.rename')
    art, _raw = store.read_own(NameMap, REL)
    up = _Upstreams(ctx, store)
    _require_mapping_current(ctx, store, art, up)
    book_txt = book_txt_rel(store)
    if art.book_txt is None or not store.exists(book_txt):
        raise UpstreamMissing(f'{book_txt} 没有当前组装账', hint='先运行 stage7.assemble')
    freshness = store.freshness(book_txt)
    # ⚠ 与上面那道闸同一把尺子：交付物**证明不了当前**与**已知过期**是两条恢复路，
    #   下载端点也不许把它们说成同一句（`R-10.14`：不以「文件存在」冒充可交付）。
    if freshness.state == 'stale':
        raise UpstreamStale(
            f'{book_txt} 已不是当前组装账那一份（{freshness.reason}），拒绝下载',
            hint='stage7.assemble（0 成本）')
    if freshness.state != 'fresh':
        raise UpstreamUnproven(
            f'{book_txt} 证明不了自己出自当前组装账'
            f'（{freshness.state}/{freshness.reason}），拒绝下载',
            hint='stage7.assemble（0 成本）')
    text = store.read_text(book_txt)
    # ⚠ 同上：freshness 之外的第二把量尺（字数/章数），量的仍是「盘上这份还是不是
    #   当前组装账那一份」⇒ 同一个类，别在这里退回抽象基类。
    if len(text) != art.book_txt.chars or len(up.recs) != art.book_txt.chapters:
        raise UpstreamStale(
            f'{book_txt} 规模账与当前内容不一致：'
            f'chars={len(text)}/{art.book_txt.chars}, '
            f'chapters={len(up.recs)}/{art.book_txt.chapters}',
            hint='stage7.assemble（0 成本，按当前映射重拼并重记账）')
    hook_new, _counts, _claimed = apply_subs(up.hook_text, dict(art.subs), kept=list(art.kept))
    expected = assemble_txt(hook_new, up.recs, dict(art.subs), list(art.kept))
    if text != expected:
        raise UpstreamStale(
            '成书与当前已批准正文、文案和改名表的确定性重建不一致',
            hint='stage7.assemble（0 成本）')
    return text


def run_assemble(ctx: RunContext) -> tuple[str, NameMap, CheckReport]:
    """`stage7.assemble`：定稿之后组装成书。**0 成本，写盘。**

    ⚠⚠ **交付残缺态在这里硬拦，而不在 `measure.collect_chapters` 里。**
    那个函数会被复检经 HTTP 工作线程调用，而 `SystemExit` 是 `BaseException`
    ——一次「这本书还没写完」就会杀掉整个服务。**这条是付过一次代价的。**
    这里是 CLI 的调用点，抛的是 `TerminalState` 那一族（显式终态，不打栈）。

    ⚠ **抛的是那一族里的具体类，不是基类本身**：`novel/api/errors.py::status_of`
    对两个抽象基类刻意不给状态码（「加一个类就得决定一次」）⇒ 抛裸 `TerminalState`
    的那条路经 HTTP 会变成 500，把「按设计走不下去了」说成「服务器内部错误」。
    七道闸各自的类见 `_require_mapping_current` 与 `read_current_book`。

    ⚠ **每一条拒绝的「下一条命令」落在 `hint`，不写回 message**：拼在一起之后，
    「这条终态有没有出路」在消费方那边只能靠读中文去猜。CLI 由
    `novel/cli/main.py::_print_hint` 单独打一行，HTTP 由 `ErrorBody.hint` 单独一格。

    ⚠ 「文案未定稿」也属于交付残缺，而**正因为这道闸挡在前面**，
    「成书里没有开篇文案」这一态**结构上不可达** ⇒ `book_txt.has_hook` 已按待决④ 裁掉。
    """
    # Stage2 是全链总闸，必须在任何 Stage7 锁/读之前先失败；否则一个没有
    # novel_dir 的关闭式终态也会被锁包装器误报成进程错误。
    require_stage2_delivery_approved(ctx)
    return _run_assemble_owned(ctx)


@_commit_owned
def _run_assemble_owned(ctx: RunContext) -> tuple[str, NameMap, CheckReport]:
    """在 Stage2 已先行放行后，持 Stage7 提交锁完成实际组装。"""
    # 锁等待期间 Stage2/frozen 下游 proof 可能变化；进入提交临界区后再用
    # 同一权威闸完整复核一次。
    require_stage2_delivery_approved(ctx)
    store = ArtifactStore(ctx)
    # 组装是交付落盘的最后边界：除了 Stage2/Stage5 产物级闸，这里再守一次
    # Stage5 typed 闭合回执（与 CLI 运行边界同一把尺子），旧文件不充当通行证。
    from novel.stages.stage5.closure import require_stage5_closed
    require_stage5_closed(store)
    if not store.exists(REL):
        raise UpstreamMissing(f'{REL} 不在盘上', hint='先跑 stage7.rename')
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
    book_txt = book_txt_rel(store)
    with store.history_transaction(REL, book_txt):
        p = store.write_text(book_txt, text)
        store.write(REL, new)
        freshness = store.freshness(book_txt)
        if freshness.state != 'fresh':
            raise ProvenanceRefused(
                f'{book_txt} was assembled but cannot prove itself current: '
                f'{freshness.state}/{freshness.reason}; map and delivery were rolled back')
    return str(p), new, _report(ctx, store, _judge(ctx, store, new, up), new)
