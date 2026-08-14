import { Outlet, useLocation, useMatch } from 'react-router-dom'
import type { ScreenName } from '@/constants/vocabulary'
import { useChrome } from '@/hooks/useChrome'
import { Rail } from './Rail'
import { ScreenBoundary } from './ScreenBoundary'
import { JobDrawer } from '@/components/job/JobDrawer'
import { ConfirmDialog } from '@/components/job/ConfirmDialog'

const SCREENS = new Set<string>([
  'stage0',
  'stage1',
  'read',
  'global',
  'style',
  'stage4',
  'depth',
  'stage5',
  'stage6',
  'stage7',
  'inbox',
])

/** 从路径里读出当前在哪本书的哪一屏。屏与章号是导航事实，家在 URL 里。 */
function useWhere() {
  const m = useMatch('/n/:sid/*')
  const { pathname } = useLocation()
  if (!m?.params.sid) return { sid: null, screen: 'corpus' as ScreenName, ordinal: null }
  const parts = pathname.split('/').filter(Boolean) // n, sid, screen, [o]
  const seg = parts[2] ?? ''
  const o = Number(parts[3])
  return {
    sid: m.params.sid,
    screen: (SCREENS.has(seg) ? seg : 'corpus') as ScreenName,
    ordinal: Number.isFinite(o) && o > 0 ? o : null,
  }
}

/**
 * 视口 1280~2560，桌面浏览器。不做移动端、不做响应式抽屉。
 * 但要用满宽屏 —— 页面没有 max-width，多出来的宽度流进并排的两栏。
 */
export function AppShell() {
  const { sid, screen, ordinal } = useWhere()
  const { drawerOpen } = useChrome()

  return (
    <div
      className="grid min-h-screen min-w-full bg-page"
      style={
        {
          gridTemplateColumns: 'var(--rail-w) 1fr',
          ['--drawer-h' as string]: drawerOpen ? '190px' : '60px',
        } as React.CSSProperties
      }
    >
      <Rail sid={sid} screen={screen} ordinal={ordinal} />
      <main className="min-w-0" style={{ paddingBottom: 'var(--drawer-h)' }}>
        <ScreenBoundary resetKey={(sid ?? '') + '/' + screen + '/' + (ordinal ?? '')}>
          <Outlet />
        </ScreenBoundary>
      </main>
      <ConfirmDialog />
      <JobDrawer />
    </div>
  )
}
