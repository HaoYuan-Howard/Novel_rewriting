#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run.py —— 一次跑的上下文。**它存在的头号理由是杀掉进程全局 `_READ`。**

【那个全局变量做过什么】原型的来源账是一个模块级的 dict，键只有两级
（`<stage>/<文件名>`，不含书名）。于是同一个进程里**读过 A 书再写 B 书**的产物，
会落下一条**看不出来的假匹配边**——`_provenance.inputs` 里多一条指向另一本书的记录，
而它长得和真的一模一样。`R-11.3`「服务进程只读，一切写操作走子进程」
至今写着这条理由。

⇒ 本模块把来源账做成 **run 级的实例字段**：
**一本书 · 一个节点 · 一次跑 = 一个 `RunContext`**。同进程跑两本 = 两个实例 = 零串扰。
那条假匹配边**在结构上不存在**，键保持两级即可（作用域已经由实例边界给出）。

⚠⚠ **`R-11.3` 这条规则仍然留着，只是它的理由换了**（`Q-8`：退役的理由必须带标注，
否则读的人会以为那个坑还在）。改写后的三条见 `docs/BACKEND_CONTRACT.md` 的 `R-11.3`。

【它不做什么】不读盘、不写盘——那是 `novel/store.py`（`ArtifactStore(ctx)`）。
本模块只持有「这一次跑是谁、算不算钱、读过什么、记了什么账」。
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, TypeVar

from contracts.invariants import NODES, RETIRED_NODES
from contracts.schema import InputEdge, Provenance, Usage
from contracts.schema.common import FreeProvenance, LlmProvenance
from contracts.schema.orchestration import (
    AgentProfile, ExecutorBackend, LegacyExecutorBackend, LegacyLlmProfile,
)
from contracts.artifacts import STAGE0_CLEAN_TEXT
from novel import paths, prov, run_identity
from novel.errors import ProvenanceRefused
from novel.llm.cache import CachePolicy
from novel.llm.ledger import CostLedger, UsageMark, ledger_path
from novel.observability import emit_observation

P = TypeVar('P', bound=Provenance)
_USAGE_FROM_CURRENT_MARK = object()


@dataclass
class RunContext:
    """一本书 · 一个节点 · 一次跑。

    ⚠ `paid` **不是从 `NODES[node].paid` 推出来的**，它由调用方传进来。
    理由是 `R-11.7`：**贵贱只有一处算**，而那一处是 `is_paid(node, flags)`
    （`novel/orchestrator/nodes.py`，Phase D）——因为真正的贵贱是 `(node, flags)` 二元组
    决定的：`stage5.review --check-only` 是 0 成本，`stage5.review --revise` 是付费。
    在这里给一个「默认从 NODES 取」会让同一个问题有第二个答案，而两个答案必然分家。
    """

    novel_dir: Path
    node: str
    paid: bool
    code: str
    cache: CachePolicy
    ledger: CostLedger

    #: 本次付费节点的显式执行后端。新 AgentRun 路径必须非空；``None`` 只承接
    #: 尚未迁移的独立 legacy CLI。分流只看 ``kind``，不再看 profile 是否为空。
    executor_backend: ExecutorBackend | None = None

    #: 旧节点 agent 实现的只读/构造兼容投影。``__post_init__`` 会与
    #: ``executor_backend`` 归一并校验；顶层分流不得再以本格是否为空判模式。
    executor_profile: LegacyLlmProfile | AgentProfile | None = None

    #: 这一次跑的跨进程联结键。它只进运行时账/观测/恢复状态，
    #: 不进正式 provenance、cache key 或 node flags。手搭 context 也必须
    #: 自动得到非空身份，否则单测/独立 evaluator 会留下一类无法联结的新账。
    run_id: str = ''

    #: 外层 AgentRun 的稳定身份。直接 CLI/job 与 ``run_id`` 相同；v2
    #: AgentRun 内 ``run_id`` 随 execution epoch 滚动，本字段保持 parent 不变。
    parent_run_id: str = ''

    #: 结构化响应被 parse/require/schema 等可修复闸拒绝时，是否先物化给外层受托
    #: agent，而不是立即烧掉下一采。显式 CLI flag 才能开启；普通 CLI/队列保持原行为。
    repair_rejected_responses: bool = False

    #: 完成度/API 视图使用的只读上下文。只读消费者需要把 stale 产物展示出来并解释
    #: 为什么过期；真正的生产节点则必须在任何 LLM 请求之前拒绝消费 stale 上游。
    #: 这个位只由 ``state.readonly_ctx`` 设置，普通运行上下文默认一律 fail closed。
    readonly: bool = False

    #: 运行期心跳/进度的唯一出口。默认写进 stdout（编排器会把它收进作业日志）；
    #: 测试可换成 ``list.append``，不需要 mock print 或线程内部。
    observer: Callable[[dict[str, object]], None] = field(
        default=emit_observation, repr=False, compare=False)

    #: 本次跑**读过**哪些上游（`<stage>/<文件名>` → 边）。由 `store.read` 自动记账，
    #: **不由任何节点手工列举**——手工清单会烂，而且是静默地烂。
    _edges: dict[str, InputEdge] = field(default_factory=dict, repr=False)

    #: 本次跑**判过**哪几份成稿（**完整**相对路径 → 10 位指纹）。见 `_common.md` §3.2：
    #: 成稿不进 `inputs`，因为逐章成稿由 `judged` 以完整相对路径和正文摘要单独绑定。
    _judged: dict[str, str] = field(default_factory=dict, repr=False)

    #: 落盘前的确定性改写记了哪些账（`repairs` / `attempts` / `scope_violations` …）。
    #: ⚠ **记账不是终点，报出来才是**（`Q-1`）。出口那张会炸的表在 B3
    #: （`novel/report/accounting.py::ACCOUNTING`）：产地记了而表里没有 ⇒ 显式终态。
    _accounting: dict[str, Any] = field(default_factory=dict, repr=False)

    #: 上一份产物的账**结算到哪**。见 `_settled`。
    _usage_mark: UsageMark | None = field(default=None, repr=False)
    _root_clean_sha1: str | None = field(default=None, init=False, repr=False)
    _observed_roots: dict[str, str] = field(default_factory=dict, init=False, repr=False)
    #: 进程内「同 key 只生效一次」的去重账。服务 same-run deterministic sweep：
    #: 正式提交重放会重走记账/合并段，批级累计读数不能因一次有界重放翻倍。
    _once_keys: set[str] = field(default_factory=set, init=False, repr=False)

    def once(self, key: str) -> bool:
        """同一把 key 在本次 run 内只让第一次调用拿到 ``True``。

        只服务「有界重放要幂等」的记账合并：重放侧用它跳过已经并进批级读数
        的那一章，而**第一次没记到的重放仍会记上**（first-attempt 失败早于记账
        时，读数不能丢）。它不是并发原语，也不承载判定语义——拿它跳过任何
        判据、闸门或落盘都是误用。
        """
        if key in self._once_keys:
            return False
        self._once_keys.add(key)
        return True

    def __post_init__(self) -> None:
        if self.executor_profile is not None:
            old_backend = LegacyExecutorBackend(profile=self.executor_profile)
            if self.executor_backend is None:
                self.executor_backend = old_backend
            elif self.executor_backend != old_backend:
                raise ValueError(
                    'RunContext 的 executor_backend 与旧 executor_profile 不一致')
        if isinstance(self.executor_backend, LegacyExecutorBackend):
            self.executor_profile = self.executor_backend.profile
        else:
            self.executor_profile = None
        # 手搭 context 常先构造 ``CostLedger()``；未显式给 run_id 时
        # 直接沿用那本账已有的身份，不在同一次跑里再生第二个。
        self.run_id = run_identity.check_run_id(self.run_id or self.ledger.run_id)
        self.parent_run_id = run_identity.check_run_id(
            self.parent_run_id or self.ledger.parent_run_id or self.run_id)
        self.ledger.bind_run_id(self.run_id)
        self.ledger.bind_parent_run_id(self.parent_run_id)
        if self.node not in NODES and not (
                self.readonly and self.node in RETIRED_NODES):
            raise ValueError(
                f'未知节点 {self.node!r}。取值面的唯一落点是 contracts.invariants.NODES：'
                f'{sorted(NODES)}。⚠ `n1..n9` 那一套已整套退役——作业 id 是 `<stage>.<动词>`；'
                f'退役节点只允许出现在只读探测里（旧书盘面 legacy_incompatible 的读路），'
                f'生产入口一律拒识')
        self.novel_dir = Path(self.novel_dir)
        clean_path = self.novel_dir / STAGE0_CLEAN_TEXT
        if clean_path.is_file():
            try:
                self._root_clean_sha1 = prov.content_sha1(clean_path.read_bytes())
            except OSError as exc:
                raise ProvenanceRefused(
                    f'读不了全链根正文 {clean_path}，不能铸造来源账：{exc!r}') from exc
        # 在这里而不是在 `open()` 里：手搭的 `RunContext`（测试、以后的 CLI）也得有起点。
        if self._usage_mark is None:
            self._usage_mark = self.ledger.snapshot()

    # ── 身份 ──────────────────────────────────────────────────────────────

    def require_current_workflow_write(self) -> None:
        """真实写入要求当前工作流；费用与写权限是两个独立边界。"""
        if self.readonly:
            raise ProvenanceRefused('只读上下文不能写入或归档产物')
        from novel.work_units import require_workflow
        require_workflow(self.novel_dir.parent)

    @property
    def sid(self) -> str:
        """这本书的不可变 id。**从目录名解析**，与命令行怎么敲无关。"""
        return paths.derive_source_meta(self.novel_dir.name)[0]

    @property
    def title(self) -> str:
        """书名。**唯一权威来源 = 目录名**，理由见 `paths.title_of`（原型 F-118）。"""
        return paths.title_of(self.novel_dir)

    @property
    def stage(self) -> str:
        """本节点的产物层。`stage5.write` → `stage5`。"""
        return NODES[self.node].stage

    @property
    def legacy_llm_profile(self) -> LegacyLlmProfile | None:
        backend = self.executor_backend
        return backend.profile if isinstance(backend, LegacyExecutorBackend) else None

    # ── 来源账（由 store 调，节点不直接碰） ────────────────────────────────

    def record_edge(self, key: str, edge: InputEdge) -> None:
        """记一条上游边。**同一个键重复读以最后一次为准**（同一份文件，指纹一样）。"""
        self._edges[key] = edge

    def record_judged(self, rel: str, fp: str) -> None:
        self._judged[rel] = fp

    def record_artifact_root(self, rel: str, root_clean_sha1: str | None) -> None:
        if root_clean_sha1 is not None:
            self._observed_roots[rel] = root_clean_sha1

    @contextmanager
    def validation_reads(self) -> Iterator[None]:
        """隔离只用于**前置校验**的读取，不让它们冒充产物的实质输入。

        正式 producer 的 ``inputs`` / ``judged`` 只应回答“构造这份产物时实际用了
        什么”。关闭式前置闸为了证明一份上游可消费，可能继续读取那份上游自己的
        正文、字节投影与 judged 主体；这些读取决定“能不能继续”，却没有进入本节点
        的 prompt、归一或派生字段。若与 producer 共用同一账，它们会泄漏成
        ``consumed_undeclared`` 的假依赖，刚写完的正式产物随即被 freshness 后置条件
        回滚。

        这里只保存并恢复三张**来源事实**；成本、观测事件与 accounting 不在隔离面内。
        调用方若把校验期间读到的载荷带出作用域参与产物构造，就是误用——那份读取应
        回到普通 ``ArtifactStore.read`` 路，由注册表如实记账。
        """
        edges = dict(self._edges)
        judged = dict(self._judged)
        observed_roots = dict(self._observed_roots)
        try:
            yield
        finally:
            self._edges.clear()
            self._edges.update(edges)
            self._judged.clear()
            self._judged.update(judged)
            self._observed_roots.clear()
            self._observed_roots.update(observed_roots)

    @property
    def root_clean_sha1(self) -> str:
        clean_path = self.novel_dir / STAGE0_CLEAN_TEXT
        try:
            current = prov.content_sha1(clean_path.read_bytes()) if clean_path.is_file() else None
        except OSError as exc:
            raise ProvenanceRefused(
                f'读不了全链根正文 {clean_path}，不能校验 root_clean_sha1：{exc!r}') from exc
        if current is None:
            raise ProvenanceRefused(
                f'{self.node}: 缺少 stage0/clean_text.txt，不能为下游产物声明 root_clean_sha1')
        return current

    def _require_single_root(self) -> str:
        from novel.store import ArtifactStore
        root = self.root_clean_sha1
        store = ArtifactStore(self)
        mismatched = {
            rel: value for rel, value in tuple(self._observed_roots.items())
            if value != root and not store.equivalent_body_root(rel, value)
        }
        if mismatched:
            raise ProvenanceRefused(
                f'{self.node}: 读到了不同 root_clean_sha1 的上游：当前根={root}，'
                f'不匹配={mismatched}。拒绝把混根输入铸成一份新产物')
        return root

    def require_observed_roots_current(self) -> None:
        """LLM 请求前复核所有已读 provenance-bearing 输入仍属于当前根。

        没有观察到来源根时不强造一条：响应/预算单元测试和独立 evaluator 可以没有
        stage0 根；普通无 provenance 的 Stage1 产物则由 ``ArtifactStore.read`` 的
        deterministic freshness bridge 负责。只要任一 typed 产物暴露了根，本闸就
        必须在缓存、预算、账本和 transport 之前响。
        """
        if self._observed_roots:
            self._require_single_root()

    @property
    def edges(self) -> dict[str, InputEdge]:
        """本次跑的来源账**快照**。返回拷贝——外面改它不该影响这一次跑。"""
        return dict(self._edges)

    @property
    def judged(self) -> dict[str, str]:
        return dict(self._judged)

    # ── 运行期观测 ────────────────────────────────────────────────────────

    def observe(self, event: str, **fields: object) -> None:
        "发一条结构化运行事件；身份字段只在这里补一次。"
        payload: dict[str, object] = {
            'event': event,
            'run_id': self.run_id,
            'sid': self.sid,
            'node': self.node,
            **fields,
        }
        self.observer(payload)

    # ── 记账 ──────────────────────────────────────────────────────────────

    def account(self, key: str, value: Any) -> None:
        """记一笔「落盘前我改写了什么」。

        ⚠ 判准是**那个位置本来有没有东西**（`_common.md` §九）：
        有，才叫改写（原值被销毁 ⇒ 记账）；没有，那叫**初始化**，
        记一份「本来是空的」只会制造恒定的噪声——stage4 的 `op_forced` 就是这个下场
        （17/17 全是 null，最后连字段一起删了）。
        """
        self._accounting[key] = value

    @property
    def accounting(self) -> dict[str, Any]:
        return dict(self._accounting)

    # ── 来源账的两个构造口 ────────────────────────────────────────────────

    def llm_provenance(self, cls: type[P] = LlmProvenance, /, *,
                       prompts: dict[str, str], model: str,
                       adopted_models: Iterable[str] | None = None,
                       settle: bool = True,
                       usage_override: Usage | None | object =
                       _USAGE_FROM_CURRENT_MARK,
                       **extra: Any) -> P:
        """付费产出的来源账。`inputs` **不接受参数**——它只能来自 `_edges`。

        `prompts` 收的是**插值之后的 system prompt 原文**，这里替它算指纹。
        ⚠ **存渲染结果不存模板**：一个模板 + 一份配方 = 一个 prompt，
        **配方变了而模板没变，发出去的东西就变了**——stage6 已经证明过这条
        （`HOOK_RECIPE` 一改，那份 SYS 的指纹必须跟着变，
        否则「这份候选是拿哪套写法产的」永远说不清）。
        ⚠ 判据表自身的指纹**统一叫 `RULES`**（原型有 `RULES`/`RULES7`/`RULES8` 三个名字，
        而它们是同一件事——`Q-6`：同一个语义必须叫同一个名字）。

        ⚠⚠ **「0 成本不许记 `model`」这道闸开在这里，不开在写盘口**（`_common.md` §1.2）。
        这是本轮实测挪的一处：写盘口拦不住它，因为若干 0 成本入口本来就要写回一份
        带 `model` 的来源账——`--revalidate` / `--recheck` 改写的是
        **付费节点产的**那份产物，它记的 `model` 属于当初那次付费产出，
        不是本次跑的新宣称；`stage6.approve` / `stage7.approve` 同理（0 成本节点，
        改的是付费产物）。⇒ 真正该拦的是「**0 成本的跑现造一份声称模型产了它的账**」，
        而**现造只有这一个入口**。放在写盘口会误伤五条真路径，那种闸迟早被人加白名单绕开。
        """
        if not self.paid:
            raise ProvenanceRefused(
                f'{self.node} 这一次跑不花钱（paid=False），却在现造一份带 `model` 的来源账。'
                f'记一个 `model` 上去就是宣称「某个模型产了它」，而这一次跑一个模型都没调。'
                f'⇒ 0 成本产出走 `free_provenance()`；'
                f'要写回一份**继承来的**来源账（`--revalidate`/`--recheck`/定稿节点…），'
                f'走 `store.promote_invalid()` 或直接把读上来的那一份原样带下去，别现造')
        actual_models = sorted(set(adopted_models or ()))
        if not actual_models:
            raise ProvenanceRefused(
                f'{self.node}: 已有付费产出要铸来源账，但没有 accepted model；'
                '拒绝拿主模型配置冒充实际响应来源')
        if (usage_override is not _USAGE_FROM_CURRENT_MARK
                and usage_override is not None
                and not isinstance(usage_override, Usage)):
            raise TypeError('usage_override 必须是 Usage、None 或省略')
        # durable 多单元 producer 会跨 CLI 恢复；这时当前 RunContext 的 mark
        # 天然晚于先前已经付费并 checkpoint 的 attempts。调用方只有在已用
        # checkpoint + ledger 关闭式证明完整 attempt 集时，才可把那份确定性
        # 聚合结果送进来。无论哪支，settle 仍推进当前 mark，避免本进程新增调用
        # 又被下一份产物重复认领。
        usage = (
            self.ledger.usage_since(self._usage_mark)
            if usage_override is _USAGE_FROM_CURRENT_MARK
            else usage_override
        )
        out = cls(code=self.code, root_clean_sha1=self._require_single_root(), inputs=self.edges,
                  prompts={k: prov.fingerprint(v) for k, v in prompts.items()},
                  model=model, adopted_models=actual_models,
                  usage=usage, **extra)
        # `settle=False` 只供**尚未决定是否落盘**的候选做同 schema 校验：若它最终被
        # HARD 拦成 `.INVALID`，调用方必须再以默认 `settle=True` 铸造落盘稿；若它继续
        # 进入下一次付费调用，最终稿才能一次认领完整窗口。预览时推进刻度会让正式稿
        # 只剩“预览之后”的尾调用，Σ（每份）与 run 总量分家。
        return self._settled(out) if settle else out

    def llm_provenance_for_chapter(
            self, chapter: int, cls: type[P] = LlmProvenance, /, *,
            prompts: dict[str, str], model: str,
            adopted_models: Iterable[str] | None = None,
            resume_claim: bool = False, **extra: Any) -> P:
        if not self.paid:
            raise ProvenanceRefused(
                f'{self.node} is paid=False and cannot mint model provenance')
        actual_models = sorted(set(adopted_models or ()))
        if not actual_models:
            raise ProvenanceRefused(
                f'{self.node}: 第 {chapter} 章已有付费产出，但没有 accepted model；'
                '拒绝拿主模型配置冒充实际响应来源')
        out = cls(
            code=self.code,
            root_clean_sha1=self._require_single_root(),
            inputs=self.edges,
            prompts={k: prov.fingerprint(v) for k, v in prompts.items()},
            model=model,
            adopted_models=actual_models,
            usage=self.ledger.chapter_usage(chapter),
            **extra,
        )
        self.ledger.commit_chapter(chapter, resume=resume_claim)
        return out

    def free_provenance(self, cls: type[P] = FreeProvenance, /, *,
                        prompts: dict[str, str] | None = None, **extra: Any) -> P:
        """0 成本产出的来源账。**没有 `model` 这个字段**（结构上不存在，不是「可以是 null」）。

        ⚠ 理由是**假账**：`--apply-edit` 落的是人手写的稿、`review_<n>.json` 是 0 成本
        算出来的，两者都**没有任何模型参与**。记一个 `model` 上去就是宣称
        「某个模型产了它」，而事后没有任何办法把它与真由模型产的那些分开。

        `prompts` 在这里通常只有一项 `RULES`——「这份报告是拿哪一版尺子量的」。
        判据表改了而报告没重跑，这个指纹是唯一说得出来的地方。
        """
        out = cls(code=self.code, root_clean_sha1=self._require_single_root(), inputs=self.edges,
                  prompts=({k: prov.fingerprint(v) for k, v in prompts.items()}
                           if prompts else None),
                  usage=self.ledger.usage_since(self._usage_mark), **extra)
        return self._settled(out)

    def recovered_llm_provenance(
            self, cls: type[P] = LlmProvenance, /, *,
            prompts: dict[str, str], model: str,
            adopted_models: Iterable[str], **extra: Any) -> P:
        """**第四个口，也是唯一一个「0 成本的跑写一份带 `model` 的账」**。

        它服务已付费且带不可变证据的 Stage5 prepared checkpoint 恢复：
        原 run 的完整回答由 --recover-prepared 在当前代码下重判收口。
        产物确实由某个模型产出，所以 `model` 必须记；而**这一次跑没有调用任何模型**，
        所以它过不了 `llm_provenance` 的 `paid` 闸，也不该走 `free_provenance`
        （那一口结构上没有 `model`，记不下「谁产的」）。

        调用方必须交出 typed prepared_checkpoint_adoption；旧 Stage4 整书拒稿
        采用证据已经退役，不能用它构造当前来源账。

        【`usage` 为什么是本次窗口而不是历史那次的数】**成本的唯一权威源是账本**
        （`<base>/_service/ledger.jsonl`），产物 `usage` 不与它混加。把历史那一次的
        token 抄进这份产物，等于让同一批 token 被两处各声称一次。这一次跑真的是
        0 次调用，就如实记 0；那次付费仍然在账本里，并由 `attempt_id` 精确联结。
        """
        actual_models = sorted(set(adopted_models))
        if not actual_models:
            raise ProvenanceRefused(
                f'{self.node}: 0 wire 模型产物恢复必须交出当初实际产出响应的模型')
        if (extra.get('prepared_checkpoint_adoption') is None
                or 'rejected_response_adoption' in extra):
            raise ProvenanceRefused(
                f'{self.node}: 0 wire 模型产物恢复必须交出当前 typed prepared adoption 证据')
        out = cls(code=self.code, root_clean_sha1=self._require_single_root(),
                  inputs=self.edges,
                  prompts={k: prov.fingerprint(v) for k, v in prompts.items()},
                  model=model, adopted_models=actual_models,
                  usage=self.ledger.usage_since(self._usage_mark), **extra)
        return self._settled(out)

    def rewrite_provenance(self, prev: P, *, by: str) -> P:
        """**第三个口，也是最窄的一个**：一条 0 成本路径只改了这份产物的一部分。

        `--recheck` / `--renormalize`，以及只改一部分决策集的
        `--apply-edit`，改完之后那一份是**混版的**：
        LLM 写的那些字段出自当初那次付费产出，被改的那部分是**现在**从盘上重算的。
        ⇒ 继承 `prompts` / `model` / `adopted_models` 一个字不动，
        换掉 `inputs`（本次真读到的），
        标一个 `partial_rewrite` 让混版这件事看得见（`_common.md` §5.1b）。

        ⚠⚠ **它没有 `prompts` / `model` / `adopted_models` / `inputs` 四个参数**
        ——与 `promoted()` 同一条判准：
        本次一个请求都没发过，记上它们就是假账。`inputs` 只能来自 `_edges`
        （与另外两个铸造口一致：**手工列举的来源清单会烂，而且是静默地烂**）。

        ⚠ 它**不走 `_settled()`**：本次一分钱没花，`usage` 该原样留着当初那次的
        ——把一份 `calls_made=0` 的账盖上去，「这份产物花了多少」就永远说不出来了。
        """
        current = self._require_single_root()
        if prev.root_clean_sha1 != current:
            from novel.source_identity import body_inputs_only, same_body_source
            from novel.store import ArtifactStore
            if not (body_inputs_only(self.node, self.edges) and same_body_source(
                    ArtifactStore(self), prev.root_clean_sha1, node=self.node, inputs=prev.inputs)):
                raise ProvenanceRefused(
                    f'{self.node}: 旧稿 root_clean_sha1={prev.root_clean_sha1}，当前根={current}；'
                    '局部改写不能把旧根内容伪装成新根产物，必须从产地重建')
            # 正文逐字相同的已审源修订只证明可继续消费；原模型根仍保留。
        return prev.partially_rewritten(by=by, code=self.code, inputs=self.edges)

    def _settled(self, out: P) -> P:
        """把成本刻度推到这一份产物之后。**两个铸造口的最后一步都是它。**

        ⚠⚠ `usage` 原来直接写 `self.ledger.usage()`，而那是 **run 累计**。
        `_common.md` §八写得很清楚，两份账答的是两个问题：账本答「这周/这本书花了多少」，
        产物里那一份答「**这一份**花了多少」——那是看**返工回路边际成本**时会问的。
        一次跑产 N 份产物（stage5 逐章正是这个形状）时，累计值让第 2 份带着第 1 份的账、
        第 3 份带着前两份的，一路加上去。边际成本从此读不出来，而账面看着都对。

        ⚠ **结算点是「铸造来源账」，不是「落盘」。** 铸造口只有这两个
        （`llm_provenance` / `free_provenance`），一份产物 = 一次铸造 = 一次结算。
        挪到写盘口有两个坏处：① 「先铸 N 份再逐份落盘」时每份会认领同一批 token；
        ② 要给 `promote_invalid` / `write_invalid` 开例外，而 `store.py` 那边刚论证过
        「需要白名单的闸迟早被绕开」。

        ⚠ **推刻度必须在构造成功之后。** 来源账自己带后置条件
        （`StyleProvenance.attempts` 非空之类），构造是会抛的；抛了还把刻度推走，
        那段窗口的账就再也没有产物认领它——Σ（每份）== run 总量 当场破。
        """
        self._usage_mark = self.ledger.commit(self._usage_mark)
        return out

    # ── 构造 ──────────────────────────────────────────────────────────────

    @classmethod
    def open(cls, base: str | os.PathLike[str], sid: str, node: str, *,
             paid: bool, cache: CachePolicy | None = None,
             ledger: CostLedger | None = None, write_ledger: bool = True,
             repair_rejected_responses: bool = False,
             executor_backend: ExecutorBackend | None = None,
             executor_profile: LegacyLlmProfile | None = None,
             run_id: str | None = None,
             parent_run_id: str | None = None) -> RunContext:
        """按 `<Output>` + 书 id 开一次跑。**找不到书目录 ⇒ 显式终态**（`UpstreamMissing`）。

        `write_ledger=False` 时账本只在内存里攒（`--dry-run` / 测试）——
        ⚠ 那不是「不记账」，`usage` 照样产出；它只是不往盘上追加行。
        """
        novel_dir = paths.resolve_novel_dir(base, sid)
        if paid:
            from novel.work_units import require_workflow
            require_workflow(base)
        resolved_run_id = (
            run_identity.check_run_id(ledger.run_id)
            if run_id is None and ledger is not None
            else run_identity.resolve_run_id(run_id)
        )
        resolved_parent_run_id = (
            run_identity.check_run_id(ledger.parent_run_id)
            if parent_run_id is None and ledger is not None
            else run_identity.check_run_id(parent_run_id or resolved_run_id)
        )
        return cls(
            novel_dir=novel_dir, node=node, paid=paid,
            code=prov.code_version(),
            cache=cache or CachePolicy.default(),
            ledger=ledger or CostLedger(
                path=ledger_path(base) if write_ledger else None,
                run_id=resolved_run_id,
                parent_run_id=resolved_parent_run_id),
            run_id=resolved_run_id,
            parent_run_id=resolved_parent_run_id,
            executor_backend=executor_backend,
            executor_profile=executor_profile,
            repair_rejected_responses=repair_rejected_responses,
        )
