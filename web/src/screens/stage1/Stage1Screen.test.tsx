import { api } from '@/api/endpoints'
import { screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { renderRoute } from '@/test/render'
import type { ApprovalDoc } from '@/types/approvals'
import { Stage1Screen } from './Stage1Screen'

describe('Stage1Screen 信息与操作布局', () => {
  it('只展示一份开篇文案，并把校验与确认合并到正文之前', async () => {
    vi.spyOn(api, 'getStage1Segments').mockResolvedValue({
      input_sha1: 'a'.repeat(40),
      segments: [
        {
          id: 'hook',
          kind: 'hook',
          start: 0,
          end: 4,
          chars: 4,
          flagged: false,
          text: '开篇内容',
          chapter_ordinal: null,
          source_chapter_no: null,
        },
        {
          id: 'chapter-1',
          kind: 'chapter',
          start: 4,
          end: 8,
          chars: 4,
          flagged: false,
          text: '第一章正文',
          chapter_ordinal: 1,
          source_chapter_no: 1,
        },
      ],
    })
    vi.spyOn(api, 'getStage1Chapters').mockResolvedValue({
      chapters: [
        { chapter_ordinal: 1, source_chapter_no: 1, chars: 4, preview: '第一章正文' },
      ],
    })
    vi.spyOn(api, 'getApprovals').mockResolvedValue({
      v: 1,
      sid: 'book-1',
      gate: {
        s0: true,
        s1: false,
        s2: false,
        s3: false,
        s4: false,
        s5: false,
        s6: false,
        s7: false,
      },
      seen: { read: false, global: false },
      deep: [],
      writing: false,
      spawns: {},
    } satisfies ApprovalDoc)
    const hookCopy = vi.spyOn(api, 'getStage1HookCopy')

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
    expect(hookCopy).not.toHaveBeenCalled()

    const controls = screen.getByRole('group', { name: 'Stage1 校验与切分确认' })
    expect(await within(controls).findByRole('button', { name: '确认切分' })).toBeVisible()
    const result = screen.getByText('原文切分结果')
    expect(controls.compareDocumentPosition(result) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })
})
