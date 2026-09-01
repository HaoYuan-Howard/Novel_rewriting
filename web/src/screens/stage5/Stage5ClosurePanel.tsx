import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { errorBody } from '@/api/errors'
import { qk } from '@/api/queryKeys'
import { lookupAction, type ClassifiableAction } from '@/constants/actions'
import { useApprovals } from '@/hooks/useApprovals'
import { useAskJob, useChrome } from '@/hooks/useChrome'
import { useEnums } from '@/hooks/useEnums'
import type {
  BatchTrimResolutionRequest,
  Stage5ClosureRequest,
  Stage5ClosureTemplate,
  Stage5ClosureWorkpack,
} from '@/types/approvals'
import type { Job } from '@/types/jobs'
import { ErrorHint } from '@/components/state/States'
import { ActionButton, LinkButton } from '@/components/ui/ActionButton'
import { ActorReasonFields } from '@/components/ui/ActorReasonFields'
import { Mono } from '@/components/ui/Mono'
import { Panel } from '@/components/ui/Panel'
import { GateMark } from '@/components/vocab/Badges'

interface MustAppearDraft {
  ruling: string
  reason: string
  confirmed: boolean
}

/**
 * Stage5 的唯一整书闭合面。
 *
 * state / workpack / 两枚 trim SHA 全部由服务端给；这里只摆证据、收 typed 人工输入，
 * 不从 finding 数量、Review badge 或正文字符串另算「能不能闭合」。
 */
export function Stage5ClosurePanel({
  sid,
  onJumpChapter,
}: {
  sid: string
  onJumpChapter: (ordinal: number) => void
}) {
  const approvals = useApprovals(sid)
  const enums = useEnums()
  const ask = useAskJob()
  const qc = useQueryClient()
  const { notify, openDrawer, askConfirm } = useChrome()
  const [templateOpen, setTemplateOpen] = useState(false)

  const closure = approvals.doc?.closure ?? null
  const workpack = closure?.workpack ?? null
  const templateQ = useQuery({
    queryKey: qk.stage5ClosureTemplate(sid, workpack?.workpack_sha256 ?? null),
    queryFn: () => api.getStage5ClosureTemplate(sid),
    enabled: templateOpen && workpack !== null,
    retry: false,
    staleTime: Infinity,
  })

  const batch = useMutation({
    mutationFn: (request: BatchTrimResolutionRequest) => api.retainPretrimBatch(sid, request),
    onSuccess: (job) => {
      if (job.state === 'failed' || job.state === 'unknown') {
        openDrawer()
      } else if (job.state === 'running') {
        notify('批量 trim 销账作业已发起；等待终态后重读闭合工作包')
      } else {
        notify('批量 trim 销账已落盘；正在重读闭合工作包')
      }
      void qc.invalidateQueries({ queryKey: qk.jobs(sid) })
      void qc.invalidateQueries({ queryKey: qk.approvals(sid) })
      void qc.invalidateQueries({ queryKey: ['stage5', sid] })
      void qc.invalidateQueries({ queryKey: qk.inbox(sid) })
    },
  })

  if (approvals.isLoading) {
    return (
      <Panel className="mx-30 mt-20 mb-4 px-20 py-16 text-body text-ink-4">
        读取 Stage5 Commander 闭合工作包…
      </Panel>
    )
  }
  if (approvals.isError) {
    return (
      <Panel className="mx-30 mt-20 mb-4 border-warn-line bg-warn-bg px-20 py-16 text-body text-warn-ink">
        Stage5 闭合记录读取失败：{errorBody(approvals.error)}
        <ErrorHint error={approvals.error} />
      </Panel>
    )
  }
  const doc = approvals.doc
  if (!doc || !closure) return null

  const reviewAction = lookupAction(enums.data, { kind: 'stage5.review', extra: null })
  const batchAction = lookupAction(enums.data, {
    kind: 'stage5.review',
    extra: ['--retain-pretrim-batch'],
  })
  const askBatchTrim = (request: BatchTrimResolutionRequest) => {
    askConfirm({
      req: {
        kind: 'stage5.review',
        label: '批量确认保留 pretrim · ' + request.items.length + ' 章',
        extra: ['--retain-pretrim-batch'],
        args: ['(服务端一次性 manifest)'],
        required_inputs: batchAction?.required_inputs,
        sid,
        ordinal: null,
      },
      run: () => batch.mutateAsync(request),
    })
  }
  const runFinalReview = () => {
    ask({
      kind: 'stage5.review',
      label: 'Stage5 多次改稿后的最终全书验收',
      extra: null,
      required_inputs: reviewAction?.required_inputs,
      sid,
      ordinal: null,
    })
  }

  const canOpenTemplate = closure.state === 'ready' || closure.state === 'expired'
  const current = closure.current_decision
  return (
    <Panel className="mx-30 mt-20 mb-4 overflow-hidden">
      <div className="flex items-start gap-12 border-b border-line-row px-20 py-16">
        <GateMark ok={doc.gate.s5} />
        <div className="flex min-w-0 flex-1 flex-col gap-5">
          <div className="flex flex-wrap items-baseline gap-9">
            <span className="text-read font-semibold">Stage5 Commander 闭合</span>
            <Mono className="rounded-tag bg-inset px-7 py-2 text-mini">
              服务端状态 {closure.state}
            </Mono>
          </div>
          <span className="prose-cn text-sm2 text-ink-4">
            这是 Stage5「做完了吗」的唯一 typed 回执。它绑定当前 canonical 全章、fresh Review、
            finding 集与 trim 决定；不会把 UNVER 改成 PASS，也没有匿名盖章或跳过入口。
          </span>
          <span className="text-tag text-ink-4">
            s5 闸：{doc.gate.s5 ? '已由当前回执打开' : '未打开'}
            {' · '}历史回执 {closure.history.length} 份
          </span>
        </div>
        <LinkButton onClick={() => { void approvals.refetch() }}>重读当前闭合态</LinkButton>
      </div>

      {closure.state === 'review_required' ? (
        <div className="flex items-center gap-14 border-b border-warn-line bg-warn-bg px-20 py-14">
          <div className="flex min-w-0 flex-1 flex-col gap-4 text-warn-ink">
            <span className="text-body font-semibold">当前没有可用于闭合的 fresh 最终 Review</span>
            <span className="prose-cn text-sm2">
              {closure.next_step ?? '完成这一轮的多次人工编辑后，只跑一次最终 Stage5 Review。'}
              工作包为空时界面不会拿旧 Review 或历史回执补成 ready/closed。
            </span>
          </div>
          <ActionButton
            action={reviewAction}
            disabled={!reviewAction}
            disabledReason="服务端未提供 stage5.review 动作"
            onClick={runFinalReview}
          >
            多次改稿完成，跑一次最终 Review
          </ActionButton>
        </div>
      ) : null}

      {closure.state === 'missing' ? (
        <div className="flex items-center gap-14 border-b border-warn-line bg-warn-bg px-20 py-14">
          <div className="flex min-w-0 flex-1 flex-col gap-4 text-warn-ink">
            <span className="text-body font-semibold">Stage5 闭合工作包尚不可用</span>
            <span className="prose-cn text-sm2">
              {closure.next_step ?? '先完成 Stage5 canonical 成稿并跑最终全书验收。'}
            </span>
          </div>
          <ActionButton
            action={reviewAction}
            disabled={!reviewAction}
            disabledReason="服务端未提供 stage5.review 动作"
            onClick={runFinalReview}
          >
            跑最终全书验收
          </ActionButton>
        </div>
      ) : null}

      {workpack ? <ClosureWorkpackSummary workpack={workpack} /> : null}

      {closure.state === 'blocked' && workpack ? (
        <div className="flex flex-col gap-10 border-t border-warn-line bg-warn-bg px-20 py-14">
          <div className="flex flex-col gap-4 text-warn-ink">
            <span className="text-body font-semibold">工作包仍有服务端阻断项</span>
            <span className="prose-cn text-sm2">
              {closure.next_step ?? '先处理下列阻断项，再重跑最终 Review 并提交回执。'}
            </span>
          </div>
          <BlockerLists workpack={workpack} onJumpChapter={onJumpChapter} />
        </div>
      ) : null}

      {workpack?.unresolved_trim_items.length ? (
        <BatchTrimForm
          key={workpack.workpack_sha256}
          items={workpack.unresolved_trim_items}
          action={batchAction}
          pending={batch.isPending}
          error={batch.error}
          result={batch.data ?? null}
          onSubmit={askBatchTrim}
        />
      ) : null}

      {current ? (
        <div className="flex flex-col gap-5 border-t border-gate-ok-line bg-gate-ok-bg px-20 py-14 text-gate-ok-ink">
          <div className="flex flex-wrap items-baseline gap-8">
            <span className="text-body font-semibold">当前 Commander 回执已闭合</span>
            <span className="text-tag">{current.actor} · {current.decided_at}</span>
          </div>
          <span className="prose-cn text-sm2">{current.reason}</span>
          <Mono className="break-all text-micro">decision {current.decision_sha256}</Mono>
        </div>
      ) : null}

      {canOpenTemplate && !templateOpen ? (
        <div className="flex items-center gap-12 border-t border-line-row px-20 py-14">
          <div className="prose-cn min-w-0 flex-1 text-sm2 text-ink-4">
            {closure.state === 'expired'
              ? '旧回执只留在历史；必须按当前工作包重新生成关闭式模板并逐项裁决。'
              : '当前服务端状态为 ready；生成关闭式模板后仍需逐项显式确认，模板默认值不能直接提交。'}
          </div>
          <ActionButton
            action={{ kind: 'approval.stage5', extra: null, paid: false, writes_disk: false }}
            onClick={() => setTemplateOpen(true)}
          >
            生成 Commander 回执模板
          </ActionButton>
        </div>
      ) : null}

      {templateOpen && templateQ.isLoading ? (
        <div className="border-t border-line-row px-20 py-14 text-body text-ink-4">
          按当前工作包生成关闭式模板…
        </div>
      ) : null}
      {templateOpen && templateQ.isError ? (
        <div className="border-t border-warn-line bg-warn-bg px-20 py-14 text-body text-warn-ink">
          闭合模板读取失败：{errorBody(templateQ.error)}
          <ErrorHint error={templateQ.error} />
        </div>
      ) : null}
      {templateOpen && templateQ.data && workpack ? (
        <Stage5ClosureForm
          key={templateQ.data.workpack_sha256}
          template={templateQ.data}
          workpack={workpack}
          rulings={enums.data?.stage5_must_appear_rulings ?? []}
          groups={enums.data?.stage5_closure_groups ?? []}
          enumsLoading={enums.isLoading}
          enumsError={enums.error}
          pending={approvals.approveStage5Closure.isPending}
          error={approvals.approveStage5Closure.error}
          onJumpChapter={onJumpChapter}
          onSubmit={async (request) => {
            await approvals.approveStage5Closure.mutateAsync(request)
            notify('Stage5 Commander 闭合回执已保存')
          }}
        />
      ) : null}
    </Panel>
  )
}

function ClosureWorkpackSummary({ workpack }: { workpack: Stage5ClosureWorkpack }) {
  return (
    <div className="grid gap-10 border-b border-line-row bg-sunken-2 px-20 py-13 md:grid-cols-2">
      <div className="flex min-w-0 flex-col gap-4 text-sm2 text-ink-3">
        <span>
          最终 Review <Mono>#{workpack.review_seq}</Mono>
          {' · '}计划 {workpack.planned_chapters.length} 章
          {' · '}canonical 主体 {workpack.chapter_subjects.length} 章
        </span>
        <Mono className="break-all text-micro text-ink-4">{workpack.review_rel}</Mono>
        <Mono className="break-all text-micro text-ink-4">review {workpack.review_sha256}</Mono>
      </div>
      <div className="flex min-w-0 flex-col gap-4 text-sm2 text-ink-3">
        <span>
          must_appear {workpack.must_appear.length} 条
          {' · '}trim 待销账 {workpack.unresolved_trim_items.length} 章
          {' · '}披露 {Object.values(workpack.disclosure).reduce((sum, n) => sum + n, 0)} 条
        </span>
        <Mono className="break-all text-micro text-ink-4">workpack {workpack.workpack_sha256}</Mono>
        <details className="text-micro text-ink-4">
          <summary className="cursor-pointer">查看全章 canonical SHA-256</summary>
          <div className="mt-5 flex flex-col gap-3">
            {workpack.chapter_subjects.map((row) => (
              <Mono key={row.output_ordinal} className="break-all">
                ch{row.output_ordinal} {row.chapter_sha256}
              </Mono>
            ))}
          </div>
        </details>
      </div>
    </div>
  )
}

function BlockerLists({
  workpack,
  onJumpChapter,
}: {
  workpack: Stage5ClosureWorkpack
  onJumpChapter: (ordinal: number) => void
}) {
  const chapterGroups = [
    ['缺正式章稿', workpack.missing_chapters],
    ['仍有 .INVALID', workpack.invalid_chapters],
    ['trim 待销账', workpack.unresolved_trim_chapters],
  ] as const
  return (
    <div className="flex flex-col gap-7">
      {chapterGroups.map(([label, ordinals]) => ordinals.length ? (
        <div key={label} className="flex flex-wrap items-center gap-6 text-sm2 text-warn-ink">
          <span className="font-semibold">{label}</span>
          {ordinals.map((ordinal) => (
            <LinkButton key={ordinal} onClick={() => onJumpChapter(ordinal)}>
              第 {ordinal} 章
            </LinkButton>
          ))}
        </div>
      ) : null)}
      {workpack.hard_findings.length ? (
        <div className="flex flex-wrap items-center gap-6 text-sm2 text-warn-ink">
          <span className="font-semibold">阻断级 finding</span>
          {workpack.hard_findings.map((rule) => <Mono key={rule}>{rule}</Mono>)}
        </div>
      ) : null}
    </div>
  )
}

function BatchTrimForm({
  items,
  action,
  pending,
  error,
  result,
  onSubmit,
}: {
  items: Stage5ClosureWorkpack['unresolved_trim_items']
  action: ClassifiableAction | null
  pending: boolean
  error: unknown
  result: Job | null
  onSubmit: (request: BatchTrimResolutionRequest) => void
}) {
  const [actor, setActor] = useState('')
  const [reason, setReason] = useState('')
  const [selected, setSelected] = useState(() => new Set(items.map((item) => item.output_ordinal)))
  const [overrides, setOverrides] = useState<Record<number, string>>({})
  const selectedItems = items
    .filter((item) => selected.has(item.output_ordinal))
    .map((item) => ({ ...item, reason: overrides[item.output_ordinal]?.trim() ?? '' }))
  const complete = Boolean(actor.trim() && reason.trim() && selectedItems.length)

  return (
    <div className="flex flex-col gap-10 border-t border-warn-line bg-warn-bg px-20 py-14">
      <div className="flex flex-col gap-4 text-warn-ink">
        <span className="text-body font-semibold">批量销账 current trim audit</span>
        <span className="prose-cn text-sm2">
          两枚 SHA 都来自上方当前 workpack；浏览器不重算。服务端先预检整批，任一章漂移就一章也不开始；
          成功项只确认保留 pretrim，不采纳裁剪候选。
        </span>
      </div>
      <div className="flex flex-col divide-y divide-warn-line rounded-block border border-warn-line bg-card">
        {items.map((item) => {
          const checked = selected.has(item.output_ordinal)
          return (
            <div key={item.output_ordinal} className="flex flex-col gap-6 px-12 py-10">
              <label className="flex items-center gap-7 text-sm2 font-semibold text-ink-2">
                <input
                  type="checkbox"
                  aria-label={'批量销账第 ' + item.output_ordinal + ' 章'}
                  checked={checked}
                  disabled={pending}
                  onChange={(event) => setSelected((current) => {
                    const next = new Set(current)
                    if (event.target.checked) next.add(item.output_ordinal)
                    else next.delete(item.output_ordinal)
                    return next
                  })}
                />
                第 {item.output_ordinal} 章
              </label>
              <details className="text-micro text-ink-4">
                <summary className="cursor-pointer">查看服务端绑定摘要</summary>
                <Mono className="mt-4 block break-all">audit {item.audit_sha256}</Mono>
                <Mono className="mt-2 block break-all">chapter {item.chapter_sha256}</Mono>
              </details>
              <label className="flex flex-col gap-4 text-tag text-ink-3">
                单项覆盖理由（可空）
                <input
                  aria-label={'第 ' + item.output_ordinal + ' 章 trim 单项理由'}
                  value={overrides[item.output_ordinal] ?? ''}
                  disabled={!checked || pending}
                  onChange={(event) => setOverrides((current) => ({
                    ...current,
                    [item.output_ordinal]: event.target.value,
                  }))}
                  placeholder="空着就使用下方共享理由"
                  className="rounded-btn border border-line-btn bg-card px-10 py-7 text-body font-normal text-ink disabled:opacity-45"
                />
              </label>
            </div>
          )
        })}
      </div>
      {error ? (
        <div className="text-body text-warn-ink">
          批量销账失败：{errorBody(error)}
          <ErrorHint error={error} />
        </div>
      ) : null}
      {result?.state === 'failed' || result?.state === 'unknown' ? (
        <div className="text-body text-warn-ink">
          批量作业终态为 {result.state}；工作包不会据此自动消失，请在作业抽屉核对后重试。
        </div>
      ) : null}
      <ActorReasonFields
        actor={actor}
        reason={reason}
        onActor={setActor}
        onReason={setReason}
        reasonPlaceholder="说明为何这些章都保留当前 pretrim 原稿"
        note="actor/reason 是整批共享审计；确有差异的章可在上方填写单项覆盖理由。"
        actions={
          <ActionButton
            action={action}
            disabled={!action || !complete || pending}
            disabledReason={
              !action
                ? '服务端未提供 stage5.review --retain-pretrim-batch 动作'
                : '至少选择一章，并填写自报身份与共享理由'
            }
            onClick={() => {
              if (!complete) return
              onSubmit({
                actor: actor.trim(),
                reason: reason.trim(),
                items: selectedItems,
              })
            }}
          >
            {pending ? '整批预检与提交中…' : '批量确认保留 pretrim'}
          </ActionButton>
        }
      />
    </div>
  )
}

function Stage5ClosureForm({
  template,
  workpack,
  rulings,
  groups,
  enumsLoading,
  enumsError,
  pending,
  error,
  onJumpChapter,
  onSubmit,
}: {
  template: Stage5ClosureTemplate
  workpack: Stage5ClosureWorkpack
  rulings: string[]
  groups: string[]
  enumsLoading: boolean
  enumsError: unknown
  pending: boolean
  error: unknown
  onJumpChapter: (ordinal: number) => void
  onSubmit: (request: Stage5ClosureRequest) => Promise<void>
}) {
  const [actor, setActor] = useState('')
  const [reason, setReason] = useState('')
  const rowsByIdentity = new Map(workpack.must_appear.map((row) => [
    row.chapter_ordinal + '|' + row.evidence_key,
    row,
  ]))
  const templateCurrent = template.workpack_sha256 === workpack.workpack_sha256
    && template.review_sha256 === workpack.review_sha256
    && template.must_appear.every((row) => {
      const current = rowsByIdentity.get(row.chapter_ordinal + '|' + row.evidence_key)
      return current?.finding_sha256 === row.finding_sha256
        && current.chapter_sha256 === row.chapter_sha256
    })
  const [drafts, setDrafts] = useState<Record<string, MustAppearDraft>>(() =>
    Object.fromEntries(template.must_appear.map((row) => {
      const reusable = templateCurrent && !row.placeholder && row.template_token === null
      return [row.chapter_ordinal + '|' + row.evidence_key, {
        ruling: reusable ? row.ruling : '',
        reason: reusable ? row.reason : '',
        confirmed: reusable,
      }]
    })),
  )
  const [confirmedGroups, setConfirmedGroups] = useState(() => new Set(
    template.group_acks
      .filter((ack) => templateCurrent && !ack.placeholder && ack.template_token === null)
      .map((ack) => ack.group),
  ))

  const decisions = template.must_appear.map((row) => {
    const key = row.chapter_ordinal + '|' + row.evidence_key
    const draft = drafts[key] ?? { ruling: '', reason: '', confirmed: false }
    return {
      ...row,
      ruling: draft.ruling,
      reason: draft.reason.trim(),
      placeholder: !draft.confirmed,
      template_token: draft.confirmed ? null : row.template_token,
    }
  })
  const acks = template.group_acks.map((ack) => ({
    ...ack,
    placeholder: !confirmedGroups.has(ack.group),
    template_token: confirmedGroups.has(ack.group) ? null : ack.template_token,
  }))
  const enumReady = rulings.length > 0 && template.group_acks.every((ack) => groups.includes(ack.group))
  const rowsComplete = decisions.every((row) =>
    !row.placeholder
    && rulings.includes(row.ruling)
    && (row.ruling !== 'accepted_exception' || row.reason.length > 0),
  )
  const groupsComplete = acks.every((ack) => !ack.placeholder)
  const complete = Boolean(
    templateCurrent && actor.trim() && reason.trim() && enumReady && rowsComplete && groupsComplete,
  )

  const setDraft = (key: string, patch: Partial<MustAppearDraft>) => {
    setDrafts((current) => ({
      ...current,
      [key]: {
        ...(current[key] ?? { ruling: '', reason: '', confirmed: false }),
        ...patch,
      },
    }))
  }

  return (
    <div className="flex flex-col border-t border-line-row">
      {!templateCurrent ? (
        <div className="border-b border-warn-line bg-warn-bg px-20 py-14 text-body text-warn-ink">
          页面工作包与刚读取的模板身份不同；提交已禁用。请重读闭合态后按新模板重新审阅。
        </div>
      ) : null}
      <div className="flex flex-col gap-5 border-b border-line-row px-20 py-14">
        <span className="text-body font-semibold">逐项裁决 must_appear</span>
        <span className="prose-cn text-sm2 text-ink-4">
          「需要修改」不是终态：请跳到本章改稿，完成全部编辑后只跑一次最终 Review，再重新生成模板。
          已承载与接受缺项是两种不同终态，后者必须写理由。
        </span>
      </div>
      {template.must_appear.length ? (
        <div className="flex flex-col divide-y divide-line-row">
          {template.must_appear.map((row) => {
            const key = row.chapter_ordinal + '|' + row.evidence_key
            const draft = drafts[key] ?? { ruling: '', reason: '', confirmed: false }
            const evidence = rowsByIdentity.get(key)
            const valid = rulings.includes(draft.ruling)
              && (draft.ruling !== 'accepted_exception' || draft.reason.trim().length > 0)
            return (
              <div key={key} className="grid gap-12 px-20 py-14 md:grid-cols-2">
                <div className="flex min-w-0 flex-col gap-5">
                  <div className="flex flex-wrap items-center gap-7">
                    <LinkButton onClick={() => onJumpChapter(row.chapter_ordinal)}>
                      第 {row.chapter_ordinal} 章
                    </LinkButton>
                    <Mono className="break-all text-micro text-ink-4">{row.evidence_key}</Mono>
                  </div>
                  <span className="prose-cn text-sm2 text-ink-2">
                    {evidence?.detail ?? '当前 workpack 没有这条证据；模板已不可提交。'}
                  </span>
                  <Mono className="break-all text-micro text-ink-5">finding {row.finding_sha256}</Mono>
                  <Mono className="break-all text-micro text-ink-5">chapter {row.chapter_sha256}</Mono>
                </div>
                <div className="flex min-w-0 flex-col gap-7">
                  <label className="flex flex-col gap-4 text-tag text-ink-3">
                    终态裁决
                    <select
                      aria-label={'第 ' + row.chapter_ordinal + ' 章 must_appear 裁决'}
                      value={draft.ruling}
                      disabled={!templateCurrent || pending || !rulings.length}
                      onChange={(event) => setDraft(key, {
                        ruling: event.target.value,
                        confirmed: false,
                      })}
                      className="rounded-btn border border-line-btn bg-card px-10 py-8 font-mono text-body font-normal text-ink"
                    >
                      <option value="">请选择服务端下发的终态</option>
                      {rulings.map((ruling) => <option key={ruling} value={ruling}>{ruling}</option>)}
                    </select>
                  </label>
                  <label className="flex flex-col gap-4 text-tag text-ink-3">
                    逐项理由{draft.ruling === 'accepted_exception' ? '（必填）' : '（可选）'}
                    <input
                      aria-label={'第 ' + row.chapter_ordinal + ' 章 must_appear 理由'}
                      value={draft.reason}
                      disabled={!templateCurrent || pending}
                      onChange={(event) => setDraft(key, {
                        reason: event.target.value,
                        confirmed: false,
                      })}
                      placeholder="说明正文如何承载，或为何明确接受缺项"
                      className="rounded-btn border border-line-btn bg-card px-10 py-8 text-body font-normal text-ink"
                    />
                  </label>
                  <label className="flex items-center gap-7 text-sm2 font-semibold text-ink-2">
                    <input
                      type="checkbox"
                      aria-label={'已核对第 ' + row.chapter_ordinal + ' 章 ' + row.evidence_key}
                      checked={draft.confirmed}
                      disabled={!templateCurrent || pending || !valid}
                      onChange={(event) => setDraft(key, { confirmed: event.target.checked })}
                    />
                    我已按当前 finding 与 chapter SHA 逐项核对
                  </label>
                </div>
              </div>
            )
          })}
        </div>
      ) : (
        <div className="border-b border-line-row px-20 py-12 text-sm2 text-ink-4">
          当前工作包没有 must_appear 逐项裁决。
        </div>
      )}

      <div className="flex flex-col gap-8 border-t border-line-row bg-sunken-2 px-20 py-14">
        <span className="text-body font-semibold">整组披露确认</span>
        {template.group_acks.length ? template.group_acks.map((ack) => {
          const group = ack.group === workpack.fact_group?.group
            ? workpack.fact_group
            : ack.group === workpack.unver_group?.group
              ? workpack.unver_group
              : null
          return (
            <label key={ack.group} className="flex flex-col gap-5 rounded-btn border border-line-row bg-card px-12 py-10">
              <span className="flex items-center gap-7 text-sm2 font-semibold text-ink-2">
                <input
                  type="checkbox"
                  aria-label={'确认整组 ' + ack.group}
                  checked={confirmedGroups.has(ack.group)}
                  disabled={!templateCurrent || pending || !groups.includes(ack.group)}
                  onChange={(event) => setConfirmedGroups((current) => {
                    const next = new Set(current)
                    if (event.target.checked) next.add(ack.group)
                    else next.delete(ack.group)
                    return next
                  })}
                />
                {ack.group} · {ack.items} 条
              </span>
              <span className="prose-cn text-sm2 text-ink-4">
                规则：{group?.rules.join('、') ?? '当前 workpack 找不到这一组'}。
                确认只表示已读完整集合，不会把 finding 改判成 PASS。
              </span>
              <Mono className="break-all text-micro text-ink-5">findings {ack.findings_sha256}</Mono>
            </label>
          )
        }) : (
          <span className="text-sm2 text-ink-4">当前工作包没有需要整组确认的 fact landing / UNVER。</span>
        )}
      </div>

      {error ? (
        <div className="border-t border-warn-line bg-warn-bg px-20 py-14 text-body text-warn-ink">
          回执提交失败：{errorBody(error)}
          <ErrorHint error={error} />
        </div>
      ) : null}
      <div className="border-t border-line-row px-20 py-16">
        <ActorReasonFields
          actor={actor}
          reason={reason}
          onActor={setActor}
          onReason={setReason}
          reasonPlaceholder="说明如何完成整书 Stage5 闭合复核"
          note={
            <>
              本次提交绑定 workpack <Mono className="break-all">{template.workpack_sha256}</Mono>；
              任一 canonical 章、Review 或 finding 集漂移，服务端都会关闭式拒绝。
            </>
          }
          actions={
            <ActionButton
              action={{ kind: 'approval.stage5', extra: null, paid: false, writes_disk: true }}
              disabled={!complete || pending}
              disabledReason={
                enumsLoading
                  ? '正在读取 Stage5 回执取值面'
                  : enumsError
                    ? 'Stage5 回执取值面读取失败'
                    : !templateCurrent
                      ? '模板与页面当前工作包身份不一致'
                      : '请填写 actor/reason，并完成全部逐项裁决与整组确认'
              }
              onClick={() => {
                if (!complete) return
                void onSubmit({
                  actor: actor.trim(),
                  reason: reason.trim(),
                  review_token: template.review_token,
                  must_appear: decisions.map((row) => ({
                    chapter_ordinal: row.chapter_ordinal,
                    evidence_key: row.evidence_key,
                    ruling: row.ruling,
                    reason: row.reason.trim(),
                  })),
                  group_acks: acks.map((row) => ({
                    group: row.group,
                    acknowledged: true,
                  })),
                })
              }}
            >
              {pending ? '保存回执中…' : '提交 Stage5 Commander 回执'}
            </ActionButton>
          }
        />
      </div>
    </div>
  )
}
