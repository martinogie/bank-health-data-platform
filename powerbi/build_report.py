"""Generate the Bank Health Power BI project (PBIP: TMDL semantic model + PBIR report).

Run from the repo root after Power BI Desktop has saved powerbi/BankHealth.pbip once
(that first save provides the .platform files and base theme). Power BI must be closed.

    python powerbi/build_report.py               # model + report
    python powerbi/build_report.py --report-only # pages and theme only, keeps loaded data

It rewrites the semantic model tables, relationships and measures, and the report pages.
Then open powerbi/BankHealth.pbip in Power BI Desktop and click Refresh.
"""
import json
import re
import shutil
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT = "BankHealth"
SM = ROOT / f"{PROJECT}.SemanticModel" / "definition"
RP = ROOT / f"{PROJECT}.Report" / "definition"

# The Snowflake server is a Power Query parameter, so the account address isn't in git.
# expressions.tmdl is created once with a placeholder; set the real value in Power BI
# (Transform data > Edit parameters). The file is then kept out of commits with
#   git update-index --skip-worktree powerbi/BankHealth.SemanticModel/definition/expressions.tmdl
SERVER_PARAM = "SnowflakeServer"
SERVER_PLACEHOLDER = "<your-account>.snowflakecomputing.com"
WAREHOUSE = "BANK_WH"
ROLE = "BANK_REPORTER_ROLE"

# Teal top-bar style
TEAL, TEAL_DARK, TEAL_LIGHT = "#0F766E", "#115E59", "#99D5CF"
INK, MUTED, BG, LINE = "#16302D", "#5F6F6D", "#F6F8F8", "#D9E2E1"
GREEN, RED, GREY = "#0F766E", "#D9480F", "#9AA5A4"


_LID = [0]


def lid():
    """Deterministic lineage tags, so re-running the script does not churn every file."""
    _LID[0] += 1
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"bank-health/{_LID[0]}"))


def nid(prefix=""):
    return (prefix + uuid.uuid4().hex)[:20]


# =============================================================== semantic model
TABLES = {
    # name: (snowflake table, [(column, type, format/None, summarize)])
    "Bank": ("DIM_BANK", [
        ("CERT", "int64", "0", "none"), ("BANK_NAME", "string", None, "none"),
        ("CITY", "string", None, "none"), ("STATE_CODE", "string", None, "none"),
        ("STATE_NAME", "string", None, "none"), ("BANK_CLASS", "string", None, "none"),
        ("PRIMARY_REGULATOR", "string", None, "none"), ("HOLDING_COMPANY", "string", None, "none"),
        ("IS_ACTIVE", "boolean", None, "none"), ("HAS_FAILED", "boolean", None, "none"),
        ("LATEST_TOTAL_ASSETS_K", "double", "#,0", "sum"),
        ("LATEST_ASSET_SIZE_TIER", "string", None, "none"),
        ("HAS_CFPB_COMPLAINTS", "boolean", None, "none"),
    ]),
    "Scorecard": ("MART_BANK_SCORECARD", [
        ("CERT", "int64", "0", "none"), ("REPORT_DATE", "dateTime", "d mmm yyyy", "none"),
        ("ASSET_SIZE_TIER", "string", None, "none"), ("PEERS_IN_TIER", "int64", "#,0", "none"),
        ("TOTAL_ASSETS_K", "double", "#,0", "sum"), ("TOTAL_DEPOSITS_K", "double", "#,0", "sum"),
        ("NET_INCOME_QTR_K", "double", "#,0", "sum"), ("ROA_PCT", "double", "0.00", "none"),
        ("NONCURRENT_LOANS_PCT", "double", "0.00", "none"),
        ("TIER1_LEVERAGE_PCT", "double", "0.00", "none"),
        ("EFFICIENCY_RATIO_PCT", "double", "0.0", "none"),
        ("CRE_TO_EQUITY_PCT", "double", "0.0", "none"),
        ("LOAN_GROWTH_YOY_PCT", "double", "0.0", "none"),
        ("DEPOSIT_GROWTH_YOY_PCT", "double", "0.0", "none"),
        ("COMPLAINT_DATA_COVERED", "boolean", None, "none"),
        ("COMPLAINTS", "int64", "#,0", "sum"),
        ("COMPLAINTS_PER_1B_DEPOSITS", "double", "0.0", "none"),
        ("COMPLAINTS_PER_1B_DEPOSITS_PEER_PCTILE", "double", "0%", "none"),
    ]),
    "Early Warning": ("MART_EARLY_WARNING", [
        ("CERT", "int64", "0", "none"), ("BANK_NAME", "string", None, "none"),
        ("CITY", "string", None, "none"), ("STATE_CODE", "string", None, "none"),
        ("REPORT_DATE", "dateTime", "d mmm yyyy", "none"),
        ("ASSET_SIZE_TIER", "string", None, "none"),
        ("TOTAL_ASSETS_K", "double", "#,0", "sum"),
        ("DISTRESS_PROBABILITY", "double", "0.0%", "none"),
        ("RISK_PERCENTILE", "double", "0%", "none"), ("RISK_RANK", "int64", "#,0", "none"),
        ("RISK_BAND", "string", None, "none"), ("ALREADY_IN_DISTRESS", "boolean", None, "none"),
        ("REASON_1", "string", None, "none"), ("REASON_2", "string", None, "none"),
        ("REASON_3", "string", None, "none"),
        ("TIER1_LEVERAGE_PCT", "double", "0.00", "none"),
        ("NONCURRENT_LOANS_PCT", "double", "0.00", "none"),
        ("ROA_PCT", "double", "0.00", "none"),
        ("COMPLAINTS", "int64", "#,0", "sum"),
        ("COMPLAINTS_PER_1B_DEPOSITS", "double", "0.0", "none"),
        ("MODEL_VERSION", "int64", "0", "none"),
    ]),
    "Complaints": ("FCT_COMPLAINTS", [
        ("CERT", "int64", "0", "none"), ("DATE_RECEIVED", "dateTime", "d mmm yyyy", "none"),
        ("QUARTER_END_DATE", "dateTime", "d mmm yyyy", "none"),
        ("COMPANY", "string", None, "none"), ("IS_MATCHED_BANK", "boolean", None, "none"),
        ("PRODUCT", "string", None, "none"), ("ISSUE", "string", None, "none"),
        ("COMPANY_RESPONSE", "string", None, "none"),
        ("IS_MONETARY_RELIEF", "boolean", None, "none"),
        ("TIMELY_RESPONSE", "boolean", None, "none"),
        ("SUBMITTED_VIA", "string", None, "none"), ("STATE_CODE", "string", None, "none"),
    ]),
}

# Reasons come from the model as "past_due_30_89_to_loans_pct = 8.10 (median 0.34)"; make them readable.
PRETTY = ("VAR r = SELECTEDVALUE ( 'Early Warning'[{c}] )\n"
          "RETURN SUBSTITUTE ( SUBSTITUTE ( SUBSTITUTE ( r, \"30_89\", \"30-89 days\" ), \"_pct\", \" %\" ), \"_\", \" \" )")

# measures: (table, name, expression, format)
MEASURES = [
    ("Scorecard", "Bank Quarters", "COUNTROWS ( Scorecard )", "#,0"),
    ("Scorecard", "Total Assets $B", "DIVIDE ( SUM ( Scorecard[TOTAL_ASSETS_K] ), 1e6 )", "#,0"),
    ("Scorecard", "Median Tier 1 Leverage %", "MEDIAN ( Scorecard[TIER1_LEVERAGE_PCT] )", "0.00"),
    ("Scorecard", "Median Noncurrent Loans %", "MEDIAN ( Scorecard[NONCURRENT_LOANS_PCT] )", "0.00"),
    ("Scorecard", "Industry ROA %",
     "DIVIDE (\n    SUMX ( Scorecard, Scorecard[ROA_PCT] * Scorecard[TOTAL_ASSETS_K] ),\n"
     "    SUMX ( FILTER ( Scorecard, NOT ISBLANK ( Scorecard[ROA_PCT] ) ), Scorecard[TOTAL_ASSETS_K] )\n)", "0.00"),
    ("Scorecard", "Share of Banks with NCL > 3%",
     "DIVIDE ( COUNTROWS ( FILTER ( Scorecard, Scorecard[NONCURRENT_LOANS_PCT] > 3 ) ), [Bank Quarters] )", "0.0%"),
    ("Scorecard", "Complaints (covered)",
     "CALCULATE ( SUM ( Scorecard[COMPLAINTS] ), Scorecard[COMPLAINT_DATA_COVERED] = TRUE () )", "#,0"),
    ("Scorecard", "Complaints per $1B Deposits",
     "DIVIDE (\n    [Complaints (covered)],\n    CALCULATE ( SUM ( Scorecard[TOTAL_DEPOSITS_K] ), Scorecard[COMPLAINT_DATA_COVERED] = TRUE () ) / 1e6\n)", "0.0"),
    ("Complaints", "Complaint Count", "COUNTROWS ( Complaints )", "#,0"),
    ("Complaints", "Monetary Relief %",
     "DIVIDE ( CALCULATE ( [Complaint Count], Complaints[IS_MONETARY_RELIEF] = TRUE () ), [Complaint Count] )", "0.0%"),
    ("Scorecard", "Report Quarter",
     "VAR selected = MAX ( 'Quarter'[Quarter End] )\nVAR latest = CALCULATE ( MAX ( Scorecard[REPORT_DATE] ), REMOVEFILTERS ( 'Quarter' ) )\nRETURN MIN ( selected, latest )", "d mmm yyyy"),
    ("Scorecard", "Report Quarter Label",
     "VAR q = [Report Quarter]\nRETURN \"Quarter: \" & YEAR ( q ) & \" Q\" & ROUNDUP ( MONTH ( q ) / 3, 0 )", None),
    ("Scorecard", "Previous Quarter", "EOMONTH ( [Report Quarter], -3 )", "d mmm yyyy"),
]


def kpi(base, name, fmt):
    return [
        ("Scorecard", name,
         f"VAR q = [Report Quarter]\nRETURN CALCULATE ( [{base}], REMOVEFILTERS ( 'Quarter' ), Scorecard[REPORT_DATE] = q )", fmt),
        ("Scorecard", name + " PQ",
         f"VAR q = [Previous Quarter]\nRETURN CALCULATE ( [{base}], REMOVEFILTERS ( 'Quarter' ), Scorecard[REPORT_DATE] = q )", fmt),
    ]


MEASURES += kpi("Bank Quarters", "Banks Reporting", "#,0")
MEASURES += kpi("Total Assets $B", "KPI Total Assets $B", "\\$#,0.0B")
MEASURES += [("Scorecard", "KPI Total Assets $T", "DIVIDE ( [KPI Total Assets $B], 1000 )", "\\$#,0.00T")]
MEASURES += kpi("Median Tier 1 Leverage %", "KPI Tier 1 %", "0.00\\%")
MEASURES += kpi("Median Noncurrent Loans %", "KPI NCL %", "0.00\\%")
MEASURES += kpi("Industry ROA %", "KPI ROA %", "0.00\\%")
MEASURES += kpi("Complaints per $1B Deposits", "KPI Complaints per $1B", "0.0")


def arrow(name, cur, pq, kind, up_good, unit_fmt, suffix):
    # kind: 'pct' relative change, 'pp' / 'abs' difference
    if kind == "pct":
        d = f"DIVIDE ( [{cur}] - [{pq}], [{pq}] )"
    else:
        d = f"[{cur}] - [{pq}]"
    good = ">= 0" if up_good else "<= 0"
    return [
        ("Scorecard", f"Arrow {name}",
         f"VAR d = {d}\nRETURN IF ( ISBLANK ( [{pq}] ), \"\",\n"
         f"    IF ( d >= 0, \"▲ \", \"▼ \" ) & FORMAT ( ABS ( d ), \"{unit_fmt}\" ) & \"{suffix} vs prior quarter\" )", None),
        ("Scorecard", f"Color {name}",
         f"VAR d = {d}\nRETURN IF ( d {good}, \"{GREEN}\", \"{RED}\" )", None),
    ]


MEASURES += arrow("Banks", "Banks Reporting", "Banks Reporting PQ", "abs", True, "#,0", " banks")
MEASURES += arrow("Assets", "KPI Total Assets $B", "KPI Total Assets $B PQ", "pct", True, "0.0%", "")
MEASURES += arrow("Tier 1", "KPI Tier 1 %", "KPI Tier 1 % PQ", "pp", True, "0.00", " pp")
MEASURES += arrow("NCL", "KPI NCL %", "KPI NCL % PQ", "pp", False, "0.00", " pp")
MEASURES += arrow("ROA", "KPI ROA %", "KPI ROA % PQ", "pp", True, "0.00", " pp")
MEASURES += arrow("Complaints", "KPI Complaints per $1B", "KPI Complaints per $1B PQ", "abs", False, "0.0", "")

MEASURES += [
    ("Early Warning", "Banks In Band", "COUNTROWS ( 'Early Warning' )", "#,0"),
    ("Early Warning", "Banks Scored",
     "CALCULATE ( COUNTROWS ( 'Early Warning' ), NOT 'Early Warning'[RISK_BAND] IN { \"New bank – not scored\", "
     "\"Office of a foreign bank – not scored\" } )", "#,0"),
    ("Early Warning", "Banks Not Scored",
     "CALCULATE ( COUNTROWS ( 'Early Warning' ), 'Early Warning'[RISK_BAND] IN { \"New bank – not scored\", "
     "\"Office of a foreign bank – not scored\" } )", "#,0"),
    ("Early Warning", "Banks High Risk", "CALCULATE ( [Banks Scored], 'Early Warning'[RISK_BAND] = \"High\" )", "#,0"),
    ("Early Warning", "Banks Elevated Risk", "CALCULATE ( [Banks Scored], 'Early Warning'[RISK_BAND] = \"Elevated\" )", "#,0"),
    ("Early Warning", "Banks Watch", "CALCULATE ( [Banks Scored], 'Early Warning'[RISK_BAND] = \"Watch\" )", "#,0"),
    ("Early Warning", "Banks Already in Distress",
     "CALCULATE ( [Banks Scored], 'Early Warning'[RISK_BAND] = \"Already in distress\" )", "#,0"),
    ("Early Warning", "Assets High + Elevated $B",
     "DIVIDE (\n    CALCULATE ( SUM ( 'Early Warning'[TOTAL_ASSETS_K] ), 'Early Warning'[RISK_BAND] IN { \"High\", \"Elevated\" } ),\n    1e6\n)",
     "\\$#,0B"),
    ("Early Warning", "Avg Distress Probability", "AVERAGE ( 'Early Warning'[DISTRESS_PROBABILITY] )", "0.0%"),
    ("Early Warning", "Avg Complaints per $1B", "AVERAGE ( 'Early Warning'[COMPLAINTS_PER_1B_DEPOSITS] )", "0.0"),
    ("Early Warning", "Scores As Of",
     "\"Scores as of \" & FORMAT ( MAX ( 'Early Warning'[REPORT_DATE] ), \"d mmm yyyy\" ) & \" · model v\" & MAX ( 'Early Warning'[MODEL_VERSION] ) & \" · \" & FORMAT ( [Banks Scored], \"#,0\" ) & \" banks scored\"", None),
    ("Bank", "Selected Bank",
     "VAR b = SELECTEDVALUE ( Bank[BANK_NAME] )\nRETURN IF ( ISBLANK ( b ), \"Right-click a bank on Early warning → Drill through → Bank detail\",\n    b & \" · \" & SELECTEDVALUE ( Bank[CITY] ) & \", \" & SELECTEDVALUE ( Bank[STATE_CODE] ) & \" · CERT \" & SELECTEDVALUE ( Bank[CERT] ) )", None),
    ("Bank", "Bank Holding Company",
     "VAR h = SELECTEDVALUE ( Bank[HOLDING_COMPANY] )\nRETURN IF ( ISBLANK ( h ), \"No holding company on file\", \"Holding company: \" & h )", None),
    ("Early Warning", "Bank Risk Band", "\"Risk band: \" & SELECTEDVALUE ( 'Early Warning'[RISK_BAND], \"Not scored\" )", None),
    ("Early Warning", "Bank Distress Probability", "SELECTEDVALUE ( 'Early Warning'[DISTRESS_PROBABILITY] )", "0.0%"),
    ("Early Warning", "Main Reason", PRETTY.format(c="REASON_1"), None),
    ("Early Warning", "Second Reason", PRETTY.format(c="REASON_2"), None),
    ("Early Warning", "Third Reason", PRETTY.format(c="REASON_3"), None),
    ("Early Warning", "Bank Reason 1", "\"1. \" & COALESCE ( [Main Reason], \"—\" )", None),
    ("Early Warning", "Bank Reason 2", "\"2. \" & COALESCE ( [Second Reason], \"—\" )", None),
    ("Early Warning", "Bank Reason 3", "\"3. \" & COALESCE ( [Third Reason], \"—\" )", None),
    ("Scorecard", "Bank NCL %", "SELECTEDVALUE ( Scorecard[NONCURRENT_LOANS_PCT] )", "0.00"),
    ("Scorecard", "Bank Tier 1 %", "SELECTEDVALUE ( Scorecard[TIER1_LEVERAGE_PCT] )", "0.00"),
    ("Scorecard", "Bank ROA %", "SELECTEDVALUE ( Scorecard[ROA_PCT] )", "0.00"),
]


def peer(col, name):
    return ("Scorecard", name,
            f"VAR tier = SELECTEDVALUE ( Scorecard[ASSET_SIZE_TIER] )\n"
            f"RETURN IF ( ISBLANK ( tier ), BLANK (),\n"
            f"    CALCULATE ( MEDIAN ( Scorecard[{col}] ), REMOVEFILTERS ( Bank ), REMOVEFILTERS ( 'Early Warning' ), REMOVEFILTERS ( Scorecard[CERT] ),\n"
            f"        Scorecard[ASSET_SIZE_TIER] = tier ) )", "0.00")


MEASURES += [
    peer("NONCURRENT_LOANS_PCT", "Peer Median NCL %"),
    peer("TIER1_LEVERAGE_PCT", "Peer Median Tier 1 %"),
    peer("ROA_PCT", "Peer Median ROA %"),
    ("Scorecard", "Bank Complaint Peer Percentile",
     "VAR q = [Report Quarter]\nVAR p = CALCULATE ( SELECTEDVALUE ( Scorecard[COMPLAINTS_PER_1B_DEPOSITS_PEER_PCTILE] ), REMOVEFILTERS ( 'Quarter' ), Scorecard[REPORT_DATE] = q )\n"
     "RETURN IF ( ISBLANK ( p ) || p = 0, \"No CFPB complaints matched to this bank\", \"Higher complaint rate than \" & FORMAT ( p, \"0%\" ) & \" of size peers\" )", None),
]

QUARTER_DAX = """
ADDCOLUMNS (
    SELECTCOLUMNS ( GENERATESERIES ( 1, 108, 1 ), "n", [Value] ),
    "Quarter End", EOMONTH ( DATE ( 2000, 1, 1 ), [n] * 3 - 1 ),
    "Year", YEAR ( EOMONTH ( DATE ( 2000, 1, 1 ), [n] * 3 - 1 ) ),
    "Quarter Label",
        YEAR ( EOMONTH ( DATE ( 2000, 1, 1 ), [n] * 3 - 1 ) ) & " Q"
            & ROUNDUP ( MONTH ( EOMONTH ( DATE ( 2000, 1, 1 ), [n] * 3 - 1 ) ) / 3, 0 )
)""".strip()


def q(name):
    return f"'{name}'" if re.search(r"[^A-Za-z0-9_]", name) else name


def tmdl_expr(expr, indent):
    lines = expr.split("\n")
    if len(lines) == 1:
        return " " + lines[0]
    pad = "\t" * indent
    return "\n" + "\n".join(pad + l for l in lines)


def m_query(sf_table, cols):
    names = ", ".join(f'"{c[0]}"' for c in cols)
    return [
        "let",
        f'    Source = Snowflake.Databases({SERVER_PARAM}, "{WAREHOUSE}", [Role = "{ROLE}", Implementation = "2.0"]),',
        '    BANK_HEALTH_Database = Source{[Name = "BANK_HEALTH", Kind = "Database"]}[Data],',
        '    MARTS_Schema = BANK_HEALTH_Database{[Name = "MARTS", Kind = "Schema"]}[Data],',
        f'    Data = MARTS_Schema{{[Name = "{sf_table}", Kind = "Table"]}}[Data],',
        f"    Kept = Table.SelectColumns(Data, {{{names}}})" + ("," if sf_table == "MART_EARLY_WARNING" else ""),
    ] + ([
        '    Ordered = Table.AddColumn(Kept, "BAND_ORDER", each if [RISK_BAND] = "High" then 1 else if [RISK_BAND] = "Elevated" then 2 '
        'else if [RISK_BAND] = "Watch" then 3 else if [RISK_BAND] = "Low" then 4 else if [RISK_BAND] = "Already in distress" then 5 '
        'else if [RISK_BAND] = "New bank – not scored" then 6 else 7, Int64.Type)',
        "in",
        "    Ordered",
    ] if sf_table == "MART_EARLY_WARNING" else ["in", "    Kept"])


def write_table(name, sf_table, cols):
    out = [f"table {q(name)}", f"\tlineageTag: {lid()}", ""]
    for tname, mname, expr, fmt in MEASURES:
        if tname != name:
            continue
        out.append(f"\tmeasure {q(mname)} ={tmdl_expr(expr, 3)}")
        if fmt and fmt != "DYN_MONEY":
            out.append(f"\t\tformatString: {fmt}")
        out.append(f"\t\tlineageTag: {lid()}")
        if fmt == "DYN_MONEY":   # value is in $ billions: show $24.6T or $812.3B (child object goes last)
            out.append("")
            out.append('\t\tformatStringDefinition = IF ( SELECTEDMEASURE () >= 1000, "\\$#,0.0,\\T", "\\$#,0.0\\B" )')
        out.append("")
    for c, typ, fmt, summ in cols:
        out.append(f"\tcolumn {q(c)}")
        out.append(f"\t\tdataType: {typ}")
        if fmt:
            out.append(f"\t\tformatString: {fmt}")
        out.append(f"\t\tlineageTag: {lid()}")
        out.append(f"\t\tsummarizeBy: {summ}")
        out.append(f"\t\tsourceColumn: {c}")
        if name == "Early Warning" and c == "RISK_BAND":
            out.append("\t\tsortByColumn: BAND_ORDER")
        out.append("")
        out.append("\t\tannotation SummarizationSetBy = User")
        out.append("")
    if name == "Early Warning":
        out += [
            "\tcolumn BAND_ORDER",
            "\t\tdataType: int64",
            "\t\tisHidden",
            f"\t\tlineageTag: {lid()}",
            "\t\tsummarizeBy: none",
            "\t\tsourceColumn: BAND_ORDER",
            "",
        ]
    out.append(f"\tpartition {q(name)} = m")
    out.append("\t\tmode: import")
    out.append("\t\tsource =")
    out += ["\t\t\t\t" + l for l in m_query(sf_table, cols)]
    out.append("")
    out.append("\tannotation PBI_ResultType = Table")
    out.append("")
    (SM / "tables" / f"{name}.tmdl").write_text("\n".join(out), encoding="utf-8")


def write_quarter():
    out = ["table Quarter", f"\tlineageTag: {lid()}", "",
           "\tcolumn 'Quarter End'", "\t\tdataType: dateTime", "\t\tformatString: mmm yyyy",
           f"\t\tlineageTag: {lid()}", "\t\tsummarizeBy: none", "\t\tsourceColumn: [Quarter End]", "",
           "\tcolumn Year", "\t\tdataType: int64", "\t\tformatString: 0",
           f"\t\tlineageTag: {lid()}", "\t\tsummarizeBy: none", "\t\tsourceColumn: [Year]", "",
           "\tcolumn 'Quarter Label'", "\t\tdataType: string",
           f"\t\tlineageTag: {lid()}", "\t\tsummarizeBy: none", "\t\tsourceColumn: [Quarter Label]",
           "\t\tsortByColumn: 'Quarter End'", "",
           "\tcolumn n", "\t\tdataType: int64", "\t\tisHidden",
           f"\t\tlineageTag: {lid()}", "\t\tsummarizeBy: none", "\t\tsourceColumn: [n]", "",
           "\tpartition Quarter = calculated", "\t\tmode: import", "\t\tsource ="]
    out += ["\t\t\t\t" + l for l in QUARTER_DAX.split("\n")]
    out += ["", "\tannotation PBI_Id = " + uuid.uuid4().hex, ""]
    (SM / "tables" / "Quarter.tmdl").write_text("\n".join(out), encoding="utf-8")


RELS = [
    ("Scorecard", "CERT", "Bank", "CERT"),
    ("Complaints", "CERT", "Bank", "CERT"),
    ("'Early Warning'", "CERT", "Bank", "CERT"),
    ("Scorecard", "REPORT_DATE", "Quarter", "'Quarter End'"),
    ("Complaints", "QUARTER_END_DATE", "Quarter", "'Quarter End'"),
]
# 'Early Warning' holds one quarter, so it has no Quarter relationship. Its link to Bank filters both ways,
# so drilling through on 'Early Warning'[CERT] filters Bank, Scorecard and Complaints too.
BOTH_WAYS = {("'Early Warning'", "CERT")}


def write_model():
    out = []
    for ft, fc, tt, tc in RELS:
        rel = [f"relationship {lid()}"]
        if (ft, fc) in BOTH_WAYS:
            rel.append("\tcrossFilteringBehavior: bothDirections")
        out += rel + [f"\tfromColumn: {ft}.{fc}", f"\ttoColumn: {tt}.{tc}", ""]
    (SM / "relationships.tmdl").write_text("\n".join(out), encoding="utf-8")

    model = (SM / "model.tmdl").read_text(encoding="utf-8")
    model = re.sub(r"\nref table .*", "", model)
    model = re.sub(r"\nannotation PBI_QueryOrder = .*", "", model)
    model = model.replace("__PBI_TimeIntelligenceEnabled = 1", "__PBI_TimeIntelligenceEnabled = 0")
    model = model.rstrip() + "\n\n"
    order = ["Bank", "Scorecard", "Early Warning", "Complaints"]
    model += "annotation PBI_QueryOrder = " + json.dumps([SERVER_PARAM] + order) + "\n\n"
    for t in order + ["Quarter"]:
        model += f"ref table {q(t)}\n"
    (SM / "model.tmdl").write_text(model, encoding="utf-8")


def write_expressions():
    """The SnowflakeServer parameter. Created only if missing, so a rebuild never
    overwrites the real address you entered in Power BI."""
    path = SM / "expressions.tmdl"
    if path.exists():
        return
    path.write_text(
        f'expression {SERVER_PARAM} = "{SERVER_PLACEHOLDER}" meta [IsParameterQuery=true, '
        'Type="Text", IsParameterQueryRequired=true]\n'
        f"\tlineageTag: {uuid.uuid5(uuid.NAMESPACE_URL, 'bank-health/param/' + SERVER_PARAM)}\n\n"
        "\tannotation PBI_ResultType = Text\n\n",
        encoding="utf-8",
    )


def build_model():
    tdir = SM / "tables"
    for f in tdir.glob("*.tmdl"):
        f.unlink()  # includes Power BI's auto date tables (auto date/time is switched off)
    for name, (sf, cols) in TABLES.items():
        write_table(name, sf, cols)
    write_quarter()
    write_model()
    write_expressions()


# =============================================================== report helpers
W_PAGE, H_PAGE = 1280, 720
L, R = 16, 1264            # content left / right edge
CW = R - L                 # content width
DEFAULT = {"id": "default"}


def lit(v):
    if isinstance(v, bool):
        return {"expr": {"Literal": {"Value": "true" if v else "false"}}}
    if isinstance(v, (int, float)):
        return {"expr": {"Literal": {"Value": f"{v}D"}}}
    return {"expr": {"Literal": {"Value": "'" + str(v).replace("'", "''") + "'"}}}


def color(c):
    return {"solid": {"color": lit(c)}}


def col(entity, prop):
    return {"Column": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}}


def mea(entity, prop):
    return {"Measure": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}}


def proj(field, display=None, active=None):
    kind = next(iter(field))
    if kind == "Aggregation":
        inner = field["Aggregation"]["Expression"]["Column"]
        fname = {0: "Sum", 1: "Average", 2: "Count", 3: "Min", 4: "Max"}[field["Aggregation"]["Function"]]
        ref = f"{fname}({inner['Expression']['SourceRef']['Entity']}.{inner['Property']})"
        native = f"{fname} of {inner['Property']}"
    else:
        ent = field[kind]["Expression"]["SourceRef"]["Entity"]
        ref = f"{ent}.{field[kind]['Property']}"
        native = field[kind]["Property"]
    p = {"field": field, "queryRef": ref, "nativeQueryRef": native}
    if display:
        p["displayName"] = display
    if active is not None:
        p["active"] = active
    return p


def props(**kw):
    return [{"properties": {k: v for k, v in kw.items()}}]


def no_chrome(title=False):
    return {"background": props(show=lit(False)), "border": props(show=lit(False)),
            "dropShadow": props(show=lit(False)), "title": props(show=lit(title))}


def card_chrome(title=None, bg="#FFFFFF"):
    o = {"background": props(show=lit(True), color=color(bg), transparency=lit(0)),
         "border": props(show=lit(True), color=color(LINE), radius=lit(6), width=lit(1)),
         "dropShadow": props(show=lit(False))}
    o["subTitle"] = props(show=lit(False))
    if title:
        o["title"] = props(show=lit(True), text=lit(title), fontSize=lit(11), bold=lit(True),
                           fontColor=color(INK), titleWrap=lit(True), fontFamily=lit("Segoe UI Semibold"))
    else:
        o["title"] = props(show=lit(False))
    return o


class Page:
    def __init__(self, name, display, hidden=False):
        self.name, self.display, self.hidden = name, display, hidden
        self.visuals, self.z, self.filters, self.binding = [], 0, [], None

    def add(self, vtype, x, y, w, h, query=None, objects=None, vco=None, sort=None, filters=None):
        self.z += 1000
        v = {"visualType": vtype}
        if query:
            v["query"] = {"queryState": query}
            if sort:
                v["query"]["sortDefinition"] = sort
        if objects:
            v["objects"] = objects
        if vco:
            v["visualContainerObjects"] = vco
        v["drillFilterOtherVisuals"] = True
        c = {"$schema": "", "name": nid("v"),
             "position": {"x": round(x, 1), "y": round(y, 1), "z": self.z, "height": round(h, 1),
                          "width": round(w, 1), "tabOrder": self.z},
             "visual": v}
        if filters:
            c["filterConfig"] = {"filters": filters}
        self.visuals.append(c)
        return c


def textbox(page, x, y, w, h, paras, bg=None):
    vco = no_chrome()
    if bg:
        vco["background"] = props(show=lit(True), color=color(bg), transparency=lit(0))
    vco["padding"] = props(top=lit(0), bottom=lit(0), left=lit(0), right=lit(0))
    p = [{"textRuns": [{"value": t, "textStyle": s} for t, s in runs]} for runs in paras]
    return page.add("textbox", x, y, w, h, objects={"general": [{"properties": {"paragraphs": p}}]},
                    vco=vco)


def card(page, x, y, w, h, field, size=22, label_color=None, bold=True, wrap=False, value_color=INK,
         chrome=None):
    objs = {
        "labels": props(color=label_color or color(value_color), fontSize=lit(size), bold=lit(bold),
                        fontFamily=lit("Segoe UI Semibold" if bold else "Segoe UI")),
        "categoryLabels": props(show=lit(False)),
        "wordWrap": props(show=lit(wrap)),
    }
    objs["labels"][0]["properties"]["labelDisplayUnits"] = lit(1)
    vco = chrome or no_chrome()
    vco.setdefault("padding", props(top=lit(0), bottom=lit(0), left=lit(2), right=lit(2)))
    return page.add("card", x, y, w, h, query={"Values": {"projections": [proj(field)]}}, objects=objs, vco=vco)


def kpi_tile(page, x, y, w, h, label, value_field, arrow_entity=None, arrow=None, colour=None,
             value_color=INK):
    """White tile: a rounded rectangle behind, a label + big value card, and a coloured change line below."""
    tile(page, x, y, w, h)
    chrome = no_chrome(title=True)
    chrome["title"] = props(show=lit(True), text=lit(label), fontSize=lit(10), fontColor=color(MUTED),
                            fontFamily=lit("Segoe UI"), titleWrap=lit(False))
    vh = h - 34 if arrow else h - 6
    card(page, x + 4, y + 2, w - 8, vh, value_field, size=20, value_color=value_color, chrome=chrome)
    if arrow:
        lc = {"solid": {"color": {"expr": mea(arrow_entity, colour)}}}
        card(page, x + 4, y + h - 32, w - 8, 30, mea(arrow_entity, arrow), size=9, label_color=lc)


def tile(page, x, y, w, h, fill="#FFFFFF"):
    objs = {
        "shape": [{"properties": {"tileShape": lit("rectangleRounded"), "roundEdge": lit(8)}}],
        "fill": [{"properties": {"show": lit(True)}},
                 {"properties": {"fillColor": color(fill), "transparency": lit(0)}, "selector": DEFAULT}],
        "outline": [{"properties": {"show": lit(True)}},
                    {"properties": {"lineColor": color(LINE), "weight": lit(1)}, "selector": DEFAULT}],
    }
    return page.add("shape", x, y, w, h, objects=objs, vco=no_chrome())


def topbar(page, active):
    textbox(page, 0, 0, W_PAGE, 64, [[("BANK HEALTH", {"fontSize": "15pt", "fontWeight": "bold",
                                                       "color": "#FFFFFF", "fontFamily": "Segoe UI Semibold"})],
                                      [("US banks · FDIC call reports + CFPB complaints",
                                        {"fontSize": "9pt", "color": TEAL_LIGHT})]], bg=TEAL)
    # nudge the text in from the edge with a second, transparent layer is not needed: use padding
    page.visuals[-1]["visual"]["visualContainerObjects"]["padding"] = props(
        top=lit(10), bottom=lit(6), left=lit(18), right=lit(0))
    x = 330
    for pname, label in [("overview", "Overview"), ("earlywarning", "Early warning")]:
        sel = pname == active
        objs = {
            "icon": [{"properties": {"show": lit(False)}, "selector": DEFAULT}],
            "text": [{"properties": {"show": lit(True)}},
                     {"properties": {"text": lit(label), "fontColor": color(TEAL if sel else "#FFFFFF"),
                                     "fontSize": lit(11), "bold": lit(True)}, "selector": DEFAULT}],
            "fill": [{"properties": {"show": lit(True)}},
                     {"properties": {"fillColor": color("#FFFFFF" if sel else TEAL_DARK), "transparency": lit(0)},
                      "selector": DEFAULT}],
            "outline": [{"properties": {"show": lit(False)}}],
            "shape": [{"properties": {"tileShape": lit("rectangleRounded"), "roundEdge": lit(16)}}],
        }
        vco = no_chrome()
        vco["visualLink"] = props(show=lit(True), type=lit("PageNavigation"), navigationSection=lit(pname))
        page.add("actionButton", x, 14, 150, 36, objects=objs, vco=vco)
        x += 160


def header(page, title, subtitle, width=900):
    textbox(page, L, 72, width, 50, [
        [(title, {"fontSize": "16pt", "fontWeight": "bold", "color": INK, "fontFamily": "Segoe UI Semibold"})],
        [(subtitle, {"fontSize": "9pt", "color": MUTED})],
    ])


def chart_style(vtype, title, c=TEAL, labels=True):
    objs = {}
    if vtype in ("clusteredBarChart", "clusteredColumnChart"):
        objs["dataPoint"] = props(fill=color(c))
        objs["labels"] = props(show=lit(labels), color=color(INK), fontSize=lit(9), labelDisplayUnits=lit(1))
        objs["valueAxis"] = props(show=lit(False), gridlineShow=lit(False))
        objs["categoryAxis"] = props(showAxisTitle=lit(False), fontSize=lit(9), labelColor=color(MUTED))
    if vtype == "lineChart":
        objs["dataPoint"] = props(fill=color(c))
        objs["categoryAxis"] = props(showAxisTitle=lit(False), labelColor=color(MUTED), gridlineShow=lit(False))
        objs["valueAxis"] = props(showAxisTitle=lit(False), labelColor=color(MUTED), gridlineColor=color("#E8EEED"),
                                  gridlineStyle=lit("dotted"))
        objs["lineStyles"] = props(strokeWidth=lit(2))
    return objs, card_chrome(title)


def sort_by(field, desc=True):
    return {"sort": [{"field": field, "direction": "Descending" if desc else "Ascending"}], "isDefaultSort": True}


def in_filter(name, entity, prop, values):
    return {"name": name, "field": col(entity, prop), "type": "Categorical",
            "filter": {"Version": 2, "From": [{"Name": "s", "Entity": entity, "Type": 0}],
                       "Where": [{"Condition": {"In": {
                           "Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": "s"}}, "Property": prop}}],
                           "Values": [[{"Literal": {"Value": "'" + v + "'"}}] for v in values]}}}]}}


def topn_filter(name, entity, prop, n, by_entity, by_measure):
    return {"name": name, "field": col(entity, prop), "type": "TopN",
            "filter": {"Version": 2, "From": [
                {"Name": "subquery", "Expression": {"Subquery": {"Query": {
                    "Version": 2, "From": [{"Name": "b", "Entity": entity, "Type": 0}],
                    "Select": [{"Column": {"Expression": {"SourceRef": {"Source": "b"}}, "Property": prop},
                                "Name": "field"}],
                    "OrderBy": [{"Direction": 2, "Expression": {"Measure": {
                        "Expression": {"SourceRef": {"Entity": by_entity}}, "Property": by_measure}}}],
                    "Top": n}}}, "Type": 2},
                {"Name": "b", "Entity": entity, "Type": 0}],
                "Where": [{"Condition": {"In": {
                    "Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": "b"}}, "Property": prop}}],
                    "Table": {"SourceRef": {"Source": "subquery"}}}}}]}}


def slicer(page, x, y, w, h, field, title, single=False, on_bar=False):
    objs = {"data": props(mode=lit("Dropdown")),
            "selection": props(singleSelect=lit(single), selectAllCheckboxEnabled=lit(not single)),
            "header": props(show=lit(True), text=lit(title), fontColor=color("#FFFFFF" if on_bar else MUTED),
                            textSize=lit(9)),
            "items": props(fontColor=color(INK), textSize=lit(10))}
    vco = no_chrome() if on_bar else card_chrome()
    return page.add("slicer", x, y, w, h, query={"Values": {"projections": [proj(field, active=True)]}},
                    objects=objs, vco=vco)


S, E, B, C, Q = "Scorecard", "Early Warning", "Bank", "Complaints", "Quarter"


# =============================================================== pages
def overview():
    p = Page("overview", "Overview")
    topbar(p, "overview")
    slicer(p, 1080, 6, 184, 54, col(Q, "Quarter Label"), "Quarter (blank = latest)", single=True, on_bar=True)
    header(p, "How healthy is the US banking system?",
           "Every FDIC-insured bank, quarterly since 2000. Cards show the latest quarter, or the one you pick, "
           "with the change from the quarter before.")
    tiles = [
        ("Banks reporting", mea(S, "Banks Reporting"), "Arrow Banks", "Color Banks"),
        ("Total assets", mea(S, "KPI Total Assets $T"), "Arrow Assets", "Color Assets"),
        ("Median Tier 1 leverage", mea(S, "KPI Tier 1 %"), "Arrow Tier 1", "Color Tier 1"),
        ("Median non-current loans", mea(S, "KPI NCL %"), "Arrow NCL", "Color NCL"),
        ("Industry ROA (asset-weighted)", mea(S, "KPI ROA %"), "Arrow ROA", "Color ROA"),
        ("Complaints per $1B deposits", mea(S, "KPI Complaints per $1B"), "Arrow Complaints", "Color Complaints"),
    ]
    g = 12
    w = (CW - 5 * g) / 6
    for i, (lab, f, a, c) in enumerate(tiles):
        kpi_tile(p, L + i * (w + g), 128, w, 112, lab, f, S, a, c)

    half = (CW - g) / 2
    objs, vco = chart_style("lineChart", "What share of banks had bad loans above 3% of their loans?", c=RED)
    p.add("lineChart", L, 252, half, 222,
          query={"Category": {"projections": [proj(col(Q, "Quarter End"), active=True)]},
                 "Y": {"projections": [proj(mea(S, "Share of Banks with NCL > 3%"), "Banks with non-current loans > 3%")]}},
          objects=objs, vco=vco, sort=sort_by(col(Q, "Quarter End"), desc=False))
    objs, vco = chart_style("lineChart", "How many banks are left?")
    p.add("lineChart", L + half + g, 252, half, 222,
          query={"Category": {"projections": [proj(col(Q, "Quarter End"), active=True)]},
                 "Y": {"projections": [proj(mea(S, "Bank Quarters"), "Banks reporting")]}},
          objects=objs, vco=vco, sort=sort_by(col(Q, "Quarter End"), desc=False))

    objs, vco = chart_style("clusteredBarChart",
                            "Which large banks ($10B+) get the most complaints per $1B of deposits? (Oct 2023 onward)")
    p.add("clusteredBarChart", L, 486, half, 222,
          query={"Category": {"projections": [proj(col(B, "BANK_NAME"), active=True)]},
                 "Y": {"projections": [proj(mea(S, "Complaints per $1B Deposits"), "Complaints per $1B deposits")]}},
          objects=objs, vco=vco, sort=sort_by(mea(S, "Complaints per $1B Deposits")),
          filters=[in_filter("tierfilter", S, "ASSET_SIZE_TIER", ["4: $10B-$100B", "5: >$100B"])])
    objs, vco = chart_style("clusteredColumnChart", "How many banks are in each size group? (selected quarter)")
    p.add("clusteredColumnChart", L + half + g, 486, half, 222,
          query={"Category": {"projections": [proj(col(S, "ASSET_SIZE_TIER"), active=True)]},
                 "Y": {"projections": [proj(mea(S, "Banks Reporting"), "Banks")]}},
          objects=objs, vco=vco, sort=sort_by(col(S, "ASSET_SIZE_TIER"), desc=False))
    return p


def early_warning():
    p = Page("earlywarning", "Early warning")
    topbar(p, "earlywarning")
    header(p, "Which banks are most likely to enter distress in the next 12 months?",
           "The model uses FDIC call-report features only; CFPB complaints are shown as context, not model "
           "inputs. Right-click a bank in the table → Drill through → Bank detail.", width=860)
    card(p, 900, 80, 364, 36, mea(E, "Scores As Of"), size=9, bold=False, value_color=MUTED, wrap=True)
    # Band meanings: share of such banks that entered distress within a year, out of sample 2001–2019
    tiles = [("High (≥50% became distressed)", mea(E, "Banks High Risk"), RED),
             ("Elevated (25–50%)", mea(E, "Banks Elevated Risk"), "#E8590C"),
             ("Watch (10–25%)", mea(E, "Banks Watch"), INK),
             ("Already in distress", mea(E, "Banks Already in Distress"), MUTED),
             ("Not scored (new or foreign office)", mea(E, "Banks Not Scored"), MUTED),
             ("Assets in High + Elevated", mea(E, "Assets High + Elevated $B"), INK)]
    g = 12
    w = (CW - (len(tiles) - 1) * g) / len(tiles)
    for i, (lab, f, vc) in enumerate(tiles):
        kpi_tile(p, L + i * (w + g), 128, w, 86, lab, f, value_color=vc)

    lw = 360
    objs, vco = chart_style("clusteredBarChart", "How many banks fall in each risk band?")
    p.add("clusteredBarChart", L, 226, lw, 210,
          query={"Category": {"projections": [proj(col(E, "RISK_BAND"), active=True)]},
                 "Y": {"projections": [proj(mea(E, "Banks In Band"), "Banks")]}},
          objects=objs, vco=vco, sort=sort_by(col(E, "RISK_BAND"), desc=False))

    objs = {"dataPoint": props(fill=color(TEAL)),
            "categoryAxis": props(showAxisTitle=lit(True), titleText=lit("Distress probability"), labelColor=color(MUTED)),
            "valueAxis": props(showAxisTitle=lit(True), titleText=lit("Complaints per $1B deposits"),
                               labelColor=color(MUTED), gridlineColor=color("#E8EEED")),
            "markers": props(markerSize=lit(4))}
    p.add("scatterChart", L, 446, lw, 234,
          query={"Category": {"projections": [proj(col(E, "CERT"), active=True)]},
                 "X": {"projections": [proj(mea(E, "Avg Distress Probability"), "Distress probability")]},
                 "Y": {"projections": [proj(mea(E, "Avg Complaints per $1B"), "Complaints per $1B deposits")]},
                 "Tooltips": {"projections": [proj({"Aggregation": {"Expression": col(E, "BANK_NAME"), "Function": 3}},
                                                   "Bank")]}},
          objects=objs, vco=card_chrome("Do higher-risk banks also draw more complaints?"))

    tx = L + lw + g
    tw = R - tx
    sw = (tw - 2 * g) / 3
    slicer(p, tx, 226, sw, 52, col(E, "RISK_BAND"), "Risk band")
    slicer(p, tx + sw + g, 226, sw, 52, col(E, "ASSET_SIZE_TIER"), "Size group")
    slicer(p, tx + 2 * (sw + g), 226, sw, 52, col(E, "STATE_CODE"), "State")

    cols = [(col(E, "CERT"), "CERT"), (col(E, "BANK_NAME"), "Bank"), (col(E, "STATE_CODE"), "State"),
            (col(E, "ASSET_SIZE_TIER"), "Size"), (col(E, "DISTRESS_PROBABILITY"), "Probability"),
            (col(E, "RISK_BAND"), "Band"), (mea(E, "Main Reason"), "Main reason"),
            (mea(E, "Second Reason"), "Second reason"), (col(E, "TIER1_LEVERAGE_PCT"), "Tier 1 %"),
            (col(E, "NONCURRENT_LOANS_PCT"), "NCL %")]
    objs = {
        "columnHeaders": props(fontColor=color(INK), backColor=color("#E6F2F1"), fontSize=lit(9),
                               wordWrap=lit(True), bold=lit(True)),
        "values": props(fontColorPrimary=color(INK), backColorPrimary=color("#FFFFFF"),
                        backColorSecondary=color(BG), fontSize=lit(9)),
        "grid": props(gridHorizontal=lit(True), gridHorizontalColor=color("#EDF1F1"), rowPadding=lit(3)),
        "columnFormatting": [{"properties": {"dataBars": {"positiveColor": color("#F4B9A3"), "negativeColor": color(RED),
                                                            "axisColor": color(INK), "reverseDirection": False,
                                                            "hideText": False}},
                              "selector": {"metadata": "Early Warning.DISTRESS_PROBABILITY"}}],
    }
    p.add("tableEx", tx, 288, tw, 392, query={"Values": {"projections": [proj(f, d) for f, d in cols]}},
          objects=objs, vco=card_chrome("Which banks are most likely to enter distress? (banks not yet in distress)"),
          sort=sort_by(col(E, "DISTRESS_PROBABILITY")),
          filters=[in_filter("notyet", E, "RISK_BAND", ["High", "Elevated", "Watch", "Low"])])

    textbox(p, L, 688, CW, 28, [[(
        "Model v5: gradient boosting on FDIC call-report ratios, trained on 2001–2019, tested once on 2020–2024: "
        "ROC-AUC 0.8515, PR-AUC 0.333 against a 5.5% base rate (6.0× random); 52.6% of distress cases fall in the "
        "riskiest 10%. Bands are fixed probability cut-offs. Banks under 3 years old and offices of foreign "
        "banks are not scored. Portfolio model on public data, not a supervisory rating.",
        {"fontSize": "8pt", "color": MUTED})]])
    return p


def bank_detail():
    p = Page("bankdetail", "Bank detail", hidden=True)
    topbar(p, "bankdetail")
    vco = no_chrome()
    vco["visualLink"] = props(show=lit(True), type=lit("Back"))
    p.add("actionButton", L, 80, 34, 34,
          objects={"icon": [{"properties": {"shapeType": lit("back"), "lineColor": color(TEAL), "lineWeight": lit(2)},
                             "selector": DEFAULT}],
                   "fill": [{"properties": {"show": lit(False)}, "selector": DEFAULT}],
                   "outline": [{"properties": {"show": lit(False)}, "selector": DEFAULT}]}, vco=vco)
    card(p, L + 44, 70, 1100, 40, mea(B, "Selected Bank"), size=14)
    card(p, L + 44, 106, 800, 24, mea(B, "Bank Holding Company"), size=10, bold=False, value_color=MUTED)

    g = 12
    w = (CW - 4 * g) / 5
    tiles = [("Total assets", mea(S, "KPI Total Assets $B"), "Arrow Assets", "Color Assets"),
             ("Tier 1 leverage", mea(S, "KPI Tier 1 %"), "Arrow Tier 1", "Color Tier 1"),
             ("Non-current loans", mea(S, "KPI NCL %"), "Arrow NCL", "Color NCL"),
             ("Return on assets", mea(S, "KPI ROA %"), "Arrow ROA", "Color ROA")]
    for i, (lab, f, a, c) in enumerate(tiles):
        kpi_tile(p, L + i * (w + g), 134, w, 108, lab, f, S, a, c)
    x = L + 4 * (w + g)
    tile(p, x, 134, w, 108)
    chrome = no_chrome(title=True)
    chrome["title"] = props(show=lit(True), text=lit("12-month distress probability"), fontSize=lit(10),
                            fontColor=color(MUTED), fontFamily=lit("Segoe UI"), titleWrap=lit(False))
    card(p, x + 4, 136, w - 8, 74, mea(E, "Bank Distress Probability"), size=20, value_color=RED, chrome=chrome)
    card(p, x + 4, 134 + 108 - 32, w - 8, 30, mea(E, "Bank Risk Band"), size=9, value_color=INK)

    lw = 360
    tile(p, L, 254, lw, 142)
    textbox(p, L + 12, 260, lw - 24, 22, [[("What drives this bank's risk score?",
                                             {"fontSize": "11pt", "fontWeight": "bold", "color": INK})]])
    for i in (1, 2, 3):
        card(p, L + 8, 284 + (i - 1) * 36, lw - 16, 36, mea(E, f"Bank Reason {i}"), size=9, bold=False, wrap=True)
    card(p, L, 404, lw, 52, mea(S, "Bank Complaint Peer Percentile"), size=10, wrap=True,
         chrome=card_chrome("Complaint rate vs size peers"))
    objs, vco = chart_style("clusteredBarChart", "What do customers complain about? (Oct 2023 onward)")
    p.add("clusteredBarChart", L, 464, lw, 244,
          query={"Category": {"projections": [proj(col(C, "PRODUCT"), active=True)]},
                 "Y": {"projections": [proj(mea(C, "Complaint Count"), "Complaints")]}},
          objects=objs, vco=vco, sort=sort_by(mea(C, "Complaint Count")))

    rx = L + lw + g
    rw = (R - rx - g) / 2

    def peer_line(x, y, title, bank_m, peer_m):
        objs, vco = chart_style("lineChart", title)
        objs["dataPoint"] = [
            {"properties": {"fill": color(TEAL)}, "selector": {"metadata": f"Scorecard.{bank_m}"}},
            {"properties": {"fill": color(GREY)}, "selector": {"metadata": f"Scorecard.{peer_m}"}}]
        objs["lineStyles"] = [
            {"properties": {"strokeWidth": lit(2)}},
            {"properties": {"lineStyle": lit("dashed")}, "selector": {"metadata": f"Scorecard.{peer_m}"}}]
        objs["legend"] = props(show=lit(True), position=lit("Top"), fontSize=lit(9), labelColor=color(MUTED))
        p.add("lineChart", x, y, rw, 221,
              query={"Category": {"projections": [proj(col(Q, "Quarter End"), active=True)]},
                     "Y": {"projections": [proj(mea(S, bank_m), "This bank"),
                                           proj(mea(S, peer_m), "Median of same-size banks")]}},
              objects=objs, vco=vco, sort=sort_by(col(Q, "Quarter End"), desc=False))

    peer_line(rx, 254, "Are bad loans (non-current %) rising faster than at similar-size banks?",
              "Bank NCL %", "Peer Median NCL %")
    peer_line(rx + rw + g, 254, "Is capital (Tier 1 leverage %) keeping up with peers?",
              "Bank Tier 1 %", "Peer Median Tier 1 %")
    peer_line(rx, 487, "Is the bank as profitable (ROA %) as its peers?", "Bank ROA %", "Peer Median ROA %")
    objs, vco = chart_style("clusteredColumnChart", "Are complaints rising? (complaints per quarter)")
    p.add("clusteredColumnChart", rx + rw + g, 487, rw, 221,
          query={"Category": {"projections": [proj(col(Q, "Quarter Label"), active=True)]},
                 "Y": {"projections": [proj(mea(C, "Complaint Count"), "Complaints")]}},
          objects=objs, vco=vco, sort=sort_by(col(Q, "Quarter Label"), desc=False))

    p.filters = [{"name": "drillcert", "field": col(E, "CERT"), "type": "Categorical", "howCreated": "Drillthrough"}]
    p.binding = {"name": "drillbinding", "type": "Drillthrough", "referenceScope": "Default",
                 "parameters": [{"name": "certparam", "boundFilter": "drillcert", "fieldExpr": col(E, "CERT")}]}
    return p


THEME = {
    "name": "Bank Health Teal",
    "dataColors": [TEAL, "#5EAAA8", RED, "#F2A65A", "#3D5A80", "#9AA5A4", "#B8E0DC", "#16302D"],
    "background": "#FFFFFF", "foreground": INK, "tableAccent": TEAL,
    "good": GREEN, "neutral": GREY, "bad": RED,
    "maximum": TEAL, "center": "#B8E0DC", "minimum": "#F0F7F6",
    "textClasses": {
        "callout": {"fontFace": "Segoe UI Semibold", "fontSize": 24, "color": INK},
        "title": {"fontFace": "Segoe UI Semibold", "fontSize": 11, "color": INK},
        "header": {"fontFace": "Segoe UI Semibold", "fontSize": 10, "color": INK},
        "label": {"fontFace": "Segoe UI", "fontSize": 9, "color": MUTED}},
    "visualStyles": {
        "*": {"*": {"dropShadow": [{"show": False}],
                    "border": [{"show": True, "color": {"solid": {"color": LINE}}, "radius": 6}]}},
        "page": {"*": {"background": [{"color": {"solid": {"color": BG}}, "transparency": 0}],
                       "outspace": [{"color": {"solid": {"color": BG}}, "transparency": 0}]}},
    },
}


def write_pages(pages, schemas):
    pdir = RP / "pages"
    if pdir.exists():
        shutil.rmtree(pdir)
    pdir.mkdir(parents=True)
    for pg in pages:
        d = pdir / pg.name
        (d / "visuals").mkdir(parents=True)
        pj = {"$schema": schemas["page"], "name": pg.name, "displayName": pg.display,
              "displayOption": "FitToPage", "height": H_PAGE, "width": W_PAGE,
              "objects": {"background": props(color=color(BG), transparency=lit(0)),
                          "outspace": props(color=color(BG), transparency=lit(0))}}
        if pg.hidden:
            pj["visibility"] = "HiddenInViewMode"
        if pg.filters:
            pj["filterConfig"] = {"filters": pg.filters}
        if pg.binding:
            pj["pageBinding"] = pg.binding
        (d / "page.json").write_text(json.dumps(pj, indent=2, ensure_ascii=False), encoding="utf-8")
        for v in pg.visuals:
            v["$schema"] = schemas["visual"]
            vd = d / "visuals" / v["name"]
            vd.mkdir()
            (vd / "visual.json").write_text(json.dumps(v, indent=2, ensure_ascii=False), encoding="utf-8")
    meta = {"$schema": schemas["pages"], "pageOrder": [p.name for p in pages], "activePageName": pages[0].name}
    (pdir / "pages.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


def install_theme():
    name = "bank_health_teal_theme.json"
    (ROOT / name).write_text(json.dumps(THEME, indent=2), encoding="utf-8")
    rr = RP.parent / "StaticResources" / "RegisteredResources"
    rr.mkdir(parents=True, exist_ok=True)
    for old in rr.glob("*.json"):
        old.unlink()
    shutil.copy(ROOT / name, rr / name)
    rj = json.loads((RP / "report.json").read_text(encoding="utf-8"))
    tc = rj.setdefault("themeCollection", {})
    tc["customTheme"] = {"name": name, "reportVersionAtImport": tc.get("baseTheme", {}).get("reportVersionAtImport"),
                         "type": "RegisteredResources"}
    pkgs = rj.setdefault("resourcePackages", [])
    pkgs[:] = [p for p in pkgs if p.get("name") != "RegisteredResources"]
    pkgs.append({"name": "RegisteredResources", "type": "RegisteredResources",
                 "items": [{"name": name, "path": name, "type": "CustomTheme"}]})
    (RP / "report.json").write_text(json.dumps(rj, indent=2), encoding="utf-8")


def schemas_from_existing():
    s = {"page": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/page/2.1.0/schema.json",
         "pages": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/pagesMetadata/1.1.0/schema.json",
         "visual": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.14.0/schema.json"}
    for f in (RP / "pages").rglob("*.json"):
        sc = json.loads(f.read_text(encoding="utf-8")).get("$schema", "")
        if "/page/" in sc:
            s["page"] = sc
        elif "pagesMetadata" in sc:
            s["pages"] = sc
        elif "visualContainer" in sc:
            s["visual"] = sc
    return s


if __name__ == "__main__":
    if not SM.exists() or not RP.exists():
        sys.exit("Save the report once from Power BI Desktop as powerbi/BankHealth.pbip first.")
    if "--report-only" not in sys.argv:   # --report-only keeps the model (and its refreshed data) as is
        build_model()
    schemas = schemas_from_existing()
    install_theme()
    write_pages([overview(), early_warning(), bank_detail()], schemas)
    print("Model and report written. Open powerbi/BankHealth.pbip and click Refresh.")
