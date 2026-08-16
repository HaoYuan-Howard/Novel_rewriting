#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
store.py —— 产物的**唯一**读写口。`ArtifactStore(ctx)`，一次跑一个实例。

【为什么必须只有一条路】来源账的「结构上不可能漏」只在**所有人都走这条路**时成立。
原型立过同一条纪律，实测被**十几处裸读绕过**——原因很简单：**绕过更省事**。
于是 `chapter_blueprints` 漏记 global、`style_grounding` 与 `fidelity_reference`
的 `inputs` 干脆是空的。一份自称能证明来源的记录悄悄漏掉一个来源，
**比没有这个记录更坏**，因为下游会拿它当证据。

⇒ 本轮换成**让绕过变成更费事的那条路**，四道叠起来：

  1. **只有 `store.read` 返回带类型的模型**，而所有下游函数签名都吃模型
     ⇒ 绕过意味着同时放弃校验和类型。**这是主杠杆**，另外三条是补漏。
  2. `tests/test_layering.py` 的 AST 测试：`novel/**` 里 `json.load(` 只许出现在本文件。
  3. **写盘口拒绝**（不是像原型那样 `print` 一句警告就照写）——见 `_refuse_bad_provenance`。
  4. **没有模块级 `save_json`**：写盘只经 store 实例，而 store 只由 `RunContext` 造。

【落盘一律原子 + 读回验证】
`open(p,'w')` 是「先截断再写」：在它写完之前，盘上那份是**半截 JSON**。
原型踩过——两个节点并发时，后者正好在前者落盘的瞬间去读它，
而半截 JSON 的坏处不是读失败，是**读失败会被当成「上游还没产出」**，
于是一次**成功的付费跑**被报成失败。
⚠ 读回不是多余的：一份**读不出来的产物**与一份不存在的产物，在需要它的那一刻是同一件事，
而前者还会让人以为自己有备份。**这里读回还要过一遍模型**——
只 `json.load` 一下只能证明「是合法 JSON」，证明不了「下次还读得回来」。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, TypeVar

from pydantic import BaseModel, ValidationError

from contracts.schema import (
    ArtifactVersionError, InputEdge, Provenance, decode_artifact, encode_artifact,
)
from novel import paths, prov
from novel.consumption import producer_node, projection_for
from novel.errors import (
    ArtifactUnreadable, ByteFormRefused, PathRefused, ProvenanceRefused, UpstreamMissing,
)
from novel.prov import FreshnessReport
from novel.run import RunContext

M = TypeVar('M', bound=BaseModel)

#: 产物里那个来源账字段的**盘上键名**。模型侧字段叫 `provenance`（`_` 开头在 Pydantic 里
#: 是私有属性），靠 `Field(alias='_provenance')` 对上；这里按 alias 找，不按字段名猜。
PROVENANCE_KEY = '_provenance'


def provenance_field(model: type[BaseModel]) -> str | None:
    """这个模型里哪个字段是来源账。没有就返回 `None`。

    ⚠ **五份产物按契约就没有来源账**（stage0 的剔除报告 · stage1 的三份 ·
    `name_map_edit.json` 那份**人产的输入**）。那不是漏，别替它们补一份
    ——补出来的是一条没人验证过的记录。
    """
    for name, f in model.model_fields.items():
        if f.alias == PROVENANCE_KEY:
            return name
    return None


class ArtifactStore:
    """一次跑的读写口。**它只由 `RunContext` 造**，因为记账要落在那个实例上。"""

    def __init__(self, ctx: RunContext) -> None:
        self.ctx = ctx

    # ── 定位 ──────────────────────────────────────────────────────────────

    def path(self, rel: str) -> Path:
        return paths.artifact_path(self.ctx.novel_dir, rel)

    def exists(self, rel: str) -> bool:
        return self.path(rel).exists()

    # ── 读 ────────────────────────────────────────────────────────────────

    def read(self, model: type[M], rel: str) -> M:
        """读一份上游产物，**顺手把它记进本次跑的来源账**。

        本节点真正读到的字段投影从 `novel.consumption` 按 `(node, rel)` 取得。
        这里不再接受调用点临时传一个函数：写来源账与 freshness 重算必须共用同一对象。
        未登记仍诚实地写 `consumed=null`，不会把「不知道」偷换成 fresh。

        ⚠ 投影吃的是**盘上那份原始 dict**，不是校验后的模型。理由是新鲜度那一侧：
        判过期时要对一份**可能已经不合当前 schema** 的上游重算同一个投影，
        而模型化会先炸在校验上。同一个函数、同一种输入，两侧才对得上。
        """
        key = paths.input_key(rel)          # ← 两级键；三级路径在这里就被拒（见 `PathRefused`）
        project = projection_for(self.ctx.node, key)
        raw = self._read_bytes(rel)
        data = self._loads(rel, raw)
        obj, data = self._validate_payload(model, rel, data)
        self.ctx.record_edge(key, InputEdge(
            file=prov.fingerprint_bytes(raw),
            consumed=prov.projection_fingerprint(project(data)) if project else None,
        ))
        return obj

    def read_judged(self, model: type[M], rel: str, *,
                    project: Callable[[Any], Any] | None = None) -> tuple[M, str, str | None]:
        """读一份**成稿**。记进 `judged`，**不记进 `inputs`**（`_common.md` §3.2）。

        返回 `(模型, 整份指纹, 投影指纹|None)`——调用方要拿它们去填 `judged[]` 那三格。

        ⚠⚠ **为什么成稿必须走另一条路**：`inputs` 的键是两级的，
        `stage5/deep/ch01.json` 会被记成一个**反查不到文件**的键 ⇒ 指纹取不到
        ⇒ 那条边**永远显示对得上**。**一个假的匹配比没有记录更坏。**
        ⇒ 逐份成稿记**完整相对路径** + 指纹。
        """
        paths.check_rel_deep(rel)
        raw = self._read_bytes(rel)
        data = self._loads(rel, raw)
        obj, data = self._validate_payload(model, rel, data)
        fp = prov.fingerprint_bytes(raw)
        consumed = prov.projection_fingerprint(project(data)) if project else None
        self.ctx.record_judged(paths.check_rel_deep(rel), fp)
        return obj, fp, consumed

    def read_own(self, model: type[M], rel: str) -> tuple[M, Any]:
        """读**本节点自己产的那一份**（复检 / 重算 / 提升前要先看看它）。
        返回 `(模型, 盘上原始 dict)`。

        ⚠⚠ **它刻意不记来源边，这是本方法存在的全部理由。**
        `inputs` 回答的是「我这次产出照哪些**上游**算的」——
        把产物自己记成自己的上游是**假账**，而且那条边永远显示对得上。

        ⚠ 它**不是**「绕过唯一读路」的口子：字节读取、解码、JSON 解析、模型校验
        走的还是同一条路（`_read_bytes` / `_loads` / `_validate`），
        少的只有 `record_edge` 那一行。原型这里是**裸 `json.load`**，
        于是解码与校验那两道也一起绕过去了。

        ⚠ **同时返回原始 dict** 是有用的：判据跑在**模型之前**的那份 dict 上
        （新契约把八维做成了 `Field(min_length=1)`，模型化之后 `required` 必然全绿，
        而按「判据 vs 载体按失败成本划」它该留在判据侧——载体拒收要再花一次钱，
        判据拦下是 0 成本自救）。见 `novel/stages/stage3/checks.py` 开头那段。
        """
        raw = self._read_bytes(rel)
        data = self._loads(rel, raw)
        return self._validate_payload(model, rel, data)

    def read_text(self, rel: str) -> str:
        """读一份**字节形态**产物（`clean_text.txt` / `draft.md` / `book.txt`）。记边。

        ⚠ **按字节读再自己解码**，不用文本模式：文本模式会把 `\\r\\n` 悄悄翻成 `\\n`，
        于是「盘上那份带 CRLF」这件事在读的一侧**看不见**，
        而写的一侧刚刚为它立了一道闸（`write_text`）。两侧口径必须一致。

        ⚠ **先解码，再记边。** 反过来的话，一份解不出来的文件会在本次跑的来源账里
        留下一条边——那条边说的是「我读过它」，而我们其实一个字都没读到。
        """
        key = paths.input_key(rel)
        raw = self._read_bytes(rel)
        text = self._decode(rel, raw)
        project = projection_for(self.ctx.node, key)
        self.ctx.record_edge(key, InputEdge(
            file=prov.fingerprint_bytes(raw),
            consumed=prov.projection_fingerprint(project(text)) if project else None,
        ))
        return text

    def _read_bytes(self, rel: str) -> bytes:
        p = self.path(rel)
        try:
            return p.read_bytes()
        except FileNotFoundError:
            raise UpstreamMissing(
                f'{self.ctx.node}: 上游产物不在盘上：{rel}（{p}）——先跑产它的那一层') from None
        except OSError as e:
            raise ArtifactUnreadable(f'{self.ctx.node}: 读不了 {rel}：{e!r}') from e

    @staticmethod
    def _decode(rel: str, raw: bytes) -> str:
        """字节 → 文本。**解不出来是显式终态**，不是一条裸 `UnicodeDecodeError`。

        ⚠ `_loads` 早就把 `UnicodeDecodeError` 兜成 `ArtifactUnreadable` 了，
        而 `read_text` 那一侧原来是裸 `raw.decode('utf-8')`——同一件事在两条读路上
        一条有词汇、一条没有。`errors.py` 明写「**不设默认异常**，每一处终态都要选一个具体的类」，
        而只有 `novel/cli/` 那一层会把这些类翻译成退出码：
        漏网的那一条到了 CLI 就是一段栈，说不出「哪个节点、要跑什么、盘上哪份坏了」。
        """
        try:
            return raw.decode('utf-8')
        except UnicodeDecodeError as e:
            raise ArtifactUnreadable(
                f'{rel} 不是 UTF-8：{e!r}。'
                f'⚠ 三份字节形态产物（`clean_text.txt` / `draft.md` / `book.txt`）盘上一律 UTF-8；'
                f'别把它当成「上游还没产出」——那会把一次成功的付费跑报成失败') from e

    @classmethod
    def _loads(cls, rel: str, raw: bytes) -> Any:
        try:
            return json.loads(cls._decode(rel, raw))
        except json.JSONDecodeError as e:
            raise ArtifactUnreadable(
                f'{rel} 不是合法 JSON：{e!r}。'
                f'⚠ 别把它当成「上游还没产出」——那会把一次成功的付费跑报成失败') from e

    @staticmethod
    def _validate_payload(model: type[M], rel: str, data: Any) -> tuple[M, Any]:
        try:
            decoded = decode_artifact(rel, data)
        except ArtifactVersionError as e:
            raise ArtifactUnreadable(str(e)) from e
        try:
            return model.model_validate(decoded.payload), decoded.payload
        except ValidationError as e:
            raise ArtifactUnreadable(
                f'{rel} 的 schema v{decoded.version} 不合 {model.__name__} 的当前契约：\n{e}') from e

    # ── 写 ────────────────────────────────────────────────────────────────

    def write(self, rel: str, artifact: BaseModel) -> Path:
        """落一份正式产物。**先校验后落盘**——校验在 `artifact` 被构造的那一刻就发生过了。

        ⚠ `.INVALID` 不走这条（走 `write_invalid`）：两条路的语义不同，
        合成一条之后「这份合格」与「这份被拦下了」就只剩一个调用点参数的差别，
        而那种差别是会被传错的。
        """
        rel = paths.check_rel_deep(rel)
        if paths.is_invalid(rel):
            raise PathRefused(
                f'{rel} 是 `.INVALID` 形态，不走 `write`。'
                f'被闸门拦下的稿走 `write_invalid`（它强制你交代 `gates_skipped`）')
        return self._write_json(rel, artifact)

    def write_invalid(self, rel: str, artifact: BaseModel, *,
                      gates_skipped: list[str]) -> Path:
        """落一份被闸门拦下的稿。**不覆盖已有正式产物**（`Q-5`：先校验后落盘）。

        `gates_skipped` **没有默认值**，调用方必须回答「有没有闸门缺料」。
        ⚠ **空列表也要写。** 空列表 = 这次没有闸门缺料；键不在 = 这份产物早于这套机制。
        它是判据 36 那整套设计的**唯一痕迹**，而「闸门没跑成」与「闸门跑过没事」
        正是 `Q-2` 要求必须能区分的两件事。

        ⚠ 稿子**没有丢，也没有再花钱**：0 成本提升路是 `promote_invalid`。
        """
        rel = paths.check_rel_deep(rel)
        src = self._provenance_of(artifact)
        if src is None:
            raise ProvenanceRefused(
                f'{type(artifact).__name__} 没有 `_provenance` 字段，落不了 `.INVALID`：'
                f'`gates_skipped` 只有来源账这一个落点，没有它就等于没记过闸门缺料')
        return self._write_json(paths.invalid_of(rel), artifact,
                                prov_override=src.model_copy(
                                    update={'gates_skipped': list(gates_skipped)}))

    def write_text(self, rel: str, text: str) -> Path:
        """落一份**字节形态**产物。**显式按字节写 `\\n`，不带 BOM。**

        ⚠ 盘上三份字节形态产物（`clean_text.txt` / `draft.md` / `book.txt`）**全是 LF-only**，
        而 Windows 上 `open(p, 'w')` 默认吐 CRLF——同一份代码在两台机器上产出两种字节，
        而本项目有一条逐字节等价的验收线。

        ⚠⚠ **拿到 `\\r` 一律拒收，不静默归一。** 归一是**内容决策**，属产地
        （它要为「我改了什么」负责，可能还要记账）；写盘口只保证**平台不掺东西**。
        替产地做一次它自己没记账的改写，正是本项目要消灭的那种「安静地改了点什么」。
        """
        rel = paths.check_rel_deep(rel)
        if '\r' in text:
            n = text.count('\r')
            raise ByteFormRefused(
                f'{rel}: 文本里有 {n} 个 `\\r`。三份字节形态产物盘上全是 LF-only，'
                f'而归一属产地——请在产它的那一层先归一（并按 `_common.md` §九 判断要不要记账），'
                f'别让写盘口替你悄悄改内容')
        p = self.path(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = text.encode('utf-8')          # ← 不加 BOM：`utf-8` 不是 `utf-8-sig`
        self._replace_atomically(p, data)
        return p

    # ── `.INVALID` 的 0 成本提升路 ────────────────────────────────────────

    def promote_invalid(self, rel: str, model: type[M]) -> Path:
        """按**当前**判据重判合格之后，把 `.INVALID` 提升为正式产物。**唯一提升入口。**

        两件事，都是拿实测教训换来的：

        ① **来源账继承，一个字不改**（`_common.md` §5.2）。`promoted()` 的签名里
           **没有** `prompts` / `model` / `inputs`——**没有参数，就没有可以传错的东西**。
           原型的 `--revalidate` 之所以能**伪造**，正是因为提升分支够得到那个
           会构造一份全新来源账的函数：它盖上当前 SYS 指纹 + 当前 model + 当前 inputs，
           `round` 还 +1，而**没有任何一次新落笔发生过**。

        ② **`.INVALID` 归档进 `_rounds/`，不删**（`Q-8`）。原型「读完问题稿就把它删了」
           造出的正是盘上那条幽灵边：另一份产物的 `inputs` 还指着它。
           **删一个还被别人指着的文件，会同时制造两个问题。**

        ③ **已有的正式稿也要归档，不许被覆盖掉。** 落正式稿走的是原子替换，
           而原子替换是**无条件**的：盘上那份**花过钱**的正式稿原地消失，
           `_rounds/` 里连一份副本都没有。原来只归档 `.INVALID` 那一条历史。
           对 stage5 这直接违反「上一轮正式稿进入 `_rounds`」；对别的层，
           它违反的是本模块自己那句「**归档是出事之后唯一能回去的那一份**」。
        """
        inv_rel = paths.invalid_of(paths.check_rel_deep(rel))
        official_rel = paths.check_rel_deep(rel)
        if not self.exists(inv_rel):
            raise UpstreamMissing(f'{self.ctx.node}: 没有 {inv_rel} 可提升')

        artifact, _payload = self._validate_payload(
            model, inv_rel, self._loads(inv_rel, self._read_bytes(inv_rel)))
        src = self._provenance_of(artifact)
        if src is None:
            raise ProvenanceRefused(
                f'{inv_rel} 没有来源账，提升上来就是一份来历不明的正式产物。'
                f'⚠ 盘上真有这么一份（`stage4/plan_chapters.json`，一本书），'
                f'根因就是问题稿当初是用裸 `json.dump` 写的——那条路已经不存在了')

        # 【四步的顺序是挑过的】① 把两个**大概率的失败**挡在「一个字节都没动」之前
        # （轮次号撞了 · 载体层往返不过）② 上一轮正式稿进归档 ③ 落正式稿
        # ④ 把 `.INVALID` 挪进归档。
        #
        # ⚠ 剩下那个窗口（③④ 之间硬崩）**是刻意选的那一半**：崩在这里会同时留下
        #   一份提升好的正式稿和一份还没挪走的 `.INVALID`，而**再跑一次 `--revalidate`
        #   就自愈**（两份内容本来就相同，正式稿那份带 `revalidated: true`）。
        #   反过来「先归档后落稿」崩掉的话，`.INVALID` 已经从它该在的位置消失、
        #   正式稿又还没写——人必须自己去 `_rounds/` 里翻，而没有一行报告会告诉他去翻。
        # ⚠ 这一处进 `R-14.6` 那张「不保证原子的地方」的表（D 期建表时别漏了它）。
        round_no = self._round_on_disk(inv_rel) or self.next_round(inv_rel)
        dest = self.path(paths.round_rel(inv_rel, round_no))
        if dest.exists():
            raise PathRefused(
                f'归档位 {dest} 已被占用，**一个字节都没动**。轮次号多半算错了')
        data = self._render(official_rel, artifact, prov_override=src.promoted(code=self.ctx.code))
        self._verify_round_trip(official_rel, type(artifact), data)

        p = self.path(official_rel)
        # ② ⚠⚠ **挪，不是 `keep=True` 抄。** 抄一份会让归档位从此被占住，
        #   于是③④之间崩溃后的重跑撞上 `PathRefused` 卡死——那正好毁掉上面刻意选的那半个窗口。
        #   挪走之后重跑没有东西可归档，`--revalidate` 自愈。
        # ⚠⚠ **字节相同就不叫销毁。** 归档是为了保住一份**会被销毁**的版本；
        #   ③④ 之间崩过一次的话，重跑时盘上那份已经是提升好的那一份，
        #   再归档一次就是凭空往轮次账里塞一个**幻影轮**——对 stage5 就是
        #   「把一次重判记成第 N 轮写作」，正是 `promoted()` 存在要防的那件事。
        # ⚠ 正式稿与 `.INVALID` 的 stem 不同（`stem_of` 保留 `.INVALID`），
        #   两条历史各编各的号，`r1` 不会撞。
        if p.is_file() and p.read_bytes() != data:
            self.archive_round(official_rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        self._replace_atomically(p, data)
        self.archive_round(inv_rel, round_no=round_no)
        return p

    # ── 轮次与归档 ────────────────────────────────────────────────────────

    def seen_rounds(self, rel: str) -> set[int]:
        """这份产物在盘上见过的所有轮次：`_rounds/` 里的 + 当前那份记的。

        ⚠ **含 `.INVALID`**（`_common.md` §六）：被拦下的那一轮也是落过笔的一轮，
        不算进去会让下一轮与它同号。
        """
        rel = paths.check_rel_deep(rel)
        rounds = paths.archived_rounds(self.path(paths.rounds_dir_of(rel)), paths.stem_of(rel))
        live = {rel} if paths.is_invalid(rel) else {rel, paths.invalid_of(rel)}
        for candidate in sorted(live):
            n = self._round_on_disk(candidate)
            if n is not None:
                rounds.add(n)
        return rounds

    def next_round(self, rel: str) -> int:
        """下一轮 = **盘上见过的最大轮次 + 1**，不是 `len(glob) + 1`。见 `prov.next_round`。"""
        return prov.next_round(self.seen_rounds(rel))

    def archive_round(self, rel: str, *, round_no: int | None = None,
                      keep: bool = False) -> Path:
        """把盘上当前这份挪进 `_rounds/<stem>.r<N>.json`。

        `round_no=None` ⇒ **按它自己记的那一轮**归档；没记就取 `next_round`。
        ⚠ **必须是「它自己的」而不是「下一个」**：写错这一处不会报错，
        它只是让轮次账**整体错位一格**（第 1 轮的内容躺在 `r2` 里、新稿标成第 3 轮），
        而等到有人去数「这是第几轮」时，已经没有别的账可以校对它了。

        `keep=True` ⇒ 抄一份而不是挪走。**这个差别是语义的，不是风格的**：
        逐章成稿归档之后当前文件必须**消失**（下一轮马上要写一份新的到同一个路径），
        而「先抄一份再原地改写」的那几层需要它留着。
        """
        rel = paths.check_rel_deep(rel)
        src = self.path(rel)
        if not src.exists():
            raise UpstreamMissing(f'{self.ctx.node}: 要归档的 {rel} 不在盘上')
        if round_no is None:
            round_no = self._round_on_disk(rel) or self.next_round(rel)
        dest = self.path(paths.round_rel(rel, round_no))
        if dest.exists():
            raise PathRefused(
                f'归档目标已存在：{dest}。**归档是出事之后唯一能回去的那一份**，'
                f'不许静默覆盖——轮次号多半算错了（`len(glob)+1` 那个式子的典型症状）')
        dest.parent.mkdir(parents=True, exist_ok=True)
        if keep:
            self._replace_atomically(dest, src.read_bytes())
        else:
            # ⚠ **一步 `os.replace`，不是「抄一份再删」。** 后者中间崩一次会同时留下
            #   原件和归档，而下一次归档会撞上已存在的目标、把这条路堵死。
            #   `_rounds/` 与产物同卷，`replace` 在这里是原子的。
            os.replace(src, dest)
        return dest

    def archive_bytes(self, rel: str, destination: str, *, keep: bool = False) -> Path:
        '''把任意字节形态产物归档到显式目标；绝不覆盖已有目标。

        stage0 的一轮由 removal_report.json 与 clean_text.txt 两种后缀组成，通用
        archive_round 固定的 json 命名无法表达后者。目标名由 stage0 唯一函数构造，
        此处只负责不覆盖、原子复制或移动。复制式归档遇到相同字节可幂等重放，
        供多文件轮次从两次复制之间的中断恢复；移动式归档仍拒绝重放，避免制造幻影轮。
        '''
        source_rel = paths.check_rel_deep(rel)
        destination_rel = paths.check_rel_deep(destination)
        source = self.path(source_rel)
        dest = self.path(destination_rel)
        if not source.is_file():
            raise UpstreamMissing(f'{self.ctx.node}: 要归档的 {source_rel} 不在盘上')
        source_data = self._read_bytes(source_rel)
        if dest.exists():
            try:
                same = dest.is_file() and dest.read_bytes() == source_data
            except OSError as exc:
                raise ArtifactUnreadable(f'{self.ctx.node}: 读不了归档目标 {dest}：{exc!r}') from exc
            if same:
                if keep:
                    # 中断可能发生在一轮多文件归档的两次复制之间。已经复制成功的那一份
                    # 必须可安全重放，否则“重跑一次自愈”会被自己的归档位挡住。
                    return dest
                raise PathRefused(
                    f'归档目标已存在且字节相同：{dest}。移动式归档不重放，'
                    '否则会把同一版再记成一轮')
            raise PathRefused(f'归档目标已存在且字节不同：{dest}。上一版不许被静默覆盖')
        dest.parent.mkdir(parents=True, exist_ok=True)
        if keep:
            self._replace_atomically(dest, source_data)
        else:
            os.replace(source, dest)
        return dest

    def _round_on_disk(self, rel: str) -> int | None:
        """盘上那份 `_provenance.round`；没有文件 / 没有这个键 ⇒ `None`。"""
        p = self.path(rel)
        if not p.is_file():
            return None
        try:
            raw_data = json.loads(p.read_bytes().decode('utf-8'))
            data = decode_artifact(rel, raw_data).payload
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ArtifactVersionError):
            return None
        if not isinstance(data, dict):
            return None
        n = ((data.get(PROVENANCE_KEY) or {}) or {}).get('round')
        return int(n) if isinstance(n, int) and n >= 1 else None

    # ── 新鲜度 ────────────────────────────────────────────────────────────

    def freshness(self, rel: str) -> FreshnessReport:
        """这份产物是拿**盘上现在这几份**上游产的吗？算法在 `novel/prov.py::freshness`。

        ⚠ **读原始 dict，不过模型。** 一份陈旧产物很可能已经不合当前 schema
        （新契约裁了一批字段之后，原型产的 3/3 本全部加载失败），
        而「它是不是陈旧的」恰恰是这时候最该答得出来的问题。
        让新鲜度依赖校验通过，等于在最需要它的那一刻把它关掉。
        """
        rel = paths.check_rel_deep(rel)
        p = self.path(rel)
        if not p.is_file():
            raise UpstreamMissing(f'{self.ctx.node}: 要判新鲜度的 {rel} 不在盘上')
        try:
            raw_data = json.loads(p.read_bytes().decode('utf-8'))
            data = decode_artifact(rel, raw_data).payload
        except (UnicodeDecodeError, json.JSONDecodeError, ArtifactVersionError) as e:
            raise ArtifactUnreadable(f'{rel} 不是合法 JSON，判不了新鲜度：{e!r}') from e

        recorded = None
        if isinstance(data, dict):
            block = data.get(PROVENANCE_KEY)
            recorded = (block or {}).get('inputs') if isinstance(block, dict) else None
            if block is not None and recorded is None:
                recorded = {}                # 有来源账但没有 `inputs` ⇒ `no_inputs`，不是 `no_provenance`
        node = producer_node(rel, fallback=self.ctx.node)
        return prov.freshness(
            recorded,
            file_now=self._file_fp_of_key,
            consumed_now=lambda key: self._consumed_fp_of_key(node, key),
        )

    def _file_fp_of_key(self, key: str) -> str | None:
        """来源账里那个键 → 盘上现在的整份指纹。**取不到一律 `None`（幽灵边）。**

        ⚠ 键是存量产物里记下来的，可能是任何字符串。不合法的一律当成「反查不到」——
        **别让一条坏键抛异常**，那会把「有一条边指向已删文件」变成「整份报告出不来」。
        """
        parts = str(key).replace('\\', '/').split('/')
        if not parts or any(p in ('', '.', '..') for p in parts):
            return None
        p = self.ctx.novel_dir.joinpath(*parts)
        try:
            return prov.fingerprint_bytes(p.read_bytes())
        except OSError:
            return None

    # ── 内部：写盘口的两道闸 + 原子落盘 ───────────────────────────────────

    def _consumed_fp_of_key(self, node: str | None, key: str) -> str | None:
        if node is None or (project := projection_for(node, key)) is None:
            return None
        return self._project_current(key, project)

    def _project_current(self, key: str, project: Callable[[Any], Any]) -> str | None:
        parts = str(key).replace(chr(92), '/').split('/')
        if not parts or any(part in ('', '.', '..') for part in parts):
            return None
        p = self.ctx.novel_dir.joinpath(*parts)
        try:
            raw = p.read_bytes()
            data: object = (decode_artifact(key, json.loads(raw.decode('utf-8'))).payload
                            if p.suffix.lower() == '.json' else raw.decode('utf-8'))
            return prov.projection_fingerprint(project(data))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError,
                AttributeError, KeyError, IndexError, TypeError, ValueError,
                ArtifactVersionError):
            return None

    @staticmethod
    def _provenance_of(artifact: BaseModel) -> Provenance | None:
        name = provenance_field(type(artifact))
        return getattr(artifact, name) if name else None

    def _refuse_bad_provenance(self, rel: str, src: Provenance) -> None:
        """写盘口的闸。**拒绝，不是打印一句警告然后照写。**

        ① **`prompts` 非空而 `inputs` 为空 ⇒ 拒。** 一个跑过 LLM 的节点不可能没有上游；
           `inputs` 空就是**某处绕过了 `store.read`**。原型这里是 `print` 一句，
           于是三份产物的来源账各漏各的，而没有任何一层出声。

        ⚠⚠ **另一道闸（「0 成本节点不许记 `model`」）不在这里，在铸造口**
        （`RunContext.llm_provenance`）。这是本轮实测改掉的一处：
        放在写盘口会**误伤三条 0 成本入口**——`--revalidate` / `--remeasure` / `--recheck`
        写回的是一份**继承来的**来源账，它记的 `model` 属于当初那次付费产出，
        不是本次跑的新宣称；`stage6.approve` / `stage7.approve` 同理
        （它们是 0 成本节点，改写的却是付费节点产的那份产物）。
        ⇒ 该拦的是「**0 成本的跑现造一份声称模型产了它的账**」，而现造只有一个入口。
        """
        prompts = getattr(src, 'prompts', None)
        if prompts and not src.inputs:
            raise ProvenanceRefused(
                f'{self.ctx.node} 要往 {rel} 写一份 `prompts` 非空而 `inputs` 为空的来源账。'
                f'跑过 LLM 的节点不可能没有上游 ⇒ 多半有一处绕过了 `store.read`（它是唯一入口，'
                f'记账在那里发生）。⚠ 原型这里只 print 一句警告然后照写，'
                f'代价是三份产物的来源账各漏各的，而没有任何一层出声')

    def _render(self, rel: str, artifact: BaseModel, *,
                prov_override: Provenance | None = None) -> bytes:
        """产物 → **将要落盘的那串字节**。过写盘口那道闸，但一个字节都不写。

        ⚠ 抽出来是给 `promote_invalid` 用的：它要在**动归档之前**知道自己将要写什么，
        才能回答「盘上那份会不会被销毁」。见那边第②步。
        """
        name = provenance_field(type(artifact))
        payload = artifact.model_dump(by_alias=True, mode='json')
        if name is not None:
            src = prov_override if prov_override is not None else getattr(artifact, name)
            self._refuse_bad_provenance(rel, src)
            # ⚠ 三态只在 `to_disk()` 里兑现：`revalidated: null` 与「没有这个键」
            #   在 JSON 里必须是两份不同的字节，而 `model_dump` 会把 `None` 写成 `null`。
            payload[PROVENANCE_KEY] = src.to_disk()
        payload = encode_artifact(rel, payload)
        return (json.dumps(payload, ensure_ascii=False, indent=2) + '\n').encode('utf-8')

    def _write_json(self, rel: str, artifact: BaseModel, *,
                    prov_override: Provenance | None = None) -> Path:
        data = self._render(rel, artifact, prov_override=prov_override)
        p = self.path(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        self._verify_round_trip(rel, type(artifact), data)
        self._replace_atomically(p, data)
        return p

    @staticmethod
    def _verify_round_trip(rel: str, model: type[BaseModel], data: bytes) -> None:
        """**落盘之前**先证明这堆字节读得回来。

        ⚠ 只 `json.load` 一下证明不了什么——它只说「是合法 JSON」。
        真正会咬人的是**模型这一侧**：某个 `None` 被 `exclude_none` 顺手抹掉之后，
        下一次 `model_validate` 会因为「必填键不在」当场失败，
        而那时产物已经落了盘、上游也已经花过钱。
        """
        try:
            raw = json.loads(data.decode('utf-8'))
            decoded = decode_artifact(rel, raw)
            model.model_validate(decoded.payload)
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError,
                ArtifactVersionError) as e:
            raise ProvenanceRefused(
                f'{rel} 序列化之后读不回 {model.__name__}，**没有落盘**：\n{e}\n'
                f'→ 这是载体层的 bug（多半是某个三态键在落盘形态里被抹掉了），不是数据问题'
            ) from e

    @staticmethod
    def _replace_atomically(p: Path, data: bytes) -> None:
        """写临时文件 → `os.replace`。**同目录**，否则 `replace` 跨卷就不是原子的了。"""
        tmp = p.with_name(f'{p.name}.{os.getpid()}.tmp')
        try:
            tmp.write_bytes(data)
            os.replace(tmp, p)
        finally:
            # `replace` 成功之后 tmp 已经不在了；只有失败路径才真的删到东西。
            tmp.unlink(missing_ok=True)
