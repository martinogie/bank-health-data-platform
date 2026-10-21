# Bank Health Data Platform

**Which of the banks you depend on should you worry about this quarter, and why?**

Regulators rate every US bank, but those ratings are confidential. Everyone else with money at
risk at a bank has to work it out from public filings:

- a **fintech** whose customer accounts sit at one small sponsor bank,
- a **company treasurer** holding deposits above the $250,000 FDIC limit,
- a **bank** with loan participations or correspondent accounts at other banks.

When Silicon Valley Bank failed in March 2023, and when Synapse collapsed in 2024 and froze its
fintech customers' funds, those were the people caught out.

This project is an early-warning system for them, built only from public data. Every quarter it
scores every FDIC-insured bank (4,257 in 2026 Q2) on its chance of distress in the next 12 months.
It gives each bank's top three reasons and compares it with banks of the same size. A
**watchlist** lets you enter the banks you deal with and see their scores, trends and reasons on
one page.

**Try the watchlist:** [martinogie.github.io/bank-health-data-platform/watchlist.html](https://martinogie.github.io/bank-health-data-platform/watchlist.html)

It runs on 26 years of FDIC call reports for 27,835 banks and nearly 2 million CFPB consumer
complaints, through a full production-style pipeline.

> A screening tool on public data, not a supervisory rating. Use it to decide which banks to look
> at more closely, not to judge any bank.
