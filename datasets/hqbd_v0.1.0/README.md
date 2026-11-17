# HQBD v0.1.0 — CVDP + HWE-bench fused release

HQBD v0.1.0 is a fused hardware benchmark dataset built from two sources:

| Source | Version | Cases | Native format |
|---|---|---:|---|
| CVDP | 1.1.0 | 872 | CVDP JSONL (`copilot` / `agentic` / `agentic_heavy`) |
| HWE-bench | 0.1.0 snapshot 2026-09-09 | 417 | HWE native JSONL (repo-backed bug repair) |
| **Total** |  | **1289** | two formats, one unified index |

The release keeps each source in its native format. It does **not** rewrite HWE
records into the CVDP agentic schema, because the two sources are consumed by
different runners.

## Files

| File | Rows | Description |
|---|---:|---|
| `hqbd_v0.1.0_nonagentic_code_generation_no_commercial.jsonl` | 302 | CVDP code generation, no-commercial split |
| `hqbd_v0.1.0_nonagentic_code_generation_commercial.jsonl` | 187 | CVDP code generation, commercial split |
| `hqbd_v0.1.0_agentic_code_generation_no_commercial.jsonl` | 92 | CVDP agentic code generation, no-commercial |
| `hqbd_v0.1.0_agentic_code_generation_commercial.jsonl` | 68 | CVDP agentic code generation, commercial |
| `hqbd_v0.1.0_agentic_heavy_code_generation_no_commercial.jsonl` | 75 | CVDP heavy agentic, external git bundle |
| `hqbd_v0.1.0_agentic_heavy_code_generation_commercial.jsonl` | 25 | CVDP heavy agentic, commercial |
| `hqbd_v0.1.0_nonagentic_code_comprehension.jsonl` | 123 | CVDP code comprehension, no executable harness |
| `hqbd_v0.1.0_hwe_bench_repair.jsonl` | 417 | HWE-bench merged, native fields + `hqbd_*` metadata |
| `hqbd_v0.1.0_hwe_bench_repair_by_repo/*.jsonl` | 417 | Same records split into 6 repositories |
| `hqbd_v0.1.0_unified_index.jsonl` | 1289 | Cross-source metadata index with file:line pointers |
| `hqbd_v0.1.0_unified_index.csv` | 1289 | Flattened convenience mirror; JSONL is canonical |
| `ID_MAPPING.csv` | 1289 | HQBD ID → source ID + file:line |
| `BUNDLE_MAPPING.csv` | 40 | Heavy bundle names and paths |
| `BUILD_MANIFEST.json` |  | Input/output SHA-256 and counts |

The seven CVDP files are byte-for-byte copies of the v0.0.1 files with only the
version prefix changed; case IDs are unchanged.

HWE per-repository counts:

| Repository | Cases |
|---|---:|
| `lowRISC/opentitan` | 245 |
| `OpenXiangShan/XiangShan` | 54 |
| `lowRISC/ibex` | 35 |
| `openhwgroup/cva6` | 35 |
| `chipsalliance/rocket-chip` | 32 |
| `chipsalliance/caliptra-rtl` | 16 |

## HWE native records

Every HWE record preserves the upstream fields. Only two changes are made:

- the numeric GitHub PR `id` becomes `source_pr_id`;
- the top-level `id` becomes `hqbd_hwe_<repo_slug>_<pr-number>`, for example
  `hqbd_hwe_ibex_2232`.

HQBD metadata is appended under the `hqbd_` prefix:
`hqbd_schema_version`, `hqbd_source_dataset`, `hqbd_source_version`,
`hqbd_source_snapshot_date`, `hqbd_source_id`, `hqbd_source_url`,
`hqbd_task_type`, `hqbd_language`, `hqbd_representation`, and
`hqbd_runnability`.

> **Evaluation material:** HWE records contain `fix_patch` (the ground-truth
> fix) and `tb_script` (the hidden fail-to-pass test). Never expose either
> field to an agent under evaluation.

## Runnability

| Status | Cases | Meaning |
|---|---:|---|
| `cvdp_harness` | 649 | CVDP generation case with a harness |
| `cvdp_heavy_bundle` | 100 | CVDP heavy case; needs an external git bundle |
| `comprehension_no_harness` | 123 | CVDP comprehension; no executable harness |
| `docker_image_published` | 172 | HWE case with a published image |
| `local_build_required` | 245 | OpenTitan; requires a local image and Synopsys VCS |

`runnable` is true for 921 cases (649 + 100 + 172). Heavy cases are runnable
only with the bundles listed in `BUNDLE_MAPPING.csv`; OpenTitan cases are not
runnable without a local build and an EDA license.

`image_verified` is `false` for every HWE case: the generator does not contact
the container registry. The image names follow the upstream convention but are
not a verified availability claim.

## Unified index

`hqbd_v0.1.0_unified_index.jsonl` has one row per case with a stable schema.
Source-specific fields are `null` when not applicable. The index stores
metadata and pointers only; load content from `source_file` at `source_line`.

Useful filters:

```python
import json

rows = [json.loads(line) for line in open(
    "hqbd_v0.1.0_unified_index.jsonl", encoding="utf-8")]

runnable_hwe = [r for r in rows
                if r["source"] == "HWE-bench" and r["runnable"]]
no_eda = [r for r in rows
          if r["source"] == "CVDP" and not r["requires_eda_license"]]
commercial_ok = [r for r in rows if r["usage_license"] == "commercial_ok"]
```

`usage_license` values:

| Value | Cases | Meaning |
|---|---:|---|
| `no_commercial_only` | 469 | CVDP no-commercial split |
| `commercial_ok` | 280 | CVDP commercial split |
| `cc_by_4.0` | 123 | CVDP comprehension (CC BY 4.0) |
| `upstream_unverified` | 417 | HWE-bench: dataset-level Apache-2.0; per-case upstream license not verified |

## Runners

There is no single runner for both sources.

- CVDP files: use the CVDP/RTL benchmark runner. The ID prefix keeps the
  upstream format detector working (`id.split("_")[1]`).
- HWE files: use the HWE-bench Harbor flow and the per-repository files. Do not
  feed HWE files to the CVDP runner.

See `../docs/BENCHMARK.md` for the flow and image commands.

## Heavy bundles

The 1.37 GB of heavy git bundles are **not duplicated** in this release. They
remain in:

```text
../hqbd_v0.0.1/hqbd_v0.0.1_agentic_heavy_code_generation_public/
```

Point the runner at that directory, for example
`CVDP_HEAVY_REPOS_PATH=../hqbd_v0.0.1/hqbd_v0.0.1_agentic_heavy_code_generation_public`.
See `HEAVY_BUNDLES.md`.

## Licensing

- CVDP content: see `LICENSE` and `NOTICE`; the no-commercial and commercial
  split membership determines `usage_license`.
- HWE-bench content: dataset project licensed Apache-2.0; see
  `LICENSE-HWE-bench`. Per-case upstream repository licenses were not verified
  in this package; see `licenses/upstream-repos.md`.

## Rebuild and validate

From the HQBD repository root:

```bash
uv run --no-project python tools/build_hqbd_v0.1.0.py --check
uv run --no-project python tools/validate_hqbd_v0.1.0.py --root .
```

The build is deterministic: the same inputs produce byte-identical outputs.
