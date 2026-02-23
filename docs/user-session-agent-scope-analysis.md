# Mem0 中 “存储 metadata” 与 “查询 filters” 的含义（User / Session / Agent 作用域）

## 结论先行

在 Mem0 的 Python SDK 中，`user_id` / `run_id` / `agent_id` 不是三套互斥机制，而是**同一套“作用域标签”机制的三个可选维度**：

1. 写入时，把作用域信息放进每条记忆的 metadata（向量库 payload）。
2. 检索时，把同样的作用域条件放进 filters（查询过滤条件）。
3. 三者至少要提供一个，否则直接报错，避免产生“无作用域”的记忆污染全局。

这套逻辑由 `_build_filters_and_metadata(...)` 统一构建，返回 `(base_metadata_template, effective_query_filters)` 两个字典供后续写入与检索复用。

---

## “存储 metadata” 是什么？

**存储 metadata** 指：在 `add(...)` 写入记忆时，除了正文（`data`）和向量外，还把 `user_id` / `run_id` / `agent_id` 等字段作为 payload 元数据一并存入向量库。

在实现中：

- `_build_filters_and_metadata(...)` 会把传入的 `user_id`、`agent_id`、`run_id` 写入 `base_metadata_template`。
- `Memory.add(...)` 调用该函数后，把返回的 `processed_metadata` 继续传给 `_add_to_vector_store(...)`，最终成为每条记忆的 payload 元信息。

直观理解：

- 记忆内容是“这条记忆说了什么”；
- metadata 是“这条记忆属于谁、属于哪次会话、属于哪个 agent，以及其它业务标签”。

这一步的目的，是给记忆“打标签”，让后续检索能按范围精准命中。

---

## “查询 filters” 是什么？

**查询 filters** 指：在 `search(...)`、`get_all(...)` 等读取场景下，先构造过滤条件，再让向量库只在满足条件的记忆集合里执行检索或列举。

在实现中：

- `_build_filters_and_metadata(...)` 同步把 `user_id` / `agent_id` / `run_id` 放进 `effective_query_filters`。
- `get_all(...)` 将该 filters 传入 `vector_store.list(filters=...)`。
- `search(...)` 将该 filters 传入 `_search_vector_store(...)`（进而作用到向量检索）。

直观理解：

- metadata 是“写进去的标签”；
- filters 是“读出来时用来筛选标签的条件”。

两者是一体两面：**先标注，再按标注筛选**。

---

## 为什么必须至少给一个作用域 ID？

`_build_filters_and_metadata(...)` 内部有显式校验：如果 `user_id` / `agent_id` / `run_id` 都没给，会抛 `Mem0ValidationError`。

这意味着系统在设计上强制每条记忆“有归属范围”，避免出现以下问题：

- 任何用户都可能命中同一批“公共脏记忆”；
- 不同会话/任务的上下文互相干扰；
- 无法做稳定的多租户隔离与回溯。

---

## 三个 ID 如何组合？

Mem0 的行为是“可叠加”，不是“二选一”：

- 只传 `user_id`：按用户全局长期记忆聚合。
- 只传 `run_id`：按一次会话/任务隔离。
- 只传 `agent_id`：按某个 agent 的工作记忆隔离。
- 同时传多个：交集约束（例如 `user_id + run_id`），范围更窄，结果更精准。

因此你可以把三者看成一组可组合的作用域键，而不是三套不同存储系统。

---

## `actor_id` 的特殊点（补充）

同一函数还支持 `actor_id`：

- `actor_id` 只进入查询 filters（用于检索时进一步限定说话人/角色）。
- 默认不会写入 `base_metadata_template`（即不会作为写入模板字段被强制落库）。

这说明它更偏向“检索侧的附加约束”，而非核心作用域主键。

---

## 一个最小心智模型

可以把 Mem0 的作用域机制记成一句话：

> **写入时：给记忆打 scope 标签；检索时：用同样的 scope 标签做过滤。**

其中 `user_id` / `run_id` / `agent_id` 至少一个必填，保证每条记忆都“有边界、有归属、可隔离”。

