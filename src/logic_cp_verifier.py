#!/usr/bin/env python3
"""
logic_cp: Counter-Example Guided FOL Translation Validation.

Adapted from CLOVER's "Disproving by Counter-Interpretation" (ICLR 2025).

When Z3 finds a counterexample (SAT), we don't immediately trust the FOL encoding.
Instead, we ask an LLM to judge if the counterexample model genuinely
contradicts the original natural language argument.

If the counterexample IS valid → formal fallacy confirmed (high confidence)
If the counterexample SEEMS WRONG → FOL translation error → downgrade to candidate

Usage:
  from src.logic_cp_verifier import build_counterexample_prompt, parse_judgment
"""
import json
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

PROMPT_TEMPLATE = """You are evaluating whether a counterexample found by a theorem prover genuinely disproves a logical argument.

ORIGINAL ARGUMENT:
{original_text}

FOL ENCODING:
Variables:
{variables}

Premises (Z3 formulas):
{premises}

Claimed Conclusion:
{conclusion}

Z3 found this argument INVALID (SAT = counterexample exists).
The counterexample assigns these values:
{model}

QUESTION: Does this counterexample make logical sense given the ORIGINAL argument?

Think step by step:
1. What does each variable represent in the real argument?
2. In the counterexample, verify each premise: is it really true given the assigned values?
3. Is the conclusion really false?
4. Does this scenario genuinely show a logical error in the ORIGINAL argument?

Answer with EXACTLY one line at the end:
VALID_COUNTEREXAMPLE
  → The counterexample correctly shows the original argument's reasoning is flawed
INVALID_COUNTEREXAMPLE
  → The counterexample does NOT match the original argument's meaning (FOL translation error)
"""


def build_counterexample_prompt(
    original_text: str,
    premises: list,
    conclusion: str,
    variables: list,
    model_dict: dict,
) -> str:
    """Build the prompt for LLM to judge a counterexample."""
    # Format variables
    var_lines = []
    for v in variables:
        name = v.get("name", "?")
        meaning = v.get("meaning", "")
        var_lines.append(f"  {name}: {meaning}")
    var_str = "\n".join(var_lines) if var_lines else "  (no variables declared)"

    # Format premises
    prem_str = "\n".join(f"  - {p}" for p in premises) if premises else "  (none)"

    # Format model
    model_lines = []
    for k, v in sorted(model_dict.items()):
        model_lines.append(f"  {k} = {v}")
    model_str = "\n".join(model_lines) if model_lines else "  (empty model)"

    return PROMPT_TEMPLATE.format(
        original_text=original_text,
        variables=var_str,
        premises=prem_str,
        conclusion=conclusion,
        model=model_str,
    )


def parse_judgment(response: str) -> dict:
    """
    Parse the LLM's judgment response.

    Returns:
      {"verdict": "valid"|"invalid", "reasoning": str}
    """
    response_upper = response.upper()

    # IMPORTANT: check INVALID first — INVALID_COUNTEREXAMPLE contains VALID_COUNTEREXAMPLE as substring
    if "INVALID_COUNTEREXAMPLE" in response_upper:
        reasoning = response
        if "INVALID_COUNTEREXAMPLE" in response:
            reasoning = response.split("INVALID_COUNTEREXAMPLE")[0].strip()
        return {
            "verdict": "invalid",
            "reasoning": reasoning,
        }
    elif "VALID_COUNTEREXAMPLE" in response_upper:
        reasoning = response
        if "VALID_COUNTEREXAMPLE" in response:
            reasoning = response.split("VALID_COUNTEREXAMPLE")[0].strip()
        return {
            "verdict": "valid",
            "reasoning": reasoning,
        }
    else:
        # Fallback: check if the response seems to confirm or reject
        # Note: check INVALID before VALID since INVALID contains VALID as substring
        if any(word in response_upper for word in ["WRONG", "INCORRECT", "NOT VALID", "INVALID"]):
            return {"verdict": "invalid", "reasoning": response.strip()}
        elif any(word in response_upper for word in ["YES", "CORRECT", "GENUINE", "VALID"]):
            return {"verdict": "valid", "reasoning": response.strip()}
        else:
            return {"verdict": "unknown", "reasoning": response.strip()}


def apply_logic_cp_verdict(
    z3_result: dict,
    cp_verdict: str,
) -> dict:
    """
    Apply logic_cp verdict to modify Z3's original verdict.

    If Z3 said SAT (formal) but LLM says the counterexample is INVALID →
      downgrade to candidate (FOL translation error)
    If Z3 said SAT and LLM confirms VALID →
      keep as formal (high confidence)
    If Z3 said UNSAT →
      no counterexample, logic_cp doesn't apply
    """
    result = dict(z3_result)  # copy

    if result.get("z3_result") != "sat":
        # UNSAT or UNKNOWN → no counterexample, logic_cp doesn't apply
        return result

    if cp_verdict == "invalid":
        # Counterexample doesn't match NL → FOL translation error
        result["verification_verdict"] = "candidate"
        result["interpretation"] = "logic_cp_rejected: counterexample invalid for original text"
        result["logic_cp"] = "rejected"
    elif cp_verdict == "valid":
        # Counterexample confirmed
        result["interpretation"] = "logic_cp_confirmed: counterexample validated by LLM"
        result["logic_cp"] = "confirmed"
    else:
        # Unknown → keep original but mark it
        result["logic_cp"] = "unclear"

    return result


# ─── Self-test ────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Test with a simple affirming consequent case
    prompt = build_counterexample_prompt(
        original_text='If it is raining, the ground is wet. The ground is wet. Therefore, it is raining.',
        premises=["Implies(P, Q)", "Q"],
        conclusion="P",
        variables=[
            {"name": "P", "type": "bool", "meaning": "It is raining"},
            {"name": "Q", "type": "bool", "meaning": "The ground is wet"},
        ],
        model_dict={"P": "False", "Q": "True"},
    )
    print("=" * 60)
    print("LOGIC_CP TEST PROMPT")
    print("=" * 60)
    print(prompt)
    print()
    print("=" * 60)
    print("Expected judgment: VALID_COUNTEREXAMPLE (P=False, Q=True → "
          "ground is wet but not raining → affirming consequent)")
    print("=" * 60)
