import { Navigate, Route, Routes, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import { AppShell } from '@/components/app/AppShell'
import { BookLayout } from '@/components/app/BookLayout'
import { CorpusScreen } from '@/screens/corpus/CorpusScreen'
import { Stage0Screen } from '@/screens/stage0/Stage0Screen'
import { Stage1Screen } from '@/screens/stage1/Stage1Screen'
import { ReadScreen } from '@/screens/read/ReadScreen'
import { GlobalScreen } from '@/screens/global/GlobalScreen'
import { StyleScreen } from '@/screens/style/StyleScreen'
import { Stage4Screen } from '@/screens/stage4/Stage4Screen'
import { DepthScreen } from '@/screens/depth/DepthScreen'
import { Stage5Screen } from '@/screens/stage5/Stage5Screen'
import { Stage6Screen } from '@/screens/stage6/Stage6Screen'
import { Stage7Screen } from '@/screens/stage7/Stage7Screen'
import { InboxScreen } from '@/screens/inbox/InboxScreen'
import { useApprovals } from '@/hooks/useApprovals'

/** 换书 / 换章都整棵重挂：屏内的选中、开关、编辑中的文本不会跟过去 */
function Keyed({ children }: { children: React.ReactNode }) {
  const { sid, o } = useParams<{ sid: string; o: string }>()
  return <div key={(sid ?? '') + '/' + (o ?? '')}>{children}</div>
}

/** 还没开写就先去挑深度章节。跳转是真的跳转，不是同一条路由下的条件渲染。 */
function Stage5Guard() {
  const { sid = '', o = '' } = useParams<{ sid: string; o: string }>()
  const { doc, isLoading } = useApprovals()
  const ordinal = Number(o)
  const existing = useQuery({
    queryKey: qk.stage5(sid, ordinal),
    queryFn: () => api.getStage5(sid, ordinal),
    enabled: !!sid && Number.isInteger(ordinal) && ordinal > 0,
    retry: false,
  })

  if (!Number.isInteger(ordinal) || ordinal < 1) {
    return <Navigate to={'/n/' + sid + '/depth'} replace />
  }
  if (isLoading || existing.isPending) {
    return <div className="px-30 py-24 text-body text-ink-4">确认这一章是否已有成稿 …</div>
  }

  // 旧流程跑出的真实成稿可能早于 ApprovalDoc。已有稿只读可看，不能被新流程记录挡掉。
  const hasDraft = !!existing.data && (existing.data.written > 0 || existing.data.drafts.length > 0)
  if (doc && !doc.writing && !hasDraft && !existing.isError) {
    return <Navigate to={'/n/' + sid + '/depth'} replace />
  }
  return (
    <Keyed>
      <Stage5Screen />
    </Keyed>
  )
}

function Stage5IndexRedirect() {
  const { sid = '' } = useParams<{ sid: string }>()
  return <Navigate to={'/n/' + sid + '/depth'} replace />
}

/** 老路由（brief/04_delivery.md 的固定表）保留为重定向，URL 一条都没丢 */
function RedirectChapter({ to }: { to: string }) {
  const { o } = useParams<{ o: string }>()
  return <Navigate to={'../../' + to + '/' + o} replace relative="path" />
}

export function AppRoutes() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route path="/" element={<CorpusScreen />} />

        <Route path="/n/:sid" element={<BookLayout />}>
          <Route index element={<Navigate to="stage0" replace />} />

          <Route path="stage0" element={<Stage0Screen />} />
          <Route path="stage1" element={<Stage1Screen />} />
          <Route path="global" element={<GlobalScreen />} />
          <Route path="style" element={<StyleScreen />} />
          <Route path="depth" element={<DepthScreen />} />
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
