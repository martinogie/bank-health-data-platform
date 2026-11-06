# Yearly model review checklist

Once a year (next: **October 2027**, a year after v5), and after any new champion. Done by Martins;
a person other than the model's builder for an independent review ([validation pack](validation/README.md)).
Record the result as `docs/validation/review_<yyyy-mm-dd>.md` (date, who, the ticked list, notes).

## 1. Is it still the model we think it is?

- [ ] `scripts/validation_reproduce.py` passes (the card's numbers = MLflow's records).
- [ ] The model card describes the current `@champion` version, environment and features.
- [ ] Open validation points (VF-xx) have an answer or a date.

## 2. Is it still working?

- [ ] Backtest in `OPS.MODEL_MONITORING`: realised top-10% capture per quarter vs the test result
      (52.6%); any quarter below the alert line (42.6%) explained.
- [ ] Score and feature drift (PSI) vs the previous 4 quarters: alerts in the year, and what was done.
- [ ] Band counts per quarter: no band suddenly empty or full without a reason.
- [ ] The weekly top-20 release check passed every week; any exception explained.

## 3. Has the world changed?

- [ ] New FDIC fields or definitions (call-report changes, new CBLR rules) that affect a feature
      or the label: check the FDIC's release notes and the data contracts' warnings.
- [ ] Bank failures in the year: was each one in a high band beforehand? List the misses.
- [ ] The known limits (CBLR, > $100B, assistance counted as failure, immaterial losses): any worse?

## 4. Decide

- [ ] Keep v___ as champion, **or** plan a retrain (v6 list in the model card), **or** retire.
- [ ] If retraining: train in dev, compare on the same test rows; promotion only through the gate
      with `promote=true`.
- [ ] Update the model card ("reviewed on ...") and set the next review date here.
