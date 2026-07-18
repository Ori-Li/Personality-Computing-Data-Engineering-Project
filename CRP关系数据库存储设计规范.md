# CRP 关系数据库存储设计规范

## 1. 文档目的

本文档用于指导 Agent 将 CRP 数据稳定、可扩展地写入关系数据库。

本文中的 CRP 指某个文化实体的结构化心理表征档案，主要包含：

- 文化实体基础信息；
- CRP 版本信息；
- 89 维心理体验向量；
- 支撑事实与来源；
- 维度与证据之间的关联；
- Jung 八维、Big Five、神经质等人格投影；
- 文本或推荐模型生成的 Embedding；
- 数据生成批次、模型版本和 Prompt 版本。

CRP 不应只保存为一条 JSON，也不建议把 89 个维度直接设计为 89 个固定字段。

推荐采用以下混合结构：

- 关系表：保存可检索、可校验、可训练的数据；
- JSONB：保存原始生成快照和扩展字段；
- Vector：保存模型 Embedding；
- 版本表：保存不同模型和不同 Schema 生成的历史版本。

---

## 2. 核心设计原则

### 2.1 统一文化实体

人物、作品、虚拟角色、事件、地点、产品等对象统一使用 `cultural_entity` 作为父实体。

CRP 只关联 `cultural_entity.id`，避免分别创建：

- `person_crp`
- `work_crp`
- `character_crp`
- `event_crp`

### 2.2 CRP 必须版本化

同一实体可能因为以下原因生成多个 CRP：

- Prompt 修改；
- 模型升级；
- Schema 修改；
- 新增来源；
- 人工纠错；
- 重新生成；
- 人格投影模型升级。

因此新版本不能直接覆盖旧版本。

### 2.3 89 维不能设计成宽表

不要设计成：

```sql
cognitive_complexity_score
emotional_intensity_score
symbolic_density_score
...
```

应使用：

- `crp_dimension_definition`：定义维度；
- `crp_dimension_value`：保存实体在该维度上的值。

这样以后增加、删除或修改维度时，不需要修改数据库表结构。

### 2.4 缺少证据不等于低分

必须区分：

```text
score = 0
```

和：

```text
score = NULL
evidence_state = INSUFFICIENT
```

含义分别是：

- `0`：有证据支持该特征很低；
- `NULL + INSUFFICIENT`：现有材料不足，不能判断。

Agent 禁止把证据不足自动写成零分。

### 2.5 事实与推断分层

以下内容必须分开保存：

1. 可验证事实；
2. 事实对应的心理维度解释；
3. 89 维心理体验值；
4. Jung 八维、Big Five 等人格投影；
5. 模型 Embedding。

人格投影是模型推导结果，不是原始事实。

### 2.6 原始 JSON 只用于审计和恢复

LLM 输出的完整 CRP JSON 可以写入 `crp_profile.raw_payload`，但不得作为主要训练和查询入口。

主要训练数据必须拆解到标准关系表中。

---

## 3. 总体关系结构

```text
cultural_entity
    │
    ├── person
    ├── work
    ├── character
    ├── event
    ├── place
    └── product
    │
    ▼
crp_profile
    │
    ├── crp_dimension_value
    │       │
    │       └── crp_dimension_evidence
    │                   │
    │                   ▼
    ├── crp_evidence ─── source_record
    │
    ├── crp_projection_value
    │
    └── crp_embedding

crp_generation_run
    │
    └── crp_profile
```

---

## 4. 推荐表结构

## 4.1 统一文化实体表

```sql
CREATE TABLE cultural_entity (
    id              BIGSERIAL PRIMARY KEY,
    entity_type     VARCHAR(32) NOT NULL,
    canonical_name  TEXT NOT NULL,
    canonical_key   VARCHAR(255) NOT NULL UNIQUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CHECK (entity_type IN (
        'PERSON',
        'WORK',
        'CHARACTER',
        'MEDIA',
        'PLACE',
        'PRODUCT',
        'EVENT',
        'IDEA'
    ))
);
```

字段说明：

| 字段 | 含义 |
|---|---|
| `id` | 数据库内部统一实体 ID |
| `entity_type` | 人物、作品、角色、事件等实体类型 |
| `canonical_name` | 标准显示名称 |
| `canonical_key` | 用于去重的标准键 |
| `created_at` | 创建时间 |

`canonical_key` 不应只使用名称。

推荐组成：

```text
entity_type + country + domain + normalized_name + year_or_birth
```

例如：

```text
WORK|CN|NOVEL|红楼梦|1791
PERSON|CN|MUSIC|周杰伦|1979
```

---

## 4.2 人物表

```sql
CREATE TABLE person (
    entity_id       BIGINT PRIMARY KEY
                    REFERENCES cultural_entity(id),

    birth_date      DATE,
    death_date      DATE,
    country_code    VARCHAR(16),
    field_code      VARCHAR(64),
    metadata        JSONB
);
```

---

## 4.3 作品表

```sql
CREATE TABLE work (
    entity_id       BIGINT PRIMARY KEY
                    REFERENCES cultural_entity(id),

    original_name   TEXT,
    work_type       VARCHAR(64),
    release_year    INTEGER,
    country_code    VARCHAR(16),
    metadata        JSONB
);
```

---

## 4.4 人物—作品关系表

```sql
CREATE TABLE person_work (
    person_entity_id BIGINT NOT NULL
                     REFERENCES person(entity_id),

    work_entity_id   BIGINT NOT NULL
                     REFERENCES work(entity_id),

    role_code        VARCHAR(64) NOT NULL,
    role_detail      TEXT,

    PRIMARY KEY (
        person_entity_id,
        work_entity_id,
        role_code
    )
);
```

一个人物可关联多个作品，一个作品也可关联多个创作者。

`role_code` 示例：

```text
AUTHOR
DIRECTOR
COMPOSER
PERFORMER
PAINTER
DESIGNER
PRODUCER
SCREENWRITER
CREATOR
```

---

## 4.5 CRP 生成运行记录表

```sql
CREATE TABLE crp_generation_run (
    id                  BIGSERIAL PRIMARY KEY,

    pipeline_version    VARCHAR(64) NOT NULL,
    prompt_version      VARCHAR(64) NOT NULL,

    model_provider      VARCHAR(64) NOT NULL,
    model_name          VARCHAR(128) NOT NULL,
    model_version       VARCHAR(64),

    web_search_used     BOOLEAN NOT NULL DEFAULT FALSE,

    input_hash          VARCHAR(128),
    output_hash         VARCHAR(128),

    status              VARCHAR(24) NOT NULL,
    generation_metadata JSONB,

    started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at         TIMESTAMPTZ,

    CHECK (status IN (
        'CREATED',
        'RUNNING',
        'SUCCEEDED',
        'FAILED',
        'PARTIAL',
        'REJECTED'
    ))
);
```

用途：

- 追踪使用了哪个模型；
- 追踪使用了哪个 Prompt；
- 追踪是否联网；
- 追踪数据生成批次；
- 支持失败重试；
- 支持模型升级后的重新生成；
- 支持数据质量回溯。

---

## 4.6 CRP 主表

```sql
CREATE TABLE crp_profile (
    id                  BIGSERIAL PRIMARY KEY,

    entity_id           BIGINT NOT NULL
                        REFERENCES cultural_entity(id),

    schema_version      VARCHAR(32) NOT NULL,
    profile_version     INTEGER NOT NULL,

    status              VARCHAR(24) NOT NULL DEFAULT 'DRAFT',
    is_current          BOOLEAN NOT NULL DEFAULT FALSE,

    summary_text        TEXT,
    overall_confidence  NUMERIC(6,5),
    evidence_coverage   NUMERIC(6,5),

    generation_run_id   BIGINT
                        REFERENCES crp_generation_run(id),

    raw_payload         JSONB,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    validated_at        TIMESTAMPTZ,

    UNIQUE (
        entity_id,
        schema_version,
        profile_version
    ),

    CHECK (status IN (
        'DRAFT',
        'VALIDATED',
        'PUBLISHED',
        'REJECTED',
        'SUPERSEDED'
    )),

    CHECK (
        overall_confidence IS NULL
        OR overall_confidence BETWEEN 0 AND 1
    ),

    CHECK (
        evidence_coverage IS NULL
        OR evidence_coverage BETWEEN 0 AND 1
    )
);
```

确保一个实体在同一个 Schema 下只能存在一个当前版本：

```sql
CREATE UNIQUE INDEX uq_crp_current_profile
ON crp_profile(entity_id, schema_version)
WHERE is_current = TRUE;
```

字段说明：

| 字段 | 含义 |
|---|---|
| `schema_version` | 89 维体系版本 |
| `profile_version` | 当前实体的 CRP 版本号 |
| `status` | 草稿、已验证、已发布、拒绝等 |
| `is_current` | 是否为当前有效版本 |
| `summary_text` | CRP 总结 |
| `overall_confidence` | 整体置信度 |
| `evidence_coverage` | 证据覆盖率 |
| `generation_run_id` | 生成批次 |
| `raw_payload` | 原始 CRP JSON 快照 |

---

## 4.7 CRP 维度定义表

```sql
CREATE TABLE crp_dimension_definition (
    id                  SMALLSERIAL PRIMARY KEY,

    dimension_code      VARCHAR(100) NOT NULL,
    group_code          VARCHAR(64) NOT NULL,

    name_zh             VARCHAR(128) NOT NULL,
    name_en             VARCHAR(128),

    description         TEXT,

    positive_pole       TEXT,
    negative_pole       TEXT,

    schema_version      VARCHAR(32) NOT NULL,
    display_order       INTEGER NOT NULL,

    is_active           BOOLEAN NOT NULL DEFAULT TRUE,

    UNIQUE (
        dimension_code,
        schema_version
    )
);
```

示例：

```text
dimension_code: cognitive_complexity
group_code: cognition
name_zh: 认知复杂度

dimension_code: emotional_intensity
group_code: emotion
name_zh: 情绪强度

dimension_code: symbolic_density
group_code: cognition
name_zh: 象征密度
```

维度定义属于 Schema 数据，应单独维护。

---

## 4.8 CRP 维度值表

```sql
CREATE TABLE crp_dimension_value (
    profile_id          BIGINT NOT NULL
                        REFERENCES crp_profile(id)
                        ON DELETE CASCADE,

    dimension_id        SMALLINT NOT NULL
                        REFERENCES crp_dimension_definition(id),

    score               NUMERIC(6,5),
    confidence          NUMERIC(6,5),

    evidence_state      VARCHAR(32) NOT NULL,

    evidence_count      INTEGER NOT NULL DEFAULT 0,
    rationale           TEXT,

    PRIMARY KEY (
        profile_id,
        dimension_id
    ),

    CHECK (
        score IS NULL
        OR score BETWEEN 0 AND 1
    ),

    CHECK (
        confidence IS NULL
        OR confidence BETWEEN 0 AND 1
    ),

    CHECK (evidence_state IN (
        'PRESENT',
        'ABSENT_SUPPORTED',
        'CONTRADICTORY',
        'INSUFFICIENT'
    )),

    CHECK (
        (
            evidence_state = 'INSUFFICIENT'
            AND score IS NULL
        )
        OR
        (
            evidence_state <> 'INSUFFICIENT'
            AND score IS NOT NULL
        )
    )
);
```

### evidence_state 规则

| 状态 | 含义 | score |
|---|---|---|
| `PRESENT` | 有事实支持该特征存在 | 必须有值 |
| `ABSENT_SUPPORTED` | 有事实支持该特征有限或缺失 | 必须有值 |
| `CONTRADICTORY` | 不同证据相互冲突 | 可以有综合值 |
| `INSUFFICIENT` | 证据不足，无法判断 | 必须为 `NULL` |

Agent 写入时必须遵守：

```text
证据不足 ≠ 低分
没有发现 ≠ 已证明不存在
NULL ≠ 0
```

---

## 4.9 来源表

```sql
CREATE TABLE source_record (
    id              BIGSERIAL PRIMARY KEY,

    source_type     VARCHAR(32) NOT NULL,
    source_url      TEXT,
    source_title    TEXT,
    publisher       TEXT,
    author_name     TEXT,

    published_at    TIMESTAMPTZ,
    accessed_at     TIMESTAMPTZ,

    content_hash    VARCHAR(128),
    source_metadata JSONB,

    CHECK (source_type IN (
        'OFFICIAL',
        'BOOK',
        'PAPER',
        'INTERVIEW',
        'NEWS',
        'DATABASE',
        'ENCYCLOPEDIA',
        'ARCHIVE',
        'USER_INPUT',
        'MODEL_INPUT',
        'OTHER'
    ))
);
```

`source_locator` 不放在此表，因为同一个来源可能被多个证据引用到不同位置。

---

## 4.10 CRP 证据表

```sql
CREATE TABLE crp_evidence (
    id                  BIGSERIAL PRIMARY KEY,

    profile_id          BIGINT NOT NULL
                        REFERENCES crp_profile(id)
                        ON DELETE CASCADE,

    source_id           BIGINT
                        REFERENCES source_record(id),

    evidence_type       VARCHAR(48) NOT NULL,

    fact_text           TEXT NOT NULL,
    source_locator      TEXT,

    verification_status VARCHAR(24) NOT NULL DEFAULT 'UNVERIFIED',
    quality_score       NUMERIC(6,5),

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CHECK (evidence_type IN (
        'CONTENT_FACT',
        'STRUCTURAL_FEATURE',
        'CREATOR_STATEMENT',
        'BEHAVIOR_FACT',
        'RECEPTION_FACT',
        'HISTORICAL_CONTEXT',
        'ABSENCE_EVIDENCE',
        'CONTRADICTORY_EVIDENCE'
    )),

    CHECK (verification_status IN (
        'UNVERIFIED',
        'AUTO_VERIFIED',
        'HUMAN_VERIFIED',
        'CONTRADICTED',
        'REJECTED'
    )),

    CHECK (
        quality_score IS NULL
        OR quality_score BETWEEN 0 AND 1
    )
);
```

### fact_text 数据干净性要求

`fact_text` 只保存干净、独立、可复用的事实描述。

正确：

```text
作品采用多线叙事，并通过多个家族成员的视角展示社会关系。
```

错误：

```text
作品采用多线叙事[1][Reference 3][turn0search2]。
```

禁止写入以下污染内容：

- `[1]`
- `[Reference 3]`
- `turn0search2`
- `source_001`
- Markdown 脚注编码；
- 网页抓取内部编号；
- LLM 工具引用编号；
- 多余 URL；
- Prompt 指令；
- 推理过程；
- “根据搜索结果”等模型措辞。

引用信息必须拆分到：

- `source_id`
- `source_locator`
- `source_url`

---

## 4.11 维度—证据关联表

```sql
CREATE TABLE crp_dimension_evidence (
    profile_id          BIGINT NOT NULL,
    dimension_id        SMALLINT NOT NULL,

    evidence_id         BIGINT NOT NULL
                        REFERENCES crp_evidence(id)
                        ON DELETE CASCADE,

    relation_type       VARCHAR(24) NOT NULL,
    evidence_weight     NUMERIC(6,5),
    explanation         TEXT,

    PRIMARY KEY (
        profile_id,
        dimension_id,
        evidence_id
    ),

    FOREIGN KEY (
        profile_id,
        dimension_id
    )
    REFERENCES crp_dimension_value(
        profile_id,
        dimension_id
    )
    ON DELETE CASCADE,

    CHECK (relation_type IN (
        'SUPPORTS',
        'LIMITS',
        'CONTRADICTS',
        'SUPPORTS_ABSENCE'
    )),

    CHECK (
        evidence_weight IS NULL
        OR evidence_weight BETWEEN 0 AND 1
    )
);
```

一个事实可以支持多个维度，一个维度也可以由多条事实支持。

示例：

```text
事实：
《红楼梦》采用大量人物关系、诗词隐喻和多层社会结构。

维度关联：
cognitive_complexity      SUPPORTS
symbolic_density          SUPPORTS
social_system_complexity  SUPPORTS
```

不要为了三个维度复制三次相同的事实文本。

---

## 4.12 人格投影表

```sql
CREATE TABLE crp_projection_value (
    profile_id          BIGINT NOT NULL
                        REFERENCES crp_profile(id)
                        ON DELETE CASCADE,

    projection_system   VARCHAR(32) NOT NULL,
    trait_code          VARCHAR(64) NOT NULL,

    score               NUMERIC(6,5) NOT NULL,
    confidence          NUMERIC(6,5),

    projection_model    VARCHAR(128) NOT NULL,
    model_version       VARCHAR(64) NOT NULL,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (
        profile_id,
        projection_system,
        trait_code,
        projection_model,
        model_version
    ),

    CHECK (projection_system IN (
        'JUNG_8',
        'BIG_FIVE',
        'NEUROTICISM',
        'MBTI_AFFINITY'
    )),

    CHECK (score BETWEEN 0 AND 1),

    CHECK (
        confidence IS NULL
        OR confidence BETWEEN 0 AND 1
    )
);
```

数据示例：

```text
profile_id | projection_system | trait_code | score
----------------------------------------------------
10001      | JUNG_8            | NI         | 0.87
10001      | JUNG_8            | NE         | 0.71
10001      | BIG_FIVE          | OPENNESS   | 0.92
10001      | NEUROTICISM       | STABILITY  | 0.64
```

人格投影层必须与 89 维原始心理体验层分开。

原因：

- 89 维是解释性特征；
- Jung 八维和 Big Five 是模型投影；
- 投影模型可以独立升级；
- 同一 CRP 可以由多个投影模型计算；
- 不同投影模型可能输出不同结果。

---

## 4.13 Embedding 表

使用 PostgreSQL 时可以启用 `pgvector`：

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

示例：

```sql
CREATE TABLE crp_embedding (
    profile_id          BIGINT NOT NULL
                        REFERENCES crp_profile(id)
                        ON DELETE CASCADE,

    embedding_type      VARCHAR(64) NOT NULL,
    model_name          VARCHAR(128) NOT NULL,
    model_version       VARCHAR(64) NOT NULL,

    embedding           VECTOR(768),

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (
        profile_id,
        embedding_type,
        model_name,
        model_version
    )
);
```

`embedding_type` 示例：

```text
WORK_SEMANTIC_EMBEDDING
PSYCHOLOGICAL_TEXT_EMBEDDING
RECOMMENDATION_EMBEDDING
ENTITY_PROFILE_EMBEDDING
```

注意：

- 89 维心理向量不是 Embedding；
- Jung 八维不是 Embedding；
- Big Five 不是 Embedding；
- Encoder 输出的高维稠密向量才是 Embedding。

如果不同模型输出维度不同，不建议在一个固定 `VECTOR(768)` 字段中混用。

可选方案：

1. 统一所有生产模型输出维度；
2. 按 Embedding 维度拆表；
3. 每种主模型单独建表；
4. 原始向量存对象存储，数据库只存索引和元数据。

---

## 5. CRP 写入流程

Agent 必须按以下顺序执行。

### 阶段一：实体处理

```text
1. 接收人物、作品、角色或其他文化实体 JSON。
2. 生成 canonical_key。
3. 查询 cultural_entity 是否已存在。
4. 如果不存在，插入 cultural_entity。
5. 根据实体类型写入 person、work、character 等子表。
6. 处理人物和作品之间的 person_work 关系。
```

### 阶段二：创建生成批次

```text
7. 创建 crp_generation_run。
8. 保存 pipeline_version。
9. 保存 prompt_version。
10. 保存模型提供商、模型名和模型版本。
11. 保存是否使用网络搜索。
12. 保存输入 Hash。
```

### 阶段三：创建 CRP 草稿

```text
13. 获取实体当前最大的 profile_version。
14. 新版本号 = 最大版本号 + 1。
15. 创建 crp_profile。
16. status 设置为 DRAFT。
17. is_current 设置为 FALSE。
18. 将原始生成 JSON 写入 raw_payload。
```

### 阶段四：写入心理维度

```text
19. 校验 dimension_code 是否存在于当前 schema_version。
20. 将 89 维数据写入 crp_dimension_value。
21. 证据不足时 score 必须为 NULL。
22. evidence_state 必须与 score 状态一致。
23. 禁止缺失维度被自动补零。
```

### 阶段五：写入来源和事实

```text
24. 对来源 URL 或内容 Hash 去重。
25. 写入或复用 source_record。
26. 清洗 supporting_facts。
27. 删除 reference 编码和工具编号。
28. 将干净事实写入 crp_evidence。
29. 保存 source_locator。
30. 保存 verification_status。
```

### 阶段六：建立证据关系

```text
31. 建立 crp_dimension_evidence。
32. 指明事实是支持、限制、冲突还是支持缺失。
33. 可以写入 evidence_weight。
34. 可以写入该事实如何支持该维度的简短说明。
```

### 阶段七：写入人格投影

```text
35. 运行 Jung 八维映射模型。
36. 写入 crp_projection_value。
37. 运行 Big Five 映射模型。
38. 写入 crp_projection_value。
39. 保存 projection_model 和 model_version。
```

### 阶段八：写入 Embedding

```text
40. 使用 Encoder 生成 Embedding。
41. 写入 crp_embedding。
42. 保存 embedding_type、model_name 和 model_version。
```

### 阶段九：校验与发布

```text
43. 检查必需字段。
44. 检查维度数量。
45. 检查 evidence_state 与 score 的一致性。
46. 检查所有证据是否没有 reference 编码污染。
47. 检查来源是否存在。
48. 计算 evidence_coverage。
49. 计算 overall_confidence。
50. 将 CRP 状态改为 VALIDATED。
51. 将旧版本 is_current 改为 FALSE。
52. 将新版本 is_current 改为 TRUE。
53. 将旧版本状态改为 SUPERSEDED。
54. 完成后删除临时 JSON 文件。
```

---

## 6. 推荐事务边界

一个 CRP Profile 的完整写入应放在同一个数据库事务中。

伪代码：

```text
BEGIN TRANSACTION

create generation_run
create crp_profile
insert dimension_values
insert sources
insert evidences
insert dimension_evidence_relations
insert projection_values
insert embeddings
validate profile
switch current version

COMMIT
```

任意关键步骤失败时：

```text
ROLLBACK
```

不要出现以下半成品状态：

- Profile 已存在但没有维度；
- 维度已存在但没有证据；
- 新版本设为 current，但写入尚未完成；
- 旧版本已失效，但新版本验证失败。

---

## 7. 版本切换规则

创建新版本时不要立即设置为 `is_current = TRUE`。

正确流程：

```text
DRAFT
→ 写入全部数据
→ 校验
→ VALIDATED
→ 事务内切换 current
```

事务内执行：

```sql
UPDATE crp_profile
SET
    is_current = FALSE,
    status = CASE
        WHEN status IN ('VALIDATED', 'PUBLISHED')
        THEN 'SUPERSEDED'
        ELSE status
    END
WHERE entity_id = :entityId
  AND schema_version = :schemaVersion
  AND is_current = TRUE;

UPDATE crp_profile
SET
    is_current = TRUE,
    status = 'VALIDATED',
    validated_at = NOW()
WHERE id = :newProfileId;
```

---

## 8. 数据完整性规则

## 8.1 必须满足

每个已验证 CRP 至少应包含：

- 一个合法实体；
- 一个 `crp_profile`；
- 当前 Schema 对应的维度记录；
- 支撑关键维度的事实；
- 来源信息或明确的输入来源；
- 一个生成批次；
- 完整版本号；
- 整体置信度；
- 证据覆盖率。

## 8.2 证据数量不是唯一质量指标

不能仅依赖：

```text
evidence_count
```

还应考虑：

- 来源可靠性；
- 来源是否独立；
- 是否为一手来源；
- 事实是否直接支持维度；
- 是否存在相互冲突；
- 是否只是同一来源的重复转述；
- 是否存在模型臆测。

## 8.3 禁止从总结反推事实

`summary_text` 是结果层。

Agent 不得把总结中的推断句重新拆成“事实”写入 `crp_evidence`。

事实应来自：

- 来源原文；
- 已验证结构信息；
- 作品直接内容；
- 创作者公开陈述；
- 用户明确输入；
- 可追踪的数据库记录。

## 8.4 支持缺失需要正向证据

`ABSENT_SUPPORTED` 不表示没有找到材料，而表示存在材料支持该特征有限、弱化或缺失。

例如：

```text
作品刻意采用克制、重复和单一视角，避免复杂叙事分支。
```

可以支持：

```text
narrative_complexity
relation_type = SUPPORTS_ABSENCE
```

但以下情况不能使用 `ABSENT_SUPPORTED`：

```text
没有搜索到复杂叙事相关描述。
```

这种情况只能写：

```text
evidence_state = INSUFFICIENT
score = NULL
```

---

## 9. 数据清洗规则

Agent 在写入 `crp_evidence.fact_text` 前必须执行以下清洗。

### 删除内容

```text
[1]
[2]
[Reference 1]
[ref_003]
turn0search1
turn1view2
source_001
网页内部锚点
Prompt 指令
模型解释前缀
“根据上述搜索结果”
“作为AI模型”
```

### 保留内容

```text
作品结构事实
创作者陈述
历史背景事实
行为事实
可验证受众反应
可验证形式特征
与维度相关的直接事实
```

### 不允许混入

```text
URL
Markdown citation
HTML citation
搜索结果编号
模型内部工具编号
无来源的心理诊断
无证据的人格定性
```

---

## 10. 查询示例

## 10.1 查询实体当前 CRP

```sql
SELECT cp.*
FROM crp_profile cp
WHERE cp.entity_id = :entityId
  AND cp.schema_version = :schemaVersion
  AND cp.is_current = TRUE;
```

## 10.2 查询一个实体的 89 维

```sql
SELECT
    d.dimension_code,
    d.name_zh,
    v.score,
    v.confidence,
    v.evidence_state,
    v.rationale
FROM crp_dimension_value v
JOIN crp_dimension_definition d
  ON d.id = v.dimension_id
WHERE v.profile_id = :profileId
ORDER BY d.display_order;
```

## 10.3 查询某一维度的全部证据

```sql
SELECT
    e.fact_text,
    e.evidence_type,
    e.verification_status,
    r.relation_type,
    r.evidence_weight,
    s.source_title,
    s.source_url,
    e.source_locator
FROM crp_dimension_evidence r
JOIN crp_evidence e
  ON e.id = r.evidence_id
LEFT JOIN source_record s
  ON s.id = e.source_id
WHERE r.profile_id = :profileId
  AND r.dimension_id = :dimensionId;
```

## 10.4 查询 Jung 八维

```sql
SELECT
    trait_code,
    score,
    confidence,
    projection_model,
    model_version
FROM crp_projection_value
WHERE profile_id = :profileId
  AND projection_system = 'JUNG_8';
```

## 10.5 查询证据不足的维度

```sql
SELECT
    d.dimension_code,
    d.name_zh
FROM crp_dimension_value v
JOIN crp_dimension_definition d
  ON d.id = v.dimension_id
WHERE v.profile_id = :profileId
  AND v.evidence_state = 'INSUFFICIENT';
```

## 10.6 查询低分但有证据支持的维度

```sql
SELECT
    d.dimension_code,
    d.name_zh,
    v.score,
    v.evidence_state
FROM crp_dimension_value v
JOIN crp_dimension_definition d
  ON d.id = v.dimension_id
WHERE v.profile_id = :profileId
  AND v.score <= 0.2
  AND v.evidence_state IN (
      'PRESENT',
      'ABSENT_SUPPORTED',
      'CONTRADICTORY'
  );
```

---

## 11. Java / Spring Boot 建议分层

推荐实体和服务划分：

```text
entity/
    CulturalEntity.java
    Person.java
    Work.java
    PersonWork.java

    CrpProfile.java
    CrpDimensionDefinition.java
    CrpDimensionValue.java
    CrpEvidence.java
    CrpDimensionEvidence.java
    CrpProjectionValue.java
    CrpEmbedding.java
    CrpGenerationRun.java
    SourceRecord.java

repository/
    CulturalEntityRepository.java
    CrpProfileRepository.java
    CrpDimensionValueRepository.java
    CrpEvidenceRepository.java
    CrpProjectionValueRepository.java
    CrpEmbeddingRepository.java

service/
    EntityResolutionService.java
    CrpImportService.java
    CrpValidationService.java
    CrpVersionService.java
    CrpEvidenceService.java
    CrpProjectionService.java
    CrpEmbeddingService.java

dto/
    CrpImportRequest.java
    CrpDimensionDto.java
    CrpEvidenceDto.java
    CrpProjectionDto.java
    CrpValidationResult.java
```

推荐主服务：

```text
CrpImportService.importCrp(...)
```

内部流程：

```text
resolveEntity
createGenerationRun
createDraftProfile
saveDimensions
saveSourcesAndEvidence
saveDimensionEvidenceRelations
saveProjections
saveEmbeddings
validateProfile
activateProfileVersion
```

---

## 12. Agent 实现要求

Agent 在生成代码或执行数据导入时必须遵守以下要求。

### 12.1 不得覆盖旧 CRP

任何重新生成都创建新版本。

### 12.2 不得把未知写成 0

证据不足必须：

```text
score = null
evidence_state = INSUFFICIENT
```

### 12.3 不得把人格推断当事实

Jung 八维、Big Five、MBTI 亲和度只能进入：

```text
crp_projection_value
```

不得进入：

```text
crp_evidence
```

### 12.4 不得把 Embedding 当 89 维

高维 Embedding 与解释性心理向量必须分开。

### 12.5 不得只保存 summary 引用

每条关键心理维度必须能够追踪到独立事实。

### 12.6 不得保留工具引用编码

任何类似以下内容都必须从事实文本中删除：

```text
turn0search1
[Reference 2]
[source_4]
```

### 12.7 所有生产数据必须可回溯

必须能够从任意生产 CRP 查询到：

- 对应实体；
- Schema 版本；
- Profile 版本；
- 生成模型；
- Prompt 版本；
- 生成时间；
- 支撑事实；
- 来源；
- 维度；
- 人格投影；
- Embedding 模型。

---

## 13. 最小可行版本

如果当前阶段不希望一次实现全部结构，最少先实现以下八张表：

```text
cultural_entity
crp_profile
crp_dimension_definition
crp_dimension_value
crp_evidence
crp_dimension_evidence
source_record
crp_generation_run
```

第二阶段再增加：

```text
crp_projection_value
crp_embedding
```

但即使在最小版本中，也必须保留：

- 版本化；
- 维度定义表；
- 证据表；
- 维度—证据关系；
- `NULL + INSUFFICIENT` 规则；
- 原始 JSON 快照；
- 生成批次记录。

---

## 14. 最终结论

CRP 应被视为一个有版本、可追溯、可验证的心理表征档案，而不是一条静态 JSON。

核心结构为：

```text
实体层
→ CRP 版本层
→ 89 维心理体验层
→ 事实证据层
→ 人格投影层
→ Embedding 层
```

必须坚持以下边界：

```text
事实 ≠ 心理解释
心理解释 ≠ 人格投影
人格投影 ≠ Embedding
缺少证据 ≠ 低分
原始 JSON ≠ 生产训练表
```

推荐的完整核心表：

```text
cultural_entity
person
work
person_work
crp_generation_run
crp_profile
crp_dimension_definition
crp_dimension_value
source_record
crp_evidence
crp_dimension_evidence
crp_projection_value
crp_embedding
```

该结构能够支持：

- 数十万文化实体；
- 多版本 CRP；
- 89 维心理体验向量；
- 可追溯证据链；
- 多人格模型投影；
- 推荐系统；
- 模型训练；
- 数据重生成；
- 人工校验；
- Schema 演进；
- 数据质量审计。
