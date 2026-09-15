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
import { ActionButton } from '@/components/ui/ActionButton'
import { Mono } from '@/components/ui/Mono'
import { Panel, PaneHeader } from '@/components/ui/Panel'
import { SPEND_INK } from '@/constants/vocabulary'
import { cx } from '@/lib/cx'
import type { Job } from '@/types/jobs'

/** 一版是怎么来的。取值面在后端，这里只负责把它显示成中文。
 *  ⚠ `human_edit` / `human_adopt` 只在存量书（2026-09-15 全自动裁决之前）出现。 */
const REVISION_ORIGIN_LABEL: Record<string, string> = {
  machine: '机器剔除',
  human_edit: '人工编辑',
  human_adopt: '人工采纳机器结果',
}

function requireLanded(job: Job): void {
  if (job.state !== 'ok' && job.state !== 'landed_red') {
    throw new Error(job.label + '没有落盘：' + job.state + '。作业日志里有完整输出。')
  }
}

export function Stage0Screen() {
  const sid = useSid()
  const nav = useNavigate()
  const qc = useQueryClient()
  const { openDrawer } = useChrome()
  const enums = useEnums()
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

  // 阅读序固定为服务端给出的分区；stage0 人工重选已随 2026-09-15 全自动裁决退役。
  const view = segments.data?.segments ?? []
  const segmentVirtualizer = useVirtualizer({
    count: view.length,
    getScrollElement: () => segmentScroll.current,
    estimateSize: () => 84,
    getItemKey: (index) => view[index]?.id ?? index,
    overscan: 8,
    initialRect: { width: 1000, height: 640 },
  })

  useEffect(() => {
    segmentVirtualizer.scrollToOffset(0)
  }, [sourceOpen, segmentVirtualizer])

  const removed = useMemo(
    () => view.filter((item) => item.kind === 'remove'),
    [view],
  )
  const removedChars = removed.reduce((sum, item) => sum + item.chars, 0)

  const run = useMutation({
    mutationFn: async () => {
      // 机械校验通过即自动接受（system:stage0-auto）：没有编辑/批准两步，
      // stage1.partition 直接可跑。landed_red = Stage1 两份材料已经落盘，只是
      // 校验有硬红。红项审阅面就在 Stage1；把它挡在 Stage0 会让用户反而
      // 进不了唯一的恢复入口。
      requireLanded(await api.partitionStage1(sid))
    },
    onSuccess: async () => {
      openDrawer()
      await Promise.all([
        qc.invalidateQueries({ queryKey: qk.stage0Report(sid) }),
        qc.invalidateQueries({ queryKey: qk.stage0Segments(sid) }),
        qc.invalidateQueries({ queryKey: qk.stage1(sid) }),
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

  return (
    <>
      <QueryStates q={report} loadingLabel={'读 Stage 0 审校报告'}>
      {(current) => (
        <QueryStates q={segments} loadingLabel={'读原文连续分区'}>
          {(source) => {
            // ⚠ 执行资格**不在这里算**：后端 `eligibility.py` 是唯一落点，响应里的
            //   `executable` 是它的投影。
            const executable = current.executable
            return (
              <section className={'flex flex-col gap-16 px-30 py-24'}>
                <header className={'flex items-start justify-between gap-24'}>
                  <div className={'flex flex-col gap-5'}>
                    <h1 className={'m-0 text-screen font-semibold'}>剔除非正文 · stage0</h1>
                    <p className={'prose-cn m-0 max-w-[58em] text-body text-ink-3'}>
                      机器剔除结果只读展示；机械校验通过即自动接受，Stage1 直接可跑。
                    </p>
                  </div>
                  <div className={'flex flex-col items-end gap-2 text-right'}>
                    <Mono className={'text-tag text-ink-4'}>{current.original_filename}</Mono>
                  </div>
                </header>
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
                          executable ? 'bg-gate-ok-bg text-ink-2' : 'bg-warn-bg text-warn',
                        )}
                      >
                        {executable ? '机械校验通过 · 已自动接受' : '存量稿待重跑 stage0.remove'}
                      </span>
                      <span className={'text-body font-semibold'}>
                        机器剔除 {removed.length} 段 · {removedChars} 码点
                      </span>
                      <span className={'text-tag text-ink-4'}>
                        原文 {current.total_code_points} 码点 · 剔除后正文{' '}
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
                        <button
                          type="button"
                          disabled={run.isPending}
                          onClick={() => setAgentOpen(true)}
                          title="查看完整命令与授权后，让 Agent 从当前盘面代跑"
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
                          onClick={() => run.mutate()}
                        >
                          {run.isPending ? '切分中…' : '切分并进入 stage1'}
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
                        <PaneHeader title={'机器剔除账'} meta={removed.length + ' 段'} />
                        {removed.length ? (
                          removed.map((item) => (
                            <div
                              key={item.id}
                              className={
                                'flex w-full flex-col gap-3 border-0 border-b border-l-5 ' +
                                'border-line-row border-l-remove bg-remove-bg px-14 py-10 text-remove'
                              }
                            >
                              <span className={'text-body font-semibold'}>
                                {item.removed_kind ?? '机器剔除'} · {item.chars} 字
                              </span>
                              <span className={'text-tag text-ink-4'}>
                                {item.marker_line ?? '机器标记'}
                              </span>
                            </div>
                          ))
                        ) : (
                          <p className={'prose-cn m-0 px-14 py-14 text-body text-ink-4'}>
                            机器没有剔除任何正文。
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
                                title="warning 是自动扫描发现的可疑内容；它不会自动修改正文。"
                                className="inline-flex h-18 w-18 cursor-help items-center justify-center rounded-full border border-line-btn bg-card text-tag font-semibold text-ink-3"
                              >
                                ?
                              </span>
                              <span className={'inline-flex items-center gap-5 text-tag font-semibold text-remove'}>
                                <span
                                  aria-hidden="true"
                                  className="h-10 w-10 rounded-[2px] border border-remove-line bg-remove-bg"
                                />
                                红底 = 机器已剔除
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
                              const item = view[virtualRow.index]
                              if (!item) return null
                              const isRemoved = item.kind === 'remove'
                              return (
                                <div
                                  key={item.id}
                                  ref={segmentVirtualizer.measureElement}
                                  data-index={virtualRow.index}
                                  data-segment-id={item.id}
                                  title={
                                    item.kind === 'end_marker'
                                      ? '结尾标记'
                                      : isRemoved
                                        ? '机器已剔除这一段'
                                        : '正文'
                                  }
                                  className={cx(
                                    'absolute top-0 left-0 m-0 w-full border-0 border-b px-18 py-11 text-left font-sans ' +
                                      'text-read leading-[2.05] whitespace-pre-wrap',
                                    item.kind === 'end_marker'
                                      ? 'border-dashed border-line-btn bg-inset text-ink-4'
                                      : isRemoved
                                        ? 'border-line-row border-l-remove bg-remove-bg text-remove'
                                        : 'border-line-row bg-card text-ink',
                                  )}
                                  style={{ transform: `translateY(${virtualRow.start}px)` }}
                                >
                                  {item.text}
                                </div>
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
