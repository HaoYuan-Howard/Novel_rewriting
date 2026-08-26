import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { api } from '@/api/endpoints'
import type { UploadResponse } from '@/types/jobs'
import type { Artifact, NovelRow } from '@/types/novels'
import { CorpusScreen } from './CorpusScreen'

function uploaded(sid: string): UploadResponse {
  return {
    sid,
    job: {
      id: 'upload-' + sid,
      kind: 'stage0.remove',
      label: '扫描非正文',
      argv: 'novel stage0.remove',
      state: 'ok',
      paid: false,
      started: 1,
      finished: 2,
      sid,
      ordinal: null,
    },
  }
}

function renderCorpus(novels: NovelRow[] = []) {
  vi.spyOn(api, 'getNovels').mockResolvedValue({ base: 'E:/outputs', novels })
  vi.spyOn(api, 'getAgentQueue').mockResolvedValue({
    running: [],
    pending: [],
    rejected: [],
    max_concurrent: 3,
  })
  vi.spyOn(api, 'getSampleCoverage').mockResolvedValue(null)

  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  })
  const view = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter
        initialEntries={['/']}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <Routes>
          <Route path="/" element={<CorpusScreen />} />
          <Route path="/n/:sid/stage0" element={<div>Stage0 人工审校落点</div>} />
          <Route path="/n/:sid/stage5/:ordinal" element={<div>Stage5 未知状态落点</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return { queryClient, ...view }
}

function artifact(stage: string, name: string): Artifact {
  return {
    stage,
    name,
    exists: true,
    bytes: 1024,
    mtime: 1,
    provenance: {},
    freshness: 'fresh',
    display_freshness: 'fresh',
    condition: 'ready',
    stale: [],
  }
}

function compactBook(): NovelRow {
  return {
    sid: 'book-1',
    title: '测试书',
    dir: '测试书_book-1',
    dirname: '测试书_book-1',
    chapters: 12,
    stages: {
      stage0: [artifact('stage0', 'removal_report')],
      stage7: [artifact('stage7', 'name_map')],
    },
    stage_done: {
      stage0: true,
      stage1: false,
      stage2: false,
      stage3: false,
      stage4: false,
      stage5: null,
      stage6: false,
      stage7: true,
    },
    stage_reviewable: {},
    chapters_done: {},
    summary: {
      verdict: 'pending',
      text: '待继续',
      detail: '下一步：stage1 · stage1 分章',
      artifact_count: 2,
      latest_mtime: 1,
    },
  }
}

function choose(files: File[]) {
  const input = document.querySelector<HTMLInputElement>('input[type="file"]')
  expect(input).not.toBeNull()
  fireEvent.change(input!, { target: { files } })
  return input!
}

describe('CorpusScreen 批量开书', () => {
  it('语料库只保留操作与符号状态，不重复解释流程和下一步', async () => {
    renderCorpus([compactBook()])

    expect(await screen.findByRole('button', { name: '测试书' })).toBeVisible()
    expect(within(screen.getByTestId('corpus-header')).getByRole('textbox', {
      name: '按 sid 或书名过滤',
    })).toBeVisible()
    expect(screen.queryByText('上传后每本书会走的路')).not.toBeInTheDocument()
    expect(screen.queryByText('下一步：stage1 · stage1 分章')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /开一本或一批新书/ }))
    expect(screen.getByText('选择或拖入一本或多本 .txt / .md 原作')).toBeVisible()
    expect(document.querySelector('input[type="file"]')).toHaveAttribute('accept', '.txt,.md')
    expect(
      screen.queryByText('文件名、大小与内容由后端逐本校验；原始字节会完整保留'),
    ).not.toBeInTheDocument()
    expect(screen.queryByText('还没跑过任何一条臂')).not.toBeInTheDocument()
    expect(screen.queryByText('成稿不进产物表，只记跑过哪几条臂')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '打开 →' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Agent' })).toBeVisible()
    expect(screen.getByRole('button', { name: '删除《测试书》项目' })).toBeVisible()
    expect(screen.getByText('Agent 并发上限 3 本')).toBeVisible()

    expect(screen.getByTestId('stage-state-stage0')).toHaveTextContent('✓')
    expect(screen.getByTestId('stage-state-stage0')).not.toHaveTextContent('已完成')
    expect(screen.getByTestId('stage-state-stage7')).toHaveTextContent('✓')
    const stage0Artifact = screen.getByTestId('artifact-stage0-removal_report')
    expect(within(stage0Artifact).getByRole('img', { name: '新鲜' })).toBeVisible()
    expect(within(stage0Artifact).queryByText('新鲜')).not.toBeInTheDocument()
  })

  it('Stage5 展示 canonical 已写章数，不再出现写作臂', async () => {
    const book = compactBook()
    book.chapters_done.stage5 = [1, 2]
    renderCorpus([book])

    expect(await screen.findByText('canonical 2 章')).toBeVisible()
    expect(screen.queryByText('deep')).not.toBeInTheDocument()
    expect(screen.queryByText('shallow')).not.toBeInTheDocument()
  })

  it('打开时不会越过 done=null 的 Stage5', async () => {
    const book = compactBook()
    for (const stage of ['stage0', 'stage1', 'stage2', 'stage3', 'stage4']) {
      book.stage_done[stage] = true
    }
    renderCorpus([book])

    fireEvent.click(await screen.findByRole('button', { name: '测试书' }))

    expect(await screen.findByText('Stage5 未知状态落点')).toBeVisible()
  })

  it('单本扫描成功后立即弹出 agent 确认，返回时直接进入这本书的 Stage0', async () => {
    vi.spyOn(api, 'uploadCorpus').mockResolvedValue(uploaded('fq-a-1001'))
    const preview = vi.spyOn(api, 'previewAgentRun').mockResolvedValue({
      argv: ['claude', '-p', '从当前 Stage0 盘面续跑'],
      prompt: '从当前 Stage0 盘面续跑',
      authorized: false,
      egress_authorized: true,
    })

    renderCorpus()
    await screen.findByText('一本书都还没有')
    expect(screen.queryByRole('button', { name: '重新读取' })).not.toBeInTheDocument()

    choose([new File(['第一本正文'], 'FQ_甲_1001.txt', { type: 'text/plain' })])
    fireEvent.click(screen.getByRole('button', { name: '上传并扫非正文（Stage0）' }))

    expect(await screen.findByText('让 agent 跑这本书')).toBeVisible()
    await waitFor(() =>
      expect(preview).toHaveBeenCalledWith({
        sid: 'fq-a-1001',
        authorized: false,
        egress_authorized: true,
      }),
    )

    fireEvent.click(screen.getByRole('button', { name: '返回' }))
    expect(await screen.findByText('Stage0 人工审校落点')).toBeVisible()
  })

  it('逐本顺序上传，全部成功后自动打开整批 agent 授权', async () => {
    const upload = vi
      .spyOn(api, 'uploadCorpus')
      .mockResolvedValueOnce(uploaded('fq-a-1001'))
      .mockResolvedValueOnce(uploaded('fq-b-1002'))

    renderCorpus()
    await screen.findByText('一本书都还没有')

    const first = new File(['第一本正文'], 'FQ_甲_1001.txt', { type: 'text/plain' })
    const second = new File(['第二本正文'], 'FQ_乙_1002.txt', { type: 'text/plain' })
    const input = choose([first, second])

    expect(input).toHaveAttribute('multiple')
    expect(screen.getByText('已选择 2 本原作')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '上传并扫描这 2 本' }))

    expect(await screen.findByText('让 agent 跑这 2 本')).toBeVisible()
    expect(upload).toHaveBeenNthCalledWith(1, first)
    expect(upload).toHaveBeenNthCalledWith(2, second)
    expect(screen.getByText('FQ_甲_1001.txt')).toBeVisible()
    expect(screen.getByText('FQ_乙_1002.txt')).toBeVisible()
    expect(screen.getByText(/付费授权/)).toBeVisible()
    expect(screen.getByText(/正文外发授权/)).toBeVisible()
  })

  it('一本失败不回滚成功项，并允许只把成功项交给 agent', async () => {
    const upload = vi
      .spyOn(api, 'uploadCorpus')
      .mockResolvedValueOnce(uploaded('fq-a-1001'))
      .mockRejectedValueOnce(new Error('文件名无法建立 sid'))

    renderCorpus()
    await screen.findByText('一本书都还没有')

    const first = new File(['第一本正文'], 'FQ_甲_1001.txt', { type: 'text/plain' })
    const second = new File(['第二本正文'], '命名错误.txt', { type: 'text/plain' })
    choose([first, second])
    fireEvent.click(screen.getByRole('button', { name: '上传并扫描这 2 本' }))

    expect(await screen.findByText(/成功 1 本 · 失败 1 本/)).toBeVisible()
    expect(screen.getByText('命名错误.txt')).toBeVisible()
    expect(screen.getByText(/文件名无法建立 sid/)).toBeVisible()
    expect(screen.queryByText('让 agent 跑这 1 本')).not.toBeInTheDocument()
    expect(upload).toHaveBeenCalledTimes(2)

    fireEvent.click(screen.getByRole('button', { name: '交给 agent 跑成功的 1 本' }))

    await waitFor(() => expect(screen.getByText('让 agent 跑这 1 本')).toBeVisible())
    expect(screen.getByText('FQ_甲_1001.txt')).toBeVisible()
  })
})
