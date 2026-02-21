# Mem0 召回方法与评估指标（中文说明）

本文整理了当前项目里已经实现/使用的召回方式，并用业务场景解释 Recall@K、Precision@K、MRR、nDCG@K 的意义。

## 1. 什么是“召回（Recall）”

一句话：**把“本来应该找到的相关内容”找回来多少。**

- 你怕漏答案：优先看 Recall@K
- 你怕前几条太脏：优先看 Precision@K
- 你要前 1~3 条特别准：重点看 MRR / nDCG@K

---

## 2. 当前项目中使用的召回方法

> 结论：当前主链路采用 **Dense 向量召回**，并叠加 **元数据过滤**、**阈值过滤**、**可选重排（rerank）**、**可选图谱并行检索**。

### 方法 A：Dense 向量 Top-K 召回（主方法）

流程：`query -> embedding -> vector_store.search(top-k)`。

- 在 `search` 流程中，先对 query 做 embedding，再向量检索。
- 同步与异步版本都采用相同思想。

适用场景：通用语义检索、用户记忆召回、FAQ 语义问答。

### 方法 B：元数据过滤召回（Filter-scoped Retrieval）

流程：先构建/处理过滤条件，再做向量检索。

- 支持 `AND/OR/NOT`
- 支持 `eq/ne/gt/gte/lt/lte/in/nin/contains/icontains`
- 典型过滤字段：`user_id/agent_id/run_id/actor_id/role`

适用场景：多租户隔离、会话隔离、仅检索某角色记忆。

### 方法 C：阈值召回（Thresholded Retrieval）

流程：向量检索后，仅保留 `score >= threshold` 的结果。

适用场景：高风险问答（医疗、法务）中降低低相关噪声。

### 方法 D：重排召回（Rerank）

流程：先向量粗召回，再用 reranker 精排。

- 代码支持多个 reranker provider（如 `cohere`、`sentence_transformer`、`llm_reranker` 等）。

适用场景：候选很多时，将最相关内容压到前几位，提升首屏质量。

### 方法 E：图谱并行检索（Graph + Vector）

流程：当 `enable_graph=true` 时，向量检索与图检索并发执行，最后合并返回。

适用场景：需要实体关系链路（人物-组织-事件）的问答。

### 方法 F：新增记忆时的“冲突召回”

这不是 `search()` API 的对外召回，但它是记忆更新闭环的重要召回：

- 对每个新 fact 做 embedding
- 检索相关旧记忆
- 再交给 LLM 判断 `ADD/UPDATE/DELETE/NONE`

适用场景：用户偏好变化、事实纠偏、去重与冲突消解。

---

## 3. 指标定义与直觉

### Recall@K

在前 K 条结果中，命中了多少“应该命中的相关项”。

- 高 Recall@K：漏召回少
- 低 Recall@K：漏掉关键答案

### Precision@K

前 K 条结果里，有多少比例是真的相关。

- 高 Precision@K：前几条更“干净”
- 低 Precision@K：前几条噪声多

### MRR（Mean Reciprocal Rank）

看“第一个正确答案”的位置是否靠前。

- 第 1 位命中最好（分数最高）
- 第 5 位才命中说明用户体验会差

### nDCG@K

既考虑是否相关，也考虑排序位置与相关等级（graded relevance）。

- 更适合复杂排序任务（推荐/搜索）
- 不只看“命中没命中”，还看“排得好不好”

---

## 4. 业务案例对照

### 案例 1：客服知识库

用户问“活动商品能退吗？”

- 召回不足：只返回普通退款条款，漏掉活动例外
- 典型做法：先 Top-20 粗召回，再 rerank 到 Top-5

### 案例 2：个性化记忆

用户从“海鲜过敏”改为“轻度海鲜过敏”。

- 若冲突召回成功命中旧记忆，后续更可能触发 UPDATE
- 若召回失败，容易误做 ADD，导致重复或冲突记忆

### 案例 3：企业多租户助手

同一句 query，必须只在当前租户与当前用户范围检索。

- 典型做法：先 metadata filter，再向量检索

---

## 5. 项目内实践建议

1. 同一 collection 尽量固定 embedding 模型与维度。
2. 线上默认采用“两阶段”：向量粗召回 + rerank 精排。
3. 多租户/多会话场景必须启用 metadata 过滤。
4. 对高风险场景设置 threshold，抑制低分噪声。
5. 用固定评测集持续监控 Recall@K / Precision@K / MRR / nDCG@K。

---

## 6. 参考实现位置（仓库）

- `mem0/memory/main.py`
  - `search()` / `_search_vector_store()`（向量召回、阈值、过滤、并发图检索、rerank）
  - `_process_metadata_filters()` / `_has_advanced_operators()`（高级过滤）
  - `_add_to_vector_store()`（新增记忆时对新 facts 做冲突召回）
- `mem0/vector_stores/base.py`
  - `search(query, vectors, limit, filters)`（向量检索抽象接口）
- `mem0/utils/factory.py`
  - `RerankerFactory`（重排组件工厂）
- `mem0/configs/rerankers/config.py`
  - reranker 配置

