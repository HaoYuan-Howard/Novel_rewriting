import type { ReactNode } from 'react'
import type { ScreenName } from '@/constants/vocabulary'
import { CheckPanel } from '@/components/check/CheckPanel'

/**
 * 默认顺序是屏体 → 校验面板。闸门确认（GateBar）已随人工逐 Stage 模式退役。
 */
export function ScreenFrame({
  screen,
  children,
  onJumpChapter,
  showCheck = true,
  allowCheckRerun = true,
}: {
  screen: ScreenName
  children: ReactNode
  onJumpChapter?: (n: number) => void
  showCheck?: boolean
  /** @deprecated 闸门写面已退役；保留参数以免旧调用方类型报错。 */
  showGate?: boolean
  allowCheckRerun?: boolean
}) {
  return (
    <>
      {children}
      {showCheck ? (
        <CheckPanel
          screen={screen}
          onJumpChapter={onJumpChapter}
          allowRerun={allowCheckRerun}
        />
      ) : null}
    </>
  )
}
