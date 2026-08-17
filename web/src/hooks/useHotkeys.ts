import { useEffect } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useChrome } from '@/hooks/useChrome'

/**
 * 全局快捷键。不引库 —— 全部就是这一个 window keydown 监听。
 *
 * - `← / →`：带章号的屏（read / stage4 / stage5 / stage7）翻章，屏名保留、只动章号。
 *   下界钳在 1；上界各屏数据层自己管（跳过去会看到「没样本」或「还没跑」，不算走错）。
 * - `Esc`：确认框开着关确认框（框自己处理，见 ConfirmDialog），否则关抽屉。
 *   确认框处理过的 Esc 会 preventDefault，这里不重复关。
 * - 带 Ctrl / Alt / Meta 的组合一律不碰；焦点在输入框 / textarea / select /
 *   contentEditable 里也不碰 —— 打字的时候左右键是光标。
 */

/** URL 里有数字章号、←/→ 讲得通的屏 */
const CHAPTERED = new Set(['read', 'stage4', 'stage5', 'stage7'])

function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  const tag = target.tagName
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable
}

export function useHotkeys() {
  const nav = useNavigate()
  const { pathname } = useLocation()
  const { confirm, closeConfirm, drawerOpen, toggleDrawer } = useChrome()

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.altKey || e.metaKey || e.defaultPrevented) return
      if (e.key === 'Escape') {
        if (isTyping(e.target)) return
        if (confirm) closeConfirm()
        else if (drawerOpen) toggleDrawer()
        return
      }
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
      if (isTyping(e.target)) return
      const parts = pathname.split('/').filter(Boolean) // n, sid, screen, [o]
      if (parts[0] !== 'n' || !parts[1] || !CHAPTERED.has(parts[2] ?? '')) return
      const o = Number(parts[3])
      if (!Number.isFinite(o) || o < 1) return
      const next = e.key === 'ArrowLeft' ? o - 1 : o + 1
      if (next < 1) return
      nav('/n/' + parts[1] + '/' + parts[2] + '/' + next)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [nav, pathname, confirm, closeConfirm, drawerOpen, toggleDrawer])
}
