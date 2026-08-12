import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
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
import type { Stage0EditRequest, Stage0Report, Stage0Segments } from '@/types/stages'

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
        }
        requireOk(await api.editStage0(sid, request))
      }
      requireOk(await api.approveStage0(sid))
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
    <QueryStates q={report} loadingLabel={'读取 Stage 0 审校报告'}>
      {(current) => (
        <QueryStates q={segments} loadingLabel={'读取原文连续分区'}>
          {(source) => {
            const picked = selection ?? initial
            const removed = source.segments.filter((item) => picked.has(item.id))
            const dirty = !sameIds(picked, initial)
            const removedChars = removed.reduce((sum, item) => sum + item.chars, 0)
            const approved = current.approved === 'approved'
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
                          disabled={run.isPending}
                          disabledReason={'作业正在运行'}
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
                          title={'完整原文'}
                          meta={source.segments.length + ' 个连续分区 · 全部由服务端切好'}
                          right={
                            <span className={'text-tag text-ink-4'}>
                              红底 = 将删除 · 虚线 = 结尾标记
                            </span>
                          }
                        />
                        <div className={'whitespace-pre-wrap'}>
                          {source.segments.map((item) => {
                            const isRemoved = picked.has(item.id)
                            const locked = run.isPending || item.kind === 'end_marker'
                            return (
                              <button
                                key={item.id}
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
                                  'm-0 w-full border-0 border-b px-18 py-11 text-left font-sans ' +
                                    'text-read leading-[2.05] whitespace-pre-wrap',
                                  item.kind === 'end_marker'
                                    ? 'cursor-not-allowed border-dashed border-line-btn bg-inset text-ink-4'
                                    : isRemoved
                                      ? 'cursor-pointer border-line-row bg-spend-bg text-ink'
                                      : 'cursor-pointer border-line-row bg-card text-ink hover:bg-sunken-2',
                                )}
                              >
                                {item.text}
                              </button>
                            )
                          })}
                        </div>
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
