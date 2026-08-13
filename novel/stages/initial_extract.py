from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from contracts.schema.report import CheckReport
from contracts.schema.stage3 import StyleGrounding
from novel.llm.client import LlmClient
from novel.run import RunContext
from novel.stages.stage2 import extract
from novel.stages.stage3 import style


def run_initial_extract(
    global_ctx: RunContext,
    global_client: LlmClient,
    blueprint_ctx: RunContext,
    blueprint_client: LlmClient,
    style_ctx: RunContext,
    style_client: LlmClient,
) -> tuple[CheckReport, StyleGrounding]:
    # These are independent paid requests. Blueprint fans out again, one future
    # per chapter; global and style therefore overlap the entire chapter batch.
    with ThreadPoolExecutor(max_workers=3) as pool:
        global_future = pool.submit(
            extract.run_global, global_ctx, global_client)
        blueprint_future = pool.submit(
            extract.request_blueprints, blueprint_ctx, blueprint_client)
        style_future = pool.submit(
            style.request_style, style_ctx, style_client)

    global_future.result()
    blueprint_batch = blueprint_future.result()
    style_request = style_future.result()

    report: CheckReport | None = None
    artifact: StyleGrounding | None = None
    errors: list[Exception] = []
    try:
        report = extract.finish_blueprints(
            blueprint_ctx, blueprint_client, blueprint_batch)
    except Exception as exc:
        errors.append(exc)
    try:
        artifact = style.finish_style(style_ctx, style_client, style_request)
    except Exception as exc:
        errors.append(exc)

    if errors:
        raise errors[0]
    assert report is not None and artifact is not None
    return report, artifact
