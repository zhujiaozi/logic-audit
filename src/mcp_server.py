#!/usr/bin/env python3
"""
logic-audit MCP Server

Exposes Z3 formal verification and logic_cp counterexample validation
as MCP tools for integration with Claude Agents.

Usage:
  python src/mcp_server.py
  # Or register in Claude Code settings.json as:
  # "logic-audit-mcp": { "command": "python", "args": ["src/mcp_server.py"] }
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

# ── Logger ─────────────────────────────────────────────────────────────────
logger = logging.getLogger("logic-audit-mcp")
_log_handler = logging.StreamHandler(sys.stderr)
_log_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S"
))
logger.addHandler(_log_handler)
logger.setLevel(logging.INFO)

# ── Ensure project root is on sys.path (works regardless of cwd) ──────────
_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ── Imports ────────────────────────────────────────────────────────────────
from src.z3_verifier import (
    parse_fol_encoding,
    run_verification,
    verify_all,
    parse_formula,
)
from src.logic_cp_verifier import (
    build_counterexample_prompt,
    parse_judgment,
    apply_logic_cp_verdict,
)
from src.run_direction_b_eval import (
    evaluate_case,
    compute_metrics,
    generate_report,
    load_dataset,
)

import mcp.server.stdio
from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.server import NotificationOptions
from mcp.types import TextContent, Tool


# ── Server Instance ─────────────────────────────────────────────────────────
server = Server("logic-audit")


# ═══════════════════════════════════════════════════════════════════════════
# ERROR TYPE REFERENCE
# ═══════════════════════════════════════════════════════════════════════════

ERROR_TYPE_INFO: dict[str, dict[str, Any]] = {
    "affirming_consequent": {
        "description": "If P then Q, Q, therefore P — affirming the consequent",
        "nl_pattern": "If P then Q, Q is true, so P must be true",
        "fol_pattern": "Premises: Implies(P, Q), Q | Conclusion: P",
        "z3_check": "check_inference → SAT expected (counterexample: P=False, Q=True)",
        "query_type": "check_inference",
    },
    "denying_antecedent": {
        "description": "If P then Q, not P, therefore not Q — denying the antecedent",
        "nl_pattern": "If P then Q, P is false, so Q must be false",
        "fol_pattern": "Premises: Implies(P, Q), Not(P) | Conclusion: Not(Q)",
        "z3_check": "check_inference → SAT expected (counterexample: P=False, Q=True)",
        "query_type": "check_inference",
    },
    "non_sequitur": {
        "description": "Conclusion does not follow from premises",
        "nl_pattern": "P, therefore Q (no logical connection)",
        "fol_pattern": "Premises: [P] | Conclusion: Q",
        "z3_check": "check_inference → SAT expected (P true, Q false)",
        "query_type": "check_inference",
    },
    "contradiction": {
        "description": "Asserting both P and not P",
        "nl_pattern": "Both P and not P are claimed to be true",
        "fol_pattern": "Premises: [P, Not(P)]",
        "z3_check": "check_contradiction → UNSAT expected",
        "query_type": "check_contradiction",
    },
    "circular_reasoning": {
        "description": "Premise assumes the conclusion (begging the question)",
        "nl_pattern": "P iff Q, therefore P (or similar circular structure)",
        "fol_pattern": "Premises: [Eq(P, Q)] or [Implies(P, Q), Implies(Q, P)] | Conclusion: P",
        "z3_check": "check_inference → SAT expected",
        "query_type": "check_inference",
    },
    "invalid_syllogism": {
        "description": "Syllogistic form that does not validly entail the conclusion",
        "nl_pattern": "All A are B, C is B, therefore C is A (invalid form)",
        "fol_pattern": "Premises: [Forall(x, Implies(A(x), B(x))), B(c)] | Conclusion: A(c)",
        "z3_check": "check_inference → SAT expected (counterexample: A(c)=False, B(c)=True)",
        "query_type": "check_inference",
    },
    "valid_reasoning": {
        "description": "Logically valid argument (modus ponens, etc.)",
        "nl_pattern": "If P then Q, P, therefore Q (modus ponens — valid)",
        "fol_pattern": "Premises: [Implies(P, Q), P] | Conclusion: Q",
        "z3_check": "check_inference → UNSAT expected (no counterexample)",
        "query_type": "check_inference",
    },
}

# ═══════════════════════════════════════════════════════════════════════════
# TOOL DEFINITIONS
# ═══════════════════════════════════════════════════════════════════════════

@server.list_tools()
async def handle_list_tools() -> list[Tool]:
    return [
        Tool(
            name="verify_fol_encoding",
            description="""Verify a First-Order Logic encoding using Z3 theorem prover.
Given variables, premises, and a claimed conclusion, Z3 checks whether the inference is valid.
- check_inference: asserts premises ∧ ¬conclusion → SAT means fallacy confirmed
- check_contradiction: asserts premises → UNSAT means contradiction detected
Returns z3_result (sat/unsat/unknown/parse_error), verification_verdict (formal/discard/candidate), and counterexample model if SAT.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "finding_id": {"type": "string", "description": "Identifier for this verification"},
                    "error_type": {"type": "string", "description": "Logical error type label (e.g. affirming_consequent, non_sequitur). Used for downstream filtering and reporting."},
                    "query_type": {
                        "type": "string",
                        "enum": ["check_inference", "check_contradiction", "check_equivalence"],
                        "description": "check_inference: verify if conclusion follows from premises. check_contradiction: verify if premises contain a contradiction. check_equivalence: verify if premises are equivalent to conclusion.",
                    },
                    "variables": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "Variable name (e.g., P, Q, A)"},
                                "type": {"type": "string", "enum": ["bool", "function", "constant"], "description": "bool for propositions, function for predicates, constant for named entities"},
                                "meaning": {"type": "string", "description": "Natural language meaning of this variable"},
                            },
                            "required": ["name", "type", "meaning"],
                        },
                        "description": "Variable declarations used in the formulas",
                    },
                    "premises_formulas": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of premise formulas using supported operators: Implies, And, Or, Not, Xor, Eq, Forall, Exists. Example: ['Implies(P, Q)', 'Q']",
                    },
                    "claimed_conclusion": {
                        "type": "string",
                        "description": "The claimed conclusion formula (omit for check_contradiction)",
                    },
                },
                "required": ["finding_id", "query_type", "variables", "premises_formulas"],
            },
        ),
        Tool(
            name="batch_verify",
            description="""Verify multiple FOL encodings in a single call.
Takes a list of FOL encoding objects (same structure as verify_fol_encoding inputs)
and returns all results. More efficient than calling verify_fol_encoding repeatedly.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "encodings": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "finding_id": {"type": "string"},
                                "error_type": {"type": "string", "description": "Logical error type label"},
                                "query_type": {"type": "string", "enum": ["check_inference", "check_contradiction", "check_equivalence"]},
                                "variables": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "name": {"type": "string", "description": "Variable name (e.g., P, Q, A)"},
                                            "type": {"type": "string", "enum": ["bool", "function", "constant"], "description": "bool for propositions, function for predicates, constant for named entities"},
                                            "meaning": {"type": "string", "description": "Natural language meaning of this variable"},
                                        },
                                        "required": ["name", "type", "meaning"],
                                    },
                                    "description": "Variable declarations used in the formulas",
                                },
                                "premises_formulas": {"type": "array", "items": {"type": "string"}},
                                "claimed_conclusion": {"type": "string"},
                            },
                            "required": ["finding_id", "query_type", "variables", "premises_formulas"],
                        },
                    },
                },
                "required": ["encodings"],
            },
        ),
        Tool(
            name="verify_with_logic_cp",
            description="""End-to-end verification: Z3 verification + optional logic_cp counterexample validation in one call.
Accepts the same FOL encoding as verify_fol_encoding, plus:
- original_text: the natural language argument (required for logic_cp)
- llm_judgment: if provided, runs the full pipeline (Z3 → build CP prompt → parse → apply verdict)
  and returns the final verdict with logic_cp results merged.
- If original_text is given but llm_judgment is omitted, returns Z3 result + the CP prompt
  so the caller can invoke an LLM externally, then feed the response back via llm_judgment.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "finding_id": {"type": "string", "description": "Identifier for this verification"},
                    "error_type": {"type": "string", "description": "Logical error type label"},
                    "query_type": {
                        "type": "string",
                        "enum": ["check_inference", "check_contradiction", "check_equivalence"],
                        "description": "Type of Z3 verification query",
                    },
                    "variables": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "Variable name (e.g., P, Q, A)"},
                                "type": {"type": "string", "enum": ["bool", "function", "constant"], "description": "bool for propositions, function for predicates, constant for named entities"},
                                "meaning": {"type": "string", "description": "Natural language meaning of this variable"},
                            },
                            "required": ["name", "type", "meaning"],
                        },
                        "description": "Variable declarations used in the formulas",
                    },
                    "premises_formulas": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of premise formulas using supported operators: Implies, And, Or, Not, Xor, Eq, Forall, Exists.",
                    },
                    "claimed_conclusion": {
                        "type": "string",
                        "description": "The claimed conclusion formula (omit for check_contradiction)",
                    },
                    "original_text": {
                        "type": "string",
                        "description": "The natural language argument being audited (required for logic_cp validation)",
                    },
                    "llm_judgment": {
                        "type": "string",
                        "description": "Optional raw LLM response from a CP prompt. If provided, runs the full pipeline including parse + apply verdict.",
                    },
                },
                "required": ["finding_id", "query_type", "variables", "premises_formulas"],
            },
        ),
        Tool(
            name="build_logic_cp_prompt",
            description="""Build a prompt for an LLM judge to evaluate whether a Z3 counterexample
genuinely disproves the original natural language argument.
Use this when Z3 returns SAT (counterexample found) and you want to verify
the counterexample makes sense in context. The returned prompt should be sent
to an LLM; then pass the response to parse_logic_cp_judgment.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "original_text": {"type": "string", "description": "The original natural language argument being audited"},
                    "variables": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "type": {"type": "string"},
                                "meaning": {"type": "string"},
                            },
                        },
                    },
                    "premises": {"type": "array", "items": {"type": "string"}, "description": "FOL premise formulas"},
                    "conclusion": {"type": "string", "description": "The claimed conclusion formula"},
                    "model": {
                        "type": "object",
                        "description": "The Z3 counterexample model (variable → value mapping)",
                        "additionalProperties": {"type": "string"},
                    },
                },
                "required": ["original_text", "variables", "premises", "conclusion", "model"],
            },
        ),
        Tool(
            name="parse_logic_cp_judgment",
            description="""Parse an LLM judge's response from a logic_cp prompt.
Returns structured verdict: valid (counterexample confirmed), invalid (translation error suspected),
or unknown (ambiguous response).""",
            inputSchema={
                "type": "object",
                "properties": {
                    "response": {"type": "string", "description": "Raw LLM response text to parse"},
                },
                "required": ["response"],
            },
        ),
        Tool(
            name="apply_logic_cp_verdict",
            description="""Apply a logic_cp verdict to modify a Z3 verification result.
If Z3 said SAT but LLM judge says INVALID counterexample → downgrade from formal to candidate.
If Z3 said SAT and LLM judge says VALID → keep as formal (high confidence).
If Z3 said UNSAT → no change (logic_cp doesn't apply).""",
            inputSchema={
                "type": "object",
                "properties": {
                    "z3_result": {
                        "type": "object",
                        "description": "The full result dict from verify_fol_encoding",
                    },
                    "cp_verdict": {
                        "type": "string",
                        "enum": ["valid", "invalid", "unknown"],
                        "description": "The verdict from parse_logic_cp_judgment",
                    },
                },
                "required": ["z3_result", "cp_verdict"],
            },
        ),
        Tool(
            name="run_direction_b_eval",
            description="""Run Direction B evaluation on the built-in AI formal fallacies dataset (48 cases).
Loads hand-written test cases with ground-truth FOL encodings, runs Z3 verification,
and returns per-case results and aggregate metrics. No LLM calls needed — purely Z3.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset_path": {
                        "type": "string",
                        "description": "Optional custom dataset path (defaults to data/ai_formal_fallacies.json)",
                    },
                },
            },
        ),
        Tool(
            name="get_error_type_info",
            description="""Get reference information about supported logical error types.
Returns FOL encoding patterns, Z3 query types, and expected results for each error type.
Use this to understand how to structure FOL encodings for verify_fol_encoding.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "error_type": {
                        "type": "string",
                        "description": "Optional specific error type (omit to list all)",
                    },
                },
            },
        ),
        Tool(
            name="parse_fol_formula",
            description="""Parse and validate a FOL formula string without running Z3 verification.
Useful for checking formula syntax before submitting to verify_fol_encoding.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "formula": {"type": "string", "description": "Formula string to parse, e.g. 'Implies(P, Q)'"},
                    "variables": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "type": {"type": "string", "enum": ["bool", "function"]},
                            },
                        },
                        "description": "Available variables",
                    },
                },
                "required": ["formula", "variables"],
            },
        ),
    ]


# ═══════════════════════════════════════════════════════════════════════════
# TOOL HANDLERS
# ═══════════════════════════════════════════════════════════════════════════

@server.call_tool()
async def handle_call_tool(name: str, arguments: dict) -> list[TextContent]:
    finding_id = arguments.get("finding_id", arguments.get("error_type", "?"))
    logger.info("Tool call: %s | id=%s", name, finding_id)
    try:
        if name == "verify_fol_encoding":
            return await _handle_verify_fol(arguments)
        elif name == "batch_verify":
            count = len(arguments.get("encodings", []))
            logger.info("  batch size: %d", count)
            return await _handle_batch_verify(arguments)
        elif name == "verify_with_logic_cp":
            return await _handle_verify_with_logic_cp(arguments)
        elif name == "build_logic_cp_prompt":
            return await _handle_build_cp_prompt(arguments)
        elif name == "parse_logic_cp_judgment":
            return await _handle_parse_judgment(arguments)
        elif name == "apply_logic_cp_verdict":
            return await _handle_apply_verdict(arguments)
        elif name == "run_direction_b_eval":
            return await _handle_run_eval(arguments)
        elif name == "get_error_type_info":
            return await _handle_error_type_info(arguments)
        elif name == "parse_fol_formula":
            return await _handle_parse_formula(arguments)
        else:
            logger.warning("Unknown tool requested: %s", name)
            return [TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]
    except Exception as e:
        logger.error("Tool %s failed: %s", name, str(e), exc_info=True)
        return [TextContent(type="text", text=json.dumps({"error": str(e)}, ensure_ascii=False))]


# ── Tool Implementations ──────────────────────────────────────────────────


async def _handle_verify_fol(args: dict) -> list[TextContent]:
    finding_id = args.get("finding_id", "unknown")
    query_type = args.get("query_type", "check_inference")
    variables = args.get("variables", [])
    premises = args.get("premises_formulas", [])
    conclusion = args.get("claimed_conclusion", "")

    fol_input = {
        "finding_id": finding_id,
        "error_type": args.get("error_type", "unknown"),
        "query_type": query_type,
        "variables": variables,
        "premises_formulas": premises,
        "claimed_conclusion": conclusion,
    }

    parsed = parse_fol_encoding(fol_input)
    result = run_verification(parsed)

    return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]


async def _handle_batch_verify(args: dict) -> list[TextContent]:
    encodings = args.get("encodings", [])
    results = verify_all(encodings)
    return [TextContent(type="text", text=json.dumps(results, indent=2, ensure_ascii=False))]


async def _handle_verify_with_logic_cp(args: dict) -> list[TextContent]:
    """End-to-end: Z3 verify → optionally build CP prompt → optionally parse + apply verdict."""
    finding_id = args.get("finding_id", "unknown")
    query_type = args.get("query_type", "check_inference")
    variables = args.get("variables", [])
    premises = args.get("premises_formulas", [])
    conclusion = args.get("claimed_conclusion", "")
    original_text = args.get("original_text", "")
    llm_judgment = args.get("llm_judgment")

    # Step 1: Z3 verification
    fol_input = {
        "finding_id": finding_id,
        "error_type": args.get("error_type", "unknown"),
        "query_type": query_type,
        "variables": variables,
        "premises_formulas": premises,
        "claimed_conclusion": conclusion,
    }
    parsed = parse_fol_encoding(fol_input)
    z3_result = run_verification(parsed)

    # If Z3 didn't find SAT, no CP needed
    if z3_result.get("z3_result") != "sat":
        return [TextContent(type="text", text=json.dumps({
            "finding_id": finding_id,
            "z3_result": z3_result,
            "logic_cp_status": "not_applicable",
        }, indent=2, ensure_ascii=False))]

    # Step 2: Build CP prompt if original_text is provided
    cp_prompt = None
    if original_text and z3_result.get("model"):
        cp_prompt = build_counterexample_prompt(
            original_text=original_text,
            premises=premises,
            conclusion=conclusion,
            variables=variables,
            model_dict=z3_result.get("model", {}),
        )

    # Step 3: If LLM judgment is provided, parse and apply it
    if llm_judgment and cp_prompt:
        parsed_judgment = parse_judgment(llm_judgment)
        final_result = apply_logic_cp_verdict(z3_result, parsed_judgment["verdict"])
        return [TextContent(type="text", text=json.dumps({
            "finding_id": finding_id,
            "z3_result": z3_result,
            "logic_cp_status": "completed",
            "cp_verdict": parsed_judgment["verdict"],
            "cp_reasoning": parsed_judgment.get("reasoning", ""),
            "final_verdict": final_result,
        }, indent=2, ensure_ascii=False))]

    # Return Z3 result + CP prompt for caller to complete the pipeline
    return [TextContent(type="text", text=json.dumps({
        "finding_id": finding_id,
        "z3_result": z3_result,
        "logic_cp_status": "prompt_ready",
        "cp_prompt": cp_prompt,
        "prompt": "Send this cp_prompt to an LLM, then call verify_with_logic_cp again with the llm_judgment parameter set to the response.",
    }, indent=2, ensure_ascii=False))]


async def _handle_build_cp_prompt(args: dict) -> list[TextContent]:
    original_text = args["original_text"]
    variables = args.get("variables", [])
    premises = args.get("premises", [])
    conclusion = args.get("conclusion", "")
    model = args.get("model", {})

    prompt = build_counterexample_prompt(
        original_text=original_text,
        premises=premises,
        conclusion=conclusion,
        variables=variables,
        model_dict=model,
    )

    return [TextContent(type="text", text=prompt)]


async def _handle_parse_judgment(args: dict) -> list[TextContent]:
    response_text = args.get("response", "")
    result = parse_judgment(response_text)
    return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]


async def _handle_apply_verdict(args: dict) -> list[TextContent]:
    z3_result = args.get("z3_result", {})
    cp_verdict = args.get("cp_verdict", "unknown")
    result = apply_logic_cp_verdict(z3_result, cp_verdict)
    return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]


async def _handle_run_eval(args: dict) -> list[TextContent]:
    dataset_path = args.get("dataset_path")
    base = _PROJECT_ROOT

    if dataset_path:
        path = Path(dataset_path)
        if not path.is_absolute():
            path = base / path
    else:
        path = base / "data" / "ai_formal_fallacies.json"

    if not path.exists():
        return [TextContent(
            type="text",
            text=json.dumps({"error": f"Dataset not found: {path}"}, ensure_ascii=False),
        )]

    data = load_dataset(str(path))
    meta = data.get("meta", {})
    cases = data.get("cases", [])

    results = [evaluate_case(c) for c in cases]
    metrics = compute_metrics(results)

    report = generate_report(results, metrics, meta)

    summary = {
        "total_cases": len(results),
        "correct_verdicts": sum(1 for r in results if r["verdict_correct"]),
        "accuracy": round(sum(1 for r in results if r["verdict_correct"]) / len(results) * 100, 1) if results else 0,
        "metrics": {k: v for k, v in metrics.items() if isinstance(v, dict)},
        "per_case": [
            {
                "id": r["id"],
                "error_type": r["error_type"],
                "z3_result": r["z3_result"],
                "expected_verdict": r["expected_verdict"],
                "actual_verdict": r["actual_verdict"],
                "correct": r["verdict_correct"],
            }
            for r in results
        ],
        "report_markdown": report,
    }

    return [TextContent(type="text", text=json.dumps(summary, indent=2, ensure_ascii=False))]


async def _handle_error_type_info(args: dict) -> list[TextContent]:
    error_type = args.get("error_type")

    if error_type:
        info = ERROR_TYPE_INFO.get(error_type)
        if not info:
            available = list(ERROR_TYPE_INFO.keys())
            return [TextContent(
                type="text",
                text=json.dumps({
                    "error": f"Unknown error type: {error_type}",
                    "available_types": available,
                }, indent=2, ensure_ascii=False),
            )]
        result = {"error_type": error_type, **info}
    else:
        result = {
            "description": "Supported FOL-verifiable logical error types",
            "types": ERROR_TYPE_INFO,
        }

    return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]


async def _handle_parse_formula(args: dict) -> list[TextContent]:
    formula_str = args.get("formula", "")
    variables = args.get("variables", [])

    # Reuse parse_fol_encoding to build the var_map (avoids duplicating Z3 type mapping)
    fol_input = {
        "finding_id": "parse-only",
        "error_type": "unknown",
        "query_type": "check_inference",
        "variables": variables,
        "premises_formulas": ["True"],
        "claimed_conclusion": "True",
    }
    parsed = parse_fol_encoding(fol_input)
    if not parsed["success"]:
        return [TextContent(type="text", text=json.dumps(
            {"success": False, "error": parsed.get("error", "variable parsing failed")},
            indent=2, ensure_ascii=False,
        ))]

    expr, error = parse_formula(formula_str, parsed["var_map"])
    if error:
        result = {"success": False, "error": error}
    else:
        result = {"success": True, "z3_expr_repr": str(expr)}

    return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]


# ═══════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════

async def _run_server() -> None:
    logger.info("Starting logic-audit MCP server (v1.0.0)")
    async with mcp.server.stdio.stdio_server() as (read, write):
        logger.info("MCP stdio transport established")
        await server.run(
            read,
            write,
            InitializationOptions(
                server_name="logic-audit",
                server_version="1.0.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities=None,
                ),
            ),
        )


def main() -> None:
    import asyncio
    asyncio.run(_run_server())


if __name__ == "__main__":
    main()
