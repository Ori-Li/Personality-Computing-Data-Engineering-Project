# 人物、作品与 CPR 数据管道渐进式更新与 Agent 复用手册

## 1. 文档目的

本文面向两类读者：

- 需要理解、检查和维护数据管道的人类开发者；
- 需要按批次生成、校验、导入和修正数据的 Agent。

管道用于把独立 JSON 数据集安全地写入后端 MySQL，并维护以下关系：

```text
人物实体 ──创作关系──> 作品实体
   │                     │
   ├─名称、国籍、领域     ├─体裁、子领域、年代
   │                     │
   └─────────────────────└──> CPR 档案
                              ├─维度值
                              ├─事实证据
                              ├─维度—证据映射
                              └─人格偏好投影
```

核心原则是：**内容数据保存在 JSON，Python 只承担契约校验、实体解析、事务写入、审计和回滚。** 不应把具体人物、作品或心理标注硬编码进导入脚本。

---

## 2. 能实现的目标

### 2.1 实体数据

- 批量导入人物和作品；
- 支持一个人物对应多个作品、一个作品对应多个创作者；
- 支持主领域及多个子领域；
- 使用确定性 BIGINT ID，使相同逻辑实体重复处理时保持稳定；
- 对已有数据库进行增量协作者、作品和关系补充；
- 在导入前分析名称冲突、枚举错误、断链和多义实体。

### 2.2 CPR 心理数据

- 导出数据库中尚未具备 CPR 的作品；
- 接收 API 或人工生成的独立 CPR JSON；
- 保存完整原始 JSON，同时拆分为可查询的关系表；
- 建立作品、CPR、维度、证据和偏好投影之间的跨表映射；
- 支持知识不足时的稀疏标注：无依据维度保存为 `NULL + INSUFFICIENT`；
- 对偏好投影进行单独生成、校准和替换。

### 2.3 运维能力

- 分析但不写库；
- 事务化导入；
- 生成导入清单及受影响数据快照；
- 按清单精准回滚某个批次；
- 审计覆盖率、重复当前版本、断链、证据映射和 JSON/关系表一致性；
- 必要时清空全部实体业务数据，同时保留表结构、Flyway、枚举和维度定义。

---

## 3. 项目文件结构与职责

```text
Data Engineering/
├─ Prompt/
│  ├─ character_generating_prompt.txt       人物 JSON 生成契约
│  ├─ work_generating_prompt.txt            作品 JSON 生成契约
│  └─ work_psychInfo_generating_prompt.txt  CPR-1.2 生成契约
├─ DataSet/
│  └─ *.json                                人工确认后待导入的规范数据
├─ DataSetRaw/
│  ├─ deepseek_runs/<batch>/                DeepSeek 原始响应的提取、隔离与验证结果
│  ├─ reports/                              分析、枚举契约和推荐效果报告
│  └─ import_runs/<runId>/
│     ├─ analysis.json                      结构与质量分析报告
│     └─ manifest.json                      写入 ID、快照和回滚依据
├─ database/
│  ├─ config.py                             从环境变量读取数据库配置
│  ├─ mysql_client.py                       UTF-8 连接与事务上下文
│  ├─ import_prompt_dataset.py              人物/作品契约与底层导入
│  ├─ dataset_pipeline.py                   实体分析、导入、状态、回滚
│  ├─ deepseek_dataset_intake.py            DeepSeek 原始响应提取、清洗、验证、隔离
│  ├─ enum_contract_check.py                Prompt/Python/Backend/数据库枚举契约检查
│  ├─ recommendation_prior_evaluation.py    推荐模型先验离线消融报告
│  ├─ pipeline_acceptance.py                 一键只读验收入口
│  ├─ entity_enrichment_pipeline.py         已有实体的增量扩充
│  ├─ crp_dataset_pipeline.py               CPR 导出、校验、导入、状态
│  ├─ affinity_projection_pipeline.py       偏好投影生成、校验、写入
│  ├─ crp_dimension_catalog.py              维度代码及中文名称权威目录
│  ├─ sync_crp_dimension_catalog.py         同步/检查数据库维度定义
│  ├─ generate_crp_flyway_seed.py           生成维度定义 Flyway 种子
│  ├─ test_entity_contract.py               实体契约测试
│  ├─ audit_curated_cpr.py                  CPR 证据完整性审计参考
│  ├─ clear_all_entity_data.py              全实体业务数据清空工具
│  └─ README.md                             常用命令速查
├─ 人物与作品批量实体生成及数据库导入规范.md
└─ CRP关系数据库存储设计规范.md
```

### 工具分级

| 文件 | 定位 | 生产建议 |
|---|---|---|
| `dataset_pipeline.py` | 人物/作品主数据管道 | 推荐 |
| `entity_enrichment_pipeline.py` | 已有实体增量补丁 | 推荐 |
| `crp_dataset_pipeline.py` | 完整 CPR 数据导入 | 推荐 |
| `affinity_projection_pipeline.py` | 投影独立更新 | 可用，但应审查算法版本 |
| `generate_missing_crp.py` | 旧的本地测试生成器 | **禁止生成生产数据**；包含规则和哈希扰动 |
| `knowledge_density_crp_pipeline.py` | 稀疏策略原型 | 仅供结构测试，不替代高质量模型标注 |
| `apply_curated_cpr_batch.py` | 特定批次人工清洗 | 一次性维护工具，不应作为通用入口 |
| `clear_all_entity_data.py` | 全库业务数据清空 | 高风险，仅在明确要求时使用 |

---

## 4. 数据库边界

### 4.1 业务实体数据

主要包括：

- `t_character_info`：人物主体；
- `t_character_name`：原名、中文名及其他名称；
- `t_character_work`：作品主体；
- `t_work_creator_relation`：作品—创作者多对多关系；
- `t_work_subcategory_relation`：作品—子领域关系；
- 其他以 `t_character*`、`t_work*` 开头的业务表。

### 4.2 CPR 数据

- `t_crp_generation_run`：一次生成/导入批次；
- `t_crp_profile`：某作品某版本的 CPR，`raw_payload` 保存原始 JSON；
- `t_crp_dimension_definition`：维度定义，属于基础结构数据；
- `t_crp_dimension_value`：每个档案的维度值；
- `t_crp_evidence`：作品可观察事实；
- `t_crp_dimension_evidence`：维度与证据的明确映射；
- `t_crp_projection_value`：Ni/Ne/Ti/Te/Fi/Fe/Si/Se 等偏好投影；
- `t_crp_source_record`、`t_crp_embedding`：来源及向量扩展能力。

### 4.3 不应随普通数据批次删除的内容

- Flyway schema history；
- 表结构和约束；
- 国家、领域、体裁、朝代等基础枚举；
- `t_crp_dimension_definition` 及其中文名称；
- 用户账号和与本数据集无关的系统配置。

---

## 5. 推荐的渐进式工作流

### 5.1 阶段 A：确认契约与后端版本

每个 Agent 开始前必须：

1. 完整阅读对应 Prompt；
2. 阅读本手册和两份数据库规范；
3. 检查 Backend 最新 Flyway；
4. 检查 Java 枚举，不凭记忆猜整数值；
5. 确认 JSON 的 `schema`、`schemaVersion`、`promptVersion`。

Prompt 负责定义内容，Java 枚举和 Flyway 负责定义数据库可接受范围。三者冲突时停止导入并报告，不能静默映射。

### 5.2 阶段 B：生成独立 JSON

API、人工或 Agent 只生成 JSON，不直接生成包含具体实体的 Python：

```text
输入名单/数据库快照
        ↓
生成独立 JSON
        ↓
静态校验
        ↓
数据库解析与导入
```

建议每批 20—100 个实体。批次应有唯一名称，例如：

```text
entity_set_001.json
work_set_001.json
crp_set_001.json
```

小批次便于人工抽检、失败定位和精准回滚。不要在一个文件中混入上一批已经导入的数据。

### 5.3 阶段 C：只分析，不写库

```powershell
$env:MYSQL_ROOT_PASSWORD = "你的密码"

# 人物/作品
.\.venv\Scripts\python.exe -m database.dataset_pipeline analyze

# CPR
.\.venv\Scripts\python.exe -m database.crp_dataset_pipeline validate `
  --input DataSet\crp_set_001.json
```

必须检查：

- JSON 能否解析；
- 名称和原名是否符合 Prompt；
- 枚举是否存在；
- `workId`、人物逻辑 ID 和关系引用是否闭合；
- 多作者、多作品关系是否双向一致；
- 同一实体是否存在零命中或多命中；
- 分数是否越界；
- 证据索引是否越界；
- 每个非空维度是否具有证据映射。

### 5.4 阶段 D：人工质量闸门

结构正确不等于内容可靠。导入前至少抽检：

- 高知识密度实体；
- 低知识密度实体；
- 不同国家、时期和媒介；
- 多作者、多作品和跨领域实体；
- 同名作品、翻拍、改编和跨媒介作品。

重点查找：

- 杜甫诗歌被标成小说之类的媒介错误；
- 《红高粱》文学与电影被错误合并；
- 外国人物被强行填写中国朝代；
- `beginCentury` 未按出生年份定义；
- 作品简介被写成人格分析或推荐话术；
- 所有作品得到近似相同的投影；
- 通用句子被冒充作品事实；
- 一条证据机械映射全部维度。

### 5.5 阶段 E：事务导入

```powershell
# 人物/作品
.\.venv\Scripts\python.exe -m database.dataset_pipeline import

# CPR
.\.venv\Scripts\python.exe -m database.crp_dataset_pipeline import `
  --input DataSet\crp_set_001.json `
  --dataset-name crp_set_001
```

正常导入必须：

- 使用单一事务；
- 在写入前完成所有可静态发现的错误检查；
- 记录运行 ID、输入哈希、模型版本和 Prompt 版本；
- 保存受影响 ID 和导入前快照；
- 任何异常整批回滚，不保留半批数据。

### 5.6 阶段 F：数据库审计

```powershell
.\.venv\Scripts\python.exe -m database.dataset_pipeline status
.\.venv\Scripts\python.exe -m database.crp_dataset_pipeline status
```

建议额外核验这些不变量：

```text
作品总数 = 预期作品总数
current CPR 每个作品最多 1 个
score IS NULL  ⇔ evidence_state = INSUFFICIENT
每个非空 score 至少存在 1 条 dimension_evidence
dimension_value.evidence_count = 实际映射数量
每个需要投影的当前 CPR 恰好存在预期数量的 trait
raw_payload 与拆分表数值一致
外键断链数量 = 0
```

### 5.7 阶段 G：记录与继续下一批

每批完成后保存：

- 输入 JSON；
- API 原始输出；
- `analysis.json`；
- `manifest.json`；
- 审计结果；
- Prompt、模型、温度和生成日期；
- 人工修订说明。

下一 Agent 只从最近一个成功批次之后继续，不重新生成已经确认的数据。

---

## 6. CPR 数据的可靠性规则

### 6.1 证据必须是作品事实

合格证据应描述可观察内容，例如叙事结构、人物关系、歌词主题、构图、材料、空间组织或玩法机制。

以下内容不能单独支撑心理维度：

- “该作品是一部电影”；
- “作品于 1999 年发布”；
- “作者是某某”；
- “作品十分著名”；
- “作品获得很多好评”。

这些可以作为元数据，但不是心理特征证据。

### 6.2 知识不足时必须稀疏

知识不足不等于填写中间值。正确表达为：

```text
score = NULL
confidence = NULL
evidence_state = INSUFFICIENT
evidence_count = 0
```

不能使用 `0` 代表未知，因为 `0` 表示特征明确不存在。

### 6.3 低分也需要依据

维度较弱是一种判断，也需要“缺失受到证据支持”或明确的内容对照。不能因为某个关键词没有出现就直接赋低分。

### 6.4 禁止伪随机多样性

禁止使用下列方式制造作品差异：

- 作品 ID 哈希；
- 随机数；
- 按行号轮换；
- 同一媒介模板加微小抖动；
- 所有作品先设统一基础值再加关键词。

这些方法可以测试数据库范围约束，但不能用于用户环境或推荐效果验证。

### 6.5 偏好投影是派生层

`personality_affinity` 不是作品事实，应由经过审核的 CPR 派生。更新 CPR 后必须重新计算投影，并同步：

- `t_crp_projection_value`；
- `t_crp_profile.raw_payload.personality_affinity`。

只更新其中一处会形成双轨数据。

---

## 7. 实体解析与多对多关系

### 7.1 人物锁定

建议使用组合键：

```text
name + countryCode + field
```

姓名不能单独作为唯一依据。同名、多语言名、艺名和团体名都可能产生歧义。

### 7.2 作品锁定

建议使用：

```text
workName + countryCode + genre + year
```

必要时增加 `originalName` 和创作者。任何零命中或多命中都应整批停止，由人确认。

### 7.3 多作者与跨媒介

- 一部作品可有多个创作者，并带不同 `relationType`；
- 同一人物可对应多个作品；
- 同名但媒介不同的作品必须是独立实体；
- 改编关系不能替代创作者关系；
- 乐队、工作室和创作团体应按 Prompt 的团体主体规则建模，不拆成虚构的单一自然人。

---

## 8. 回滚与清理

### 8.1 优先使用批次回滚

```powershell
.\.venv\Scripts\python.exe -m database.dataset_pipeline rollback `
  --manifest DataSetRaw\import_runs\<runId>\manifest.json
```

回滚只处理该清单记录的 ID，并恢复导入前快照。生产环境应优先使用这种方式。

### 8.2 全实体清空

先预览：

```powershell
.\.venv\Scripts\python.exe -m database.clear_all_entity_data
```

明确确认后执行：

```powershell
.\.venv\Scripts\python.exe -m database.clear_all_entity_data --execute
```

该工具会清除人物、作品、关系和 CPR 业务数据，并保留维度定义及数据库结构。它是破坏性操作，不应用于普通批次纠错。

---

## 9. Flyway 与运行时导入的边界

Flyway 适合：

- 建表；
- 新增列、索引、约束；
- 稳定的枚举和 CPR 维度定义；
- 每次重建数据库都必须存在的基础数据。

运行时数据管道适合：

- 人物和作品实体；
- API 生成的 CPR；
- 经常迭代的证据和投影；
- 可按批次回滚的数据集。

不要把几千个人物或模型生成结果直接固化为 Flyway。也不要只在当前数据库手动修改维度定义而不补 Flyway，否则数据库重建后修改会丢失。

---

## 10. 已知风险与实践问题

### 10.1 字符编码

症状包括中文乱码、`name_zh` 显示英文或问号。要求：

- 文件统一 UTF-8；
- MySQL 连接使用 `utf8mb4`；
- JSON 写入使用 `ensure_ascii=False`；
- Windows 命令行可设置 `$env:PYTHONUTF8="1"`；
- 不要通过编码不明的 shell 管道导入中文 SQL。

### 10.2 数据库锁等待

长事务被终端中断后，连接可能仍处于 Sleep 并持有未提交锁。表现为：

```text
Lock wait timeout exceeded
```

处理顺序：

1. 检查 `SHOW FULL PROCESSLIST`；
2. 确认是本管道遗留连接；
3. 只终止该连接；
4. 确认事务已回滚或完整提交；
5. 再以幂等方式重跑。

不能看到锁就随意终止其他服务连接。

### 10.3 `DELETE`、外键与事务

- 普通批次按依赖顺序删除；
- `FOREIGN_KEY_CHECKS=0` 只用于经过预览的全清理；
- 无论成功失败都必须恢复外键检查；
- 清理后再次核验 `@@foreign_key_checks = 1`。

### 10.4 JSON 与拆分表漂移

同一 CPR 同时存在于 `raw_payload` 和关系表。任何修订都必须同步两者，或者从权威 JSON 完整重建该 Profile。局部手工 SQL 最容易造成漂移。

### 10.5 版本与当前档案

每个作品同一 schema 只能有一个 `is_current=1` 的有效档案。替换时应：

1. 创建新版本；
2. 完整写入维度、证据和投影；
3. 通过校验；
4. 再切换 current 状态；
5. 保留旧版本用于审计或回滚。

### 10.6 API 输出不稳定

模型可能遗漏字段、混淆媒介、输出 Markdown 包裹或中途截断。应：

- 每批控制规模；
- 使用 JSON Schema 或严格结构校验；
- 对失败项单独重试，不重新生成成功项；
- 保存原始响应；
- 使用固定 Prompt 版本和模型参数；
- 对高风险实体做人工抽检。

---

## 11. Agent 执行协议

后续 Agent 应严格遵循以下顺序：

```text
READ
读取 Prompt、规范、Java 枚举、Flyway 和上一批 manifest

DISCOVER
导出缺失实体或缺失 CPR，不自行扩大输入范围

GENERATE
只生成独立 JSON；记录模型、Prompt、批次和处理状态

VALIDATE
先做静态结构、枚举、引用和证据映射检查

REVIEW
进行跨领域、跨时期、高低知识密度抽检

IMPORT
单事务写入并生成 manifest

AUDIT
核验覆盖、唯一 current、空值状态、映射、投影和断链

REPORT
报告成功数、跳过数、失败项、数据质量限制和下一批起点
```

Agent 不得：

- 未读 Prompt 就生成；
- 联网补充事实，除非用户明确授权；
- 将数据硬编码进 Python；
- 为追求覆盖率填充未知值；
- 名称模糊匹配后直接写库；
- 在没有 manifest 或快照时覆盖已有数据；
- 将“脚本运行不报错”等同于“数据质量合格”；
- 用户要求停止生成后继续扩展。

---

## 12. 推荐的 API 批处理状态文件

建议为每次 API 任务维护一个状态文件：

```json
{
  "datasetName": "crp_deepseek_001",
  "promptVersion": "CPR-1.2",
  "model": "模型名称",
  "inputHash": "...",
  "total": 100,
  "succeeded": ["work-id-1"],
  "failed": [{"workId":"work-id-2","reason":"JSON 截断"}],
  "pending": ["work-id-3"],
  "validated": false,
  "imported": false,
  "manifest": null
}
```

这样 Agent 可以从 `pending` 或 `failed` 继续，而不是依赖聊天上下文记忆。

---

## 13. 最小验收清单

一次数据更新只有同时满足以下条件才算完成：

- [ ] 原始 JSON 已保存，Python 中没有硬编码实体内容；
- [ ] Prompt、模型、schema 和生成日期可追溯；
- [ ] 静态校验通过；
- [ ] 实体唯一解析，无零命中或多命中；
- [ ] 多作者、多作品关系完整；
- [ ] 非空 CPR 分数均有事实证据映射；
- [ ] 未知 CPR 维度为 `NULL + INSUFFICIENT`；
- [ ] current Profile 不重复；
- [ ] 投影有差异且与 CPR 同步；
- [ ] 数据库无断链；
- [ ] 生成 manifest 和审计报告；
- [ ] 已记录可执行的回滚方式；
- [ ] 人工抽检确认内容可用于用户环境。

满足“导入不报错”只能说明结构适配成功，不能替代最后一项内容质量验收。

---

## 14. DeepSeek 数据集接入与次日验收流程

### 14.1 目录规范

- `DataSet/`：只存放准备进入正式导入流程的规范 JSON；
- `DataSetRaw/deepseek_runs/`：保存 DeepSeek 原始响应的提取结果、隔离状态和校验报告；
- `DataSetRaw/import_runs/`：唯一的实体导入运行目录；
- `DataSetRaw/reports/`：枚举契约、推荐先验和其他只读分析报告；
- 旧的 `DataSet/import_runs/` 仅作为兼容读取位置，不再写入新运行。

不能直接把对话框文本复制成正式数据后立即导库。DeepSeek 即使声称输出 JSON，也可能包含 Markdown 围栏、解释文字、截断内容或枚举漂移。

### 14.2 保存 DeepSeek 原始响应

推荐让一次响应返回：

```json
{
  "characters": [],
  "works": []
}
```

将对话框原文原样保存，例如：

```text
DataSetRaw/deepseek_responses/deepseek_batch_001.txt
```

不要手工删除围栏或修改字段后覆盖原文。原始文件是追溯模型兼容性和失败原因的依据。

### 14.3 提取、清洗和隔离（只写文件，不写数据库）

合并响应：

```powershell
.\.venv\Scripts\python.exe -m database.deepseek_dataset_intake `
  --batch deepseek_batch_001 `
  --raw DataSetRaw\deepseek_responses\deepseek_batch_001.txt `
  --model deepseek-chat `
  --prompt-version entity-prompt-v1
```

人物和作品分开响应：

```powershell
.\.venv\Scripts\python.exe -m database.deepseek_dataset_intake `
  --batch deepseek_batch_001 `
  --characters-raw DataSetRaw\deepseek_responses\characters_001.txt `
  --works-raw DataSetRaw\deepseek_responses\works_001.txt `
  --model deepseek-chat `
  --prompt-version entity-prompt-v1
```

DeepSeek 生成 CPR-1.2（包括 `personality_affinity` 模型先验）时：

```powershell
.\.venv\Scripts\python.exe -m database.deepseek_dataset_intake `
  --batch deepseek_crp_001 --kind crp `
  --raw DataSetRaw\deepseek_responses\deepseek_crp_001.txt `
  --model deepseek-chat --prompt-version CPR-1.2
```

通过后得到 `DataSetRaw/deepseek_runs/deepseek_crp_001/crp.json`，再交给
`database.crp_dataset_pipeline validate/import`。实体 JSON 与 CPR JSON 不得混用入口。

输出目录：

```text
DataSetRaw/deepseek_runs/deepseek_batch_001/
├─ raw_01.txt
├─ characters.json
├─ works.json
└─ intake_report.json
```

状态含义：

- `validated`：结构、枚举和双向引用通过，可以进入人工抽检；
- `quarantined`：不得导库，查看 `issues` 或 `fatalError`；
- 进程退出码 `0` 表示可进入下一阶段，退出码 `2` 表示被隔离；
- 工具只移除 JSON 外层包装，不会猜测枚举、补造事实或自动修复实体关系。

同名 `batch` 不允许覆盖。重试 DeepSeek 后必须使用新批次名，例如 `deepseek_batch_001_retry1`，从而保留原始失败证据。

### 14.4 枚举四方契约检查（只读）

静态检查 Prompt、Python 和 Backend：

```powershell
.\.venv\Scripts\python.exe -m database.enum_contract_check
```

连同数据库当前值检查：

```powershell
$env:MYSQL_PASSWORD = "你的密码"
.\.venv\Scripts\python.exe -m database.enum_contract_check --database
```

检查范围包括：

- 36 个作品创作关系，即 `1—35` 与 `99`；
- 关系允许的 genre；
- 161 个子领域及其所属 genre；
- 数据库是否已经存在后端和管道不认识的整数值。

数据库没有独立枚举目录，因此数据库检查证明的是“现有值均合法”，不能证明数据库已经保存全部允许值。允许全集仍以 Backend 枚举、Prompt 与 Python 契约共同一致为准。

### 14.5 导入前分析与正式导入

先分析 DeepSeek 提取结果：

```powershell
.\.venv\Scripts\python.exe -m database.dataset_pipeline analyze `
  --characters DataSetRaw\deepseek_runs\deepseek_batch_001\characters.json `
  --works DataSetRaw\deepseek_runs\deepseek_batch_001\works.json `
  --dataset-name deepseek_batch_001
```

人工抽检通过后才允许导入：

```powershell
.\.venv\Scripts\python.exe -m database.dataset_pipeline import `
  --characters DataSetRaw\deepseek_runs\deepseek_batch_001\characters.json `
  --works DataSetRaw\deepseek_runs\deepseek_batch_001\works.json `
  --dataset-name deepseek_batch_001
```

新运行统一写入 `DataSetRaw/import_runs/`。成功指针与最近一次尝试指针分开：

- `<dataset>_latest.json`：最近一次成功导入；
- `<dataset>_latest_attempt.json`：最近一次尝试，可能成功也可能失败。

这样失败运行不会覆盖最后一个已知成功批次。

### 14.6 失败重试与来源链

数据库异常发生时，单事务会自动回滚，并生成 `status=failed` 的 manifest。修复数据库结构或临时环境问题后执行：

```powershell
.\.venv\Scripts\python.exe -m database.dataset_pipeline retry `
  --manifest DataSetRaw\import_runs\<failedRunId>\manifest.json
```

约束：

- 只有 `failed` 批次可以 `retry`；
- 必须显式提供 manifest，禁止模糊重试 latest；
- 原输入文件和 SHA-256 仍须存在；
- 新 manifest 记录 `retryOfRunId`，不会覆盖失败 manifest；
- `quarantined` 是导入前结构/内容失败，应重新生成或人工修订 JSON，不能使用数据库 retry；
- `rolled_back` 表示一次成功导入后来被主动撤销，也不能伪装成失败重试。

### 14.7 推荐模型先验效果检查（只读数据库）

DeepSeek 数据和 CPR 导入后执行：

```powershell
$env:MYSQL_PASSWORD = "你的密码"
.\.venv\Scripts\python.exe -m database.recommendation_prior_evaluation
```

报告位置：

```text
DataSetRaw/reports/recommendation_prior_evaluation.json
```

报告比较四个语义明确的消融版本：

1. `behaviorContentOnly`：仅“用户历史偏好八维 × 作品 CRP 八维”；
2. `behaviorContentAndAudience`：在行为层加入“用户历史偏好八维 × 作品受众八维”；
3. `behaviorAndTestContentAudience`：再加入完整测试层，但固定 MBTI 层用中性分，以单独观察八维模型先验；
4. `productionFull`：与线上推荐一致，再加入完整固定 MBTI 层。

线上总分保持三层比例：

```text
总分 = 70% × 行为层 + 20% × 人格测试层 + 10% × 固定 MBTI 层
```

行为层和人格测试层都使用同一种作品受众置信度逻辑：

```text
行为层 = (行为八维 × CRP 八维) × (1 - 0.25 × 受众置信度)
       + (行为八维 × 作品受众八维) × (0.25 × 受众置信度)

测试层 = (测试八维 × CRP 八维) × (1 - 0.25 × 受众置信度)
       + (测试八维 × 作品受众八维) × (0.25 × 受众置信度)
```

固定 MBTI 层包含两项：

```text
固定 MBTI 层 = (固定 MBTI × CRP 转译人格) × (1 - 历史受众证据置信度)
              + (固定 MBTI × 历史作品受众 MBTI) × 历史受众证据置信度

历史受众证据置信度 = score_count 总和 / (score_count 总和 + 20)
```

CRP 八维不落新表，推荐时按 `Ni→N/I/J`、`Ne→N/E/P`、`Si→S/I/J`、`Se→S/E/P`、`Ti→T/I/P`、`Te→T/E/J`、`Fi→F/I/P`、`Fe→F/E/J` 累加，然后分别在 I/E、N/S、T/F、J/P 轴内归一化。一次候选计算只有固定数量的加法，不需要缓存；只有以后候选规模或调用频率经过监控确认构成瓶颈时才增加缓存。

`GET /audience/work` 还会返回独立的 `crpMbtiPrior` 解释字段。每个字母先取得其对立轴内相对值 `p`，再计算：

```text
attitude = clamp(round((p - 0.5) × 4), -2, 2)
```

因此 `0.9→2`、`0.1→-2`、`0.49/0.51→0`，同轴两字母始终互为相反数。四字母 `derivedMbti` 使用舍入前的原始轴强弱确定，只有原始轴完全相等时该位才为 `X`。这个字段具有以下硬边界：

- `recommendationEligible=false`，不得写入或冒充 `t_work_audience_result`；
- CRP 翻译值固定作为权重为 1 的第 1 张展示票；第一名具有有效 MBTI 的真实评分者是第 2 张票；
- `mergedLetterAttitudes = (CRP基础attitude + 各真实MBTI评分字母贡献之和) / (1 + realMbtiRatingCount)`；
- 真人选择的四个 MBTI 字母加入其作品评分，四个对立字母加入评分的相反数；缺少有效 MBTI 的评分不进入字母合并分母；
- `effectiveDisplayVoteCount=1+realMbtiRatingCount`，CRP 影响会被真实评分快速稀释，但不会突然消失；
- 合并结果会接入 `workAudienceMBTILetterStatVOS[*].attitude`，并映射为 `positiveStat.percentage=(attitude+2)/4`、`negativeStat.percentage=1-positiveStat.percentage`；
- `positiveStat/negativeStat.scoreCount` 仍只记录真人证据，不把 CRP 基础票伪造成真人计数；
- 推荐算法仍可使用独立的“固定用户 MBTI × 作品 CRP”内容先验，但不得把同一 CRP 翻译值再次当作“历史受众 MBTI”重复计权。

重点查看：

- `eligibleRatings`：同时具备评分、用户八维和作品 CRP 的样本数；
- `separation`：正评分平均分与负评分平均分之差；
- `separationDeltaVsBehaviorContentOnly`：相对仅行为×CRP基线的变化；
- `pairwiseAuc`：同一用户正评分作品排在负评分作品之前的比例；
- `comparisonPairs`：AUC 的有效比较对数。

如果 `ready=false`，应先查看 coverage。没有 CRP、没有用户行为向量或样本过少时，不能宣称先验有效。该报告是离线相关性检查，并非线上因果 A/B 测试；正式效果验证还需要按时间切分训练/验证行为，避免评分信息泄漏。

### 14.8 一键只读验收

仅检查代码契约：

```powershell
.\.venv\Scripts\python.exe -m database.pipeline_acceptance
```

带 DeepSeek 批次和数据库检查：

```powershell
$env:MYSQL_PASSWORD = "你的密码"
.\.venv\Scripts\python.exe -m database.pipeline_acceptance `
  --deepseek-report DataSetRaw\deepseek_runs\deepseek_batch_001\intake_report.json `
  --manifest DataSetRaw\import_runs\deepseek_batch_001_latest.json `
  --database
```

这个命令不会写数据库。只有以下项目全部通过时，顶层 `passed` 才为 `true`：

- 实体契约测试；
- Prompt/Python/Backend/数据库枚举检查；
- 指定 DeepSeek 批次未被隔离；
- 指定导入 manifest 的全部跟踪行仍然存在，跨表检查通过；
- 推荐效果评估存在可计算样本。

明日建议按以下固定顺序操作：

```text
保存 DeepSeek 原始响应
→ deepseek_dataset_intake
→ 人工抽检 intake_report 与规范 JSON
→ enum_contract_check --database
→ dataset_pipeline analyze
→ dataset_pipeline import
→ dataset_pipeline status
→ 导入/确认 CPR personality_affinity
→ recommendation_prior_evaluation
→ pipeline_acceptance --deepseek-report ... --database
```

不要因为最后一键验收返回 `passed=false` 就自动修改数据。先根据子报告区分：模型 JSON 失败、枚举漂移、数据库导入失败、CPR 覆盖不足，还是推荐行为样本不足。
