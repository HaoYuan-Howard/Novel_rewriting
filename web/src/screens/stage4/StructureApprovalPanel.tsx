import { errorBody } from '@/api/errors'
import { useApprovals } from '@/hooks/useApprovals'
import type {
  Stage4IndexRow,
} from '@/types/stages'
import type { StructureApprovalDecision, StructureBindingConcern } from '@/types/approvals'
import { ErrorHint } from '@/components/state/States'
import { Mono } from '@/components/ui/Mono'
import { Panel } from '@/components/ui/Panel'
import { GateMark } from '@/components/vocab/Badges'

/**
 * Stage4B 结构裁决的历史只读投影与当前计划的逐章结构决定。
 *
 * 逐章 typed 审批（accept/request_replan + review token）已随 2026-09-15 全自动
 * 裁决退役：不渲染任何表单、token 提交或工作包轮询；s4 的唯一打开条件是统一
 * 机械准入。这里只照画服务端历史账与 stage4 view 下发的逐章结构决定。
 */
export function StructureApprovalPanel({ rows }: { rows: Stage4IndexRow[] }) {
  const approvals = useApprovals()

  if (approvals.isLoading) {
    return <Panel className="mx-30 mt-20 mb-4 px-20 py-16 text-body text-ink-4">读取 Stage4B 结构裁决记录…</Panel>
  }
  if (approvals.isError) {
    return (
      <Panel className="mx-30 mt-20 mb-4 border-warn-line bg-warn-bg px-20 py-16 text-body text-warn-ink">
        Stage4B 结构裁决记录读取失败：{errorBody(approvals.error)}
        <ErrorHint error={approvals.error} />
      </Panel>
    )
  }
  const doc = approvals.doc
  if (!doc) return null

  const view = doc.structure
  const orderedRows = [...rows].sort((a, b) => a.output_ordinal - b.output_ordinal)
  return (
    <Panel className="mx-30 mt-20 mb-4 overflow-hidden">
      <div className="flex items-start gap-12 border-b border-line-row px-20 py-16">
        <GateMark ok={doc.gate.s4} />
        <div className="flex min-w-0 flex-1 flex-col gap-5">
          <div className="flex flex-wrap items-baseline gap-10">
            <span className="text-read font-semibold">Stage4B 结构裁决 · 历史只读</span>
            <Mono className="rounded-tag bg-inset px-7 py-2 text-micro">{view.state}</Mono>
          </div>
          <span className="prose-cn text-sm2 text-ink-4">
            逐章结构审批已退役：机械准入放行计划后即可组装写作上下文，typed infeasible
            终态由预算内自动重规划消费。本面板只呈现服务端保留的历史裁决账，
            不提供保存或重规划入口。
          </span>
          <span className="text-tag text-ink-4">结构主体 SHA-256</span>
          <Mono className="break-all text-mini text-ink-2">
            {view.current_subject_sha256 ?? '（没有）'}
          </Mono>
        </div>
        <div className="flex flex-col items-end gap-4 text-tag text-ink-4">
          <span>{doc.gate.s4 ? 's4 已打开' : 's4 未打开'}</span>
          <span>Stage5 写作授权（独立）：{doc.writing ? '已授权' : '未授权'}</span>
        </div>
      </div>

      {view.binding_concerns.length ? (
        <ConcernList concerns={view.binding_concerns} />
      ) : null}

      {orderedRows.length ? <PlanDecisionRows rows={orderedRows} /> : null}

      <div className="border-t border-line-row px-20 py-16">
        <div className="flex flex-col gap-5 text-tag text-ink-4">
          <span>历史结构裁决 {view.history.length} 次</span>
          {view.current_decision ? <DecisionSummary decision={view.current_decision} /> : null}
          {view.history.length ? <HistoryTable history={view.history} /> : null}
        </div>
      </div>
    </Panel>
  )
}

function ConcernList({ concerns }: { concerns: StructureBindingConcern[] }) {
  return (
    <div className="flex flex-col gap-8 border-b border-warn-line bg-warn-bg px-20 py-14">
      <div className="flex flex-wrap items-baseline gap-8">
        <span className="text-body font-semibold text-warn-ink">Stage4B 绑定关注项（历史账）</span>
        <span className="text-tag text-warn-ink">只展示；语义结论以披露常驻，不改判计划</span>
      </div>
      {concerns.map((concern, index) => (
        <div
          key={concern.constraint_code + ':' + concern.chapter_ordinal + ':' + index}
          className="grid gap-8 rounded-btn border border-warn-line bg-card px-12 py-10 text-sm2"
          style={{ gridTemplateColumns: 'minmax(0,0.7fr) minmax(0,1.3fr)' }}
        >
          <div className="flex min-w-0 flex-col gap-4 text-warn-ink">
            <Mono className="font-semibold">{concern.constraint_code}</Mono>
            <span>{concern.chapter_ordinal === null ? '全书关注项' : '第 ' + concern.chapter_ordinal + ' 章'}</span>
            <span>场景：{concern.scenes.join('、') || '（无）'}</span>
          </div>
          <div className="flex min-w-0 flex-col gap-4 text-ink-2">
            <span className="prose-cn">{concern.reason}</span>
            <span className="text-micro text-ink-4">
              closed_refs：{concern.closed_refs.join('、') || '（无）'}
            </span>
          </div>
        </div>
      ))}
    </div>
  )
}

function PlanDecisionRows({ rows }: { rows: Stage4IndexRow[] }) {
  return (
    <div className="flex flex-col divide-y divide-line-row">
      {rows.map((row) => {
        const plan = row.structure_decision
        return (
          <div key={row.output_ordinal} className="flex flex-col gap-7 px-20 py-15">
            <div className="flex flex-wrap items-center gap-8">
              <Mono className="rounded-tag bg-inset px-7 py-2 text-mini">第 {row.output_ordinal} 章</Mono>
              <Mono className="rounded-tag bg-accent-bg px-7 py-2 text-mini text-accent-ink">
                {plan.mode}
              </Mono>
            </div>
            <span className="prose-cn text-body text-ink-2">{plan.reason}</span>
            <div className="flex flex-wrap gap-6 text-micro text-ink-4">
              <span>声明变化</span>
              {plan.declared_change_kinds.length
                ? plan.declared_change_kinds.map((kind) => (
                    <Mono key={'declared-' + kind} className="rounded-tag bg-inset px-6 py-1">{kind}</Mono>
                  ))
                : <span>（无）</span>}
            </div>
            <div className="flex flex-wrap gap-6 text-micro text-ink-4">
              <span>代码复算</span>
              {plan.computed_change_kinds.length
                ? plan.computed_change_kinds.map((kind) => (
                    <Mono key={'computed-' + kind} className="rounded-tag bg-inset px-6 py-1">{kind}</Mono>
                  ))
                : <span>（无）</span>}
            </div>
          </div>
        )
      })}
    </div>
  )
}

function DecisionSummary({ decision }: { decision: StructureApprovalDecision }) {
  return (
    <div className="flex flex-col gap-4 rounded-btn border border-line-row bg-card px-12 py-10 text-sm2">
      <div className="flex flex-wrap items-center gap-6">
        <span className="text-body font-semibold text-ink-2">最近一次历史裁决</span>
        <span className="text-tag text-ink-4">{decision.actor}</span>
        <Mono className="text-micro text-ink-4">{decision.decided_at}</Mono>
      </div>
      <span className="prose-cn text-ink-2">{decision.reason}</span>
      <span className="text-micro text-ink-4">
        逐章：{decision.chapters
          .map((row) => '第 ' + row.output_ordinal + ' 章 ' + row.decision)
          .join('、') || '（无）'}
      </span>
    </div>
  )
}

function HistoryTable({ history }: { history: StructureApprovalDecision[] }) {
  return (
    <details className="mt-4 rounded-btn border border-line-row bg-card px-10 py-8">
      <summary className="cursor-pointer text-sm2 font-semibold text-ink-2">历史裁决摘要</summary>
      <div className="flex flex-col divide-y divide-line-row pt-4">
        {history.map((decision, index) => (
          <div key={decision.decided_at + ':' + index} className="flex flex-col gap-3 py-6 text-sm2">
            <div className="flex flex-wrap items-center gap-6">
              <span className="text-tag text-ink-4">{decision.actor}</span>
              <Mono className="text-micro text-ink-4">{decision.decided_at}</Mono>
            </div>
            <span className="prose-cn text-ink-2">{decision.reason}</span>
            <span className="text-micro text-ink-4">
              逐章：{decision.chapters
                .map((row) => '第 ' + row.output_ordinal + ' 章 ' + row.decision)
                .join('、') || '（无）'}
            </span>
          </div>
        ))}
      </div>
    </details>
  )
}
