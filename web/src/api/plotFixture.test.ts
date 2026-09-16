import { describe, expect, it } from 'vitest'
import { mockApi, PLOT_SAMPLE_SID, SAMPLE_SID, plotFlowFixture, plotReviewFixture } from './mock'

describe('现役整链录制样本', () => {
  it('Prepare 与最终文案材料读取同次正式来源，独立接受不靠旧 Global 拼接', async () => {
    const flow = plotFlowFixture()
    const review = plotReviewFixture()
    const [view, pack, hook, approvals] = await Promise.all([
      mockApi.getStage6Prepare(PLOT_SAMPLE_SID), mockApi.getStage6PrepareWorkpack(PLOT_SAMPLE_SID),
      mockApi.getStage6ApprovalWorkpack(PLOT_SAMPLE_SID), mockApi.getApprovals(PLOT_SAMPLE_SID),
    ])
    expect(flow.metadata.synthetic).toBe(true)
    expect(flow.metadata.sid).toBe(PLOT_SAMPLE_SID)
    expect(view).toEqual(flow['final:http:stage6/prepare'])
    expect(pack).toEqual(review.prepare)
    expect(view.status.state).toBe('accepted')
    expect(view.status.review?.source_sha256).toBe(view.status.subject_sha256)
    expect(hook).toEqual(review.hook)
    expect(hook.materials.source_blurb).toEqual(view.status.prepare!.source_blurb)
    expect(hook.materials.source_hook).toEqual(pack.hook_copy.text)
    expect(hook.materials.rewritten_chapters.map((chapter) => chapter.chapter_ordinal)).toEqual([1, 2, 3])
    expect(hook.review_token).toMatch(/^s6r4_[0-9a-f]{64}$/)
    expect(approvals).toEqual(flow['final:http:approvals'])
    expect(approvals.prepare).toEqual(view.status)
  })

  it('Stage7 终态、逐章替换与文本下载来自真实 HTTP，返回副本互不改写', async () => {
    const flow = plotFlowFixture()
    const [novel, view, pack, chapter, text, blob] = await Promise.all([
      mockApi.getNovel(PLOT_SAMPLE_SID), mockApi.getStage7(PLOT_SAMPLE_SID),
      mockApi.getStage7ApprovalWorkpack(PLOT_SAMPLE_SID), mockApi.getStage7Chapter(PLOT_SAMPLE_SID, 1),
      mockApi.getStage7Book(PLOT_SAMPLE_SID), mockApi.downloadStage7Book(PLOT_SAMPLE_SID),
    ])
    expect(novel.stage_done.stage7).toBe(true)
    expect(view).toEqual(flow['final:http:stage7'])
    expect(pack).toEqual(plotReviewFixture().rename)
    expect(pack.disposition).toBe('already_current')
    expect(['ready', 'ready_with_disclosures']).toContain(view.delivery.state)
    expect(chapter.rows!.some((row) => row.changed)).toBe(true)
    expect(text).toBe(flow['final:http:stage7/book.txt'])
    expect(text).toContain('顾清棠')
    expect(text).not.toContain('李宝珠')
    expect(blob.size).toBe(new Blob([text]).size)
    const downloaded = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => resolve(reader.result as string)
      reader.onerror = () => reject(reader.error)
      reader.readAsText(blob)
    })
    expect(downloaded).toBe(text)
    view.characters.length = 0
    expect((await mockApi.getStage7(PLOT_SAMPLE_SID)).characters.length).toBeGreaterThan(0)
    expect((await mockApi.getNovels()).novels.some((book) => book.sid === PLOT_SAMPLE_SID)).toBe(true)
  })

  it('历史小说没有当前 Prepare 与终审工作包；缺章仍为 nosample', async () => {
    for (const request of [mockApi.getStage6Prepare(SAMPLE_SID), mockApi.getStage6ApprovalWorkpack(SAMPLE_SID),
      mockApi.getStage7ApprovalWorkpack(SAMPLE_SID), mockApi.getStage7Chapter(PLOT_SAMPLE_SID, 99)]) {
      await expect(request).rejects.toMatchObject({ kind: 'nosample' })
    }
  })

  it('只读样本不会伪造编辑、批准和恢复作业', async () => {
    const pack = await mockApi.getStage6PrepareWorkpack(PLOT_SAMPLE_SID)
    await expect(mockApi.approveHook(PLOT_SAMPLE_SID, { review_token: 'current', actor: 'test', reason: '测试', pick: 1 }))
      .rejects.toMatchObject({ status: 501 })
    // 批次 B4：prepare review/edit 与 stage7.approve 已从 Api 退役，mock 亦无对应方法。
    expect('previewStage6PrepareEdit' in mockApi).toBe(false)
    expect('reviewStage6Prepare' in mockApi).toBe(false)
    expect('approveNames' in mockApi).toBe(false)
    expect((await mockApi.getStage6Prepare(PLOT_SAMPLE_SID)).status).toEqual(pack.state)
  })
})
