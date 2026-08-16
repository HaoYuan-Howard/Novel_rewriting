import { JOB, when, type JobState } from '@/constants/vocabulary'
import { useChrome } from '@/hooks/useChrome'
import { useJobLog, useJobs } from '@/hooks/useJobs'
import { useSid } from '@/hooks/useRouteParams'
import { JobStateChip } from '@/components/vocab/Badges'
import { cx } from '@/lib/cx'

const ALL_STATES: JobState[] = ['running', 'ok', 'landed_red', 'failed', 'unknown']

/**
 * 底部常驻作业面板。
 *
 * 四态里 `unknown` 是重点：服务重启后原本 running 的一律改判 unknown，不是 ok。
 * 在 mock 里，刷新页面就是那次「重启」——这条路径是真的会走到的。
 */
export function JobDrawer() {
  const sid = useSid()
  const { drawerOpen, toggleDrawer } = useChrome()
  const jobs = useJobs(sid)

  const list = jobs.data?.jobs ?? []
  const latest = list.length ? list[list.length - 1] : null
  const log = useJobLog(latest?.id ?? null)

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
      style={{ left: 'var(--rail-w)' }}
    >
      <button
        type="button"
        onClick={toggleDrawer}
        className="flex w-full cursor-pointer items-center gap-12 border-none bg-transparent px-24 py-12 text-left"
      >
        <span className="text-body-lg font-semibold">作业</span>
        <span className="text-body text-ink-4">{summary}</span>
        {latest ? <JobStateChip state={latest.state} /> : null}
        <span className="ml-auto text-body text-ink-4">{drawerOpen ? '▾ 收起' : '▴ 展开'}</span>
      </button>

      {drawerOpen ? (
        <div className="flex h-[130px] gap-0 border-t border-line-soft">
          <div className="flex w-[300px] flex-none flex-col gap-8 border-r border-line-soft px-24 py-12">
            {latest ? (
              <>
                <div className="truncate text-body font-semibold" title={latest.label}>
                  {latest.label}
                </div>
                <div className="font-mono text-mini break-all text-ink-4">{latest.argv}</div>
                <div className="text-micro text-ink-5">起于 {when(latest.started)}</div>
              </>
            ) : (
              <>
                <div className="text-body text-ink-3">还没有发起过作业</div>
                <div className="prose-cn text-micro text-ink-5">
                  这是空态，不是出错。发起一个作业之后，日志会实时流在右边。
                </div>
                <div className="mt-4 flex flex-wrap gap-8">
                  {ALL_STATES.map((s) => (
                    <span key={s} className="inline-flex items-center gap-6 text-micro text-ink-4">
                      <span
                        aria-hidden
                        className={cx('inline-block h-8 w-8 flex-none')}
                        style={
                          JOB[s].shape === 'circle'
                            ? { borderRadius: '50%', background: JOB[s].color }
                            : JOB[s].shape === 'square'
                              ? { borderRadius: 1.5, background: JOB[s].color }
                              : {
                                  borderRadius: '50%',
                                  border: '1.5px dashed ' + JOB[s].color,
                                  boxSizing: 'border-box',
                                }
                        }
                      />
                      {JOB[s].label}
                    </span>
                  ))}
                </div>
              </>
            )}
          </div>

          <div className="min-w-0 flex-1 overflow-y-auto bg-sunken-2 px-24 py-12">
            {latest ? (
              <div className="flex flex-col gap-2 font-mono text-mini leading-[1.9] text-ink-2">
                {(log.data?.lines ?? []).map((line, index) =>
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
                )}
              </div>
            ) : (
              <div className="text-mini text-ink-5">—</div>
            )}
          </div>
        </div>
      ) : null}
    </section>
  )
}
