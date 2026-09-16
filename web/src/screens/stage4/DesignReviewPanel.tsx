import { errorBody } from '@/api/errors'
import { useApprovals } from '@/hooks/useApprovals'
import type {
  BindingInfeasibilityWitness,
  DesignChapterRepairCapability,
  DesignReviewHistoryEntry,
  DesignReviewRepairCapability,
  DesignReviewView,
} from '@/types/approvals'
import { ErrorHint } from '@/components/state/States'
import { Mono } from '@/components/ui/Mono'
import { Panel } from '@/components/ui/Panel'
import { GateMark } from '@/components/vocab/Badges'

function stateLabel(state: string): string {
  switch (state) {
    case 'unavailable': return '当前没有可审阅候选'
    case 'missing': return '当前候选尚未审阅'
    case 'repair_requested': return '历史候选曾要求逐章返修'
    case 'strategy_replan_requested': return '历史候选曾要求整书重规划'
    case 'accepted': return '历史候选曾被正式接受'
    case 'expired': return '历史审阅已过期'
    default: return '服务端状态 ' + state
  }
}

/**
 * Stage4A 设计审阅的历史只读投影。
 *
 * 四道人工闸已随 2026-09-15 全自动裁决退役：workpack、关闭式模板与
 * accept/request_repair/request_replan 写面全部删除，推进只认统一机械准入。
 * 这里不再有任何表单、token 提交或 workpack 轮询，只照画服务端的历史账。
 */
export function DesignReviewPanel() {
  const approvals = useApprovals()

  if (approvals.isLoading) {
    return <Panel className="mx-30 mt-20 mb-4 px-20 py-16 text-body text-ink-4">读取 Stage4A 审阅记录…</Panel>
  }
  if (approvals.isError) {
    return (
      <Panel className="mx-30 mt-20 mb-4 border-warn-line bg-warn-bg px-20 py-16 text-body text-warn-ink">
        Stage4A 审阅记录读取失败：{errorBody(approvals.error)}
        <ErrorHint error={approvals.error} />
      </Panel>
    )
  }
  const doc = approvals.doc
  if (!doc) return null

  const view = doc.design_review
  return (
    <Panel className="mx-30 mt-20 mb-4 overflow-hidden">
      <div className="flex items-start gap-12 px-20 py-16">
        <GateMark ok={view.state === 'accepted'} />
        <div className="flex min-w-0 flex-1 flex-col gap-5">
          <div className="flex flex-wrap items-baseline gap-10">
            <span className="text-read font-semibold">Stage4A 设计审阅 · 历史只读</span>
            <Mono className="rounded-tag bg-inset px-7 py-2 text-micro">{view.state}</Mono>
            <span className="text-tag text-ink-4">{stateLabel(view.state)}</span>
          </div>
          <span className="prose-cn text-sm2 text-ink-4">
            Stage4 人工审阅已退役：设计候选由机械准入放行，B 的 typed infeasible 终态
            触发预算内自动重规划。本面板只呈现服务端保留的历史审阅账，不提供任何决定入口。
          </span>
          <div className="grid gap-5 text-tag text-ink-4 md:grid-cols-2">
            <span>
              当前候选 SHA-256　
              <Mono className="break-all text-mini text-ink-2">{view.candidate_sha256 ?? '（没有）'}</Mono>
            </span>
            <span>
              聚合审阅主体 SHA-256　
              <Mono className="break-all text-mini text-ink-2">{view.aggregate_subject_sha256 ?? '（没有）'}</Mono>
            </span>
          </div>
          <span className="text-tag text-ink-4">历史正式审阅 {view.history.length} 次</span>
          {view.history.length ? <HistoryTable history={view.history} /> : null}
        </div>
      </div>

      {view.current_decision ? <CurrentDecisionSummary view={view} /> : null}
      {view.binding_infeasibility ? (
        <BindingInfeasibilityEvidence evidence={view.binding_infeasibility} />
      ) : null}
      {view.repair_capability ? <RepairCapabilitySummary capability={view.repair_capability} /> : null}
    </Panel>
  )
}

function HistoryTable({ history }: { history: DesignReviewHistoryEntry[] }) {
  return (
    <details className="rounded-btn border border-line-row bg-card px-10 py-8">
      <summary className="cursor-pointer text-sm2 font-semibold text-ink-2">历史审阅摘要</summary>
      <div className="flex flex-col divide-y divide-line-row pt-6">
        {history.map((entry, index) => (
          <div key={entry.decision_sha256 + ':' + index} className="flex flex-col gap-3 py-6 text-sm2">
            <div className="flex flex-wrap items-center gap-6">
              <Mono className="rounded-tag bg-inset px-6 py-1 text-micro">
                {entry.strategy_decision}
              </Mono>
              <span className="text-tag text-ink-4">{entry.actor}</span>
              <Mono className="text-micro text-ink-4">{entry.decided_at}</Mono>
            </div>
            <span className="prose-cn text-ink-2">{entry.general_reason}</span>
            <span className="text-micro text-ink-4">
              逐章：{Object.entries(entry.chapter_decisions)
                .map(([ordinal, decision]) => '第 ' + ordinal + ' 章 ' + decision)
                .join('、') || '（无）'}
            </span>
          </div>
        ))}
      </div>
    </details>
  )
}

function CurrentDecisionSummary({ view }: { view: DesignReviewView }) {
  const decision = view.current_decision!
  return (
    <div className="flex flex-col gap-5 border-t border-line-row bg-sunken-2 px-20 py-14">
      <div className="flex flex-wrap items-center gap-8">
        <span className="text-body font-semibold">最近一次历史决定</span>
        <Mono className="rounded-tag bg-inset px-7 py-2 text-mini">{decision.strategy.decision}</Mono>
        <Mono className="break-all text-micro text-ink-4">{decision.decision_sha256}</Mono>
        <span className="ml-auto text-tag text-ink-4">{decision.decided_at}</span>
      </div>
      <span className="prose-cn text-sm2 text-ink-2">{decision.strategy.reason}</span>
      <div className="flex flex-wrap gap-4">
        {decision.chapters.map((chapter) => (
          <Mono key={chapter.output_ordinal} className="rounded-tag bg-card px-7 py-2 text-mini text-ink-3">
            第 {chapter.output_ordinal} 章 · {chapter.decision}
          </Mono>
        ))}
      </div>
    </div>
  )
}

function capabilityLabel(value: boolean | null | undefined): string {
  return value === null || value === undefined ? '（无）' : value ? '可用' : '不可用'
}

function RepairCapabilitySummary({ capability }: {
  capability: DesignReviewRepairCapability
}) {
  const legacy = capability.repairable !== null && capability.repairable !== undefined
  const current = capability.agent_repairable !== null && capability.agent_repairable !== undefined
  return (
    <div className="flex flex-col gap-5 border-t border-line-row bg-sunken-2 px-20 py-14">
      <div className="flex flex-wrap items-center gap-7">
        <span className="text-body font-semibold">候选恢复能力（服务端结论，只读）</span>
        {legacy ? (
          <Mono className={
            'rounded-tag px-7 py-2 text-mini '
            + (capability.repairable ? 'bg-gate-ok-bg text-gate-ok-ink' : 'bg-warn-bg text-warn-ink')
          }>
            legacy repairable：{capability.repairable ? 'repairable' : 'not_repairable'}
          </Mono>
        ) : null}
        {legacy && capability.reason_code ? (
          <span className="text-tag text-ink-4">reason_code　<Mono className="text-mini text-ink-2">{capability.reason_code}</Mono></span>
        ) : null}
      </div>
      {current ? (
        <div className="grid gap-5 text-tag text-ink-4 md:grid-cols-2">
          <span>
            agent 定向返修　
            <Mono className="text-mini text-ink-2">{capabilityLabel(capability.agent_repairable)}</Mono>
            {capability.agent_reason_code ? ' · ' + capability.agent_reason_code : ''}
          </span>
          <span>
            完整章替换　
            <Mono className="text-mini text-ink-2">{capabilityLabel(capability.full_chapter_editable)}</Mono>
            {capability.full_chapter_edit_reason_code ? ' · ' + capability.full_chapter_edit_reason_code : ''}
          </span>
        </div>
      ) : null}
      <details className="rounded-btn border border-line-row bg-card px-10 py-8">
        <summary className="cursor-pointer text-sm2 font-semibold text-ink-2">逐章能力</summary>
        <div className="flex flex-col divide-y divide-line-row pt-4">
          {capability.chapters.map((row) => (
            <ChapterCapabilityRow key={row.output_ordinal} row={row} />
          ))}
        </div>
      </details>
    </div>
  )
}

function ChapterCapabilityRow({ row }: { row: DesignChapterRepairCapability }) {
  return (
    <div className="flex flex-wrap items-center gap-8 py-5 text-sm2 text-ink-2">
      <Mono className="rounded-tag bg-inset px-6 py-1 text-micro">第 {row.output_ordinal} 章</Mono>
      <span className="text-micro text-ink-4">committed_rounds <Mono>{row.committed_rounds}</Mono></span>
      {row.request_repair_available !== null && row.request_repair_available !== undefined ? (
        <span className="text-micro text-ink-4">request_repair：{capabilityLabel(row.request_repair_available)}</span>
      ) : null}
      {row.agent_repair_available !== null && row.agent_repair_available !== undefined ? (
        <span className="text-micro text-ink-4">agent 返修：{capabilityLabel(row.agent_repair_available)}</span>
      ) : null}
      {row.full_chapter_edit_available !== null && row.full_chapter_edit_available !== undefined ? (
        <span className="text-micro text-ink-4">完整章替换：{capabilityLabel(row.full_chapter_edit_available)}</span>
      ) : null}
    </div>
  )
}

function BindingInfeasibilityEvidence({
  evidence,
}: {
  evidence: {
    candidate_sha256: string
    binding_concerns: { constraint_code: string; reason: string }[]
    infeasibility_proof: BindingInfeasibilityWitness[]
  }
}) {
  return (
    <div className="flex flex-col gap-9 border-t border-warn-line bg-warn-bg px-20 py-14">
      <div className="flex flex-wrap items-center gap-7">
        <span className="text-body font-semibold text-warn-ink">Stage4B 不可行证明（历史账）</span>
        <Mono className="break-all text-mini text-warn-ink">{evidence.candidate_sha256}</Mono>
      </div>
      <span className="prose-cn text-sm2 text-warn-ink">
        这里只转录服务端判定并由代码铸造的证明；模型 concern 不会被前端升级成不可行结论。
        现役链上这一终态由预算内自动重规划消费，不再等待人工裁决。
      </span>
      {evidence.infeasibility_proof.map((witness, index) => (
        <div key={witness.proof_basis + ':' + index} className="rounded-btn border border-warn-line bg-card px-11 py-9">
          <div className="flex flex-wrap gap-6 text-sm2 text-warn-ink">
            <Mono>{witness.constraint_code}</Mono>
            <span>第 {witness.chapter_ordinal} 章</span>
            <span>场景 {witness.scenes.join('、')}</span>
          </div>
          <div className="mt-4 break-all text-micro text-ink-4">
            closed_refs {witness.closed_refs.join('、')} · proof_basis <Mono>{witness.proof_basis}</Mono>
          </div>
        </div>
      ))}
      {evidence.binding_concerns.length ? (
        <div className="flex flex-col gap-4 text-micro text-ink-4">
          <span>同时保留的非阻断 concerns</span>
          {evidence.binding_concerns.map((concern, index) => (
            <span key={concern.constraint_code + ':' + index} className="prose-cn">
              <Mono>{concern.constraint_code}</Mono> · {concern.reason}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  )
}
