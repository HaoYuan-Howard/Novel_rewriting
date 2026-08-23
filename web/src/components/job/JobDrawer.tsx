import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { when } from '@/constants/vocabulary'
import { useChrome } from '@/hooks/useChrome'
import { useJobLog, useJobs } from '@/hooks/useJobs'
import { useSid } from '@/hooks/useRouteParams'
import { JobStateChip } from '@/components/vocab/Badges'
import { cx } from '@/lib/cx'

/**
 * 底部常驻作业面板：左侧是全部作业，右侧是选中作业的 stdout/stderr。
 * 普通作业日志没有第二个消费者；这里若只留状态，failed 就失去唯一恢复依据。
 */
export function JobDrawer() {
  const sid = useSid()
  const { drawerOpen, toggleDrawer } = useChrome()
  const jobs = useJobs(sid)
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const list = jobs.data?.jobs ?? []
  const latest = list.length ? list[list.length - 1] : null
  const newestFirst = [...list].reverse()
  const selected = newestFirst.find((job) => job.id === selectedId) ?? latest
  const log = useJobLog(drawerOpen ? selected?.id ?? null : null)
  const logRef = useRef<HTMLDivElement>(null)
  const lineCount = log.data?.lines.length ?? 0

  useEffect(() => {
    const element = logRef.current
    if (element) element.scrollTop = element.scrollHeight
  }, [drawerOpen, lineCount, selected?.id])

  const summary = !sid
    ? '还没打开哪一本书 —— 作业是按书记的'
    : jobs.isPending
      ? '取作业列表中 …'
      : jobs.isError
        ? '取不到作业列表'
        : jobs.data?.running
          ? '1 个在跑'
          : list.length
            ? list.length + ' 条记录'
            : '没有在跑的作业 · 0 条记录'

  return (
    <section
      className="fixed right-0 bottom-0 z-20 border-t border-line-btn bg-card shadow-drawer"
      style={{ left: 'var(--rail-current-w, var(--rail-w))' }}
    >
      <button
        type="button"
        aria-expanded={drawerOpen}
        onClick={toggleDrawer}
        className="flex w-full cursor-pointer items-center gap-12 border-none bg-transparent px-24 py-12 text-left"
      >
        <span className="text-body-lg font-semibold">作业</span>
        <span className="text-body text-ink-4">{summary}</span>
        {latest ? <JobStateChip state={latest.state} /> : null}
        <span className="ml-auto text-body text-ink-4">{drawerOpen ? '▾ 收起' : '▴ 展开'}</span>
      </button>

      <div
        className={cx(
          'grid transition-[grid-template-rows] duration-200 ease-out',
          drawerOpen ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]',
        )}
      >
        <div className="min-h-0 overflow-hidden">
          <div className="border-t border-line-soft">
            <div className="flex h-[196px] min-w-0">
              <div className="w-[360px] flex-none overflow-y-auto border-r border-line-soft bg-sunken-2">
                {newestFirst.length ? newestFirst.map((job) => (
                  <button
                    key={job.id}
                    type="button"
                    aria-label={'查看“' + job.label + '”日志'}
                    aria-pressed={selected?.id === job.id}
                    onClick={() => setSelectedId(job.id)}
                    className={cx(
                      'grid w-full cursor-pointer items-center gap-10 border-0 border-b border-line-soft px-16 py-9 text-left last:border-0',
                      selected?.id === job.id ? 'bg-card' : 'bg-transparent hover:bg-inset',
                    )}
                    style={{ gridTemplateColumns: 'minmax(0,1fr) 110px' }}
                  >
                    <span className="min-w-0">
                      <span className="block truncate text-body font-semibold" title={job.label}>
                        {job.label}
                      </span>
                      <span className="text-micro text-ink-5">
                        更新于 {when(job.finished ?? job.started)}
                      </span>
                    </span>
                    <JobStateChip state={job.state} />
                  </button>
                )) : (
                  <div className="px-16 py-14 text-body text-ink-4">还没有发起过作业</div>
                )}
              </div>

              <div className="flex min-w-0 flex-1 flex-col">
                {selected ? (
                  <>
                    <div className="flex items-center gap-10 border-b border-line-soft px-18 py-8">
                      <span className="truncate text-body font-semibold">{selected.label}</span>
                      <JobStateChip state={selected.state} />
                      <span className="ml-auto font-mono text-micro text-ink-5">{selected.id}</span>
                    </div>
                    <div
                      ref={logRef}
                      aria-label={selected.label + ' 作业日志'}
                      className="min-h-0 flex-1 overflow-auto bg-sunken-2 px-18 py-10 font-mono text-mini leading-[1.8] text-ink-2"
                    >
                      {log.isError ? (
                        <div className="font-sans text-body text-warn-ink">取不到这条作业日志</div>
                      ) : log.isPending ? (
                        <div className="font-sans text-body text-ink-4">取日志中 …</div>
                      ) : log.data?.lines.length ? (
                        log.data.lines.map((line, index) =>
                          line.details ? (
                            <details key={index} className="rounded-tag border border-line-soft bg-card px-7 py-2">
                              <summary className="cursor-pointer whitespace-pre-wrap">{line.text}</summary>
                              <pre className="m-0 mt-3 overflow-x-auto border-t border-line-soft pt-3 text-micro whitespace-pre-wrap text-ink-4">
                                {JSON.stringify(line.details, null, 2)}
                              </pre>
                            </details>
                          ) : (
                            <div
                              key={index}
                              className={line.kind === 'malformed' ? 'text-warn-ink' : 'whitespace-pre-wrap'}
                              title={line.raw ?? undefined}
                            >
                              {line.text}
                            </div>
                          ),
                        )
                      ) : (
                        <div className="font-sans text-body text-ink-5">这条作业没有输出日志</div>
                      )}
                    </div>
                  </>
                ) : (
                  <div className="px-18 py-14 text-body text-ink-5">选择一条作业查看日志</div>
                )}
              </div>
            </div>
            <div className="flex items-center border-t border-line-soft px-24 py-6">
              <span className="text-micro text-ink-5">选择任一作业可查看完整 stdout / stderr</span>
              {sid ? (
                <Link
                  to={'/n/' + sid + '/run'}
                  className="ml-auto text-body font-semibold text-accent-ink no-underline"
                >
                  查看运行进度 →
                </Link>
              ) : null}
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
