import { Link } from 'react-router-dom'
import { PaneHeader, Panel } from '@/components/ui/Panel'
import type { BlueprintRunProgress } from '@/types/plot.generated'

const CHECKPOINT_LABEL = {
  missing: '尚无检查点', current: '检查点已保存', expired: '检查点输入已过期',
  unver: '检查点待核验',
} as const
const PROCESSING_LABEL = {
  pending: '待处理', generated: '回答已生成', mechanical: '代码处理',
  history_adopted: '历史来源已采用', cached: '响应缓存已读取',
  failed: '生成失败', unknown: '运行结果未知',
} as const
const ARTIFACT_LABEL = {
  missing: '尚未提交', formal: '正式蓝图已提交', invalid: '问题稿待修订',
  unreadable: '蓝图状态待核验',
} as const

export function BlueprintProgressPanel({ progress, sid }: {
  progress: BlueprintRunProgress | null | undefined
  sid: string
}) {
  const count = (value: number | null | undefined) => value == null ? '未知' : value
  return <Panel>
    <PaneHeader title="蓝图生成进度" meta="生成、检查点与正式提交分别显示" />
    <div className="flex flex-col gap-12 px-18 py-14" aria-label="蓝图生成进度">
      {!progress ? <p className="m-0 text-ink-4">当前没有可读取的蓝图进度。</p> : <>
        <dl className="m-0 grid grid-cols-2 gap-12 md:grid-cols-4">
          {[
            ['源章节', progress.total_chapters],
            ['已生成章数', progress.generated_chapters],
            ['当前检查点', progress.checkpoint_chapters],
            ['机械已提交章数', progress.submitted_chapters],
          ].map(([label, value]) => <div key={label as string} className="rounded-block bg-sunken-2 px-12 py-10">
            <dt className="text-tag text-ink-4">{label}</dt>
            <dd className="m-0 mt-4 text-title font-semibold tabular-nums">{count(value as number | null | undefined)}</dd>
          </div>)}
        </dl>
        <div className="flex flex-wrap gap-x-20 gap-y-6 text-body">
          <span>蓝图提交：{ARTIFACT_LABEL[progress.artifact_state]}</span>
        </div>
        {progress.unavailable_reason ? <p role="note" className="m-0 text-warn-ink">{progress.unavailable_reason}</p> : null}
        <details>
          <summary className="cursor-pointer text-sm2">逐章生成状态</summary>
          <ul className="m-0 mt-8 list-none space-y-6 p-0">
            {progress.chapters.map((chapter) => {
              return <li key={chapter.chapter_ordinal} className="flex flex-wrap items-baseline gap-x-12 text-sm2">
                <Link className="text-accent hover:underline" to={`/n/${sid}/read/${chapter.chapter_ordinal}`}>第 {chapter.chapter_ordinal} 章</Link>
                <span>{CHECKPOINT_LABEL[chapter.checkpoint_state]}</span>
                {chapter.processing_state ? <span>{PROCESSING_LABEL[chapter.processing_state]}</span> : null}
                {chapter.reason ? <span className="text-warn-ink">{chapter.reason}</span> : null}
              </li>
            })}
          </ul>
        </details>
        <p className="m-0 text-tag text-ink-4">检查点保存后仍需聚合校验和正式提交；机械可用性不代表语义审阅通过。</p>
      </>}
    </div>
  </Panel>
}
