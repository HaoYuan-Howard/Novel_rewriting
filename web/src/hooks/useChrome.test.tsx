import { useRef } from 'react'
import { act, fireEvent, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderRoute } from '@/test/render'
import { useChrome } from './useChrome'

/**
 * notice 槽（useChrome 的 notify）：写回成败的一句话反馈。
 * 守三件事：出现了、约 3s 自己消掉、最多堆 3 条（先进先出）。
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
})
