"""SEC EDGAR (free, public). Endpoints used, all GET, all read-only:
  www.sec.gov/files/company_tickers.json            ticker -> CIK
  data.sec.gov/submissions/CIK##########.json       filings index (form, dates, accession, primary doc, 8-K items)
  data.sec.gov/api/xbrl/companyfacts/CIK#########.json  quarterly XBRL facts (revenue, net income, ...)
  www.sec.gov/Archives/edgar/data/{cik}/{acc}/index.json  files of one filing (Form 4 XML, 13F info table, EX-99.1)
SEC's fair-access policy: <= 10 requests/second and a User-Agent naming you and a contact address. The client reads the
agent string from the SEC_USER_AGENT env var and refuses to run without it. Parsers are pure functions on bytes/dicts."""
from __future__ import annotations
import re, time, html as htmlmod, datetime as dt
from html.parser import HTMLParser
import xml.etree.ElementTree as ET
import pandas as pd

REV_TAGS = ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax"]
METRIC_TAGS = {"revenue": REV_TAGS, "net_income": ["NetIncomeLoss"], "operating_income": ["OperatingIncomeLoss"], "gross_profit": ["GrossProfit"],
               "eps_diluted": ["EarningsPerShareDiluted"], "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"]}
SUFFIXES = r"\b(CORP|CORPORATION|INC|INCORPORATED|CO|COMPANY|LTD|LIMITED|PLC|HOLDINGS|HOLDING|GROUP|ADR|ADS|COM|NEW|DEL|N V|NV|S A|SA|CL A|CLASS A|CL B|CLASS B|COMMON|STOCK|SHS|SPONSORED|ORD|TRUST|ETF|FUND|LP|L P)\b"


# ---------------------------------------------------------------- pure parsers
def parse_company_tickers(obj: dict) -> dict[str, int]:
    return {str(v["ticker"]).upper(): int(v["cik_str"]) for v in obj.values() if v.get("ticker")}


def parse_submissions(obj: dict) -> pd.DataFrame:
    rec = obj["filings"]["recent"]; n = len(rec["accessionNumber"])
    df = pd.DataFrame({"form": rec["form"], "filing_date": rec["filingDate"], "report_date": rec.get("reportDate", [None] * n),
                       "accession": rec["accessionNumber"], "primary_doc": rec.get("primaryDocument", [None] * n), "items": rec.get("items", [""] * n)})
    df["cik"] = int(obj["cik"]); df["filing_date"] = pd.to_datetime(df.filing_date); df["report_date"] = pd.to_datetime(df.report_date, errors="coerce")
    return df


def earnings_events_from_index(idx: pd.DataFrame) -> pd.DataFrame:
    """8-K with item 2.02 (Results of Operations and Financial Condition). Event date = report date (the event the 8-K reports),
    filing date shown alongside, as the article requires."""
    if not len(idx): return pd.DataFrame(columns=["symbol", "cik", "event_date", "filing_date", "accession", "source"])
    k = idx[(idx.form == "8-K") & idx["items"].fillna("").astype(str).str.contains(r"\b2\.02\b")].copy()
    k["event_date"] = k.report_date.fillna(k.filing_date); k["source"] = "edgar 8-K item 2.02"
    return k[["symbol", "cik", "event_date", "filing_date", "accession", "source"]]


def _local(tag: str) -> str: return tag.split("}")[-1]


def _findtext(el, path_tags: list[str]) -> str | None:
    """Namespace-agnostic nested lookup: path_tags = ['transactionAmounts','transactionShares','value']."""
    cur = el
    for t in path_tags:
        nxt = next((c for c in cur if _local(c.tag) == t), None)
        if nxt is None: return None
        cur = nxt
    return (cur.text or "").strip() if cur is not None else None


def parse_form4_xml(xml_bytes: bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    owners = []
    for ro in root.iter():
        if _local(ro.tag) != "reportingOwner": continue
        name = _findtext(ro, ["reportingOwnerId", "rptOwnerName"]) or ""
        rel = next((c for c in ro if _local(c.tag) == "reportingOwnerRelationship"), None)
        parts = []
        if rel is not None:
            if (_findtext(rel, ["isDirector"]) or "").strip() in ("1", "true"): parts.append("Director")
            if (_findtext(rel, ["isOfficer"]) or "").strip() in ("1", "true"): parts.append(_findtext(rel, ["officerTitle"]) or "Officer")
            if (_findtext(rel, ["isTenPercentOwner"]) or "").strip() in ("1", "true"): parts.append("10% owner")
        owners.append((name, ", ".join(parts)))
    insider = "; ".join(n for n, _ in owners) or "unknown"; relationship = "; ".join(r for _, r in owners if r)
    out = []
    for tx in root.iter():
        if _local(tx.tag) != "nonDerivativeTransaction": continue
        def f(path):
            v = _findtext(tx, path)
            try: return float(v) if v not in (None, "") else None
            except ValueError: return None
        out.append({"transaction_date": _findtext(tx, ["transactionDate", "value"]), "code": _findtext(tx, ["transactionCoding", "transactionCode"]),
                    "acquired_disposed": _findtext(tx, ["transactionAmounts", "transactionAcquiredDisposedCode", "value"]),
                    "shares": f(["transactionAmounts", "transactionShares", "value"]), "price": f(["transactionAmounts", "transactionPricePerShare", "value"]),
                    "shares_after": f(["postTransactionAmounts", "sharesOwnedFollowingTransaction", "value"]), "insider": insider, "relationship": relationship})
    return out


def parse_13f_infotable(xml_bytes: bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes); out = []
    for it in root.iter():
        if _local(it.tag) != "infoTable": continue
        def f(path):
            v = _findtext(it, path)
            try: return float(v) if v not in (None, "") else None
            except ValueError: return None
        out.append({"issuer_name": _findtext(it, ["nameOfIssuer"]), "title_of_class": _findtext(it, ["titleOfClass"]), "cusip": (_findtext(it, ["cusip"]) or "").upper(),
                    "value": f(["value"]), "shares": f(["shrsOrPrnAmt", "sshPrnamt"]), "sh_type": _findtext(it, ["shrsOrPrnAmt", "sshPrnamtType"])})
    return out


def parse_index_json(obj: dict) -> list[str]:
    return [it["name"] for it in obj.get("directory", {}).get("item", []) if it.get("name")]


def pick_form4_xml(names: list[str], primary_doc: str | None) -> str | None:
    if primary_doc and primary_doc.lower().endswith(".xml"): return primary_doc.split("/")[-1]
    xmls = [n for n in names if n.lower().endswith(".xml") and not n.startswith("R") and "FilingSummary" not in n and not n.lower().startswith("xsl")]
    pref = [n for n in xmls if "form4" in n.lower() or "ownership" in n.lower() or "doc4" in n.lower()]
    return (pref or xmls or [None])[0]


def pick_13f_table(names: list[str], primary_doc: str | None) -> str | None:
    xmls = [n for n in names if n.lower().endswith(".xml") and not n.startswith("R") and "FilingSummary" not in n]
    pref = [n for n in xmls if "infotable" in n.lower() or "information" in n.lower() or "info_table" in n.lower()]
    rest = [n for n in xmls if primary_doc is None or n.split("/")[-1] != primary_doc.split("/")[-1]]
    return (pref or rest or [None])[0]


def pick_exhibit_99(names: list[str]) -> str | None:
    c = [n for n in names if re.search(r"ex[-_]?99", n, re.I) and n.lower().endswith((".htm", ".html", ".txt"))]
    c.sort(key=lambda n: (0 if re.search(r"99[-_.]?1", n) else 1, len(n)))
    return c[0] if c else None


class _Text(HTMLParser):
    def __init__(self): super().__init__(); self.parts = []; self.skip = 0
    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"): self.skip += 1
        if tag in ("p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "table"): self.parts.append("\n")
    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip: self.skip -= 1
        if tag in ("td", "th"): self.parts.append(" ")
    def handle_data(self, d):
        if not self.skip: self.parts.append(d)


def html_to_text(raw: bytes | str) -> str:
    s = raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes) else raw
    p = _Text(); p.feed(s); t = htmlmod.unescape("".join(p.parts))
    t = t.replace("\xa0", " "); t = re.sub(r"[ \t]+", " ", t); t = re.sub(r"\n\s*\n+", "\n\n", t)
    return t.strip()


MDNA = re.compile(r"management[’']?s discussion and analysis", re.I)
QQ = re.compile(r"quantitative and qualitative disclosures? about market risk", re.I)


def extract_mdna(text: str, max_chars: int = 150_000) -> str:
    """The MD&A body: among all 'Management's Discussion' occurrences take the segment that ends at the next
    'Quantitative and Qualitative' marker and is the longest (the table-of-contents occurrence is short)."""
    best = ""
    for m in MDNA.finditer(text):
        q = QQ.search(text, m.end())
        seg = text[m.start(): q.start()] if q else text[m.start(): m.start() + max_chars]
        if len(seg) > len(best): best = seg
    return best[:max_chars]


def parse_companyfacts(obj: dict, metric_tags: dict = METRIC_TAGS) -> pd.DataFrame:
    """Quarterly (frame CY####Q#) values from 10-Q/10-K/20-F/6-K facts; first tag with >= 4 quarterly frames wins per metric."""
    gaap = obj.get("facts", {}).get("us-gaap", {}); rows = []
    for metric, tags in metric_tags.items():
        for tag in tags:
            units = gaap.get(tag, {}).get("units", {}); found = []
            for unit, vals in units.items():
                for v in vals:
                    fr = v.get("frame") or ""
                    if re.fullmatch(r"CY\d{4}Q\d", fr):
                        found.append({"metric": metric, "tag": tag, "frame": fr, "end": v.get("end"), "val": v.get("val"), "form": v.get("form"), "filed": v.get("filed")})
            if len({f["frame"] for f in found}) >= 4:
                rows.extend(found); break
    df = pd.DataFrame(rows, columns=["metric", "tag", "frame", "end", "val", "form", "filed"])
    if len(df): df = df.sort_values(["metric", "frame", "filed"]).drop_duplicates(["metric", "frame"], keep="last")
    return df.reset_index(drop=True)


def normalize_issuer(name: str) -> str:
    s = re.sub(r"[^A-Z0-9 ]", " ", (name or "").upper()); s = re.sub(SUFFIXES, " ", s); return re.sub(r"\s+", " ", s).strip()


def match_issuer(issuer_name: str, universe: pd.DataFrame) -> str | None:
    n = normalize_issuer(issuer_name)
    if not n: return None
    for _, r in universe.iterrows():
        if n == normalize_issuer(r["name"]) or n == r["symbol"].upper(): return r["symbol"]
    for _, r in universe.iterrows():
        u = normalize_issuer(r["name"])
        if u and (n.startswith(u + " ") or u.startswith(n + " ")): return r["symbol"]
    return None


# ---------------------------------------------------------------- client (network injected)
class EdgarClient:
    def __init__(self, user_agent: str, get=None, sleep=None, max_rps: float = 8.0, data_url="https://data.sec.gov", www_url="https://www.sec.gov", timeout=30.0):
        if not user_agent or "@" not in user_agent:
            raise RuntimeError("SEC requires a User-Agent with a name and contact email, e.g. SEC_USER_AGENT='Jane Doe jane@example.com'")
        if get is None:
            import requests; get = requests.get
        self.ua = user_agent; self._get = get; self._sleep = sleep or time.sleep; self.min_gap = 1.0 / max_rps; self._last = 0.0
        self.data_url = data_url.rstrip("/"); self.www_url = www_url.rstrip("/"); self.timeout = timeout; self.requests_made = 0

    def _fetch(self, url: str, retries: int = 4):
        for i in range(retries):
            gap = self.min_gap - (time.time() - self._last)
            if gap > 0: self._sleep(gap)
            self._last = time.time(); self.requests_made += 1
            r = self._get(url, headers={"User-Agent": self.ua, "Accept-Encoding": "gzip, deflate"}, timeout=self.timeout)
            if r.status_code in (429, 503) and i < retries - 1: self._sleep(2.0 * (i + 1)); continue
            r.raise_for_status(); return r
        raise RuntimeError(f"EDGAR fetch failed: {url}")

    def get_json(self, url): return self._fetch(url).json()
    def get_bytes(self, url): return self._fetch(url).content

    def company_tickers(self) -> dict[str, int]: return parse_company_tickers(self.get_json(f"{self.www_url}/files/company_tickers.json"))
    def submissions(self, cik: int) -> pd.DataFrame: return parse_submissions(self.get_json(f"{self.data_url}/submissions/CIK{int(cik):010d}.json"))
    def companyfacts(self, cik: int) -> dict: return self.get_json(f"{self.data_url}/api/xbrl/companyfacts/CIK{int(cik):010d}.json")

    def filing_dir(self, cik: int, accession: str) -> str: return f"{self.www_url}/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}"
    def filing_files(self, cik: int, accession: str) -> list[str]: return parse_index_json(self.get_json(self.filing_dir(cik, accession) + "/index.json"))
    def filing_doc(self, cik: int, accession: str, name: str) -> bytes: return self.get_bytes(self.filing_dir(cik, accession) + "/" + name)

    def form4(self, cik: int, accession: str, primary_doc: str | None) -> list[dict]:
        name = pick_form4_xml(self.filing_files(cik, accession), primary_doc)
        return parse_form4_xml(self.filing_doc(cik, accession, name)) if name else []

    def table_13f(self, cik: int, accession: str, primary_doc: str | None) -> list[dict]:
        name = pick_13f_table(self.filing_files(cik, accession), primary_doc)
        return parse_13f_infotable(self.filing_doc(cik, accession, name)) if name else []

    def primary_text(self, cik: int, accession: str, primary_doc: str) -> str: return html_to_text(self.filing_doc(cik, accession, primary_doc.split("/")[-1]))

    def exhibit_99_text(self, cik: int, accession: str) -> str | None:
        name = pick_exhibit_99(self.filing_files(cik, accession))
        return html_to_text(self.filing_doc(cik, accession, name)) if name else None


# ---------------------------------------------------------------- ingest orchestrators (warehouse in, warehouse out)
def ingest_ciks(wh, client: EdgarClient, universe: pd.DataFrame) -> pd.DataFrame:
    m = client.company_tickers(); u = universe.copy(); u["cik"] = u.symbol.str.upper().map(m)
    wh.write("universe", u.assign(source="data/derived/rwa_meta.json + edgar company_tickers.json"), mode="replace"); return u


def ingest_filings_index(wh, client: EdgarClient, universe: pd.DataFrame, forms: list[str]) -> tuple[int, list]:
    rows, errors = [], []
    for _, r in universe.dropna(subset=["cik"]).iterrows():
        try:
            idx = client.submissions(int(r.cik)); idx = idx[idx.form.isin(forms)].copy(); idx["symbol"] = r.symbol
            idx["source"] = "edgar submissions"; idx["fetched_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"); rows.append(idx)
        except Exception as e: errors.append({"symbol": r.symbol, "step": "submissions", "error": str(e)})
    if not rows: return 0, errors
    idx = pd.concat(rows, ignore_index=True); n = wh.write("filings_index", idx, mode="append")
    wh.write("earnings_events", earnings_events_from_index(wh.read("filings_index")), mode="replace")
    return n, errors


def ingest_form4(wh, client: EdgarClient, universe: pd.DataFrame, lookback_days: int = 365, max_filings_per_symbol: int = 200) -> tuple[int, list]:
    idx = wh.read("filings_index"); since = pd.Timestamp.today().normalize() - pd.Timedelta(days=lookback_days)
    idx = idx[(idx.form == "4") & (idx.filing_date >= since) & idx.symbol.isin(set(universe.symbol))]
    done = set(wh.read("insider_form4").accession.astype(str)); rows, errors = [], []
    for sym, g in idx.groupby("symbol"):
        for _, f in g.sort_values("filing_date", ascending=False).head(max_filings_per_symbol).iterrows():
            if f.accession in done: continue
            try:
                for t in client.form4(int(f.cik), f.accession, f.primary_doc):
                    t.update({"symbol": sym, "cik": int(f.cik), "accession": f.accession, "filing_date": f.filing_date, "source": "edgar form 4"}); rows.append(t)
            except Exception as e: errors.append({"symbol": sym, "accession": f.accession, "step": "form4", "error": str(e)})
    n = wh.write("insider_form4", pd.DataFrame(rows), mode="append") if rows else len(done)
    return n, errors


def ingest_13f(wh, client: EdgarClient, filers: list[int], universe: pd.DataFrame, max_filings_per_filer: int = 8) -> tuple[int, list]:
    """Holdings of the configured filers in universe names. Issuer -> symbol by the cusip_map table first, then by
    normalised issuer name (recorded in cusip_map with matched_by='name' so the user can correct it)."""
    cmap = wh.read("cusip_map"); known = dict(zip(cmap.cusip.astype(str), cmap.symbol.astype(str))) if len(cmap) else {}
    rows, errors, new_map = [], [], []
    for fc in filers:
        try:
            sub = client.submissions(int(fc)); name = None
            try: name = client.get_json(f"{client.data_url}/submissions/CIK{int(fc):010d}.json").get("name")
            except Exception: pass
            hr = sub[sub.form.isin(["13F-HR", "13F-HR/A"])].sort_values("filing_date", ascending=False).head(max_filings_per_filer)
            for _, f in hr.iterrows():
                for it in client.table_13f(int(fc), f.accession, f.primary_doc):
                    sym = known.get(it["cusip"]) or match_issuer(it["issuer_name"] or "", universe)
                    if not sym: continue
                    if it["cusip"] not in known: known[it["cusip"]] = sym; new_map.append({"cusip": it["cusip"], "symbol": sym, "issuer_name": it["issuer_name"], "matched_by": "name"})
                    rows.append({"filer_cik": int(fc), "filer_name": name, "symbol": sym, "issuer_name": it["issuer_name"], "cusip": it["cusip"], "period_of_report": f.report_date,
                                 "filing_date": f.filing_date, "accession": f.accession, "shares": it["shares"], "value": it["value"], "source": "edgar 13F-HR information table"})
        except Exception as e: errors.append({"filer": fc, "step": "13f", "error": str(e)})
    if new_map: wh.write("cusip_map", pd.DataFrame(new_map), mode="append")
    n = wh.write("holdings_13f", pd.DataFrame(rows), mode="append") if rows else 0
    return n, errors


def ingest_xbrl(wh, client: EdgarClient, universe: pd.DataFrame) -> tuple[int, list]:
    rows, errors = [], []
    for _, r in universe.dropna(subset=["cik"]).iterrows():
        try:
            df = parse_companyfacts(client.companyfacts(int(r.cik)))
            if len(df): df["symbol"] = r.symbol; df["cik"] = int(r.cik); df["source"] = "edgar companyfacts xbrl"; rows.append(df)
        except Exception as e: errors.append({"symbol": r.symbol, "step": "xbrl", "error": str(e)})
    n = wh.write("xbrl_quarterly", pd.concat(rows, ignore_index=True), mode="append") if rows else 0
    return n, errors


def ingest_filing_text(wh, client: EdgarClient, universe: pd.DataFrame, quarters: int = 8, max_chars: int = 150_000) -> tuple[int, list]:
    """MD&A of the last `quarters` 10-Q/10-K (or the 6-K/20-F primary doc for foreign filers) and the EX-99.1 press
    release of each 8-K 2.02. This is the eight-quarter packet the deep-read stage compares."""
    idx = wh.read("filings_index"); have = set(wh.read("filing_text").accession.astype(str)); rows, errors = [], []
    for sym, g in idx[idx.symbol.isin(set(universe.symbol))].groupby("symbol"):
        q = g[g.form.isin(["10-Q", "10-K", "20-F"])].sort_values("filing_date", ascending=False).head(quarters)
        k = g[(g.form == "8-K") & g["items"].fillna("").astype(str).str.contains(r"\b2\.02\b")].sort_values("filing_date", ascending=False).head(quarters)
        for _, f in pd.concat([q, k]).iterrows():
            if f.accession in have: continue
            try:
                if f.form == "8-K":
                    txt = client.exhibit_99_text(int(f.cik), f.accession); section = "8-K EX-99.1 press release"
                else:
                    full = client.primary_text(int(f.cik), f.accession, f.primary_doc); txt = extract_mdna(full, max_chars) or full[:max_chars]; section = "MD&A"
                if not txt: continue
                rows.append({"symbol": sym, "cik": int(f.cik), "accession": f.accession, "form": f.form, "filing_date": f.filing_date, "report_date": f.report_date,
                             "section": section, "chars": len(txt), "text": txt[:max_chars], "source": "edgar filing document"})
            except Exception as e: errors.append({"symbol": sym, "accession": f.accession, "step": "text", "error": str(e)})
    n = wh.write("filing_text", pd.DataFrame(rows), mode="append") if rows else len(have)
    return n, errors
