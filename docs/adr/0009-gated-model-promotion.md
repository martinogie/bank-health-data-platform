# ADR 0009: Gated model promotion

- **Status:** accepted (Phase 3, PR 9, 3 Nov 2026)
- **Findings:** M-09

## Context

A retrained model became `@champion` whenever a person ran training with `promote=true`. Nothing
stopped a worse model from being promoted by mistake, and nothing recorded why a promotion was
allowed.

## Decision

A retrained model becomes `@champion` only with **both** a person's `promote=true` **and** a
promotable decision from the gate (`databricks/notebooks/_promotion.py`), judged on **the same test
rows** as the champion (a hash of the sorted row keys):

| Result | When | With `promote=true` |
|---|---|---|
| `fail` | more than 0.005 PR-AUC or 1.0 point of top-10% capture below the champion, or different test rows | refused; the training run fails with the reasons |
| `pass` | better beyond a tolerance on either measure | promoted |
| `tie` | within both tolerances | promoted (not worse) |

The decision is logged in MLflow (`promotion_gate.json`) and on the model version's tags
(`gate_decision`, `test_rows_hash`). It runs only on a retrain.

## Why these tolerances

Agreed on 29 Oct 2026: differences this small are within the noise of one test period, so a tie
shouldn't block a model with other reasons to change (fixes, simpler features); anything worse
needs a deliberate decision, not a promotion by habit.

## Consequences

v5 was promoted before the gate existed; the first gated decision comes with v6.
