import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { errorBody } from '@/api/errors'
import { ErrorHint } from '@/components/state/States'
import { qk } from '@/api/queryKeys'
import { lookupAction } from '@/constants/actions'
import { REVIEW_GROUP_LABEL, REVIEW_GROUP_ORDER, SEV } from '@/constants/vocabulary'
import { useChrome } from '@/hooks/useChrome'
import { useEnums } from '@/hooks/useEnums'
import { QueryStates } from '@/components/state/QueryStates'
import { ActionButton } from '@/components/ui/ActionButton'
import { ActorReasonFields } from '@/components/ui/ActorReasonFields'
import { Mono } from '@/components/ui/Mono'
import { Panel, PaneHeader } from '@/components/ui/Panel'
import type {
  EvidenceRef,
  GlobalReviewDecision,
  GlobalReviewDecisionSet,
  GlobalReviewItem,
  GlobalReviewState,
} from '@/types/stages'

interface ReviewCard {
  key: string
  label: string
  subject: string
  items: GlobalReviewItem[]
  blocking: number
  disclosure: number
}

/**
 * Group the unresolved rows into one card per subject.
 *
 * A person reads this surface by subject ("this character", "this secret's
 * timeline"), not as one 145-row table. The grouping key and the blocking /
 * disclosure verdict both arrive from the server; this function only lays them
 * out. Bulk submit still expands into per-item hash-bound decisions.
 */
function reviewCards(items: GlobalReviewItem[]): ReviewCard[] {
  const byKey = new Map<string, GlobalReviewItem[]>()
  for (const item of items) {
    const rows = byKey.get(item.group_key)
    if (rows) rows.push(item)
    else byKey.set(item.group_key, [item])
  }
  const cards = [...byKey.entries()].map(([key, rows]) => {
    const [prefix, subject] = key.includes(':') ? key.split(':') : [key, '']
    return {
      key,
      label: REVIEW_GROUP_LABEL[prefix] ?? prefix,
      subject,
      items: rows,
      blocking: rows.filter((row) => row.effect === 'blocking').length,
      disclosure: rows.filter((row) => row.effect === 'disclosure').length,
    }
  })
  const rank = (key: string) => {
    const at = REVIEW_GROUP_ORDER.indexOf(key.split(':')[0])
    return at < 0 ? REVIEW_GROUP_ORDER.length : at
  }
  // Cards that can hold Stage4 closed come first; ties keep a stable order.
  return cards.sort((a, b) =>
    Number(b.blocking > 0) - Number(a.blocking > 0)
    || rank(a.key) - rank(b.key)
    || a.key.localeCompare(b.key))
}

type DecisionState = Exclude<GlobalReviewState, 'pending'>

interface RowDraft {
  selected: boolean
  state: DecisionState | ''
  reason: string
  artifact: string
  evidenceSha256: string
  start: string
  end: string
}

const EMPTY_DRAFT: RowDraft = {
  selected: false,
  state: '',
  reason: '',
  artifact: '',
  evidenceSha256: '',
  start: '',
  end: '',
}

const SHA256 = /^[0-9a-f]{64}$/

function decisionEvidence(draft: RowDraft): EvidenceRef[] {
  const artifact = draft.artifact.trim()
  const digest = draft.evidenceSha256.trim()
  const hasOffsets = draft.start.trim() !== '' || draft.end.trim() !== ''
  if (!artifact && !digest && !hasOffsets) return []
  if (!artifact || !SHA256.test(digest)) {
    throw new Error('证据必须同时填写 artifact 与 64 位小写 subject SHA-256')
  }
  if ((draft.start.trim() === '') !== (draft.end.trim() === '')) {
    throw new Error('证据 span 的 start/end 必须同时填写或同时留空')
  }
  if (!hasOffsets) {
    return [{ artifact, subject_sha256: digest, start: null, end: null }]
  }
  const start = Number(draft.start)
  const end = Number(draft.end)
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end <= start) {
    throw new Error('证据 span 必须是 0 起、end 大于 start 的整数区间')
  }
  return [{ artifact, subject_sha256: digest, start, end }]
}

function decisionOf(item: GlobalReviewItem, draft: RowDraft): GlobalReviewDecision {
  if (!draft.state) throw new Error(item.subject_path + ' 还没有选择 PASS / FAIL / UNVER')
  if (!draft.reason.trim()) throw new Error(item.subject_path + ' 必须填写本次复核理由')
  return {
    item_id: item.item_id,
    expected_subject_sha256: item.subject_sha256,
    state: draft.state,
    reason: draft.reason.trim(),
    evidence: decisionEvidence(draft),
  }
}

export function GlobalReviewPanel({ sid }: { sid: string }) {
  const qc = useQueryClient()
  const enums = useEnums()
  const { askConfirm, openDrawer, notify } = useChrome()
  const [actor, setActor] = useState('')
  const [drafts, setDrafts] = useState<Record<string, RowDraft>>({})

  const review = useQuery({
    queryKey: qk.globalReview(sid),
    queryFn: () => api.getGlobalReview(sid),
    retry: false,
  })
  const identity = review.data
    ? review.data.current_subject_sha256 + ':' + review.data.items
      .map((item) => item.item_id + ':' + item.subject_sha256 + ':' + item.state)
      .join('|')
    : ''

  useEffect(() => {
    if (!review.data) return
    setDrafts(Object.fromEntries(
      review.data.items
        .filter((item) => item.state !== 'PASS')
        .map((item) => [item.item_id, { ...EMPTY_DRAFT }]),
    ))
  // Refreshing the exact artifact surface intentionally clears unfinished form state.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [identity])

  const unresolved = useMemo(
    () => review.data?.items.filter((item) => item.state !== 'PASS') ?? [],
    [review.data?.items],
  )
  const cards = useMemo(() => reviewCards(unresolved), [unresolved])
  const selected = unresolved.filter((item) => drafts[item.item_id]?.selected)
  const selectedValid = selected.length > 0 && selected.every((item) => {
    const draft = drafts[item.item_id]
    if (!draft?.state || !draft.reason.trim()) return false
    try {
      decisionEvidence(draft)
      return true
    } catch {
      return false
    }
  })

  const decide = useMutation({
    mutationFn: async (request: GlobalReviewDecisionSet) => {
      if (!review.data?.current) throw new Error('复核面已过期；先从当前 GlobalGrounding 重建')
      const job = await api.decideGlobalReview(sid, request)
      if (job.state === 'failed' || job.state === 'unknown') {
        throw new Error('决策作业没有落盘：' + job.state + '；作业日志里有完整原因')
      }
      return job
    },
    onSuccess: async () => {
      openDrawer()
      await Promise.all([
        qc.invalidateQueries({ queryKey: qk.globalReview(sid) }),
        qc.invalidateQueries({ queryKey: qk.inbox(sid) }),
        qc.invalidateQueries({ queryKey: qk.approvals(sid) }),
        qc.invalidateQueries({ queryKey: qk.jobs(sid) }),
        qc.invalidateQueries({ queryKey: qk.novel(sid) }),
      ])
    },
    onError: () => openDrawer(),
  })

  const patchDraft = (id: string, patch: Partial<RowDraft>) => {
    setDrafts((current) => ({
      ...current,
      [id]: { ...(current[id] ?? EMPTY_DRAFT), ...patch },
    }))
  }
  const action = lookupAction(enums.data, {
    kind: 'stage2.global',
    extra: ['--review-decisions'],
  })
  const disabled =
    !review.data?.current || !actor.trim() || !selectedValid || decide.isPending

  return (
    <Panel>
      <PaneHeader
        title="全局逐项复核"
        meta="逐章骨架可先生成；只有当前完整审计面全部 PASS 才允许进入 Stage4"
      />
      <QueryStates
        q={review}
        loadingLabel="读 stage2 全局复核"
        missing={{ title: 'stage2 全局复核尚未生成' }}
      >
        {(data) => (
          <div className="flex flex-col gap-12 px-18 py-14">
            <div className="flex flex-wrap items-center gap-8 text-body">
              <span
                className="rounded-tag border px-8 py-2 font-semibold"
                style={data.approved
                  ? { color: SEV.info.color, borderColor: SEV.info.border }
                  : { color: SEV.unver.color, borderColor: SEV.unver.border }}
              >
                {data.approved ? '阻断面：全部 PASS' : 'Stage4 保持关闭'}
              </span>
              <span className="text-ink-4">
                阻断 PASS {data.effect_counts.blocking.PASS}/
                {Object.values(data.effect_counts.blocking).reduce((a, b) => a + b, 0)}
              </span>
              <span className="text-ink-4">
                披露未决 {data.effect_counts.disclosure.pending
                  + data.effect_counts.disclosure.FAIL
                  + data.effect_counts.disclosure.UNVER}
                （不挡 Stage4，仍须逐条裁决）
              </span>
              <Mono
                className="ml-auto text-micro text-ink-5"
                title={data.current_subject_sha256}
              >
                current {data.current_subject_sha256.slice(0, 16)}…
              </Mono>
            </div>

            {!data.current ? (
              <div
                className="rounded-block border px-14 py-10 text-body"
                style={{ color: SEV.hard.color, borderColor: SEV.hard.border, background: SEV.hard.bg }}
              >
                盘上复核没有绑定当前完整 GlobalGrounding，或缺少现役必审项。旧决定不可提交、不可开门；先重建复核面。
              </div>
            ) : null}

            {unresolved.length === 0 ? (
              <div className="rounded-block bg-sunken-2 px-14 py-11 text-body text-ink-3">
                没有 pending / FAIL / UNVER；读 API 已按当前 GlobalGrounding 重算并确认全部 PASS。
              </div>
            ) : (
              <>
                <ActorReasonFields actor={actor} onActor={setActor} />

                <div className="flex flex-col gap-14">
                  {cards.map((card) => (
                    <section
                      key={card.key}
                      aria-label={'复核卡 ' + card.key}
                      className="rounded-block border border-line px-12 py-10"
                    >
                      <div className="flex flex-wrap items-center gap-8">
                        <input
                          type="checkbox"
                          aria-label={'全选 ' + card.key}
                          checked={card.items.every(
                            (row) => drafts[row.item_id]?.selected)}
                          onChange={(event) => card.items.forEach((row) =>
                            patchDraft(row.item_id, { selected: event.target.checked }))}
                        />
                        <span className="text-body font-semibold">{card.label}</span>
                        {card.subject ? (
                          <Mono className="text-mini text-ink-5">{card.subject}</Mono>
                        ) : null}
                        <span className="ml-auto flex items-center gap-8 text-mini">
                          {card.blocking ? (
                            <span
                              className="rounded-tag border px-7 py-2"
                              style={{ color: SEV.hard.color, borderColor: SEV.hard.border }}
                            >
                              阻断 {card.blocking}
                            </span>
                          ) : null}
                          {card.disclosure ? (
                            <span
                              className="rounded-tag border px-7 py-2"
                              style={{ color: SEV.metric.color, borderColor: SEV.metric.border }}
                            >
                              披露 {card.disclosure}
                            </span>
                          ) : null}
                        </span>
                      </div>
                      <div className="mt-10 flex flex-col gap-10">
                  {card.items.map((item) => {
                    const draft = drafts[item.item_id] ?? EMPTY_DRAFT
                    return (
                      <div key={item.item_id} className="rounded-block border border-line-soft bg-sunken-2 px-14 py-12">
                        <div className="flex flex-wrap items-center gap-8">
                          <input
                            type="checkbox"
                            aria-label={'选择 ' + item.subject_path}
                            checked={draft.selected}
                            onChange={(event) => patchDraft(item.item_id, { selected: event.target.checked })}
                          />
                          <Mono className="text-mini text-ink-5">{item.kind}</Mono>
                          <span className="text-body font-semibold">{item.subject_path}</span>
                          <span
                            className="rounded-tag border px-7 py-2 text-micro"
                            style={item.effect === 'blocking'
                              ? { color: SEV.hard.color, borderColor: SEV.hard.border }
                              : { color: SEV.metric.color, borderColor: SEV.metric.border }}
                          >
                            {item.effect === 'blocking' ? '阻断 Stage4' : '只披露'}
                          </span>
                          <span className="ml-auto rounded-tag bg-card px-7 py-2 text-mini text-ink-3">
                            {item.state}
                          </span>
                        </div>
                        <div className="mt-5 flex items-center gap-8">
                          <Mono className="text-micro text-ink-5" title={item.subject_sha256}>
                            expected_subject_sha256 {item.subject_sha256.slice(0, 16)}…
                          </Mono>
                          <button
                            type="button"
                            onClick={() => {
                              void navigator.clipboard.writeText(item.subject_sha256)
                              notify('已复制')
                            }}
                            className="cursor-pointer rounded-tag border border-line-btn bg-card px-7 py-2 text-micro text-ink-3"
                          >
                            复制
                          </button>
                        </div>
                        {item.reason ? (
                          <div className="mt-5 text-tag text-ink-4">上次理由：{item.reason}</div>
                        ) : null}

                        <div className="mt-10 grid gap-8" style={{ gridTemplateColumns: '150px minmax(280px,1fr)' }}>
                          <label className="flex flex-col gap-3 text-tag text-ink-4">
                            本次结论
                            <select
                              aria-label={item.subject_path + ' 结论'}
                              value={draft.state}
                              onChange={(event) => patchDraft(item.item_id, {
                                state: event.target.value as DecisionState | '',
                              })}
                              className="rounded-btn border border-line bg-card px-9 py-7 text-body text-ink"
                            >
                              <option value="">请选择</option>
                              <option value="PASS">PASS</option>
                              <option value="FAIL">FAIL</option>
                              <option value="UNVER">UNVER</option>
                            </select>
                          </label>
                          <label className="flex flex-col gap-3 text-tag text-ink-4">
                            本次理由
                            <input
                              aria-label={item.subject_path + ' 理由'}
                              value={draft.reason}
                              onChange={(event) => patchDraft(item.item_id, { reason: event.target.value })}
                              className="rounded-btn border border-line bg-card px-9 py-7 text-body text-ink"
                            />
                          </label>
                        </div>
                        <div className="mt-8 grid gap-8" style={{ gridTemplateColumns: '1fr 1fr 90px 90px' }}>
                          <input
                            aria-label={item.subject_path + ' evidence artifact'}
                            value={draft.artifact}
                            onChange={(event) => patchDraft(item.item_id, { artifact: event.target.value })}
                            className="rounded-btn border border-line bg-card px-9 py-7 text-tag"
                            placeholder="evidence artifact（可留空）"
                          />
                          <input
                            aria-label={item.subject_path + ' evidence sha256'}
                            value={draft.evidenceSha256}
                            onChange={(event) => patchDraft(item.item_id, { evidenceSha256: event.target.value })}
                            className="rounded-btn border border-line bg-card px-9 py-7 font-mono text-micro"
                            placeholder="subject SHA-256"
                          />
                          <input
                            aria-label={item.subject_path + ' evidence start'}
                            value={draft.start}
                            onChange={(event) => patchDraft(item.item_id, { start: event.target.value })}
                            className="rounded-btn border border-line bg-card px-9 py-7 text-tag"
                            inputMode="numeric"
                            placeholder="start"
                          />
                          <input
                            aria-label={item.subject_path + ' evidence end'}
                            value={draft.end}
                            onChange={(event) => patchDraft(item.item_id, { end: event.target.value })}
                            className="rounded-btn border border-line bg-card px-9 py-7 text-tag"
                            inputMode="numeric"
                            placeholder="end"
                          />
                        </div>
                      </div>
                    )
                  })}
                      </div>
                    </section>
                  ))}
                </div>

                {decide.isError ? (
                  <div className="text-body text-warn-ink" role="alert">
                    {errorBody(decide.error)}
                    <ErrorHint error={decide.error} />
                  </div>
                ) : null}
                <div className="flex items-center gap-10">
                  <span className="text-tag text-ink-4">
                    证据可空；填写时 locator/hash 必须闭合，span 两端必须同时给。
                  </span>
                  <ActionButton
                    className="ml-auto"
                    action={action}
                    disabled={disabled}
                    disabledReason={!data.current
                      ? '复核面已过期'
                      : !actor.trim()
                        ? '先填写 actor'
                        : !selected.length
                          ? '至少选择一项'
                          : !selectedValid
                            ? '所选项必须给结论、理由，证据若填写则必须合法'
                            : ''}
                    onClick={() => {
                      // Freeze the audited body at the same moment as the
                      // command preview; edits behind the modal cannot change
                      // what the eventual specialized POST sends.
                      const request: GlobalReviewDecisionSet = {
                        actor: actor.trim(),
                        decisions: selected.map(
                          (item) => decisionOf(item, drafts[item.item_id])),
                      }
                      askConfirm({
                        req: {
                          kind: 'stage2.global',
                          label: '提交 ' + selected.length + ' 项 stage2 全局复核',
                          extra: ['--review-decisions'],
                          args: ['(服务端一次性决策文件)'],
                          sid,
                          ordinal: null,
                        },
                        run: () => decide.mutateAsync(request),
                      })
                    }}
                  >
                    提交 {selected.length} 项复核
                  </ActionButton>
                </div>
              </>
            )}
          </div>
        )}
      </QueryStates>
    </Panel>
  )
}
