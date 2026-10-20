# Power BI dashboard guide — Bank Health

Files in this folder:
- `bank_health_theme.json`: navy theme (View → Themes → Browse for themes)
- `measures.dax`: every DAX measure, calculated table and column, ready to paste
- `bank_health.pbix` + `screenshots/`: saved here when the report is built

## 1. Connect to Snowflake (Import mode)

### 1a. One-time setup: a read-only Power BI user with key-pair login
Snowflake requires MFA for human password logins, and Power BI's password login is
being deprecated. Power BI Desktop's Snowflake connector (implementation 2.0) supports
**key-pair** login, so Power BI gets its own read-only service user.

1. In Git Bash, in the project folder, make a **new** key pair just for Power BI:
   ```bash
   openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out keys/powerbi_rsa_key.p8
   openssl pkey -in keys/powerbi_rsa_key.p8 -pubout -out keys/powerbi_rsa_key.pub
   grep -v "PUBLIC KEY" keys/powerbi_rsa_key.pub | tr -d '\n'; echo
   ```
2. In Snowsight, as ACCOUNTADMIN, run `snowflake/powerbi_user.sql`, then the
   `ALTER USER SVC_POWERBI SET RSA_PUBLIC_KEY = '...'` line with the public key.
   `DESC USER SVC_POWERBI` should show `RSA_PUBLIC_KEY_FP`.

The role `BANK_REPORTER_ROLE` can only read `BANK_HEALTH.MARTS`, including tables dbt
re-creates later.

### 1b. In Power BI Desktop
1. **Get Data → Database → Snowflake → Connect.**
2. **Server**: your account URL without `https://`
   (`<org>-<account>.snowflakecomputing.com`). **Warehouse**: `BANK_WH`.
3. **Advanced options**: Role name `BANK_REPORTER_ROLE`, Database `BANK_HEALTH`.
   Data connectivity mode: **Import**.
4. Credentials: choose **Key pair**. User name `SVC_POWERBI`, upload `keys/powerbi_rsa_key.p8`.
   (No passphrase box appears for an unencrypted key.)
5. Navigator → `BANK_HEALTH` → `MARTS`: tick `DIM_BANK`, `MART_BANK_SCORECARD`,
   `MART_EARLY_WARNING`, `FCT_COMPLAINTS` → **Transform Data**.
6. In Power Query, rename the tables to `Bank`, `Scorecard`, `Early Warning` and
   `Complaints`. In `Complaints`, keep only the columns you'll use (CERT, COMPLAINT_ID,
   DATE_RECEIVED, QUARTER_END_DATE, COMPANY, MATCH_METHOD, IS_MATCHED_BANK, PRODUCT,
   ISSUE, COMPANY_RESPONSE, IS_MONETARY_RELIEF, TIMELY_RESPONSE, SUBMITTED_VIA,
   STATE_CODE). That keeps the 1.97M-row table small. **Close & Apply.**

If you'd rather use your own login: the same dialog accepts your user name with
**Key pair** too, after `ALTER USER <you> SET RSA_PUBLIC_KEY = ...`. Password + MFA may
work interactively but is being deprecated; Microsoft Entra SSO only supports DirectQuery.

`FCT_BANK_QUARTER` isn't needed: `MART_BANK_SCORECARD` has the same grain plus peer
percentiles. Add it later if you need ratios the scorecard leaves out.

## 2. Data model (star schema)

Create the `Quarter` table (Modeling → New table, from `measures.dax`), mark
`Quarter End` as the date column, and sort `Quarter Label` by `Quarter End`.

| From (many) | To (one) | Cardinality | Filter direction |
|---|---|---|---|
| `Scorecard[CERT]` | `Bank[CERT]` | many-to-one | single (Bank → Scorecard) |
| `Complaints[CERT]` | `Bank[CERT]` | many-to-one | single |
| `Early Warning[CERT]` | `Bank[CERT]` | many-to-one (unique on both sides) | single |
| `Scorecard[REPORT_DATE]` | `Quarter[Quarter End]` | many-to-one | single |
| `Complaints[QUARTER_END_DATE]` | `Quarter[Quarter End]` | many-to-one | single |
| `Early Warning[REPORT_DATE]` | `Quarter[Quarter End]` | many-to-one | single |

- `Bank` and `Quarter` are the only dimensions. Use them for slicers (state, size,
  quarter) so one slicer filters every fact table.
- About 70% of complaints have a blank `CERT` (the company isn't an FDIC bank). They
  count in industry totals and sit under "(Blank)" for bank-level visuals.
- Turn off **Auto date/time** (File → Options → Data Load) to keep the file small.
- Delete any relationships Power BI auto-detects that aren't in this table.

## 3. Style (navy finance)

- Import `bank_health_theme.json` first.
- Each page: a **navy sidebar** rectangle (`#0B1F3A`, full height, ~200 px wide) on the
  left, holding the report title in white and a **Page navigator** button group
  (Insert → Buttons → Navigator → Page navigator; text white, selected state `#1F3B63`).
- Page background `#F5F7FA`; white rounded cards come from the theme.
- **Question-style titles** on every visual (e.g. "Which banks are most likely to enter
  distress?").
- Bars: one colour, navy `#1F3B63`, **sorted** descending, **data labels on**.
- KPI cards (the new Card visual): value = KPI measure. Reference label = `Arrow …`
  measure, font colour → fx → *Field value* → `Color …` measure (green `#1E8E5A` /
  red `#C0392B`).
- A slicer on `Quarter[Quarter Label]` (single select) drives the KPI cards. With no
  selection the cards show the latest FDIC quarter (2026 Q2).

## 4. Pages

### Page 1 — Overview: "How healthy is the US banking system?"
| Visual | Fields / measures | Title |
|---|---|---|
| 5 KPI cards (top row) | `Banks Reporting`; `KPI Total Assets $B` + `Arrow Assets`; `KPI Tier 1 %` + `Arrow Tier 1`; `KPI NCL %` + `Arrow NCL`; `KPI ROA %` + `Arrow ROA` | label each card; subtitle `Report Quarter Label` |
| Line chart | X `Quarter[Quarter End]`; Y `Share of Banks with NCL > 3%` and `Median Noncurrent Loans %` (secondary axis) | "How has bank asset quality changed since 2000?" |
| Line chart | X `Quarter[Quarter End]`; Y `Banks Reporting` per quarter (use `Bank Quarters`) | "How many banks are left?" |
| Clustered bar | Y `Bank[BANK_NAME]`; X `Complaints per $1B Deposits`; visual filter: `Scorecard[ASSET_SIZE_TIER]` is 4 or 5; Top N 10; quarter = 2026 Q2 | "Which large banks get the most complaints per $1B of deposits?" |
| Column chart | X `Scorecard[ASSET_SIZE_TIER]`; Y `Bank Quarters` (at the selected quarter) | "How many banks are in each size group?" |
| KPI card | `KPI Complaints per $1B` + `Arrow Complaints` | "Complaints per $1B deposits" |

### Page 2 — Early warning: "Which banks are most likely to enter distress in the next 12 months?"
| Visual | Fields / measures | Title |
|---|---|---|
| KPI cards | `Banks High Risk`, `Banks Elevated Risk`, `Banks Watch`, `Banks Already in Distress`, `Assets High + Elevated $B` | |
| Clustered bar | Y `Early Warning[RISK_BAND]` (sorted by `Band Order`); X `Banks Scored` | "How many banks fall in each risk band?" |
| Table (main) | `Bank[CERT]`, `Bank[BANK_NAME]`, `Bank[STATE_CODE]`, `Early Warning[ASSET_SIZE_TIER]`, `DISTRESS_PROBABILITY` (0.0%, navy data bars), `RISK_BAND`, `REASON_1`, `REASON_2`, `TIER1_LEVERAGE_PCT`, `NONCURRENT_LOANS_PCT`, `ROA_PCT`, `COMPLAINTS_PER_1B_DEPOSITS`; sort by probability desc | "Which banks are most likely to enter distress?" |
| Scatter | X `DISTRESS_PROBABILITY`; Y `COMPLAINTS_PER_1B_DEPOSITS`; details `Bank[BANK_NAME]`; size `TOTAL_ASSETS_K` | "Do higher-risk banks also draw more complaints?" |
| Slicers | `Bank[STATE_CODE]`, `Early Warning[ASSET_SIZE_TIER]`, `Early Warning[RISK_BAND]` (default High + Elevated) | |
| Text box (footer) | `Scores As Of` + "Gradient-boosted model trained 2001–2019, tested 2020–2024: ROC-AUC 0.854, PR-AUC 0.357 vs a 5.6% base rate. Portfolio model on public FDIC data, not a supervisory rating." | |

Right-click a row → **Drill through → Bank detail**.

### Page 3 — Bank detail (drill-through): "How is this bank doing?"
Drill-through well: `Bank[CERT]`. Add a **Back** button (Insert → Buttons → Back).

| Visual | Fields / measures | Title |
|---|---|---|
| Header card | `Selected Bank`; small text `Bank Holding Company` | |
| KPI cards | `KPI Total Assets $B`; `KPI Tier 1 %` + `Arrow Tier 1`; `KPI NCL %` + `Arrow NCL`; `KPI ROA %` + `Arrow ROA`; `Bank Distress Probability` (0.0%) + `Bank Risk Band` | |
| Text cards | `Bank Reason 1/2/3` | "What drives this bank's risk score?" |
| Line chart | X `Quarter[Quarter End]`; Y `Bank NCL %` (navy) and `Peer Median NCL %` (grey `#8BA6C9`, dashed) | "Are bad loans rising faster than at similar-size banks?" |
| Line chart | Y `Bank Tier 1 %` vs `Peer Median Tier 1 %` | "Is capital keeping up with peers?" |
| Line chart | Y `Bank ROA %` vs `Peer Median ROA %` | "Is the bank as profitable as its peers?" |
| Bar chart | Y `Complaints[PRODUCT]`; X `Complaint Count`; sorted, labels | "What do customers complain about?" |
| Column chart | X `Quarter[Quarter Label]` (2023 Q4 onward); Y `Complaint Count` | "Are complaints rising?" |
| Card | `Bank Complaint Peer Percentile` | |

## 5. Before saving
- Refresh once (Home → Refresh) and check `Banks Reporting` = 4,257 for 2026 Q2 and
  `Banks High Risk` = 195.
- Save as `powerbi/bank_health.pbix` and export one PNG screenshot per page to
  `powerbi/screenshots/`.
- A refresh runs queries on `BANK_WH` (XSMALL, auto-suspends after 60 s), so it uses a
  little Snowflake credit.
