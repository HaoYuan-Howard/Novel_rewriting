import { Navigate, Route, Routes } from 'react-router-dom'
import { AppShell } from '@/components/app/AppShell'
import { BookLayout } from '@/components/app/BookLayout'
import { CorpusScreen } from '@/screens/corpus/CorpusScreen'
import { Stage0Screen } from '@/screens/stage0/Stage0Screen'
import { Stage1Screen } from '@/screens/stage1/Stage1Screen'
import { RunScreen } from '@/screens/run/RunScreen'
import { SettingsScreen } from '@/screens/settings/SettingsScreen'
import { Str06MonitorScreen } from '@/screens/str06/Str06MonitorScreen'

/**
 * 现役路由表（权威表在 `web/AGENTS.md`「路由与屏幕职责」）。
 *
 * STR-06 切换（F8c）后旧链读屏（read / global / stage4 / stage5 / stage7）、
 * 待办屏（inbox）与它们的老路由重定向一并退役：没有可重定向的目标了
 * （review→stage5、check→inbox 的目标屏都已删），未匹配路径由兜底
 * `* → /` 处理，不保留指向空气的重定向。
 */
export function AppRoutes() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route path="/" element={<CorpusScreen />} />
        <Route path="/settings" element={<SettingsScreen />} />
        {/* STR-06 候选链只读监控：隔离候选根，不在现役 Stage 导航内。 */}
        <Route path="/str06/:candidate" element={<Str06MonitorScreen />} />

        <Route path="/n/:sid" element={<BookLayout />}>
          <Route index element={<Navigate to="stage0" replace />} />
          <Route path="run" element={<RunScreen />} />

          <Route path="stage0" element={<Stage0Screen />} />
          <Route path="stage1" element={<Stage1Screen />} />
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
