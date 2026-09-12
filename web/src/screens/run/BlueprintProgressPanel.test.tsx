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
    review: {
      total_chapters: 2, generated_chapters: 2, accepted_chapters: 2,
      next_scope: 'cross_chapter', accepted: false, unresolved_issues: 0,
      scopes: ['chapter:1', 'chapter:2', 'cross_chapter'].map((scope) => ({
        scope, state: scope === 'cross_chapter' ? 'missing' : 'accepted',
        subject_sha256: null, decision_sha256: null, open_issue_ids: [],
      })),
    },
  }
}

describe('蓝图跑书进度', () => {
  it('全部章审接受仍展示跨章待审与尚未放行', () => {
    renderRoute(<BlueprintProgressPanel sid="book-1" progress={progress()} />, '/n/book-1/run')
    expect(screen.getByText('跨章复核：待审')).toBeVisible()
    expect(screen.getByText('全书放行：尚未放行')).toBeVisible()
    expect(screen.getByText('当前待审：跨章复核')).toBeVisible()
    expect(screen.getByText('有效接受章数')).toBeVisible()
    expect(screen.queryByRole('button', { name: /接受|生成|提交/ })).not.toBeInTheDocument()
  })

  it('提交前的检查点不显示为章审通过，坏账显示未知而非零', () => {
    const value = progress()
    value.artifact_state = 'missing'
    value.submitted_chapters = 0
    value.generated_chapters = value.checkpoint_chapters = null
    value.review = null
    value.unavailable_reason = '检查点内容与身份摘要不闭合'
    value.chapters[0].checkpoint_state = 'unver'
    renderRoute(<BlueprintProgressPanel sid="book-1" progress={value} />, '/n/book-1/run')
    expect(screen.getByText('蓝图提交：尚未提交（0 章）')).toBeVisible()
    expect(screen.getByText('全书放行：未知')).toBeVisible()
    expect(screen.getByText('检查点内容与身份摘要不闭合')).toBeVisible()
    expect(screen.getAllByText('未知')).toHaveLength(3)
    expect(screen.queryByText('审阅：已接受')).not.toBeInTheDocument()
  })
})
