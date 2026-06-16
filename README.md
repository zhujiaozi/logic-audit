# Logic Audit — MCP Server

[![License](https://img.shields.io/badge/License-MIT-111111?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square)](pyproject.toml)
[![Z3](https://img.shields.io/badge/Z3-Theorem%20Prover-FF6B35?style=flat-square)](https://github.com/Z3Prover/z3)
[![MCP](https://img.shields.io/badge/MCP-Model%20Context%20Protocol-6B5B95?style=flat-square)](https://modelcontextprotocol.io)
[![Claude Code](https://img.shields.io/badge/Claude%20Code-Ready-111111?style=flat-square)](https://claude.ai)

**Z3 定理证明器驱动的形式谬误验证工具**，通过 MCP 协议接入任何支持 MCP 的 AI Agent（Claude Code、Codex 等）。

与纯 LLM 的软判断不同，本工具对可形式化的推理模式给出**可证明、可核查、可复现**的验证结论——支持 `↯` 反例生成、批量验证、以及 LLM Judge 反例合理性校验。

---

## 30 秒安装

```bash
pip install -e .
```

添加到 Claude Code 的 `~/.claude/settings.local.json`：

```json
{
  "mcpServers": {
    "logic-audit": {
      "command": "python",
      "args": ["src/mcp_server.py"]
    }
  }
}
```

重启对话即可使用。

---

## 能做什么

本工具对**形式谬误**做精确验证，区别于 LLM 的概率性判断：

| # | 能力 | 说明 |
|---|------|------|
| 1 | 形式谬误检测 | Z3 定理证明器验证 6 类形式谬误，返回 SAT/UNSAT 及反例模型 |
| 2 | 反例生成 | 不仅说"有错"，还给出具体赋值（如 `{P=False, Q=True}`），可人工复核 |
| 3 | 批量验证 | 一次验证数十条 FOL 编码，支持并发加速 |
| 4 | 反例合理性校验 | 可选 LLM Judge 环节，判断 Z3 反例在自然语言上下文中是否合理 |
| 5 | 内置评估 | 48 条手写测试用例，覆盖全部 6 类谬误 + 有效推理，一键运行 |

### 与 LLM 直接判断的差异

| 场景 | 纯 LLM 判决 | 本工具（Z3 + logic_cp） |
|------|------------|------------------------|
| `If P then Q, Q, ∴ P` | 可能识别，但无确定保证 | Z3 返回 SAT + 反例 `{P=False, Q=True}` — **可证明** |
| `∀x(A→B), B(c), ∴ A(c)` | 依赖训练数据覆盖 | Z3 返回 SAT + 反例模型 — **证明类型** |
| 同一条文重复 10 次 | 可能不同（温度影响） | **完全相同**（确定性） |
| 反例是否匹配原文 | 不可单独验证 | logic_cp 做二次复核，可信度分级 |
| 洁净文本（无谬误） | 可能误报 | Z3 返回 UNSAT → discard |

---

## 架构

```
输入：FOL 编码（变量 + 谓词公式）
        │
        ▼
┌─────────────────┐
│  Z3 Verifier    │  ←  FOL 解析 → Z3 Solver → SAT / UNSAT / UNKNOWN
│  (z3_verifier)  │      + 反例模型（SAT 时）
└────────┬────────┘
         │
         ▼  (如果 SAT)
┌─────────────────┐
│  logic_cp       │  ←  构建 prompt → LLM Judge → 解析 verdict
│  (cp_verifier)  │      → valid:  保持 formal（高置信度）
│                 │      → invalid: 降级 candidate（翻译存疑）
└─────────────────┘
         │
         ▼
输出：verification_verdict
      formal  → 谬误确认（Z3 证明 + 可选 LLM 确认）
      discard → 无谬误（推理有效）
      candidate → 不确定（翻译错误 / 超时 / 未知）
```

---

## 工具清单

通过 MCP 协议暴露 9 个工具：

### 核心验证

| 工具 | 输入 | 输出 |
|------|------|------|
| `verify_fol_encoding` | 变量声明 + 前提公式 + 结论 + 查询类型 | z3_result + 反例模型 + verification_verdict |
| `batch_verify` | 多条 FOL 编码 | 每条的结果数组 |
| `verify_with_logic_cp` | 同上 + 可选 original_text + 可选 llm_judgment | 一次调用完成 Z3 → CP prompt → 融合判决全流程 |

### 反例校验流水线

| 工具 | 说明 |
|------|------|
| `build_logic_cp_prompt` | 构建 LLM Judge prompt，让外部 LLM 判断反例是否合理 |
| `parse_logic_cp_judgment` | 解析 LLM Judge 返回 → valid/invalid/unknown |
| `apply_logic_cp_verdict` | 将 LLM 判决写入 Z3 结果（formal → candidate 降级等） |

### 评估与参考

| 工具 | 说明 |
|------|------|
| `run_direction_b_eval` | 在 48 条内置测试数据集上运行 Z3 评估（无需 LLM） |
| `get_error_type_info` | 查询 7 类形式谬误的 FOL 编码模式 |
| `parse_fol_formula` | 验证 FOL 公式语法，不跑 Z3 |

---

## 支持的形式谬误

| 类型 | NL 模式 | FOL 模式 | Z3 预期 |
|------|---------|----------|---------|
| 肯定后件 | 如果 P 则 Q，Q，所以 P | Implies(P,Q), Q ⊢ P | **SAT** → formal |
| 否定前件 | 如果 P 则 Q，非 P，所以非 Q | Implies(P,Q), ¬P ⊢ ¬Q | **SAT** → formal |
| 非循序 | P，所以 Q（无关） | P ⊢ Q（P、Q 无共享符号） | **SAT** → formal |
| 矛盾 | 既 P 又非 P | P, ¬P | **UNSAT** → formal |
| 循环论证 | P↔Q，所以 P | Eq(P,Q) ⊢ P | **SAT** → formal |
| 无效三段论 | 所有 A 是 B，C 是 B，所以 C 是 A | ∀x(A→B), B(c) ⊢ A(c) | **SAT** → formal |
| 有效推理（对照） | 如果 P 则 Q，P，所以 Q | Implies(P,Q), P ⊢ Q | **UNSAT** → discard |

---

## 使用示例

### 检测肯定后件谬误

```json
{
  "finding_id": "demo-001",
  "error_type": "affirming_consequent",
  "query_type": "check_inference",
  "variables": [
    {"name": "P", "type": "bool", "meaning": "正在下雨"},
    {"name": "Q", "type": "bool", "meaning": "地面是湿的"}
  ],
  "premises_formulas": ["Implies(P, Q)", "Q"],
  "claimed_conclusion": "P"
}
```

Z3 返回：

```json
{
  "z3_result": "sat",
  "verification_verdict": "formal",
  "model": { "P": "False", "Q": "True" }
}
```

`P=False, Q=True` → 地面湿但不雨 → 推理无效，谬误确认。

### 全流程：Z3 + LLM Judge 双重验证

```
① verify_fol_encoding → SAT + 反例
② build_logic_cp_prompt → 发给 LLM
③ LLM 返回 judgment
④ parse_logic_cp_judgment → valid/invalid
⑤ apply_logic_cp_verdict → 最终 formal/candidate
```

或者一步到位用 `verify_with_logic_cp`。

---

## 项目结构

```
├── pyproject.toml           # 包定义 + 入口点
├── LICENSE                  # MIT
├── README.md                # 本文件
├── data/
│   └── ai_formal_fallacies.json  # 内置 48 条测试数据集
├── tests/
│   └── test_edge_cases.py   # 边界测试（23 项，覆盖个体域/多元谓词/大公式等）
└── src/
    ├── __init__.py
    ├── mcp_server.py        # MCP Server（9 个工具）
    ├── z3_verifier.py       # Z3 定理证明器（FOL 解析 + SMT 查询）
    ├── logic_cp_verifier.py # LLM Judge 反例验证
    └── run_direction_b_eval.py  # 数据集评估引擎
```

---

## 评估

### 内置数据集（48 条）

覆盖全部 7 类标签（6 类谬误 + 有效推理），每条含手写 FOL 编码和金标准 verdict。

```bash
run_direction_b_eval   # 通过 MCP 工具
python src/run_direction_b_eval.py  # 或直接调用
```

预期准确率：100% —— Z3 定理证明，不是统计预测。

### FOLIO 验证集（第三方基准）

[FOLIO](https://github.com/Yale-LILY/FOLIO) 是耶鲁发布的 FOL 推理基准，每条含 NL + FOL 编码 + 金标准标签。

```bash
python test_folio.py --start N --size 10   # 从第 N 条起跑 10 条
```

实测 35 条正确率 **97.1%**（34/35），唯一争议案例经检查为 FOLIO 标注本身可能不准确，Z3 给出了完整的可核查反例模型。

### 边界测试（23 项）

```bash
python tests/test_edge_cases.py
```

覆盖：空数据校验 / 经典推理模式 / 个体域（苏格拉底三段论）/ 多元谓词（一元至三元）/ 嵌套量词 / 大公式深度 10 嵌套 / 12 变量 And 链 / 未声明变量自动创建 / MCP 协议集成。

---

## 依赖

- Python ≥ 3.10
- z3-solver ≥ 4.13
- mcp ≥ 1.9.0, < 2.0

---

## 协议

MIT © 2026 zhujiaozi
