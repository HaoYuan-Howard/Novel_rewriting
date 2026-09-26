import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import { RAIL, type ScreenName } from '@/constants/vocabulary'
import { cx } from '@/lib/cx'

/**
 * 左侧常驻阶段导航。
 *
 * STR-06 切换（F8c）后只剩进度、Stage0、Stage1 三个 `/n/:sid` 屏：
 * 新链 Stage2/4/5/7 的只读监控在 `/str06/:candidate`（按登记不进左导航），
 * 人工闸词汇与「锁定/已确认」徽标随人工逐 Stage 模式整体退役——
 * 导航不再从 gate 投影推导可达性，只呈现路由事实。
 */
export function Rail({
  sid,
  screen,
  collapsed,
  onToggle,
}: {
  sid: string | null
  screen: ScreenName | null
  collapsed: boolean
  onToggle: () => void
}) {
  const nav = useNavigate()
  const overview = useQuery({
    queryKey: qk.novel(sid ?? ''),
    queryFn: () => api.getNovel(sid!),
    enabled: !!sid,
    retry: false,
  })

  const inBook = !!sid

  return (
    <nav
      data-collapsed={collapsed ? 'true' : 'false'}
      className="sticky top-0 flex h-screen min-w-0 flex-col overflow-x-hidden overflow-y-auto border-r border-line bg-inset"
    >
      <div className={cx('flex gap-6 pt-14 pb-12', collapsed ? 'justify-center px-6' : 'px-16')}>
        {collapsed ? null : (
          <div className="min-w-0 flex-1 text-read font-semibold tracking-[0.02em]">
            小说重构 · 审校台
          </div>
        )}
        <button
          type="button"
          aria-label={collapsed ? '展开侧栏' : '折叠侧栏'}
          title={collapsed ? '展开侧栏' : '折叠侧栏'}
          onClick={onToggle}
          className="flex h-28 w-28 flex-none cursor-pointer items-center justify-center rounded-btn border border-line-btn bg-card text-body-lg text-ink-3 hover:text-ink"
        >
          {collapsed ? '›' : '‹'}
        </button>
      </div>
      {!collapsed && inBook && overview.data?.title ? (
        <div className="truncate px-16 pb-12 text-body text-ink-3" title={overview.data.title}>
          {overview.data.title}
        </div>
      ) : null}

      {collapsed && screen === 'corpus' ? null : (
        <div className={cx('flex flex-col gap-2', collapsed ? 'px-6' : 'px-10')}>
          <button
            type="button"
            aria-label="语料库"
            title={collapsed ? '返回语料库' : undefined}
            onClick={() => nav('/')}
            className={cx(
              'flex cursor-pointer items-center rounded-btn border-none py-9 text-left text-body-lg font-semibold',
              collapsed ? 'justify-center px-0' : 'gap-8 px-12',
              screen === 'corpus' ? 'bg-card shadow-current' : 'bg-transparent',
            )}
          >
            {collapsed ? (
              <span aria-hidden className="font-mono text-body text-ink-3">←</span>
            ) : (
              <>
                <span
                  aria-hidden
                  className={cx(
                    'h-16 w-5 rounded-nub',
                    screen === 'corpus' ? 'bg-accent' : 'bg-transparent',
                  )}
                />
                语料库
              </>
            )}
          </button>
        </div>
      )}

      {inBook ? (
        <div className={cx('mt-14 flex flex-col gap-2 pb-24', collapsed ? 'px-6' : 'px-10')}>
          {collapsed ? null : (
            <div className="px-12 pt-6 pb-8 text-micro tracking-[0.08em] text-ink-5">流水线</div>
          )}
          {/* 进度是观察屏：不进 RAIL，跑到哪一步都能看 */}
          <button
            type="button"
            aria-label="进度"
            title={collapsed ? '进度' : undefined}
            aria-current={screen === 'run' ? 'page' : undefined}
            onClick={() => nav('/n/' + sid + '/run')}
            className={cx(
              'flex cursor-pointer items-center rounded-btn border-none py-7 text-left text-body',
              collapsed ? 'justify-center px-0' : 'gap-7 px-10',
              screen === 'run'
                ? 'bg-card font-semibold text-ink shadow-current'
                : 'bg-transparent text-ink-3',
            )}
          >
            {collapsed ? null : (
              <span
                aria-hidden
                className={cx(
                  'h-14 w-4 flex-none rounded-nub',
                  screen === 'run' ? 'bg-accent' : 'bg-transparent',
                )}
              />
            )}
            <span className="w-12 flex-none text-center font-mono text-mini text-ink-5">»</span>
            {collapsed ? null : <span className="min-w-0 flex-1 truncate">进度</span>}
          </button>
          {RAIL.map((r) => {
            const active = screen === r.screen
            return (
              <button
                key={r.screen}
                type="button"
                aria-label={r.label}
                aria-current={active ? 'page' : undefined}
                onClick={() => nav('/n/' + sid + '/' + r.screen)}
                className={cx(
                  'flex w-full cursor-pointer items-center rounded-btn border-none py-7 text-left text-body',
                  collapsed ? 'justify-center px-0' : 'gap-7 px-10',
                  active ? 'bg-card font-semibold text-ink shadow-current' : 'bg-transparent text-ink-3',
                )}
              >
                {collapsed ? null : (
                  <span
                    aria-hidden
                    className={cx(
                      'h-14 w-4 flex-none rounded-nub',
                      active ? 'bg-accent' : 'bg-transparent',
                    )}
                  />
                )}
                <span className="w-12 flex-none text-center font-mono text-mini text-ink-5">{r.n}</span>
                {collapsed ? null : <span className="min-w-0 flex-1 truncate">{r.label}</span>}
              </button>
            )
          })}
        </div>
      ) : null}

      <div className={cx('mt-auto flex flex-col gap-2', collapsed ? 'px-6' : 'px-10')}>
        <button
          type="button"
          aria-label="设置"
          title={collapsed ? '设置' : undefined}
          aria-current={screen === 'settings' ? 'page' : undefined}
          onClick={() => nav('/settings')}
          className={cx(
            'flex cursor-pointer items-center rounded-btn border-none py-8 text-left text-body',
            collapsed ? 'justify-center px-0' : 'gap-8 px-12',
            screen === 'settings'
              ? 'bg-card font-semibold text-ink shadow-current'
              : 'bg-transparent text-ink-3',
          )}
        >
          <span aria-hidden className="w-12 text-center font-mono text-mini text-ink-5">⚙</span>
          {collapsed ? null : <span>设置</span>}
        </button>
      </div>

      {collapsed ? null : (
        <div className="px-16 pt-10 pb-14 text-micro text-ink-5">Esc 关闭</div>
      )}
    </nav>
  )
}
