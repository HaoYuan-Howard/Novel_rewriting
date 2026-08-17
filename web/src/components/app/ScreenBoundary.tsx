import { Component, type ErrorInfo, type ReactNode } from 'react'
import { SEV } from '@/constants/vocabulary'
import { useChrome } from '@/hooks/useChrome'
import { LinkButton } from '@/components/ui/ActionButton'

/** 复制错误文本。类组件用不了 hook，notify 经这个小组件拿。 */
function CopyErrorButton({ text }: { text: string }) {
  const { notify } = useChrome()
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text)
      notify('已复制')
    } catch {
      notify('复制失败，请手动选中错误文本复制', 'err')
    }
  }
  return <LinkButton onClick={copy}>复制</LinkButton>
}

/**
 * 一屏崩了要说「这一屏崩了」，不能变成白板。
 *
 * 白板和「这一层没有数据」在屏幕上长得一模一样 —— 而这正是这个工具最贵的那类错误：
 * 让人以为自己已经看过、已经确认过。
 */
export class ScreenBoundary extends Component<
  { children: ReactNode; resetKey?: string },
  { err: Error | null; stack: string }
> {
  state = { err: null as Error | null, stack: '' }

  static getDerivedStateFromError(err: Error) {
    return { err, stack: '' }
  }

  componentDidCatch(err: Error, info: ErrorInfo) {
    this.setState({ err, stack: (info.componentStack ?? '').split('\n').slice(0, 6).join('\n') })
  }

  componentDidUpdate(prev: { resetKey?: string }) {
    if (prev.resetKey !== this.props.resetKey && this.state.err) {
      this.setState({ err: null, stack: '' })
    }
  }

  render() {
    if (!this.state.err) return this.props.children
    const errText =
      this.state.err.name +
      ': ' +
      this.state.err.message +
      (this.state.stack ? '\n' + this.state.stack : '')
    return (
      <section className="flex flex-col gap-12 px-30 py-26">
        <div
          className="flex flex-col gap-10 rounded-card border border-dashed p-24"
          style={{ borderColor: SEV.hard.border, background: SEV.hard.wash }}
        >
          <div className="text-h2 font-semibold" style={{ color: SEV.hard.color }}>
            这一屏崩了
          </div>
          <div className="prose-cn max-w-[46em] text-body text-ink-2">
            界面出错了，这一屏什么都没渲染出来。**这不是「这一层没有数据」**——
            别把它当成已经看过。把下面这段报给前端。
          </div>
          <pre className="m-0 overflow-x-auto rounded-block border border-line bg-inset px-14 py-12 font-mono text-mini whitespace-pre-wrap text-ink-2">
            {errText}
          </pre>
          <div className="flex items-center gap-10">
            <LinkButton onClick={() => this.setState({ err: null, stack: '' })}>
              重试渲染
            </LinkButton>
            <CopyErrorButton text={errText} />
          </div>
        </div>
      </section>
    )
  }
}
