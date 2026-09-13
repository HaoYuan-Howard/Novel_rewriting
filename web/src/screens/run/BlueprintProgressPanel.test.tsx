import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { renderRoute } from '@/test/render'
import type { BlueprintRunProgress } from '@/types/plot.generated'
import { BlueprintProgressPanel } from './BlueprintProgressPanel'

function progress(): BlueprintRunProgress {
  return {
    total_chapters: 2, generated_chapters: 2, checkpoint_chapters: 2,
    submitted_chapters: 2, artifact_state: 'formal', unavailable_reason: null,
    chapters: [1, 2].map((ordinal) => ({
      chapter_ordinal: ordinal, run_id: 'test-run', checkpoint_state: 'current',
      processing_state: 'generated', reason: null,
    })),
  }
}

describe('蓝图跑书进度', () => {
  it('生成、检查点与正式提交分别显示，不出现审阅待办', () => {
    renderRoute(<BlueprintProgressPanel sid="book-1" progress={progress()} />, '/n/book-1/run')
    expect(screen.getByText('蓝图提交：正式蓝图已提交')).toBeVisible()
    expect(screen.getByText('机械已提交章数')).toBeVisible()
    expect(screen.queryByRole('button', { name: /接受|生成|提交/ })).not.toBeInTheDocument()
    expect(screen.queryByText(/章审|跨章|放行/)).not.toBeInTheDocument()
  })

  it('提交前的检查点不显示为已提交，坏账显示未知而非零', () => {
    const value = progress()
    value.artifact_state = 'missing'
    value.submitted_chapters = 0
    value.generated_chapters = value.checkpoint_chapters = null
    value.unavailable_reason = '检查点内容与身份摘要不闭合'
    value.chapters[0].checkpoint_state = 'unver'
    renderRoute(<BlueprintProgressPanel sid="book-1" progress={value} />, '/n/book-1/run')
    expect(screen.getByText('蓝图提交：尚未提交')).toBeVisible()
    expect(screen.getByText('检查点内容与身份摘要不闭合')).toBeVisible()
    expect(screen.getAllByText('未知')).toHaveLength(2)
    expect(screen.queryByText('审阅：已接受')).not.toBeInTheDocument()
  })
})
