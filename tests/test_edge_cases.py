#!/usr/bin/env python3
"""
边界测试：覆盖 Z3 verifier 的空数据、大公式、多变量、多元谓词、
嵌套量词、重言式、矛盾式等场景。
"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.z3_verifier import parse_fol_encoding, run_verification, parse_formula, Individual

# ── 测试计数 ──────────────────────────────────────────────────────────────
passed = 0
failed = 0

def test(name, fn):
    global passed, failed
    try:
        fn()
        passed += 1
    except Exception as e:
        failed += 1
        print(f"  ✗ {name}: {e}")

def check(name, condition):
    global passed, failed
    if condition:
        passed += 1
    else:
        failed += 1
        print(f"  ✗ {name}")

# ── 辅助 ──────────────────────────────────────────────────────────────────
def make_input(**kw):
    defaults = dict(finding_id="test", error_type="test", query_type="check_inference",
                    variables=[], premises_formulas=[], claimed_conclusion="")
    defaults.update(kw)
    return defaults

def run(mcp):
    parsed = parse_fol_encoding(mcp)
    assert parsed["success"], f"parse_fol_encoding failed: {parsed.get('error')}"
    return run_verification(parsed)

def sat(mcp):
    return run(mcp)["z3_result"] == "sat"

def unsat(mcp):
    return run(mcp)["z3_result"] == "unsat"

# ══════════════════════════════════════════════════════════════════════════
# 1. 空数据 / 边界值
# ══════════════════════════════════════════════════════════════════════════
print("\n=== 1. 空数据 / 边界值 ===")

def test_empty_premises():
    mcp = make_input(premises_formulas=[], claimed_conclusion="P",
                     variables=[{"name":"P","type":"bool","meaning":"P"}])
    assert not parse_fol_encoding(mcp)["success"], "空 premises 应失败"
test("空 premises 报错", test_empty_premises)

def test_no_conclusion_for_inference():
    mcp = make_input(premises_formulas=["P"], claimed_conclusion="",
                     variables=[{"name":"P","type":"bool","meaning":"P"}])
    assert not parse_fol_encoding(mcp)["success"], "check_inference 无 conclusion 应失败"
test("无 conclusion 报错", test_no_conclusion_for_inference)

def test_contradiction_no_conclusion():
    mcp = make_input(query_type="check_contradiction",
                     premises_formulas=["P", "Not(P)"],
                     variables=[{"name":"P","type":"bool","meaning":"P"}])
    assert parse_fol_encoding(mcp)["success"], "contradiction 不需要 conclusion"
    assert unsat(mcp), "P, Not(P) 应为 UNSAT"
test("矛盾检测无需 conclusion", test_contradiction_no_conclusion)

def test_invalid_query_type():
    mcp = make_input(query_type="check_magic",
                     premises_formulas=["P"],
                     variables=[{"name":"P","type":"bool","meaning":"P"}])
    assert not parse_fol_encoding(mcp)["success"], "非法 query_type 应报错"
test("非法 query_type 报错", test_invalid_query_type)

# ══════════════════════════════════════════════════════════════════════════
# 2. 经典推理模式
# ══════════════════════════════════════════════════════════════════════════
print("\n=== 2. 经典推理模式 ===")

def test_modus_ponens():
    mcp = make_input(
        premises_formulas=["Implies(P, Q)", "P"],
        claimed_conclusion="Q",
        variables=[{"name":"P","type":"bool","meaning":"P"},{"name":"Q","type":"bool","meaning":"Q"}])
    assert unsat(mcp), "Modus ponens 应为 UNSAT"
test("Modus ponens", test_modus_ponens)

def test_modus_tollens():
    mcp = make_input(
        premises_formulas=["Implies(P, Q)", "Not(Q)"],
        claimed_conclusion="Not(P)",
        variables=[{"name":"P","type":"bool","meaning":"P"},{"name":"Q","type":"bool","meaning":"Q"}])
    assert unsat(mcp), "Modus tollens 应为 UNSAT"
test("Modus tollens", test_modus_tollens)

def test_affirming_consequent():
    mcp = make_input(
        premises_formulas=["Implies(P, Q)", "Q"],
        claimed_conclusion="P",
        variables=[{"name":"P","type":"bool","meaning":"P"},{"name":"Q","type":"bool","meaning":"Q"}])
    assert sat(mcp), "肯定后件 应为 SAT"
test("肯定后件 (谬误)", test_affirming_consequent)

def test_tautology():
    mcp = make_input(
        premises_formulas=["P"],
        claimed_conclusion="P",
        variables=[{"name":"P","type":"bool","meaning":"P"}])
    assert unsat(mcp), "P ⊢ P 应为 UNSAT"
test("重言式", test_tautology)

# ══════════════════════════════════════════════════════════════════════════
# 3. 个体域 (Individual sort)
# ══════════════════════════════════════════════════════════════════════════
print("\n=== 3. 个体域 ===")

def test_socrates_syllogism():
    mcp = make_input(
        premises_formulas=["Forall(x, Implies(Man(x), Mortal(x)))", "Man(Socrates)"],
        claimed_conclusion="Mortal(Socrates)",
        variables=[
            {"name":"Man","type":"function","meaning":"Is a man"},
            {"name":"Mortal","type":"function","meaning":"Is mortal"},
            {"name":"Socrates","type":"constant","meaning":"Socrates"},
        ])
    assert unsat(mcp), "苏格拉底三段论 应为 UNSAT"
test("苏格拉底三段论", test_socrates_syllogism)

def test_individual_not_false():
    """个体常量不应被赋 False（之前 BoolSort 的 bug）"""
    mcp = make_input(
        premises_formulas=["P(james)"],
        claimed_conclusion="P(james)",
        variables=[
            {"name":"P","type":"function","meaning":"Predicate"},
            {"name":"james","type":"constant","meaning":"James"},
        ])
    result = run(mcp)
    assert result["z3_result"] == "unsat", "P(james) ⊢ P(james) 应为 UNSAT"
    if result.get("model"):
        # james 不应出现在 model 中（个体常量不赋值）
        assert "james" not in result["model"] or result["model"]["james"] != "False", \
            "james 不应为 False"
test("个体常量不被赋 False", test_individual_not_false)

# ══════════════════════════════════════════════════════════════════════════
# 4. 多元谓词
# ══════════════════════════════════════════════════════════════════════════
print("\n=== 4. 多元谓词 ===")

def test_binary_predicate():
    mcp = make_input(
        premises_formulas=["ParentOf(john, mary)"],
        claimed_conclusion="ParentOf(john, mary)",
        variables=[
            {"name":"ParentOf","type":"function","meaning":"Is parent of"},
            {"name":"john","type":"constant","meaning":"John"},
            {"name":"mary","type":"constant","meaning":"Mary"},
        ])
    assert unsat(mcp), "二元谓词恒真 应为 UNSAT"
test("二元谓词", test_binary_predicate)

def test_ternary_predicate():
    mcp = make_input(
        premises_formulas=["Between(a, b, c)"],
        claimed_conclusion="And(Between(a, b, c), True)",
        variables=[
            {"name":"Between","type":"function","meaning":"Is between"},
            {"name":"a","type":"constant","meaning":"a"},
            {"name":"b","type":"constant","meaning":"b"},
            {"name":"c","type":"constant","meaning":"c"},
        ])
    assert unsat(mcp), "三元谓词恒真 应为 UNSAT"
test("三元谓词", test_ternary_predicate)

def test_mixed_arity_predicates():
    mcp = make_input(
        premises_formulas=[
            "Forall(x, Forall(y, Implies(ParentOf(x,y), Human(x))))",
            "ParentOf(adam, eve)",
        ],
        claimed_conclusion="Human(adam)",
        variables=[
            {"name":"ParentOf","type":"function","meaning":"二元谓词"},
            {"name":"Human","type":"function","meaning":"一元谓词"},
            {"name":"adam","type":"constant","meaning":"Adam"},
            {"name":"eve","type":"constant","meaning":"Eve"},
        ])
    assert unsat(mcp), "混合元数推理 应为 UNSAT"
test("混合元数谓词", test_mixed_arity_predicates)

# ══════════════════════════════════════════════════════════════════════════
# 5. 嵌套量词
# ══════════════════════════════════════════════════════════════════════════
print("\n=== 5. 嵌套量词 ===")

def test_double_quantifier():
    mcp = make_input(
        premises_formulas=["Forall(x, Forall(y, Implies(And(P(x), Q(y)), R(x,y))))",
                           "Forall(x, Implies(S(x), P(x)))", "S(a)", "Q(b)"],
        claimed_conclusion="R(a, b)",
        variables=[
            {"name":"P","type":"function","meaning":"P"},{"name":"Q","type":"function","meaning":"Q"},
            {"name":"R","type":"function","meaning":"R"},{"name":"S","type":"function","meaning":"S"},
            {"name":"a","type":"constant","meaning":"a"},{"name":"b","type":"constant","meaning":"b"},
        ])
    assert unsat(mcp), "嵌套全称量词 应为 UNSAT"
test("嵌套全称量词", test_double_quantifier)

def test_exists_quantifier():
    mcp = make_input(
        premises_formulas=["Exists(x, P(x))"],
        claimed_conclusion="Not(Forall(x, Not(P(x))))",
        variables=[{"name":"P","type":"function","meaning":"P"}])
    assert unsat(mcp), "∃x P(x) ⊢ ¬∀x ¬P(x) 应为 UNSAT"
test("存在量词换位", test_exists_quantifier)

# ══════════════════════════════════════════════════════════════════════════
# 6. 大/复杂公式
# ══════════════════════════════════════════════════════════════════════════
print("\n=== 6. 大/复杂公式 ===")

def test_large_formula():
    """构造一个嵌套深度 10 的公式——确保解析不崩, Z3 返回有效结果"""
    # Implies(P, Implies(P, ... Implies(P, P)...)) is a tautology (≡ True)
    f = "P"
    for _ in range(10):
        f = f"Implies(P, {f})"
    parsed = parse_fol_encoding(make_input(
        premises_formulas=[f],
        claimed_conclusion="P",
        variables=[{"name":"P","type":"bool","meaning":"P"}]))
    assert parsed["success"], f"大公式解析失败: {parsed.get('error')}"
    result = run_verification(parsed)
    # Formula is a tautology (True), but True does not entail P, so Z3 returns SAT.
    # The test's purpose is to confirm no crash/overflow, not logical correctness.
    assert result["z3_result"] in ("sat", "unsat", "unknown"), f"意外结果: {result['z3_result']}"
test("深度 10 嵌套公式不崩", test_large_formula)

def test_many_variables():
    """12 个布尔变量, 长 And 链"""
    names = [chr(ord('A')+i) for i in range(12)]
    vars_list = [{"name":n,"type":"bool","meaning":n} for n in names]
    # Chain And(A, B, C, ..., L) verified: if it's True, A must be True
    formulas = names[1:]  # B..L
    and_chain = names[0]
    for f in formulas:
        and_chain = f"And({and_chain}, {f})"
    mcp = make_input(
        premises_formulas=[and_chain],
        claimed_conclusion="A",
        variables=vars_list)
    assert unsat(mcp), f"12 变量 And 链应为 UNSAT"
test("12 个布尔变量", test_many_variables)

def test_many_quantified_predicates():
    """6 个一元量词谓词"""
    names = [f"P{i}" for i in range(6)]
    vars_list = [{"name":n,"type":"function","meaning":n} for n in names]
    premises = [f"Forall(x, Implies(P{i}(x), P{i+1}(x)))" for i in range(5)]
    premises.append("P0(a)")
    mcp = make_input(
        premises_formulas=premises,
        claimed_conclusion="P5(a)",
        variables=vars_list + [{"name":"a","type":"constant","meaning":"a"}])
    assert unsat(mcp), "6 链谓词 应为 UNSAT"
test("6 链量词谓词", test_many_quantified_predicates)

# ══════════════════════════════════════════════════════════════════════════
# 7. 自动创建 (Fallback)
# ══════════════════════════════════════════════════════════════════════════
print("\n=== 7. 自动创建 (fallback) ===")

def test_undeclared_predicate():
    """不声明 P 函数，让解析器自动创建"""
    mcp = make_input(
        premises_formulas=["P(a)"],
        claimed_conclusion="P(a)",
        variables=[{"name":"a","type":"constant","meaning":"a"}])
    # P 不在变量列表中，但解析器应自动创建
    assert unsat(mcp), "未声明的谓词自动创建 应为 UNSAT"
test("未声明的谓词自动创建", test_undeclared_predicate)

def test_undeclared_constant():
    """不声明 a 常量，让解析器自动创建"""
    mcp = make_input(
        premises_formulas=["P(a)"],
        claimed_conclusion="P(a)",
        variables=[{"name":"P","type":"function","meaning":"P"}])
    assert unsat(mcp), "未声明的常量自动创建 应为 UNSAT"
test("未声明的常量自动创建", test_undeclared_constant)

# ══════════════════════════════════════════════════════════════════════════
# 8. MCP 协议集成
# ══════════════════════════════════════════════════════════════════════════
print("\n=== 8. MCP 协议集成 ===")

def test_tool_list_has_new_tools():
    from src.mcp_server import handle_list_tools
    import asyncio
    tools = asyncio.run(handle_list_tools())
    names = [t.name for t in tools]
    assert "verify_with_logic_cp" in names, f"缺少 verify_with_logic_cp: {names}"
    assert "verify_fol_encoding" in names
    assert len(names) == 9, f"应有 9 个工具, 有 {len(names)}"
test("工具列表完整性", test_tool_list_has_new_tools)

def test_verify_with_logic_cp_sat_path():
    from src.mcp_server import _handle_verify_with_logic_cp
    import json, asyncio
    result = asyncio.run(_handle_verify_with_logic_cp({
        "finding_id":"edge-cp","query_type":"check_inference","error_type":"affirming_consequent",
        "variables":[{"name":"P","type":"bool","meaning":"P"},{"name":"Q","type":"bool","meaning":"Q"}],
        "premises_formulas":["Implies(P, Q)", "Q"],"claimed_conclusion":"P",
        "original_text":"If P then Q, Q, so P.",
    }))
    data = json.loads(result[0].text)
    assert data["logic_cp_status"] == "prompt_ready", f"expected prompt_ready, got {data['logic_cp_status']}"
    assert data["z3_result"]["z3_result"] == "sat"
    assert "cp_prompt" in data
test("verify_with_logic_cp SAT 路径", test_verify_with_logic_cp_sat_path)

def test_verify_with_logic_cp_full():
    from src.mcp_server import _handle_verify_with_logic_cp
    import json, asyncio
    result = asyncio.run(_handle_verify_with_logic_cp({
        "finding_id":"edge-cp-full","query_type":"check_inference","error_type":"affirming_consequent",
        "variables":[{"name":"P","type":"bool","meaning":"P"},{"name":"Q","type":"bool","meaning":"Q"}],
        "premises_formulas":["Implies(P, Q)", "Q"],"claimed_conclusion":"P",
        "original_text":"If P then Q, Q, so P.",
        "llm_judgment":"This is invalid. VALID_COUNTEREXAMPLE",
    }))
    data = json.loads(result[0].text)
    assert data["logic_cp_status"] == "completed"
    assert data["cp_verdict"] == "valid"
test("verify_with_logic_cp 全流程", test_verify_with_logic_cp_full)

# ══════════════════════════════════════════════════════════════════════════
# 报告
# ══════════════════════════════════════════════════════════════════════════
print(f"\n{'='*50}")
print(f"  {passed} passed, {failed} failed")
if failed:
    print("  ❌ SOME TESTS FAILED")
    sys.exit(1)
else:
    print("  ✅ ALL EDGE CASES PASSED")
