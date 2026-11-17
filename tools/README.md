# tools/

只读辅助工具，用于检查、统计、抽取和转换 `datasets/` 下的原生 JSONL。
这些脚本不会修改输入文件；只有显式指定输出文件时才写入。

本文中的路径都相对于本包根目录，`<HQBD_v0.1.0_ROOT>` 表示你实际保存本包的目录。

## 工具

| 工具 | 用途 |
|---|---|
| `print_testcase.py` | 按 ID 或行号打印单条题目，便于人工检查 prompt、context、output/patch 和 harness |
| `dataset_analyzer.py` | 统计 category、难度、token 长度、分布和离群点 |
| `dataset_subset_creator.py` | 按 ID、category、难度或数量生成确定性子集 |
| `jsonl_to_yaml.py` | JSONL 与 YAML 互转，支持 roundtrip 校验 |
| `merge_dataset_files.py` | 按顶层 `id` 合并多个 JSONL，先给出的文件优先级更高 |

这些脚本源自 NVIDIA CVDP benchmark 的辅助工具，保留原始 SPDX Apache-2.0 头。

## 安装依赖

```bash
cd <HQBD_v0.1.0_ROOT>
python -m pip install -r tools/requirements.txt
```

也可以不建环境，直接用 `uv` 运行：

```bash
uv run --no-project --with-requirements tools/requirements.txt python tools/print_testcase.py ...
```

## 示例

打印一条题目，不展开文件内容：

```bash
python tools/print_testcase.py \
  datasets/hqbd_v0.1.0/hqbd_v0.1.0_agentic_code_generation_no_commercial.jsonl \
  -i hqbd_agentic_64b66b_codec_0001 \
  --no-files
```

分析一个原生文件：

```bash
python tools/dataset_analyzer.py \
  datasets/hqbd_v0.1.0/hqbd_v0.1.0_nonagentic_code_generation_no_commercial.jsonl \
  -o analysis_nonagentic_no_comm
```

生成确定性子集：

```bash
python tools/dataset_subset_creator.py \
  datasets/hqbd_v0.1.0/hqbd_v0.1.0_agentic_code_generation_no_commercial.jsonl \
  subset_agentic_3.jsonl --total 3 --seed 42
```

JSONL 转 YAML 并校验 roundtrip：

```bash
python tools/jsonl_to_yaml.py subset_agentic_3.jsonl --test-roundtrip
```

合并修订记录：

```bash
python tools/merge_dataset_files.py revised.jsonl base.jsonl -o merged.jsonl
```

## 边界

- 这些工具只处理 CVDP 风格的原生 JSONL，不执行评测，也不读取 `runs/`。
- HWE 记录保留上游字段，打印时可能包含 `fix_patch`、`tb_script` 等评测材料；不要把输出交给被测 agent，也不要写入共享日志。
- 构建、校验发布数据和更新哈希的工具属于发布流程，不随本包分发；见 `使用说明书.md` 第 8.2 节。
- 工具不会改动 `datasets/hqbd_v0.1.0/` 中的发布文件；`dataset validate --check-hashes` 仍会按 `BUILD_MANIFEST.json` 校验。
