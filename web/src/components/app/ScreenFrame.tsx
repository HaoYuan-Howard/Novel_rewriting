import type { ReactNode } from 'react'
import type { ScreenName } from '@/constants/vocabulary'
import { CheckPanel } from '@/components/check/CheckPanel'
import { GateBar } from '@/components/gate/GateBar'

/**
 * 默认顺序是屏体 → 校验面板 → 闸门，免得普通页面漏掉其中一块。
 * 需要把二者合并进屏内关键位置的页面（目前是 Stage1）显式关闭默认挂载，
 * 再复用同一组组件的 embedded 形态；结论与闸门仍只有一个实现。
 */
export function ScreenFrame({
  screen,
  children,
  onJumpChapter,
  showCheck = true,
  showGate = true,
  allowCheckRerun = true,
}: {
  screen: ScreenName
  children: ReactNode
  onJumpChapter?: (n: number) => void
  showCheck?: boolean
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
      {showGate ? <GateBar screen={screen} /> : null}
    </>
  )
}
