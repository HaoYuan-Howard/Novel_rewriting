import { useState } from 'react'
import { Outlet, useLocation, useMatch } from 'react-router-dom'
import type { ScreenName } from '@/constants/vocabulary'
import { useChrome } from '@/hooks/useChrome'
import { useHotkeys } from '@/hooks/useHotkeys'
import { cx } from '@/lib/cx'
import { Rail } from './Rail'
import { ScreenBoundary } from './ScreenBoundary'
import { JobDrawer } from '@/components/job/JobDrawer'
import { ConfirmDialog } from '@/components/job/ConfirmDialog'

/**
 * `/n/:sid` 下现役的屏段。STR-06 切换（F8c）后旧链读屏（read/global/stage4/
 * stage5/stage7）与待办屏（inbox）已退役：它们不再是导航事实，未匹配段按
 * corpus 处理，由路由兜底重定向。
 */
const SCREENS = new Set<string>([
  'run',
  'stage0',
  'stage1',
])

/** 从路径里读出当前在哪本书的哪一屏。屏是导航事实，家在 URL 里。 */
function useWhere() {
  const m = useMatch('/n/:sid/*')
  const { pathname } = useLocation()
  if (!m?.params.sid) {
    return {
      sid: null,
      screen: (pathname === '/settings' ? 'settings' : 'corpus') as ScreenName,
    }
  }
  const parts = pathname.split('/').filter(Boolean) // n, sid, screen
  const seg = parts[2] ?? ''
  return {
    sid: m.params.sid,
    screen: (SCREENS.has(seg) ? seg : 'corpus') as ScreenName,
  }
}

/**
 * 视口 1280~2560，桌面浏览器。不做移动端、不做响应式抽屉。
 * 但要用满宽屏 —— 页面没有 max-width，多出来的宽度流进并排的两栏。
 */
export function AppShell() {
  const { sid, screen } = useWhere()
  const { drawerOpen, notices } = useChrome()
  const [railCollapsed, setRailCollapsed] = useState(false)
  useHotkeys()

  return (
    <div
      className="grid min-h-screen min-w-full bg-page transition-[grid-template-columns] duration-200 ease-out"
      style={
        {
          gridTemplateColumns: 'var(--rail-current-w) minmax(0, 1fr)',
          ['--rail-current-w' as string]: railCollapsed ? '58px' : 'var(--rail-w)',
          ['--drawer-h' as string]: drawerOpen ? '190px' : '60px',
        } as React.CSSProperties
      }
    >
      <Rail
        sid={sid}
        screen={screen}
        collapsed={railCollapsed}
        onToggle={() => setRailCollapsed((value) => !value)}
      />
      <main
        className="min-w-0 transition-[padding-bottom] duration-200 ease-out"
        style={{ paddingBottom: 'var(--drawer-h)' }}
      >
        <ScreenBoundary resetKey={(sid ?? '') + '/' + screen}>
          <Outlet />
        </ScreenBoundary>
      </main>
      <ConfirmDialog />
      <JobDrawer />
      {/* 瞬态通知槽：右下角、抽屉之上（抽屉 z-20）。写回成败的一句话反馈都落这里。 */}
      <div
        aria-live="polite"
        className="fixed right-24 z-30 flex w-[380px] flex-col gap-8"
        style={{ bottom: 'calc(var(--drawer-h) + 12px)' }}
      >
        {notices.map((n) => (
          <div
            key={n.id}
            role="status"
            className={cx(
              'rounded-card border px-14 py-10 text-body shadow-drawer',
              n.tone === 'ok'
                ? 'border-gate-ok-line bg-gate-ok-bg text-gate-ok-ink'
                : 'border-warn-line bg-warn-bg text-warn-ink',
            )}
          >
            {n.text}
          </div>
        ))}
      </div>
    </div>
  )
}
