import { fireEvent, screen } from '@testing-library/react'
import { useLocation } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { renderRoute } from '@/test/render'
import { useChrome } from './useChrome'
import { useHotkeys } from './useHotkeys'

/**
 * 全局快捷键：Esc 先关确认框再关抽屉；输入框聚焦时不劫持；
 * 带修饰键的组合不劫持。`← / →` 翻章随旧链读屏整层退役（F8c）。
 */

function Probe() {
  useHotkeys()
  const { pathname } = useLocation()
  const { askConfirm, closeConfirm, drawerOpen, toggleDrawer, notify } = useChrome()
  return (
    <div>
      <span data-testid="path">{pathname}</span>
      <span data-testid="drawer">{drawerOpen ? '开' : '关'}</span>
      <button onClick={() => askConfirm({
        req: { kind: 'stage0.remove', label: '测试', extra: null, sid: 'x', ordinal: null },
      })}>开确认框</button>
      <button onClick={closeConfirm}>关确认框</button>
      <button onClick={toggleDrawer}>切抽屉</button>
      <button onClick={() => notify('一条通知')}>通知</button>
      <input aria-label="随便一个输入框" />
    </div>
  )
}

describe('useHotkeys', () => {
  it('Esc 先关确认框，确认框不在才关抽屉', () => {
    renderRoute(<Probe />, '/n/x/run')
    fireEvent.click(screen.getByRole('button', { name: '开确认框' }))
    fireEvent.click(screen.getByRole('button', { name: '切抽屉' }))
    expect(screen.getByTestId('drawer').textContent).toBe('开')

    fireEvent.keyDown(window, { key: 'Escape' })
    expect(screen.getByTestId('drawer').textContent).toBe('开')

    fireEvent.keyDown(window, { key: 'Escape' })
    expect(screen.getByTestId('drawer').textContent).toBe('关')
  })

  it('输入框聚焦时不劫持 Esc', () => {
    renderRoute(<Probe />, '/n/x/run')
    fireEvent.click(screen.getByRole('button', { name: '切抽屉' }))
    const box = screen.getByRole('textbox')
    box.focus()

    fireEvent.keyDown(box, { key: 'Escape' })
    expect(screen.getByTestId('drawer').textContent).toBe('开')
  })

  it('带修饰键的组合不劫持', () => {
    renderRoute(<Probe />, '/n/x/run')
    fireEvent.click(screen.getByRole('button', { name: '切抽屉' }))

    fireEvent.keyDown(window, { key: 'Escape', ctrlKey: true })
    expect(screen.getByTestId('drawer').textContent).toBe('开')
  })

  it('没有章号屏后左右键不再改路由', () => {
    renderRoute(<Probe />, '/n/x/run')

    fireEvent.keyDown(window, { key: 'ArrowRight' })
    fireEvent.keyDown(window, { key: 'ArrowLeft' })
    expect(screen.getByTestId('path').textContent).toBe('/n/x/run')
  })
})
