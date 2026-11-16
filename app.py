"""
International Cost of Capital App
=================================
Compares a project's cost of equity, cost of debt and WACC in USD and in the
currency of a selected country, following the course method
(Rebelo, International Finance, Kellogg 2026 - Case Packet slides 572-587):

    beta_E  = beta_U * [1 + (1 - tau) * d / (1 - d)]          (relever, zero debt beta)
    Re      = Rf + beta_E * ERP                                 (CAPM in USD)
    Rd      = Rf + s                                            (USD pretax borrowing)
    WACC    = (1 - d) * Re + d * Rd * (1 - tau)

    F       = (1 + Rf*) / (1 + Rf)                              (relative PPP + Fisher)
    Re*     = (1 + Re) * F - 1
    s*      = s * F            (default; user can override)
    Rd*     = Rf* + s*
    WACC*   = (1 - d) * Re* + d * Rd* * (1 - tau)

Data: Damodaran (industry unlevered betas, BBB spread), OECD (statutory CIT rates),
Trading Economics (10-year government yields). Every fetched input can be
overridden manually, and every fetch has a dated fallback so the app never breaks.
"""

from __future__ import annotations

import io
import re
from datetime import date, datetime, timezone

import pandas as pd
import requests
from bs4 import BeautifulSoup

try:  # streamlit is only needed when the UI runs (tests import the math only)
    import streamlit as st
except ImportError:  # pragma: no cover
    st = None

# --------------------------------------------------------------------------------------
# Source URLs
# --------------------------------------------------------------------------------------
BETAS_URL = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls"
RATINGS_URL = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ratings.html"
OECD_PAGE_URL = (
    "https://www.oecd.org/en/publications/corporate-tax-statistics-2024_9c27d6e8-en/"
    "full-report/statutory-corporate-income-tax-rates_440f9286.html"
)
OECD_API_URL = (
    "https://sdmx.oecd.org/public/rest/data/OECD.CTP.TPS,DSD_TAX_CIT@DF_CIT,/all"
    "?startPeriod=2022&dimensionAtObservation=AllDimensions&format=csvfilewithlabels"
)
TE_BONDS_URL = "https://tradingeconomics.com/bonds"
TE_COUNTRY_URL = "https://tradingeconomics.com/{slug}/government-bond-yield"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = 25

DEFAULT_INDUSTRY = "Software (System & Application)"
DEFAULT_COUNTRY = "United States"
DEFAULT_ERP = 6.5   # percent
DEFAULT_DEBT = 30.0  # percent

# --------------------------------------------------------------------------------------
# Fallback data (used only if a live fetch fails; always labelled as fallback in the app)
# --------------------------------------------------------------------------------------
# Trading Economics /bonds snapshot, captured 15-16 Nov 2026 (value, quote date on TE).
TE_SNAPSHOT_DATE = "2026-11-15"
TE_SNAPSHOT = {
    "United States": (5.1470, "Nov/16"), "United Kingdom": (5.3377, "Nov/13"),
    "Japan": (2.8780, "Nov/16"), "Australia": (5.1380, "Nov/16"),
    "Germany": (3.3762, "Nov/13"), "Brazil": (15.3350, "Nov/13"),
    "Russia": (16.7400, "Nov/13"), "India": (7.3120, "Nov/13"),
    "Canada": (3.8560, "Nov/13"), "Italy": (4.4370, "Nov/13"),
    "France": (4.9210, "Nov/13"), "South Africa": (8.6750, "Nov/13"),
    "China": (1.6181, "Nov/13"), "Switzerland": (0.5626, "Nov/13"),
    "Mexico": (9.3080, "Nov/13"), "Netherlands": (3.4768, "Nov/13"),
    "New Zealand": (4.7700, "Nov/16"), "Portugal": (3.8186, "Nov/13"),
    "South Korea": (3.9920, "Nov/16"), "Spain": (3.9442, "Nov/13"),
    "Greece": (4.2636, "Nov/13"), "Turkey": (33.5200, "Nov/13"),
    "Finland": (3.7611, "Nov/13"), "Lithuania": (4.3250, "Nov/13"),
    "Norway": (4.3842, "Nov/13"), "Poland": (6.3350, "Nov/13"),
    "Romania": (7.1200, "Nov/13"), "Slovakia": (4.3050, "Nov/13"),
    "Slovenia": (3.9140, "Nov/13"), "Sweden": (3.0980, "Nov/13"),
    "Hungary": (5.5249, "Nov/13"), "Iceland": (6.8100, "Nov/13"),
    "Ireland": (3.5391, "Nov/13"), "Austria": (3.6096, "Nov/13"),
    "Belgium": (4.3545, "Nov/13"), "Croatia": (3.8668, "Nov/13"),
    "Czech Republic": (5.2222, "Nov/13"), "Denmark": (3.1690, "Nov/15"),
    "Peru": (6.2414, "Nov/13"), "Chile": (6.0100, "Nov/13"),
    "Colombia": (12.6980, "Nov/13"), "Singapore": (2.3331, "Nov/16"),
    "Taiwan": (1.8600, "Nov/16"), "Thailand": (1.9950, "Nov/16"),
    "Vietnam": (4.5726, "Nov/16"), "Hong Kong": (3.9941, "Nov/16"),
    "Indonesia": (7.6070, "Nov/13"), "Israel": (4.1830, "Nov/13"),
    "Malaysia": (3.8647, "Nov/15"), "Pakistan": (12.6040, "Nov/13"),
    "Philippines": (7.7670, "Nov/16"), "Zambia": (14.7400, "Nov/13"),
    "Kenya": (12.5000, "Nov/13"), "Nigeria": (16.5980, "Nov/13"),
}

# OECD combined statutory CIT rates (central + sub-central), Corporate Tax Statistics
# 2026 as shown in the course packet (slide 550). Colombia and Brazil are not on that
# slide; their 2024 values come from OECD Corporate Tax Statistics 2024.
OECD_FALLBACK_LABEL = "OECD Corporate Tax Statistics 2026 (course slide 550); fallback"
OECD_FALLBACK = {
    "France": 36.1, "Argentina": 35.0, "Germany": 30.1, "Australia": 30.0, "Kenya": 30.0,
    "Mexico": 30.0, "Nigeria": 30.0, "Senegal": 30.0, "Zambia": 30.0, "Japan": 29.7,
    "Portugal": 29.5, "Peru": 29.5, "Pakistan": 29.0, "New Zealand": 28.0, "Italy": 27.8,
    "South Korea": 27.5, "Chile": 27.0, "Dominican Republic": 27.0, "South Africa": 27.0,
    "Canada": 26.0, "Netherlands": 25.8, "United States": 25.5, "India": 25.2,
    "Angola": 25.0, "Belgium": 25.0, "China": 25.0, "Spain": 25.0, "United Kingdom": 25.0,
    "Jamaica": 25.0, "Panama": 25.0, "Philippines": 25.0, "Uruguay": 25.0, "Turkey": 25.0,
    "Malaysia": 24.0, "Slovakia": 24.0, "Luxembourg": 23.9, "Austria": 23.0, "Israel": 23.0,
    "Egypt": 22.5, "Botswana": 22.0, "Denmark": 22.0, "Greece": 22.0, "Indonesia": 22.0,
    "Norway": 22.0, "Slovenia": 22.0, "Estonia": 22.0, "Czech Republic": 21.0,
    "Sweden": 20.6, "Iceland": 20.0, "Finland": 20.0, "Latvia": 20.0, "Saudi Arabia": 20.0,
    "Thailand": 20.0, "Vietnam": 20.0, "Switzerland": 19.5, "Poland": 19.0, "Croatia": 18.0,
    "Ukraine": 18.0, "Singapore": 17.0, "Lithuania": 17.0, "Hong Kong": 16.5,
    "Romania": 16.0, "Ireland": 12.5, "Bulgaria": 10.0, "Hungary": 9.0,
    "Colombia": 35.0, "Brazil": 34.0,
}

# Damodaran ratings.html, large non-financial service firms, Baa2/BBB (January 2026).
BBB_FALLBACK = (1.11, "January 2026")

# ISO-3 codes so OECD (which uses ISO codes / official names) matches TE country names.
ISO3 = {
    "United States": "USA", "United Kingdom": "GBR", "Japan": "JPN", "Australia": "AUS",
    "Germany": "DEU", "Brazil": "BRA", "Russia": "RUS", "India": "IND", "Canada": "CAN",
    "Italy": "ITA", "France": "FRA", "South Africa": "ZAF", "China": "CHN",
    "Switzerland": "CHE", "Mexico": "MEX", "Netherlands": "NLD", "New Zealand": "NZL",
    "Portugal": "PRT", "South Korea": "KOR", "Spain": "ESP", "Greece": "GRC",
    "Turkey": "TUR", "Finland": "FIN", "Lithuania": "LTU", "Norway": "NOR",
    "Poland": "POL", "Romania": "ROU", "Slovakia": "SVK", "Slovenia": "SVN",
    "Sweden": "SWE", "Hungary": "HUN", "Iceland": "ISL", "Ireland": "IRL",
    "Austria": "AUT", "Belgium": "BEL", "Croatia": "HRV", "Czech Republic": "CZE",
    "Denmark": "DNK", "Peru": "PER", "Chile": "CHL", "Colombia": "COL",
    "Singapore": "SGP", "Taiwan": "TWN", "Thailand": "THA", "Vietnam": "VNM",
    "Hong Kong": "HKG", "Indonesia": "IDN", "Israel": "ISR", "Malaysia": "MYS",
    "Pakistan": "PAK", "Philippines": "PHL", "Zambia": "ZMB", "Kenya": "KEN",
    "Nigeria": "NGA", "Egypt": "EGY", "Bulgaria": "BGR", "Latvia": "LVA",
    "Estonia": "EST", "Luxembourg": "LUX", "Argentina": "ARG", "Saudi Arabia": "SAU",
    "Ukraine": "UKR", "Uruguay": "URY", "Serbia": "SRB", "Morocco": "MAR",
    "Botswana": "BWA", "Uganda": "UGA", "Mauritius": "MUS", "Namibia": "NAM",
    "Bangladesh": "BGD", "Sri Lanka": "LKA", "Qatar": "QAT", "Bahrain": "BHR",
    "Kazakhstan": "KAZ", "Jordan": "JOR", "Ghana": "GHA", "Cyprus": "CYP",
    "Malta": "MLT", "Costa Rica": "CRI", "Panama": "PAN", "Dominican Republic": "DOM",
    "Jamaica": "JAM", "Angola": "AGO", "Senegal": "SEN",
}

# ======================================================================================
# 1. CALCULATIONS (pure functions - verified independently, see verify.py)
# ======================================================================================

def compute_cost_of_capital(beta_u: float, tax: float, debt_share: float, erp: float,
                            rf_us: float, rf_fx: float, spread_usd: float,
                            spread_fx_override: float | None = None) -> dict:
    """All rates as decimals (0.05 = 5%). Returns a dict of every intermediate result."""
    if not 0 <= debt_share < 1:
        raise ValueError("Debt share must be in [0, 1).")
    d = debt_share
    de = d / (1 - d)                                   # D/E from target debt share
    beta_e = beta_u * (1 + (1 - tax) * de)             # relever (zero debt beta)

    # --- USD ---
    re_usd = rf_us + beta_e * erp                      # CAPM, no country premium
    rd_usd = rf_us + spread_usd                        # pretax borrowing
    rd_usd_at = rd_usd * (1 - tax)
    wacc_usd = (1 - d) * re_usd + d * rd_usd_at

    # --- Foreign currency ---
    F = (1 + rf_fx) / (1 + rf_us)                      # conversion factor
    re_fx = (1 + re_usd) * F - 1
    spread_fx_auto = spread_usd * F                    # same factor F on gross rate
    spread_fx = spread_fx_auto if spread_fx_override is None else spread_fx_override
    rd_fx = rf_fx + spread_fx
    rd_fx_at = rd_fx * (1 - tax)
    wacc_fx = (1 - d) * re_fx + d * rd_fx_at

    return dict(D_over_E=de, beta_e=beta_e, F=F,
                re_usd=re_usd, rd_usd=rd_usd, rd_usd_at=rd_usd_at, wacc_usd=wacc_usd,
                re_fx=re_fx, spread_fx_auto=spread_fx_auto, spread_fx=spread_fx,
                rd_fx=rd_fx, rd_fx_at=rd_fx_at, wacc_fx=wacc_fx)


# ======================================================================================
# 2. DATA FETCHERS (each returns data + metadata; raise on failure, UI handles fallback)
# ======================================================================================

def _get(url: str) -> requests.Response:
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r


def parse_betas_xls(content: bytes) -> tuple[pd.DataFrame, str]:
    """Parse Damodaran's betas.xls -> DataFrame[Industry, Number of firms, Unlevered beta]."""
    sheets = pd.read_excel(io.BytesIO(content), sheet_name=None, header=None)
    # Prefer the 'Industry Averages' sheet; otherwise any sheet with 'Industry Name'.
    order = sorted(sheets, key=lambda n: 0 if "industry average" in n.lower() else 1)
    for name in order:
        raw = sheets[name]
        hdr_rows = raw.index[raw.iloc[:, 0].astype(str).str.strip().str.lower() == "industry name"]
        if len(hdr_rows) == 0:
            continue
        h = hdr_rows[0]
        header = [str(x).strip() for x in raw.iloc[h].tolist()]
        body = raw.iloc[h + 1:].copy()
        body.columns = header
        # first column called 'Unlevered beta' (not the cash-corrected one)
        ucol = next(c for c in header
                    if c.lower().startswith("unlevered beta") and "cash" not in c.lower())
        ncol = next((c for c in header if "number of firms" in c.lower()), None)
        out = pd.DataFrame({
            "Industry": body["Industry Name"].astype(str).str.strip(),
            "Number of firms": pd.to_numeric(body[ncol], errors="coerce") if ncol else None,
            "Unlevered beta": pd.to_numeric(body[ucol], errors="coerce"),
        })
        out = out[out["Industry"].ne("nan") & out["Unlevered beta"].notna()]
        out = out[~out["Industry"].str.lower().str.startswith("total market")]
        # Date: the cell right of 'Date updated:' (an Excel date), else a month-year string
        for i in range(h):
            if str(raw.iat[i, 0]).strip().lower().startswith("date updated"):
                nxt = [v for v in raw.iloc[i, 1:].tolist() if pd.notna(v)]
                if nxt:
                    v = nxt[0]
                    when = v.strftime("%B %d, %Y") if hasattr(v, "strftime") else str(v)
                    return out.reset_index(drop=True), when
        top_text = " ".join(str(v) for v in raw.iloc[:h].values.ravel() if pd.notna(v))
        m = (re.search(r"Date updated:?\s*([A-Za-z]+\s+\d{1,2},?\s*\d{4}|[A-Za-z]+\s+\d{4})", top_text)
             or re.search(r"(January|February|March|April|May|June|July|August|September|"
                          r"October|November|December)\s+\d{4}", top_text))
        when = (m.group(1) if m and m.lastindex else m.group(0)) if m else "date not stated in file"
        return out.reset_index(drop=True), when
    raise ValueError("Could not find an 'Industry Name' table in betas.xls")


def fetch_betas() -> tuple[pd.DataFrame, str]:
    return parse_betas_xls(_get(BETAS_URL).content)


def parse_bbb_spread(html: str) -> tuple[float, str]:
    """Return (spread in %, 'as of' text) for Baa2/BBB, large non-financial firms."""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    m = re.search(r"Data used is as of\s+([A-Za-z]+\s+\d{4})", text)
    when = m.group(1) if m else "date not stated"
    # The large non-financial table is the first one on the page; within each row the
    # first 'Baa2/BBB' cell belongs to that (left-hand) panel and the next cell is the spread.
    for tr in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True).replace("\xa0", " ") for c in tr.find_all(["td", "th"])]
        for i, c in enumerate(cells):
            if c.replace(" ", "") in ("Baa2/BBB",) and i + 1 < len(cells):
                val = cells[i + 1].replace("%", "").strip()
                return float(val), when
    m2 = re.search(r"Baa2/BBB\s+([\d.]+)%", text)
    if m2:
        return float(m2.group(1)), when
    raise ValueError("Baa2/BBB row not found")


def fetch_bbb_spread() -> tuple[float, str]:
    return parse_bbb_spread(_get(RATINGS_URL).text)


def parse_oecd_csv(text: str) -> tuple[dict, str]:
    """Parse OECD SDMX labelled CSV -> {ISO3: (rate %, year)} for the combined CIT rate."""
    df = pd.read_csv(io.StringIO(text), low_memory=False)
    str_cols = [c for c in df.columns if not pd.api.types.is_numeric_dtype(df[c])]
    # rows whose code or label identifies the *combined* statutory rate
    mask = pd.Series(False, index=df.index)
    for c in str_cols:
        s = df[c].astype(str)
        mask |= s.str.contains("COMB_CIT", case=False, regex=False)
        mask |= s.str.contains("Combined corporate income tax rate", case=False, regex=False)
    sub = df[mask].copy()
    # keep the standard statutory rate, drop the 'Targeted at small business' variant
    # (filter on the Targeting column only: the dataset title mentions small business too)
    if "TARGETING" in sub.columns:
        sub = sub[sub["TARGETING"].astype(str).str.upper() == "ST"]
    elif "Targeting" in sub.columns:
        sub = sub[sub["Targeting"].astype(str).str.lower() == "statutory"]
    if sub.empty:
        raise ValueError("No combined CIT rows in OECD response")
    area = "REF_AREA" if "REF_AREA" in sub.columns else next(
        c for c in sub.columns if c.upper().startswith("COU"))
    sub["TIME_PERIOD"] = pd.to_numeric(sub["TIME_PERIOD"], errors="coerce")
    sub["OBS_VALUE"] = pd.to_numeric(sub["OBS_VALUE"], errors="coerce")
    sub = sub.dropna(subset=["OBS_VALUE", "TIME_PERIOD"]).sort_values("TIME_PERIOD")
    latest = sub.groupby(area).tail(1)
    rates = {r[area]: (float(r["OBS_VALUE"]), int(r["TIME_PERIOD"])) for _, r in latest.iterrows()}
    years = sorted({y for _, y in rates.values()})
    return rates, f"OECD Corporate Tax Statistics database (latest year {years[-1]})"


def fetch_oecd_tax() -> tuple[dict, str]:
    r = requests.get(OECD_API_URL, headers={**HEADERS, "Accept": "text/csv"}, timeout=60)
    r.raise_for_status()
    return parse_oecd_csv(r.text)


def _te_date(s: str) -> str:
    """TE shows 'Nov/13' for past days or 'HH:MM' for today."""
    s = (s or "").strip()
    today = date.today()
    if ":" in s:
        return today.isoformat()
    try:
        d = datetime.strptime(f"{s}/{today.year}", "%b/%d/%Y").date()
        if (d - today).days > 2:          # allow for time-zone differences
            d = d.replace(year=today.year - 1)
        return d.isoformat()
    except ValueError:
        return s or "n/a"


def parse_te_bonds(html: str) -> dict:
    """Parse tradingeconomics.com/bonds -> {country: (yield %, date)} for 10Y yields."""
    soup = BeautifulSoup(html, "html.parser")
    out = {}
    for tr in soup.select("tr[data-symbol]"):
        tds = tr.find_all("td")
        name_td = tr.select_one("td.datatable-item-first") or (tds[1] if len(tds) > 1 else None)
        price_td = tr.select_one("td#p")
        if not name_td or not price_td:
            continue
        name = name_td.get_text(" ", strip=True)
        try:
            val = float(price_td.get_text(strip=True).replace(",", ""))
        except ValueError:
            continue
        if name and name not in out:
            out[name] = (val, _te_date(tds[-1].get_text(strip=True)))
    return out


def parse_te_country(html: str) -> float:
    """Parse a TE country bond page (meta description sentence) -> yield %."""
    soup = BeautifulSoup(html, "html.parser")
    for sel in ['meta[name="description"]', 'meta[property="og:description"]']:
        tag = soup.select_one(sel)
        if tag and tag.get("content"):
            m = re.search(r"(?:to|at)\s+(-?\d+(?:\.\d+)?)\s*(?:%|percent)", tag["content"])
            if m:
                return float(m.group(1))
    raise ValueError("yield not found on country page")


def fetch_te_bonds() -> dict:
    data = parse_te_bonds(_get(TE_BONDS_URL).text)
    if len(data) < 5:
        raise ValueError("TE bonds table not in server HTML (loaded by JavaScript)")
    return data


def fetch_te_country(country: str) -> tuple[float, str]:
    slug = country.lower().replace(" ", "-")
    return parse_te_country(_get(TE_COUNTRY_URL.format(slug=slug)).text), date.today().isoformat()


# ======================================================================================
# 3. STREAMLIT UI
# ======================================================================================

def pct(x: float, nd: int = 2) -> str:
    return f"{x * 100:.{nd}f}%"


def main() -> None:  # noqa: C901 - one linear page
    st.set_page_config(page_title="International Cost of Capital", page_icon="🌍", layout="wide")

    # ---------------- cached loaders with fallbacks ----------------
    @st.cache_data(ttl=6 * 3600, show_spinner="Fetching Damodaran industry betas…")
    def load_betas():
        try:
            df, when = fetch_betas()
            return df, when, "live", None
        except Exception as e:  # noqa: BLE001
            return None, None, "failed", str(e)

    @st.cache_data(ttl=6 * 3600, show_spinner="Fetching Damodaran BBB spread…")
    def load_bbb():
        try:
            s, when = fetch_bbb_spread()
            return s, when, "live", None
        except Exception as e:  # noqa: BLE001
            return BBB_FALLBACK[0], BBB_FALLBACK[1], "fallback", str(e)

    @st.cache_data(ttl=6 * 3600, show_spinner="Fetching OECD corporate tax rates…")
    def load_tax():
        try:
            rates_iso, label = fetch_oecd_tax()
            by_name = {}
            for name, iso in ISO3.items():
                if iso in rates_iso:
                    by_name[name] = rates_iso[iso]
            if "United States" not in by_name:
                raise ValueError("US missing from OECD response")
            return by_name, label, "live", None
        except Exception as e:  # noqa: BLE001
            fb = {k: (v, 2026) for k, v in OECD_FALLBACK.items()}
            return fb, OECD_FALLBACK_LABEL, "fallback", str(e)

    @st.cache_data(ttl=3600, show_spinner="Fetching Trading Economics 10Y yields…")
    def load_yields():
        try:
            return fetch_te_bonds(), "live (tradingeconomics.com/bonds)", None
        except Exception as e:  # noqa: BLE001
            snap = {k: (v, _te_date(d)) for k, (v, d) in TE_SNAPSHOT.items()}
            return snap, f"fallback snapshot captured {TE_SNAPSHOT_DATE}", str(e)

    @st.cache_data(ttl=3600, show_spinner=False)
    def load_country_yield(country):
        try:
            return fetch_te_country(country)
        except Exception:  # noqa: BLE001
            return None

    # ---------------- header ----------------
    st.title("🌍 International Cost of Capital")
    st.caption("Cost of equity, cost of debt and WACC for a project — in USD and in the "
               "selected country's currency. Method: CAPM in USD with U.S. industry betas, "
               "converted with relative PPP + Fisher: (1+R*) = (1+R)·(1+Rf*)/(1+Rf).")

    if "fetched_at" not in st.session_state:
        st.session_state.fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    betas, betas_date, betas_status, betas_err = load_betas()
    bbb, bbb_date, bbb_status, bbb_err = load_bbb()
    taxes, tax_label, tax_status, tax_err = load_tax()
    yields, yields_label, yields_err = load_yields()

    # ---------------- sidebar: controls ----------------
    with st.sidebar:
        st.header("Inputs")
        if st.button("🔄 Refresh data", width="stretch",
                     help="Clear the cache and re-fetch every source"):
            st.cache_data.clear()
            st.session_state.fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            st.rerun()
        st.caption(f"Data last fetched: {st.session_state.fetched_at}")

        if betas is not None:
            industries = betas["Industry"].tolist()
            idx = industries.index(DEFAULT_INDUSTRY) if DEFAULT_INDUSTRY in industries else 0
            industry = st.selectbox("Industry (Damodaran, U.S.)", industries, index=idx)
        else:
            industry = st.text_input("Industry (betas file unavailable)", DEFAULT_INDUSTRY)

        countries = sorted(set(yields) - {"Euro area"})
        if DEFAULT_COUNTRY not in countries:
            countries = [DEFAULT_COUNTRY] + countries
        country = st.selectbox("Target country", countries, index=countries.index(DEFAULT_COUNTRY))

        erp = st.slider("Equity risk premium (%)", 3.0, 10.0, DEFAULT_ERP, 0.1) / 100
        d = st.slider("Target debt share D/(D+E) (%)", 0.0, 80.0, DEFAULT_DEBT, 1.0) / 100

        tax_choice = st.radio("Project tax rate", ["U.S. rate", "Selected-country rate"],
                              horizontal=True, index=0,
                              help="One tax rate is applied to both currencies.")

        # ----- fetched values -----
        beta_fetched = (float(betas.loc[betas["Industry"] == industry, "Unlevered beta"].iloc[0])
                        if betas is not None and industry in set(betas["Industry"]) else None)
        tax_us_fetched = taxes.get("United States", (None, None))[0]
        tax_fx_fetched = taxes.get(country, (None, None))[0]

        def yield_for(c):
            live_table = yields_err is None
            if live_table and c in yields:
                return yields[c][0], yields[c][1], yields_label
            got = load_country_yield(c)          # live per-country page
            if got:
                return got[0], got[1], "live (TE country page)"
            if c in yields:                      # dated snapshot
                return yields[c][0], yields[c][1], yields_label
            return None, None, "not available"

        rf_us_f, rf_us_date, rf_us_src = yield_for("United States")
        rf_fx_f, rf_fx_date, rf_fx_src = yield_for(country)

        st.subheader("Manual overrides")
        st.caption("Switch on to replace a fetched value. Missing data must be entered here.")

        def override(label, fetched, key, step=0.01, fmt="%.4f", force=False):
            on = st.toggle(f"Override {label}", value=force or fetched is None,
                           key=f"t_{key}_{fetched is None}", disabled=fetched is None)
            # key includes the fetched value so the box refreshes when country/industry changes
            val = st.number_input(label, value=None if fetched is None else float(fetched),
                                  step=step, format=fmt, key=f"v_{key}_{label}_{fetched}",
                                  disabled=not on,
                                  placeholder="not available - enter a value")
            return (val if on else fetched), on

        beta_u, beta_manual = override("Unlevered beta", beta_fetched, "beta", 0.01, "%.4f")
        tax_us_pct, tax_us_manual = override("U.S. tax rate (%)", tax_us_fetched, "taxus", 0.1, "%.2f")
        tax_fx_pct, tax_fx_manual = override(f"{country} tax rate (%)", tax_fx_fetched, "taxfx", 0.1, "%.2f")
        bbb_pct, bbb_manual = override("USD BBB spread (%)", bbb, "bbb", 0.01, "%.2f")
        rf_us_pct, rf_us_manual = override("U.S. 10Y yield (%)", rf_us_f, "rfus", 0.01, "%.4f")
        rf_fx_pct, rf_fx_manual = override(f"{country} 10Y yield (%)", rf_fx_f, "rffx", 0.01, "%.4f")

        # foreign-currency spread: override or auto (s·F)
        sfx_on = st.toggle(f"Override {country}-currency spread", value=False, key="t_sfx")
        spread_fx_override = None
        if None not in (rf_us_pct, rf_fx_pct, bbb_pct):
            auto_sfx = (bbb_pct / 100) * (1 + rf_fx_pct / 100) / (1 + rf_us_pct / 100)
            if sfx_on:
                sfx_val = st.number_input(f"{country}-currency spread (%)",
                                          value=round(auto_sfx * 100, 4), step=0.01,
                                          format="%.4f", key=f"v_sfx_{country}")
                spread_fx_override = sfx_val / 100
            else:
                st.number_input(f"{country}-currency spread (%) — auto",
                                value=auto_sfx * 100, format="%.4f", disabled=True,
                                key=f"v_sfx_auto_{auto_sfx:.10f}",
                                help="Automatic value s* = s·(1+Rf*)/(1+Rf). "
                                     "Switch the override on to replace it.")

    # ---------------- guard rails ----------------
    missing = [n for n, v in [("unlevered beta", beta_u), ("U.S. tax rate", tax_us_pct),
                              (f"{country} tax rate", tax_fx_pct), ("U.S. yield", rf_us_pct),
                              (f"{country} yield", rf_fx_pct)] if v is None]
    tax_pct = tax_us_pct if tax_choice == "U.S. rate" else tax_fx_pct
    if None in (beta_u, tax_pct, rf_us_pct, rf_fx_pct, bbb_pct):
        st.error("Missing inputs: " + ", ".join(missing) + ". Enter them under *Manual overrides*.")
        st.stop()
    if missing:
        st.warning("Not available from the sources: " + ", ".join(missing) +
                   " (not needed with the current tax choice, or enter it manually).")

    tax = tax_pct / 100
    rf_us_dec, rf_fx_dec = rf_us_pct / 100, rf_fx_pct / 100
    res =compute_cost_of_capital(beta_u, tax, d, erp, rf_us_dec, rf_fx_dec,
                                  bbb_pct / 100, spread_fx_override)
    ccy = "USD" if country == "United States" else f"{country} currency"

    tabs = st.tabs(["📊 Results", "🏭 All industries", "🗂️ Data & sources",
                    "📐 Method & assumptions", "✅ Verification"])

    # ======================= RESULTS =======================
    with tabs[0]:
        st.subheader(f"{industry} — project in {country}")
        st.caption(f"Tax rate used for both currencies: **{tax_pct:.2f}%** "
                   f"({'U.S.' if tax_choice == 'U.S. rate' else country}) · ERP **{erp*100:.2f}%** · "
                   f"debt share **{d*100:.0f}%** · βU **{beta_u:.4f}** → βE **{res['beta_e']:.4f}**")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("#### 🇺🇸 In USD")
            a, b, c = st.columns(3)
            a.metric("Cost of equity", pct(res["re_usd"]))
            b.metric("Pretax cost of debt", pct(res["rd_usd"]))
            c.metric("WACC", pct(res["wacc_usd"]))
        with c2:
            st.markdown(f"#### 🌐 In {ccy}")
            a, b, c = st.columns(3)
            a.metric("Cost of equity", pct(res["re_fx"]),
                     delta=f"{(res['re_fx']-res['re_usd'])*100:+.2f} pp vs USD", delta_color="off")
            b.metric("Pretax cost of debt", pct(res["rd_fx"]),
                     delta=f"{(res['rd_fx']-res['rd_usd'])*100:+.2f} pp vs USD", delta_color="off")
            c.metric("WACC", pct(res["wacc_fx"]),
                     delta=f"{(res['wacc_fx']-res['wacc_usd'])*100:+.2f} pp vs USD", delta_color="off")

        table = pd.DataFrame({
            "Item": ["Risk-free rate (10Y gov. yield)", "Levered equity beta βE", "Cost of equity",
                     "Borrowing spread", "Pretax cost of debt", "After-tax cost of debt",
                     "Equity weight", "Debt weight", "WACC"],
            "USD": [pct(rf_us_dec, 4), f"{res['beta_e']:.4f}", pct(res['re_usd'], 4),
                    pct(bbb_pct/100, 4), pct(res['rd_usd'], 4), pct(res['rd_usd_at'], 4),
                    pct(1-d, 0), pct(d, 0), pct(res['wacc_usd'], 4)],
            ccy: [pct(rf_fx_dec, 4), f"{res['beta_e']:.4f}", pct(res['re_fx'], 4),
                  pct(res['spread_fx'], 4) + (" (override)" if sfx_on else " (auto = s·F)"),
                  pct(res['rd_fx'], 4), pct(res['rd_fx_at'], 4),
                  pct(1-d, 0), pct(d, 0), pct(res['wacc_fx'], 4)],
        })
        st.dataframe(table, hide_index=True, width="stretch")
        st.caption(f"Conversion factor F = (1+Rf*)/(1+Rf) = {res['F']:.6f}. "
                   "Calculations use full precision; displayed rates are rounded.")

        # downloadable results
        inputs = pd.DataFrame({
            "Input": ["Industry", "Country", "Unlevered beta", "Tax rate used", "Tax choice",
                      "Debt share", "ERP", "U.S. 10Y yield", f"{country} 10Y yield",
                      "USD BBB spread", "Foreign spread override"],
            "Value": [industry, country, beta_u, tax, tax_choice, d, erp, rf_us_dec, rf_fx_dec,
                      bbb_pct/100, "on" if sfx_on else "off"],
        })
        out = pd.DataFrame({
            "Metric": ["Levered beta", "D/E", "Conversion factor F", "Cost of equity",
                       "Borrowing spread", "Pretax cost of debt", "After-tax cost of debt", "WACC"],
            "USD": [res["beta_e"], res["D_over_E"], 1.0, res["re_usd"], bbb_pct/100,
                    res["rd_usd"], res["rd_usd_at"], res["wacc_usd"]],
            ccy: [res["beta_e"], res["D_over_E"], res["F"], res["re_fx"], res["spread_fx"],
                  res["rd_fx"], res["rd_fx_at"], res["wacc_fx"]],
        })
        st.session_state["_dl_inputs"], st.session_state["_dl_out"] = inputs, out
        st.download_button("⬇️ Download results (CSV)",
                           pd.concat([inputs.rename(columns={"Input": "Item"}),
                                      out.rename(columns={"Metric": "Item"})]).to_csv(index=False),
                           file_name=f"cost_of_capital_{country.replace(' ', '_')}.csv",
                           mime="text/csv")

    # ======================= ALL INDUSTRIES =======================
    with tabs[1]:
        st.subheader(f"All industries — {country}, same tax, ERP, debt share and yields")
        if betas is None:
            st.info("Damodaran betas file unavailable; refresh to retry.")
            all_df = pd.DataFrame()
        else:
            rows = []
            for _, r in betas.iterrows():
                x = compute_cost_of_capital(r["Unlevered beta"], tax, d, erp, rf_us_dec,
                                            rf_fx_dec, bbb_pct/100, spread_fx_override)
                rows.append({"Industry": r["Industry"], "Firms": r["Number of firms"],
                             "Unlevered beta": r["Unlevered beta"], "Levered beta": x["beta_e"],
                             "Cost of equity USD": x["re_usd"], f"Cost of equity {ccy}": x["re_fx"],
                             "WACC USD": x["wacc_usd"], f"WACC {ccy}": x["wacc_fx"]})
            all_df = pd.DataFrame(rows).sort_values("WACC USD", ascending=False)
            fmt = {c: "{:.2%}" for c in all_df.columns if "Cost" in c or "WACC" in c}
            fmt.update({"Unlevered beta": "{:.2f}", "Levered beta": "{:.2f}", "Firms": "{:.0f}"})
            st.dataframe(
                all_df.style.format(fmt).apply(
                    lambda row: ["background-color: rgba(42,120,214,0.15)"
                                 if row["Industry"] == industry else "" for _ in row], axis=1),
                hide_index=True, width="stretch", height=420)
            try:
                import altair as alt
                long = all_df.melt(id_vars=["Industry"], value_vars=["WACC USD", f"WACC {ccy}"],
                                   var_name="Currency", value_name="WACC")
                order = all_df["Industry"].tolist()
                chart = alt.Chart(long).mark_bar(cornerRadiusEnd=3, height=6).encode(
                    y=alt.Y("Industry:N", sort=order, title=None, axis=alt.Axis(labelLimit=260)),
                    yOffset="Currency:N",
                    x=alt.X("WACC:Q", axis=alt.Axis(format="%"), title="WACC"),
                    color=alt.Color("Currency:N",
                                    scale=alt.Scale(range=["#2a78d6", "#eb6834"]),
                                    legend=alt.Legend(orient="top", title=None)),
                    tooltip=["Industry", "Currency", alt.Tooltip("WACC:Q", format=".2%")],
                ).properties(height=max(400, 18 * len(order)))
                with st.expander("Chart: WACC by industry", expanded=False):
                    st.altair_chart(chart, width="stretch")
            except Exception:  # noqa: BLE001
                pass
            st.download_button("⬇️ Download all-industry table (CSV)", all_df.to_csv(index=False),
                               file_name=f"all_industries_{country.replace(' ', '_')}.csv",
                               mime="text/csv")

        # Excel with everything
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as xw:
            st.session_state["_dl_inputs"].to_excel(xw, sheet_name="Inputs", index=False)
            st.session_state["_dl_out"].to_excel(xw, sheet_name="Results", index=False)
            if not all_df.empty:
                all_df.to_excel(xw, sheet_name="All industries", index=False)
        st.download_button("⬇️ Download everything (Excel)", buf.getvalue(),
                           file_name=f"cost_of_capital_{country.replace(' ', '_')}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    # ======================= DATA & SOURCES =======================
    with tabs[2]:
        st.subheader("Inputs, sources and dates")

        def status(manual, base):
            return "✏️ manual override" if manual else base

        src = pd.DataFrame([
            ["Unlevered beta — " + industry, f"{beta_u:.4f}" if beta_u is not None else "—",
             "Damodaran, betas.xls (U.S. industry averages)", BETAS_URL,
             betas_date or "—", status(beta_manual, betas_status)],
            ["Corporate tax rate — United States", f"{tax_us_pct:.2f}%" if tax_us_pct is not None else "—",
             tax_label, OECD_PAGE_URL, str(taxes.get("United States", (None, "—"))[1]),
             status(tax_us_manual, tax_status)],
            [f"Corporate tax rate — {country}", f"{tax_fx_pct:.2f}%" if tax_fx_pct is not None else "—",
             tax_label, OECD_PAGE_URL, str(taxes.get(country, (None, "—"))[1]),
             status(tax_fx_manual, tax_status if tax_fx_fetched is not None else "missing")],
            ["USD BBB spread (Baa2/BBB, large non-financial)", f"{bbb_pct:.2f}%",
             "Damodaran, ratings.html", RATINGS_URL, bbb_date, status(bbb_manual, bbb_status)],
            ["U.S. 10Y government yield", f"{rf_us_pct:.4f}%", "Trading Economics",
             TE_BONDS_URL, rf_us_date or "—", status(rf_us_manual, rf_us_src)],
            [f"{country} 10Y government yield", f"{rf_fx_pct:.4f}%", "Trading Economics",
             TE_BONDS_URL, rf_fx_date or "—", status(rf_fx_manual, rf_fx_src)],
            [f"{country}-currency spread", pct(res["spread_fx"], 4),
             "Calculated: s* = s·(1+Rf*)/(1+Rf)" if not sfx_on else "User input", "—", "—",
             "✏️ manual override" if sfx_on else "auto"],
            ["Equity risk premium", pct(erp), "User assumption (default 6.5%)", "—", "—", "user"],
            ["Target debt share", pct(d, 0), "User assumption (default 30%)", "—", "—", "user"],
        ], columns=["Input", "Value", "Source", "URL", "Data date", "Status"])
        st.dataframe(src, hide_index=True, width="stretch",
                     column_config={"URL": st.column_config.LinkColumn("URL")})
        errs = {"Betas": betas_err, "BBB spread": bbb_err, "OECD tax": tax_err,
                "TE yields": yields_err}
        errs = {k: v for k, v in errs.items() if v}
        if errs:
            with st.expander("⚠️ Fetch notes (fallbacks in use)"):
                for k, v in errs.items():
                    st.write(f"**{k}:** {v}")
                st.caption("Fallback values are dated and labelled. Use *Refresh data* to retry, "
                           "or overwrite any value manually in the sidebar.")
        with st.expander("All 10-year yields loaded"):
            st.dataframe(pd.DataFrame([(k, v[0], v[1]) for k, v in sorted(yields.items())],
                                      columns=["Country", "10Y yield (%)", "Date"]),
                         hide_index=True, width="stretch")
        with st.expander("All corporate tax rates loaded"):
            st.dataframe(pd.DataFrame([(k, v[0], v[1]) for k, v in sorted(taxes.items())],
                                      columns=["Country", "Combined statutory CIT rate (%)", "Year"]),
                         hide_index=True, width="stretch")

    # ======================= METHOD =======================
    with tabs[3]:
        st.subheader("How the numbers are built")
        st.latex(r"\beta_E=\beta_U\left[1+(1-\tau)\frac{d}{1-d}\right]")
        st.latex(r"R_e=R_f+\beta_E\cdot ERP \qquad R_d=R_f+s \qquad "
                 r"WACC=(1-d)R_e+d\,R_d(1-\tau)")
        st.latex(r"F=\frac{1+R_f^*}{1+R_f}\qquad 1+R_e^*=(1+R_e)F\qquad s^*=s\,F\qquad "
                 r"R_d^*=R_f^*+s^*")
        st.latex(r"WACC^*=(1-d)R_e^*+d\,R_d^*(1-\tau)")
        st.markdown("""
**Main assumptions**
- **Risk benchmark:** risk is measured against the U.S. market; U.S. industry unlevered betas (Damodaran) proxy for project risk. CAPM omits other priced risks.
- **Leverage:** target debt share is constant; βE is relevered with the project's own *d* and tax rate, assuming debt and the tax shield have zero beta.
- **Risk-free rates:** 10-year government yields proxy for the risk-free rate in each currency. They include liquidity/term premia and may include sovereign default risk.
- **Currency conversion:** equal real rates across countries (Fisher) and relative PPP mean the yield ratio equals expected depreciation, so the same factor F converts the equity cost and, by default, the gross borrowing rate (s* = s·F).
- **No separate country equity premium:** currency risk is treated as largely diversifiable (exchange-rate betas near zero).
- **Taxes:** one project tax rate (U.S. or selected country, your choice) is applied in both currencies; interest is fully and immediately deductible in the financing currency.
- **Credit spread:** Damodaran's Baa2/BBB default spread for large non-financial firms is the USD borrowing spread.
""")

    # ======================= VERIFICATION =======================
    with tabs[4]:
        st.subheader("Built-in checks")
        checks = []
        col = compute_cost_of_capital(0.80, 0.35, 0.30, 0.065, 0.04, 0.12, 0.015)
        checks.append(["Reproduces course Colombia example (WACC 8.5265% USD / 16.0670% COP)",
                       f"{col['wacc_usd']*100:.4f}% / {col['wacc_fx']*100:.4f}%",
                       abs(col["wacc_usd"]-0.085265) < 5e-7 and abs(col["wacc_fx"]-0.160670) < 5e-7])
        eq = compute_cost_of_capital(beta_u, tax, d, erp, rf_us_dec, rf_us_dec, bbb_pct/100, None)
        checks.append(["Equal yields (Rf* = Rf), spread override off → identical results",
                       f"WACC {pct(eq['wacc_usd'],4)} vs {pct(eq['wacc_fx'],4)}; Re {pct(eq['re_usd'],4)} vs {pct(eq['re_fx'],4)}",
                       all(abs(eq[a]-eq[b]) < 1e-12 for a, b in
                           [("re_usd", "re_fx"), ("rd_usd", "rd_fx"), ("wacc_usd", "wacc_fx")])])
        z = compute_cost_of_capital(beta_u, tax, 0.0, erp, rf_us_dec, rf_fx_dec, bbb_pct/100,
                                    spread_fx_override)
        checks.append(["Zero debt → WACC = cost of equity (both currencies)",
                       f"USD {pct(z['wacc_usd'],4)} = {pct(z['re_usd'],4)}; FX {pct(z['wacc_fx'],4)} = {pct(z['re_fx'],4)}",
                       abs(z["wacc_usd"]-z["re_usd"]) < 1e-12 and abs(z["wacc_fx"]-z["re_fx"]) < 1e-12])
        base = compute_cost_of_capital(beta_u, tax, d, erp, rf_us_dec, rf_fx_dec, bbb_pct/100, None)
        ov = compute_cost_of_capital(beta_u, tax, d, erp, rf_us_dec, rf_fx_dec, bbb_pct/100, 0.05)
        checks.append(["Overriding the foreign-currency spread leaves USD results unchanged",
                       f"USD WACC {pct(base['wacc_usd'],4)} → {pct(ov['wacc_usd'],4)}; "
                       f"FX WACC {pct(base['wacc_fx'],4)} → {pct(ov['wacc_fx'],4)}",
                       all(base[k] == ov[k] for k in ["re_usd", "rd_usd", "wacc_usd"])])
        st.dataframe(pd.DataFrame([[n, v, "✅ pass" if ok else "❌ fail"] for n, v, ok in checks],
                                  columns=["Check", "Values", "Result"]),
                     hide_index=True, width="stretch")
        st.caption("Checks 2–4 use your current inputs. Check 4 sets the foreign spread to 5%.")


if __name__ == "__main__":
    main()
