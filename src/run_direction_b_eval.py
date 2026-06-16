#!/usr/bin/env python3
"""
Direction B evaluation: run Z3 verification on AI formal fallacy dataset and produce report.
This validates the Z3 verifier against ground-truth FOL encodings.
"""
import json
import sys
from collections import Counter
from pathlib import Path
from src.z3_verifier import parse_fol_encoding, run_verification, verify_all


def load_dataset(path: str) -> dict:
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def evaluate_case(case: dict) -> dict:
    """Run Z3 verification on a single case's ground truth FOL encoding."""
    gt = case['ground_truth']
    fol = gt['fol_encoding']

    # Convert variables
    variables = []
    for var_name, var_desc in fol['variables'].items():
        if isinstance(var_desc, dict):
            vtype = var_desc.get('type', 'function')  # 读取实际类型（constant/function）
            variables.append({'name': var_name, 'type': vtype, 'meaning': var_desc['meaning']})
        else:
            variables.append({'name': var_name, 'type': 'bool', 'meaning': var_desc})

    query_type = 'check_contradiction' if fol['formula_type'] == 'contradiction' else 'check_inference'
    claimed_conclusion = fol.get('claimed_conclusion', '')

    fol_input = {
        'finding_id': case['id'],
        'error_type': gt['error_type'],
        'query_type': query_type,
        'variables': variables,
        'premises_formulas': fol['premises'],
        'claimed_conclusion': claimed_conclusion,
    }

    parsed = parse_fol_encoding(fol_input)
    result = run_verification(parsed)

    # Determine if correct
    expected_z3 = fol['expected_z3_result']
    expected_verdict = gt['expected_verdict']
    z3_correct = result['z3_result'] == expected_z3
    verdict_correct = result['verification_verdict'] == expected_verdict
    overall_correct = z3_correct and verdict_correct

    return {
        'id': case['id'],
        'error_type': gt['error_type'],
        'expected_z3_result': expected_z3,
        'z3_result': result['z3_result'],
        'expected_verdict': expected_verdict,
        'actual_verdict': result['verification_verdict'],
        'z3_correct': z3_correct,
        'verdict_correct': verdict_correct,
        'overall_correct': overall_correct,
        'interpretation': result['interpretation'],
        'model': result.get('model'),
        'error': result.get('error'),
        'formula_type': fol['formula_type'],
    }


def compute_metrics(results: list) -> dict:
    """Compute precision, recall, F1 per error type and overall."""
    per_type = {}
    for r in results:
        et = r['error_type']
        if et not in per_type:
            per_type[et] = {'tp': 0, 'fp': 0, 'fn': 0, 'tn': 0, 'total': 0}
        per_type[et]['total'] += 1

        if et == 'clean':
            # Clean: expected_verdict = discard (no fallacy)
            if r['actual_verdict'] == 'discard':
                per_type[et]['tn'] += 1
            else:
                per_type[et]['fp'] += 1
        else:
            # Non-clean: expected_verdict = formal (has fallacy)
            if r['actual_verdict'] == 'formal':
                per_type[et]['tp'] += 1
            elif r['actual_verdict'] == 'discard':
                per_type[et]['fn'] += 1
            else:  # candidate
                per_type[et]['fn'] += 1  # candidate counts as miss for formal detection

    metrics = {}
    for et, counts in sorted(per_type.items()):
        tp, fp, fn, tn = counts['tp'], counts['fp'], counts['fn'], counts['tn']
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
        specificity = tn / (tn + fp) if (tn + fp) > 0 else 0

        metrics[et] = {
            'total': counts['total'],
            'tp': tp, 'fp': fp, 'fn': fn, 'tn': tn,
            'precision': round(precision, 4),
            'recall': round(recall, 4),
            'f1': round(f1, 4),
            'specificity': round(specificity, 4),
        }

    # Overall (excluding clean)
    non_clean = {et: m for et, m in metrics.items() if et != 'clean'}
    if non_clean:
        total_tp = sum(m['tp'] for m in non_clean.values())
        total_fp = sum(m['fp'] for m in non_clean.values())
        total_fn = sum(m['fn'] for m in non_clean.values())
        overall_precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0
        overall_recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0
        overall_f1 = 2 * overall_precision * overall_recall / (overall_precision + overall_recall) if (overall_precision + overall_recall) > 0 else 0
        metrics['overall'] = {
            'total': sum(m['total'] for m in non_clean.values()),
            'tp': total_tp, 'fp': total_fp, 'fn': total_fn,
            'precision': round(overall_precision, 4),
            'recall': round(overall_recall, 4),
            'f1': round(overall_f1, 4),
        }

    # Clean metrics
    if 'clean' in metrics:
        c = metrics['clean']
        metrics['clean_false_positive_rate'] = c['fp'] / (c['fp'] + c['tn']) if (c['fp'] + c['tn']) > 0 else 0
        metrics['clean_specificity'] = c['specificity']

    return metrics


def generate_report(results: list, metrics: dict, meta: dict) -> str:
    """Generate markdown report."""
    lines = []
    lines.append("# Direction B: Formal Verification Evaluation Report")
    lines.append(f"\n## Summary")
    lines.append(f"- Total cases: {meta['total_cases']}")
    lines.append(f"- Z3 verdict accuracy: {sum(1 for r in results if r['verdict_correct'])}/{len(results)} ({sum(1 for r in results if r['verdict_correct'])/len(results)*100:.1f}%)")
    lines.append(f"- Z3 result accuracy: {sum(1 for r in results if r['z3_correct'])}/{len(results)} ({sum(1 for r in results if r['z3_correct'])/len(results)*100:.1f}%)")
    lines.append(f"- Overall correct: {sum(1 for r in results if r['overall_correct'])}/{len(results)} ({sum(1 for r in results if r['overall_correct'])/len(results)*100:.1f}%)")

    lines.append(f"\n## Metrics by Error Type")
    lines.append(f"| Type | Total | TP | FP | FN | TN | Precision | Recall | F1 |")
    lines.append(f"|------|-------|----|----|----|----|-----------|--------|-----|")
    for et, m in sorted(metrics.items()):
        if et == 'overall':
            continue
        if et == 'clean_false_positive_rate':
            continue
        if et == 'clean_specificity':
            continue
        lines.append(f"| {et} | {m['total']} | {m['tp']} | {m['fp']} | {m['fn']} | {m['tn']} | {m['precision']:.2f} | {m['recall']:.2f} | {m['f1']:.2f} |")
    if 'clean' in metrics:
        lines.append(f"| **Clean** | {metrics['clean']['total']} | - | {metrics['clean']['fp']} | - | {metrics['clean']['tn']} | - | - | - |")
        lines.append(f"| **Clean FP Rate** | | | {metrics.get('clean_false_positive_rate', 0):.4f} | | | | | |")

    if 'overall' in metrics:
        m = metrics['overall']
        lines.append(f"| **Overall (non-clean)** | {m['total']} | {m['tp']} | {m['fp']} | {m['fn']} | - | {m['precision']:.4f} | {m['recall']:.4f} | {m['f1']:.4f} |")

    lines.append(f"\n## Detail by Case")
    lines.append(f"| ID | Error Type | Z3 Result | Expected | Verdict Match |")
    lines.append(f"|----|-----------|-----------|----------|---------------|")
    for r in results:
        match = "✅" if r['verdict_correct'] else "❌"
        lines.append(f"| {r['id']} | {r['error_type']} | {r['z3_result']} | {r['expected_verdict']} | {match} |")

    lines.append(f"\n## Error Analysis")
    failed = [r for r in results if not r['verdict_correct']]
    if failed:
        lines.append(f"### Verdict Mismatches ({len(failed)} cases)")
        for r in failed:
            lines.append(f"- {r['id']} ({r['error_type']}): Z3={r['z3_result']}, expected_verdict={r['expected_verdict']}, actual={r['actual_verdict']}")
            if r.get('error'):
                lines.append(f"  - Error: {r['error']}")
    else:
        lines.append("All verdicts correct! 🎉")

    return '\n'.join(lines)


def main():
    base = Path.cwd()
    data_path = base / "data" / "ai_formal_fallacies.json"

    data = load_dataset(str(data_path))
    meta = data['meta']
    cases = data['cases']

    print(f"Running Direction B evaluation on {len(cases)} cases...")

    # Evaluate all cases
    results = [evaluate_case(c) for c in cases]

    # Compute metrics
    metrics = compute_metrics(results)

    # Generate report (returned as string, not written to file)
    report = generate_report(results, metrics, meta)

    scores = {
        'meta': meta,
        'metrics': {k: v for k, v in metrics.items() if isinstance(v, dict)},
        'clean_false_positive_rate': metrics.get('clean_false_positive_rate'),
        'results': [
            {k: r[k] for k in ('id', 'error_type', 'z3_result', 'expected_verdict', 'actual_verdict', 'verdict_correct', 'interpretation')}
            for r in results
        ]
    }

    # Print summary
    correct = sum(1 for r in results if r['verdict_correct'])
    print(f"\n{'='*50}")
    print(f"Direction B Evaluation Results")
    print(f"{'='*50}")
    print(f"Total cases: {len(results)}")
    print(f"Verdict accuracy: {correct}/{len(results)} ({correct/len(results)*100:.1f}%)")
    if 'overall' in metrics:
        m = metrics['overall']
        print(f"Overall Precision: {m['precision']:.4f}")
        print(f"Overall Recall: {m['recall']:.4f}")
        print(f"Overall F1: {m['f1']:.4f}")
    if 'clean' in metrics:
        print(f"Clean FP Rate: {metrics.get('clean_false_positive_rate', 0):.4f}")

    return results, metrics, report


if __name__ == '__main__':
    main()
