# datasets/

所有数据集都放在本目录下，一个数据集一个子目录：

```text
datasets/
├── hqbd_v0.1.0/     # 当前发布数据：CVDP 872 + HWE-bench 417
└── hqbd_v0.0.1/
    └── hqbd_v0.0.1_agentic_heavy_code_generation_public/   # 可选，当前为空
```

## 当前内容

### `hqbd_v0.1.0/`

HQBD v0.1.0 发布数据，内部结构保持不变：

- `hqbd_v0.1.0_unified_index.jsonl`：1289 条统一索引，`hqbd_bench` 的选样入口；
- 7 个 CVDP JSONL、HWE 全量与 per-repo JSONL；
- `ID_MAPPING.csv`、`BUNDLE_MAPPING.csv`、`BUILD_MANIFEST.json`、许可证。

不要改写目录内文件；`dataset validate --check-hashes` 会按
`BUILD_MANIFEST.json` 校验 25 个发布文件。

### `hqbd_v0.0.1/`

这里**不是**旧版数据集，只是 v0.1.0 heavy 任务引用的 40 个 Git bundle 的落点。
本包中该目录**默认为空**：heavy bundle 是可选依赖，不随包分发。

`hqbd_v0.1.0/BUNDLE_MAPPING.csv` 和 `BUILD_MANIFEST.json` 使用
`../hqbd_v0.0.1/hqbd_v0.0.1_agentic_heavy_code_generation_public` 这个相对路径，
因此两级的目录名都不能改。

需要跑 100 条 heavy 用例时，把 bundle 恢复进来：

```bash
cp -a <HQBD_DEV_ROOT>/hqbd_v0.0.1/hqbd_v0.0.1_agentic_heavy_code_generation_public/. \
      datasets/hqbd_v0.0.1/hqbd_v0.0.1_agentic_heavy_code_generation_public/
```

恢复后 `python -m hqbd_bench dataset validate --all --strict --check-hashes`
应通过；未恢复时 `cvdp.heavy_bundles` 为 warn，非 heavy 用例不受影响。

如果 bundle 放在别处，也可以不改目录，直接在运行时指定：

```bash
python -m hqbd_bench run cvdp --env CVDP_HEAVY_REPOS_PATH=/path/to/payload ...
```

## 加新数据集

1. 新建 `datasets/<name>/`，放入原生文件和一份索引 JSONL；
2. 索引每行至少包含 `hqbd_id`、`source`、`source_file`、`source_line`，
   `{source_file, source_line}` 必须能指回原生记录；
3. 在 `benchmarks/<name>.json` 中声明 `catalog.path`、`catalog.native_base`、
   `backend.root`、`select` 和 `expect`；
4. 运行 `python -m hqbd_bench dataset validate <name> --strict`。

索引字段规范见 `benchmarks/README.md` 和 `docs/ORCHESTRATOR.md`。
