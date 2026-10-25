# Databricks notebook source
# MAGIC %md
# MAGIC # _features — model features and labels (shared by 04_train_model and 05_score)
# MAGIC
# MAGIC Loaded with `%run ./_features` after `%run ./_common`. Builds one row per bank-quarter `t`.
# MAGIC
# MAGIC **Features** use only information available at `t` (that quarter's call report and earlier
# MAGIC ones): capital, asset quality, earnings, liquidity, growth, loan mix, size and changes
# MAGIC versus 1 and 4 quarters earlier. Prior quarters are matched on the exact quarter index, so a
# MAGIC missing quarter gives NULL rather than a wrong comparison.
# MAGIC
# MAGIC **Distress at a quarter** = any of: non-current loans > 3% of loans; Tier 1 leverage < 5%;
# MAGIC net loss this quarter *and* the previous one.
# MAGIC
# MAGIC **Label** (`distress_next_4q`) = distress in any of quarters t+1..t+4, **or** an FDIC failure
# MAGIC within 12 months after `t` (every failure date counts, not just a bank's first).
# MAGIC
# MAGIC **Challenger label** (`distress_strict_next_4q`) = failure within 12 months, or Tier 1 < 5%,
# MAGIC or non-current loans > 3% in t+1..t+4 (no "two net losses" condition).
# MAGIC
# MAGIC Total risk-based capital is **not** a feature: CBLR banks (from 2020) don't report it, and
# MAGIC no training year has CBLR banks. It stays in the data for display.
# MAGIC
# MAGIC Rows used for training/testing (`is_model_row`):
# MAGIC - the bank is **not already in distress** at `t` (we predict the onset of distress);
# MAGIC - it is **not a US office of a foreign bank** (`is_foreign_office`: no capital ratios, so
# MAGIC   the model can't judge it);
# MAGIC - it is **at least 3 years old** (`is_new_bank`): new banks make start-up losses by design,
# MAGIC   which the label reads as distress;
# MAGIC - the outcome is **observed**: the bank still reports at t+4, or it failed within the window
# MAGIC   (banks that vanish without failing, usually mergers, have an unknown outcome and are dropped);
# MAGIC - `t` is at least 4 quarters before the latest data (otherwise the label is incomplete).

# COMMAND ----------

from pyspark.sql import Window
from pyspark.sql import functions as F

NCL_THRESHOLD = 3.0      # non-current loans, % of loans
TIER1_THRESHOLD = 5.0    # Tier 1 leverage ratio, %
NEW_BANK_YEARS = 3.0     # younger banks are not scored

FEATURES = [
    # capital
    "tier1_leverage_pct", "equity_to_assets_pct",
    # asset quality
    "noncurrent_loans_pct", "net_chargeoff_rate_pct", "past_due_30_89_to_loans_pct",
    "past_due_90_to_loans_pct", "nonaccrual_to_assets_pct", "ore_to_assets_pct",
    # earnings
    "roa_pct", "roe_pct", "net_interest_margin_pct", "efficiency_ratio_pct", "net_income_qtr_to_assets_pct",
    # liquidity and funding
    "loans_to_deposits_pct", "brokered_to_deposits_pct",
    # growth
    "loan_growth_qoq_pct", "loan_growth_yoy_pct", "deposit_growth_yoy_pct", "asset_growth_yoy_pct",
    # loan mix / concentration
    "cre_to_loans_pct", "cre_to_equity_pct", "construction_to_loans_pct", "ci_to_loans_pct",
    "consumer_to_loans_pct", "residential_to_loans_pct", "ag_to_loans_pct",
    # size
    "log_total_assets",
    # changes
    "noncurrent_change_1q_pp", "noncurrent_change_4q_pp", "tier1_change_4q_pp", "roa_change_4q_pp",
]

# Capital ratios built on risk-weighted assets: CBLR banks (from 2020) don't report them, so
# they are kept out of FEATURES (v5 onward). Listed so a test can check they stay out.
RISK_BASED_CAPITAL_FEATURES = ["total_risk_based_capital_pct"]


def _ratio(num, den, scale=100.0):
    return F.when(F.col(den) > 0, F.col(num) / F.col(den) * scale)


def build_model_frame():
    """Spark DataFrame: cert, report_date, FEATURES, distress flags, label, is_model_row."""
    gold = spark.table(f"{GOLD}.bank_quarter")
    latest = gold.agg(F.max("report_date")).first()[0]

    df = (
        gold.withColumn("q_idx", F.year("report_date") * 4 + F.quarter("report_date"))
        .withColumn("past_due_30_89_to_loans_pct", _ratio("past_due_30_89_k", "gross_loans_k"))
        .withColumn("past_due_90_to_loans_pct", _ratio("past_due_90_plus_k", "gross_loans_k"))
        .withColumn("nonaccrual_to_assets_pct", _ratio("nonaccrual_assets_k", "total_assets_k"))
        .withColumn("ore_to_assets_pct", _ratio("other_real_estate_owned_k", "total_assets_k"))
        .withColumn("net_income_qtr_to_assets_pct", _ratio("net_income_qtr_k", "total_assets_k"))
        .withColumn("brokered_to_deposits_pct", _ratio("brokered_deposits_k", "total_deposits_k"))
        .withColumn("construction_to_loans_pct", _ratio("construction_loans_k", "gross_loans_k"))
        .withColumn("ci_to_loans_pct", _ratio("commercial_industrial_loans_k", "gross_loans_k"))
        .withColumn("consumer_to_loans_pct", _ratio("consumer_loans_k", "gross_loans_k"))
        .withColumn("residential_to_loans_pct", _ratio("residential_re_loans_k", "gross_loans_k"))
        .withColumn("ag_to_loans_pct", _ratio("agricultural_loans_k", "gross_loans_k"))
        .withColumn("is_loss_qtr", F.col("net_income_qtr_k") < 0)
        .withColumn("cblr_reporter_flag", F.col("cblr_reporter").cast("double"))
        .withColumn("is_new_bank", F.coalesce(F.col("bank_age_years") < NEW_BANK_YEARS, F.lit(False)))
    )

    # Values from exactly 1 and 4 quarters earlier (NULL if that quarter is missing)
    prior = df.select(
        "cert", "q_idx", "noncurrent_loans_pct", "tier1_leverage_pct", "roa_pct", "is_loss_qtr"
    )
    def lagged(k):
        return prior.select(
            "cert", (F.col("q_idx") + k).alias("q_idx"),
            *[F.col(c).alias(f"{c}_lag{k}") for c in ("noncurrent_loans_pct", "tier1_leverage_pct", "roa_pct", "is_loss_qtr")]
        )
    df = (
        df.join(lagged(1), ["cert", "q_idx"], "left")
        .join(lagged(4), ["cert", "q_idx"], "left")
        .withColumn("noncurrent_change_1q_pp", F.col("noncurrent_loans_pct") - F.col("noncurrent_loans_pct_lag1"))
        .withColumn("noncurrent_change_4q_pp", F.col("noncurrent_loans_pct") - F.col("noncurrent_loans_pct_lag4"))
        .withColumn("tier1_change_4q_pp", F.col("tier1_leverage_pct") - F.col("tier1_leverage_pct_lag4"))
        .withColumn("roa_change_4q_pp", F.col("roa_pct") - F.col("roa_pct_lag4"))
        # Distress at this quarter (NULL metrics count as "no breach")
        .withColumn("is_distressed",
                    F.coalesce(F.col("noncurrent_loans_pct") > NCL_THRESHOLD, F.lit(False))
                    | F.coalesce(F.col("tier1_leverage_pct") < TIER1_THRESHOLD, F.lit(False))
                    | (F.coalesce(F.col("is_loss_qtr"), F.lit(False))
                       & F.coalesce(F.col("is_loss_qtr_lag1"), F.lit(False))))
        # Challenger: capital or asset-quality breach only
        .withColumn("is_distressed_strict",
                    F.coalesce(F.col("noncurrent_loans_pct") > NCL_THRESHOLD, F.lit(False))
                    | F.coalesce(F.col("tier1_leverage_pct") < TIER1_THRESHOLD, F.lit(False)))
    )

    # Future window t+1..t+4 (by quarter index, so gaps are handled)
    future = Window.partitionBy("cert").orderBy("q_idx").rangeBetween(1, 4)
    df = (
        df.withColumn("distress_in_next_4q", F.max(F.col("is_distressed").cast("int")).over(future))
        .withColumn("strict_in_next_4q", F.max(F.col("is_distressed_strict").cast("int")).over(future))
        .withColumn("last_future_q", F.max("q_idx").over(future))
    )

    # Failures within 12 months after t (all failure records, not just the first)
    failures = spark.table(f"{SILVER}.fdic_failures").filter("cert is not null").select("cert", "fail_date")
    failed_soon = (
        df.select("cert", "report_date").join(failures, "cert")
        .filter((F.col("fail_date") > F.col("report_date"))
                & (F.col("fail_date") <= F.last_day(F.add_months("report_date", 12))))
        .select("cert", "report_date").distinct().withColumn("fails_within_12m", F.lit(1))
    )
    df = df.join(failed_soon, ["cert", "report_date"], "left").fillna({"fails_within_12m": 0})

    latest_idx = latest.year * 4 + (latest.month - 1) // 3 + 1
    df = (
        df.withColumn("distress_next_4q",
                      F.greatest(F.coalesce("distress_in_next_4q", F.lit(0)), F.col("fails_within_12m")))
        .withColumn("distress_strict_next_4q",
                    F.greatest(F.coalesce("strict_in_next_4q", F.lit(0)), F.col("fails_within_12m")))
        .withColumn("outcome_observed",
                    F.coalesce(F.col("last_future_q") == F.col("q_idx") + 4, F.lit(False))
                    | (F.col("fails_within_12m") == 1))
        .withColumn("label_complete", F.col("q_idx") <= F.lit(latest_idx - 4))
        .withColumn("is_model_row",
                    ~F.col("is_distressed") & F.col("outcome_observed") & F.col("label_complete")
                    & (F.col("total_assets_k") > 0) & ~F.col("is_foreign_office") & ~F.col("is_new_bank"))
    )
    return df.select(
        "cert", "report_date", "bank_name", "state_code", "asset_size_tier", "total_assets_k",
        *FEATURES, "cblr_reporter_flag", "cblr_reporter", "is_foreign_office", "foreign_office_rule",
        "bank_age_years", "is_new_bank", "capital_zero_review", "outcome_observed", "label_complete",
        "is_distressed", "fails_within_12m", "distress_next_4q", "distress_strict_next_4q", "is_model_row",
    )
