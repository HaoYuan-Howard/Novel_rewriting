import { api } from '@/api/endpoints'
import { screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { renderRoute } from '@/test/render'
import { CheckPanel } from './CheckPanel'

describe('CheckPanel', () => {
  it('renders stable note, instance detail, typed wontfix and exact unavailable reason as text', async () => {
    vi.spyOn(api, 'getCheck').mockResolvedValue({
      sid: 'book-1',
      node: 'stage1.partition',
      stage: 'stage1',
      provenance: {
        freshness: 'stale',
        reason: '规则版本已变化，需要重新验收',
        ghosts: [],
        edges: [],
        invalid: [],
        origin: null,
      },
      rules: [
        {
          rule: 'draft_too_short',
          sev: 'hard',
          status: 'hit',
          item: '成稿不是空的或残的',
          requirement: 'R-7.15',
          check_kind: 'mechanical',
          remediation: 'llm',
          fix: {
            node: 'stage1.partition',
            flags: [],
            paid: false,
            available: false,
            unavailable_reason: '机械复检不接受附加参数',
          },
          findings: [
            {
              rule: 'draft_too_short',
              sev: 'hard',
              item: 'ch11',
              output_ordinal: 11,
              detail: '本书实例 **不解析 Markdown**',
              evidence: ['原字节'],
              fix_args: {},
              evidence_key: 'e1',
              owner: 'stage5',
              actionable: true,
              identity_hit: true,
              identity_dead: true,
              converge: null,
              unresolved_rounds: 1,
              wontfix: { why: '人工确认保留', at: 'review_4' },
            },
          ],
          why: null,
          scanned: '1',
          baseline: null,
          note: '稳定规则解释只在 note 出现。',
        },
      ],
      accounting: [],
      badge: 'fail',
      passed: false,
      counts: { ok: 0, hit: 1, unver: 0, skipped: 0 },
    })
    const jump = vi.fn()
    renderRoute(<CheckPanel screen="stage1" onJumpChapter={jump} />)

    expect(await screen.findByText('稳定规则解释只在 note 出现。')).toBeVisible()
    expect(screen.getByText('本书实例 **不解析 Markdown**')).toBeVisible()
    expect(screen.getByText('已豁免：人工确认保留')).toBeVisible()
    expect(screen.getByText('identity_hit · 恒等基线也命中')).toBeVisible()
    expect(screen.getByText('identity_dead · 判据失去判别力')).toBeVisible()
    expect(screen.getByText('报告已过期：规则版本已变化，需要重新验收')).toBeVisible()
    const chapter = screen.getByRole('button', { name: '第 11 章' })
    expect(chapter).toBeVisible()
    chapter.click()
    expect(jump).toHaveBeenCalledWith(11)
    expect(screen.getByText('不能自动重跑：机械复检不接受附加参数')).toBeVisible()
  })
})
