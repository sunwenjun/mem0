# UPDATE_GRAPH_PROMPT 在当前项目中的实现现状与演进分析

> 面向场景：新图信息进入后，对现有边关系做修正/精炼/去重。

## 1. 先说结论（TL;DR）

- 项目中**定义了** `UPDATE_GRAPH_PROMPT`，其规则与你给出的 4.2 要求高度一致（以 `source/target` 匹配、冲突时考虑准确性与时效性、仅输出更新指令）。
- 但在当前 Python 主实现里，这个 prompt **尚未接入执行链路**；当前真实落库策略是“**先删后增**”，而不是“同主键关系直接 update”。
- 也就是说：
  - 关系修正能力目前由 `delete_graph_memory` + `add_graph_memory` 的组合间接实现；
  - `update_graph_memory` 工具与 `UPDATE_GRAPH_PROMPT` 目前属于“已定义但未调用”的能力预留。

---

## 2. 代码级全链路：图关系是如何从 LLM tool call 落到知识图谱的

### 2.1 入口：Memory.add -> _add_to_graph -> graph.add

业务入口在 `Memory._add_to_graph`：把消息拼接成文本后，调用图存储的 `self.graph.add(data, filters)`。这意味着“图关系的增删改策略”都在 `graph.add` 内封装。 

### 2.2 核心编排：`MemoryGraph.add`

当前 Python 版图编排顺序如下：

1. 抽实体类型映射：`_retrieve_nodes_from_data`
2. 抽关系（LLM tool call）：`_establish_nodes_relations_from_data`
3. 图检索候选旧关系：`_search_graph_db`
4. 判定需删除关系（LLM tool call）：`_get_delete_entities_from_search_output`
5. 执行删除：`_delete_entities`
6. 执行新增/合并：`_add_entities`

这个流程体现的是“对旧关系先冲突清理，再写入新关系”。

### 2.3 LLM 工具协议（Tool Schema）

`mem0/graphs/tools.py` 定义了如下图工具：

- `update_graph_memory`（更新关系）
- `add_graph_memory`（新增关系）
- `delete_graph_memory`（删除关系）
- `establish_relationships` / `establish_relations`（关系抽取）
- `extract_entities`（实体抽取）

但当前 `graph_memory.py` 里实际被调用的是：

- 实体抽取工具：`extract_entities`
- 关系抽取工具：`establish_relationships`（或结构化版本）
- 删除工具：`delete_graph_memory`

`update_graph_memory` 在当前路径未被使用。

### 2.4 Prompt 层策略

#### A) `UPDATE_GRAPH_PROMPT`（已定义，未接线）

其指导原则与你的 4.2 规则一致：

- 用 source/target 作为匹配主键
- 冲突时按新信息准确性与时效性更新
- 输出仅包含需要更新的指令

#### B) 当前实际生效的是：关系抽取 + 删除判定 Prompt

- 抽关系：`EXTRACT_RELATIONS_PROMPT`
- 删除判定：`DELETE_RELATIONS_SYSTEM_PROMPT`

删除判定 Prompt 里还明确了“不要误删可并存关系”（例如同类关系但不同目的节点可并存）。因此当前语义更接近：

> 关系演进 = “删掉过时/冲突边” + “添加最新边”

而不是“原地更新边类型”。

---

## 3. 完整案例（一步一步推理演示）

下面用一个和你场景一致的“关系修正”例子演示：

### 3.1 初始图谱（已有关系）

- `alice -- works_at -- openai`

### 3.2 新输入文本

- “Alice 现在在 Anthropic 工作。”

### 3.3 代码执行推演

#### Step 1：实体抽取 `_retrieve_nodes_from_data`

LLM 通过 `extract_entities` 输出（示意）：

```json
{
  "entities": [
    {"entity": "alice", "entity_type": "person"},
    {"entity": "anthropic", "entity_type": "organization"}
  ]
}
```

并在代码中做标准化（小写、空格转下划线）。

#### Step 2：关系抽取 `_establish_nodes_relations_from_data`

LLM 通过 `establish_relationships` 输出（示意）：

```json
{
  "entities": [
    {"source": "alice", "relationship": "works_at", "destination": "anthropic"}
  ]
}
```

随后 `_remove_spaces_from_entities` 会继续清洗关系名（Cypher 安全化）。

#### Step 3：近邻图检索 `_search_graph_db`

系统对 `alice` / `anthropic` 做 embedding 检索，召回相关边，典型会得到历史关系：

- `alice -- works_at -- openai`

#### Step 4：删除判定 `_get_delete_entities_from_search_output`

把“已有关系列表 + 新文本”喂给删除判定 Prompt，LLM 通过 `delete_graph_memory` 给出待删边（示意）：

```json
{
  "source": "alice",
  "relationship": "works_at",
  "destination": "openai"
}
```

#### Step 5：执行删除 `_delete_entities`

生成 Cypher：按 `source + relationship + destination + user_id`（以及可选 agent/run 过滤）精确删边。

#### Step 6：执行新增 `_add_entities`

写入：

- `alice -- works_at -- anthropic`

最终图谱达到“关系更新”的业务效果。

---

## 4. 与你的 4.2 `UPDATE_GRAPH_PROMPT` 要求逐条对照

### 要求 A：以 source/target 为主键做匹配

- 设计层：`UPDATE_GRAPH_PROMPT` 已明确该原则。
- 执行层：当前删除/新增均围绕 `(source, relationship, destination)` 操作，严格主键更新（source+target 仅改关系）尚未直接实现。

### 要求 B：处理冲突时考虑准确性与时效性

- 设计层：`UPDATE_GRAPH_PROMPT` 与 `DELETE_RELATIONS_SYSTEM_PROMPT` 都强调“更新/删除以更准确、更近期信息为准”。
- 执行层：当前由“删除判定 LLM”承担冲突判断，再由 Cypher 落库。

### 要求 C：输出仅包含需要更新的关系指令

- 设计层：`UPDATE_GRAPH_PROMPT` 明确“只输出需要更新项”。
- 执行层：当前落地为“只输出需要删除项 + 抽取到的新关系项”；并非独立 update 指令集。

---

## 5. 为什么当前实现依然可用

虽然没有直接使用 `UPDATE_GRAPH_PROMPT`，但“删旧边 + 加新边”在多数场景可达到关系演进目的，且具备这些工程优势：

- Cypher 执行路径清晰：删除与新增天然幂等化（MERGE）。
- 对多图后端兼容性更好（Neo4j/Memgraph 统一思路）。
- 与当前实体抽取、关系抽取、向量召回链路耦合较低。

潜在不足是：

- 缺少“同 source/target 仅替换 relationship”的原子更新语义。
- 无法直接输出“更新指令审计日志”（只有删/增日志）。

---

## 6. ANSI Art 时序图（当前真实实现）

```text
+---------+        +-------------+        +-------------------+        +---------+
|  User   |        | Memory.main |        | MemoryGraph.add   |        | Neo4j   |
+----+----+        +------+------+        +---------+---------+        +----+----+
     |                    |                         |                        |
     | add(messages)      |                         |                        |
     |------------------->|                         |                        |
     |                    | _add_to_graph(data)     |                        |
     |                    |------------------------>|                        |
     |                    |                         | 1) extract_entities     |
     |                    |                         |----LLM tool call------->|
     |                    |                         |<---entity_type_map------|
     |                    |                         |                        |
     |                    |                         | 2) establish_relations   |
     |                    |                         |----LLM tool call------->|
     |                    |                         |<---to_be_added----------|
     |                    |                         |                        |
     |                    |                         | 3) search_graph_db       |
     |                    |                         |------------------------->|
     |                    |                         |<---search_output---------|
     |                    |                         |                        |
     |                    |                         | 4) delete_graph_memory   |
     |                    |                         |----LLM tool call------->|
     |                    |                         |<---to_be_deleted--------|
     |                    |                         |                        |
     |                    |                         | 5) DELETE edges          |
     |                    |                         |------------------------->|
     |                    |                         |<---deleted_entities------|
     |                    |                         |                        |
     |                    |                         | 6) MERGE nodes/edges     |
     |                    |                         |------------------------->|
     |                    |                         |<---added_entities--------|
     |                    |<------------------------|                        |
     |<-------------------| return relations result |                        |
```

---

## 7. 演进建议（若要完全落地 4.2）

如果你希望严格实现 `UPDATE_GRAPH_PROMPT` 的“source/target 主键更新”语义，可在当前链路中增加一个 **update 阶段**：

1. 用 `UPDATE_GRAPH_PROMPT` + `update_graph_memory` 产出更新指令；
2. 对每条指令执行 Cypher：
   - `MATCH (s)-[r]->(t)` 按 source/target 定位
   - 删除旧类型关系并创建新类型关系，或改为关系属性模型
3. 仅当 update 无法覆盖时再 fallback 到 delete+add。

这样可以同时得到：

- 可解释的“更新指令列表”（满足审计）
- 更精确的“关系演进语义”

