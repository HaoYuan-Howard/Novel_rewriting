# Current-schema corpus acceptance manifest

The final acceptance will generate the committed, text-free identity of the two-book corpus at the
path shown in the build and verify commands below. It contains no title, source text, prompt,
response, or generated prose.
It records only the acceptance identity:

- each `sid`, normalized source SHA-1 and chapter count;
- every expected Stage 0–7 artifact path and each JSON artifact's exact schema version;
- the frozen producing code identity and exact rebuild commands;
- per-book ledger counts, token sums, provider cache hit/miss coverage, model/effort distribution and
  outcomes; a provider `null` is counted separately from a reported zero;
- the exact pytest skip allowlist (`nodeid` plus reason).

Build and verify it only against an isolated output directory:

```powershell
conda run -n novel_refactor python tools/corpus_manifest.py build `
  --base <isolated-current-corpus> `
  --output docs/corpus/current_schema_manifest.json `
  --command "<exact rebuild command>" `
  --skip-file <recorded-skips.json>

conda run -n novel_refactor python tools/corpus_manifest.py verify `
  --base <isolated-current-corpus> `
  --manifest docs/corpus/current_schema_manifest.json
```

The hard acceptance invocation is:

```powershell
$env:NOVEL_OUTPUT='<isolated-current-corpus>'
conda run -n novel_refactor python -m pytest --require-corpus
```

`--require-corpus` overrides the cheap default marker expression and collects both `corpus` and
non-`corpus` tests. It refuses a missing environment or manifest, book/artifact reduction, source or
chapter drift, schema-version drift, ledger drift, and any skip not exactly listed in the manifest.
Ledger row partitions (`wire + local-cache`, outcome, model and effort) must each close exactly; every
token column separately records reported/null row counts, and a column with zero reported rows cannot
claim a nonzero total. Pytest `xfail` reports are not silently reclassified as skips in the allowlist.
Use `--record-corpus-skips <path>` during manifest finalisation to capture a candidate allowlist;
recording is explicit and is never enabled by normal test runs.
