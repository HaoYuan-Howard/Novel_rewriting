import { useState } from 'react'
import { errorBody } from '@/api/errors'
import { lookupAction, type ClassifiableAction } from '@/constants/actions'
import { useApprovals } from '@/hooks/useApprovals'
import { useAskJob, useChrome } from '@/hooks/useChrome'
import { useEnums } from '@/hooks/useEnums'
import type {
  ApprovalDoc,
  ActionRef,
  StructureApprovalDecision,
  StructureChapterApproval,
} from '@/types/approvals'
import type { Stage4IndexRow } from '@/types/stages'
import { ErrorHint } from '@/components/state/States'
import { ActionButton, LinkButton } from '@/components/ui/ActionButton'
import { ActorReasonFields } from '@/components/ui/ActorReasonFields'
import { Mono } from '@/components/ui/Mono'
import { Panel } from '@/components/ui/Panel'
import { GateMark } from '@/components/vocab/Badges'

interface DraftDecision {
  decision: string
  reason: string
}

/**
 * Stage4 的专用结构审批面。
 *
 * 通用 GateBar 只有一个 bool，无法携带 subject / actor / 全章 typed 决定，因此 s4
 * 必须在这里原子提交。这里也不出现「授权写作」按钮：writing 是 Stage5 的另一项授权。
 */
export function StructureApprovalPanel({
  rows,
  identity,
  viewSubject,
  onRefreshView,
}: {
  rows: Stage4IndexRow[]
  identity: boolean
  /** `rows` 所属的同份 Stage4 view 主体；不得从 approvals 反推。 */
  viewSubject: string | null
  /** 强制重读 Stage4 view；审批记录由本面板同时重读。 */
  onRefreshView: () => unknown | Promise<unknown>
}) {
  const approvals = useApprovals()
  const enums = useEnums()
  const ask = useAskJob()
  const [refreshing, setRefreshing] = useState(false)

  if (approvals.isLoading) {
    return <Panel className="mx-30 mt-20 mb-4 px-20 py-16 text-body text-ink-4">读取结构审批记录…</Panel>
  }
  if (approvals.isError) {
    return (
      <Panel className="mx-30 mt-20 mb-4 border-warn-line bg-warn-bg px-20 py-16 text-body text-warn-ink">
        结构审批记录读取失败：{errorBody(approvals.error)}
        <ErrorHint error={approvals.error} />
      </Panel>
    )
  }
  const doc = approvals.doc
  if (!doc) return null

  // 是否存在付费恢复只认服务端 action；界面不根据 plan/binding 自己猜。
  // 因而 B=infeasible 即使没有正式 plan subject，也仍能走同一条预览确认缝。
  const structureSpawns = doc.spawns.s4 ?? []
  const replanSpawn = structureSpawns.length === 1 ? structureSpawns[0] : null
  const replanAction = lookupAction(enums.data, replanSpawn)
  const startReplan = () => {
    if (!replanSpawn) return
    ask({
      kind: replanSpawn.kind,
      label: replanSpawn.label,
      extra: replanSpawn.extra,
      required_inputs: replanAction?.required_inputs,
      sid: doc.sid,
      ordinal: null,
    })
  }
  const subject = doc.structure.current_subject_sha256
  if (identity) {
    return (
      <Panel className="mx-30 mt-20 mb-4 flex items-start gap-12 px-20 py-16">
        <GateMark ok={false} />
        <div className="flex flex-col gap-5">
          <span className="text-read font-semibold">正式 Stage4 计划还不能审批</span>
          <span className="prose-cn text-sm2 text-ink-4">
            当前展示的是恒等基线，不是服务端可绑定的结构主体。先生成并校验正式计划；
            没有完整 subject SHA-256 时，界面不会编一个批准对象。
          </span>
          {replanSpawn ? (
            <span className="prose-cn text-sm2 text-warn-ink">
              Stage4B 已落下当前 typed infeasible 结论。保存结论本身没有发起作业；
              请另行预览并确认重跑 Stage4A，完成后仍需单独确认 Stage4B。
            </span>
          ) : null}
        </div>
        {replanSpawn ? (
          <ActionButton action={replanAction} onClick={startReplan}>
            {replanSpawn.label}
          </ActionButton>
        ) : null}
      </Panel>
    )
  }

  // Stage4 view 与 approvals 是两个独立查询。仅靠提交时的后端乐观锁，
  // 会允许审核人看着旧 rows，却把决定绑到 approvals 刚读到的新主体。
  // 因此这里先闭合“展示表面”：两份完整 hash 不相等就连表单都不开。
  if (viewSubject === null || subject === null || viewSubject !== subject) {
    const refreshBoth = async () => {
      setRefreshing(true)
      try {
        await Promise.all([
          Promise.resolve(onRefreshView()),
          approvals.refetch(),
        ])
      } finally {
        setRefreshing(false)
      }
    }
    return (
      <Panel className="mx-30 mt-20 mb-4 flex items-start gap-12 border-warn-line bg-warn-bg px-20 py-16">
        <GateMark ok={false} />
        <div className="flex min-w-0 flex-1 flex-col gap-6">
          <span className="text-read font-semibold text-warn-ink">结构审批对象已变化</span>
          <span className="prose-cn text-sm2 text-warn-ink">
            页面逐章 rows 所属的计划与审批服务当前主体不同。
            提交已禁用；请同时重读两份数据，再按刷新后的 rows 逐章重审。
          </span>
          <span className="text-tag text-warn-ink">页面计划主体 SHA-256</span>
          <Mono className="break-all text-mini text-warn-ink">{viewSubject ?? '（没有）'}</Mono>
          <span className="text-tag text-warn-ink">审批服务主体 SHA-256</span>
          <Mono className="break-all text-mini text-warn-ink">{subject ?? '（没有）'}</Mono>
          <div className="flex flex-wrap gap-8 pt-4">
            <LinkButton
              disabled={refreshing}
              title={refreshing ? '正在同时重读结构与审批记录' : undefined}
              onClick={() => { void refreshBoth() }}
            >
              {refreshing ? '重新读取中…' : '重新读取结构与审批'}
            </LinkButton>
            <ActionButton
              action={{
                kind: 'approval.structure',
                extra: null,
                paid: false,
                writes_disk: false,
              }}
              disabled
              disabledReason="页面结构主体与审批服务当前主体不一致"
            >
              保存全书结构裁决
            </ActionButton>
          </div>
        </div>
      </Panel>
    )
  }

  const existing = doc.structure.current_decision
  const formKey = [
    subject,
    existing?.decided_at ?? 'undecided',
    rows.map((row) => row.output_ordinal).join(','),
  ].join(':')
  return (
    <StructureApprovalForm
      key={formKey}
      doc={doc}
      rows={rows}
      subject={viewSubject}
      existing={existing}
      decisions={enums.data?.structure_approval_decisions ?? []}
      enumsLoading={enums.isLoading}
      enumsError={enums.error}
      submit={approvals.approveStructure}
      replanSpawn={replanSpawn}
      replanAction={replanAction}
      onReplan={startReplan}
    />
  )
}

function StructureApprovalForm({
  doc,
  rows,
  subject,
  existing,
  decisions,
  enumsLoading,
  enumsError,
  submit,
  replanSpawn,
  replanAction,
  onReplan,
}: {
  doc: ApprovalDoc
  rows: Stage4IndexRow[]
  subject: string
  existing: StructureApprovalDecision | null
  decisions: string[]
  enumsLoading: boolean
  enumsError: unknown
  submit: ReturnType<typeof useApprovals>['approveStructure']
  replanSpawn: ActionRef | null
  replanAction: ClassifiableAction | null
  onReplan: () => void
}) {
  const { notify } = useChrome()
  const existingByChapter = new Map(
    (existing?.chapters ?? []).map((row) => [row.output_ordinal, row]),
  )
  const orderedRows = [...rows].sort((a, b) => a.output_ordinal - b.output_ordinal)
  const [actor, setActor] = useState(existing?.actor ?? '')
  const [reason, setReason] = useState(existing?.reason ?? '')
  const [drafts, setDrafts] = useState<Record<number, DraftDecision>>(() =>
    Object.fromEntries(orderedRows.map((row) => {
      const previous = existingByChapter.get(row.output_ordinal)
      return [row.output_ordinal, {
        decision: previous?.decision ?? '',
        reason: previous?.reason ?? '',
      }]
    })),
  )

  const chapters: StructureChapterApproval[] = orderedRows.map((row) => ({
    output_ordinal: row.output_ordinal,
    decision: drafts[row.output_ordinal]?.decision ?? '',
    reason: drafts[row.output_ordinal]?.reason ?? '',
  }))
  const enumReady = decisions.length > 0
  const complete = Boolean(
    actor.trim() &&
    reason.trim() &&
    enumReady &&
    chapters.length > 0 &&
    chapters.every((row) => decisions.includes(row.decision) && row.reason.trim()),
  )

  const setChapter = (ordinal: number, patch: Partial<DraftDecision>) => {
    setDrafts((current) => {
      const previous = current[ordinal] ?? { decision: '', reason: '' }
      return { ...current, [ordinal]: { ...previous, ...patch } }
    })
  }

  return (
    <Panel className="mx-30 mt-20 mb-4 overflow-hidden">
      <div className="flex items-start gap-12 border-b border-line-row px-20 py-16">
        <GateMark ok={doc.gate.s4} />
        <div className="flex min-w-0 flex-1 flex-col gap-5">
          <div className="flex flex-wrap items-baseline gap-10">
            <span className="text-read font-semibold">Stage4 独立结构审批</span>
            <Mono className="rounded-tag bg-inset px-7 py-2 text-micro">
              服务端状态 {doc.structure.state}
            </Mono>
          </div>
          <span className="prose-cn text-sm2 text-ink-4">
            一次提交必须覆盖当前计划的全部章节。任一章要求重规划，只会保存 typed 决定并关闭 s4；
            不会静默发起付费重规划，也不会授予 Stage5 写作权限。
          </span>
          <span className="text-tag text-ink-4">
            结构主体 SHA-256
          </span>
          <Mono className="break-all text-mini text-ink-2">{subject}</Mono>
        </div>
        <div className="flex flex-col items-end gap-4 text-tag text-ink-4">
          <span>{doc.gate.s4 ? 's4 已打开' : 's4 未打开'}</span>
          <span>Stage5 写作授权（独立）：{doc.writing ? '已授权' : '未授权'}</span>
        </div>
      </div>

      {replanSpawn ? (
        <div className="flex items-center gap-14 border-b border-warn-line bg-warn-bg px-20 py-14">
          <div className="flex min-w-0 flex-1 flex-col gap-4">
            <span className="text-body font-semibold text-warn-ink">结构重规划请求已保存</span>
            <span className="prose-cn text-sm2 text-warn-ink">
              保存决定本身没有发起作业。请另行预览并确认 Stage4A；完成后仍需单独确认 Stage4B。
            </span>
          </div>
          <ActionButton
            action={replanAction}
            disabled={submit.isPending}
            disabledReason="正在保存结构裁决"
            onClick={onReplan}
          >
            {replanSpawn.label}
          </ActionButton>
        </div>
      ) : null}

      <div className="flex flex-col divide-y divide-line-row">
        {orderedRows.map((row) => {
          const draft = drafts[row.output_ordinal] ?? { decision: '', reason: '' }
          const plan = row.structure_decision
          return (
            <div
              key={row.output_ordinal}
              className="grid gap-14 px-20 py-15"
              style={{ gridTemplateColumns: 'minmax(0,1.35fr) minmax(0,1fr)' }}
            >
              <div className="flex min-w-0 flex-col gap-7">
                <div className="flex flex-wrap items-center gap-8">
                  <Mono className="rounded-tag bg-inset px-7 py-2 text-mini">
                    第 {row.output_ordinal} 章
                  </Mono>
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

              <div className="flex min-w-0 flex-col gap-8">
                <label className="flex flex-col gap-4 text-tag text-ink-3">
                  第 {row.output_ordinal} 章结构裁决
                  <select
                    aria-label={'第 ' + row.output_ordinal + ' 章结构裁决'}
                    value={draft.decision}
                    disabled={!enumReady || submit.isPending}
                    onChange={(event) => setChapter(row.output_ordinal, {
                      decision: event.target.value,
                    })}
                    className="rounded-btn border border-line-btn bg-card px-10 py-8 font-mono text-body font-normal text-ink"
                  >
                    <option value="">请选择服务端下发的决定</option>
                    {decisions.map((decision) => (
                      <option key={decision} value={decision}>{decision}</option>
                    ))}
                  </select>
                </label>
                <label className="flex flex-col gap-4 text-tag text-ink-3">
                  第 {row.output_ordinal} 章裁决理由
                  <input
                    aria-label={'第 ' + row.output_ordinal + ' 章裁决理由'}
                    value={draft.reason}
                    disabled={submit.isPending}
                    onChange={(event) => setChapter(row.output_ordinal, {
                      reason: event.target.value,
                    })}
                    placeholder="说明核对了哪些结构变化，或为何要求重规划"
                    className="rounded-btn border border-line-btn bg-card px-10 py-8 text-body font-normal text-ink"
                  />
                </label>
              </div>
            </div>
          )
        })}
      </div>

      <div className="border-t border-line-row px-20 py-16">
        <ActorReasonFields
          actor={actor}
          reason={reason}
          onActor={setActor}
          onReason={setReason}
          reasonPlaceholder="说明逐章核对了什么，以及为何接受或要求重规划"
          note={
            <>
              本次决定绑定上方完整 subject SHA-256 与全部逐章理由。结构审批和写作授权是两项独立动作；
              后者仍只在 Stage5 的付费写作确认流中授予。
            </>
          }
          actions={
            <ActionButton
              action={{
                kind: 'approval.structure',
                extra: null,
                paid: false,
                writes_disk: false,
              }}
              disabled={!complete || submit.isPending}
              disabledReason={
                enumsLoading
                  ? '正在读取服务端结构审批取值面'
                  : enumsError
                    ? '服务端结构审批取值面读取失败'
                    : !complete
                      ? 'actor、全书理由、每章决定与每章理由都必须填写'
                      : ''
              }
              onClick={() => submit.mutate({
                subject_sha256: subject,
                actor: actor.trim(),
                reason: reason.trim(),
                chapters: chapters.map((row) => ({ ...row, reason: row.reason.trim() })),
              }, {
                onSuccess: (next) => notify(
                  next.gate.s4
                    ? '全章结构裁决已保存，s4 已打开；Stage5 写作仍需单独授权'
                    : '结构重规划请求已保存，s4 已关闭；未自动发起付费作业',
                ),
              })}
            >
              {submit.isPending ? '保存结构裁决中…' : '保存全书结构裁决'}
            </ActionButton>
          }
        />
        {enumsError ? (
          <div className="mt-10 text-sm2 text-warn-ink">
            结构审批取值面读取失败：{errorBody(enumsError)}
            <ErrorHint error={enumsError} />
          </div>
        ) : null}
        {submit.isError ? (
          <div className="mt-10 text-sm2 text-warn-ink">
            结构裁决保存失败：{errorBody(submit.error)}
            <ErrorHint error={submit.error} />
          </div>
        ) : null}
      </div>
    </Panel>
  )
}
