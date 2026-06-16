"""
Logic Audit — formal verification framework for logical fallacy detection.

Core modules:
  - z3_verifier:      Z3 theorem prover wrapper (parse FOL → verify → verdict)
  - logic_cp_verifier: Counterexample-guided validation (adapted from CLOVER ICLR 2025)
  - run_direction_b_eval: Full evaluation pipeline on built-in datasets
"""

from .z3_verifier import (
    parse_fol_encoding,
    run_verification,
    verify_all,
    VerificationVerdict,
    parse_formula,
)
from .logic_cp_verifier import (
    build_counterexample_prompt,
    parse_judgment,
    apply_logic_cp_verdict,
)
from .run_direction_b_eval import (
    evaluate_case,
    compute_metrics,
    generate_report,
    load_dataset,
)

__all__ = [
    # z3_verifier
    "parse_fol_encoding",
    "run_verification",
    "verify_all",
    "VerificationVerdict",
    "parse_formula",
    # logic_cp_verifier
    "build_counterexample_prompt",
    "parse_judgment",
    "apply_logic_cp_verdict",
    # run_direction_b_eval
    "evaluate_case",
    "compute_metrics",
    "generate_report",
    "load_dataset",
]
