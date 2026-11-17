# Running HQBD with `hqbd_bench`

`hqbd_bench` is a standard-library-only orchestration layer over the native
evaluators. It selects cases from the HQBD index, materializes the exact native
subset for each backend, invokes the existing runners, and normalizes their
artifacts into one result schema.

It does not reimplement scoring:

| Dataset | Backend | Scoring owner |
|---|---|---|
| CVDP | `evaluators/cvdp/run_benchmark.py` | CVDP harness |
| HWE-bench | `evaluators/hwe`: `adapter -> harbor -> verify_bridge -> evaluator` | HWE-bench evaluator |
| custom | manifest stages or a plugin adapter | that backend |

`datasets/hqbd_v0.1.0/` and the (optional) heavy bundle payload under
`datasets/hqbd_v0.0.1/` are read-only inputs.

## Quick start

Run from the package root, with Python 3.12+ available:

```bash
# What is installed?
python -m hqbd_bench dataset list
python -m hqbd_bench doctor --all

# Validate data and manifests without Docker.
# Without the optional heavy bundle payload, cvdp.heavy_bundles is a warning,
# so use --strict only after restoring the bundles.
python -m hqbd_bench dataset validate --all --check-hashes

# Inspect the exact commands a run would execute; writes nothing.
python -m hqbd_bench run cvdp --runnable --dry-run
python -m hqbd_bench run hwe  --runnable --dry-run
```

Expected planning counts:

- CVDP: 6 groups, 749 cases (one group per native source file).
- HWE-bench: 5 groups, 172 cases (OpenTitan's 245 local-build cases excluded).

A real CVDP run needs a model and a working CVDP environment:

```bash
python -m hqbd_bench run cvdp \
  --ids hqbd_agentic_64b66b_codec_0001 \
  --mode llm --model <model> \
  --run-id smoke-cvdp
```

A real HWE run needs Docker, the per-PR images, Harbor, and agent credentials:

```bash
python -m hqbd_bench run hwe \
  --ids hqbd_hwe_ibex_2232 \
  --agent claude-code --model <model> \
  --agent-env ANTHROPIC_API_KEY \
  --run-id smoke-hwe
```

Outputs land in `runs/<run-id>/`: `results.jsonl`, `summary.json`, and
`report.md`. `runs/` is gitignored.

## Selection

Selection always starts from
`datasets/hqbd_v0.1.0/hqbd_v0.1.0_unified_index.jsonl`, restricted to the manifest's
`select` constraints. Native records are resolved through
`{source_file, source_line}` and checked against the catalog ID.

| Flag | Meaning |
|---|---|
| `--ids a,b` | exact HQBD IDs; unknown IDs are an error |
| `--source` | `CVDP` or `HWE-bench` (any indexed value) |
| `--representation` | e.g. `cvdp_copilot`, `hwe_native` |
| `--task-type` | e.g. `code_generation`, `bug_repair` |
| `--usage-license` | index value such as `upstream_unverified` |
| `--where KEY=VALUE` | equality filter on any index field (repeatable) |
| `--runnable / --no-runnable` | override `defaults.runnable` |
| `--allow-local-build` | include `local_build_required` cases |
| `--limit N` | first N after filtering |
| `--shard I/N` | deterministic 1-based shard |

`local_build_required` currently means the 245 OpenTitan cases. They need a
locally built image and Synopsys VCS; they are excluded by default.

The selection SHA-256 covers the selected IDs and the filter snapshot. Changing
either requires a new run ID or `--force-rebuild`.

## Backends and credentials

`backend.python` and `backend.harbor` are argv lists. If `backend.python` is
`["python"]` (or omitted) and `<backend.root>/.venv/Scripts/python.exe`
(Windows) or `.venv/bin/python` exists, that interpreter is used
automatically. `--backend-python "uv run python"` and
`--harbor-command <argv>` override the manifest for one run.

- CVDP reads `evaluators/cvdp/.env`; copy `.env.example` and fill it in.
  Required keys include `OSS_SIM_IMAGE`. `CVDP_HEAVY_REPOS_PATH` is injected
  automatically for heavy groups and points at
  `datasets/hqbd_v0.0.1/hqbd_v0.0.1_agentic_heavy_code_generation_public`.
  That directory ships empty; the heavy bundle payload is an optional
  dependency. Without it, the 100 heavy cases fail unless you pass
  `--env CVDP_HEAVY_REPOS_PATH=<payload>`.
- HWE passes agent credentials with `--agent-env NAME`. Only the variable name
  is stored in the plan, state, and logs; the value is read from the host
  environment at execution time and passed as a redacted
  `--ae NAME=***` in printed plans.
- `--env KEY=VALUE` adds an environment variable to every backend command. The
  report stores the key but masks the value.

`doctor` checks interpreters, entry files, Docker, `.env` key names, heavy
bundles, Harbor, and disk space. It never pulls images. Add `--check-images`
to inspect local HWE images.

## Run layout

```text
runs/<run-id>/
  run.json                  # provenance, resolved options, per-stage state
  selection.jsonl           # selected index rows (no native content)
  events.jsonl              # stage start/end/skip events
  groups/<group>/
    inputs/dataset.jsonl    # exact native subset for this group
    inputs/ids.txt
    plan.json
    logs/<stage>.log
    ...                     # backend artifacts
  results.jsonl
  summary.json
  report.md
```

Groups and stages run sequentially. Concurrency stays inside each backend
(`-t` for CVDP, `--n-concurrent` and `--max_workers` for HWE), which avoids
Docker network and workspace races. A lock file prevents two processes from
writing the same run directory.

## Resume

Every stage has a fingerprint over the manifest hash, adapter and tool version,
selection hash, group, stage, argv/cwd/env names, and condition.

- A stage is skipped only when it previously finished `ok`, the fingerprint
  matches, and its completion check passes.
- `--resume` first verifies the manifest, adapter version, tool version, and
  selection hash. Any difference is reported and refused.
- A CVDP `raw_result.json` whose key set differs from the group selection is
  archived as `raw_result.partial.<timestamp>.json`; the group reruns.
- An HWE evaluator workdir is archived as `*.stale.<timestamp>` when the patch
  file or dataset changed.
- `--force-rebuild` archives the whole run directory and starts fresh.
- `--only-stage` limits work to named stages (others are recorded as skipped).

Failed required stages fail the group. `collect_on_failure` stages (HWE
`harbor`, `verify`, `evaluate`) still let the run collect whatever artifacts
exist. Without `--keep-going`, the first failed group stops later groups, and
their cases are recorded as `missing`.

## Result contract

`results.jsonl` has one row per selected case:

```json
{
  "schema_version": "1.0",
  "run_id": "cvdp-20261021T022732Z",
  "hqbd_id": "hqbd_hwe_ibex_2232",
  "source": "HWE-bench",
  "native_id": "lowRISC/ibex#2232",
  "representation": "hwe_native",
  "task_type": "bug_repair",
  "group_id": "lowrisc__ibex",
  "status": "resolved",
  "resolved": true,
  "score": 1.0,
  "tests_passed": 1,
  "tests_failed": 0,
  "tests_total": 1,
  "error_category": null,
  "error_message": null,
  "backend": { "framework": "harbor", "image": "ghcr.io/pku-liang/lowrisc_m_ibex:pr-2232" },
  "artifacts": { "patches": "groups/lowrisc__ibex/patches/patches.jsonl" }
}
```

Statuses: `resolved`, `unresolved`, `no_patch`, `error`, `missing`,
`skipped`, `timeout`.

`summary.json` reports counts, two denominators, and breakdowns:

- `resolve_rate_selected` = resolved / selected.
- `resolve_rate_submitted` = resolved / (resolved + unresolved + error).
  HWE's upstream report omits empty patches from its denominator, so use this
  one only when comparing to upstream numbers.

The normalizer and `write_outputs` drop `fix_patch`, `tb_patch`, `test_patch`,
`tb_script`, `problem_statement`, and `prompt` keys at any depth. Artifact paths
may point at native files inside the run directory; those files are still
evaluation material and must not be exposed to an agent under test.

## Maintenance

```bash
python -m hqbd_bench dataset list
python -m hqbd_bench dataset show cvdp
python -m hqbd_bench dataset validate cvdp --strict
python -m hqbd_bench dataset validate --all --strict --check-hashes
python -m hqbd_bench dataset new mybench --adapter generic
python -m hqbd_bench adapter list
python -m hqbd_bench adapter doctor hwe
python -m hqbd_bench doctor --all
python -m hqbd_bench runs list
python -m hqbd_bench runs show <run-id>
python -m hqbd_bench collect <run-id> --force
python -m hqbd_bench report <run-id> --format md
```

Read-only data helpers live in [`tools/`](../tools/README.md): print one case,
compute category/difficulty statistics, create deterministic subsets, convert
JSONL/YAML, and merge files by ID. They never execute an evaluation and never
modify their input.

`dataset validate` checks manifest loading, unique IDs, native pointers,
expected counts, ID mapping coverage, and adapter-specific invariants
(CVDP mode tokens, heavy bundles, comprehension harness flags, HWE native
evaluation fields and image names). `--check-hashes` verifies every output
listed in `datasets/hqbd_v0.1.0/BUILD_MANIFEST.json`.

Exit codes: `0` success, `1` validation or run failure, `2` usage error.

## Adding a dataset

Put data under `datasets/<name>/`, the runner under `evaluators/<name>/`, and a
manifest under `benchmarks/<name>.json`. See
[benchmarks/README.md](../benchmarks/README.md). A simple backend needs only a
manifest; a complex one adds a plugin adapter. The core runner does not need to
change.

## Tests

```bash
uv run --no-project python -m unittest discover -s tests -t . -v
```

The suite uses synthetic fixtures and temporary directories; it does not need
Docker, the real datasets, or network access.

## Integrity rules

- `datasets/` and `evaluators/` are never written by the orchestrator, except
  for evaluator-local environments and caches created by their own tooling.
- Upstream runners and datasets are not modified.
- `runs/` is gitignored.
- `fix_patch` and `tb_script` never enter `results.jsonl`, `summary.json`,
  `report.md`, plans, or stage logs.
- Ground-truth fields are not copied into prompts or agent workspaces.
