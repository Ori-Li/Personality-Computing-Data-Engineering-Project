# DeepSeek V4 Pro API 接入

本项目通过 DeepSeek 的 OpenAI 兼容接口访问 `deepseek-v4-pro`，API Key 只从环境变量读取，不写入仓库。

## 1. 创建并配置 API Key

在 DeepSeek 开放平台创建 API Key。PowerShell 当前会话中配置：

```powershell
$env:DEEPSEEK_API_KEY="你的 API Key"
$env:DEEPSEEK_MODEL="deepseek-v4-pro"
```

也可复制 `.env.example` 为 `.env` 作为本地记录，但 Python 不会自动加载 `.env`；运行命令前仍需导入环境变量，或由部署平台的 Secret 管理器注入。`.env` 已被 Git 忽略。

## 2. 验证连接

只验证密钥、网络和 V4 Pro 模型权限（不产生对话输出）：

```powershell
python -m database.deepseek_client --check
```

发送一个低成本测试请求：

```powershell
python -m database.deepseek_client --prompt "请只回复：连接成功" --max-tokens 16 --no-thinking
```

## 3. 在数据管道中调用

```python
from database.deepseek_client import DeepSeekClient

client = DeepSeekClient()
result = client.ask(
    "请以 JSON 输出人物实体；json 格式为 {\"name\": \"...\"}",
    system="你是人格计算数据工程助手。只输出合法 json。",
    json_output=True,
    thinking=False,
    max_tokens=2048,
)
```

完整 API 响应可通过 `client.chat(...)` 获得。生产环境建议在 Secret 管理器中配置 `DEEPSEEK_API_KEY`，并为批处理增加并发限制、重试、用量记录和输出契约校验。

## 4. 批量发送作品列表并保存

准备一个 UTF-8 文本文件，每行一个作品名，例如 `DataSetRaw/work_names.txt`：

```text
百年孤独
千与千寻
星月夜
```

使用默认的 `Prompt/work_generating_prompt.txt` 调用 V4 Pro：

```bat
python -m database.deepseek_batch_generate --list DataSetRaw\work_names.txt --batch works_20260718_01 --batch-size 10 --no-thinking
```

结果自动保存到 `DataSetRaw/deepseek_generations/works_20260718_01/`：

- `combined.json`：全部批次合并后的作品 JSON；
- `manifest.json`：批次状态、模型、输入数量及失败信息；
- `batch_XXXX_prompt.txt`：每批实际发送的用户 Prompt；
- `batch_XXXX_response.json`：完整 API 响应；
- `batch_XXXX_content.json`：解析后的该批作品数组。

命令中断后，使用同一 `--batch` 再次运行会跳过已有的成功批次。更换 `--prompt` 可使用其他 Prompt 文件；若需要更强推理，可移除 `--no-thinking`，但会增加耗时和费用。

## 5. 续跑七组作品 CRP

现有 CRP 会从 `workcrpsetN.normalized.json` 或已经续跑的 `workcrpsetN.v4pro.json` 自动恢复，不会重新生成已完成作品。默认一次只新增一部：

```bat
python -m database.deepseek_crp_resume --set 1 --max-items 1
```

续跑器默认允许单部作品最多输出 32768 token，为完整的 `vector_facts` 和 JSON 闭合预留空间。API 按实际生成 token 计费，并不会因为上限设为 32768 就按 32768 收费。如有需要，可用 `--max-tokens` 显式调整。

set1 的续跑结果直接合并保存到 `DataSetRaw/entity/workcrpset1.json`。第一次写回前，原始文件会自动备份为 `workcrpset1.pre-resume.json`；已有的 `workcrpset1.normalized.json` 和 `workcrpset1.v4pro.json` 进度也会按 `workId` 合并去重。每部作品的完整原始 API 响应另存到 `DataSetRaw/entity/deepseek_crp_raw/set1/`。再次运行相同命令会从下一部继续。

一次检查 set1 至 set7，并让每个已有作品数据的 set 最多新增一部：

```bat
python -m database.deepseek_crp_resume --max-items 1
```

空的 `workentitysetN.json` 会显示 `waiting` 并被安全跳过。需要提高速度时可增大 `--max-items`；添加 `--no-thinking` 可降低费用，但 CRP 标注质量可能下降。
