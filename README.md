# Personality Computing Data Engineering

本仓库包含文化作品数据管道，以及一条以 ModernBERT 为编码器的多维人格向量回归基线。

## 项目结构

- `DataSet/`：作品、人物及向量数据。训练时将原始作品资料与 CPR 标注分开读取。
- `DataSetsCode/`：嵌套 JSON 读取、文本拼接、标签展开、PyTorch Dataset 和动态 padding。
- `models/`：ModernBERT 编码器与多输出回归头。
- `trainers/`：训练、数据切分、指标。
- `inference/`：训练产物推理入口。
- `configs/model.yaml`：模型、数据、目标向量及超参数。
- `Prompt/`、`Script/`、`PipeLineGuide/`：数据生成与工程文档。

## 环境

建议 Python 3.10–3.12。当前依赖锁定 PyTorch CUDA 13.0 wheel，与本机 NVIDIA 596.08 驱动兼容。

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

若 PyPI 在中国大陆出现 TLS/速度问题，可保留 PyTorch 官方额外索引并使用镜像：

```powershell
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

检查 GPU：

```powershell
python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU')"
```

## 训练

默认使用 `answerdotai/ModernBERT-base`。输入严格采用 `work_generating_prompt.txt` 规定的原始字段（作品介绍、文化影响、类型、创作者等），标签采用 `work_psychInfo_generating_prompt.txt` 规定的共享 `psychology_vector`。模型不会把生成后的 `semantic` 字段作为输入，以避免标签泄漏。首次运行会从 Hugging Face 下载权重。

```powershell
python -m trainers.train --config configs/model.yaml
```

先验证完整链路（只训练一步）：

```powershell
python -m trainers.train --config configs/model.yaml --smoke-test
```

预测：

```powershell
python -m inference.predict --text "一部描写家族兴衰、个体命运与复杂情感的长篇小说。"
```

最终标注文件生成后，把 `annotation_path` 指向该文件。要更换标签，在配置中修改 `target_path`，例如 `experience_vector`、`personality_affinity` 或 `psychology_vector.emotion`。嵌套数值维度会稳定展开，标签名写入输出目录供推理复用。

> 现有 `work_vector_list.json` 只是旧的 10 条过渡样例，并非最终文件契约；`v2`–`v5` 目前为空。正式微调应使用 Prompt 生成的完整最终标注，并按作品或人物分组切分训练/验证/测试集，避免同源内容泄漏。
