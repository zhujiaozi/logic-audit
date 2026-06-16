# Logic Audit — MCP Server

Z3 定理证明器驱动的形式谬误验证工具，通过 MCP 协议集成到任何 Claude Agent。

## 安装

```bash
pip install -e .
```

依赖：`z3-solver>=4.13`、`mcp>=1.27`。

## 启动 MCP Server

```bash
python src/mcp_server.py
```

启动后通过 **stdio 协议** 与 Claude Agent 通信。

## 8 个 MCP 工具

### 1. 核心验证

| 工具 | 说明 |
|------|------|
| `verify_fol_encoding` | Z3 验证 FOL 编码，返回 sat/unsat + formal/discard/candidate 判定 |
| `batch_verify` | 批量验证多个 FOL 编码 |

### 2. logic_cp 反例校验

| 工具 | 说明 |
|------|------|
| `build_logic_cp_prompt` | 为 LLM Judge 构建反例验证 prompt |
| `parse_logic_cp_judgment` | 解析 LLM Judge 的返回 → structured verdict |
| `apply_logic_cp_verdict` | 合并 Z3 + logic_cp → 最终判定 |

### 3. 评估与参考

| 工具 | 说明 |
|------|------|
| `run_direction_b_eval` | 在 48 条内置数据上运行 Z3 评估（无需 LLM） |
| `get_error_type_info` | 查询 7 类形式谬误的 FOL 编码参考 |
| `parse_fol_formula` | 验证 FOL 公式语法（不跑 Z3） |

## 典型用法

### 在 Claude Code 中注册

在 `.claude/settings.local.json` 中添加：

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

### Z3 验证一条 FOL 编码

```json
{
  "finding_id": "test-001",
  "query_type": "check_inference",
  "variables": [
    {"name": "P", "type": "bool", "meaning": "正在下雨"},
    {"name": "Q", "type": "bool", "meaning": "地面是湿的"}
  ],
  "premises_formulas": ["Implies(P, Q)", "Q"],
  "claimed_conclusion": "P"
}
```

Z3 返回 `SAT` + `formal`（确认的谬误）+ 反例模型 `{P: False, Q: True}`。

### logic_cp 完整流程

```
Z3 返回 SAT（反例）
       │
       ▼
build_logic_cp_prompt → 发给 LLM Judge
       │
       ▼
parse_logic_cp_judgment → 解析为 valid/invalid
       │
       ▼
apply_logic_cp_verdict → 最终: formal（确认）或 candidate（翻译存疑）
```

### 查询错误类型参考

```json
// get_error_type_info
// 返回 7 类形式谬误的 FOL 编码模式
```

## 支持的错误类型

| 类型 | NL 模式 | FOL 模式 | Z3 预期 |
|------|---------|----------|---------|
| affirming_consequent | 如果 P 则 Q，Q，所以 P | Implies(P,Q), Q ⊢ P | SAT |
| denying_antecedent | 如果 P 则 Q，非 P，所以非 Q | Implies(P,Q), ¬P ⊢ ¬Q | SAT |
| non_sequitur | P，所以 Q（无关） | P ⊢ Q | SAT |
| contradiction | 既 P 又 非 P | P, ¬P | UNSAT |
| circular_reasoning | P↔Q，所以 P | Eq(P,Q) ⊢ P | SAT |
| invalid_syllogism | 所有 A 是 B，C 是 B，所以 C 是 A | ∀x(A→B), B(c) ⊢ A(c) | SAT |
| valid_reasoning | 如果 P 则 Q，P，所以 Q | Implies(P,Q), P ⊢ Q | UNSAT |

## 项目结构

```
├── pyproject.toml               # 包定义（pip install -e .）
├── data/
│   └── ai_formal_fallacies.json # 内置 48 条测试数据集
└── src/
    ├── __init__.py               # 包导出
    ├── z3_verifier.py            # Z3 定理证明器（FOL 解析 + SMT 查询）
    ├── logic_cp_verifier.py      # LLM Judge 反例验证（改编自 CLOVER ICLR 2025）
    ├── run_direction_b_eval.py   # 数据集评估引擎
    └── mcp_server.py             # MCP Server（8 个工具）
```
