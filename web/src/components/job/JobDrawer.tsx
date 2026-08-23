import { Link } from 'react-router-dom'
import { when } from '@/constants/vocabulary'
import { useChrome } from '@/hooks/useChrome'
import { useJobs } from '@/hooks/useJobs'
import { useSid } from '@/hooks/useRouteParams'
import { JobStateChip } from '@/components/vocab/Badges'
import { cx } from '@/lib/cx'

/**
 * 底部常驻作业面板。
 *
 * 这里只画全部作业的最新状态概览；命令行与 stdout/stderr 属于进度页，
 * 不在全局抽屉重复一份日志查看器。
 */
export function JobDrawer() {
  const sid = useSid()
  const { drawerOpen, toggleDrawer } = useChrome()
  const jobs = useJobs(sid)

  const list = jobs.data?.jobs ?? []
  const latest = list.length ? list[list.length - 1] : null
  const newestFirst = [...list].reverse()

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

      {/*
        开合平滑：0fr/1fr 网格行动画代替高度硬切（auto 高度没法 transition）。
        border-t 收进 overflow-hidden 里，收起时不留一条孤线。
        main 的 paddingBottom 由 AppShell 那边同步 transition。
      */}
      <div
        className={cx(
          'grid transition-[grid-template-rows] duration-200 ease-out',
          drawerOpen ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]',
        )}
      >
        <div className="min-h-0 overflow-hidden">
        <div className="border-t border-line-soft">
          <div className="h-[104px] overflow-y-auto bg-sunken-2">
            {newestFirst.length ? newestFirst.map((job) => (
              <div
                key={job.id}
                className="grid items-center gap-16 border-b border-line-soft px-24 py-9 last:border-0"
                style={{ gridTemplateColumns: 'minmax(0,1fr) 150px 210px' }}
              >
                <div className="truncate text-body font-semibold" title={job.label}>
                  {job.label}
                </div>
                <JobStateChip state={job.state} />
                <div className="text-right text-mini text-ink-4">
                  更新于 {when(job.finished ?? job.started)}
                </div>
              </div>
            )) : (
              <div className="px-24 py-14 text-body text-ink-4">还没有发起过作业</div>
            )}
          </div>
          <div className="flex items-center border-t border-line-soft px-24 py-6">
            <span className="text-micro text-ink-5">这里只显示全部作业的最新状态</span>
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
