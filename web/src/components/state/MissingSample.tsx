import { useNavigate } from 'react-router-dom'
import { ApiError } from '@/api/errors'
import { SAMPLE_ORDINAL, SAMPLE_SID } from '@/api/mock'
import { ChapterStrip } from '@/components/ui/ChapterStrip'
import { cx } from '@/lib/cx'

/**
 * 「这一份响应没有在样本里」。
 *
 * 样本抓自一本 8 章的书，逐章响应只抓了第 3 章。
 * 其余章、其余书一律走这一屏 —— **绝不拿另一本 / 另一章的数据顶上**，
 * 那会让人以为自己在看这一份。这是设计的一部分，不是缺陷。
 */
export function MissingSample({
  error,
  title,
  chapters,
  ordinal,
  path,
}: {
  error: ApiError
  title: string
  /** 章号屏才给：让人一眼看到哪一章是有样本的 */
  chapters?: number[]
  ordinal?: number
  /** 当前屏在路由里的段名，用于生成「回到第 3 章」的目标 */
  path?: string
}) {
  const nav = useNavigate()
  const isChapter = error.scope === 'chapter'

  return (
    <section className="flex flex-col gap-16 px-30 py-26">
      <header className="flex items-end justify-between gap-24">
        <div className="flex flex-col gap-5">
          <h1 className="m-0 text-h1 font-semibold">
            {title}
            {isChapter && ordinal ? ' · 第 ' + ordinal + ' 章' : ''}
          </h1>
          <div className="text-body text-ink-3">
            {isChapter ? '样本只抓了第 3 章的逐章响应' : '样本只抓了这一本书的详情响应'}
          </div>
        </div>
        {isChapter && chapters?.length ? (
          <ChapterStrip
            items={chapters.map((n) => ({
              n,
              active: n === ordinal,
              dim: n !== SAMPLE_ORDINAL,
              go: () => nav('../' + (path ?? '') + '/' + n, { relative: 'path' }),
            }))}
          />
        ) : null}
      </header>

      <div
        className={cx(
          'flex flex-col items-center gap-12 rounded-modal border border-dashed border-line-dash bg-sunken px-36 py-44',
        )}
      >
        <div className="text-h2 font-semibold">这一份响应没有在样本里</div>
        <div className="prose-cn max-w-[600px] text-center text-body-lg text-ink-3">
          {isChapter
            ? `这一屏按章取数（GET …${error.path}）。样本包里只有第 ${SAMPLE_ORDINAL} 章那一份，所以第 ${ordinal} 章这里是空的——空的原因是「没抓样本」，不是「这一章没跑」。真实后端起来之后每一章都有。`
            : '这本书在语料库列表里是真实的（列表来自 /api/novels），但样本包只抓了《我把夫君捧成头牌后，全家悔疯了》一本的详情响应。界面不拿另一本的数据顶上——那会让你以为你在看这本。'}
        </div>
        <div className="mt-6 flex gap-8">
          <button
            type="button"
            onClick={() =>
              isChapter
                ? nav('../' + (path ?? '') + '/' + SAMPLE_ORDINAL, { relative: 'path' })
                : nav('/n/' + SAMPLE_SID + '/stage1')
            }
            className="cursor-pointer rounded-btn-lg border border-ink bg-ink px-18 py-9 text-body-lg font-semibold text-card"
          >
            {isChapter ? '回到第 ' + SAMPLE_ORDINAL + ' 章' : '打开有样本的那本书'}
          </button>
          <button
            type="button"
            onClick={() => nav('/')}
            className="cursor-pointer rounded-btn-lg border border-line-btn bg-card px-18 py-9 text-body-lg"
          >
            回语料库
          </button>
        </div>
      </div>
    </section>
  )
}
