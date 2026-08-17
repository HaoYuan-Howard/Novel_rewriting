import { api } from '@/api/endpoints'
import { screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { renderRoute } from '@/test/render'
import { CheckPanel } from './CheckPanel'

describe('CheckPanel', () => {
  it('renders stable note, instance detail, typed wontfix and exact unavailable reason as text', async () => {
    vi.spyOn(api, 'getCheck').mockResolvedValue({
      sid: 'book-1',
      node: 'stage5.review',
      stage: 'stage5',
      provenance: {
        freshness: 'fresh',
        reason: '',
        ghosts: [],
        edges: [],
        invalid: [],
        revalidated: true,
        origin: null,
      },
      rules: [
        {
          rule: 'hinge_in_hole',
          sev: 'hard',
          status: 'hit',
          item: '要害落洞',
          requirement: '要害必须落在正文',
          check_kind: 'mechanical',
          remediation: 'llm',
          fix: {
            node: 'stage5.write',
            flags: [],
            paid: true,
            available: false,
            unavailable_reason: '缺少 --chapters 的动态章号',
          },
          findings: [
            {
              rule: 'hinge_in_hole',
              sev: 'hard',
              item: 'ch11',
              output_ordinal: 11,
              detail: '本书实例 **不解析 Markdown**',
              evidence: ['原字节'],
              fix_args: {},
              evidence_key: 'e1',
              owner: 'stage5',
              actionable: true,
              identity_hit: false,
              identity_dead: false,
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
    renderRoute(<CheckPanel screen="stage5" onJumpChapter={jump} />)

    expect(await screen.findByText('稳定规则解释只在 note 出现。')).toBeVisible()
    expect(screen.getByText('本书实例 **不解析 Markdown**')).toBeVisible()
    expect(screen.getByText('已豁免：人工确认保留')).toBeVisible()
    const chapter = screen.getByRole('button', { name: '第 11 章' })
    expect(chapter).toBeVisible()
    chapter.click()
    expect(jump).toHaveBeenCalledWith(11)
    expect(screen.getByText('不能自动重跑：缺少 --chapters 的动态章号')).toBeVisible()
  })
})
