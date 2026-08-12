#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
api.py —— 对应 `docs/schema/_api.md`。**一次 HTTP 响应长什么样。**

【它与另外三份非产物契约的分界线】
`common.py` 管每份产物里都有的那几个键；`report.py` 管一次校验的输出；
`orchestration.py` 管一次跑 / 一批跑 / 这本书跑到哪了；
**本模块管的是「界面收到的那一份」**——它是前三者的**视图**，一个新事实都不产生。

【本模块的两条自有纪律，各配一条测试】

  ① **只组合，不手抄取值面。** 这里不许出现 `Literal['fresh', 'stale', …]` 这种
     字面量取值面——取值面的唯一落点是 `contracts/invariants.py`。
     一份响应模型抄一份取值面，就是那份取值面的第 N 个落点，而它们必然分家。
  ② **一个字段都不许自己算判定**（`R-11.4`）。`badge` / `passed` / `counts`
     只出现在 `CheckReport` 里。视图给的是「两份数字并排放着让人自己看」
     （`R-11` 节首语的原话），判定给的是「合不合格」——**混在一格里就是第二个判官**。

⚠ 本模块**不 import fastapi**。它住在 `contracts/` 正是为了这件事：
离线产前端 TS 类型的脚本要 import 它，而那个脚本一行 HTTP 都不发，
不该为此背上整个 web 栈（`tests/test_layering.py::test_contracts_never_imports_the_web_stack`）。

⚠ `/api/enums` **刻意没有响应模型**，理由见 `_api.md` §二：它的键集就是它的内容，
写成一个五十来个字段的模型等于把「自动发布」变回一份手写清单。**别来补齐它。**
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from contracts.invariants import (
    HOOK_KEYS,
    NONBODY_KINDS,
    REMOVED_ORIGIN,
    STAGE0_SEGMENT_KINDS,
    STAGE1_SEGMENT_KINDS,
    VOICE_KEYS,
)
from contracts.schema.base import FrozenModel, Sha1, StrictModel
from contracts.schema.orchestration import (
    Freshness, FreshnessReason, JobRecord, NovelOverview,
)
from contracts.schema.stage1 import HookCopy
from contracts.schema.stage3 import Measurable, StyleProvenance

VoiceKey = Literal[VOICE_KEYS]
HookKey = Literal[HOOK_KEYS]
Stage0SegmentKind = Literal[STAGE0_SEGMENT_KINDS]
Stage1SegmentKind = Literal[STAGE1_SEGMENT_KINDS]
NonbodyKind = Literal[NONBODY_KINDS]
RemovedOrigin = Literal[REMOVED_ORIGIN]


# ─────────────────────────────────────────────────────────────────────────────
# 零、S-00 / S-03 连续文本视图与零成本动作
# ─────────────────────────────────────────────────────────────────────────────

class Stage0Segment(FrozenModel):
    '''S-00 原始文本上的一个连续分区；坐标恒是 Python 码点半开区间。'''

    id: str = Field(min_length=1)
    kind: Stage0SegmentKind
    start: int = Field(ge=0)
    end: int = Field(ge=1)
    chars: int = Field(ge=1)
    flagged: bool
    text: str = Field(min_length=1)
    origin: RemovedOrigin | None = None
    removed_kind: NonbodyKind | None = None
    marker_line: str | None = None

    @model_validator(mode='after')
    def _interval_and_remove_metadata_agree(self) -> Stage0Segment:
        if self.end <= self.start:
            raise ValueError(f'[{self.start}, {self.end}) 不是非空半开区间')
        if self.chars != self.end - self.start or self.chars != len(self.text):
            raise ValueError('chars 必须同时等于 end-start 与 text 的码点长度')
        is_remove = self.kind == 'remove'
        if self.flagged != is_remove:
            raise ValueError('stage0 只有 remove 分区 flagged=true')
        metadata = (self.origin, self.removed_kind, self.marker_line)
        if is_remove != all(value is not None for value in metadata):
            raise ValueError('remove 必须带完整剔除元数据，非 remove 不得冒充剔除记录')
        return self


class Stage0Segments(StrictModel):
    '''S-00 连续原文。原文件移走时返回显式 unavailable，而不是假装没有剔除项。'''

    source_sha1: Sha1
    available: bool
    reason: Annotated[str, Field(min_length=1)] | None
    segments: list[Stage0Segment] = Field(default_factory=list)

    @model_validator(mode='after')
    def _availability_is_explicit(self) -> Stage0Segments:
        if self.available != (self.reason is None):
            raise ValueError('available 与 reason 必须恰好表达同一状态')
        if not self.available and self.segments:
            raise ValueError('原文不可用时不得发一份看似完整的分区')
        if self.available:
            if not self.segments or self.segments[0].start != 0:
                raise ValueError('可用的 stage0 分区必须从坐标 0 开始且非空')
            for left, right in zip(self.segments, self.segments[1:]):
                if left.end != right.start:
                    raise ValueError('stage0 分区必须按顺序首尾连续')
        ids = [segment.id for segment in self.segments]
        if len(ids) != len(set(ids)):
            raise ValueError('stage0 分区 id 不得重复')
        return self


class Stage0EditRequest(StrictModel):
    '''人在 S-00 选择要剔除的分区；三格共同构成乐观锁。'''

    expected_round: int = Field(ge=1)
    expected_source_sha1: Sha1
    expected_clean_sha1: Sha1
    removed_segment_ids: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def _ids_are_unique(self) -> Stage0EditRequest:
        if len(self.removed_segment_ids) != len(set(self.removed_segment_ids)):
            raise ValueError('removed_segment_ids 不得重复')
        if any(not item for item in self.removed_segment_ids):
            raise ValueError('removed_segment_ids 不得含空 id')
        return self


class Stage1Segment(FrozenModel):
    '''S-03 批准后 clean_text 上的一个连续只读分区。'''

    id: str = Field(min_length=1)
    kind: Stage1SegmentKind
    start: int = Field(ge=0)
    end: int = Field(ge=1)
    chars: int = Field(ge=1)
    flagged: bool
    text: str = Field(min_length=1)
    chapter_ordinal: int | None = Field(default=None, ge=1)
    source_chapter_label: str | None = None

    @model_validator(mode='after')
    def _interval_and_chapter_metadata_agree(self) -> Stage1Segment:
        if self.end <= self.start:
            raise ValueError(f'[{self.start}, {self.end}) 不是非空半开区间')
        if self.chars != self.end - self.start or self.chars != len(self.text):
            raise ValueError('chars 必须同时等于 end-start 与 text 的码点长度')
        is_chapter = self.kind == 'chapter'
        if is_chapter != (self.chapter_ordinal is not None):
            raise ValueError('只有 chapter 分区带 chapter_ordinal')
        if is_chapter != (self.source_chapter_label is not None):
            raise ValueError('只有 chapter 分区带 source_chapter_label')
        if self.flagged != (self.kind == 'hole'):
            raise ValueError('stage1 只有 hole 分区 flagged=true')
        return self


class Stage1Segments(StrictModel):
    input_sha1: Sha1
    segments: list[Stage1Segment] = Field(min_length=1)

    @model_validator(mode='after')
    def _segments_are_one_continuous_document(self) -> Stage1Segments:
        if self.segments[0].start != 0:
            raise ValueError('stage1 分区必须从 clean_text 坐标 0 开始')
        for left, right in zip(self.segments, self.segments[1:]):
            if left.end != right.start:
                raise ValueError('stage1 分区必须按顺序首尾连续')
        ids = [segment.id for segment in self.segments]
        if len(ids) != len(set(ids)):
            raise ValueError('stage1 分区 id 不得重复')
        return self


class Stage1ChapterRow(FrozenModel):
    chapter_ordinal: int = Field(ge=1)
    source_chapter_label: str = Field(min_length=1)
    chars: int = Field(ge=0)
    preview: str


class Stage1Chapters(StrictModel):
    chapters: list[Stage1ChapterRow] = Field(default_factory=list)

    @model_validator(mode='after')
    def _ordinals_are_contiguous(self) -> Stage1Chapters:
        got = [chapter.chapter_ordinal for chapter in self.chapters]
        if got != list(range(1, len(got) + 1)):
            raise ValueError('stage1 章节索引必须从 1 起连续有序')
        return self


class HookCopyView(StrictModel):
    hook_copy: HookCopy
    note: str = Field(min_length=1)


class UploadResult(StrictModel):
    sid: str = Field(min_length=1)
    job: JobRecord


# ─────────────────────────────────────────────────────────────────────────────
# 一、S-06 文风那一屏（`_api.md` §四）
# ─────────────────────────────────────────────────────────────────────────────

class VoiceLine(FrozenModel):
    """声音内核的一维。**数组的一项，不是对象的一个字段。**

    ⚠ 发成对象（`{narrative_distance: "...", ...}`）的话，八维的**顺序**就落在
    字段声明顺序上——那是第二个落点，而顺序在界面上是有意义的
    （`narrative_distance` 打头，`mode_variation` 收尾且明显长于其余七维）。
    数组 + 一条「顺序恒等于 `VOICE_KEYS`」的后置条件，顺序就只有一处算。
    """

    key: VoiceKey
    text: str = Field(min_length=1)

    #: 这一维写了多少字。**产地是代码，不是模型**——它是给人快速看「哪一维写得敷衍」的。
    chars: int = Field(ge=0)


class HookStyleLine(FrozenModel):
    """开篇文案段写法的一项。理由同 `VoiceLine`。

    ⚠ 它**没有** `chars`：抓包里就没有。别顺手补齐——补一个没人要的字段，
    下一个人会以为它有消费方（判据 6：加字段不是免费的）。
    """

    key: HookKey
    text: str = Field(min_length=1)


class DriftItem(FrozenModel):
    """一项实测漂移：`(键, 产物里存的, 照现在的 stage1 重算的)`。

    ⚠ **具名对象，不是三元组。** `novel/core/measure.py::drift` 内部返回的是元组，
    而元组一旦出线就是一个**位置数组**——前端拿到 `["body.chars", 9516, 9520]`，
    三个格子靠下标认。全仓对位置数组的态度只有一条：不发（同 `/api/enums`
    拒收 `NamedTuple` 的那一条）。

    ⚠ `stored` / `now` 有一个特殊值 `'（缺）'`：**两边摊平后取并集比**，
    少一个键也算漂移（F-054：老产物缺 `paragraph_sep_chars`，
    而下游拿 budget 直接除以 `avg_paragraph_chars`，段落目标系统性高 14%）。
    """

    key: str = Field(min_length=1)
    stored: int | float | str | list[str] | None
    now: int | float | str | list[str] | None


class StyleView(StrictModel):
    """`GET /api/novels/:sid/stage3/style`。**S-06 那一屏的全部数据。**

    ⚠⚠ **它是视图不是产物。** 右半边来自盘上那份 `stage3/style_grounding.json`；
    **左半边（实测数字与档位词）是代码现算的，产物在不在都算得出来**
    ——`04_delivery.md:123` 明写「`exists: false` 时左半边（实测）仍有内容」。
    这不是花哨：左半边量的是 **stage1**，它与 stage3 有没有跑过无关。
    """

    #: **由 `novel/paths.py` 从目录名派生**——新契约已把 `title`/`source_id` 从产物里
    #: 裁掉了（`stage3.md`：它们在本层唯一的作用是被扫描面排除掉，
    #: 「一个字段存在的理由是记得把它从检查里排除」本身就说明它不该存在）。
    sid: str = Field(min_length=1)
    title: str = Field(min_length=1)

    exists: bool

    #: 盘上有一份 `.INVALID` 在等人处理。**留着不说等于静默**：
    #: 正式稿可能是**更早**的一版，而人会以为它就是最近这次跑出来的。
    invalid_exists: bool

    #: 能不能按**当前** schema 读回来。**`None` = 产物不在盘上，无从谈起。**
    #: ⚠ 三态而不是 `bool`：`readable=False` 与 `exists=False` 是两件事
    #: （`ArtifactState` 已经踩过——盘上真有一份没有 `_provenance` 的产物，
    #: 折成「不存在」会让界面说「还没跑」，而它跑过）。
    readable: bool | None

    # ── 右半边：产物（`exists and readable` 时才有） ──────────────────────────

    voice: list[VoiceLine] = Field(default_factory=list)
    surface_habits: list[str] = Field(default_factory=list)
    hook_style: list[HookStyleLine] = Field(default_factory=list)
    measurable_stored: Measurable | None
    bands_stored: dict[str, str] | None
    provenance: StyleProvenance | None = Field(alias='_provenance')

    # ── 左半边：现算（与产物无关） ────────────────────────────────────────────

    #: 开篇文案段原文。来自 **stage1**，与本层产物无关。
    hook_copy_text: str | None

    measured_now: Measurable | None
    bands_now: dict[str, str] | None

    #: 量不出来时的人话，与 `measured_now` **恰好一个非空**（后置条件钉死）。
    measure_error: str | None
    #: **用 id 不用措辞**（`Q-6`）：异常类名。
    #: 「stage1 还没跑」（`UpstreamMissing`）与「stage1 的产物坏了」（`ArtifactUnreadable`）
    #: 对人的下一步是**相反**的指示，靠句子前缀去认会随一次措辞改动静默断掉。
    measure_error_kind: str | None

    #: 存/现两份实测的逐项差异。
    #: ⚠⚠ **`[]` 与 `null` 是两件事**：`[]` = 「比过了，没有漂移」，
    #: `null` = 「有一边算不出来，**无从比**」。压成一个 `[]` 就回到
    #: `Q-2` 那个病（「不知道」与「没问题」必须能区分）。
    drift: list[DriftItem] | None

    # ── 来源账 ────────────────────────────────────────────────────────────────

    freshness: Freshness
    reason: FreshnessReason
    #: 指向已经不在盘上的那些边，**按名字点名**（`Q-4` 亲自点过的 bug：
    #: 原型对取不到指纹的条目静默跳过，而盘上真有一条）。**空列表也要写。**
    ghosts: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def _postconditions(self) -> StyleView:
        """五条。每一条都在挡一种「两个状态在界面上长得一样」。"""
        # ① 不在盘上 ⇒ 「读不读得回来」是个没有意义的问题。
        if (self.readable is None) != (not self.exists):
            raise ValueError(
                f'exists={self.exists} 而 readable={self.readable!r}：'
                f'`readable=None` 必须恰好对应「产物不在盘上」')

        # ② 右半边要么整块在、要么整块不在。半块在的形态没有任何消费方，
        #    而它会让界面画出一屏「有 voice 没有 measurable」的假产物。
        loaded = bool(self.exists and self.readable)
        right = (self.voice, self.surface_habits, self.hook_style,
                 self.measurable_stored, self.bands_stored, self.provenance)
        if loaded and not all(x is not None and x != [] for x in right):
            raise ValueError('产物读回来了，而右半边有空的格子——那不是一份合法的产物')
        if not loaded and any(x is not None and x != [] for x in right):
            raise ValueError(
                f'exists={self.exists} readable={self.readable} 而右半边有内容——'
                f'那份内容是从哪儿来的？')

        # ③ 顺序只有一处算：`VOICE_KEYS` / `HOOK_KEYS`。
        if loaded:
            if [v.key for v in self.voice] != list(VOICE_KEYS):
                raise ValueError(f'voice 的顺序与 VOICE_KEYS 分家了：{[v.key for v in self.voice]}')
            if [h.key for h in self.hook_style] != list(HOOK_KEYS):
                raise ValueError(
                    f'hook_style 的顺序与 HOOK_KEYS 分家了：{[h.key for h in self.hook_style]}')

        # ④ 「量到了」与「量不了，原因是 X」恰好一个成立。
        #    同空 = 什么都没说；同非空 = 说了两件互相矛盾的事。
        if (self.measured_now is None) == (self.measure_error is None):
            raise ValueError(
                'measured_now 与 measure_error 必须**恰好一个**非空：'
                '同空是「什么都没说」，同非空是「量到了但也没量到」')
        if (self.measure_error is None) != (self.measure_error_kind is None):
            raise ValueError('measure_error 与 measure_error_kind 必须同进同出')
        # 档位词是 `bands()` 从实测数字现算的纯函数结果 ⇒ 它有没有，完全由实测有没有决定。
        # ⚠ 抓包里另有一个 `bands_error`，**本模型刻意不要它**：`bands()` 在一个合法的
        #    `Measurable` 上是全函数，那个格子只可能与 `measure_error` 说同一件事
        #    ——同一条信息的第二个落点。登记在 `_api.md` §四。
        if (self.bands_now is None) != (self.measured_now is None):
            raise ValueError('bands_now 与 measured_now 必须同进同出（档位词是现算的）')
        if (self.bands_stored is None) != (self.measurable_stored is None):
            raise ValueError('bands_stored 与 measurable_stored 必须同进同出')

        # ⑤ 两边都在才比得了。`drift=[]` 是「比过了没漂移」，`None` 是「无从比」。
        comparable = self.measurable_stored is not None and self.measured_now is not None
        if (self.drift is not None) != comparable:
            raise ValueError(
                f'drift={"有" if self.drift is not None else "null"} 而两份实测'
                f'{"都在" if comparable else "不齐"}——`[]` 是「没漂移」，`null` 是「无从比」')
        return self


# ─────────────────────────────────────────────────────────────────────────────
# 二、列表与作业（`_api.md` §五）
# ─────────────────────────────────────────────────────────────────────────────

class NovelList(StrictModel):
    """`GET /api/novels`。抓包 `novels.json` 的顶层就是 `{base, novels}`。

    ⚠ **复用 `NovelOverview`，不另造一张轻量清单。** 原型 `Web/novels.py` 里那张
    第二清单的代价是具体的：stage5 的产物是逐章的、存在性不能靠一次 `is_file()`，
    于是它被**整个排除在外**——「有没有陈旧产物」那个徽章从此不覆盖成稿。
    ⚠ 已知代价：每本书都要跑一遍完成度。盘上 3 本无压力，全语料 163 本会明显慢。
    要优化就给同一个函数加浅层分支，**别造第二张表**。
    """

    #: 语料根目录的绝对路径。界面上要显示「你现在看的是哪一批书」。
    base: str = Field(min_length=1)
    novels: list[NovelOverview] = Field(default_factory=list)


class JobList(StrictModel):
    """`GET /api/jobs?sid=`。

    ⚠ 抓包 `jobs.json` 是 `{"jobs":[]}`——**一个样本都没有**，
    所以元素形状只能从 `_orchestration.md` 来（那份文档自己写着这条证据缺口）。
    """

    jobs: list[JobRecord] = Field(default_factory=list)


class LogChunk(StrictModel):
    """`GET /api/jobs/:id/log?offset=N`。按**字节**续读。

    ⚠ 中文一字跨 3 字节，而日志是按字节追加的 ⇒ **只在读的一侧解码**，
    且一次读到的末尾可能切在一个字的中间。`next_byte` 原样回传给下一次 `?offset=`，
    **不许拿 `len(text)` 去推**——那是字符数不是字节数，推出来的偏移会越滑越远。
    """

    text: str
    next_byte: int = Field(ge=0)


class PreviewRequest(StrictModel):
    """`POST /api/jobs/preview` 的请求体。响应是 `JobStart`（`_orchestration.md`）。

    ⚠ **响应直接用 `JobStart` 是有意的**：那个模型自带
    `paid == 任一步 paid` / `writes == 任一步 writes` 的后置条件，于是
    「整份贵贱是逐步折叠出来的」这条 `R-11.7` 白拿；而且**真跑时
    `jobs.run()` 构造的就是这个类**——「预览与真跑是同一个对象」
    从一句话变成字面事实（`R-11.8`）。

    ⚠ `flags` 里的**带值 flag 要带上值**（`--apply-edit <路径>`）。
    `/api/enums` 的 `actions[].needs_value` 就是用来告诉前端「这个按钮要先要一个文件」的。
    """

    sid: Annotated[str, Field(min_length=1)] | None
    node: str = Field(min_length=1)
    mode: str | None = None
    flags: list[str] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# 三、错误体（`_api.md` §六）
# ─────────────────────────────────────────────────────────────────────────────

class ErrorBody(StrictModel):
    """一次失败的响应体。状态码表在 `novel/api/errors.py::STATUS`。"""

    #: **异常类名**，不是措辞（`Q-6`）。取值面 = `novel/errors.py` 的类名集合，由测试闭合。
    #: 前端的「每屏四态」要求区分「后端没起来 / 这本书没有 / 这一步还没跑」
    #: （`04_delivery.md`），靠句子前缀去认，一次措辞改动就静默断掉。
    kind: str = Field(min_length=1)

    message: str = Field(min_length=1)

    #: 缺的是**哪一个**（`'novel'` / `'stage3/style_grounding.json'`）。
    #: `None` = 这次失败与某一份具体的东西无关。
    missing: str | None = None

    #: 下一步该做什么。`None` = 说不出来——**说不出来就别编一句**。
    hint: str | None = None
