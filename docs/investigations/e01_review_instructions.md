# E-01: how to review the match labels

**File:** [e01_match_labels.csv](e01_match_labels.csv), 250 rows, one per CFPB company. Open it in
Excel (or any spreadsheet). It is a one-time audit of how CFPB complaints are matched to FDIC
banks. No figure from it is published until you have confirmed the labels.

## What's in it

| Rows | `group` | What it is |
|---|---|---|
| 100 | `matched` | random companies (fixed seed 20261029) that **were** matched to a bank. Question: is the match right? |
| 100 | `unmatched` | random companies that were **not** matched. Question: what kind of company is it, and should it have matched a bank? |
| 50 | `top_unmatched` | the 50 largest unmatched companies by complaints (not random; 64% of unmatched complaints). Same question. Missed banks hide here. |

Population (prod silver, 3 Nov 2026): 142 matched companies (600,661 complaints) and 5,258
unmatched (1,398,891 complaints), in [e01_population.json](e01_population.json).

## The columns

**Evidence (read-only, from the data):**

| Column | Meaning |
|---|---|
| `group`, `draw_rank` | which list, and the row's place in it (random order, or size order for `top_unmatched`) |
| `company` | the company name as CFPB writes it |
| `complaints` | its complaints in the pipeline (the last 3 years, credit bureaus excluded) |
| `match_method` | how it was matched: `bank_name`, `holding_company` (complaints go to the group's largest bank), `manual_override`, or `unmatched` |
| `cert`, `matched_bank_name`, `matched_holding_company`, `matched_state`, `matched_active`, `matched_assets_k` | the FDIC bank it was matched to (assets in $ thousands) |
| `top_states`, `top_products` | where its complaints come from and what they are about, with counts. Usually the best clue. |
| `candidate_*` | unmatched rows only: the FDIC bank or holding-company name **closest in spelling**, with a 0–1 similarity. Only a hint: most are look-alikes, not the same company. |

**My proposal (filled in, for you to check):**

| Column | Meaning |
|---|---|
| `proposed_label` | my label (lists below) |
| `proposed_cert` | the FDIC cert of the bank I think it should match: filled in only where I looked it up in FDIC data (Bread Financial, USAA), never guessed |
| `evidence_note` | my reason, starting with my confidence: `[high]`, `[medium]` or `[low]` |

**Yours (empty, for you to fill in):**

| Column | What to put |
|---|---|
| `confirmed_by_martins` | **`yes`** if you agree with `proposed_label`; **`no`** if you don't; **`unsure`** if you can't tell |
| `martins_label` | only when you put `no`: the right label, from the lists below |
| `martins_note` | optional: why, or anything you looked up (e.g. "should be Rockland Trust, MA") |

## The labels

**`matched` rows:**

| Label | Meaning |
|---|---|
| `correct` | the complaints are about the matched bank (or, for a holding company, its banking group) |
| `wrong` | a different company with a similar name, or complaints that aren't about that bank |
| `unsure` | can't tell from the evidence |

**`unmatched` and `top_unmatched` rows:**

| Label | Meaning |
|---|---|
| `true_miss` | an FDIC-insured bank (or its group) that **should** have matched. Say which bank in `martins_note` if you know |
| `debt_collector` | collection agencies, debt buyers, collection law firms |
| `credit_union` | NCUA-insured credit unions (not FDIC banks, rightly unmatched) |
| `fintech` | apps and online finance companies, including ones that use partner banks |
| `other_non_bank` | everything else that isn't a bank: mortgage lenders and servicers, auto and consumer lenders, student loan servicers, credit reporting and data companies, title firms |

A company that **owns** a bank but whose complaints are about something else (Block's Cash App,
Nelnet's loan servicing, Toyota's auto loans) is labelled by what the complaints are about. If
you'd rather count those as `true_miss`, put `no` and `true_miss`.

## How to review (about 30–45 minutes)

1. Sort or filter by `group`. Start with `top_unmatched` (biggest effect), then `matched`, then
   `unmatched`.
2. Look at the rows I marked `wrong`, `unsure`, `true_miss`, or `[low]`/`[medium]` first. The
   `[high]` ones are mostly quick confirmations (filter `evidence_note` by "[high]").
3. For each row, put `yes`, `no` or `unsure` in `confirmed_by_martins`. If `no`, fill in
   `martins_label` (and a `martins_note` if useful).
4. Don't change the other columns. Save as CSV (keep the name). Tell me when it's done.

## What happens next

1. I compute precision and recall, by company and by complaint volume, with 95% intervals, from
   your confirmed labels only.
2. Fuzzy matching proposes candidate banks for the `true_miss` rows and the `wrong` matches; you
   approve each one; only approved rows become overrides. Before/after numbers.
3. Nothing here changes the pipeline until you approve the candidates.
