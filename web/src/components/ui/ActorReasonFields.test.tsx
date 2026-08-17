import { useState } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { ActorReasonFields } from './ActorReasonFields'

/** actor 经 sessionStorage('nr.actor') 记住上一次：挂载回填、改动写回。 */

function Harness({ initial = '' }: { initial?: string }) {
  const [actor, setActor] = useState(initial)
  const [reason, setReason] = useState('')
  return (
    <ActorReasonFields actor={actor} reason={reason} onActor={setActor} onReason={setReason} />
  )
}

describe('ActorReasonFields', () => {
  beforeEach(() => {
    sessionStorage.clear()
  })

  it('挂载时 actor 为空且 storage 有值：回填到父 state', () => {
    sessionStorage.setItem('nr.actor', 'editor-a')
    render(<Harness />)

    expect(screen.getByLabelText(/自报身份/)).toHaveValue('editor-a')
  })

  it('父 state 已有 actor 时不被 storage 覆盖', () => {
    sessionStorage.setItem('nr.actor', 'editor-a')
    render(<Harness initial="bob" />)

    expect(screen.getByLabelText(/自报身份/)).toHaveValue('bob')
  })

  it('storage 没有值时不回填', () => {
    render(<Harness />)

    expect(screen.getByLabelText(/自报身份/)).toHaveValue('')
  })

  it('输入时写回 sessionStorage', () => {
    render(<Harness />)

    fireEvent.change(screen.getByLabelText(/自报身份/), { target: { value: 'reviewer-b' } })
    expect(sessionStorage.getItem('nr.actor')).toBe('reviewer-b')
  })

  it('不传 onReason 时只渲染 actor 一栏', () => {
    render(<ActorReasonFields actor="" onActor={() => {}} />)

    expect(screen.getByLabelText(/自报身份/)).toBeInTheDocument()
    expect(screen.queryByLabelText(/理由/)).not.toBeInTheDocument()
  })
})
