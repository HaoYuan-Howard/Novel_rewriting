import { Navigate, Route, Routes, useParams } from 'react-router-dom'
import { AppShell } from '@/components/app/AppShell'
import { BookLayout } from '@/components/app/BookLayout'
import { CorpusScreen } from '@/screens/corpus/CorpusScreen'
import { Stage0Screen } from '@/screens/stage0/Stage0Screen'
import { Stage1Screen } from '@/screens/stage1/Stage1Screen'
import { ReadScreen } from '@/screens/read/ReadScreen'
import { GlobalScreen } from '@/screens/global/GlobalScreen'
import { Stage4Screen } from '@/screens/stage4/Stage4Screen'
import { Stage5Screen } from '@/screens/stage5/Stage5Screen'
import { Stage6Screen } from '@/screens/stage6/Stage6Screen'
import { Stage7Screen } from '@/screens/stage7/Stage7Screen'
import { InboxScreen } from '@/screens/inbox/InboxScreen'
import { RunScreen } from '@/screens/run/RunScreen'
import { SettingsScreen } from '@/screens/settings/SettingsScreen'
import { Str06MonitorScreen } from '@/screens/str06/Str06MonitorScreen'

/** 换书 / 换章都整棵重挂：屏内的选中、开关、编辑中的文本不会跟过去 */
function Keyed({ children }: { children: React.ReactNode }) {
  const { sid, o } = useParams<{ sid: string; o: string }>()
  return <div key={(sid ?? '') + '/' + (o ?? '')}>{children}</div>
}

function Stage5Guard() {
  const { sid = '', o = '' } = useParams<{ sid: string; o: string }>()
  const ordinal = Number(o)

  if (!Number.isInteger(ordinal) || ordinal < 1) {
    return <Navigate to={'/n/' + sid + '/stage5/1'} replace />
  }
  return (
    <Keyed>
      <Stage5Screen />
    </Keyed>
  )
}

function Stage5IndexRedirect() {
  const { sid = '' } = useParams<{ sid: string }>()
  return <Navigate to={'/n/' + sid + '/stage5/1'} replace />
}

/** 老路由保留为重定向；当前权威路由表在 web/AGENTS.md「路由与屏幕职责」 */
function RedirectChapter({ to }: { to: string }) {
  const { o } = useParams<{ o: string }>()
  return <Navigate to={'../../' + to + '/' + o} replace relative="path" />
}

export function AppRoutes() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route path="/" element={<CorpusScreen />} />
        <Route path="/settings" element={<SettingsScreen />} />
        {/* STR-06 候选链只读监控（F7d）：隔离候选根，不在现役 Stage 导航内。 */}
        <Route path="/str06/:candidate" element={<Str06MonitorScreen />} />

        <Route path="/n/:sid" element={<BookLayout />}>
          <Route index element={<Navigate to="stage0" replace />} />
          <Route path="run" element={<RunScreen />} />

          <Route path="stage0" element={<Stage0Screen />} />
          <Route path="stage1" element={<Stage1Screen />} />
          <Route path="global" element={<GlobalScreen />} />
          <Route path="stage6" element={<Stage6Screen />} />
          <Route path="inbox" element={<InboxScreen />} />

          <Route
            path="read/:o"
            element={
              <Keyed>
                <ReadScreen />
              </Keyed>
            }
          />
          <Route
            path="stage4/:o"
            element={
              <Keyed>
                <Stage4Screen />
              </Keyed>
            }
          />
          <Route path="stage5/:o" element={<Stage5Guard />} />
          <Route path="stage5" element={<Stage5IndexRedirect />} />

          <Route path="stage7" element={<Stage7Screen />} />
          <Route
            path="stage7/:o"
            element={
              <Keyed>
                <Stage7Screen />
              </Keyed>
            }
          />

          {/* 设计稿把这三屏并进了 stage1 / stage5 / inbox。URL 仍然可达。 */}
          <Route path="review/:o" element={<RedirectChapter to="stage5" />} />
          <Route path="check" element={<Navigate to="../inbox" replace relative="path" />} />
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
