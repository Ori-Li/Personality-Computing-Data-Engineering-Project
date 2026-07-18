# RGMJ 数据库导入起步工具

此目录提供 Data Engineering 项目到后端 `rgmj` MySQL 数据库的最小导入链路。

## 安装

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 写入样板数据

密码只通过环境变量传入，不写入代码或配置文件：

```powershell
$env:MYSQL_ROOT_PASSWORD = "你的密码"
.\.venv\Scripts\python.exe -m database.import_sample_data
```

脚本会自动检测数据库当前是否已经应用新迁移：旧结构使用
`t_character_work.auth_character_id`，新结构额外写入
`t_work_creator_relation` 及新增字段。

重复运行是安全的：脚本会先清理同名样板实体，再在单一事务中重新写入。

## 清理样板数据

```powershell
$env:MYSQL_ROOT_PASSWORD = "你的密码"
.\.venv\Scripts\python.exe -m database.import_sample_data --cleanup
```

## 导入 Prompt JSON 数据集

默认读取 `DataSet/evaluation_character_300.json` 和
`DataSet/evaluation_work_300.json`：

```powershell
$env:MYSQL_ROOT_PASSWORD = "你的密码"
.\.venv\Scripts\python.exe -m database.import_prompt_dataset --validate-only
.\.venv\Scripts\python.exe -m database.import_prompt_dataset
```

导入器只包含 `CharacterConstants`/`BaseConstants` 枚举映射，不包含任何
具体人物或作品数据。它使用确定性 BIGINT ID 建立以下关系：

`t_character_info` → `t_character_name` / `t_real_character_attribute`

`t_character_work` → `t_work_creator_relation` → `t_character_info`

`t_character_work` → `t_work_subcategory_relation`（主分类 + 多标签子领域）

当前输入契约为 `rgmj-entity-dataset/v1`：人物和作品文件的顶层都必须是
JSON 数组，字段及整数枚举与 `Prompt/character_generating_prompt.txt`、
`Prompt/work_generating_prompt.txt` 一致。导入器会一次性收集结构、枚举、
引用和人物—作品双向关系错误；`analyze` 即使发现错误也会正常生成报告。

数据库必须已应用 Backend 的最新人物/作品迁移，并包含
`creative_entity_type`、`original_name`、`subcategory`、
`t_work_creator_relation` 和 `t_work_subcategory_relation`。缺少这些字段时
导入会立即停止，不再静默降级。

## 流程化分析、导入、核验和回滚

```powershell
$env:MYSQL_ROOT_PASSWORD = "你的密码"

# 生成结构与质量报告，不连接数据库
.\.venv\Scripts\python.exe -m database.dataset_pipeline analyze

# 导入前自动保存受影响行快照，并生成运行清单
.\.venv\Scripts\python.exe -m database.dataset_pipeline import

# 核对五张表的行数和断链关系
.\.venv\Scripts\python.exe -m database.dataset_pipeline status

# 按依赖逆序删除本次写入，并恢复导入前快照
.\.venv\Scripts\python.exe -m database.dataset_pipeline rollback
```

每次导入的 `analysis.json` 和 `manifest.json` 位于
`DataSet/import_runs/<runId>/`。回滚只处理清单中记录的确定性 ID。

## 已有实体的协作者与作品增量扩充

`entity_enrichment_pipeline` 面向数据库中已经存在的人物和作品，不要求重新提交封闭的全量
人物/作品 JSON，也不会删除作品原有的创作者关系。知识内容保存在独立补丁 JSON 中，Python
只负责匹配、校验、写入和生成运行清单。

```powershell
$env:MYSQL_ROOT_PASSWORD = "你的密码"

# 导出人物名称、国籍、领域、作品体裁、年份及现有关系，供离线或 API 生成补丁
.\.venv\Scripts\python.exe -m database.entity_enrichment_pipeline export `
  --output DataSet\entity_snapshot.json

# 只校验实体是否唯一命中、逻辑 ID、关系枚举和关系—体裁兼容性
.\.venv\Scripts\python.exe -m database.entity_enrichment_pipeline validate `
  --patch DataSet\enrichment_set1.json

# 事务化增量写入；只追加补丁声明的实体、作品、标签与关系
.\.venv\Scripts\python.exe -m database.entity_enrichment_pipeline apply `
  --patch DataSet\enrichment_set1.json --dataset-name entity_enrichment_set1
```

补丁契约为 `rgmj-entity-enrichment/v1`，包含 `newCharacters`、`newWorks` 和
`relations`。既有人物使用 `name + countryCode + field` 唯一锁定，既有作品使用
`workName + countryCode + genre + year` 唯一锁定；新增实体通过 `newId` 引用。匹配为零或
命中多条时整批停止，禁止靠名称猜测。运行清单统一保存在 `DataSetRaw/import_runs/`，可交给原有
`database.dataset_pipeline rollback --manifest <manifest.json>` 回滚。

## DeepSeek 数据与一键验收

DeepSeek 对话框原始文本先经过只读文件入口，不得直接导库：

```powershell
.\.venv\Scripts\python.exe -m database.deepseek_dataset_intake `
  --batch deepseek_batch_001 --raw DataSetRaw\deepseek_responses\deepseek_batch_001.txt `
  --model deepseek-chat --prompt-version entity-prompt-v1

.\.venv\Scripts\python.exe -m database.enum_contract_check --database

.\.venv\Scripts\python.exe -m database.pipeline_acceptance `
  --deepseek-report DataSetRaw\deepseek_runs\deepseek_batch_001\intake_report.json `
  --manifest DataSetRaw\import_runs\deepseek_batch_001_latest.json --database
```

失败数据库批次使用显式来源链重试：

```powershell
.\.venv\Scripts\python.exe -m database.dataset_pipeline retry `
  --manifest DataSetRaw\import_runs\<failedRunId>\manifest.json
```

完整状态、退出码、推荐先验消融指标和明日验收顺序见
`数据管道渐进式更新与Agent复用手册.md` 第 14 节。

## 缺失作品 CPR 数据集

CPR 使用 `Prompt/work_psychInfo_generating_prompt.txt` 的 `CPR-1.2` 契约。只处理数据库中
尚无 `cpr-1.2` 当前有效档案的作品：

```powershell
$env:MYSQL_ROOT_PASSWORD = "你的密码"

# 反连接导出缺失 CPR 的作品
.\.venv\Scripts\python.exe -m database.crp_dataset_pipeline export-missing `
  --output DataSet\crp_missing_work_input.json

# 生成独立 JSON；实际生产环境可将这一步替换为 API 调用
.\.venv\Scripts\python.exe -m database.generate_missing_crp `
  --input DataSet\crp_missing_work_input.json `
  --output DataSet\crp_missing_work_dataset.json

# 完整结构与分数范围校验
.\.venv\Scripts\python.exe -m database.crp_dataset_pipeline validate `
  --input DataSet\crp_missing_work_dataset.json

# 事务化写入生成批次、档案、维度、证据、证据关联和人格亲和投影
.\.venv\Scripts\python.exe -m database.crp_dataset_pipeline import `
  --input DataSet\crp_missing_work_dataset.json --dataset-name crp_missing_works

# 检查覆盖率与断链
.\.venv\Scripts\python.exe -m database.crp_dataset_pipeline status
```

导入器会拒绝含有“已经具备当前 CPR”的作品文件，因此重复运行前必须重新执行
`export-missing`。完整 Prompt JSON 存入 `t_crp_profile.raw_payload`；数值维度、事实证据与
人格亲和投影同时拆分到对应关系表，便于查询和训练。

### 作品八维偏好投影清洗

八维亲和度使用独立 JSON 和独立管道，不再在 CPR 导入时依靠简单均值作为最终推荐值：

```powershell
$env:MYSQL_ROOT_PASSWORD = "你的密码"

.\.venv\Scripts\python.exe -m database.affinity_projection_pipeline generate `
  --output DataSet\work_personality_affinity_v2.json

.\.venv\Scripts\python.exe -m database.affinity_projection_pipeline validate `
  --input DataSet\work_personality_affinity_v2.json

.\.venv\Scripts\python.exe -m database.affinity_projection_pipeline import `
  --input DataSet\work_personality_affinity_v2.json
```

生成步骤结合当前 CPR、作品摘要、内容证据、媒介特征与八维语义锚点，并在全作品集合中
进行跨作品校准。导入只替换当前 Profile 的 `JUNG_8` 与 `MBTI_AFFINITY` 投影行，不修改
CPR Profile、维度值或证据。
