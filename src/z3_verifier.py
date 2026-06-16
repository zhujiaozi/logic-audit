#!/usr/bin/env python3
"""
Z3 Verifier for Direction B — formal verification of logical reasoning in AI responses.

Pipeline:
  1. Parse FOL encoding (JSON) from LLM FOL Translator
  2. Build Z3 expressions from formula strings
  3. Run verification query (check_inference / check_contradiction / check_equivalence)
  4. Map Z3 result (sat/unsat/unknown) → formal/candidate/discard verdict
"""

import json
import sys
from enum import Enum
from pathlib import Path
from typing import Optional
from z3 import (
    Bool, BoolVal, And, Or, Not, Implies, Xor,
    Solver, sat, unsat, unknown,
    Function, IntSort, BoolSort, RealSort,
    ForAll, Exists, Const, DeclareSort,
)

# ─── Sorts ─────────────────────────────────────────────────────────────────
# Individual sort for FOL individual constants (persons, objects, etc.)
# Predicates are functions from Individual → Bool
# Quantified variables range over Individual
Individual = DeclareSort("Individual")

# Sentinel for function-type variables — arity is unknown until formula parsing
_FUNCTION_SENTINEL = object()


# ─── Verdict Types ────────────────────────────────────────────────────────────

class VerificationVerdict(Enum):
    FALLACY_CONFIRMED = "formal"
    NO_FALLACY = "discard"
    UNCERTAIN = "candidate"


# ─── Formula Parser ───────────────────────────────────────────────────────────

def _tokenize(s: str):
    """Tokenize a formula string like 'Implies(P, Q)' or 'And(Not(P), Q)'."""
    tokens = []
    i = 0
    while i < len(s):
        c = s[i]
        if c.isspace():
            i += 1
            continue
        if c in '(),':
            tokens.append(c)
            i += 1
            continue
        # Read an identifier
        j = i
        while j < len(s) and (s[j].isalnum() or s[j] == '_'):
            j += 1
        tokens.append(s[i:j])
        i = j
    return tokens


def _build_from_tokens(tokens: list, var_map: dict, idx: int, local_vars: dict = None):
    """
    Recursive descent parser for formula language.

    Supported forms:
      - Bool constant: True, False
      - Variable: P, Q, A (must be in var_map)
      - Unary: Not(P)
      - Binary: And(P, Q), Or(P, Q), Implies(P, Q), Xor(P, Q), Eq(P, Q)
      - Quantifier: Forall(x, formula), Exists(x, formula)
      - Predicate: P(x), Q(y) etc. — where P is an uninterpreted function

    local_vars: dict of locally-scoped variables (e.g. quantified variables)
    """
    # Merge local_vars into var_map lookup for this call only
    scope = dict(var_map)
    if local_vars:
        scope.update(local_vars)
    if idx >= len(tokens):
        raise ValueError(f"Unexpected end of formula")

    token = tokens[idx]

    # Constants
    if token == 'True':
        return BoolVal(True), idx + 1
    if token == 'False':
        return BoolVal(False), idx + 1

    # Unary operators
    if token == 'Not':
        if idx + 1 >= len(tokens) or tokens[idx + 1] != '(':
            raise ValueError(f"Expected '(' after Not at position {idx}")
        arg, next_idx = _build_from_tokens(tokens, var_map, idx + 2, local_vars)
        if next_idx >= len(tokens) or tokens[next_idx] != ')':
            raise ValueError(f"Expected ')' after Not argument at position {next_idx}")
        return Not(arg), next_idx + 1

    # Binary operators
    if token in ('And', 'Or', 'Implies', 'Xor', 'Eq'):
        op = token
        if idx + 1 >= len(tokens) or tokens[idx + 1] != '(':
            raise ValueError(f"Expected '(' after {op} at position {idx}")
        left, next_idx = _build_from_tokens(tokens, var_map, idx + 2, local_vars)
        if next_idx >= len(tokens) or tokens[next_idx] != ',':
            raise ValueError(f"Expected ',' after {op} left arg at position {next_idx}")
        right, next_idx = _build_from_tokens(tokens, var_map, next_idx + 1, local_vars)
        if next_idx >= len(tokens) or tokens[next_idx] != ')':
            raise ValueError(f"Expected ')' after {op} right arg at position {next_idx}")

        if op == 'And':
            return And(left, right), next_idx + 1
        elif op == 'Or':
            return Or(left, right), next_idx + 1
        elif op == 'Implies':
            return Implies(left, right), next_idx + 1
        elif op == 'Xor':
            return Xor(left, right), next_idx + 1
        elif op == 'Eq':
            return (left == right), next_idx + 1

    # Quantifiers
    if token == 'Forall':
        if idx + 1 >= len(tokens) or tokens[idx + 1] != '(':
            raise ValueError("Expected '(' after Forall")
        var_name = tokens[idx + 2]
        if idx + 3 >= len(tokens) or tokens[idx + 3] != ',':
            raise ValueError("Expected ',' after Forall variable")
        # Create quantified variable over Individual sort
        qvar = Const(var_name, Individual)
        inner_scope = dict(local_vars or {})
        inner_scope[var_name] = qvar
        body, next_idx = _build_from_tokens(tokens, var_map, idx + 4, inner_scope)
        if next_idx >= len(tokens) or tokens[next_idx] != ')':
            raise ValueError("Expected ')' after Forall body")
        return ForAll([qvar], body), next_idx + 1

    if token == 'Exists':
        if idx + 1 >= len(tokens) or tokens[idx + 1] != '(':
            raise ValueError("Expected '(' after Exists")
        var_name = tokens[idx + 2]
        if idx + 3 >= len(tokens) or tokens[idx + 3] != ',':
            raise ValueError("Expected ',' after Exists variable")
        qvar = Const(var_name, Individual)
        inner_scope = dict(local_vars or {})
        inner_scope[var_name] = qvar
        body, next_idx = _build_from_tokens(tokens, var_map, idx + 4, inner_scope)
        if next_idx >= len(tokens) or tokens[next_idx] != ')':
            raise ValueError("Expected ')' after Exists body")
        return Exists([qvar], body), next_idx + 1

    # Predicate application: P(x) or P(x, y) or P(x, y, z) etc.
    if idx + 1 < len(tokens) and tokens[idx + 1] == '(':
        func_name = token
        idx += 2  # skip past (
        # Parse all arguments separated by commas
        args = []
        while idx < len(tokens) and tokens[idx] != ')':
            arg, idx = _build_from_tokens(tokens, var_map, idx, local_vars)
            args.append(arg)
            if idx < len(tokens) and tokens[idx] == ',':
                idx += 1
        if idx >= len(tokens) or tokens[idx] != ')':
            raise ValueError(f"Expected ')' after predicate {func_name} arguments")
        idx += 1  # skip )
        # Create or retrieve the function with correct arity
        if func_name not in scope or scope[func_name] is _FUNCTION_SENTINEL:
            # Build domain sorts: n × Individual → Bool
            domain_sorts = [Individual] * len(args)
            scope[func_name] = Function(func_name, *domain_sorts, BoolSort())
        func = scope[func_name]
        return func(*args), idx

    # Variable reference (check scope (local_vars) first, then var_map)
    if token in scope:
        return scope[token], idx + 1

    # Unknown identifier: auto-create as an Individual constant
    # This makes the parser more robust for natural language names like 'whale', 'socrates', etc.
    new_const = Const(token, Individual)
    scope[token] = new_const
    return new_const, idx + 1


def parse_formula(formula_str: str, var_map: dict):
    """Parse a formula string into a Z3 expression. Returns (expr, error_message)."""
    try:
        tokens = _tokenize(formula_str)
        if not tokens:
            return None, "empty formula"
        expr, next_idx = _build_from_tokens(tokens, var_map, 0)
        if next_idx < len(tokens):
            return None, f"unexpected trailing tokens: {tokens[next_idx:]}"
        return expr, None
    except Exception as e:
        return None, str(e)


# ─── FOL Encoding Parser ──────────────────────────────────────────────────────

def parse_fol_encoding(fol_json: dict) -> dict:
    """
    Parse and validate a FOL encoding JSON object.
    Returns a dict with 'success' flag and either parsed data or error.
    """
    try:
        finding_id = fol_json.get("finding_id", "unknown")
        error_type = fol_json.get("error_type", "unknown")
        query_type = fol_json.get("query_type", "check_inference")
        variables = fol_json.get("variables", [])
        premises_formulas = fol_json.get("premises_formulas", [])
        claimed_conclusion = fol_json.get("claimed_conclusion", "")

        # Validate required fields
        if not isinstance(premises_formulas, list) or len(premises_formulas) == 0:
            return {
                "success": False, "finding_id": finding_id,
                "error": "premises_formulas must be a non-empty array"
            }
        if not claimed_conclusion and query_type != "check_contradiction":
            return {
                "success": False, "finding_id": finding_id,
                "error": "claimed_conclusion required for query_type != check_contradiction"
            }
        if query_type not in ("check_inference", "check_contradiction", "check_equivalence"):
            return {
                "success": False, "finding_id": finding_id,
                "error": f"unknown query_type: {query_type}"
            }

        # Build variable map
        var_map = {}
        for v in variables:
            name = v["name"]
            vtype = v.get("type", "bool")
            if vtype == "bool":
                var_map[name] = Bool(name)
            elif vtype == "function":
                # Don't pre-create Z3 functions — arity is unknown until formula parsing.
                # Use a sentinel so the formula parser knows this is a function name
                # and will create it with the correct arity on first use.
                var_map[name] = _FUNCTION_SENTINEL
            elif vtype == "constant":
                # Individual constants (persons, objects, etc.)
                var_map[name] = Const(name, Individual)
            else:
                var_map[name] = Bool(name)  # default fallback

        return {
            "success": True,
            "finding_id": finding_id,
            "error_type": error_type,
            "query_type": query_type,
            "var_map": var_map,
            "premises_formulas": premises_formulas,
            "claimed_conclusion": claimed_conclusion,
        }
    except Exception as e:
        return {
            "success": False,
            "finding_id": fol_json.get("finding_id", "unknown"),
            "error": f"parse_fol_encoding exception: {str(e)}"
        }


# ─── Z3 Query Executor ───────────────────────────────────────────────────────

def run_verification(parsed: dict) -> dict:
    """
    Execute Z3 verification based on query_type.

    check_inference:
      - Assert premises, assert NOT conclusion
      - SAT → counterexample exists → inference INVALID → FALLACY CONFIRMED
      - UNSAT → conclusion necessarily follows → NO FALLACY

    check_contradiction:
      - Assert premises (which should contain both P and Not(P))
      - UNSAT → contradiction → FALLACY CONFIRMED
      - SAT → no direct contradiction → NO FALLACY

    check_equivalence:
      - Assert premises, assert NOT conclusion
      - UNSAT → equivalence holds → potentially circular → NO FALLACY
      - SAT → no equivalence → UNCERTAIN

    Returns dict with: finding_id, z3_result, interpretation, verification_verdict, model, error
    """
    finding_id = parsed["finding_id"]
    query_type = parsed["query_type"]
    var_map = parsed["var_map"]

    if not parsed["success"]:
        return {
            "finding_id": finding_id,
            "z3_result": "parse_error",
            "interpretation": "failed_to_parse_fol_encoding",
            "verification_verdict": VerificationVerdict.UNCERTAIN.value,
            "model": None,
            "error": parsed.get("error", "unknown parse error"),
        }

    # Parse premises into Z3 expressions
    parsed_premises = []
    for i, formula_str in enumerate(parsed["premises_formulas"]):
        expr, err = parse_formula(formula_str, var_map)
        if err:
            return {
                "finding_id": finding_id,
                "z3_result": "parse_error",
                "interpretation": f"premise_{i}_parse_failed",
                "verification_verdict": VerificationVerdict.UNCERTAIN.value,
                "model": None,
                "error": f"premise[{i}] '{formula_str}': {err}",
            }
        parsed_premises.append(expr)

    # Parse conclusion (if applicable)
    conclusion_expr = None
    if parsed["claimed_conclusion"]:
        conclusion_expr, err = parse_formula(parsed["claimed_conclusion"], var_map)
        if err:
            return {
                "finding_id": finding_id,
                "z3_result": "parse_error",
                "interpretation": "conclusion_parse_failed",
                "verification_verdict": VerificationVerdict.UNCERTAIN.value,
                "model": None,
                "error": f"conclusion '{parsed['claimed_conclusion']}': {err}",
            }

    # Build solver
    solver = Solver()
    solver.set("timeout", 5000)  # 5s timeout

    try:
        if query_type == "check_inference":
            # Premises AND NOT Conclusion → SAT means inference invalid
            for p in parsed_premises:
                solver.add(p)
            solver.add(Not(conclusion_expr))

        elif query_type == "check_contradiction":
            # Just check premises joint satisfiability
            for p in parsed_premises:
                solver.add(p)

        elif query_type == "check_equivalence":
            # For circular reasoning: P↔Q, P ⊢ Q - check if (premises ∧ ¬conclusion) is SAT
            for p in parsed_premises:
                solver.add(p)
            solver.add(Not(conclusion_expr))

        result = solver.check()

        # Map results
        if result == sat:
            model = solver.model()
            model_dict = {}
            for decl in model.decls():
                try:
                    val = model[decl]
                    model_dict[str(decl)] = str(val)
                except Exception:
                    pass

            if query_type == "check_inference":
                return {
                    "finding_id": finding_id,
                    "z3_result": "sat",
                    "interpretation": "counterexample_found",
                    "verification_verdict": VerificationVerdict.FALLACY_CONFIRMED.value,
                    "model": model_dict,
                    "error": None,
                }
            elif query_type == "check_contradiction":
                return {
                    "finding_id": finding_id,
                    "z3_result": "sat",
                    "interpretation": "no_contradiction",
                    "verification_verdict": VerificationVerdict.NO_FALLACY.value,
                    "model": model_dict,
                    "error": None,
                }
            else:  # check_equivalence
                return {
                    "finding_id": finding_id,
                    "z3_result": "sat",
                    "interpretation": "counterexample_found",
                    "verification_verdict": VerificationVerdict.UNCERTAIN.value,
                    "model": model_dict,
                    "error": None,
                }

        elif result == unsat:
            if query_type == "check_inference":
                return {
                    "finding_id": finding_id,
                    "z3_result": "unsat",
                    "interpretation": "valid_inference",
                    "verification_verdict": VerificationVerdict.NO_FALLACY.value,
                    "model": None,
                    "error": None,
                }
            elif query_type == "check_contradiction":
                return {
                    "finding_id": finding_id,
                    "z3_result": "unsat",
                    "interpretation": "contradiction_detected",
                    "verification_verdict": VerificationVerdict.FALLACY_CONFIRMED.value,
                    "model": None,
                    "error": None,
                }
            else:  # check_equivalence
                return {
                    "finding_id": finding_id,
                    "z3_result": "unsat",
                    "interpretation": "valid_inference_no_equivalence",
                    "verification_verdict": VerificationVerdict.NO_FALLACY.value,
                    "model": None,
                    "error": None,
                }

        else:  # unknown
            return {
                "finding_id": finding_id,
                "z3_result": "unknown",
                "interpretation": "solver_timed_out_or_unknown",
                "verification_verdict": VerificationVerdict.UNCERTAIN.value,
                "model": None,
                "error": f"solver returned {result}",
            }

    except Exception as e:
        return {
            "finding_id": finding_id,
            "z3_result": "error",
            "interpretation": "solver_exception",
            "verification_verdict": VerificationVerdict.UNCERTAIN.value,
            "model": None,
            "error": str(e),
        }


def verify_all(fol_encodings: list, max_workers: int = 1) -> list:
    """Verify a batch of FOL encodings. Returns list of verdict dicts in input order.

    When max_workers=1 (default), runs serially — safe on all platforms.
    When max_workers > 1, uses ProcessPoolExecutor for parallel verification.
    Thread-level concurrency is avoided because Z3 solver objects are not
    thread-safe on Windows (GC cross-thread access violations).
    """
    if max_workers <= 1:
        verdicts = []
        for encoding in fol_encodings:
            parsed = parse_fol_encoding(encoding)
            verdict = run_verification(parsed)
            verdicts.append(verdict)
        return verdicts

    # Process-level concurrency — avoids Z3 thread-safety issues
    from concurrent.futures import ProcessPoolExecutor, as_completed

    def _verify_one(encoding: dict) -> dict:
        parsed = parse_fol_encoding(encoding)
        return run_verification(parsed)

    verdicts = []
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_verify_one, e): i for i, e in enumerate(fol_encodings)}
        for future in as_completed(futures):
            idx = futures[future]
            try:
                verdicts.append((idx, future.result()))
            except Exception as exc:
                verdicts.append((idx, {
                    "finding_id": fol_encodings[idx].get("finding_id", "unknown"),
                    "z3_result": "error",
                    "interpretation": "concurrent_execution_failed",
                    "verification_verdict": VerificationVerdict.UNCERTAIN.value,
                    "model": None,
                    "error": str(exc),
                }))

    # Restore original order
    verdicts.sort(key=lambda x: x[0])
    return [v for _, v in verdicts]


# ─── CLI Entry Point ──────────────────────────────────────────────────────────

def main():
    """Read FOL encodings from stdin (JSON array), output verdicts to stdout."""
    raw = sys.stdin.read()
    try:
        encodings = json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"invalid JSON input: {e}"}))
        sys.exit(1)

    if not isinstance(encodings, list):
        encodings = [encodings]

    verdicts = verify_all(encodings)
    print(json.dumps(verdicts, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
