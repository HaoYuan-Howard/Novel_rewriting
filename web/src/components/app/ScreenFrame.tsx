import type { ReactNode } from 'react'
import type { ScreenName } from '@/constants/vocabulary'
import { CheckPanel } from '@/components/check/CheckPanel'
import { GateBar } from '@/components/gate/GateBar'

/**
 * 屏体 → 校验面板 → 闸门。顺序固定在这里，
 * 免得每一屏都要记得把这两块放到底部（漏一个就等于把这一层的结论藏了）。
 */
export function ScreenFrame({
  screen,
  children,
  onJumpChapter,
}: {
  screen: ScreenName
  children: ReactNode
  onJumpChapter?: (n: number) => void
}) {
  return (
    <>
      {children}
      <CheckPanel screen={screen} onJumpChapter={onJumpChapter} />
      <GateBar screen={screen} />
    </>
  )
}
