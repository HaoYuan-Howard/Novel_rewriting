import { useEffect, type ReactNode } from 'react'

const ACTOR_KEY = 'nr.actor'

/**
 * 自报身份 actor + 理由 reason 两连输入，stage0 / stage6 / stage7 / global 共用一份。
 *
 * actor 经 sessionStorage 记住上一次（key `nr.actor`）：挂载时父 state 为空就回填
 * （同时调 onActor，保证提交时带得上），每次改动也写回去。
 * 记住的只是自报身份、方便追溯审计——它不是认证，这句话必须跟着输入框走。
 */
export function ActorReasonFields({
  actor,
  reason,
  onActor,
  onReason,
  reasonPlaceholder,
  note,
  actions,
}: {
  actor: string
  /** 不传 onReason 时只渲染 actor 一栏（global 复核的理由是逐条决定的，不在这里） */
  reason?: string
  onActor: (v: string) => void
  onReason?: (v: string) => void
  reasonPlaceholder?: string
  /** 各屏自己的审计说明（这次定稿绑定哪份 SHA-256 之类），跟在固定说明后面 */
  note?: ReactNode
  /** 依赖 actor/reason 的按钮放进表单卡片里，别和输入隔着一个 header */
  actions?: ReactNode
}) {
  // 只在挂载时回填一次：父 state 里已有的 actor 不覆盖
  useEffect(() => {
    if (actor.trim()) return
    let saved = ''
    try {
      saved = sessionStorage.getItem(ACTOR_KEY) ?? ''
    } catch {
      return
    }
    if (saved) onActor(saved)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const changeActor = (v: string) => {
    onActor(v)
    try {
      sessionStorage.setItem(ACTOR_KEY, v)
    } catch {
      // 隐私模式写不进去就算了，输入本身不受影响
    }
  }

  return (
    <div className="grid gap-10 rounded-card border border-line bg-sunken-2 p-14 md:grid-cols-2">
      <label className="flex flex-col gap-5 text-tag text-ink-3">
        自报身份 actor
        <input
          value={actor}
          onChange={(event) => changeActor(event.target.value)}
          placeholder="例如：reviewer-a"
          className="rounded-btn border border-line-btn bg-card px-10 py-8 font-mono text-body font-normal text-ink"
        />
      </label>
      {onReason ? (
        <label className="flex flex-col gap-5 text-tag text-ink-3">
          本次批准 / 编辑理由 reason
          <input
            value={reason ?? ''}
            onChange={(event) => onReason(event.target.value)}
            placeholder={reasonPlaceholder ?? '说明核对了什么，以及为什么接受本次正文'}
            className="rounded-btn border border-line-btn bg-card px-10 py-8 text-body font-normal text-ink"
          />
        </label>
      ) : null}
      <p className="prose-cn m-0 text-tag text-ink-4 md:col-span-2">
        actor 是操作者自报身份，仅用于可追溯审计；它不是登录认证身份。
      </p>
      {note ? <div className="prose-cn text-tag text-ink-4 md:col-span-2">{note}</div> : null}
      {actions ? <div className="flex items-center gap-10 md:col-span-2">{actions}</div> : null}
    </div>
  )
}
