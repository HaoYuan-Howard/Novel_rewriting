import { api } from '@/api/endpoints'
import { screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { renderRoute } from '@/test/render'
import type { ApprovalDoc } from '@/types/approvals'
import { Stage1Screen } from './Stage1Screen'

describe('Stage1Screen 信息与操作布局', () => {
  it('只展示一份开篇文案，并把校验与确认合并到正文之前', async () => {
    vi.spyOn(api, 'getStage1').mockResolvedValue({
      segments: [
        {
          id: 'hook',
          kind: 'hook',
          start: 0,
          end: 4,
          chars: 4,
          text: '开篇内容',
          chapter_ordinal: null,
          source_chapter_no: null,
          label_delta: null,
          anomaly: null,
        },
        {
          id: 'chapter-1',
          kind: 'chapter',
          start: 4,
          end: 8,
          chars: 4,
          text: '第一章正文',
          chapter_ordinal: 1,
          source_chapter_no: 1,
          label_delta: null,
          anomaly: null,
        },
      ],
      anomaly_count: 0,
    })
    vi.spyOn(api, 'getApprovals').mockResolvedValue({
      v: 5,
      sid: 'book-1',
      gate: {
        s0: true,
        s1: false,
        s2: false,
        s4: false,
        s5: false,
        s6: false,
        s7: false,
      },
      seen: { read: false, global: false },
      writing: false,
      design_review: {
        state: 'unavailable', candidate_sha256: null, aggregate_subject_sha256: null,
        current_decision: null, history: [], binding_infeasibility: null, repair_capability: null,
      },
      structure: {
        state: 'unavailable', current_subject_sha256: null,
        current_decision: null, history: [], binding_concerns: [],
      },
      spawns: {},
    } satisfies ApprovalDoc)
    renderRoute(<Stage1Screen />, '/n/book-1/stage1')

    expect(await screen.findByText('原文切分结果')).toBeVisible()
    expect(screen.getAllByText('开篇文案')).toHaveLength(1)
    expect(screen.queryByText('完整 clean_text')).not.toBeInTheDocument()
    expect(screen.queryByText(/这里是 Stage 0 批准后的完整/)).not.toBeInTheDocument()
    expect(screen.queryByText(/input sha1/)).not.toBeInTheDocument()
    expect(screen.queryByText(/服务端连续分区/)).not.toBeInTheDocument()
    expect(screen.queryByText(/没有前端二次切字/)).not.toBeInTheDocument()
    expect(screen.queryByText('这一层的判据全部验过且通过。')).not.toBeInTheDocument()
    expect(screen.queryByText(/每条都带「扫过什么」/)).not.toBeInTheDocument()
    expect(screen.queryByText('确认后会发起：')).not.toBeInTheDocument()
    expect(screen.queryByText('这一刀切对了吗？')).not.toBeInTheDocument()

    const controls = screen.getByRole('group', { name: 'Stage1 校验与切分确认' })
    expect(await within(controls).findByRole('button', { name: '确认切分' })).toBeVisible()
    const result = screen.getByText('原文切分结果')
    expect(controls.compareDocumentPosition(result) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('把重号、缺号与倒退作为显式章号异常提示，不折进查过没问题', async () => {
    vi.spyOn(api, 'getStage1').mockResolvedValue({
      segments: [
        {
          id: 'hook', kind: 'hook', start: 0, end: 2, chars: 2,
          text: '钩子', chapter_ordinal: null, source_chapter_no: null,
          label_delta: null, anomaly: null,
        },
        {
          id: 'c1', kind: 'chapter', start: 2, end: 5, chars: 3, text: '第一段',
          chapter_ordinal: 1, source_chapter_no: 1,
          label_delta: null, anomaly: 'repeat',
        },
        {
          id: 'c2', kind: 'chapter', start: 5, end: 8, chars: 3, text: '第二段',
          chapter_ordinal: 2, source_chapter_no: 3,
          label_delta: 2, anomaly: 'gap',
        },
        {
          id: 'c3', kind: 'chapter', start: 8, end: 11, chars: 3, text: '第三段',
          chapter_ordinal: 3, source_chapter_no: 1,
          label_delta: -2, anomaly: 'reset',
        },
      ],
      anomaly_count: 3,
    })
    vi.spyOn(api, 'getApprovals').mockResolvedValue({
      v: 5,
      sid: 'book-1',
      gate: { s0: true, s1: false, s2: false, s4: false, s5: false, s6: false, s7: false },
      seen: { read: false, global: false },
      writing: false,
      design_review: {
        state: 'unavailable', candidate_sha256: null, aggregate_subject_sha256: null,
        current_decision: null, history: [], binding_infeasibility: null, repair_capability: null,
      },
      structure: {
        state: 'unavailable', current_subject_sha256: null,
        current_decision: null, history: [], binding_concerns: [],
      },
      spawns: {},
    } satisfies ApprovalDoc)

    renderRoute(<Stage1Screen />, '/n/book-1/stage1')

    expect(await screen.findByText('章号异常 3 处，请逐章核对')).toBeVisible()
    expect(screen.getByText('章号重复')).toBeVisible()
    expect(screen.getByText('章号缺号（差值 2）')).toBeVisible()
    expect(screen.getByText('章号倒退（差值 -2）')).toBeVisible()
  })

  it('正文接口失败时仍保留校验与确认恢复面', async () => {
    vi.spyOn(api, 'getStage1').mockRejectedValue(new Error('正文接口读取失败'))
    vi.spyOn(api, 'getApprovals').mockResolvedValue({
      v: 5,
      sid: 'book-1',
      gate: { s0: true, s1: false, s2: false, s4: false, s5: false, s6: false, s7: false },
      seen: { read: false, global: false },
      writing: false,
      design_review: {
        state: 'unavailable', candidate_sha256: null, aggregate_subject_sha256: null,
        current_decision: null, history: [], binding_infeasibility: null, repair_capability: null,
      },
      structure: {
        state: 'unavailable', current_subject_sha256: null,
        current_decision: null, history: [], binding_concerns: [],
      },
      spawns: {},
    } satisfies ApprovalDoc)

    renderRoute(<Stage1Screen />, '/n/book-1/stage1')

    const controls = screen.getByRole('group', { name: 'Stage1 校验与切分确认' })
    expect(await within(controls).findByRole('button', { name: '确认切分' })).toBeVisible()
    expect(await screen.findByText(/正文接口读取失败/)).toBeVisible()
  })
})
