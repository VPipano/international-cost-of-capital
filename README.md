# 🌍 International Cost of Capital App

A Streamlit app that compares a project's **cost of equity, cost of debt and WACC in USD and in the currency of any selected country**, built for Kellogg *International Finance* (Prof. Sergio Rebelo, 2026).

**Live app:** _add your Streamlit URL here_

## What it does
- Pulls the latest data, with sources and dates shown, and a **🔄 Refresh data** button:
  | Input | Source |
  |---|---|
  | Industry unlevered beta (U.S.) | Aswath Damodaran, [`betas.xls`](https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls) |
  | USD BBB borrowing spread (Baa2/BBB, large non-financial firms) | Damodaran, [`ratings.html`](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ratings.html) |
  | Statutory corporate tax rates (combined central + sub-central) | OECD Corporate Tax Statistics ([report](https://www.oecd.org/en/publications/corporate-tax-statistics-2024_9c27d6e8-en/full-report/statutory-corporate-income-tax-rates_440f9286.html); data via the OECD SDMX API, dataflow `DSD_TAX_CIT@DF_CIT`) |
  | 10-year government bond yields | [Trading Economics](https://tradingeconomics.com/bonds) (scraped, no API key) |
- User controls: industry, target country, equity risk premium, target debt share, and a switch between the **U.S. or selected-country tax rate** (one rate applied to both currencies).
- **Manual overrides** for unlevered beta, both tax rates, USD BBB spread and both government yields. The foreign-currency spread can be overridden; when the override is off, the app shows the automatic value.
- Results side by side (USD vs. foreign currency), a method and assumptions tab, an **all-industry comparison**, and **CSV/Excel downloads**.
- A **Verification** tab that runs the required checks live.

**Defaults:** Software (System & Application), United States, U.S. tax rate, ERP 6.5%, 30% debt.

## Method (course slides 572–587)
```
βE    = βU · [1 + (1 − τ) · d/(1 − d)]            relever at target debt share
Re    = Rf + βE · ERP                              CAPM in USD (U.S. 10Y as Rf)
Rd    = Rf + s                                     s = Damodaran BBB spread
WACC  = (1 − d)·Re + d·Rd·(1 − τ)

F     = (1 + Rf*) / (1 + Rf)                       relative PPP + Fisher
Re*   = (1 + Re)·F − 1
s*    = s·F            (default; can be overridden)
Rd*   = Rf* + s*
WACC* = (1 − d)·Re* + d·Rd*·(1 − τ)
```
Assumptions: risk measured against the U.S. market; U.S. industry betas proxy for project risk; zero debt beta; no separate country equity premium; government yields as risk-free proxies; constant target leverage; interest fully deductible in the financing currency.

## Robustness
Each source has a dated, labelled fallback: the Damodaran BBB spread for January 2026, OECD rates from the course slide (CTS 2026), and a Trading Economics snapshot taken 15 Nov 2026. Trading Economics loads its bond table with JavaScript; if the table isn't in the HTML, the app tries the country page and then the snapshot. Any value can be overwritten manually.

## Verification
- `verification/independent_check.xlsx` recomputes everything with Excel formulas. It reproduces the course Colombia example (WACC 8.5265% USD / 16.0670% COP) and runs three checks:
  1. Equal government yields → identical results in both currencies (spread override off).
  2. Zero debt → WACC = cost of equity.
  3. Overriding the foreign-currency spread leaves USD results unchanged.

## Run locally
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploy
Push `app.py`, `requirements.txt` and `README.md` to a public GitHub repo, then go to [share.streamlit.io](https://share.streamlit.io) → **Create app**, select the repo, `main` branch and `app.py` → **Deploy**.

_AI assistance: built with Claude (Anthropic). Calculations were checked independently (see Verification)._
