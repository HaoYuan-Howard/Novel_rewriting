import { useState } from 'react'
import { errorBody } from '@/api/errors'
import { lookupAction } from '@/constants/actions'
import { useApprovals } from '@/hooks/useApprovals'
import { useAskJob, useChrome } from '@/hooks/useChrome'
import { useEnums } from '@/hooks/useEnums'
import type {
  DesignChapterReview,
  DesignRepairConstraint,
  DesignReviewRequest,
  DesignReviewTemplate,
  DesignReviewTemplateQuery,
} from '@/types/approvals'
import { ErrorHint } from '@/components/state/States'
import { ActionButton, LinkButton } from '@/components/ui/ActionButton'
import { Mono } from '@/components/ui/Mono'
import { Panel } from '@/components/ui/Panel'
import { GateMark } from '@/components/vocab/Badges'
import { useDesignReview } from './useDesignReview'
import { ApprovalWorkpackPanel } from './ApprovalWorkpackPanel'

function reviewRunId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID().toLowerCase()
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (token) => {
    const value = Math.floor(Math.random() * 16)
    return (token === 'x' ? value : (value & 0x3) | 0x8).toString(16)
  })
}

function stateLabel(state: string): string {
  switch (state) {
    case 'unavailable': return '当前没有可审阅候选'
    case 'missing': return '当前候选尚未审阅'
    case 'repair_requested': return '当前候选已要求逐章返修'
    case 'strategy_replan_requested': return '当前候选已要求整书重规划'
    case 'accepted': return '当前候选已正式接受'
    case 'expired': return '历史审阅已过期'
    default: return '服务端状态 ' + state
  }
}

/** Stage4A 候选的正式人审；与 Stage4B 最终计划审批严格分票。 */
export function DesignReviewPanel() {
  const approvals = useApprovals()
  const enums = useEnums()
  const ask = useAskJob()
  const [actor, setActor] = useState('')
  const [reason, setReason] = useState('')
  const [runId, setRunId] = useState(reviewRunId)
  const [query, setQuery] = useState<DesignReviewTemplateQuery | null>(null)
  const review = useDesignReview(query)

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
  const humanReviewer = enums.data?.design_reviewer_kinds.find((value) => value === 'human') ?? ''
  const canStart = Boolean(actor.trim() && reason.trim() && humanReviewer && !enums.isLoading)
  const reset = () => {
    setQuery(null)
    setReason('')
    setRunId(reviewRunId())
  }
  // accepted 后真正的 B continuation 仍由服务端挂在 s2；返修/replan 则挂 s4。
  // 这里只切换服务端已经给出的动作面，不按盘面或决定内容自行推导下一步。
  const followups = view.state === 'accepted' ? (doc.spawns.s2 ?? []) : (doc.spawns.s4 ?? [])
  const actions = followups.map((ref) => ({
    ref,
    action: lookupAction(enums.data, ref),
  }))

  return (
    <Panel className="mx-30 mt-20 mb-4 overflow-hidden">
      <div className="flex items-start gap-12 border-b border-line-row px-20 py-16">
        <GateMark ok={view.state === 'accepted'} />
        <div className="flex min-w-0 flex-1 flex-col gap-5">
          <div className="flex flex-wrap items-baseline gap-10">
            <span className="text-read font-semibold">Stage4A 正式设计审阅</span>
            <Mono className="rounded-tag bg-inset px-7 py-2 text-micro">{view.state}</Mono>
            <span className="text-tag text-ink-4">{stateLabel(view.state)}</span>
          </div>
          <span className="prose-cn text-sm2 text-ink-4">
            本票只裁决盲候选能否进入 Stage4B。它不会批准最终计划、不会打开 s4，
            也不会在保存后静默启动付费重规划或定向返修。
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
        </div>
        {view.state === 'accepted' ? (
          <div className="rounded-btn border border-gate-ok-line bg-gate-ok-bg px-12 py-9 text-body font-semibold text-gate-ok-ink">
            服务端判定：当前 A 已接受，可进入 Stage4B；最终计划仍须独立审批
          </div>
        ) : null}
      </div>

      <ApprovalWorkpackPanel kind="design" />

      {view.binding_infeasibility ? (
        <BindingInfeasibilityEvidence evidence={view.binding_infeasibility} />
      ) : null}
      {query === null && view.repair_capability ? (
        <RepairCapabilitySummary capability={view.repair_capability} />
      ) : null}

      {actions.length ? (
        <div className="flex flex-wrap items-center gap-10 border-b border-line-row bg-sunken-2 px-20 py-12">
          <span className="mr-auto text-sm2 text-ink-4">后续动作由服务端按当前正式审阅下发，点击后仍需预览确认。</span>
          {actions.map(({ ref, action }) => (
            <ActionButton
              key={ref.kind + ':' + JSON.stringify(ref.extra)}
              action={action}
              onClick={() => ask({
                kind: ref.kind,
                label: ref.label,
                extra: ref.extra,
                required_inputs: action?.required_inputs,
                sid: doc.sid,
                ordinal: null,
              })}
            >
              {ref.label}
            </ActionButton>
          ))}
        </div>
      ) : null}

      {query === null ? (
        <div className="flex flex-col gap-12 px-20 py-16">
          <div className="prose-cn text-sm2 text-ink-4">
            先建立一次人工审阅会话，再从服务端读取关闭式模板。模板未核实项默认 request，
            本界面不提供“一键全收”。
          </div>
          <div className="grid gap-12 md:grid-cols-2">
            <label className="flex flex-col gap-4 text-tag text-ink-3">
              自报身份 actor
              <input
                aria-label="Stage4A 自报身份 actor"
                value={actor}
                onChange={(event) => setActor(event.target.value)}
                className="rounded-btn border border-line-btn bg-card px-10 py-8 text-body font-normal text-ink"
              />
            </label>
            <label className="flex flex-col gap-4 text-tag text-ink-3">
              本轮审阅总理由 reason
              <input
                aria-label="Stage4A 本轮审阅总理由 reason"
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                placeholder="说明本轮将核对哪些策略、场景和证据"
                className="rounded-btn border border-line-btn bg-card px-10 py-8 text-body font-normal text-ink"
              />
            </label>
          </div>
          <div className="flex flex-wrap items-center gap-10">
            <span className="text-tag text-ink-4">reviewer_kind</span>
            <Mono className="rounded-tag bg-inset px-7 py-2 text-mini">{humanReviewer || '（取值面未就绪）'}</Mono>
            <span className="text-tag text-ink-4">review_run_id</span>
            <Mono className="break-all text-mini text-ink-2">{runId}</Mono>
            <ActionButton
              action={{ kind: 'approval.design.template', extra: null, paid: false, writes_disk: false }}
              disabled={!canStart}
              disabledReason={
                enums.isLoading
                  ? '正在读取服务端审阅取值面'
                  : enums.error
                    ? '服务端审阅取值面读取失败'
                    : !humanReviewer
                      ? '服务端未发布 human 审阅者类型'
                      : 'actor 与本轮总理由都必须填写'
              }
              onClick={() => setQuery({
                actor: actor.trim(),
                reviewer_kind: humanReviewer,
                review_run_id: runId,
                reason: reason.trim(),
              })}
            >
              读取关闭式审阅模板
            </ActionButton>
          </div>
          {enums.error ? (
            <div className="text-sm2 text-warn-ink">
              Stage4A 审阅取值面读取失败：{errorBody(enums.error)}
              <ErrorHint error={enums.error} />
            </div>
          ) : null}
        </div>
      ) : review.template.isLoading ? (
        <div className="px-20 py-16 text-body text-ink-4">读取当前候选的关闭式审阅模板…</div>
      ) : review.template.isError ? (
        <div className="flex items-start gap-12 border-warn-line bg-warn-bg px-20 py-16 text-body text-warn-ink">
          <div className="min-w-0 flex-1">
            模板读取失败：{errorBody(review.template.error)}
            <ErrorHint error={review.template.error} />
          </div>
          <LinkButton onClick={reset}>开始新一轮审阅</LinkButton>
        </div>
      ) : review.template.data ? (
        <DesignReviewForm
          key={review.template.data.aggregate_subject_sha256 + ':' + review.template.data.review_run_id}
          template={review.template.data}
          strategyDecisions={enums.data?.design_strategy_decisions ?? []}
          chapterDecisions={enums.data?.design_chapter_decisions ?? []}
          repairCodes={enums.data?.design_repair_codes ?? []}
          submit={review.submit}
          onReset={reset}
        />
      ) : null}
    </Panel>
  )
}

function DesignReviewForm({
  template,
  strategyDecisions,
  chapterDecisions,
  repairCodes,
  submit,
  onReset,
}: {
  template: DesignReviewTemplate
  strategyDecisions: string[]
  chapterDecisions: string[]
  repairCodes: string[]
  submit: ReturnType<typeof useDesignReview>['submit']
  onReset: () => void
}) {
  const { notify } = useChrome()
  const [draft, setDraft] = useState(template)
  const acceptedChapter = chapterDecisions.find((value) => value === 'accept') ?? ''
  const repairChapter = chapterDecisions.find((value) => value === 'request_repair') ?? ''
  const repairByOrdinal = new Map(
    draft.repair_capability.chapters.map((row) => [row.output_ordinal, row]),
  )

  const setStrategy = (patch: Partial<DesignReviewRequest['strategy']>) => {
    setDraft((current) => ({
      ...current,
      strategy: {
        ...current.strategy,
        ...patch,
        placeholder: true,
        source_decision_sha256: null,
      },
    }))
  }
  const confirmStrategy = (confirmed: boolean) => {
    setDraft((current) => ({
      ...current,
      strategy: {
        ...current.strategy,
        placeholder: !confirmed,
        template_token: confirmed ? null : current.strategy.template_token,
        source_decision_sha256: confirmed
          ? current.strategy.source_decision_sha256
          : null,
      },
    }))
  }
  const setChapter = (ordinal: number, update: (chapter: DesignChapterReview) => DesignChapterReview) => {
    setDraft((current) => ({
      ...current,
      chapters: current.chapters.map((chapter) => (
        chapter.output_ordinal === ordinal
          ? {
              ...update(chapter),
              placeholder: true,
              source_decision_sha256: null,
            }
          : chapter
      )),
    }))
  }
  const confirmChapter = (ordinal: number, confirmed: boolean) => {
    setDraft((current) => ({
      ...current,
      chapters: current.chapters.map((chapter) => (
        chapter.output_ordinal === ordinal
          ? {
              ...chapter,
              placeholder: !confirmed,
              template_token: confirmed ? null : chapter.template_token,
              source_decision_sha256: confirmed
                ? chapter.source_decision_sha256
                : null,
            }
          : chapter
      )),
    }))
  }
  const setChapterDecision = (chapter: DesignChapterReview, decision: string) => ({
    ...chapter,
    decision,
    constraints: decision === acceptedChapter ? [] : chapter.constraints,
  })
  const toggleConstraint = (
    chapter: DesignChapterReview,
    code: string,
    checked: boolean,
  ): DesignChapterReview => {
    const byCode = new Map(chapter.constraints.map((constraint) => [constraint.code, constraint]))
    if (checked) byCode.set(code, byCode.get(code) ?? { code, scene_refs: [], opaque_aliases: [] })
    else byCode.delete(code)
    return {
      ...chapter,
      constraints: repairCodes.flatMap((name) => {
        const hit = byCode.get(name)
        return hit ? [hit] : []
      }),
    }
  }
  const toggleScope = (
    chapter: DesignChapterReview,
    code: string,
    field: 'scene_refs' | 'opaque_aliases',
    value: string,
    checked: boolean,
    canonicalOptions: string[],
  ): DesignChapterReview => ({
    ...chapter,
    constraints: chapter.constraints.map((constraint) => {
      if (constraint.code !== code) return constraint
      const selected = new Set(constraint[field])
      if (checked) selected.add(value)
      else selected.delete(value)
      return { ...constraint, [field]: canonicalOptions.filter((option) => selected.has(option)) }
    }),
  })

  const enumsReady = strategyDecisions.length > 0
    && chapterDecisions.length > 0
    && repairCodes.length > 0
    && acceptedChapter
    && repairChapter
  const complete = Boolean(
    enumsReady
    && draft.actor.trim()
    && draft.reviewer_kind
    && draft.review_run_id
    && draft.general_reason.trim()
    && strategyDecisions.includes(draft.strategy.decision)
    && draft.strategy.reason.trim()
    && !draft.strategy.placeholder
    && draft.strategy.template_token === null
    && draft.chapters.length > 0
    && draft.chapters.every((chapter) => (
      chapterDecisions.includes(chapter.decision)
      && chapter.reason.trim()
      && !chapter.placeholder
      && chapter.template_token === null
      && (chapter.decision === acceptedChapter
        ? chapter.constraints.length === 0
        : chapter.decision === repairChapter
          && repairByOrdinal.get(chapter.output_ordinal)?.request_repair_available === true
          && chapter.constraints.length > 0
          && chapter.constraints.every((constraint) => repairCodes.includes(constraint.code)))
    )),
  )

  return (
    <div className="flex flex-col">
      <div className="flex flex-col gap-7 border-b border-line-row bg-sunken-2 px-20 py-14">
        <RepairCapabilitySummary capability={draft.repair_capability} embedded />
        <div className="flex flex-wrap items-center gap-8">
          <span className="text-body font-semibold">当前整书策略候选</span>
          <Mono className="rounded-tag bg-inset px-7 py-2 text-mini">{draft.strategy.subject_sha256}</Mono>
        </div>
        <div className="prose-cn text-section text-ink-2">
          {draft.strategy.subject.reconstruction_strategy}
        </div>
        <div className="flex flex-wrap gap-6">
          {draft.strategy.subject.chapter_weights.map((row) => (
            <Mono key={row.chapter_ordinal} className="rounded-tag bg-card px-7 py-2 text-mini">
              第 {row.chapter_ordinal} 章 · {row.weight}
            </Mono>
          ))}
        </div>
        <div className="grid gap-4 text-micro text-ink-4 md:grid-cols-2">
          <span>review_contract　<Mono>{draft.strategy.subject.basis.review_contract}</Mono></span>
          <span>alias_algorithm　<Mono>{draft.strategy.subject.basis.alias_algorithm}</Mono></span>
          <span>root_clean_sha1　<Mono className="break-all">{draft.strategy.subject.basis.root_clean_sha1}</Mono></span>
          <span>truth_sha256　<Mono className="break-all">{draft.strategy.subject.basis.truth_sha256}</Mono></span>
        </div>
        <div className="grid gap-10 md:grid-cols-2">
          <label className="flex flex-col gap-4 text-tag text-ink-3">
            整书策略裁决
            <select
              aria-label="Stage4A 整书策略裁决"
              value={draft.strategy.decision}
              disabled={!strategyDecisions.length || submit.isPending}
              onChange={(event) => setStrategy({ decision: event.target.value })}
              className="rounded-btn border border-line-btn bg-card px-10 py-8 font-mono text-body font-normal text-ink"
            >
              {strategyDecisions.map((decision) => (
                <option key={decision} value={decision}>{decision}</option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-4 text-tag text-ink-3">
            整书策略裁决理由
            <input
              aria-label="Stage4A 整书策略裁决理由"
              value={draft.strategy.reason}
              disabled={submit.isPending}
              onChange={(event) => setStrategy({ reason: event.target.value })}
              className="rounded-btn border border-line-btn bg-card px-10 py-8 text-body font-normal text-ink"
            />
          </label>
        </div>
        <ReviewConfirmation
          label="Stage4A 整书策略已核对"
          checked={!draft.strategy.placeholder}
          disabled={submit.isPending}
          sourceDecision={draft.strategy.source_decision_sha256}
          onChange={confirmStrategy}
        />
      </div>

      <div className="flex flex-col divide-y divide-line-row">
        {draft.chapters.map((chapter) => {
          const repairCapability = repairByOrdinal.get(chapter.output_ordinal) ?? null
          return (
            <ChapterReviewEditor
              key={chapter.output_ordinal}
              chapter={chapter}
              chapterDecisions={chapterDecisions}
              repairCodes={repairCodes}
              acceptedDecision={acceptedChapter}
              repairDecision={repairChapter}
              repairCapability={repairCapability}
              candidateRepairable={draft.repair_capability.repairable}
              capabilityReason={draft.repair_capability.reason_code}
              disabled={submit.isPending}
              onConfirmed={(confirmed) => confirmChapter(chapter.output_ordinal, confirmed)}
              onDecision={(decision) => {
                if (
                  decision === repairChapter
                  && repairCapability?.request_repair_available !== true
                ) return
                setChapter(
                  chapter.output_ordinal,
                  (current) => setChapterDecision(current, decision),
                )
              }}
              onReason={(value) => setChapter(
                chapter.output_ordinal,
                (current) => ({ ...current, reason: value }),
              )}
              onConstraint={(code, checked) => setChapter(
                chapter.output_ordinal,
                (current) => toggleConstraint(current, code, checked),
              )}
              onScope={(code, field, value, checked, options) => setChapter(
                chapter.output_ordinal,
                (current) => toggleScope(current, code, field, value, checked, options),
              )}
            />
          )
        })}
      </div>

      <div className="flex flex-wrap items-start gap-12 border-t border-line-row px-20 py-16">
        <div className="flex min-w-0 flex-1 flex-col gap-5 text-tag text-ink-4">
          <span>actor　<Mono className="text-ink-2">{draft.actor}</Mono></span>
          <span>reviewer_kind　<Mono className="text-ink-2">{draft.reviewer_kind}</Mono></span>
          <span>review_run_id　<Mono className="break-all text-ink-2">{draft.review_run_id}</Mono></span>
          <span>general_reason　<span className="prose-cn text-ink-2">{draft.general_reason}</span></span>
          <span>
            提交绑定服务端模板给出的 candidate / aggregate / strategy / chapter 完整 subject；
            前端不重算 current、hash 或是否可进 B。
          </span>
        </div>
        <div className="flex flex-wrap gap-8">
          <LinkButton disabled={submit.isPending} onClick={onReset}>开始新一轮审阅</LinkButton>
          <ActionButton
            action={{ kind: 'approval.design', extra: null, paid: false, writes_disk: true }}
            disabled={!complete || submit.isPending}
            disabledReason={
              !enumsReady
                  ? '服务端 Stage4A 审阅取值面未就绪'
                : !complete
                  ? '策略与每章都必须修改真实反馈后显式核对；request_repair 至少选择一个返修码'
                  : ''
            }
            onClick={() => submit.mutate({
              ...draft,
              general_reason: draft.general_reason.trim(),
              strategy: { ...draft.strategy, reason: draft.strategy.reason.trim() },
              chapters: draft.chapters.map((chapter) => ({
                ...chapter,
                reason: chapter.reason.trim(),
              })),
            }, {
              onSuccess: (next) => notify(
                next.design_review.state === 'accepted'
                  ? 'Stage4A 正式审阅已保存；服务端判定可进入 B，最终计划仍须独立审批'
                  : 'Stage4A 正式审阅已保存；未自动发起付费作业',
              ),
            })}
          >
            {submit.isPending ? '保存 Stage4A 审阅中…' : '保存 Stage4A 正式审阅'}
          </ActionButton>
        </div>
        {submit.isError ? (
          <div className="basis-full text-sm2 text-warn-ink">
            Stage4A 审阅保存失败：{errorBody(submit.error)}
            <ErrorHint error={submit.error} />
          </div>
        ) : null}
      </div>
    </div>
  )
}

function ChapterReviewEditor({
  chapter,
  chapterDecisions,
  repairCodes,
  acceptedDecision,
  repairDecision,
  repairCapability,
  candidateRepairable,
  capabilityReason,
  disabled,
  onConfirmed,
  onDecision,
  onReason,
  onConstraint,
  onScope,
}: {
  chapter: DesignChapterReview
  chapterDecisions: string[]
  repairCodes: string[]
  acceptedDecision: string
  repairDecision: string
  repairCapability: DesignReviewRequest['repair_capability']['chapters'][number] | null
  candidateRepairable: boolean
  capabilityReason: string
  disabled: boolean
  onConfirmed: (confirmed: boolean) => void
  onDecision: (decision: string) => void
  onReason: (reason: string) => void
  onConstraint: (code: string, checked: boolean) => void
  onScope: (
    code: string,
    field: 'scene_refs' | 'opaque_aliases',
    value: string,
    checked: boolean,
    canonicalOptions: string[],
  ) => void
}) {
  const candidate = chapter.subject.chapter
  const sceneOptions = candidate.scenes.map((scene) => scene.candidate_scene_id).sort()
  const aliasDescriptions = new Map<string, string>()
  chapter.subject.blind_truth.facts.forEach((fact) => {
    aliasDescriptions.set(fact.fact_alias, fact.claim)
    fact.participant_aliases.forEach((alias) => {
      if (!aliasDescriptions.has(alias)) aliasDescriptions.set(alias, '本章事实中的参与者 alias')
    })
  })
  chapter.subject.blind_truth.causal_edges.forEach((edge) => {
    if (!aliasDescriptions.has(edge.before_alias)) aliasDescriptions.set(edge.before_alias, '因果边起点 alias')
    if (!aliasDescriptions.has(edge.after_alias)) aliasDescriptions.set(edge.after_alias, '因果边终点 alias')
  })
  chapter.subject.blind_truth.entry_state_aliases.forEach((alias) => {
    if (!aliasDescriptions.has(alias)) aliasDescriptions.set(alias, '章节入口状态 alias')
  })
  chapter.subject.blind_truth.ending_state_aliases.forEach((alias) => {
    if (!aliasDescriptions.has(alias)) aliasDescriptions.set(alias, '章节结尾状态 alias')
  })
  const aliasEntries = [...aliasDescriptions.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
  const aliasOptions = aliasEntries.map(([alias]) => alias)
  const constraints = new Map(chapter.constraints.map((constraint) => [constraint.code, constraint]))

  return (
    <div className="flex flex-col gap-12 px-20 py-16">
      <div className="grid gap-14" style={{ gridTemplateColumns: 'minmax(0,1.35fr) minmax(0,1fr)' }}>
        <div className="flex min-w-0 flex-col gap-8">
          <div className="flex flex-wrap items-center gap-7">
            <Mono className="rounded-tag bg-inset px-7 py-2 text-mini">第 {chapter.output_ordinal} 章</Mono>
            <Mono className="rounded-tag bg-accent-bg px-7 py-2 text-mini text-accent-ink">
              {candidate.structure_decision.mode}
            </Mono>
            <Mono className="rounded-tag bg-inset px-7 py-2 text-mini">权重 {candidate.weight}</Mono>
          </div>
          <div className="prose-cn text-body text-ink-2">{candidate.structure_decision.reason}</div>
          <div className="flex flex-wrap gap-6 text-micro text-ink-4">
            <span>声明变化</span>
            {candidate.structure_decision.declared_change_kinds.map((kind) => (
              <Mono key={kind} className="rounded-tag bg-inset px-6 py-1">{kind}</Mono>
            ))}
          </div>
          <div className="prose-cn rounded-btn bg-inset px-10 py-8 text-sm2 text-ink-2">
            <span className="text-tag text-ink-4">改写任务　</span>{candidate.chapter_rewrite_brief}
          </div>
          <div className="grid gap-6 text-sm2 md:grid-cols-3">
            <GoalEvidence label="本章目标" goal={candidate.chapter_goal} />
            <GoalEvidence label="开篇钩子" goal={candidate.open_hook_goal} />
            <GoalEvidence label="收尾钩子" goal={candidate.end_hook_goal} />
          </div>
        </div>

        <div className="flex min-w-0 flex-col gap-8">
          <label className="flex flex-col gap-4 text-tag text-ink-3">
            第 {chapter.output_ordinal} 章设计裁决
            <select
              aria-label={'第 ' + chapter.output_ordinal + ' 章 Stage4A 设计裁决'}
              value={chapter.decision}
              disabled={disabled || !chapterDecisions.length}
              onChange={(event) => onDecision(event.target.value)}
              className="rounded-btn border border-line-btn bg-card px-10 py-8 font-mono text-body font-normal text-ink"
            >
              {chapterDecisions.map((decision) => (
                <option
                  key={decision}
                  value={decision}
                  disabled={
                    decision === repairDecision
                    && repairCapability?.request_repair_available !== true
                  }
                >
                  {decision}
                </option>
              ))}
            </select>
          </label>
          <ChapterRepairCapability
            capability={repairCapability}
            candidateRepairable={candidateRepairable}
            reasonCode={capabilityReason}
          />
          <label className="flex flex-col gap-4 text-tag text-ink-3">
            第 {chapter.output_ordinal} 章设计裁决理由
            <input
              aria-label={'第 ' + chapter.output_ordinal + ' 章 Stage4A 裁决理由'}
              value={chapter.reason}
              disabled={disabled}
              onChange={(event) => onReason(event.target.value)}
              className="rounded-btn border border-line-btn bg-card px-10 py-8 text-body font-normal text-ink"
            />
          </label>
          <ReviewConfirmation
            label={'第 ' + chapter.output_ordinal + ' 章 Stage4A 已核对'}
            checked={!chapter.placeholder}
            disabled={disabled}
            sourceDecision={chapter.source_decision_sha256}
            onChange={onConfirmed}
          />
          <span className="text-micro text-ink-4">章审阅主体 SHA-256</span>
          <Mono className="break-all text-mini text-ink-2">{chapter.subject_sha256}</Mono>
        </div>
      </div>

      <div className="flex flex-col gap-7 rounded-block border border-line-row bg-sunken-2 px-14 py-12">
        <span className="text-tag text-ink-4">候选场景</span>
        {candidate.scenes.map((scene) => (
          <div key={scene.candidate_scene_id} className="flex flex-col gap-5 rounded-btn bg-card px-12 py-10">
            <div className="flex flex-wrap items-center gap-6">
              <Mono className="rounded-tag bg-inset px-6 py-1 text-mini">{scene.candidate_scene_id}</Mono>
              <Mono className="text-micro text-ink-4">{scene.weight}</Mono>
              <span className="text-micro text-ink-4">{scene.role} · {scene.delivery} · {scene.transition}</span>
            </div>
            <span className="prose-cn text-sm2 text-ink-2">{scene.purpose}</span>
            <div className="prose-cn text-micro text-ink-4">
              落地 alias：{scene.land_fact_aliases.join('、')}；上下文 alias：{scene.context_fact_aliases.join('、') || '（无）'}；
              转折 alias：{scene.turn_fact_alias}
            </div>
            <div className="prose-cn text-micro text-ink-4">
              进入状态：{scene.entry_state_aliases.join('、') || '（无）'}；退出状态：{scene.exit_state_aliases.join('、') || '（无）'}
            </div>
          </div>
        ))}
      </div>

      <div className="grid gap-10 md:grid-cols-2">
        <BlindTruthEvidence truth={chapter.subject.blind_truth} />
        <StructureEvidence evidence={chapter.subject.structure_evidence} />
      </div>

      <div className="flex flex-col gap-8 rounded-block border border-line-row px-14 py-12">
        <div className="flex flex-wrap items-baseline gap-8">
          <span className="text-body font-semibold">定向返修约束</span>
          <span className="text-tag text-ink-4">
            accept 必须为空；request_repair 至少选择一个服务端 repair code
          </span>
        </div>
        {chapter.decision === acceptedDecision ? (
          <span className="text-sm2 text-ink-4">本章为 accept，返修约束为空。</span>
        ) : (
          <>
            <div className="flex flex-wrap gap-9">
              {repairCodes.map((code) => (
                <label key={code} className="flex items-center gap-5 text-sm2 text-ink-2">
                  <input
                    type="checkbox"
                    aria-label={'第 ' + chapter.output_ordinal + ' 章返修码 ' + code}
                    checked={constraints.has(code)}
                    disabled={disabled}
                    onChange={(event) => onConstraint(code, event.target.checked)}
                  />
                  <Mono>{code}</Mono>
                </label>
              ))}
            </div>
            {chapter.constraints.map((constraint) => (
              <ConstraintScope
                key={constraint.code}
                ordinal={chapter.output_ordinal}
                constraint={constraint}
                sceneOptions={sceneOptions}
                aliasEntries={aliasEntries}
                aliasOptions={aliasOptions}
                disabled={disabled}
                onScope={onScope}
              />
            ))}
          </>
        )}
      </div>
    </div>
  )
}

function ReviewConfirmation({
  label,
  checked,
  disabled,
  sourceDecision,
  onChange,
}: {
  label: string
  checked: boolean
  disabled: boolean
  sourceDecision: string | null
  onChange: (confirmed: boolean) => void
}) {
  return (
    <div className="flex flex-col gap-4 rounded-btn border border-line-row bg-card px-10 py-8">
      <label className="flex items-center gap-7 text-sm2 font-semibold text-ink-2">
        <input
          type="checkbox"
          aria-label={label}
          checked={checked}
          disabled={disabled}
          onChange={(event) => onChange(event.target.checked)}
        />
        {label}
      </label>
      <span className={checked ? 'text-micro text-gate-ok-ink' : 'text-micro text-warn-ink'}>
        {checked
          ? sourceDecision
            ? '已核对：沿用服务端验证过的同主体历史 accept'
            : '已核对：将作为本轮正式人工反馈提交'
          : '未核对：编辑不会自动确认；修改已确认内容会回到此状态'}
      </span>
      {!checked ? (
        <span className="prose-cn text-micro text-ink-4">
          请先写入真实裁决与理由，再显式勾选。服务端会拒绝原样提交关闭式默认反馈。
        </span>
      ) : null}
    </div>
  )
}

function RepairCapabilitySummary({
  capability,
  embedded = false,
}: {
  capability: DesignReviewRequest['repair_capability']
  embedded?: boolean
}) {
  return (
    <div className={
      'flex flex-col gap-5 px-20 py-12 '
      + (embedded ? 'rounded-btn border border-line-row bg-card' : 'border-b border-line-row bg-sunken-2')
    }>
      <div className="flex flex-wrap items-center gap-7">
        <span className="text-body font-semibold">Stage4A 定向返修可执行性</span>
        <Mono className={
          'rounded-tag px-7 py-2 text-mini '
          + (capability.repairable
            ? 'bg-gate-ok-bg text-gate-ok-ink'
            : 'bg-warn-bg text-warn-ink')
        }>
          {capability.repairable ? 'repairable' : 'not_repairable'}
        </Mono>
        <span className="text-tag text-ink-4">服务端 reason_code</span>
        <Mono className="text-mini text-ink-2">{capability.reason_code}</Mono>
      </div>
      <span className="prose-cn text-micro text-ink-4">
        每章能否选择 request_repair 只读服务端下发的布尔结论；已提交轮次仅作审计展示，界面不按数值重算额度。
      </span>
    </div>
  )
}

function ChapterRepairCapability({
  capability,
  candidateRepairable,
  reasonCode,
}: {
  capability: DesignReviewRequest['repair_capability']['chapters'][number] | null
  candidateRepairable: boolean
  reasonCode: string
}) {
  if (!capability) {
    return (
      <div className="rounded-btn border border-warn-line bg-warn-bg px-10 py-8 text-sm2 text-warn-ink">
        服务端未给出本章返修可执行性；request_repair 保持禁用。
      </div>
    )
  }
  const message = capability.request_repair_available
    ? '服务端判定：本章 request_repair 可用'
    : candidateRepairable
      ? '服务端判定：本章返修轮次已耗尽，request_repair 不可再选'
      : '服务端判定：当前候选不能执行定向返修，request_repair 不可选'
  return (
    <div
      aria-label={'第 ' + capability.output_ordinal + ' 章定向返修能力'}
      className={
        'flex flex-col gap-3 rounded-btn border px-10 py-8 text-sm2 '
        + (capability.request_repair_available
          ? 'border-gate-ok-line bg-gate-ok-bg text-gate-ok-ink'
          : 'border-warn-line bg-warn-bg text-warn-ink')
      }
    >
      <span className="font-semibold">{message}</span>
      <span className="text-micro">
        committed_rounds <Mono>{capability.committed_rounds}</Mono>
        {' · '}reason_code <Mono>{reasonCode}</Mono>
      </span>
    </div>
  )
}

function BindingInfeasibilityEvidence({
  evidence,
}: {
  evidence: NonNullable<DesignReviewRequest['binding_infeasibility']>
}) {
  return (
    <div className="flex flex-col gap-9 border-b border-warn-line bg-warn-bg px-20 py-14">
      <div className="flex flex-wrap items-center gap-7">
        <span className="text-body font-semibold text-warn-ink">当前 Stage4B 不可行证明</span>
        <Mono className="break-all text-mini text-warn-ink">{evidence.candidate_sha256}</Mono>
      </div>
      <span className="prose-cn text-sm2 text-warn-ink">
        这里只转录服务端已判定为 current / fresh、并由代码铸造的证明；模型 concern 不会被前端升级成不可行结论。
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

function GoalEvidence({
  label,
  goal,
}: {
  label: string
  goal: { intent: string; fact_aliases: string[] }
}) {
  return (
    <div className="flex flex-col gap-3 rounded-btn border border-line-row px-9 py-7">
      <span className="text-tag text-ink-4">{label} · {goal.intent}</span>
      <Mono className="break-all text-micro text-ink-2">{goal.fact_aliases.join('、')}</Mono>
    </div>
  )
}

function BlindTruthEvidence({
  truth,
}: {
  truth: DesignChapterReview['subject']['blind_truth']
}) {
  return (
    <div className="flex min-w-0 flex-col gap-6 rounded-btn bg-inset px-11 py-9">
      <span className="text-tag text-ink-4">逐章盲证据 · {truth.chapter_function}</span>
      {truth.facts.map((fact) => (
        <div key={fact.fact_alias} className="flex min-w-0 flex-col gap-3 border-b border-line-row pb-5 text-micro last:border-0 last:pb-0">
          <div className="flex flex-wrap items-center gap-5">
            <Mono className="break-all text-ink-3">{fact.fact_alias}</Mono>
            <Mono className="rounded-tag bg-card px-5 py-1 text-ink-4">{fact.kind}</Mono>
            <Mono className="rounded-tag bg-card px-5 py-1 text-ink-4">{fact.obligation}</Mono>
          </div>
          <span className="prose-cn text-sm2 text-ink-2">{fact.claim}</span>
          <Mono className="break-all text-ink-4">
            参与者 {fact.participant_aliases.join('、') || '（无）'}
          </Mono>
        </div>
      ))}
      <span className="prose-cn text-micro text-ink-4">
        因果边：{truth.causal_edges.map((edge) => edge.before_alias + ' → ' + edge.after_alias).join('；') || '（无）'}
      </span>
      <span className="prose-cn text-micro text-ink-4">
        入口状态：{truth.entry_state_aliases.join('、') || '（无）'}；
        结尾状态：{truth.ending_state_aliases.join('、') || '（无）'}
      </span>
    </div>
  )
}

function StructureEvidence({
  evidence,
}: {
  evidence: DesignChapterReview['subject']['structure_evidence']
}) {
  const vectors = [
    ['声明变化', evidence.declared_change_kinds],
    ['代码复算', evidence.computed_change_kinds],
    ['已实现声明', evidence.realized_declared],
    ['未实现声明', evidence.unrealized_declared],
    ['未声明实变', evidence.undeclared_actual],
  ] as const
  return (
    <div className="flex min-w-0 flex-col gap-6 rounded-btn bg-inset px-11 py-9">
      <div className="flex flex-wrap items-center gap-6">
        <span className="text-tag text-ink-4">结构机械证据</span>
        <Mono className="rounded-tag bg-card px-6 py-1 text-micro">{evidence.state}</Mono>
        <Mono className="text-micro text-ink-4">{evidence.metric_contract}</Mono>
      </div>
      {vectors.map(([label, values]) => (
        <div key={label} className="flex flex-wrap gap-5 text-micro text-ink-4">
          <span>{label}</span>
          {values.length ? values.map((value) => (
            <Mono key={value} className="rounded-tag bg-card px-5 py-1 text-ink-2">{value}</Mono>
          )) : <span>（无）</span>}
        </div>
      ))}
      <div className="flex flex-col gap-3 border-t border-line-row pt-5">
        {Object.entries(evidence.readings).map(([key, value]) => (
          <div key={key} className="grid gap-5 text-micro" style={{ gridTemplateColumns: 'minmax(0,1fr) minmax(0,0.7fr)' }}>
            <Mono className="break-all text-ink-4">{key}</Mono>
            <Mono className="break-all text-ink-2">{value === null ? 'null' : String(value)}</Mono>
          </div>
        ))}
      </div>
    </div>
  )
}

function ConstraintScope({
  ordinal,
  constraint,
  sceneOptions,
  aliasEntries,
  aliasOptions,
  disabled,
  onScope,
}: {
  ordinal: number
  constraint: DesignRepairConstraint
  sceneOptions: string[]
  aliasEntries: [string, string][]
  aliasOptions: string[]
  disabled: boolean
  onScope: (
    code: string,
    field: 'scene_refs' | 'opaque_aliases',
    value: string,
    checked: boolean,
    canonicalOptions: string[],
  ) => void
}) {
  return (
    <div className="grid gap-10 border-t border-line-row pt-9 md:grid-cols-2">
      <div className="flex flex-col gap-5">
        <Mono className="text-mini font-semibold">{constraint.code} · scene_refs</Mono>
        {sceneOptions.length ? sceneOptions.map((scene) => (
          <label key={scene} className="flex items-center gap-5 text-sm2 text-ink-2">
            <input
              type="checkbox"
              aria-label={'第 ' + ordinal + ' 章 ' + constraint.code + ' 场景 ' + scene}
              checked={constraint.scene_refs.includes(scene)}
              disabled={disabled}
              onChange={(event) => onScope(
                constraint.code, 'scene_refs', scene, event.target.checked, sceneOptions,
              )}
            />
            <Mono>{scene}</Mono>
          </label>
        )) : <span className="text-sm2 text-ink-4">当前章没有候选场景引用</span>}
      </div>
      <div className="flex flex-col gap-5">
        <Mono className="text-mini font-semibold">{constraint.code} · opaque_aliases</Mono>
        {aliasEntries.length ? aliasEntries.map(([alias, ref]) => (
          <label key={alias} className="flex min-w-0 items-center gap-5 text-sm2 text-ink-2">
            <input
              type="checkbox"
              aria-label={'第 ' + ordinal + ' 章 ' + constraint.code + ' alias ' + alias}
              checked={constraint.opaque_aliases.includes(alias)}
              disabled={disabled}
              onChange={(event) => onScope(
                constraint.code, 'opaque_aliases', alias, event.target.checked, aliasOptions,
              )}
            />
            <Mono className="break-all">{alias}</Mono>
            <span className="min-w-0 break-all text-micro text-ink-4">{ref}</span>
          </label>
        )) : <span className="text-sm2 text-ink-4">当前章没有 opaque alias</span>}
      </div>
    </div>
  )
}
