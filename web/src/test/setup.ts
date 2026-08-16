import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

afterEach(cleanup)

class TestResizeObserver implements ResizeObserver {
  observe(target: Element) {
    const rect = target.getBoundingClientRect()
    const entry = {
      target,
      contentRect: rect,
      borderBoxSize: [{ inlineSize: rect.width, blockSize: rect.height }],
      contentBoxSize: [{ inlineSize: rect.width, blockSize: rect.height }],
      devicePixelContentBoxSize: [],
    } as unknown as ResizeObserverEntry
    this.callback([entry], this)
  }

  unobserve() {}
  disconnect() {}

  constructor(private readonly callback: ResizeObserverCallback) {}
}

globalThis.ResizeObserver = TestResizeObserver

Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
  configurable: true,
  get() {
    return this.dataset.testid === 'stage0-segment-viewport' ? 640 : 40
  },
})

Object.defineProperty(HTMLElement.prototype, 'offsetHeight', {
  configurable: true,
  get() {
    return this.dataset.testid === 'stage0-segment-viewport' ? 640 : 84
  },
})

Object.defineProperty(HTMLElement.prototype, 'offsetWidth', {
  configurable: true,
  get() {
    return 1000
  },
})

HTMLElement.prototype.getBoundingClientRect = function () {
  const height = this.dataset.testid === 'stage0-segment-viewport' ? 640 : 84
  return {
    x: 0,
    y: 0,
    top: 0,
    left: 0,
    right: 1000,
    bottom: height,
    width: 1000,
    height,
    toJSON: () => ({}),
  }
}
