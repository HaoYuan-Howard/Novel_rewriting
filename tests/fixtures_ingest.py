#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fixtures_ingest.py —— 摄入链（**stage0 剔除 + stage1 分章**）的合成样本。

【为什么要造】全语料 163 本跑下来，剔除的六类可疑项只有 2 类真的触发过，
`作者有话说` 这一类 `kind` 零样本，五个编码候选里四个零样本。
**「没有失败样本」不是「已验证」**——一条从没被执行过的分支，
和一条根本不存在的分支，在盘上长得一模一样。这个文件就是把它们分开。

【为什么两层放一个文件】它们的输入是同一样东西：一份原始 txt。
stage0 读它、删掉非正文、产出 `clean_text.txt`；stage1 读那份产物。
样本按 `owner` 标明自己守的是哪一层的分支，**但不该按层拆成两个文件**——
拆了之后「这份 txt 会走完整条链」这件事就没有地方表达了。

【为什么是生成而不是提交文件】样本要覆盖 UTF-16 / UTF-32 / GB18030 / 带 BOM 这些形态。
把它们当二进制文件提交进仓，编辑器、`git config core.autocrlf`、
以及任何一次「顺手格式化一下」都可能悄悄改掉它们，而**样本被改坏时测试会照常变绿**
（它验的是「解码结果符合预期」，不是「文件字节没变」）。

【期望怎么写】用 `contracts.invariants` 的常量和告警 id，**不写死措辞也不写死数字**。
判据阈值改了，样本跟着动，期望不用改；而如果某条期望写的是
「warnings[0] 以某某开头」，改一次措辞就会假红。
"""
from __future__ import annotations

import codecs
from dataclasses import dataclass, field

from contracts.invariants import (
    BODY_RATIO_MIN, DROP_RATIO_MAX, MIN_CHAPTERS, NONBODY_HEAD_MAX_CP,
    NONBODY_MARKER_WARN_CP, NOISY_DUPLICATE_MIN, STAGE0_WARNINGS, STAGE1_WARNINGS,
)

#: 每一层各自的告警取值域。样本声明的告警必须落在**它自己那一层**的域里——
#: 写错层不会有任何自然后果，所以这里显式盯着。
WARNINGS_BY_OWNER = {'stage0': STAGE0_WARNINGS, 'stage1': STAGE1_WARNINGS}

# ─────────────────────────────────────────────────────────────────────────────
# 构件
# ─────────────────────────────────────────────────────────────────────────────

HOOK = '穿成恶毒女配后，我决定摆烂。\n\n谁知男主追着我跑。\n\n'
_SENT = '她把药碗端到他面前，看着他一口一口喝完。'


def chapter(n: int, times: int = 6) -> str:
    """一个章节标记行 + 一段正文。标记行只能是数字——章节标记正则认的就是这个。"""
    return f'{n}\n\n正文第{n}章。{_SENT * times}\n\n'


def body(n_chapters: int = 5, times: int = 6) -> str:
    return HOOK + ''.join(chapter(i, times) for i in range(1, n_chapters + 1))


_FILLER = '那天的事她再也没有对任何人提起过下雪了天亮了她走了他没有再回头看一眼'


def marker_of_length(cp: int) -> str:
    """造一个以「番外」开头、`core` 恰好 `cp` 个码点的行。

    `core` = 标记行剥掉前导 `【[(（`/数字、剥掉尾部标点之后剩下的。这里不带任何括号与
    尾部标点，所以整行就是 `core`，长度所见即所得——**样本自己要能被一眼看懂**。

    ⚠ 长度不够时**抛异常，不静默截断**：整个 band 测试都建立在「这一行恰好 N 码点」上，
    悄悄短掉几个字会让样本落进相邻的那一档，而测试照样是绿的。
    """
    if cp < 2:
        raise ValueError('「番外」本身就是 2 个码点')
    if cp - 2 > len(_FILLER):
        raise ValueError(f'填充串只够造到 {len(_FILLER) + 2} 码点，要 {cp}——请加长 _FILLER')
    return '番外' + _FILLER[:cp - 2]


@dataclass(frozen=True)
class Case:
    """一份合成样本。

    `expect` 只描述**这条样本要钉住的那一件事**，不描述别的——
    一份什么都断言的样本，改任何一处判据都会红，于是很快就会被 `-k` 掉。
    """

    name: str
    owner: str                          # 'stage0' | 'stage1'：它守的是哪一层的分支
    why: str                            # 守的是哪条分支，为什么值得单独一份
    text: str                           # 归一化后的正文（用于生成 + 期望自校验）
    encoding: str = 'utf-8'             # 落成字节时用的编码
    bom: bytes = b''                    # 额外前置的 BOM（`utf-8-sig` 等由 codec 自带）
    corrupt_tail: bytes = b''           # 追加的坏字节，用于解码失败分支

    terminal: str | None = None         # 期望入显式终态（一份产物都不写），值是需求号
    fails: tuple[str, ...] = ()         # 期望产物照写、但校验器判 FAIL 的 HARD 判据
    num_chapters: int | None = None
    warnings: tuple[str, ...] = ()      # 必须落在 WARNINGS_BY_OWNER[owner] 里
    removed_kinds: tuple[str, ...] = ()  # stage0 剔除掉的 kind
    body_ratio_below: float | None = None
    body_ratio_band: tuple[float, float] | None = None
    tail: str | None = None             # R-1.14 的第三段
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_bytes(self) -> bytes:
        return self.bom + self.text.encode(self.encoding) + self.corrupt_tail


# ═════════════════════════ stage0 · 摄入与解码 ══════════════════════════════

_S0_DECODE = (
    Case(
        name='decode_bom_lies', owner='stage0',
        why='BOM 说是 UTF-16，内容却解不开 = 文件损坏。**此时不许回退 gb18030**——'
            '那条回退正是「解码成功但解错了」的来源，加 BOM 探测就是为了堵它。',
        text=body(3), encoding='utf-16-le', bom=codecs.BOM_UTF16_LE,
        corrupt_tail=b'\xff', terminal='R-0.11',
    ),
    Case(
        name='decode_nul', owner='stage0',
        why='无 BOM 的 UTF-16 会被 gb18030 **成功**解码成一串夹着 NUL 的乱码。'
            '「解码成功」于是不再意味着「解对了」——靠 NUL 兜住这一支。',
        text=body(3), encoding='utf-16-le', terminal='R-0.11',
    ),
)

_S0_ENCODINGS = (
    Case(name='enc_utf8_sig', owner='stage0', encoding='utf-8-sig', text=body(3), num_chapters=3,
         why='带 BOM 的 UTF-8。全语料 163 本零样本（都是无 BOM 的 utf-8）。'),
    Case(name='enc_utf16', owner='stage0', encoding='utf-16', text=body(3), num_chapters=3,
         why='UTF-16（codec 自带 BOM）。零样本。BOM 必须先于编码猜测判——'
             '否则会被 gb18030「成功」解成乱码。'),
    Case(name='enc_utf32', owner='stage0', encoding='utf-32', text=body(3), num_chapters=3,
         why='UTF-32。零样本。**它的 BOM 以 UTF-16 的 BOM 开头**，探测顺序里必须排在前面，'
             '否则会被当成 UTF-16。'),
    Case(name='enc_gb18030', owner='stage0', encoding='gb18030', text=body(3), num_chapters=3,
         why='GB18030，无 BOM，靠回退猜测命中。零样本。'
             '它几乎能解码任意字节序列，所以在候选顺序里必须排最后。'),
)

# ═════════════════════════ stage0 · 剔除与可疑项 ════════════════════════════

_S0_REMOVAL = (
    Case(
        name='early_cut', owner='stage0',
        why='作者把某一章的标题写成「番外篇」，而那其实是正文。判据从那一行切断，'
            '后面一大截被当番外丢掉。**剔除是破坏性的，这是本层最要紧的一道防线。**',
        text=body(2, times=2) + '番外篇\n\n' + '男主视角：她不知道我早就认出了她。' * 40 + '\n',
        num_chapters=2, fails=('R-0.9',),
        warnings=('early_cut', 'drop_ratio'), removed_kinds=('番外',),
        body_ratio_below=BODY_RATIO_MIN,
        notes=('两条告警会一起响：body + removed ≈ total，所以 early_cut 必然伴随 drop_ratio。'
               'drop_ratio 独立起作用的窗口只有 body_ratio ∈ [0.60, 0.65)，见下一份样本。',),
    ),
    Case(
        name='drop_ratio_only', owner='stage0',
        why=f'丢弃占比越过 {DROP_RATIO_MAX}，但正文占比仍 ≥ {BODY_RATIO_MIN}。'
            f'**这是 drop_ratio 唯一独立起作用的窗口**——只有它响，early_cut 不响。',
        text=body(5, times=6) + '番外\n\n' + '番外一：十年后的那个冬天，孩子已经会走路了。' * 18 + '\n',
        num_chapters=5, warnings=('drop_ratio',), removed_kinds=('番外',),
        body_ratio_band=(BODY_RATIO_MIN, 0.65),
    ),
    Case(
        name='long_marker_silent_band', owner='stage0',
        why=f'core = {NONBODY_MARKER_WARN_CP + 1} 码点：曾经的**静默假阳区**。'
            f'改阈值之前它切掉正文却一声不响；改后 18~20 照切但必须告警。',
        text=body(5, times=6) + marker_of_length(NONBODY_MARKER_WARN_CP + 1) + '\n\n'
             + '后面这段其实是第 5 章的正文，被这一行切没了。' * 6 + '\n',
        num_chapters=5, warnings=('long_marker',), removed_kinds=('番外',),
    ),
    Case(
        name='long_marker_not_cut', owner='stage0',
        why=f'core = {NONBODY_HEAD_MAX_CP + 1} 码点：超过上限，**不再命中**，'
            f'整句留在正文里。宁可漏切也不误切——漏切在章表上看得见。',
        text=body(5, times=6) + marker_of_length(NONBODY_HEAD_MAX_CP + 1) + '\n\n'
             + '后面这段是第 5 章的正文，应当原样保留。' * 6 + '\n',
        num_chapters=5, warnings=(), removed_kinds=(), tail='',
    ),
    Case(
        name='kind_author_note', owner='stage0',
        why='`作者有话说` 是 NONBODY_KINDS 四类里**唯一全语料零样本**的一类。'
            '枚举有 4 个值，实际只见过 3 个——这一支从没被执行过。',
        text=body(4) + '作者有话说\n\n' + '感谢大家追更，这本就到这里啦。' * 4 + '\n',
        num_chapters=4, removed_kinds=('作者有话说',),
    ),
    Case(
        name='end_marker_then_fanwai', owner='stage0',
        why='完结标记在前、番外在后 → 剔除的 span 从番外那一行才开始，**完结标记留在正文侧**。'
            '全语料 3 本是这个形态。它与下一份的差别只是顺序，而结果完全不同。',
        text=body(4) + '（全文完）\n\n' + '番外\n\n' + '番外体的内容。' * 10 + '\n',
        num_chapters=4, removed_kinds=('番外',),
    ),
    Case(
        name='end_marker_inside_fanwai', owner='stage0',
        why='番外在前、完结标记落在番外体里 → **完结标记被一起删掉**。全语料 4 本。'
            '⚠ 这一份要盯住的是：删完之后 stage1 那边就没有完结标记可判了。',
        text=body(4) + '番外\n\n' + '番外体的内容。' * 10 + '\n（全文完）\n',
        num_chapters=4, removed_kinds=('番外',),
    ),
)

# ═════════════════════════ stage1 · 分章 ════════════════════════════════════
# ⚠ 这一组的输入形态代表 `stage0/clean_text.txt`：**已经没有任何非正文体了**。

_S1 = (
    Case(
        name='no_hook', owner='stage1',
        why='第一个章节标记之前没有任何内容 → 没有开篇文案段。'
            '全语料 163 本零样本，但下游 stage3/6/7 都要它，所以是终态不是告警。',
        text=''.join(chapter(i) for i in range(1, 4)), terminal='R-1.12',
    ),
    Case(
        name='single_chapter', owner='stage1',
        why=f'只有 1 个正文章节 → 少于 MIN_CHAPTERS={MIN_CHAPTERS}。'
            f'全语料有 10 本是这样，原型只出一条 warning（占了全部告警的 10/11）。',
        text=HOOK + chapter(1, times=40), terminal='R-1.13',
    ),
    Case(
        name='noisy_duplicate', owner='stage1',
        why=f'同一个章号出现 ≥{NOISY_DUPLICATE_MIN} 次 = 某个固定标记被误判成了章号，'
            f'不是普通重章。全语料命中 1 本（`小三争夺战`，章号 1 出现 17 次）。'
            f'**这是 stage1 仅剩的一类可疑项。**',
        text=HOOK + chapter(1) + chapter(1) + chapter(1) + chapter(2),
        num_chapters=4, warnings=('noisy_duplicate',),
    ),
    Case(
        name='end_marker_tail', owner='stage1',
        why='切点来自完结标记 → 那一行不在正文里。全语料 42/163 本是这个形态，'
            '原契约的两段式记账在它们上面全部不成立（见 R-1.14 的来历）。',
        text=body(4) + '（全文完）\n', num_chapters=4, tail='（全文完）\n',
    ),
    Case(
        name='r114_violation', owner='stage1',
        why='完结标记之后还有一段散文 → 第三段既不为空也不是完结标记行。'
            '**这是 R-1.14 唯一的反例样本，没有它这条新判据就是一条从没响过的分支**——'
            '它在全语料 163 本上 163/163 通过，而「163/163 通过」与「根本没在查」'
            '在报告上长得一模一样。它接管的那条 warning（完结标记之后还有内容）'
            '在语料上也是 0 命中，所以这个形态**只可能靠合成样本触发**。',
        text=body(4) + '（全文完）\n\n' + '这段既不是正文也不是番外，它就不该在这儿。\n',
        num_chapters=4, fails=('R-1.14',),
        notes=('对照屏上它会渲染成一个 `hole` 段——`hole` 非空就是这条判据的可视形态。',),
    ),
)

CASES: tuple[Case, ...] = _S0_DECODE + _S0_ENCODINGS + _S0_REMOVAL + _S1

#: 每条样本守的分支。**这张表就是「哪些分支有样本了」的机器可读形式**——
#: 别拿 `len(CASES)` 当覆盖率，那只是文件数。
COVERS: dict[str, tuple[str, ...]] = {
    'stage0/解码': tuple(c.name for c in _S0_DECODE),
    'stage0/编码候选': tuple(c.name for c in _S0_ENCODINGS),
    'stage0/剔除与可疑项': tuple(c.name for c in _S0_REMOVAL),
    'stage1/分章': tuple(c.name for c in _S1),
}


def write_all(dest) -> dict[str, object]:
    """把全部样本落到 `dest` 目录，返回 {name: path}。测试用临时目录，不进仓。"""
    from pathlib import Path
    d = Path(dest)
    d.mkdir(parents=True, exist_ok=True)
    out = {}
    for c in CASES:
        p = d / f'{c.name}.txt'
        p.write_bytes(c.to_bytes())
        out[c.name] = p
    return out


for _c in CASES:
    assert _c.owner in WARNINGS_BY_OWNER, f'{_c.name}: owner={_c.owner!r} 不是 stage0/stage1'
    _unknown = set(_c.warnings) - set(WARNINGS_BY_OWNER[_c.owner])
    assert not _unknown, (
        f'{_c.name}: 告警 {_unknown} 不在 {_c.owner} 的取值域里。'
        f'判据换层时样本要跟着换 owner——写错层不会有任何自然后果'
    )
