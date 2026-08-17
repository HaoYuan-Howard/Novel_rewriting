import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ChapterStrip, type ChapterStripItem } from './ChapterStrip'

/** 章号条：当前章带 aria-current；「第 N 章」输入合法值回车跳转，非法值不跳。 */

function items(active: number, go: (n: number) => void, count = 5): ChapterStripItem[] {
  return Array.from({ length: count }, (_, i) => {
    const n = i + 1
    return { n, active: n === active, dim: false, go: () => go(n) }
  })
}

describe('ChapterStrip', () => {
  it('当前章带 aria-current，其余章不带', () => {
    render(<ChapterStrip items={items(3, () => {})} />)

    expect(screen.getByRole('button', { name: '3' })).toHaveAttribute('aria-current', 'true')
    expect(screen.getByRole('button', { name: '2' })).not.toHaveAttribute('aria-current')
  })

  it('输入合法章号回车跳转', () => {
    const go = vi.fn()
    render(<ChapterStrip items={items(1, go)} />)

    const box = screen.getByRole('textbox', { name: '跳到第几章' })
    fireEvent.change(box, { target: { value: '5' } })
    fireEvent.keyDown(box, { key: 'Enter' })
    expect(go).toHaveBeenCalledWith(5)
  })

  it('非法值不跳', () => {
    const go = vi.fn()
    render(<ChapterStrip items={items(1, go)} />)

    const box = screen.getByRole('textbox', { name: '跳到第几章' })
    fireEvent.change(box, { target: { value: '99' } })
    fireEvent.keyDown(box, { key: 'Enter' })
    fireEvent.change(box, { target: { value: 'abc' } })
    fireEvent.keyDown(box, { key: 'Enter' })
    expect(go).not.toHaveBeenCalled()
  })

  it('第 1 章时「上一章」禁用，最后一章时「下一章」禁用', () => {
    const go = vi.fn()
    const { unmount } = render(<ChapterStrip items={items(1, go)} />)
    expect(screen.getByRole('button', { name: '上一章' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '下一章' })).toBeEnabled()
    unmount()

    render(<ChapterStrip items={items(5, go)} />)
    expect(screen.getByRole('button', { name: '下一章' })).toBeDisabled()
  })
})
