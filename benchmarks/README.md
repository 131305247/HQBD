# Dataset manifests

Every dataset that `hqbd_bench` can run has one manifest under this directory.
The manifest describes where the cases come from and which adapter knows how
to execute them; it never contains evaluation material.

Directory conventions:

| Directory | Holds |
|---|---|
| `datasets/<name>/` | native dataset files, index, mappings, and licenses |
| `evaluators/<name>/` | the evaluation scripts/runner for that backend |
| `benchmarks/<name>.json` | the manifest that connects the two |
| `hqbd_bench/` | the shared orchestration layer |

The two built-in manifests are:

| Manifest | Adapter | Records | Default runnable |
|---|---|---:|---:|
| `cvdp.json` | `cvdp` | 872 | 749 |
| `hwe.json` | `hwe` | 417 | 172 |

Both read the same fused index
(`datasets/hqbd_v0.1.0/hqbd_v0.1.0_unified_index.jsonl`) and use `select` to
pin themselves to their source.

## Schema

```json
{
  "schema_version": 1,
  "name": "demo",
  "description": "One line for `dataset list`",
  "adapter": "generic",
  "select": { "source": "MyDataset" },
  "catalog": {
    "path": "datasets/hqbd_v0.1.0/hqbd_v0.1.0_unified_index.jsonl",
    "id_field": "hqbd_id",
    "native_base": "datasets/hqbd_v0.1.0",
    "native_file_field": "source_file",
    "native_line_field": "source_line"
  },
  "defaults": { "runnable": true, "mode": "agent" },
  "backend": { "root": "evaluators/my-backend", "python": ["python"] },
  "expect": { "records": 100, "runnable": 80 },
  "generic": { "...": "see below" }
}
```

- `schema_version` must be `1`.
- `select` is optional. Each key/value must match the catalog row, which is how
  two datasets can share one fused index. Without it, every row belongs to the
  dataset.
- `catalog.path`, `native_base`, and `backend.root` are relative to the package
  root. Absolute paths are also accepted.
- `defaults.runnable` selects runnable cases unless `--runnable/--no-runnable`
  is passed explicitly.
- `defaults.mode` is `llm` for CVDP and `agent` for HWE; it is used when
  `--mode` is omitted.
- `expect` is advisory metadata checked by `dataset validate`; it does not
  filter the selection.
- `{source_file, source_line}` is a 1-based line pointer into a native JSONL
  file. The native record's `id` must equal the catalog ID.

## Add a dataset without code

1. Put the native files under `datasets/<name>/` and the runner under
   `evaluators/<name>/`.
2. Add one index JSONL row per case with `hqbd_id`, `source`, `source_file`,
   and `source_line`, plus any metadata you want to filter on.
3. Declare `generic.stages` and `generic.collect` in the manifest:

```json
{
  "adapter": "generic",
  "generic": {
    "group_by": ["source"],
    "stages": [
      {
        "id": "run",
        "commands": [
          {
            "argv": [
              "{backend_root}/run_dataset.py",
              "--dataset", "{dataset}",
              "--output", "{group}/results.json"
            ],
            "cwd": "{backend_root}",
            "timeout_s": 3600
          }
        ],
        "expected_outputs": ["{group}/results.json"]
      }
    ],
    "collect": {
      "results_path": "{group}/results.json",
      "id_field": "id",
      "status_field": "status",
      "status_map": { "pass": "resolved", "fail": "unresolved" },
      "resolved_field": "passed",
      "score_field": "score"
    }
  }
}
```

Placeholders available in argv, cwd, and paths:

| Placeholder | Value |
|---|---|
| `{root}` | HQBD root |
| `{backend_root}` | `backend.root` |
| `{run_dir}` | `runs/<run-id>` |
| `{group}` | this group's directory |
| `{dataset}` | the group's materialized native JSONL |
| `{ids_file}` | one HQBD ID per line for the group |
| `{run_id}` | run identifier |
| `{env:NAME}` | the value of environment variable `NAME` |

Commands are argv lists run without a shell. A non-zero exit fails the stage.
`generic.collect` accepts a JSON object, a JSON list, a JSONL file, or an object
with a `results` list. A case missing from the results is recorded as
`missing`; a status outside the contract becomes `error`.

Recommended starting point:

```bash
python -m hqbd_bench dataset new mybench --adapter generic
python -m hqbd_bench dataset validate mybench --strict
python -m hqbd_bench run mybench --dry-run
```

## Add a dataset that needs custom logic

Set `"adapter": "benchmarks.plugins.mybench:MybenchAdapter"` and generate a
skeleton with `dataset new mybench --adapter python`. Implement the five
methods (`validate`, `doctor`, `validate_options`, `plan`, `collect`) from
`hqbd_bench/adapters/base.py`. The runner, resume handling, result schema, and
reports are reused unchanged.

## Safety

Native files may contain evaluation material such as HWE-bench `fix_patch` or
`tb_script`. Keep them under the data tree, never under `runs/` in a form that
an agent can read, and let the adapter extract only the fields needed to
execute the case. `hqbd_bench` drops known evaluation keys from
`results.jsonl`, `summary.json`, and `report.md`.
