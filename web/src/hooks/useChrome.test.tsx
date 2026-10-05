import { StrictMode, useEffect, useRef } from 'react'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderRoute } from '@/test/render'
import { ChromeProvider, useChrome } from './useChrome'

/**
 * notice 槽（useChrome 的 notify）：写回成败的一句话反馈。
 * 守出现、自消、最多堆 3 条，以及 Provider 卸载和 StrictMode 重放时的定时器寿命。
 * 渲染那一层只是 AppShell 对 notices 的 map，逻辑全在 Provider 里，所以这里直接探 Context。
 */

function Probe() {
  const { notices, notify } = useChrome()
  const seq = useRef(0)
  return (
    <div>
      <button
        type="button"
        onClick={() => {
          seq.current += 1
          notify('第 ' + seq.current + ' 条')
        }}
      >
        发一条
      </button>
      <ul>
        {notices.map((n) => (
          <li key={n.id}>{n.text}</li>
        ))}
      </ul>
    </div>
  )
}

function MountNotice() {
  const { notify } = useChrome()
  useEffect(() => {
    notify('挂载通知')
  }, [notify])
  return null
}

afterEach(() => {
  vi.useRealTimers()
})

describe('notice 槽', () => {
  it('notify 后出现文字，约 3s 自动消失', () => {
    vi.useFakeTimers()
    renderRoute(<Probe />)

    fireEvent.click(screen.getByRole('button', { name: '发一条' }))
    expect(screen.getByText('第 1 条')).toBeInTheDocument()

    act(() => {
      vi.advanceTimersByTime(3100)
    })
    expect(screen.queryByText('第 1 条')).not.toBeInTheDocument()
  })

  it('最多同时堆 3 条，多的挤掉最旧那条', () => {
    vi.useFakeTimers()
    renderRoute(<Probe />)
    const btn = screen.getByRole('button', { name: '发一条' })

    for (let i = 0; i < 4; i += 1) fireEvent.click(btn)

    expect(screen.queryByText('第 1 条')).not.toBeInTheDocument()
    expect(screen.getByText('第 2 条')).toBeInTheDocument()
    expect(screen.getByText('第 3 条')).toBeInTheDocument()
    expect(screen.getByText('第 4 条')).toBeInTheDocument()
  })

  it('卸载 Provider 后清空通知定时器，3s 回调不再留在已销毁的环境里', () => {
    vi.useFakeTimers()
    const view = render(<ChromeProvider><Probe /></ChromeProvider>)
    fireEvent.click(screen.getByRole('button', { name: '发一条' }))
    expect(vi.getTimerCount()).toBeGreaterThan(0)

    view.unmount()

    expect(vi.getTimerCount()).toBe(0)
    act(() => vi.advanceTimersByTime(3000))
    expect(vi.getTimerCount()).toBe(0)
  })

  it('后来通知不延长先前通知的 3s 寿命', () => {
    vi.useFakeTimers()
    render(<ChromeProvider><Probe /></ChromeProvider>)
    const button = screen.getByRole('button', { name: '发一条' })
    fireEvent.click(button)
    act(() => vi.advanceTimersByTime(2000))
    fireEvent.click(button)

    act(() => vi.advanceTimersByTime(1000))
    expect(screen.queryByText('第 1 条')).not.toBeInTheDocument()
    expect(screen.getByText('第 2 条')).toBeInTheDocument()
    act(() => vi.advanceTimersByTime(2000))
    expect(screen.queryByText('第 2 条')).not.toBeInTheDocument()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('StrictMode 效应重放后，挂载与后续通知都按自己的期限消失', () => {
    vi.useFakeTimers()
    render(<StrictMode><ChromeProvider><MountNotice /><Probe /></ChromeProvider></StrictMode>)
    expect(screen.getAllByText('挂载通知').length).toBeGreaterThan(0)
    act(() => vi.advanceTimersByTime(1000))
    fireEvent.click(screen.getByRole('button', { name: '发一条' }))

    act(() => vi.advanceTimersByTime(2000))
    expect(screen.queryByText('挂载通知')).not.toBeInTheDocument()
    expect(screen.getByText('第 1 条')).toBeInTheDocument()
    act(() => vi.advanceTimersByTime(1000))
    expect(screen.queryByText('第 1 条')).not.toBeInTheDocument()
    expect(vi.getTimerCount()).toBe(0)
  })
})
