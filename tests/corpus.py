#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
corpus.py —— 盘上语料 → 新契约的**迁移清单**，以及按清单现算的纯函数。

【它是什么】`E:\\practice_group\\Novel_refactoring\\Output` 下 3 本书、约 90 份真实产物，
是本项目**唯一**能 0 成本喂给新 schema 的东西。本文件把「盘上那一份与新契约差在哪」
写成三张会红的表，`tests/test_corpus_fixtures.py` 逐条断言。

【为什么必须是三张表，不是一张】三种差异的**性质不同**，混在一起就再也分不开：

| 表 | 差异 | 处置 |
|---|---|---|
| `CUT` | **新契约裁掉的字段**（0 读者 / 可 0 成本现算 / 一个常量伪装成字段） | 加载前 `pop` |
| `ADDED` | **新契约新增的必填键**，盘上零样本 | 加载前填一个**声明过的占位值** |
| `RESHAPED` | **形状改版**（`inputs` 的边 · `Finding` 13 字段 · `book_txt.sha1` 两把尺子） | 机械转换 |

⚠⚠ **别把迁移后的语料写成文件。** 那是第二个落点，且会与本表分家——
一律用下面这个纯函数现算（`load_cut`）。

⚠ 本机 `%TEMP%\\pytest-of-yh255` 的 ACL 坏了，pytest 的 `tmp_path` 一用就炸；
本文件不落任何盘，天然绕开。
"""
from __future__ import annotations

import json
import os
import re
import types
import typing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from pydantic import BaseModel

from contracts.schema import ARTIFACTS
from novel import prov          # `RESHAPED` 里 `inputs` 那一行的换算，见 `_migrate_inputs`

#: 语料目录。默认取本仓的**兄弟目录**，可用环境变量覆盖。
#: ⚠ 找不到时**显式说出来**（测试 skip 并写明「没在查」），
#: 绝不静默产出一份「所有断言都通过」的结果——那种结果与真跑过的长得一模一样。
CORPUS_ENV = 'NOVEL_OUTPUT'
_DEFAULT_CORPUS = Path(__file__).resolve().parents[2] / 'Novel_refactoring' / 'Output'


def corpus_root() -> Path | None:
    p = Path(os.environ.get(CORPUS_ENV) or _DEFAULT_CORPUS)
    return p if p.is_dir() else None


# ─────────────────────────────────────────────────────────────────────────────
# 一、盘上路径 → `ARTIFACTS` 的键
# ─────────────────────────────────────────────────────────────────────────────
#
# ⚠ 归档（`_rounds/`）**必须一起过**：正式产物是「人修完之后」的状态，它会把
# 「修之前才出现的取值」整个抹掉。实测（stage7）：`residual[].confidence` 在 3 份
# 正式产物上是**单值 `low`**（19/19），而 8 份归档里有 **35 条 `high`**。
# **只反推正式产物，会定出一个把 `high` 当成零样本分支的 schema。**

#: `(盘上路径的正则, ARTIFACTS 的键)`。顺序即匹配顺序。
PATTERNS: tuple[tuple[str, str], ...] = (
    (r'^stage0/removal_report\.json$', 'stage0/removal_report.json'),
    (r'^stage1/main_text\.json$', 'stage1/main_text.json'),
    (r'^stage1/hook_copy\.json$', 'stage1/hook_copy.json'),
    (r'^stage1/partition_report\.json$', 'stage1/partition_report.json'),
    (r'^stage2/global_grounding\.json$', 'stage2/global_grounding.json'),
    (r'^stage2/chapter_blueprints\.json$', 'stage2/chapter_blueprints.json'),
    (r'^stage3/style_grounding\.json$', 'stage3/style_grounding.json'),
    (r'^stage4/plan_chapters\.json$', 'stage4/plan_chapters.json'),
    (r'^stage4/writer_context\.json$', 'stage4/writer_context.json'),
    # 成稿：正式稿与 `_rounds/chNN.rN.json` **共用同一份章契约**（实测键集一致）。
    (r'^stage5/(deep|shallow)/(|_rounds/)ch\d+(\.r\d+)?\.json$', 'stage5/{mode}/ch{nn}.json'),
    # ⚠ `stage5/_rounds/review_4.…json` 是**人手工放进去的**（全仓 grep 无任何代码会写它），
    #   而它确实是一份 review。收进来正是因为「这个目录会被人创建」这件事已经发生过。
    (r'^stage5/(|_rounds/)review_\d+(\.[^/]+)?\.json$', 'stage5/review_{n}.json'),
    (r'^stage5/wontfix\.json$', 'stage5/wontfix.json'),
    (r'^stage6/hook\.json$', 'stage6/hook.json'),
    (r'^stage6/_rounds/hook\.r\d+\.json$', 'stage6/_rounds/hook.rN.json'),
    (r'^stage7/name_map\.json$', 'stage7/name_map.json'),
    (r'^stage7/_rounds/name_map\.r\d+\.json$', 'stage7/name_map.json'),
    (r'^stage7/name_map_edit\.json$', 'stage7/name_map_edit.json'),
)

#: 归档的 `hook.rN.json` 与正式产物**逐字段同形**，唯一差别是 `approved` 恒 `null`
#: （盘上 3/3）——**这说明归档点选对了**：换掉的是「谁被选中」这个决定，
#: 而那正是下次调 prompt 时唯一的证据。
#: ⇒ 它的 `ADDED` 少一族（`approved.findings.*`），所以单列一个模式键。
_ROUNDS_HOOK = 'stage6/_rounds/hook.rN.json'

#: **登记在册的排除项**，每一条都要写得出理由。
#: ⚠ `iter_artifacts` 会断言这些路径**真的在盘上**——一条过期的排除项
#: 与一条真的排除项，在测试结果上长得一模一样。
EXCLUDED: dict[str, str] = {
    r'^stage5/(deep|shallow)/_rounds/ch\d+\.INVALID\.superseded\.json$':
        '盘上仅有的两份 `.INVALID` 是**修好之前的贫血形**（只有 '
        '`{errs, mech, output_ordinal, text}` 四个键、完全没有 `_provenance`），'
        '`stage5.md` §3.3 明写「别拿它们当取值面」。10 个必填键里要凭空造 8 个，'
        '那不是体检、是把体检对象换成我们自己写的东西。'
        '⚠ **`.INVALID` 的现行形状（10 键 + errs + _provenance）盘上零样本**——'
        '这一格是空的，别读成「查过没事」。',
}


@dataclass(frozen=True)
class Artifact:
    """盘上的一份产物。"""

    #: 目录名后缀那串数字（`source_id`）。⚠ 用它而不是整个目录名当身份：
    #: 书名会被改，而这串 id 是**从原始文件名带过来的**。
    source_id: str
    book_dir: str
    rel: str                # 相对书目录的路径
    pattern: str            # `ARTIFACTS` 的键
    path: Path

    @property
    def model(self) -> type[BaseModel]:
        return ARTIFACTS[_ROUNDS_HOOK_BASE.get(self.pattern, self.pattern)]


#: 归档模式没有自己的模型，用正式产物那一个。
_ROUNDS_HOOK_BASE = {_ROUNDS_HOOK: 'stage6/hook.json'}


def _classify(rel: str) -> str | None:
    for pat, key in PATTERNS:
        if re.match(pat, rel):
            return key
    for pat in EXCLUDED:
        if re.match(pat, rel):
            return None
    raise AssertionError(
        f'盘上出现了一份没有登记的产物：{rel!r}。\n'
        f'→ 要么把它加进 `PATTERNS`，要么连同理由加进 `EXCLUDED`。'
        f'**一份没人认领的产物就是一份没在查的产物。**'
    )


def iter_artifacts() -> Iterator[Artifact]:
    """遍历盘上全部登记在册的产物。

    ⚠ 跳过 `_` 开头的**书目录**（`Output/_web` 是界面的缓存，不是一本书）——
    与「枚举写作臂时跳过 `_` 开头的子目录」同一个约定。
    """
    root = corpus_root()
    if root is None:
        return
    excluded_seen: set[str] = set()
    for book in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith('_')):
        source_id = book.name.rsplit('_', 1)[-1]
        for path in sorted(book.rglob('*.json')):
            rel = path.relative_to(book).as_posix()
            key = _classify(rel)
            if key is None:
                excluded_seen |= {p for p in EXCLUDED if re.match(p, rel)}
                continue
            yield Artifact(source_id, book.name, rel, key, path)
    stale = set(EXCLUDED) - excluded_seen
    assert not stale, (
        f'这些排除项在盘上一份都没匹配到：{sorted(stale)}\n'
        f'→ 一条过期的排除项与一条真的排除项，在测试结果上长得一模一样。'
    )


# ─────────────────────────────────────────────────────────────────────────────
# 二、`CUT` —— 新契约裁掉的字段
# ─────────────────────────────────────────────────────────────────────────────
#
# 路径写法：`.` 分段，`*` 表示「数组下标」或「映射的键」（**数据不是 schema**）。
# ⚠ 断言是**相等**不是包含：多裁一个红、漏裁一个也红。

#: 每一层都撞上过的那两个。书名 = 目录名、`source_id` = 目录名后缀，
#: 都能 0 成本现算，落盘只是抄第二份——而两份同源数据哪天不一致时，没人知道该信谁。
#: **实测撞了六次**（stage1 ×1 · stage2 ×2 · stage3 ×1 · stage4 ×2 · stage5 ×1 ·
#: stage6 ×1 · stage7 ×1）。
_TITLE_AND_ID = ('title', 'source_id')

CUT: dict[str, frozenset[str]] = {
    # stage1 —— 24 个字段瘦到 9 个。删掉的全部可由 `main_text` / `hook_copy` /
    # 目录名 / 原文现算；留下来的判准是**不可再生**。
    # ⚠ 摄入元数据（`original_filename` / `original_path` / `source_encoding` /
    #   `text_norm`）不是「删掉」，是**搬去了 stage0**——谁读原始 txt，谁记它的元数据。
    'stage1/partition_report.json': frozenset({
        *_TITLE_AND_ID,
        'platform',                       # 无消费方，且没有第二个平台值
        'original_filename', 'original_path', 'source_encoding', 'text_norm',  # → stage0
        'dropped', 'dropped_code_points',  # 本层不再剔除任何内容 → stage0 的 `removed[]`
        'body_ratio',                     # 恒等于两个已落盘的数之商
        'num_sections', 'chapter_nos', 'chapter_no_gaps', 'chapter_no_duplicates',
        'chapter_nos_strictly_increasing',  # 全部由 `main_text` 现算
        'hook_copy_present', 'hook_copy_code_points',  # 恒 True / 由 `hook_copy` 现算
        'notes',                          # 三条写死的散文，0 消费方
    }),

    # stage2
    'stage2/global_grounding.json': frozenset({
        *_TITLE_AND_ID,
        # 一个**进程内**的开关（「什么都没改时不落盘」）：确定性迁移那条路径 pop 掉它、
        # 正常产出那条路径忘了 pop，于是它落了盘——盘上 3/3 恒 True，**没有任何人读它**。
        '_provenance.changed',
    }),
    'stage2/chapter_blueprints.json': frozenset({
        'source_id',
        # ⚠ 它**有一个读者**——把内部数字 id 原样插进规划节点的 **prompt 载荷**，
        #   而那正是删它的理由。**移植时要连投影一起改**：只删字段不删投影，
        #   那处会安静地发一个 `None` 过去。
        # 只表达章内段界：0 消费 + 零样本（「一章内换叙述者」全语料 163 本 + 盘上 31 章
        # 一个正样本都没有）；真实发生的是**章之间**换人称。
        'blueprints.*.narration.*.last_beat_id',
    }),

    # stage3 —— 它们在本层唯一的作用是**被排除在扫描面之外**。
    # **一个字段存在的理由是「记得把它从检查里排除掉」，那本身就说明它不该存在。**
    'stage3/style_grounding.json': frozenset(_TITLE_AND_ID),

    # stage4
    'stage4/plan_chapters.json': frozenset({
        *_TITLE_AND_ID,
        # **一个常量伪装成的字段**（恒 `keep`），暗示着一种从不存在的「按章变化」。
        'output_chapters.*.op',
        # 0 读者 + 盘上 197/197 恒等于一个纯函数 ⇒ 可现算。⚠ **响应里仍然必需。**
        'output_chapters.*.beats.*.plan_beat',
        # 它记的是**初始化**不是改写（盘上 17/17 全是「本来是空的」）。
        '_provenance.op_forced',
    }),
    'stage4/writer_context.json': frozenset({
        *_TITLE_AND_ID,
        # **0 读者**：写作节点与文案节点都明确拒收它（它必然携带弧线终点）。
        # ⚠ 它躺在「写作节点的唯一输入」里而下游明确拒收 = 闸门开在**渲染侧**不在**产物侧**。
        'reconstruction_strategy',
        'book',                           # 只有一个子键 `beat_density`，0 成本现算
        'chapters.*.op',
    }),

    # stage5 —— **信息一条都没丢，只是换了落点**（见 `contracts/schema/stage5.py`）。
    'stage5/{mode}/ch{nn}.json': frozenset({'op', 'mech', 'dialogue_count'}),
    'stage5/review_{n}.json': frozenset({
        *_TITLE_AND_ID,
        'tag',                            # 全仓 0 读者，盘上 12/12 恒 ''
        'review_seq',                     # 在文件名里
        # ⚠ **stage5 这一侧也要裁**：`JudgedDraft` 全链只有一个类
        #   （stage5 / stage6 / stage7 共用），`mode` 一裁就是三层一起裁。
        #   盘上 124/124 都有它，而它就是键的中间那一段。
        'judged.*.mode',
    }),

    # stage6
    'stage6/hook.json': frozenset({
        *_TITLE_AND_ID,
        'baselines',                      # 0 读者 + 100% 现算 + **其中一格结构性说谎**
        'counts',                         # 0 读者 + 100% 现算 + **稀疏**（0 键会消失）
        'candidates.*.chars',             # ⚠ 现算必须留在 API 层（去空白 vs len 差 9.5%）
        'candidates.*.mech.quote_problems',   # 它不是记账，**是一条结论** → 升成判据
        'approved.mech.quote_problems',
        'judged.*.mode',                  # 与 stage5 / stage7 一起裁（`JudgedDraft` 只有一个类）
    }),

    # stage7 —— 三条待决 2026-08-11 用户裁决：**全裁**；待决④ 2026-08-12（F2）再裁一条。
    'stage7/name_map.json': frozenset({
        *_TITLE_AND_ID,
        '_problems',                      # 0 读者，且结论已逐条翻进 `book[]` ⇒ 两个落点
        # 待决④：`false` 分支**结构上不可达**——`R-10.14` 那道「文案未定稿一律拒写 txt」
        # 的闸挡在前面 ⇒ 只剩「拒写」与「有文案」两种结局。照 `op` 先例裁掉恒等于常量的字段。
        # ⚠ 它同时是一次**产品语义**裁决：「不允许出一本没有开篇文案的书」。
        'book_txt.has_hook',
        'title_new',                      # 待决①：`apply_subs(title, …)` 现算，盘上 3/3 与 title 逐字节相同
        'applied.title',                  # 待决①：盘上 3/3 全空，且三块里唯独它缺两个字段
        'applied.chapters.*.chars_before',  # 待决②：唯一读者是一条 metric，替换不改篇幅
        'applied.chapters.*.chars_after',
        # ⚠ **待决② 只点名了 `chapters{*}`，而 `hook` 那一块带着同样的两个字段**
        #   （`stage7.md` §1.3 的表把它写成「同上」）。留一个裁一个会让 `AppliedFace`
        #   长出两种形状，而这两个字段的读者是**同一条 metric**。⇒ 一起裁。
        'applied.hook.chars_before',
        'applied.hook.chars_after',
        '_groups',                        # 待决③：加载回来 0 读者
        'counts',                         # 与 stage6 一起裁（同形、同稀疏问题）
        'judged.*.mode',                  # 与 stage6 统一：键的**中间那一段**就是它
    }),

    # 没有任何裁撤的那几份——**空集是结论，不是遗漏**。
    'stage1/main_text.json': frozenset(),
    'stage1/hook_copy.json': frozenset(),
    'stage7/name_map_edit.json': frozenset(),
}
#: 归档少一族：`approved` 在 `_rounds/` 里恒 `null`（盘上 3/3），
#: 于是 `approved.mech.quote_problems` 那一条在它身上**按定义不适用**。
CUT[_ROUNDS_HOOK] = frozenset(
    c for c in CUT['stage6/hook.json'] if not c.startswith('approved.'))


# ─────────────────────────────────────────────────────────────────────────────
# 三、`ADDED` —— 新契约新增的必填键，盘上零样本
# ─────────────────────────────────────────────────────────────────────────────
#
# ⚠ 值是**声明过的占位符**，只为让加载走通。它们**不是实测值**——
# 拿它们去推断任何取值面都是错的。这张表的真正用途是：
# **把「我们给八层加了哪些键」变成一条会红的断言**（同 `CUT` 那一张）。

#: 值不是常量、由 `_migrate_findings` 现算。**放在表里是为了让那一族出现在断言的两边**
#: ——`ADDED` 的键是「盘上缺哪些必填键」的**唯一**声明。
COMPUTED = object()

#: `Finding` 从 5 字段变 13 字段那一族要填的六个。
#: ⚠ **`evidence_key` 是算出来的不是填的**——`'|'.join(evidence)`，
#: 于是这一族迁移顺带**真的跑了一遍** `Finding` 的后置条件。
_FINDING_FILL = ('evidence_key', 'owner', 'actionable',
                 'identity_hit', 'converge', 'unresolved_rounds')

#: 那 5 条 hard/info 结论的 `owner`，**逐条抄自 stage6/stage7 的判据表**。
#: 其余 rule 全是 `metric`/`unver` ⇒ `owner` 恒 `None`（`owner is None` ⇔ 它不是结论）。
#: ⚠ **它是「`OWNER` 少了 `stage6`/`stage7` 两个值」这条发现的直接证据。**
FINDING_OWNER: dict[str, str] = {
    'hook_ending_shape': 'stage6',
    'hook_shape': 'stage6',
    'rn_ambiguous': 'stage7',
    'rn_kept_contains_mapped': 'stage7',
    'rn_residual_stem': 'stage2',       # ⚠ 不是 stage7：根因在人物表收得够不够
}

_HOOK_ADDED = {
    # 事实④：**这是一个付费节点**，而换了模型、换了 `--max-tokens`，
    # 产物里没有任何一处说得出来。⚠ stage7 犯的是**同一个**错。
    '_provenance.model': 'deepseek-v4-pro',
    '_provenance.params': {'max_tokens': 8000, 'tag': ''},
}
_FINDING_PATHS_HOOK = ('candidates.*.findings.*', 'approved.findings.*', 'book.*')

ADDED: dict[str, dict[str, Any]] = {
    'stage1/partition_report.json': {
        # stage0 那一层加进来之后才有的字段：本层输入是 `stage0/clean_text.txt`，
        # 而存量产物记的是原型那套摄入元数据。
        'input_sha1': '0' * 40,
        # 对照屏（S-03）用的两个区间。**盘上零样本。**
        'hook_span': [0, 1],
        'chapter_spans': None,          # ← 由 `_fill` 按 `num_chapters` 现造，见下
    },
    'stage2/global_grounding.json': {
        # ⚠ **`stage2.md` §3.1 把它的证据列标成「三源」，而盘上 3/3 本一份都没有。**
        #   契约要它必填是对的（「键不在 = 不知道，不发绿灯」正是它自己写的），
        #   但那一格该是 `契·码`。已登记，见 A3 的交接。
        '_provenance.setup_chapters_pruned': [],
    },
    'stage2/chapter_blueprints.json': {
        # ⚠⚠ **这三个键原型根本没有**（F5-1 立，`stage2.md` §3.2）：盘上 3/3 本零样本。
        #   证据列标 `契·码`——「码」指的是本仓 F5-5 的产地，与上面那条
        #   `setup_chapters_pruned` 是同一种处置。
        # ⚠ 填**空列表**而不是 `None`：这三个键在契约上是**必填**的，
        #   而「空列表」= 这一趟没改过任何字节、没救过、没失败过。
        #   ⚠ 对 `verbatim_failures` 尤其要紧：它的语义是「**非空即失败态**」
        #   ⇒ 空列表是「查过、没失败」，`None` 才是「不知道」。存量产物这一格
        #   严格来说属于后者，但**载体不许它缺**（键不在会让整份加载不了），
        #   而迁移填 `None` 会让「不知道」在下游同形于「没失败」——两害取其轻：
        #   填空列表并在这里说清它是**迁移填的**，不是产地写的。
        '_provenance.verbatim_rewrites': [],
        '_provenance.verbatim_recovered': [],
        '_provenance.verbatim_failures': [],
    },
    'stage6/hook.json': {**_HOOK_ADDED,
                         **{f'{p}.{f}': COMPUTED
                            for p in _FINDING_PATHS_HOOK for f in _FINDING_FILL}},
    _ROUNDS_HOOK: {**_HOOK_ADDED,
                   # ⚠ 归档里 `approved` 恒 `null` ⇒ 它那一族**在这儿不存在**。
                   **{f'{p}.{f}': COMPUTED
                      for p in ('candidates.*.findings.*', 'book.*') for f in _FINDING_FILL}},
    'stage7/name_map.json': {
        '_provenance.model': 'deepseek-v4-pro',
        '_provenance.params': {'max_tokens': 16000, 'tag': ''},
        # ⚠⚠ **这一格填的 `None` 不是占位符，是实况。**
        #    `identity`（恒等基线的结论，`R-10.7`）是 F2 新增的键，而这三份产物产出时
        #    它还不存在 ⇒ 那三次基线**确实没有留下任何记录**。原型的 `--identity`
        #    整个输出是 `print` + 返回码，「跑过且逐字节无损」与「从没跑过」在盘上同形（`Q-2`）。
        #    ⇒ 迁移填 `null` 的含义就是**「从没跑过」**，报告要照这个含义报出来。
        #    对照 `stage6` 的 `baselines_identity`：那一格盘上**有值**（12 格），
        #    因为 stage6 在同一道工序上一开始就做对了。
        'identity': None,
        **{f'book.*.{f}': COMPUTED for f in _FINDING_FILL},
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# 四、`RESHAPED` —— 形状改版
# ─────────────────────────────────────────────────────────────────────────────

RESHAPED: dict[str, str] = {
    '_provenance.inputs': (
        '盘上 **297 条边全部是扁平的 10 位字符串**；新契约一条边是 `{file, consumed}`。'
        '`Q-4` 明写「指纹是**文件级**的：改一个不相干的字段会让整份产物的指纹失配，'
        '把一批结论降级成『读数不可用』，且没有重新挂钩的路。'
        '**字段级指纹是一次契约改版，值得在新骨架里就做对。**」'
        '⇒ 迁移填 `consumed=null`，含义是「本节点没有声明自己读了哪些字段」'
        '⇒ 该边新鲜度是 `unknown`，**不是 `fresh`**。**建槽位，不填槽位。**'
    ),
    'findings[] 5 → 13 字段': (
        '`_common.md` §七：全链一份 `Finding`（原型有 **5 份拷贝、3 种字段签名**）。'
        'stage5 盘上已经是 13 字段形；**stage6 / stage7 盘上只有 5 个**。'
        '⇒ 这两层的 findings 要补齐六格，见 `_FINDING_FILL`。'
    ),
    'stage7.book_txt.sha1  10 位 → 40 位': (
        '`_common.md` §二把 `stage7.book_txt` 的指纹列在**内容身份（40 位）**那一栏：'
        '它回答「有人换过交付物吗」，人可能拿 `sha1sum` 手工复核，**截断买不到任何东西**。'
        '而盘上记的是 10 位来源指纹。'
        '⚠ **旧值推不出新值，只能拿盘上的 `book.txt` 重算**——迁移这里填占位符，'
        '**不要把占位符读成实测值**。'
    ),
}


# ─────────────────────────────────────────────────────────────────────────────
# 五、没有 `_provenance` 的产物 —— **登记在册的那一份**
# ─────────────────────────────────────────────────────────────────────────────

#: `(source_id, rel)`。**只收「模型里有 `_provenance` 字段、而盘上那份没有」的**——
#: 契约本来就没有来源账的那些（`main_text` / `hook_copy` / `partition_report` /
#: `name_map_edit`）由构造排除，不进这张表。
def cuts_of(a: Artifact) -> frozenset[str]:
    """这一份产物**真正适用**的裁撤项。

    ⚠ 没有 `_provenance` 的那一份（见 `MISSING_PROVENANCE`），来源账里的裁撤项
    对它**按定义不适用**——它连来源账都没有，谈不上「多了一个 `op_forced`」。
    把这条写成代码而不是把断言放宽成「包含」：**放宽之后就再也抓不到「漏裁一个」了**。

    ⚠ **第二族同形的**（F2 加的）：stage7 的 8 份归档 `book_txt` 恒 `null`
    （归档写在**组装之前**，`--assemble` 不归档），于是 `book_txt.*` 那一族裁撤项
    在它们身上同样按定义不适用——**块整个不在，谈不上「块里多了一个字段」**。
    实测 8/8 归档 `book_txt is None`、3/3 正式产物是 dict，**没有第三种**。
    ⇒ 判据看的是**盘上这一份到底有没有那个块**，不是路径前缀写死一张表：
    哪天某份归档真的带了 `book_txt`，这里自动恢复检查，而写死表会静默漏掉它。
    """
    cuts = CUT[a.pattern]
    if (a.source_id, a.rel) in MISSING_PROVENANCE:
        cuts = frozenset(c for c in cuts if not c.startswith('_provenance.'))
    # ⚠ 先看这张表里有没有 `book_txt.*`，再去读盘——否则每一份产物都要多读一次文件，
    #   而顶层不是对象的那几份（`stage1/main_text.json` 是数组）根本没有块可言。
    if any(c.startswith('book_txt.') for c in cuts):
        raw = load_raw(a)
        if isinstance(raw, dict) and raw.get('book_txt') is None:
            cuts = frozenset(c for c in cuts if not c.startswith('book_txt.'))
    return cuts


MISSING_PROVENANCE: frozenset[tuple[str, str]] = frozenset({
    # ⚠⚠ `stage4.md` 事实⑤ 的**字节级铁证**：`.INVALID` 提升路把 `_provenance` 整个丢掉。
    #   三处根因串成一条：落盘闸门的失败支用**裸 `json.dump`** 写 `.INVALID`
    #   ⇒ 那份稿从来就没有来源账；提升时又**没传** `prov=`；
    #   而代码注释写着「来源信息沿用原稿里那一份」——前一条保证了原稿里没有那一份。
    #   **它必须留在这张表里，不是被当成 bug 修掉。**
    ('7581413687886353176', 'stage4/plan_chapters.json'),
})


# ─────────────────────────────────────────────────────────────────────────────
# 六、纯函数：按清单现算
# ─────────────────────────────────────────────────────────────────────────────

def load_raw(a: Artifact) -> Any:
    with a.path.open(encoding='utf-8') as fh:
        return json.load(fh)


def _walk(node: Any, parts: tuple[str, ...]) -> Iterator[tuple[Any, str]]:
    """沿路径走到**倒数第二段**，产出 `(容器, 末段)`。`*` 展开数组下标或映射的键。"""
    if not parts:
        return
    head, rest = parts[0], parts[1:]
    if head == '*':
        if isinstance(node, dict):
            children: Any = list(node.values())
        elif isinstance(node, list):
            children = list(node)
        else:
            return
        for child in children:
            if rest:
                yield from _walk(child, rest)
        return
    if not isinstance(node, dict):
        return
    if not rest:
        yield node, head
        return
    if head in node and node[head] is not None:
        yield from _walk(node[head], rest)


def _pop_cuts(data: Any, cuts: frozenset[str]) -> None:
    for path in cuts:
        for holder, last in _walk(data, tuple(path.split('.'))):
            holder.pop(last, None)


def _migrate_inputs(data: Any) -> None:
    """`{key: fp10}` → `{key: {file: fp10, consumed: null}}`。见 `RESHAPED`。

    ⚠ **换算规则不在这里，在 `novel/prov.py::normalize_inputs`。** 运行时判新鲜度时
    要对同一批存量字节做同一次换算（盘上 297 条边全是扁平字符串），
    而这里再写一份就是「同一件事的第二个落点」——本项目每一次都付过学费。
    ⇒ 本函数只负责**定位**（走到 `_provenance.inputs` 那一格），换算调它。
    """
    if not isinstance(data, dict):
        return
    prov_block = data.get('_provenance')
    if not isinstance(prov_block, dict):
        return
    edges = prov_block.get('inputs')
    if isinstance(edges, dict):
        prov_block['inputs'] = prov.normalize_inputs(edges)


def _migrate_findings(data: Any, paths: tuple[str, ...]) -> None:
    """把 5 字段的旧 finding 补成 13 字段。见 `RESHAPED` 与 `_FINDING_FILL`。"""
    for path in paths:
        parts = tuple(path.split('.'))
        for holder, last in _walk(data, parts + ('rule',)):
            assert last == 'rule'
            f = holder
            f.setdefault('evidence_key', '|'.join(f.get('evidence') or []))
            # `owner is None` ⇔ 它不是结论（metric / unver）——这条不变量由载体在守。
            f.setdefault('owner', FINDING_OWNER.get(f['rule']))
            f.setdefault('actionable', False)
            f.setdefault('identity_hit', False)
            f.setdefault('converge', 'new')
            f.setdefault('unresolved_rounds', 0)


def _set_path(data: Any, path: str, value: Any) -> None:
    for holder, last in _walk(data, tuple(path.split('.'))):
        holder.setdefault(last, value)


def load_cut(a: Artifact, *, apply_cuts: bool = True, fill_added: bool = True) -> Any:
    """**盘上那一份 → 新契约形态**，纯函数，不落任何盘。

    顺序是有讲究的：先 `pop` 再改形状再补键——
    反过来会把刚补上的键又裁掉，而那种 bug 在断言上表现为「少了一个必填键」，
    指向的是**另一个根因**。

    **两个开关让三张表各自有一个、且只有一个断言点**（`test_corpus_fixtures.py`）：

    | 参数 | 停在哪 | 那一步能问的问题 |
    |---|---|---|
    | `apply_cuts=False, fill_added=False` | 只做形状改版 | **`CUT`**：盘上有哪些键是新契约不要的 |
    | `fill_added=False` | 形状 + 裁撤 | **`ADDED`**：裁完之后还缺哪些必填键 |
    | （默认） | 全做完 | **其余全部**：类型 / 取值面 / 后置条件 |

    ⚠ **形状改版必须最先做，连问 `CUT` 的那一步也不例外**：`inputs` 还是扁平的时候，
    轮次账的**两条分支都匹配不上**，于是联合类型把两边的错误一起报出来——
    一边说 `model` 是多余的、另一边说 `model` 缺了。
    **两条互相矛盾的错误摆在一起，等于什么也没断言。**
    """
    data = load_raw(a)
    if apply_cuts:
        _pop_cuts(data, CUT[a.pattern])
    _migrate_inputs(data)
    if a.pattern == 'stage7/name_map.json' and isinstance(data.get('book_txt'), dict):
        data['book_txt']['sha1'] = '0' * 40          # 见 `RESHAPED`：只能重算，这里是占位符

    if not fill_added:
        return data

    if a.pattern in (_ROUNDS_HOOK, 'stage6/hook.json'):
        _migrate_findings(data, _FINDING_PATHS_HOOK)
    elif a.pattern == 'stage7/name_map.json':
        _migrate_findings(data, ('book.*',))

    for path, value in ADDED.get(a.pattern, {}).items():
        if value is COMPUTED:
            continue                                  # 由 `_migrate_findings` 现算，见上
        if a.pattern == 'stage1/partition_report.json' and path == 'chapter_spans':
            value = [[i, i + 1] for i in range(data['num_chapters'])]
        _set_path(data, path, value)
    return data


# ─────────────────────────────────────────────────────────────────────────────
# 七、把 pydantic 的 `loc` 归一成本文件的路径写法
# ─────────────────────────────────────────────────────────────────────────────

#: ⚠ `X | None` 在 3.10+ 产的是 `types.UnionType`，与 `typing.Optional[X]` 的 origin
#: **不是同一个对象**。两个都要认——只认一个的后果是「这一段路径归一失败」，
#: 而失败的表现是把一个真字段名归一成 `*`，断言两边都看不出为什么对不上。
_UNION_ORIGINS = (typing.Union, types.UnionType)


def _unwrap(tp: Any) -> Any:
    """剥掉 `Optional[...]` / `Annotated[...]`，取出里面那个类型。"""
    while True:
        origin = typing.get_origin(tp)
        args = typing.get_args(tp)
        if origin in _UNION_ORIGINS:
            nonnull = [a for a in args if a is not type(None)]
            if len(nonnull) != 1:
                return None                     # 多分支联合：不再往下走，够用了
            tp = nonnull[0]
            continue
        if getattr(tp, '__metadata__', None):   # Annotated
            tp = args[0]
            continue
        return tp


def _descend(tp: Any, seg: str | int) -> Any:
    """从容器类型走一步。"""
    tp = _unwrap(tp)
    if tp is None:
        return None
    if isinstance(tp, type) and issubclass(tp, BaseModel):
        if 'root' in tp.model_fields and len(tp.model_fields) == 1:      # RootModel
            return _descend(tp.model_fields['root'].annotation, seg)
        for name, f in tp.model_fields.items():
            if seg in (name, f.alias):
                return f.annotation
        return None
    args = typing.get_args(tp)
    origin = typing.get_origin(tp)
    if origin in (list, tuple):
        return args[0] if args else None
    if origin is dict:
        return args[1] if len(args) == 2 else None
    return None


def _is_field(tp: Any, seg: str | int) -> bool:
    tp = _unwrap(tp)
    if not (isinstance(tp, type) and issubclass(tp, BaseModel)):
        return False
    if 'root' in tp.model_fields and len(tp.model_fields) == 1:
        return _is_field(tp.model_fields['root'].annotation, seg)
    return any(seg in (name, f.alias) for name, f in tp.model_fields.items())


def norm_loc(model: type[BaseModel], loc: tuple[Any, ...]) -> str:
    """`('applied','chapters','3','chars_before')` → `applied.chapters.*.chars_before`。

    ⚠ **必须靠模型定义来分辨「字段名」与「映射的键」**，不能靠长相：
    `judged` 的键是路径、`applied.chapters` 的键是章号、`subs` 的键是人名——
    **它们是数据不是 schema**，而 `_provenance` 是字段。拿正则去猜会两边都错。

    ⚠ 末段一律**原样保留**：`extra_forbidden` 的末段正是那个多出来的键，
    而它按定义不在 `model_fields` 里——照上面的规则会被归一成 `*`，那就什么也没断言了。
    """
    parts: list[str] = []
    tp: Any = model
    for seg in loc[:-1]:
        branch = _union_branch(tp, seg)
        if branch is not None:
            # 联合类型的分支标签**不是路径的一段**——pydantic 拿类名当标签，
            # 而 `_provenance.RoundFreeProvenance.model` 说的是同一个 `_provenance.model`。
            tp = branch
            continue
        if isinstance(seg, int) or not _is_field(tp, seg):
            parts.append('*')
        else:
            parts.append(str(seg))
        tp = _descend(tp, seg)
    parts.append(str(loc[-1]))
    return '.'.join(parts)


def _union_branch(tp: Any, seg: Any) -> Any:
    """`seg` 是不是这个联合类型的分支标签（pydantic 用类名当标签）。"""
    if typing.get_origin(tp) not in _UNION_ORIGINS:
        return None
    for arg in typing.get_args(tp):
        if getattr(arg, '__name__', None) == seg:
            return arg
    return None
