# 方法5：图谱并行召回（Graph Retrieval）代码级实现全解析

> 目标：解释你提到的这条链路在当前项目里**到底如何实现**：
>
> - 向量召回（`future_memories`）
> - 图谱召回（`future_graph_entities`）
> - 并行执行后合并返回

---

## 1) 核心结论（先说结论）

在 `mem0` 里，图谱并行召回是由 `Memory.search(...)` 在同一个 `ThreadPoolExecutor` 中提交两个任务实现的：

1. `future_memories = executor.submit(self._search_vector_store, ...)`
2. `future_graph_entities = executor.submit(self.graph.search, ...)`（仅 `enable_graph=True` 时）
3. `wait(...)` 等待两路完成
4. 聚合为统一响应：
   - 开启图谱：`{"results": [...], "relations": [...]}`
   - 关闭图谱：`{"results": [...]}`

对应代码就在：`mem0/memory/main.py` 的 `search` 方法中。

---

## 2) 入口条件：何时启用图谱并行

图谱并行召回是否激活，取决于初始化阶段 `self.enable_graph`。

- 默认关闭：`self.enable_graph = False`
- 当 `self.config.graph_store.config` 存在时：
  - 通过 `GraphStoreFactory.create(provider, self.config)` 初始化图模块
  - 置 `self.enable_graph = True`

这意味着：**只要配置了 graph_store，就会进入“向量 + 图谱并行召回”模式**。

---

## 3) 并行检索主流程（sync 版本）

在 `Memory.search(...)` 里，流程是：

1. 构建并校验过滤条件（必须至少有 `user_id/agent_id/run_id` 之一）
2. 进入线程池并行：
   - 向量任务：`_search_vector_store(...)`
   - 图谱任务：`graph.search(...)`（仅启用图谱）
3. `wait` 等待
4. 从 future 取结果
5. （可选）对向量结果做 rerank
6. 合并返回

你提到的关键代码位点（`future_memories` 与 `future_graph_entities`）正是这一步。

---

## 4) 向量召回子流程是如何做的

`_search_vector_store(...)` 的执行顺序：

1. `embedding_model.embed(query, "search")` 生成查询向量
2. 调 `vector_store.search(query=query, vectors=embeddings, limit=limit, filters=filters)`
3. 把返回的 memory 对象规范化为统一字段：
   - `id`, `memory`, `hash`, `created_at`, `updated_at`, `score`
   - `user_id/agent_id/run_id/actor_id/role`
   - 其他字段放进 `metadata`
4. 若设置了 `threshold`，会按分数做裁剪

所以“向量侧”最终落地是一个 `results` 列表。

---

## 5) 图谱召回子流程是如何做的（默认 Neo4j 图存储）

默认图实现来自 `mem0/memory/graph_memory.py::MemoryGraph.search(...)`，核心是：

1. 用 LLM 从 query 抽实体（`_retrieve_nodes_from_data`）
2. 用实体去图数据库查关系（`_search_graph_db`）
3. 对关系三元组序列做 BM25 重排
4. 返回形如：
   ```json
   [
     {"source": "张三", "relationship": "负责", "destination": "项目A"},
     {"source": "项目A", "relationship": "存在风险", "destination": "预算超支"}
   ]
   ```

所以“图谱侧”最终落地是 `relations` 列表（关系链片段）。

---

## 6) 完整业务案例：企业知识图谱查询

### 查询问题

> “张三负责的项目预算风险”

### 假设数据

- 向量库里有记忆：
  - “张三在 2024Q4 负责项目A，预算从 500 万调整到 650 万。”
  - “项目A 风险评估：供应商延期会导致预算超支 12%。”
- 图谱里有关系：
  - `(张三)-[负责]->(项目A)`
  - `(项目A)-[预算风险]->(超支12%)`
  - `(超支12%)-[原因]->(供应商延期)`

### 调用示例（Python）

```python
from mem0 import Memory

memory = Memory.from_config({
    "vector_store": {
        "provider": "qdrant",
        "config": {"collection_name": "enterprise_memories", "host": "localhost", "port": 6333}
    },
    "embedder": {
        "provider": "openai",
        "config": {"model": "text-embedding-3-small"}
    },
    "llm": {
        "provider": "openai_structured",
        "config": {"model": "gpt-4o-mini"}
    },
    "graph_store": {
        "provider": "memgraph",  
        "config": {
            "url": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "password",
            "database": "neo4j",
            "base_label": True
        }
    }
})

resp = memory.search(
    query="张三负责的项目预算风险",
    user_id="u_enterprise_001",
    limit=5,
    threshold=0.3,
)

print(resp)
```

### 预期返回结构

```json
{
  "results": [
    {
      "id": "mem_001",
      "memory": "张三在2024Q4负责项目A，预算从500万调整到650万",
      "score": 0.89,
      "user_id": "u_enterprise_001"
    },
    {
      "id": "mem_017",
      "memory": "项目A风险评估：供应商延期会导致预算超支12%",
      "score": 0.84,
      "user_id": "u_enterprise_001"
    }
  ],
  "relations": [
    {"source": "张三", "relationship": "负责", "destination": "项目A"},
    {"source": "项目A", "relationship": "预算风险", "destination": "超支12%"},
    {"source": "超支12%", "relationship": "原因", "destination": "供应商延期"}
  ]
}
```

---

## 7) 一步一步推理演示（把并行过程“跑一遍”）

我们把 `search("张三负责的项目预算风险")` 拆成 10 个推理步骤：

1. **输入校验**：必须带 `user_id/agent_id/run_id` 至少一个，否则直接报错。
2. **过滤器拼装**：构造 `effective_filters`，例如 `{"user_id": "u_enterprise_001"}`。
3. **提交任务A（向量）**：
   - 先 embedding
   - 再 `vector_store.search(...)`
4. **提交任务B（图谱）**（启用图谱时）：
   - LLM 抽取实体：`张三 / 项目 / 预算风险`
   - 图查询关系子图
   - BM25 选 topN 关系链
5. **主线程 wait**：等待 A/B 两路 future 完成。
6. **取回向量结果**：得到高语义相似的文本记忆片段。
7. **取回图谱结果**：得到结构化实体关系。
8. **（可选）rerank**：仅对向量结果做重排，提升语义排序质量。
9. **聚合响应**：
   - `results = 向量结果`
   - `relations = 图谱结果`
10. **返回给上层应用**：应用可把 `results+relations` 拼装成最终回答。

这正对应你给的业务理解：

- 向量给“文本证据”
- 图给“关系链证据”
- 两者并行，最后 Merge

---

## 8) ANSI Art 时序图（并行召回 + 合并）

```ansi
+--------+        +---------------------+        +------------------+
|  User  |        |    Memory.search    |        |   ThreadPool     |
+---+----+        +----------+----------+        +--------+---------+
    |                          |                            |
    | query="张三负责的项目预算风险" |                            |
    |------------------------->|                            |
    |                          | submit future_memories     |
    |                          |--------------------------->|
    |                          | submit future_graph_entities (enable_graph)
    |                          |--------------------------->|
    |                          |                            |
    |                          |   +--------------------+   |
    |                          |   | VectorStore.search |<--+
    |                          |   +--------------------+
    |                          |            ||
    |                          |            || embedding(query)
    |                          |
    |                          |   +--------------------+   |
    |                          |   |   Graph.search     |<--+
    |                          |   +--------------------+
    |                          |            ||
    |                          |            || entity extract + graph traverse + BM25
    |                          |
    |                          | wait(future_memories, future_graph_entities)
    |                          |<--------------------------->|
    |                          | merge => {results, relations}
    |<-------------------------|
    |        unified response  |
```

---

## 9) 工程侧注意点（落地建议）

1. 图谱 sidecar 是可选增强，不是强依赖：没配置 `graph_store` 时仍可正常向量检索。
2. 向量结果支持 `threshold`，但图谱关系当前没有同等分数阈值裁剪接口（更多依赖图查询与 BM25）。
3. rerank 当前只作用于 `results`，不作用于 `relations`。
4. 如果要实现“联合排序”（文本+关系打分融合），需要在 merge 后增加融合层（例如 weighted scoring / graph-aware reranker）。

---

## 10) 结语

你提出的“方法5：图谱并行召回”在本项目中已经是**一等公民能力**：

- 架构层：线程池并行
- 算法层：向量语义 + 图谱关系
- 输出层：统一 JSON 双通道返回

这使得企业知识场景（如“人员-项目-风险”）能同时拿到“语义记忆”和“结构关系”，显著提升答案可解释性与可追溯性。
