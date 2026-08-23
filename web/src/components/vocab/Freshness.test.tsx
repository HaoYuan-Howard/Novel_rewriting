import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { FreshnessBadge } from './Freshness'

describe('FreshnessBadge', () => {
  it('用明显不同的符号区分无从判断和不在盘上', () => {
    render(
      <div>
        <FreshnessBadge f="unknown" />
        <FreshnessBadge f="missing" />
      </div>,
    )

    expect(screen.getByText('?')).toBeVisible()
    expect(screen.getByText('∅')).toBeVisible()
    expect(screen.getByText('无从判断')).toBeVisible()
    expect(screen.getByText('不在盘上')).toBeVisible()
  })
})
