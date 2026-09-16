import { api } from '@/api/endpoints'
import { screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { renderRoute } from '@/test/render'
import { Stage1Screen } from './Stage1Screen'

describe('Stage1Screen 信息与操作布局', () => {
  it('只读展示切分结果与校验，不提供确认切分写面', async () => {
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
    renderRoute(<Stage1Screen />, '/n/book-1/stage1')

    expect(await screen.findByText('原文切分结果')).toBeVisible()
    expect(screen.getAllByText('开篇文案')).toHaveLength(1)
    expect(screen.getByText('只读切分结果')).toBeVisible()
    expect(screen.getByRole('group', { name: 'Stage1 校验' })).toBeVisible()
    expect(screen.queryByRole('button', { name: '确认切分' })).not.toBeInTheDocument()
    expect(screen.queryByText('这一刀切对了吗？')).not.toBeInTheDocument()
    expect('partitionStage1' in api).toBe(false)
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

    renderRoute(<Stage1Screen />, '/n/book-1/stage1')

    expect(await screen.findByText('章号异常 3 处，请逐章核对')).toBeVisible()
    expect(screen.getByText('章号重复')).toBeVisible()
    expect(screen.getByText('章号缺号（差值 2）')).toBeVisible()
    expect(screen.getByText('章号倒退（差值 -2）')).toBeVisible()
  })

  it('正文接口失败时仍保留只读校验面', async () => {
    vi.spyOn(api, 'getStage1').mockRejectedValue(new Error('正文接口读取失败'))

    renderRoute(<Stage1Screen />, '/n/book-1/stage1')

    expect(screen.getByRole('group', { name: 'Stage1 校验' })).toBeVisible()
    expect(screen.queryByRole('button', { name: '确认切分' })).not.toBeInTheDocument()
    expect(await screen.findByText(/正文接口读取失败/)).toBeVisible()
  })
})
