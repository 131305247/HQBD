# rtl-benchmark (CVDP)

NVIDIA [CVDP Benchmark](https://github.com/NVlabs/cvdp_benchmark)(arXiv:2506.14074)的 fork —— 在 Docker 沙箱里用 cocotb/iverilog/yosys/verilator 评测 LLM/Agent 做 Verilog RTL 设计与验证的能力。本仓库已加入 tenyunc GLM-5.2 接入与 Windows 适配。

## 快速上手

**1. 构建 OSS 仿真镜像**(国内网络用 `docker/Dockerfile.sim.local`,源码在宿主预 clone 到 `docker/_sources/`,避免容器内访问 github):
```bash
docker build -f docker/Dockerfile.sim.local -t nvidia/cvdp-sim:v1.0.0 .
```

**2. 配环境**(Python 3.12):
```bash
python -m venv cvdp_env
source cvdp_env/Scripts/activate   # Windows Git Bash;Linux/Mac 用 cvdp_env/bin/activate
pip install -r requirements.txt
cp .env.example .env               # 然后填入 API key(见下)
```

**3. 验链路**(黄金解冒烟,不需 API key,应 100% pass):
```bash
./run_benchmark.py -f example_dataset/cvdp_v1.1.0_example_nonagentic_code_generation_no_commercial_with_solutions.jsonl
cat work/report.txt
```

**4. LLM 评测**(需 key):
```bash
./run_benchmark.py -f <dataset>.jsonl -i <problem_id> -l -m GLM-5.2 -p work_glm
cat work_glm/report.txt
```

## tenyunc GLM-5.2 接入

`.env` 配置(参考 `.env.example` 的 tenyunc 段;**真实 key 请填在本地 `.env`,勿提交**):
```
TENYUNC_API_KEY=sk-...            # 你的 key,放本地 .env
TENYUNC_BASE_URL=https://preview.llm.tenyunc.com/v1
OPENAI_USER_KEY=sk-...            # 与上面同 key,框架 fallback 用
DEFAULT_MODEL=GLM-5.2
CUSTOM_MODEL_FACTORY=tenyunc_factory.py
MODEL_TIMEOUT=300                 # GLM 生成完整 RTL 要 60-90s,默认 60 太紧
OSS_SIM_IMAGE=nvidia/cvdp-sim:v1.0.0
```
`tenyunc_factory.py` 注册前缀 `glm`/`GLM-5.2`,把请求路由到 OpenAI 兼容端点。`-m GLM-5.2` 命中。

## Windows 适配要点

本仓库相对原版打了适配补丁(升级/重装要重打):
- `src/repository.py` + `src/dataset_processor.py`:`os.sync()` 加 `hasattr` 守卫(Linux only)
- `src/repository.py`:路径 `replace('\\','/')`、bash 显式调用、`MSYS_NO_PATHCONV` 防 Git Bash 路径转换
- `src/report.py`:UTF-8 IO 编码(Windows 默认 GBK 报错)
- 读真实数据集 JSONL 的 `open()` 加 `encoding='utf-8'`(run_benchmark.py / dataset_processor.py / commercial_eda.py / data_transformer.py)
- `docker/Dockerfile.sim.local`:本地源码 COPY + yosys `ABCREV=default` + cocotbext-pcie 本地安装

## 数据集

- `example_dataset/`:带 `_with_solutions` 的小样本,冒烟验证用(含 golden 解)
- 完整数据集从 [Hugging Face nvidia/cvdp-benchmark-dataset](https://huggingface.co/datasets/nvidia/cvdp-benchmark-dataset) 下载,用 `-f <path>` 指向。三类任务:nonagentic 代码生成 / 代码理解 / agentic 代码生成(+ agentic-heavy 指向 git bundle 仓库)。

## 文档

详见 `README.md` / `README_NON_AGENTIC.md` / `README_AGENTIC.md` / `README_FULL.md` / `LOCAL_INFERENCE_GUIDE.md`。
