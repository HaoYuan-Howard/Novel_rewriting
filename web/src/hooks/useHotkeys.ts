import { useEffect } from 'react'
import { useChrome } from '@/hooks/useChrome'

/**
 * 全局快捷键。不引库 —— 全部就是这一个 window keydown 监听。
 *
 * - `Esc`：确认框开着关确认框（框自己处理，见 ConfirmDialog），否则关抽屉。
 *   确认框处理过的 Esc 会 preventDefault，这里不重复关。
 * - 带 Ctrl / Alt / Meta 的组合一律不碰；焦点在输入框 / textarea / select /
 *   contentEditable 里也不碰 —— 打字的时候 Esc 也可能是输入的一部分。
 *
 * ⚠ F8c 起 `/n/:sid` 下没有带章号的屏（旧链读屏整层退役），`← / →` 翻章随同
 * 退役；将来若再出现章号屏，翻章快捷键要连它的路由守卫一起重建，不在这里留
 * 一条猜章号的后门。
 */
function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  const tag = target.tagName
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable
}

export function useHotkeys() {
  const { confirm, closeConfirm, drawerOpen, toggleDrawer } = useChrome()

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.altKey || e.metaKey || e.defaultPrevented) return
      if (e.key !== 'Escape') return
      if (isTyping(e.target)) return
      if (confirm) closeConfirm()
      else if (drawerOpen) toggleDrawer()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [confirm, closeConfirm, drawerOpen, toggleDrawer])
}
