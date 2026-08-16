import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useVirtualizer } from '@tanstack/react-virtual'
import { api } from '@/api/endpoints'
import { errorBody } from '@/api/errors'
import { qk } from '@/api/queryKeys'
import { lookupAction } from '@/constants/actions'
import { useChrome } from '@/hooks/useChrome'
import { useEnums } from '@/hooks/useEnums'
import { useSid } from '@/hooks/useRouteParams'
import { QueryStates } from '@/components/state/QueryStates'
import { ActionButton, LinkButton } from '@/components/ui/ActionButton'
import { Mono } from '@/components/ui/Mono'
import { Panel, PaneHeader } from '@/components/ui/Panel'
import { cx } from '@/lib/cx'
import type { Job } from '@/types/jobs'
import type {
  Stage0EditRequest,
  Stage0MergeCandidate,
  Stage0MergeResolution,
  Stage0Report,
  Stage0Segments,
} from '@/types/stages'

function sameIds(left: Set<string>, right: Set<string>): boolean {
  return left.size === right.size && [...left].every((id) => right.has(id))
}

function requireOk(job: Job): void {
  if (job.state !== 'ok') {
    throw new Error(job.label + '没有完成：' + job.state + '。作业日志里有完整输出。')
  }
}

export function Stage0Screen() {
  const sid = useSid()
  const nav = useNavigate()
  const qc = useQueryClient()
  const { openDrawer } = useChrome()
  const enums = useEnums()
  const [selection, setSelection] = useState<Set<string> | null>(null)
  const [onlySuspicious, setOnlySuspicious] = useState(true)
  const [actor, setActor] = useState('')
  const [reason, setReason] = useState('')
  const [mergeResolutions, setMergeResolutions] = useState<
    Record<string, { choice: Stage0MergeResolution['choice']; custom_text: string }>
  >({})
  const segmentScroll = useRef<HTMLDivElement>(null)
  const report = useQuery({
    queryKey: qk.stage0Report(sid),
    queryFn: () => api.getStage0Report(sid),
    retry: false,
  })
  const segments = useQuery({
    queryKey: qk.stage0Segments(sid),
    queryFn: () => api.getStage0Segments(sid),
    retry: false,
  })
  const merge = useQuery({
    queryKey: qk.stage0Merge(sid),
    queryFn: () => api.getStage0Merge(sid),
    retry: false,
  })
  const initial = useMemo(
    () => new Set(
      (segments.data?.segments ?? [])
        .filter((item) => item.kind === 'remove')
        .map((item) => item.id),
    ),
    [segments.data],
  )
  const selectionKey = report.data
    ? report.data.round + ':' + report.data.clean_sha1 + ':' + [...initial].join(',')
    : ''

  useEffect(() => {
    if (segments.data?.available) setSelection(new Set(initial))
  }, [selectionKey, segments.data?.available])

  useEffect(() => {
    setMergeResolutions({})
  }, [merge.data?.candidate_id])

  const pickedForView = selection ?? initial
  const suspiciousForView = useMemo(
    () =>
      (segments.data?.segments ?? []).filter(
        (item) => item.kind === 'remove' || pickedForView.has(item.id),
      ),
    [segments.data?.segments, pickedForView],
  )
  const filteredForView = onlySuspicious
    ? suspiciousForView
    : (segments.data?.segments ?? [])
  const segmentVirtualizer = useVirtualizer({
    count: filteredForView.length,
    getScrollElement: () => segmentScroll.current,
    estimateSize: () => 84,
    getItemKey: (index) => filteredForView[index]?.id ?? index,
    overscan: 8,
    initialRect: { width: 1000, height: 640 },
  })

  useEffect(() => {
    segmentVirtualizer.scrollToOffset(0)
  }, [onlySuspicious, selectionKey, segmentVirtualizer])

  const run = useMutation({
    mutationFn: async ({
      report: current,
      segments: currentSegments,
    }: {
      report: Stage0Report
      segments: Stage0Segments
    }) => {
      const picked = selection ?? initial
      if (!sameIds(picked, initial)) {
        const request: Stage0EditRequest = {
          expected_round: current.round,
          expected_source_sha1: current.source_sha1,
          expected_clean_sha1: current.clean_sha1,
          removed_segment_ids: [...picked],
          actor: actor.trim(),
          reason: reason.trim(),
        }
        requireOk(await api.editStage0(sid, request))
      }
      requireOk(await api.approveStage0(sid, { actor: actor.trim(), reason: reason.trim() }))
      requireOk(await api.partitionStage1(sid))
      return currentSegments
    },
    onSuccess: async () => {
      openDrawer()
      await Promise.all([
        qc.invalidateQueries({ queryKey: qk.stage0Report(sid) }),
        qc.invalidateQueries({ queryKey: qk.stage0Segments(sid) }),
        qc.invalidateQueries({ queryKey: qk.s1segments(sid) }),
        qc.invalidateQueries({ queryKey: qk.s1chapters(sid) }),
        qc.invalidateQueries({ queryKey: qk.s1hook(sid) }),
        qc.invalidateQueries({ queryKey: qk.jobs(sid) }),
        qc.invalidateQueries({ queryKey: qk.approvals(sid) }),
        qc.invalidateQueries({ queryKey: qk.novel(sid) }),
        qc.invalidateQueries({ queryKey: qk.novels() }),
      ])
      nav('/n/' + sid + '/stage1')
    },
    onError: async () => {
      openDrawer()
      await Promise.all([
        qc.invalidateQueries({ queryKey: qk.stage0Report(sid) }),
        qc.invalidateQueries({ queryKey: qk.stage0Segments(sid) }),
        qc.invalidateQueries({ queryKey: qk.jobs(sid) }),
      ])
    },
  })

  const decideMerge = useMutation({
    mutationFn: async ({ candidate, reject }: { candidate: Stage0MergeCandidate; reject: boolean }) => {
      const resolutions: Stage0MergeResolution[] = reject
        ? []
        : candidate.conflicts.map((conflict) => {
            const resolution = mergeResolutions[conflict.id]
            if (!resolution) throw new Error('每个冲突都必须恰好处理一次')
            return {
              conflict_id: conflict.id,
              choice: resolution.choice,
              custom_text: resolution.choice === 'custom' ? resolution.custom_text : null,
            }
          })
      const request = {
        candidate_id: candidate.candidate_id,
        expected_round: candidate.expected_round,
        expected_source_sha1: candidate.expected_source_sha1,
        expected_clean_sha1: candidate.expected_clean_sha1,
        resolutions,
        actor: actor.trim(),
        reason: reason.trim(),
      }
      const job = reject
        ? await api.rejectStage0Merge(sid, request)
        : await api.resolveStage0Merge(sid, request)
      requireOk(job)
      return job
    },
    onSuccess: async () => {
      openDrawer()
      await Promise.all([
        qc.invalidateQueries({ queryKey: qk.stage0Report(sid) }),
        qc.invalidateQueries({ queryKey: qk.stage0Segments(sid) }),
        qc.invalidateQueries({ queryKey: qk.stage0Merge(sid) }),
        qc.invalidateQueries({ queryKey: qk.jobs(sid) }),
        qc.invalidateQueries({ queryKey: qk.novel(sid) }),
      ])
    },
    onError: () => openDrawer(),
  })

  return (
    <QueryStates q={report} loadingLabel={'读取 Stage 0 审校报告'}>
      {(current) => (
        <QueryStates q={segments} loadingLabel={'读取原文连续分区'}>
          {(source) => {
            const picked = selection ?? initial
            const removed = source.segments.filter((item) => picked.has(item.id))
            const dirty = !sameIds(picked, initial)
            const removedChars = removed.reduce((sum, item) => sum + item.chars, 0)
            const approved = current.approved === 'approved'
            const suspicious = suspiciousForView
            const filteredSegments = filteredForView
            return (
              <section className={'flex flex-col gap-16 px-30 py-24'}>
                <header className={'flex items-start justify-between gap-24'}>
                  <div className={'flex flex-col gap-5'}>
                    <h1 className={'m-0 text-screen font-semibold'}>剔除非正文 · stage0</h1>
                    <p className={'prose-cn m-0 max-w-[58em] text-body text-ink-3'}>
                      这是后端保存的完整原文。点任意正文分区可加入或移出删除范围；结尾标记只作边界提示，不能删除。
                    </p>
                  </div>
                  <div className={'flex flex-col items-end gap-2 text-right'}>
                    <Mono className={'text-tag text-ink-4'}>{current.original_filename}</Mono>
                    <Mono className={'text-tag text-ink-4'}>
                      round {current.round} · sha1 {current.source_sha1.slice(0, 12)} · {current.source_encoding}
                    </Mono>
                  </div>
                </header>
                {merge.data ? (
                  <Panel className="border-warn-line">
                    <PaneHeader
                      title="机器重跑产生了三方合并候选"
                      meta={
                        merge.data.conflicts.length
                          ? merge.data.conflicts.length + ' 个 typed conflict'
                          : '改动不重叠，可直接接受'
                      }
                    />
                    <div className="flex flex-col gap-10 px-18 py-14">
                      <p className="prose-cn m-0 text-body text-ink-3">
                        正式正文仍保持不动。base 是上次机器正文，ours 是已批准正文，theirs 是本次机器重跑；只有显式处理并通过乐观锁后才会换正式正文。
                      </p>
                      <Mono className="text-micro text-ink-5">
                        candidate {merge.data.candidate_id.slice(0, 12)} · round {merge.data.expected_round} · base snapshot {merge.data.base_snapshot_sha256.slice(0, 12)}
                      </Mono>
                      {merge.data.conflicts.map((conflict) => {
                        const resolution = mergeResolutions[conflict.id]
                        return (
                          <div key={conflict.id} className="overflow-hidden rounded-block border border-line-btn">
                            <div className="flex items-center gap-8 border-b border-line-soft bg-sunken-2 px-12 py-8">
                              <Mono className="text-mini">{conflict.id}</Mono>
                              <Mono className="text-micro text-ink-5">
                                base[{conflict.base_span[0]}:{conflict.base_span[1]}]
                              </Mono>
                            </div>
                            <div className="grid grid-cols-3 gap-1 bg-line-2">
                              {[
                                ['base', conflict.base_text],
                                ['ours', conflict.ours_text],
                                ['theirs', conflict.theirs_text],
                              ].map(([label, text]) => (
                                <div key={label} className="bg-card px-12 py-9">
                                  <Mono className="text-micro text-ink-5">{label}</Mono>
                                  <div className="prose-cn mt-3 text-body whitespace-pre-wrap text-ink-2">{text}</div>
                                </div>
                              ))}
                            </div>
                            <div className="flex flex-wrap items-center gap-8 border-t border-line-soft px-12 py-9">
                              {(['ours', 'theirs', 'custom'] as const).map((choice) => (
                                <label key={choice} className="flex cursor-pointer items-center gap-4 text-body">
                                  <input
                                    type="radio"
                                    name={'merge-' + conflict.id}
                                    checked={resolution?.choice === choice}
                                    onChange={() =>
                                      setMergeResolutions((old) => ({
                                        ...old,
                                        [conflict.id]: {
                                          choice,
                                          custom_text: old[conflict.id]?.custom_text ?? '',
                                        },
                                      }))
                                    }
                                  />
                                  {choice}
                                </label>
                              ))}
                              {resolution?.choice === 'custom' ? (
                                <textarea
                                  aria-label={conflict.id + ' custom text'}
                                  value={resolution.custom_text}
                                  onChange={(event) =>
                                    setMergeResolutions((old) => ({
                                      ...old,
                                      [conflict.id]: { choice: 'custom', custom_text: event.target.value },
                                    }))
                                  }
                                  className="min-h-70 min-w-[320px] flex-1 rounded-btn border border-line-btn px-9 py-7 text-body"
                                />
                              ) : null}
                            </div>
                          </div>
                        )
                      })}
                      <details className="rounded-btn border border-line-soft bg-sunken-2 px-12 py-9">
                        <summary className="cursor-pointer text-body font-semibold">查看自动合并后的完整候选</summary>
                        <pre className="m-0 mt-8 max-h-[360px] overflow-auto border-t border-line-soft pt-8 font-sans text-body whitespace-pre-wrap text-ink-2">
                          {merge.data.proposed_clean_text}
                        </pre>
                      </details>
                      {decideMerge.isError ? (
                        <div className="rounded-btn border border-warn-line bg-warn-bg px-12 py-9 text-body text-warn-ink">
                          {errorBody(decideMerge.error)}
                        </div>
                      ) : null}
                      <div className="flex justify-end gap-8">
                        <button
                          type="button"
                          disabled={decideMerge.isPending || !actor.trim() || !reason.trim()}
                          onClick={() => decideMerge.mutate({ candidate: merge.data!, reject: true })}
                          className="cursor-pointer rounded-btn border border-line-btn bg-card px-12 py-7 text-body font-semibold disabled:cursor-not-allowed disabled:opacity-40"
                        >
                          拒绝本次机器候选
                        </button>
                        <button
                          type="button"
                          disabled={
                            decideMerge.isPending ||
                            !actor.trim() ||
                            !reason.trim() ||
                            merge.data.conflicts.some((conflict) => {
                              const resolution = mergeResolutions[conflict.id]
                              return !resolution ||
                                (resolution.choice === 'custom' && !resolution.custom_text)
                            })
                          }
                          onClick={() => decideMerge.mutate({ candidate: merge.data!, reject: false })}
                          className="cursor-pointer rounded-btn bg-ink px-12 py-7 text-body font-semibold text-card disabled:cursor-not-allowed disabled:opacity-40"
                        >
                          接受三方合并候选
                        </button>
                      </div>
                    </div>
                  </Panel>
                ) : null}
                {!source.available ? (
                  <Panel className={'flex flex-col gap-8 border-warn-line px-18 py-16'}>
                    <div className={'text-section font-semibold'}>原文不可审校</div>
                    <p className={'prose-cn m-0 text-body text-ink-3'}>{source.reason}</p>
                    <Mono className={'text-mini text-ink-5'}>{current.original_path}</Mono>
                  </Panel>
                ) : (
                  <>
                    <Panel className={'flex flex-wrap items-center gap-12 px-18 py-13'}>
                      <span
                        className={cx(
                          'rounded-chip px-9 py-3 text-tag font-semibold',
                          approved ? 'bg-gate-ok-bg text-ink-2' : 'bg-warn-bg text-warn',
                        )}
                      >
                        {approved ? '已批准' : '待人工批准'}
                      </span>
                      <span className={'text-body font-semibold'}>
                        当前删除 {removed.length} 段 · {removedChars} 码点
                      </span>
                      <span className={'text-tag text-ink-4'}>
                        原文 {current.total_code_points} 码点 · 选择后正文{' '}
                        {Math.max(0, current.total_code_points - removedChars)} 码点
                      </span>
                      {current.edited_by_human ? (
                        <span className={'rounded-tag bg-inset px-7 py-2 text-micro text-ink-3'}>
                          含人工编辑
                        </span>
                      ) : null}
                      {current.warnings.map((warning) => (
                        <span key={warning.kind} className={'text-tag text-warn'}>
                          {warning.message}
                        </span>
                      ))}
                      <div className={'ml-auto flex items-center gap-9'}>
                        {dirty ? (
                          <LinkButton onClick={() => setSelection(new Set(initial))}>恢复本轮</LinkButton>
                        ) : null}
                        <ActionButton
                          action={lookupAction(
                            enums.data,
                            { kind: 'stage1.partition', extra: null },
                          )}
                          disabled={run.isPending || !actor.trim() || !reason.trim()}
                          disabledReason={
                            run.isPending
                              ? '作业正在运行'
                              : '人工批准必须填写自报身份 actor 与理由 reason'
                          }
                          onClick={() => run.mutate({ report: current, segments: source })}
                        >
                          {run.isPending
                            ? '保存、批准并切分中…'
                            : approved
                              ? dirty
                                ? '保存修改并重新切分'
                                : '重新切分 stage1'
                              : '保存选择并进入 stage1'}
                        </ActionButton>
                      </div>
                    </Panel>

                    <Panel className="grid grid-cols-[220px_minmax(0,1fr)] gap-10 px-18 py-14">
                      <label className="flex flex-col gap-4 text-body font-semibold">
                        自报身份 actor
                        <input
                          value={actor}
                          onChange={(event) => setActor(event.target.value)}
                          placeholder="例如：reviewer-a"
                          className="rounded-btn border border-line-btn bg-card px-10 py-7 font-mono text-body font-normal"
                        />
                      </label>
                      <label className="flex flex-col gap-4 text-body font-semibold">
                        本次批准 / 编辑理由 reason
                        <input
                          value={reason}
                          onChange={(event) => setReason(event.target.value)}
                          placeholder="说明核对了什么，以及为什么接受本次正文"
                          className="rounded-btn border border-line-btn bg-card px-10 py-7 text-body font-normal"
                        />
                      </label>
                      <p className="prose-cn col-span-2 m-0 text-tag text-ink-4">
                        actor 是操作者自报身份，仅用于可追溯审计；它不是登录认证身份。
                      </p>
                    </Panel>

                    {run.isError ? (
                      <Panel className={'border-warn-line bg-warn-bg px-18 py-14'}>
                        <div className={'text-body font-semibold text-warn'}>没有进入 Stage 1</div>
                        <div className={'prose-cn mt-5 text-body text-ink-2'}>
                          {errorBody(run.error)}
                        </div>
                        <div className={'mt-4 text-tag text-ink-4'}>
                          作业抽屉已展开，可查看子进程日志。
                        </div>
                      </Panel>
                    ) : null}

                    <div
                      className={'grid items-start gap-16'}
                      style={{ gridTemplateColumns: '300px minmax(0,1fr)' }}
                    >
                      <Panel className={'sticky top-[60px]'}>
                        <PaneHeader title={'删除账'} meta={removed.length + ' 段'} />
                        {removed.length ? (
                          removed.map((item) => (
                            <button
                              key={item.id}
                              type={'button'}
                              disabled={run.isPending}
                              onClick={() =>
                                setSelection((old) => {
                                  const next = new Set(old ?? initial)
                                  next.delete(item.id)
                                  return next
                                })
                              }
                              className={
                                'flex w-full cursor-pointer flex-col gap-3 border-0 border-b ' +
                                'border-line-row bg-spend-bg px-14 py-10 text-left'
                              }
                            >
                              <span className={'text-body font-semibold'}>
                                {item.removed_kind ?? '人工选择'} · {item.chars} 字
                              </span>
                              <span className={'text-tag text-ink-4'}>
                                {item.marker_line ?? '点击可取消删除'}
                              </span>
                            </button>
                          ))
                        ) : (
                          <p className={'prose-cn m-0 px-14 py-14 text-body text-ink-4'}>
                            当前不删除任何正文。
                          </p>
                        )}
                      </Panel>

                      <Panel>
                        <PaneHeader
                          title={onlySuspicious ? '候选 / warning 审阅' : '完整原文'}
                          meta={
                            onlySuspicious
                              ? suspicious.length + ' 个候选分区 · ' + current.warnings.length + ' 条 warning'
                              : source.segments.length + ' 个连续分区 · 视窗虚拟化'
                          }
                          right={
                            <span className="flex items-center gap-8">
                              <span className={'text-tag text-ink-4'}>
                                红底 = 将删除 · 虚线 = 结尾标记
                              </span>
                              <button
                                type="button"
                                onClick={() => setOnlySuspicious((value) => !value)}
                                className="cursor-pointer rounded-btn border border-line-btn bg-card px-10 py-5 text-tag font-semibold text-ink-2"
                              >
                                {onlySuspicious ? '查看完整原文' : '只看候选 / warning'}
                              </button>
                            </span>
                          }
                        />
                        {onlySuspicious && !suspicious.length ? (
                          <div className="px-18 py-22 text-body text-ink-3">
                            没有删除候选
                            {current.warnings.length
                              ? '；上方仍有 ' + current.warnings.length + ' 条 warning，请先处理。'
                              : '，也没有 warning。无需滑完整本书来确认空结果。'}
                          </div>
                        ) : (
                          <div
                            ref={segmentScroll}
                            data-testid="stage0-segment-viewport"
                            className="h-[640px] overflow-y-auto whitespace-pre-wrap"
                          >
                            <div
                              className="relative w-full"
                              style={{ height: segmentVirtualizer.getTotalSize() }}
                            >
                            {segmentVirtualizer.getVirtualItems().map((virtualRow) => {
                              const item = filteredSegments[virtualRow.index]
                              if (!item) return null
                              const isRemoved = picked.has(item.id)
                              const locked = run.isPending || item.kind === 'end_marker'
                              return (
                                <button
                                  key={item.id}
                                  ref={segmentVirtualizer.measureElement}
                                  data-index={virtualRow.index}
                                  data-segment-id={item.id}
                                  type={'button'}
                                  disabled={locked}
                                  title={
                                    item.kind === 'end_marker'
                                      ? '结尾标记不能删除'
                                      : isRemoved
                                        ? '取消删除这一段'
                                        : '删除这一段'
                                  }
                                  onClick={() =>
                                    setSelection((old) => {
                                      const next = new Set(old ?? initial)
                                      if (next.has(item.id)) next.delete(item.id)
                                      else next.add(item.id)
                                      return next
                                    })
                                  }
                                  className={cx(
                                    'absolute top-0 left-0 m-0 w-full border-0 border-b px-18 py-11 text-left font-sans ' +
                                      'text-read leading-[2.05] whitespace-pre-wrap',
                                    item.kind === 'end_marker'
                                      ? 'cursor-not-allowed border-dashed border-line-btn bg-inset text-ink-4'
                                      : isRemoved
                                        ? 'cursor-pointer border-line-row bg-spend-bg text-ink'
                                        : 'cursor-pointer border-line-row bg-card text-ink hover:bg-sunken-2',
                                  )}
                                  style={{ transform: `translateY(${virtualRow.start}px)` }}
                                >
                                  {item.text}
                                </button>
                              )
                            })}
                            </div>
                          </div>
                        )}
                      </Panel>
                    </div>
                  </>
                )}
              </section>
            )
          }}
        </QueryStates>
      )}
    </QueryStates>
  )
}
