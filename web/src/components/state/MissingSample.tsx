import { useNavigate } from 'react-router-dom'
import { SAMPLE_SID } from '@/api/mock'
import { cx } from '@/lib/cx'

/**
 * 「这一份响应没有在样本里」。
 *
 * mock 模式只录了一本书的详情响应。别的书一律走这一屏 ——
 * **绝不拿另一本的数据顶上**，那会让人以为自己在看这一份。这是设计的一部分，不是缺陷。
 */
export function MissingSample({
  title,
}: {
  title: string
}) {
  const nav = useNavigate()

  return (
    <section className="flex flex-col gap-16 px-30 py-26">
      <header className="flex items-end justify-between gap-24">
        <div className="flex flex-col gap-5">
          <h1 className="m-0 text-h1 font-semibold">{title}</h1>
          <div className="text-body text-ink-3">样本只录了这一本书的详情响应</div>
        </div>
      </header>

      <div
        className={cx(
          'flex flex-col items-center gap-12 rounded-modal border border-dashed border-line-dash bg-sunken px-36 py-44',
        )}
      >
        <div className="text-h2 font-semibold">这一份响应没有在样本里</div>
        <div className="prose-cn max-w-[600px] text-center text-body-lg text-ink-3">
          这本书在语料库列表里是真实的（列表来自 /api/novels），但样本包只录了一本的详情响应。
          界面不拿另一本的数据顶上——那会让你以为你在看这本。
        </div>
        <div className="mt-6 flex gap-8">
          <button
            type="button"
            onClick={() => nav('/n/' + SAMPLE_SID + '/stage1')}
            className="cursor-pointer rounded-btn-lg border border-ink bg-ink px-18 py-9 text-body-lg font-semibold text-card"
          >
            打开有样本的那本书
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
