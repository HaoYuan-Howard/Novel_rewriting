import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useVirtualizer } from '@tanstack/react-virtual'
import { api } from '@/api/endpoints'
import { errorBody } from '@/api/errors'
import { ErrorHint } from '@/components/state/States'
import { qk } from '@/api/queryKeys'
import { lookupAction } from '@/constants/actions'
import { useChrome } from '@/hooks/useChrome'
import { useEnums } from '@/hooks/useEnums'
import { useSid } from '@/hooks/useRouteParams'
import { QueryStates } from '@/components/state/QueryStates'
import { AgentRunDialog } from '@/components/agent/AgentRunDialog'
import { ActionButton, LinkButton } from '@/components/ui/ActionButton'
import { Mono } from '@/components/ui/Mono'
import { Panel, PaneHeader } from '@/components/ui/Panel'
import { SPEND_INK } from '@/constants/vocabulary'
import { cx } from '@/lib/cx'
import type { Job } from '@/types/jobs'
import type {
  Stage0EditRequest,
  Stage0CandidateView,
  Stage0Report,
  Stage0Segments,
} from '@/types/stages'

function sameIds(left: Set<string>, right: Set<string>): boolean {
  return left.size === right.size && [...left].every((id) => right.has(id))
}

/** 一版是怎么来的。取值面在后端，这里只负责把它显示成中文 */
const REVISION_ORIGIN_LABEL: Record<string, string> = {
  machine: '机器剔除',
  human_edit: '人工编辑',
  human_adopt: '人工采纳机器结果',
}

function requireOk(job: Job): void {
  if (job.state !== 'ok') {
    throw new Error(job.label + '没有完成：' + job.state + '。作业日志里有完整输出。')
  }
}

function requireLanded(job: Job): void {
  if (job.state !== 'ok' && job.state !== 'landed_red') {
    throw new Error(job.label + '没有落盘：' + job.state + '。作业日志里有完整输出。')
  }
}

/** 浏览器里的写动作就是人在操作；Agent/CLI 仍由调用者显式填写自己的审计身份与理由。 */
const HUMAN_ACTOR = 'human-ui'
const HUMAN_REASON = {
  edit: '前端人工调整 Stage0 删除范围',
  approve: '前端人工审校并批准 Stage0',
  keepCurrent: '前端人工保留当前 Stage0 版本',
  adoptCandidate: '前端人工采纳机器 Stage0 版本',
} as const

export function Stage0Screen() {
  const sid = useSid()
  const nav = useNavigate()
  const qc = useQueryClient()
  const { openDrawer } = useChrome()
  const enums = useEnums()
  const [selection, setSelection] = useState<Set<string> | null>(null)
  const [sourceOpen, setSourceOpen] = useState(true)
  const [agentOpen, setAgentOpen] = useState(false)
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
  const candidate = useQuery({
    queryKey: qk.stage0Candidate(sid),
    queryFn: () => api.getStage0Candidate(sid),
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
    ? report.data.revision_id + ':' + [...initial].join(',')
    : ''

  useEffect(() => {
    if (segments.data?.available) setSelection(new Set(initial))
  }, [selectionKey, segments.data?.available])

  const filteredForView = segments.data?.segments ?? []
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
  }, [sourceOpen, selectionKey, segmentVirtualizer])

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
          expected_revision_id: current.revision_id,
          removed_segment_ids: [...picked],
          actor: HUMAN_ACTOR,
          reason: HUMAN_REASON.edit,
        }
        requireOk(await api.editStage0(sid, request))
      }
      requireOk(await api.approveStage0(sid, {
        actor: HUMAN_ACTOR,
        reason: HUMAN_REASON.approve,
      }))
      // landed_red = Stage1 三份产物已经落盘，只是校验有硬红。红项审阅面就在
      // Stage1；把它挡在 Stage0 会让用户反而进不了唯一的恢复入口。
      requireLanded(await api.partitionStage1(sid))
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

  // 三选一里的两个写动作。第三支「采纳后再编辑」不是动作：adopt 之后这一屏就回到
  // 普通编辑态，照常勾选再批准即可 —— 多一个复合动作就多一条要各自恢复的路。
  const decideCandidate = useMutation({
    mutationFn: async ({ view, dismiss }: { view: Stage0CandidateView; dismiss: boolean }) => {
      const request = {
        candidate_revision_id: view.candidate.revision_id,
        expected_revision_id: view.current_revision_id,
        actor: HUMAN_ACTOR,
        reason: dismiss ? HUMAN_REASON.keepCurrent : HUMAN_REASON.adoptCandidate,
      }
      const job = dismiss
        ? await api.dismissStage0Candidate(sid, request)
        : await api.adoptStage0Candidate(sid, request)
      requireOk(job)
      return job
    },
    onSuccess: async () => {
      openDrawer()
      await Promise.all([
        qc.invalidateQueries({ queryKey: qk.stage0Report(sid) }),
        qc.invalidateQueries({ queryKey: qk.stage0Segments(sid) }),
        qc.invalidateQueries({ queryKey: qk.stage0Candidate(sid) }),
        qc.invalidateQueries({ queryKey: qk.jobs(sid) }),
        qc.invalidateQueries({ queryKey: qk.novel(sid) }),
      ])
    },
    onError: () => openDrawer(),
  })

  return (
    <>
      <QueryStates q={report} loadingLabel={'读 Stage 0 审校报告'}>
      {(current) => (
        <QueryStates q={segments} loadingLabel={'读原文连续分区'}>
          {(source) => {
            const picked = selection ?? initial
            const removed = source.segments.filter((item) => picked.has(item.id))
            const dirty = !sameIds(picked, initial)
            const removedChars = removed.reduce((sum, item) => sum + item.chars, 0)
            // ⚠ 执行资格**不在这里算**：后端 `eligibility.py` 是唯一落点，响应里的
            //   `executable` 是它的投影。此前这里自己拼
            //   `approved === 'approved' && origin === 'human'`，那是第二个落点
            //   ——后端谓词收紧时漏掉的那一处就是绕过闸门的复活口。
            const approved = current.executable
            const filteredSegments = filteredForView
            return (
              <section className={'flex flex-col gap-16 px-30 py-24'}>
                <header className={'flex items-start justify-between gap-24'}>
                  <div className={'flex flex-col gap-5'}>
                    <h1 className={'m-0 text-screen font-semibold'}>剔除非正文 · stage0</h1>
                    <p className={'prose-cn m-0 max-w-[58em] text-body text-ink-3'}>
                      点击正文分区可调整删除范围；结尾标记不可删除。
                    </p>
                  </div>
                  <div className={'flex flex-col items-end gap-2 text-right'}>
                    <Mono className={'text-tag text-ink-4'}>{current.original_filename}</Mono>
                  </div>
                </header>
                {candidate.data ? (
                  <Panel className="border-warn-line">
                    <PaneHeader
                      title="机器重跑算出了另一版"
                      meta={REVISION_ORIGIN_LABEL[candidate.data.current_origin] ?? candidate.data.current_origin}
                    />
                    <div className="flex flex-col gap-10 px-18 py-14">
                      <p className="prose-cn m-0 text-body text-ink-3">
                        正式正文仍保持不动。这两版**各自完整**——要留哪一版是整版的选择，不是逐段拼接：
                        剔除账本、告警与正文是同一团东西，只换其中一格保证不了它们互相对得上。
                      </p>
                      <div className="grid grid-cols-2 gap-1 bg-line-2">
                        {[
                          {
                            key: 'current',
                            label: '当前版（保留它）',
                            revision: candidate.data.current_revision_id,
                            chars: candidate.data.current_body_code_points,
                            removed: candidate.data.current_removed_count,
                            origin: candidate.data.current_origin,
                          },
                          {
                            key: 'candidate',
                            label: '机器新版（采纳它）',
                            revision: candidate.data.candidate.revision_id,
                            chars: candidate.data.candidate.body_code_points,
                            removed: candidate.data.candidate.removed_count,
                            origin: candidate.data.candidate.origin,
                          },
                        ].map((side) => (
                          <div key={side.key} className="bg-card px-12 py-9">
                            <div className="text-body font-semibold">{side.label}</div>
                            <Mono className="text-micro text-ink-5">rev {side.revision.slice(0, 12)}</Mono>
                            <div className="prose-cn mt-3 text-body text-ink-2">
                              正文 {side.chars} 码点 · 剔除 {side.removed} 段 ·{' '}
                              {REVISION_ORIGIN_LABEL[side.origin] ?? side.origin}
                            </div>
                          </div>
                        ))}
                      </div>
                      <details className="rounded-btn border border-line-soft bg-sunken-2 px-12 py-9">
                        <summary className="cursor-pointer text-body font-semibold">查看机器新版的完整正文</summary>
                        <pre className="m-0 mt-8 max-h-[360px] overflow-auto border-t border-line-soft pt-8 font-sans text-body whitespace-pre-wrap text-ink-2">
                          {candidate.data.candidate.clean_text}
                        </pre>
                      </details>
                      <p className="prose-cn m-0 text-tag text-ink-4">
                        想在机器新版基础上再改？先「采纳」，这一屏会回到普通编辑态，勾选后照常批准。
                      </p>
                      {decideCandidate.isError ? (
                        <div className="rounded-btn border border-warn-line bg-warn-bg px-12 py-9 text-body text-warn-ink">
                          {errorBody(decideCandidate.error)}
                          <ErrorHint error={decideCandidate.error} />
                        </div>
                      ) : null}
                      <div className="flex justify-end gap-8">
                        <button
                          type="button"
                          disabled={decideCandidate.isPending}
                          onClick={() => decideCandidate.mutate({ view: candidate.data!, dismiss: true })}
                          className="cursor-pointer rounded-btn border border-line-btn bg-card px-12 py-7 text-body font-semibold disabled:cursor-not-allowed disabled:opacity-40"
                        >
                          保留当前版
                        </button>
                        <button
                          type="button"
                          disabled={decideCandidate.isPending}
                          onClick={() => decideCandidate.mutate({ view: candidate.data!, dismiss: false })}
                          className="cursor-pointer rounded-btn bg-ink px-12 py-7 text-body font-semibold text-card disabled:cursor-not-allowed disabled:opacity-40"
                        >
                          采纳机器新版
                        </button>
                      </div>
                    </div>
                  </Panel>
                ) : null}
                {!source.available ? (
                  <Panel className={'flex flex-col gap-8 border-warn-line px-18 py-16'}>
                    <div className={'text-section font-semibold'}>原文不可审校</div>
                    <p className={'prose-cn m-0 text-body text-ink-3'}>{source.reason}</p>
                    <Mono className={'text-mini text-ink-5'}>{current.original_filename}</Mono>
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
                      {current.revision_origin !== 'machine' ? (
                        <span className={'rounded-tag bg-inset px-7 py-2 text-micro text-ink-3'}>
                          {REVISION_ORIGIN_LABEL[current.revision_origin] ?? current.revision_origin}
                        </span>
                      ) : null}
                      {current.warnings.map((warning) => (
                        <span key={warning.warning.kind} className={'text-tag text-warn'}>
                          {warning.message}
                        </span>
                      ))}
                      <div className={'ml-auto flex items-center gap-9'}>
                        {dirty ? (
                          <LinkButton onClick={() => setSelection(new Set(initial))}>恢复本轮</LinkButton>
                        ) : null}
                        <button
                          type="button"
                          disabled={dirty || run.isPending}
                          onClick={() => setAgentOpen(true)}
                          title={
                            dirty
                              ? '先保存修改或恢复本轮；Agent 只会读取已经落盘的 Stage0'
                              : '查看完整命令与授权后，让 Agent 从当前盘面代跑'
                          }
                          className="cursor-pointer rounded-btn border border-line-btn bg-card px-12 py-7 text-body font-semibold hover:border-accent-line-2 disabled:cursor-not-allowed disabled:opacity-40"
                          style={{ color: SPEND_INK }}
                        >
                          Agent 代跑
                        </button>
                        <ActionButton
                          action={lookupAction(
                            enums.data,
                            { kind: 'stage1.partition', extra: null },
                          )}
                          disabled={run.isPending}
                          disabledReason={run.isPending ? '作业正在运行' : undefined}
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

                    {run.isError ? (
                      <Panel className={'border-warn-line bg-warn-bg px-18 py-14'}>
                        <div className={'text-body font-semibold text-warn'}>没有进入 Stage 1</div>
                        <div className={'prose-cn mt-5 text-body text-ink-2'}>
                          {errorBody(run.error)}
                          <ErrorHint error={run.error} />
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
                                'flex w-full cursor-pointer flex-col gap-3 border-0 border-b border-l-5 ' +
                                'border-line-row border-l-remove bg-remove-bg px-14 py-10 text-left text-remove'
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
                          title={'完整原文'}
                          right={
                            <span className="flex items-center gap-8">
                              <span
                                role="img"
                                aria-label="warning 说明"
                                title="warning 是自动扫描发现、但不能安全自动删除的可疑内容，需要人工确认；它不会自动修改正文。"
                                className="inline-flex h-18 w-18 cursor-help items-center justify-center rounded-full border border-line-btn bg-card text-tag font-semibold text-ink-3"
                              >
                                ?
                              </span>
                              <span className={'inline-flex items-center gap-5 text-tag font-semibold text-remove'}>
                                <span
                                  aria-hidden="true"
                                  className="h-10 w-10 rounded-[2px] border border-remove-line bg-remove-bg"
                                />
                                红底 = 将删除
                              </span>
                              <span className={'text-tag text-ink-4'}>
                                虚线 = 结尾标记
                              </span>
                              <button
                                type="button"
                                onClick={() => setSourceOpen((value) => !value)}
                                className="cursor-pointer rounded-btn border border-line-btn bg-card px-10 py-5 text-tag font-semibold text-ink-2"
                              >
                                {sourceOpen ? '收起原文' : '展开原文'}
                              </button>
                            </span>
                          }
                        />
                        {sourceOpen ? (
                          <div
                            ref={segmentScroll}
                            data-testid="stage0-segment-viewport"
                            className="h-[60vh] overflow-y-auto whitespace-pre-wrap"
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
                                        ? 'cursor-pointer border-line-row border-l-remove bg-remove-bg text-remove'
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
                        ) : null}
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
      {agentOpen ? (
        <AgentRunDialog
          sid={sid}
          onClose={() => setAgentOpen(false)}
          onStarted={(startedSid) => {
            setAgentOpen(false)
            nav('/n/' + startedSid + '/run')
          }}
        />
      ) : null}
    </>
  )
}
