import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'
import type { JobRequest } from '@/types/jobs'

/**
 * 全应用唯一的 Context。
 *
 * 里面只有两样东西：抽屉开合、待确认的动作。
 * 其余「全局状态」要么在 URL 里（sid / 章号 / 当前屏），
 * 要么在服务端（20 份响应 + 验收记录 + 作业）—— 都不需要状态管理库。
 */

export interface PendingConfirm {
  req: JobRequest
  /** 用户确认后、启动作业前必须成功的服务端状态转换（例如批准当前闸门） */
  before?: () => Promise<void>
  /** 发起成功后要做的事（例如挑完深度章节 → 标记已开写 → 跳成稿屏） */
  after?: () => void
  /**
   * 执行覆盖：三条人工写回走**专用路由**（review/:o/edit、stage6/approve、
   * stage7/approve），不走通用 POST /jobs。确认框仍然先 preview 同一个
   * (node, flags) 的服务端计划（R-11.8：显示与执行同一份），确认后跑这个。
   */
  run?: () => Promise<unknown>
}

interface ChromeCtx {
  drawerOpen: boolean
  toggleDrawer: () => void
  openDrawer: () => void
  confirm: PendingConfirm | null
  askConfirm: (c: PendingConfirm) => void
  closeConfirm: () => void
}

const Ctx = createContext<ChromeCtx | null>(null)

export function ChromeProvider({ children }: { children: ReactNode }) {
  const [drawerOpen, setDrawerOpen] = useState(true)
  const [confirm, setConfirm] = useState<PendingConfirm | null>(null)

  const value = useMemo<ChromeCtx>(
    () => ({
      drawerOpen,
      toggleDrawer: () => setDrawerOpen((v) => !v),
      openDrawer: () => setDrawerOpen(true),
      confirm,
      askConfirm: (c) => setConfirm(c),
      closeConfirm: () => setConfirm(null),
    }),
    [drawerOpen, confirm],
  )

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useChrome(): ChromeCtx {
  const c = useContext(Ctx)
  if (!c) throw new Error('useChrome 必须在 ChromeProvider 里用')
  return c
}

/** 语法糖：把一个动作挂到确认框上 */
export function useAskJob() {
  const { askConfirm } = useChrome()
  return useCallback(
    (req: JobRequest, after?: () => void, before?: () => Promise<void>) =>
      askConfirm({ req, before, after }),
    [askConfirm],
  )
}
