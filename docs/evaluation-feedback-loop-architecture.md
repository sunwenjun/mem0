# 评测体系（Evaluation）作为架构反馈闭环：Mem0 项目代码全链路解析

> 目标：解释这个仓库如何把“实验执行 + 指标评估 + 结果汇总”放在同一代码库内，形成可持续迭代的架构闭环。

## 1. 闭环不是口号，而是由代码结构强制实现

在 `evaluation/` 目录中，项目把流程拆成了三个连续阶段：

1. **实验生成结果**：`run_experiments.py` 统一调度多种 memory 技术（Mem0 / RAG / LangMem / OpenAI memory / Zep）。
2. **结果评测**：`evals.py` 对每条问答计算 BLEU、F1，并调用 `llm_judge.py` 做 LLM 裁判评分。
3. **统计汇总**：`generate_scores.py` 聚合到各 category 以及 overall 平均分。

这就是典型的“**实验 -> 指标 -> 复盘 -> 再实验**”反馈环。它不是只做一次离线实验，而是把 pipeline 固化成可重复运行的工程能力。

---

## 2. 代码分层：从 Orchestrator 到 Metric 的职责边界

## 2.1 统一入口（Orchestrator）

`evaluation/run_experiments.py` 是总入口，根据 `--technique_type` 分派到不同实现：

- `mem0`：`MemoryADD`（写入）+ `MemorySearch`（检索问答）
- `rag`：`RAGManager`
- `langmem`：`LangMemManager`
- `zep`：`ZepAdd` + `ZepSearch`
- `openai`：`OpenAIPredict`

这种写法的价值是：**所有候选架构都遵守同一“输入数据集 -> 输出结果 JSON”契约**，便于后续共用同一评测器。

## 2.2 技术实现层（Technique Adapters）

每种技术在 `evaluation/src/` 下有自己的 adapter：

- `memzero/`：分成 add 与 search 两阶段（先建记忆，再问答）。
- `rag.py`：对会话做 chunk + embedding + 相似度检索，再拼 prompt 给模型回答。
- `langmem.py`：用 LangGraph + LangMem tool 管理 memory/search。
- `zep/`：写入到 Zep，搜索时用 graph edge/node 组成上下文。
- `openai/predict.py`：直接读取外部 memory 文本并回答。

**关键点**：虽然底层技术差异大（向量检索、知识图谱、Agent memory 工具等），但输出结构都对齐到 question/answer/response/category + latency/context 等字段，利于公平比较。

## 2.3 评测层（Metrics Layer）

`evaluation/evals.py` 会并发处理实验结果：

- 词面类指标：`calculate_bleu_scores`（BLEU1）
- overlap 类指标：`calculate_metrics` 中的 token-level F1
- 语义裁判：`evaluate_llm_judge`（OpenAI 模型按 CORRECT/WRONG 打分）

最终生成 `evaluation_metrics.json`，然后 `generate_scores.py` 按 `category` 聚合均值并输出 overall。

## 2.4 反馈回路如何落回架构决策

因为结果文件中同时保留了：

- 准确率指标（BLEU/F1/LLM score）
- 性能指标（如 `search_time`、`response_time`）
- 过程证据（`context`、召回 memories、graph relations）

所以你可以做**定位式迭代**：

- 分数低但检索快：通常是召回不足 -> 调 `top_k`、chunk 策略。
- 分数高但时延高：可能 memory 检索过重 -> 缩减上下文或索引策略。
- 时间类问题错误集中：优先调 prompt 的时间推理约束。

---

## 3. “完整案例”演示：以 Mem0 搜索评测为例

下面给一个完整可复现的案例路径（命令只是流程示意）：

### Step A：写入记忆（离线建库）

```bash
python evaluation/run_experiments.py --technique_type mem0 --method add
```

内部行为（对应 `src/memzero/add.py`）：

1. 读取 `dataset/locomo10.json`。
2. 每条会话抽取 `speaker_a/speaker_b`，构造隔离的 `user_id`（如 `Alice_0`, `Bob_0`）。
3. 将会话轮次转成 role message（双方视角各一份）。
4. 调 Mem0 API 批量写入，支持 `enable_graph`（Mem0+）。

### Step B：检索 + 回答（在线问答）

```bash
python evaluation/run_experiments.py --technique_type mem0 --method search --top_k 30
```

内部行为（对应 `src/memzero/search.py`）：

1. 对每个问题分别查询 speaker1/speaker2 的 memory。
2. 每侧取 `top_k` 记忆并带上 `timestamp/score`。
3. 将两侧 memories（以及可选 graph relations）套入 `prompts.py` 模板。
4. 调 OpenAI 生成最终短答案。
5. 记录 `speaker_*_memory_time`、`response_time`、context 证据到结果 JSON。

### Step C：统一评测

```bash
python evaluation/evals.py --input_file evaluation/results/mem0_results_top_30_filter_False_graph_False.json --output_file evaluation/evaluation_metrics.json
```

内部行为：

1. 跳过 `category == 5`（代码中显式过滤）。
2. 对每条 QA 计算 BLEU1/F1。
3. 调 LLM Judge 输出二值 `llm_score`。
4. 保存逐题评测结果。

### Step D：分组与总体汇总

```bash
python evaluation/generate_scores.py
```

内部行为：

1. 加载 `evaluation_metrics.json`。
2. 按 `category` 做均值聚合并计数。
3. 输出 overall mean（BLEU/F1/LLM）。

到这里，闭环完成：你已经拿到“某架构配置 -> 指标画像”。下一轮可以改 `top_k/filter_memories/is_graph/chunk_size` 继续跑。

---

## 4. 一步一步推理演示（单个问题的执行轨迹）

假设问题：

- Question: *“Do you remember when I started guitar lessons?”*

在 Mem0 Search 路径下，系统的推理链路是：

1. **双侧召回**：从 speaker1 与 speaker2 各自 memory 空间检索相关片段。
2. **时间锚点对齐**：优先带时间戳的记忆（prompt 明确要求把相对时间换算为绝对时间）。
3. **冲突消解**：若多条记忆冲突，按 prompt 指令优先较新的时间证据。
4. **短答案生成**：限制到 5-6 词，避免解释性冗长。
5. **评测打分**：
   - BLEU/F1 检查文本重叠；
   - LLM Judge 判断语义是否“同一事实”。

如果该问题 BLEU 低但 LLM Judge 高，通常表示“语义正确但表述不同”；如果三项都低，通常说明检索上下文本身不足。

---

## 5. 关键架构观察（为什么这套设计值得学习）

1. **同仓评测**：实验脚本和评测脚本同仓，避免“代码与评测标准分裂”。
2. **统一接口**：异构 memory 技术都产出可比较 JSON，降低横向对比成本。
3. **可解释结果**：不只存分数，还存检索证据和耗时，支持根因定位。
4. **参数即实验变量**：`top_k`、`chunk_size`、`is_graph`、`filter_memories` 都是显式实验旋钮。
5. **并发设计**：实验侧和评测侧均使用并发（ThreadPool / multiprocessing）提高迭代吞吐。

---

## 6. ANSI Art 时序图（Evaluation Feedback Loop）

```text
+-----------+      +----------------------+      +-------------------+
| Researcher|      | run_experiments.py   |      | Technique Adapter |
+-----------+      +----------------------+      +-------------------+
      |                         |                            |
      | 1) choose technique     |                            |
      |------------------------>|                            |
      |                         | 2) dispatch by type        |
      |                         |--------------------------->|  (Mem0/RAG/LangMem/Zep/OpenAI)
      |                         |                            |
      |                         | 3) produce results JSON    |
      |                         |<---------------------------|
      |                         |                            |
      | 4) run evals.py         |                            |
      |------------------------>|                            |
      |                         |----> BLEU/F1 (utils)       |
      |                         |----> LLM Judge             |
      |                         |----> evaluation_metrics.json
      |                         |
      | 5) run generate_scores.py
      |------------------------>|
      |                         |----> category means / overall means
      |                         |
      | 6) decide next config   |
      |<------------------------|
      |
      | 7) iterate: tune top_k/chunk_size/is_graph/filter_memories
      |------------------------------------------------------------>
```

---

## 7. 实战建议：如何用这套闭环训练“架构思维”

- 先固定模型，只改 retrieval 参数（`top_k/chunk_size`）观察“召回-延迟”曲线。
- 再引入结构增强（Mem0 graph / Zep graph）看时间类问题是否有提升。
- 最后再动 prompt 与裁判策略，避免变量耦合导致无法定位收益来源。

一句话总结：这个仓库把“系统实现”升级成了“可实验、可测量、可迭代”的**研究型工程系统**。
