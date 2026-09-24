"""str06_run_context —— STR-06 候选链的运行上下文：身份、来源账与 provenance 铸造（F7a-2）。

【为什么在 llm 层而不在迁移包】候选上下文要铸 provenance（`novel.prov`）并持有
正式 `CostLedger`；`novel/stages/str06/` 的迁移包纪律（test_str06_invariants）
禁止 import 现役设施，F8 分流时本模块职责并回正式 `RunContext`。放在 llm 层与
`str06_requests` 相邻，依赖方向与既有 `aggregate_runs` 一致。

【与正式 RunContext 的分工】
正式 ctx 绑**书**（stage0/clean_text 根、NODES 节点、ARTIFACT_SPECS 产物）；
候选 ctx 绑**候选根**（源输入身份、候选节点表、候选产物表）。两者共用同一套
canonical schema（`contracts.schema.common` 的 Provenance／InputEdge／Usage）
与同一个 CostLedger——不新建领域模型（I-11），不建第二份成本账（I-17）。

【provenance 铸造口与正式 ctx 同构同闸】
  · `free_provenance`：0 成本产物，结构上没有 `model`（假账防线与正式一致）；
  · `llm_provenance`：付费产物，只接受**实际已采用响应**的模型集合，拒绝拿主
    模型冒充来源；`usage` 取 ledger 窗口差值并推刻度（每份产物认领自己的边际
    窗口，Σ（每份）== run 总量）。

【采用窗口（F7a 的已知限制，F7b 收口）】
`note_adoption` 由付费边界在每次已购响应（新购或缓存命中）后登记；写盘时按
`owners` 过滤消费。同一份产物的多 key（如 Preserve 的 checkpoint 与 brief）
第二次写盘继承上次的生成事实、usage 窗口归零——与正式链「一次调用产多份
产物」的形状一致。窗口内没有采用证据而节点又是付费节点时**关闭式拒绝**，
不伪造来源。F7b 拆 owner runner 后，每次写盘紧贴其 producer 调用，窗口即精确。

【inputs 只记真读到的】
`record_edge` 在 repository 读盘时登记（键 = `family_input_key` 完整路径）。
单进程 harness 在内存里传递对象不产生边——F7b 起各 owner 经 repository
读写，inputs 随之闭合；现在缺边是诚实的空，不是假账。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, NamedTuple
from uuid import uuid4

from contracts.schema.common import FreeProvenance, InputEdge, LlmProvenance
from contracts.schema.str06.candidates import (
    STR06_CANDIDATE_NODES,
    CandidateEpoch,
    CandidateNode,
    CandidateWorkflow,
    check_candidate_workflow,
    open_candidate_epoch,
    open_candidate_workflow,
    roll_candidate_epoch,
)
from novel.errors import ProvenanceRefused
from novel.llm.ledger import CostLedger, ledger_path
from novel import prov

_SHA1_RE = re.compile(r'^[0-9a-f]{40}$')

#: 候选根 workflow 身份记录的唯一落点（runtime 家族，非版本化书产物）。
WORKFLOW_REL = 'runtime/candidate_workflow.json'


class Adoption(NamedTuple):
    """一次已购响应的采用登记（新购与缓存命中都算采用）。"""

    owner: str
    model: str
    prompts: dict[str, str]          # 渲染后 prompt 原文的指纹（键 = 语义名）
    turn_id: str = ''                # 新购的内容轮；缓存采用为空
    unit: Any = None                 # 该轮所属 WorkUnitBudget；缓存采用为 None


class CandidateRunContext:
    """候选链一次运行的上下文：节点身份、来源账、provenance 铸造与采用台账。"""

    def __init__(self, *, root: Path, node: str, epoch: CandidateEpoch, code: str,
                 ledger: CostLedger, root_sha1: str, configured_model: str = '',
                 readonly: bool = False):
        if not _SHA1_RE.match(root_sha1):
            raise ProvenanceRefused('候选根身份必须是源输入字节的 SHA-1（40 位十六进制）')
        if not code:
            raise ProvenanceRefused('候选上下文必须绑定代码身份')
        self.root = Path(root).resolve()
        self.epoch = epoch
        self.code = code
        self.ledger = ledger
        self.root_sha1 = root_sha1
        self.configured_model = configured_model
        self.readonly = readonly
        self._node = self._validated_node(node)
        self._edges: dict[str, InputEdge] = {}
        self._last_inputs: dict[str, InputEdge] = {}
        self._adoptions: list[Adoption] = []
        self._last_generation: dict[str, tuple[dict[str, str], list[str]]] = {}
        self._usage_mark = ledger.snapshot()

    # ── 身份 ──────────────────────────────────────────────────────────────

    def _validated_node(self, node_id: str) -> CandidateNode:
        spec = STR06_CANDIDATE_NODES.get(node_id)
        if spec is None:
            raise ProvenanceRefused(f'未登记的候选节点：{node_id}')
        return spec

    @property
    def node(self) -> str:
        return self._node.node

    @property
    def node_spec(self) -> CandidateNode:
        return self._node

    def enter_node(self, node_id: str) -> None:
        """切换当前执行节点（runner 按 owner 推进时调用）。"""
        self._node = self._validated_node(node_id)

    # ── 来源账 ─────────────────────────────────────────────────────────────

    def record_edge(self, rel: str, file_fp: str) -> None:
        """登记一条输入边：键 = 完整相对路径（家族内不塌两级）。"""
        self._edges[rel] = InputEdge(file=file_fp, consumed=None)

    @property
    def edges(self) -> dict[str, InputEdge]:
        return dict(self._edges)

    def _take_inputs(self, rel: str) -> dict[str, InputEdge]:
        """取用本次写盘的输入边：距上次铸账的窗口；窗口空则继承上次（同一份
        付费产出的第二个 key 与正式链「一次调用产多份产物」同形）。

        自边剔除：产物不能把自己记成自己的上游（读旧稿再重写的账归 F7c 的
        revalidation 面）。断言／视图走 `repository.inspect`，不记边——
        单进程 pipeline 里「谁读的」必须能区分生产消费与检查。
        """
        if self._edges:
            inputs = {key: edge for key, edge in self._edges.items() if key != rel}
            self._last_inputs = inputs
            self._edges = {}
            return dict(inputs)
        return {key: edge for key, edge in self._last_inputs.items() if key != rel}

    # ── 采用台账 ───────────────────────────────────────────────────────────

    def note_adoption(self, *, owner: str, model: str, prompts: dict[str, str],
                      turn_id: str = '', unit: Any = None) -> None:
        """登记一次已购响应的采用；`prompts` 是渲染后的 prompt 原文。"""
        self._adoptions.append(Adoption(
            owner=owner, model=model,
            prompts={k: prov.fingerprint(v) for k, v in prompts.items()},
            turn_id=turn_id, unit=unit))

    @property
    def pending_adoptions(self) -> tuple[Adoption, ...]:
        return tuple(self._adoptions)

    # ── provenance 铸造 ────────────────────────────────────────────────────

    def provenance_for(self, writer_node: str, rel: str, *,
                       owners: tuple[str, ...] | None = None):
        """按 writer 节点的贵贱铸来源账：付费节点 LlmProvenance，免费节点 FreeProvenance。"""
        spec = self._validated_node(writer_node)
        if spec.paid:
            return self.llm_provenance(rel, owners=owners)
        return self.free_provenance(rel)

    def free_provenance(self, rel: str) -> FreeProvenance:
        out = FreeProvenance(
            code=self.code, root_clean_sha1=self.root_sha1,
            inputs=self._take_inputs(rel), prompts=None,
            usage=self.ledger.usage_since(self._usage_mark))
        self._usage_mark = self.ledger.commit(self._usage_mark)
        return out

    def llm_provenance(self, rel: str, *,
                       owners: tuple[str, ...] | None = None) -> LlmProvenance:
        node = self.node
        window = [a for a in self._adoptions
                  if owners is None or a.owner in owners]
        if window:
            prompts: dict[str, str] = {}
            for adoption in window:
                for key, value in adoption.prompts.items():
                    prompts[f'{adoption.owner}.{key}'] = value
            models = sorted({a.model for a in window if a.model})
            if not models:
                raise ProvenanceRefused(
                    f'{node} 已有付费产出要铸来源账，但没有 accepted model；'
                    '拒绝拿主模型配置冒充实际响应来源')
            self._last_generation[node] = (prompts, models)
            self._adoptions = [a for a in self._adoptions if a not in window]
        elif node in self._last_generation:
            # 同一份付费产出的第二个 key：继承生成事实，usage 窗口归零。
            prompts, models = self._last_generation[node]
        else:
            raise ProvenanceRefused(
                f'{node} 是付费节点，但写盘窗口内没有已采用响应；'
                '拒绝现造一份声称模型产了它的账')
        out = LlmProvenance(
            code=self.code, root_clean_sha1=self.root_sha1,
            inputs=self._take_inputs(rel),
            prompts=prompts, model=self.configured_model or models[0],
            adopted_models=models,
            usage=self.ledger.usage_since(self._usage_mark))
        self._usage_mark = self.ledger.commit(self._usage_mark)
        for adoption in window:
            # 只把仍处于 delivered 的生产轮标记采纳；已被 _close_unadopted 关闭的
            # 轮（重试／崩溃重做）保持 failed——拒绝证据不因后续写盘被翻面。
            if adoption.unit is not None and adoption.turn_id:
                if adoption.unit.turn(adoption.turn_id).state == 'delivered':
                    adoption.unit.mark_adopted(adoption.turn_id)
        return out


# ─── 候选根 workflow 身份：初始化、复用与漂移滚动 ──────────────────────────────

def workflow_to_disk(workflow: CandidateWorkflow) -> dict:
    return {'epoch': dict(workflow.epoch._asdict()),
            'contract_identity': workflow.contract_identity,
            'source_identity': workflow.source_identity,
            'created_at': workflow.created_at}


def workflow_from_disk(data: Any) -> CandidateWorkflow:
    if not isinstance(data, dict) or 'epoch' not in data:
        raise ProvenanceRefused('候选 workflow 记录形状损坏')
    return CandidateWorkflow(epoch=CandidateEpoch(**data['epoch']),
                             contract_identity=data['contract_identity'],
                             source_identity=data['source_identity'],
                             created_at=data['created_at'])


def init_or_resume_candidate_workflow(
        *, repository, base, code: str, root_sha1: str, source_identity: str,
        task_id: str, configured_model: str = '', created_at: int,
        readonly: bool = False) -> tuple[CandidateWorkflow, CandidateRunContext]:
    """候选根的 workflow 身份生命周期（F7a-2；F7a-3 的历史采用建立在同一记录上）。

    · 无记录：首开 epoch（`initial`）并落盘记录；
    · 记录有效且代码未漂移：**原 epoch 原样复用**——身份未漂移不开新 epoch，
      累计的整书额度与工作单元账跨调用连续（Q-10）；
    · 代码漂移：按 `code_drift` 滚动 epoch（parent 会话不变），重写记录；
    · 契约表漂移或源输入身份变化：**关闭式拒绝**——旧根的已购采用结论不覆盖
      新输入，也不能拿旧表身份的采用冒充新表（F7a-3 的阻断条件）。

    ledger 以 epoch 的 `session_run_id` 为 parent：每次 CLI 调用是新 run，
    epoch 是稳定窗口，与正式 AgentRun 的 run／parent 分工同构。
    """
    try:
        record = workflow_from_disk(repository.read(WORKFLOW_REL))
    except FileNotFoundError:
        record = None
    if record is None:
        epoch = open_candidate_epoch(
            code_version=code, task_id=task_id,
            session_run_id=uuid4().hex, created_at=created_at)
        workflow = open_candidate_workflow(
            epoch=epoch, source_identity=source_identity, created_at=created_at)
        repository.write(WORKFLOW_REL, workflow_to_disk(workflow))
    else:
        problems = check_candidate_workflow(record, code_version=code)
        if record.source_identity != source_identity:
            problems.append(
                '源输入身份与根初始化时不一致：旧根的已购采用不覆盖新输入，'
                '请用独立候选根重建，不原地升级')
        fatal = [p for p in problems if '代码身份漂移' not in p]
        if fatal:
            raise ProvenanceRefused(
                '候选根 workflow 身份复校失败：' + '；'.join(fatal))
        if any('代码身份漂移' in p for p in problems):
            epoch = roll_candidate_epoch(
                record.epoch, reason='code_drift', code_version=code,
                task_id=record.epoch.task_id, session_run_id=uuid4().hex,
                created_at=created_at)
            workflow = record._replace(epoch=epoch)
            repository.write(WORKFLOW_REL, workflow_to_disk(workflow))
        else:
            workflow = record
    ledger = CostLedger(path=ledger_path(Path(base)),
                        parent_run_id=workflow.epoch.session_run_id)
    context = CandidateRunContext(
        root=repository.root, node='str06.stage2.source', epoch=workflow.epoch,
        code=code, ledger=ledger, root_sha1=root_sha1,
        configured_model=configured_model, readonly=readonly)
    repository.attach_context(context)
    return workflow, context
