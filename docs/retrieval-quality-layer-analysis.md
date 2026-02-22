# 检索质量层：高级过滤 + 阈值 + Reranker（基于当前代码实现）

本文基于当前仓库代码，系统拆解 Mem0 在 `search` 路径里如何做“检索后治理”：

- 复杂 metadata 逻辑（AND/OR/NOT、比较操作符）
- `threshold` 截断
- 可选 `rerank` 二次排序

> 结论先行：**Mem0 的检索质量层是“统一 filter 表达 + 向量检索 + 分数阈值 + 可插拔 reranker”的组合流水线**。但不同向量库对高级过滤的支持能力不同，实际效果取决于后端适配器。

---

## 1) 主流程入口：`Memory.search` / `AsyncMemory.search`

同步与异步两套实现逻辑基本一致，核心步骤是：

1. 构建 session 过滤条件（必须包含 `user_id/agent_id/run_id` 至少一个）。
2. 检测并处理高级过滤语法（`AND/OR/NOT`、`eq/ne/gt/...`、`*`）。
3. 执行向量检索。
4. 按 `threshold` 做分数截断。
5. 若启用 reranker，则二次重排。
6. 返回 `{"results": ...}`（图谱开启时加 `relations`）。

对应代码：

- 同步 `Memory.search`：`mem0/memory/main.py`（约 758-990 行）
- 异步 `AsyncMemory.search`：`mem0/memory/main.py`（约 1807-2048 行）

---

## 2) 高级过滤：统一语义层如何实现

### 2.1 检测高级语法

`_has_advanced_operators()` 检测以下模式：

- 顶层逻辑运算：`AND` / `OR` / `NOT`
- 字段比较运算：`eq/ne/gt/gte/lt/lte/in/nin/contains/icontains`
- 通配符：`"*"`

如果命中则进入 `_process_metadata_filters()`。

### 2.2 规范化转换

`_process_metadata_filters()` 会把平台无关语法转换为“统一中间格式”：

- `AND`：直接展开并合并到同一个字典（等价于 must/并且）
- `OR`：转成 `"$or": [ ... ]`
- `NOT`：转成 `"$not": [ ... ]`
- 比较运算保留为 `{field: {op: value}}`

也就是说，search 层把高级语义“编译”为统一结构，再交给各向量库适配器。

---

## 3) 阈值截断：发生在向量检索结果标准化之后

`_search_vector_store()` 在拿到向量库返回结果后，会先标准化成 `MemoryItem` 格式，然后做：

- `threshold is None`：保留全部
- 否则仅保留 `mem.score >= threshold`

这一步发生在 rerank 之前，因此阈值是**第一道质量闸门**。

---

## 4) Reranker：可插拔二次排序

### 4.1 初始化

`Memory` 与 `AsyncMemory` 在初始化时，若配置存在 `config.reranker`，通过 `RerankerFactory.create()` 构建 reranker。

`RerankerFactory` 当前支持：

- `cohere`
- `sentence_transformer`
- `zero_entropy`
- `llm_reranker`
- `huggingface`

### 4.2 执行时机

在初检完成且结果非空时：

- `rerank=True` 且 `self.reranker` 存在 -> 调用 `self.reranker.rerank(query, original_memories, limit)`
- 失败会降级（warning + 使用原排序）

这保证 rerank 是**可选增强，不是硬依赖**。

---

## 5) 关键现实：高级过滤“语义统一”，但“后端能力不统一”

虽然 search 层支持丰富表达，但最终是否被正确执行，取决于 vector store 适配器：

- `Chroma` 适配器可识别 `"$or"`，并把 `eq/ne/gt/...` 映射到 Chroma `$eq/$gt/...`；但 `NOT` 当前直接跳过，`contains/icontains` 退化为等值匹配。
- `Qdrant` 适配器目前主要处理简单等值与 `gte+lte` 区间组合；对 `"$or"/"$not"` 等通用结构没有完整翻译逻辑。
- `PGVector` 适配器默认把过滤写成 `payload->>k = v` 的 AND 等值 SQL 条件，不支持复杂逻辑和比较操作符语义翻译。

因此在生产里要做一件事：**按你的向量库能力设计过滤 DSL 使用边界**，并回归验证。

---

## 6) 完整案例（从请求到结果逐步推演）

假设我们调用：

```python
result = memory.search(
    query="下周东京出行偏好",
    user_id="u_42",
    limit=5,
    threshold=0.78,
    rerank=True,
    filters={
        "AND": [
            {"trip_type": {"in": ["business", "vacation"]}},
            {"budget": {"gte": 5000}},
        ],
        "OR": [
            {"language": "ja"},
            {"language": "en"}
        ],
        "NOT": [
            {"sensitive": True}
        ]
    }
)
```

### Step A：构建基础过滤

`_build_filters_and_metadata()` 先把 `user_id` 注入查询过滤，得到最小作用域：

```python
{"user_id": "u_42", ...}
```

### Step B：高级过滤编译

`_has_advanced_operators()` 返回 True，进入 `_process_metadata_filters()`。

产物近似：

```python
{
  "trip_type": {"in": ["business", "vacation"]},
  "budget": {"gte": 5000},
  "$or": [{"language": "ja"}, {"language": "en"}],
  "$not": [{"sensitive": True}]
}
```

再与 session 过滤合并：

```python
{
  "user_id": "u_42",
  "trip_type": {"in": ["business", "vacation"]},
  "budget": {"gte": 5000},
  "$or": [{"language": "ja"}, {"language": "en"}],
  "$not": [{"sensitive": True}]
}
```

### Step C：向量召回

`_search_vector_store()`：

- 先 embed query
- 调用 `vector_store.search(query, vectors, limit, filters)`
- 将返回项标准化为统一 `MemoryItem` 字段

### Step D：阈值截断

遍历结果，仅保留 `score >= 0.78`。

示意：

- m1: 0.91 ✅
- m2: 0.82 ✅
- m3: 0.77 ❌（被截断）

### Step E：rerank 二次排序

把 `[m1, m2]`（阈值后结果）送给 reranker，得到可能的新顺序，比如 `[m2, m1]`。

### Step F：输出

返回：

```json
{
  "results": [
    {"id": "...", "memory": "...", "score": 0.82, "metadata": {...}},
    {"id": "...", "memory": "...", "score": 0.91, "metadata": {...}}
  ]
}
```

---

## 7) 你这句“生产里真正决定效果的是检索后治理”在本项目中的落地结论

1. **过滤是“可控召回边界”**：先约束候选，再谈排序，避免语义相近但业务不相关的记忆混入。
2. **threshold 是“硬门槛”**：它先于 rerank 生效，能快速切掉噪声，但阈值过高会损伤召回。
3. **rerank 是“精排”**：改善 top-k 质量与顺序稳定性，尤其在 query 意图复杂时显著。
4. **适配器差异是关键风险**：同一 filter DSL 在不同向量库的执行语义可能不同，必须做 provider 级验证。
5. **建议调参顺序**：先 filter 正确性 -> 再 threshold -> 最后 rerank 模型与成本。

---

## 8) ANSI Art 时序图（检索质量层）

```text
+---------+        +------------------+        +-------------------+        +-----------------+        +-----------+
| Client  |        | Memory.search()  |        | VectorStoreAdapter|        | Threshold Gate  |        | Reranker  |
+---------+        +------------------+        +-------------------+        +-----------------+        +-----------+
     |                        |                           |                            |                         |
     | query + filters + IDs  |                           |                            |                         |
     |----------------------->|                           |                            |                         |
     |                        | build session filters     |                            |                         |
     |                        | detect advanced operators |                            |                         |
     |                        | compile -> $or/$not/...   |                            |                         |
     |                        |-------------------------->| vector search(query,filter)|                         |
     |                        |                           |--------------------------->| raw hits(score)         |
     |                        |                           |<---------------------------|                         |
     |                        | normalize MemoryItem      |                            |                         |
     |                        |-------------------------->|                            |                         |
     |                        |                           |                            | keep score>=threshold   |
     |                        |<------------------------------------------------------| filtered hits           |
     |                        | if rerank enabled + exists                                                        |
     |                        |--------------------------------------------------------------------------->|      |
     |                        |                                                           rerank(query,hits)|      |
     |                        |<---------------------------------------------------------------------------|      |
     |                        | final results (and relations if graph)                                      |      |
     |<-----------------------|                                                                             |      |
```

---

## 9) 实操建议（生产）

- 给每种向量库建立“过滤能力矩阵”（支持/降级/不支持）。
- 将 `threshold` 设为可观测参数（记录命中率、空结果率、MRR/NDCG）。
- rerank 失败要可追踪（当前代码已降级但建议配告警）。
- 对“强约束字段”优先用 filter，不要让 rerank 去补业务约束。

