import { fireEvent, screen } from '@testing-library/react'
import { useLocation } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { renderRoute } from '@/test/render'
import { useHotkeys } from './useHotkeys'

/**
 * 全局快捷键：← / → 在带章号的屏翻章；输入框聚焦时不劫持；
 * 第 1 章按 ← 不动（下界钳在 1）。
 */

function Probe() {
  useHotkeys()
  const { pathname } = useLocation()
  return (
    <div>
      <span data-testid="path">{pathname}</span>
      <input aria-label="随便一个输入框" />
    </div>
  )
}

describe('useHotkeys', () => {
  it('→ 从 /n/x/read/3 翻到第 4 章，← 翻回第 2 章', () => {
    renderRoute(<Probe />, '/n/x/read/3')

    fireEvent.keyDown(window, { key: 'ArrowRight' })
    expect(screen.getByTestId('path').textContent).toBe('/n/x/read/4')

    fireEvent.keyDown(window, { key: 'ArrowLeft' })
    fireEvent.keyDown(window, { key: 'ArrowLeft' })
    expect(screen.getByTestId('path').textContent).toBe('/n/x/read/2')
  })

  it('输入框聚焦时不翻章', () => {
    renderRoute(<Probe />, '/n/x/read/3')
    const box = screen.getByRole('textbox')
    box.focus()

    fireEvent.keyDown(box, { key: 'ArrowRight' })
    expect(screen.getByTestId('path').textContent).toBe('/n/x/read/3')
  })

  it('第 1 章按 ← 不动', () => {
    renderRoute(<Probe />, '/n/x/read/1')

    fireEvent.keyDown(window, { key: 'ArrowLeft' })
    expect(screen.getByTestId('path').textContent).toBe('/n/x/read/1')
  })

  it('带修饰键的组合不劫持', () => {
    renderRoute(<Probe />, '/n/x/read/3')

    fireEvent.keyDown(window, { key: 'ArrowRight', ctrlKey: true })
    expect(screen.getByTestId('path').textContent).toBe('/n/x/read/3')
  })
})
