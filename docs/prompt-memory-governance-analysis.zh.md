# 策略层全景：mem0 预置提示词（Prompt）如何驱动“提取-治理-写入”

> 你问的是：**当前项目预置了哪些提示词来做“提取”**，以及每个提示词的**业务场景、编写规范、要求与优点**。
> 下面基于仓库代码做完整归纳，并把可执行建议（怎么写更稳）一并给出。

---

## 1. 先给结论：mem0 的提取类 Prompt 分三层

在当前代码里，和“提取/治理”最相关的预置提示词可以分为三层：

1. **语义记忆事实提取层（conversation → facts）**
   - `FACT_RETRIEVAL_PROMPT`（legacy）
   - `USER_MEMORY_EXTRACTION_PROMPT`（主路径）
   - `AGENT_MEMORY_EXTRACTION_PROMPT`（agent 画像路径）
2. **语义记忆治理决策层（facts + old memory → ADD/UPDATE/DELETE/NONE）**
   - `DEFAULT_UPDATE_MEMORY_PROMPT`
   - `get_update_memory_messages(...)`（将上下文拼装成最终治理输入）
3. **图记忆提取/治理层（text → triples / update / delete）**
   - `EXTRACT_RELATIONS_PROMPT`
   - `UPDATE_GRAPH_PROMPT`
   - `DELETE_RELATIONS_SYSTEM_PROMPT`

另外还有一个 `PROCEDURAL_MEMORY_SYSTEM_PROMPT`，属于“过程记忆总结”而非事实抽取，但同样是关键策略提示词。

---

## 2. 语义记忆提取类 Prompt（最核心）

### 2.1 `USER_MEMORY_EXTRACTION_PROMPT`

**业务场景**
- 用户消息中的长期有用信息抽取：偏好、身份信息、计划、健康、职业等。
- 用于“我该记住用户什么”这一层。

**编写规范/要求（项目中已明确写入 Prompt）**
- 只允许基于 **user** 消息生成 facts。
- 明确禁止使用 assistant/system。
- 强制输出 JSON：`{"facts": ["..."]}`。
- 给了 few-shot 样例，降低模型发散。
- 要求输出语言与用户输入语言一致。

**优点**
- 角色边界清晰，能明显降低“把助手的话误记成用户事实”的污染。
- 输出协议稳定（固定 key），方便后续程序化处理。
- few-shot 覆盖典型记忆槽位，抽取召回率较好。

---

### 2.2 `AGENT_MEMORY_EXTRACTION_PROMPT`

**业务场景**
- 给“助手本身”建画像：能力、偏好、风格、特征。
- 常见于多 agent 系统的人设一致性管理。

**编写规范/要求**
- 只允许基于 **assistant** 消息。
- 禁止使用 user/system。
- 同样强制 `{"facts": [...]}` 输出。
- few-shot 示例引导“只提取助手自述/表露信息”。

**优点**
- 与用户抽取形成对称设计，治理语义一致。
- 可把 assistant persona 和 user profile 分离存储，减少混淆。

---

### 2.3 `FACT_RETRIEVAL_PROMPT`（legacy 兼容）

**业务场景**
- 旧版兼容入口（`get_fact_retrieval_messages_legacy`）。

**规范特点**
- 也要求 JSON 输出与 few-shot。
- 角色约束粒度不如 USER/AGENT 双 prompt 细。

**优点**
- 向后兼容，便于平滑迁移。

---

## 3. 语义记忆治理 Prompt（ADD/UPDATE/DELETE/NONE）

### 3.1 `DEFAULT_UPDATE_MEMORY_PROMPT`

**业务场景**
- 当 facts 已提取完，系统需要判定每条事实对当前记忆是新增、更新、删除还是不变。

**编写规范/要求**
- 明确事件集合：`ADD | UPDATE | DELETE | NONE`。
- 给出每种事件的判定规则和示例。
- 要求 UPDATE 保持原 ID，不随意新建。
- 输出 JSON 结构固定（含 `id/text/event/old_memory` 约束）。

**优点**
- 把“记忆写入策略”显式化，便于评审与调优。
- 通过结构化事件将生成式输出转成可执行指令，降低不可控性。

---

### 3.2 `get_update_memory_messages(...)`（策略编排器）

**业务场景**
- 不是固定 prompt 常量，而是运行时拼装器：把“旧记忆 + 新 facts + 输出契约”注入二轮决策提示词。

**规范/要求**
- 若当前记忆为空，明确告知 `Current memory is empty.`。
- 强约束只返回 JSON，不要额外文本。
- 支持 `custom_update_memory_prompt` 覆盖默认策略。

**优点**
- 把静态策略和动态上下文组合，形成“状态感知”的治理决策。
- 允许业务定制策略但保持输出协议一致。

---

## 4. 图记忆提取/治理 Prompt（Graph Memory）

### 4.1 `EXTRACT_RELATIONS_PROMPT`

**业务场景**
- 从文本提取实体关系（知识图谱三元组）。

**规范/要求**
- 只提取文本中显式信息。
- 自指（I/me/my）统一映射为 `USER_ID`。
- 关系类型要一致、通用、可复用。
- 留有 `CUSTOM_PROMPT` 插槽可业务扩展。

**优点**
- 三元组抽取规则明确，图谱结构更干净。
- 统一关系命名可减少后续图查询碎片化。

---

### 4.2 `UPDATE_GRAPH_PROMPT`

**业务场景**
- 新图信息进入后，对现有边关系做修正/精炼/去重。

**规范/要求**
- 以 source/target 为主键做匹配。
- 处理冲突时考虑新信息准确性与时效性。
- 输出仅包含需要更新的关系指令。

**优点**
- 把“图关系演进”从硬编码规则迁移到可解释策略层。

---

### 4.3 `DELETE_RELATIONS_SYSTEM_PROMPT`

**业务场景**
- 删除过期/冲突的图关系。

**规范/要求**
- 只能在“矛盾或过时”时删。
- 特别强调：同关系类型但不同 destination 的并存场景**不要误删**。
- 输出应为删除指令列表。

**优点**
- 降低误删（特别是“一人多偏好”场景）的业务风险。

---

## 5. 代码里如何选择并执行这些 Prompt

### 5.1 事实提取 Prompt 的选择逻辑

在 `_add_to_vector_store()` 中：

1. 若配置了 `custom_fact_extraction_prompt`，直接覆盖默认提取策略。
2. 否则调用 `get_fact_retrieval_messages(parsed_messages, is_agent_memory)`：
   - `is_agent_memory=True` 时走 `AGENT_MEMORY_EXTRACTION_PROMPT`。
   - 否则走 `USER_MEMORY_EXTRACTION_PROMPT`。

`is_agent_memory` 判定条件是：`metadata` 有 `agent_id` 且消息中存在 assistant role。

---

### 5.2 更新治理 Prompt 的执行逻辑

1. 第一轮提取得到 `facts[]`。
2. 对每条 fact 做向量检索，得到旧记忆候选。
3. 用 `get_update_memory_messages()` 组二轮治理 prompt。
4. 解析二轮 JSON，按事件执行：
   - ADD → `_create_memory`
   - UPDATE → `_update_memory`
   - DELETE → `_delete_memory`
   - NONE → no-op（可补写会话元数据）

这里的核心是：**Prompt 负责“判定”，代码负责“执行”**。

---

## 6. 这些 Prompt 的“统一编写规范”总结（可作为团队规范）

结合项目现状，可以提炼出 mem0 已在实践的 Prompt 规范：

1. **角色边界先行**：先规定“可用哪类消息、禁用哪类消息”。
2. **输出协议先行**：强制 JSON key 与枚举值。
3. **few-shot 但不过量**：给足典型例子，不让模型自由发挥。
4. **反提示注入约束**：显式禁止泄露 system prompt。
5. **语言一致性**：输入输出语言保持一致，减少跨语种语义漂移。
6. **可定制但不破协议**：允许 custom prompt，但必须兼容下游解析结构。
7. **状态感知**：治理 prompt 必须携带“当前记忆状态”，而不是只看新增内容。

---

## 7. 综合优点（从工程角度）

1. **可解释**：为什么 ADD/UPDATE/DELETE/NONE，有清晰策略文本和样例。
2. **可调优**：策略在 prompt 层即可迭代，不必频繁改底层存储逻辑。
3. **可扩展**：同一套范式可覆盖语义记忆、图记忆、过程记忆。
4. **低耦合**：判定与执行解耦，便于替换模型或存储引擎。
5. **安全性更高**：角色约束 + JSON 契约 + fallback 解析，降低脏写风险。

---

## 8. 完整案例（一步一步演示）

### 场景
- 当前作用域：`user_id=u_42`
- 已有记忆：
  - `m1`: “喜欢奶酪披萨”
  - `m2`: “在北京工作”
- 新输入：
  - user: “我现在更喜欢鸡肉披萨了，而且我上个月搬到上海工作。”
  - assistant: “收到，我记住了。”

### Step 1：选择提取 Prompt
- 无 `agent_id` → 使用 `USER_MEMORY_EXTRACTION_PROMPT`。

### Step 2：事实提取
- 可能输出：`{"facts": ["更喜欢鸡肉披萨", "上个月搬到上海工作"]}`。

### Step 3：召回旧记忆
- 使用每条 fact 检索候选旧记忆，命中 `m1/m2`。

### Step 4：治理决策
- `DEFAULT_UPDATE_MEMORY_PROMPT` 结合“旧记忆+新事实”给出：
  - `m1` → UPDATE 为“喜欢鸡肉披萨”
  - `m2` → DELETE（北京工作已过时）
  - 新增“在上海工作” → ADD

### Step 5：执行落库
- 代码按事件调用 `_update_memory/_delete_memory/_create_memory`。

这条链路展示了：**系统记住什么、如何修改，主要由策略 prompt 决定**。

---

## 9. ANSI Art 时序图（提取治理主链路）

```text
+--------+        +----------------+        +---------------------+        +-------------+
| Client |        | Memory.add()   |        | LLM Prompt Layer    |        | VectorStore |
+--------+        +----------------+        +---------------------+        +-------------+
    |                      |                            |                         |
    | add(messages, ids)   |                            |                         |
    |--------------------->|                            |                         |
    |                      | parse_messages             |                         |
    |                      | choose USER/AGENT prompt   |                         |
    |                      |--------------------------->| facts extraction         |
    |                      |<---------------------------| {"facts":[...]}         |
    |                      | embed + retrieve old mem   |------------------------>|
    |                      |<-----------------------------------------------------|
    |                      | build governance prompt    |                         |
    |                      | (old memory + new facts)   |                         |
    |                      |--------------------------->| ADD/UPDATE/DELETE/NONE  |
    |                      |<---------------------------| {"memory":[...events]}   |
    |                      | apply events               |------------------------>|
    |                      |<-----------------------------------------------------|
    |                      | return results             |                         |
    |<---------------------|                            |                         |
```

---

## 10. 给你的落地建议（如果要继续升级）

1. 为 USER/AGENT 提取加“负样例”few-shot（特别是 system 注入、反问句、否定句）。
2. 对 UPDATE/DELETE 增加“置信度分层”输出（例如 `confidence`），再由代码做阈值保护。
3. 统一维护“提示词版本号”（Prompt Versioning），便于线上回溯记忆变更原因。
4. 在 CI 增加 prompt 契约测试（JSON schema + 关键枚举值 + 错误恢复）。

这样可以把现有“可用”策略层，升级为“可审计、可回放、可灰度”的治理系统。

---

## 11. 每个提示词“什么时候被调用、在什么场景调用、结果是什么”（逐个说明）

> 本节按“**使用场景 → 调用时机 → 输入形态 → 处理结果**”统一描述，便于直接用于设计评审。

### 11.1 `USER_MEMORY_EXTRACTION_PROMPT`

- **使用场景**：用户画像/偏好记忆沉淀（如饮食偏好、职业、计划）。
- **调用时机**：`Memory.add(..., infer=True)` 且未命中 agent 抽取条件时。
- **典型输入**：一段 user+assistant 对话文本（由 `parse_messages` 拼接），但 prompt 要求只采 user 内容。
- **处理结果**：返回 `{"facts": [...]}`；若为空则本轮不进入更新决策。
- **案例**：
  - 对话：`user=“我乳糖不耐受，喜欢无糖酸奶。”`，`assistant=“收到。”`
  - 输出：`{"facts":["乳糖不耐受","喜欢无糖酸奶"]}`
  - 后续：进入 ADD/UPDATE/DELETE/NONE 决策。

### 11.2 `AGENT_MEMORY_EXTRACTION_PROMPT`

- **使用场景**：assistant persona / capability 画像记忆。
- **调用时机**：`metadata` 含 `agent_id` 且消息中含 assistant role（`_should_use_agent_memory_extraction=True`）。
- **典型输入**：同样是拼接对话文本，但 prompt 只允许抽 assistant 内容。
- **处理结果**：返回 assistant 侧 facts，供后续治理决策写入。
- **案例**：
  - 对话：assistant 说“我擅长把复杂任务拆解成步骤，并优先给可执行方案”。
  - 输出：`{"facts":["擅长任务拆解","优先给可执行方案"]}`

### 11.3 `FACT_RETRIEVAL_PROMPT`（legacy）

- **使用场景**：兼容旧流程/旧调用器。
- **调用时机**：通过 `get_fact_retrieval_messages_legacy` 显式使用时。
- **典型输入**：对话文本。
- **处理结果**：返回 `facts[]`，但角色约束相对新 prompt 更宽。
- **案例**：老版本集成未切换 USER/AGENT 双 prompt 时继续可用。

### 11.4 `DEFAULT_UPDATE_MEMORY_PROMPT`

- **使用场景**：语义记忆治理决策（事实如何作用于已有记忆）。
- **调用时机**：第一轮已产出非空 `facts[]` 后；由第二轮 LLM 决策调用。
- **典型输入**：`旧记忆候选 + 新facts`。
- **处理结果**：返回带事件的 JSON（`ADD/UPDATE/DELETE/NONE`），程序据此执行写库。
- **案例**：
  - 旧记忆：`喜欢奶酪披萨`
  - 新事实：`更喜欢鸡肉披萨`
  - 结果：`UPDATE`（可能融合或替换为更完整表达）

### 11.5 `get_update_memory_messages(...)`

- **使用场景**：治理 prompt 的“运行时编排”。
- **调用时机**：发起第二轮治理 LLM 前必经。
- **典型输入**：`retrieved_old_memory_dict`、`response_content(facts)`、可选 `custom_update_memory_prompt`。
- **处理结果**：生成最终治理指令 prompt（含 JSON 输出契约），用于请求 LLM。
- **案例**：当前记忆为空时自动注入 `Current memory is empty.`，引导模型以 ADD 为主。

### 11.6 `EXTRACT_RELATIONS_PROMPT`

- **使用场景**：图记忆三元组抽取（实体-关系-实体）。
- **调用时机**：graph memory add 流程中，需从文本提关系时。
- **典型输入**：用户文本 + 运行时替换后的 `USER_ID`/`CUSTOM_PROMPT`。
- **处理结果**：产出关系候选，用于图存储写入。
- **案例**：
  - 输入：“我在上海工作，隶属XX团队”
  - 输出候选：`USER_ID -- works_in -- 上海`，`USER_ID -- member_of -- XX团队`

### 11.7 `UPDATE_GRAPH_PROMPT`

- **使用场景**：图关系增量更新（冲突修正、关系精炼、去重）。
- **调用时机**：图记忆已有关系，且有新关系进入时。
- **典型输入**：`existing_memories` + `new_memories`。
- **处理结果**：返回“需要更新的关系指令列表”，仅更新必要边。
- **案例**：旧关系“USER_ID -- lives_in -- 北京”，新信息“现居上海”→ 更新该边关系。

### 11.8 `DELETE_RELATIONS_SYSTEM_PROMPT`

- **使用场景**：图关系删除治理（删除过时/矛盾边）。
- **调用时机**：图删除判断阶段，先检索现有关系再喂给 LLM 评估。
- **典型输入**：现有关系列表 + 新文本。
- **处理结果**：返回应删关系指令；对“同关系不同目标可并存”场景有保护。
- **案例**：
  - 旧：`alice -- loves_to_eat -- pizza`
  - 新：`Alice also loves to eat burger`
  - 结果：不删 pizza 关系（并存保护生效）。

### 11.9 `PROCEDURAL_MEMORY_SYSTEM_PROMPT`

- **使用场景**：代理执行历史的程序性记忆总结（非事实槽位抽取）。
- **调用时机**：`memory_type=procedural_memory` 的创建流程。
- **典型输入**：N 步 agent 执行轨迹。
- **处理结果**：结构化“过程摘要”，用于后续任务接续。
- **案例**：浏览器代理多步抓取后生成“动作-结果-上下文”连续记录。

---

## 12. 调用时机与结果的一览表（速查）

| 提示词 | 典型调用时机 | 主要输入 | 主要输出/处理结果 |
|---|---|---|---|
| `USER_MEMORY_EXTRACTION_PROMPT` | `infer=True` 且非 agent 抽取 | 对话文本 | `facts[]`（用户事实） |
| `AGENT_MEMORY_EXTRACTION_PROMPT` | `agent_id` 存在且有 assistant 消息 | 对话文本 | `facts[]`（助手画像事实） |
| `FACT_RETRIEVAL_PROMPT` | 旧流程兼容调用 | 对话文本 | `facts[]` |
| `DEFAULT_UPDATE_MEMORY_PROMPT` | facts 非空后的第二轮治理 | 旧记忆+新事实 | `memory[]` 事件列表 |
| `get_update_memory_messages` | 第二轮 LLM 请求前 | 旧记忆、新事实、可选自定义策略 | 最终治理 prompt 文本 |
| `EXTRACT_RELATIONS_PROMPT` | 图记忆抽取阶段 | 文本+USER_ID | 图关系候选 |
| `UPDATE_GRAPH_PROMPT` | 图更新阶段 | 旧关系+新关系 | 关系更新指令 |
| `DELETE_RELATIONS_SYSTEM_PROMPT` | 图删除判定阶段 | 现有关系+新文本 | 删除指令（含误删保护） |
| `PROCEDURAL_MEMORY_SYSTEM_PROMPT` | procedural memory 创建 | 执行历史 | 结构化过程总结 |
