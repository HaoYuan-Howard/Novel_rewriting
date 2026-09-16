import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from 'react'
import type { Job, JobRequest } from '@/types/jobs'

/**
 * 全应用唯一的 Context。
 *
 * 里面只有三样东西：抽屉开合、待确认的动作、瞬态通知。
 * 其余「全局状态」要么在 URL 里（sid / 章号 / 当前屏），
 * 要么在服务端（20 份响应 + 验收记录 + 作业）—— 都不需要状态管理库。
 */

export type NoticeTone = 'ok' | 'err'

export interface Notice {
  id: number
  text: string
  tone: NoticeTone
}

export interface PendingConfirm {
  req: JobRequest
  /** 用户确认后、启动作业前必须成功的服务端状态转换（例如批准当前闸门） */
  before?: () => Promise<void>
  /** 发起成功后要做的事；通用作业会带回真实 job，专用同步写回传 null。 */
  after?: (job: Job | null) => void
  /**
   * 执行覆盖：历史专用写回路由已随全自动只读面退役；现役确认框若仍带 run，
   * 须先 preview 同一服务端计划（R-11.8），再跑该覆盖。Stage6 改选走 approveHook。
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
  /** 瞬态通知（右下角）：写回成功 / 失败的一句话反馈 */
  notices: Notice[]
  notify: (text: string, tone?: NoticeTone) => void
}

const Ctx = createContext<ChromeCtx | null>(null)

export function ChromeProvider({ children }: { children: ReactNode }) {
  // 阅读是主任务；没有作业时不让空抽屉抢走首屏高度。发起作业仍会自动展开。
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [confirm, setConfirm] = useState<PendingConfirm | null>(null)
  const [notices, setNotices] = useState<Notice[]>([])
  const noticeSeq = useRef(0)

  // 约 3s 自消，最多同时堆 3 条（先进先出，多的挤掉最旧那条）
  const notify = useCallback((text: string, tone: NoticeTone = 'ok') => {
    noticeSeq.current += 1
    const id = noticeSeq.current
    setNotices((prev) => [...prev, { id, text, tone }].slice(-3))
    setTimeout(() => {
      setNotices((prev) => prev.filter((n) => n.id !== id))
    }, 3000)
  }, [])

  const value = useMemo<ChromeCtx>(
    () => ({
      drawerOpen,
      toggleDrawer: () => setDrawerOpen((v) => !v),
      openDrawer: () => setDrawerOpen(true),
      confirm,
      askConfirm: (c) => setConfirm(c),
      closeConfirm: () => setConfirm(null),
      notices,
      notify,
    }),
    [drawerOpen, confirm, notices, notify],
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
    (req: JobRequest, after?: (job: Job | null) => void, before?: () => Promise<void>) =>
      askConfirm({ req, before, after }),
    [askConfirm],
  )
}
