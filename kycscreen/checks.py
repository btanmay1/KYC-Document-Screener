"""Screening rules. Each check returns zero or more Findings carrying a reason code, severity and evidence."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher

from .extract import Document


@dataclass
class Finding:
    code: str
    severity: str
    message: str
    evidence: str
    document: str = ""


def _tokens(name: str) -> list[str]:
    return [t for t in re.sub(r"[^A-Z ]", " ", name.upper()).split() if t]


def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def names_match(a: str, b: str, threshold: float = 0.85) -> bool:
    """Tolerates case, punctuation, dropped middle names and initials ('R. K. Sharma' ~ 'Rahul Kumar Sharma').
    Requires the surname and the first name (or its initial) to agree."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb or _sim(ta[-1], tb[-1]) < threshold:
        return False
    fa, fb = ta[0], tb[0]
    if len(fa) == 1 or len(fb) == 1:
        return fa[0] == fb[0]
    return _sim(fa, fb) >= threshold


def watchlist_score(name: str, entry: str) -> float:
    ta, te = _tokens(name), _tokens(entry)
    full = _sim(" ".join(sorted(ta)), " ".join(sorted(te)))
    first_last = _sim(f"{ta[0]} {ta[-1]}", f"{te[0]} {te[-1]}") if ta and te else 0.0
    return max(full, first_last)


class Screener:
    def __init__(self, rules: dict, watchlist: list[dict]):
        self.r = rules
        self.watchlist = watchlist
        self.pan_re = re.compile(rules["pan_regex"])

    def f(self, code: str, message: str, evidence: str, document: str = "") -> Finding:
        return Finding(code, self.r["severity"][code], message, evidence, document)

    def run(self, form: dict, docs: list[Document]) -> list[Finding]:
        out: list[Finding] = []
        by_type: dict[str, list[Document]] = {}
        for d in docs:
            by_type.setdefault(d.doc_type, []).append(d)
        submitted = date.fromisoformat(form["submitted_date"])

        # 1. Readability
        unreadable = by_type.get("UNREADABLE", []) + by_type.get("UNKNOWN", [])
        for d in unreadable:
            out.append(self.f("UNREADABLE_DOC", "Document has no readable text layer or unrecognised type; manual check required",
                              f"{d.path.name}: {len(d.text.strip())} characters extracted", d.path.name))

        # 2. Completeness (a missing category may be explained by an unreadable document)
        missing = []
        if "PAN" not in by_type:
            missing.append(("MISSING_ID_PROOF", "No PAN card found as identity proof"))
        if "AADHAAR" not in by_type and "UTILITY_BILL" not in by_type:
            missing.append(("MISSING_ADDRESS_PROOF", "No Aadhaar or utility bill found as address proof"))
        if "SALARY_SLIP" not in by_type:
            missing.append(("MISSING_INCOME_PROOF", "No salary slip found as income proof"))
        if len(missing) > len(unreadable):
            for code, msg in missing:
                out.append(self.f(code, msg, f"Document types found: {sorted(by_type)}"))

        # 3. PAN validity and match
        if not self.pan_re.match(form.get("pan", "")):
            out.append(self.f("PAN_INVALID_FORMAT", "PAN on application fails the individual PAN format (AAAPA9999A)", form.get("pan", "")))
        for d in by_type.get("PAN", []):
            doc_pan = d.fields.get("pan", "")
            if doc_pan and doc_pan != form.get("pan") and self.pan_re.match(doc_pan):
                out.append(self.f("PAN_MISMATCH", "PAN on card differs from PAN on application",
                                  f"card {doc_pan} vs application {form.get('pan')}", d.path.name))

        # 4. Cross-document identity consistency
        dob_form = date.fromisoformat(form["date_of_birth"])
        for d in docs:
            name = d.fields.get("name")
            if name and not names_match(name, form["full_name"], self.r["name_token_similarity"]):
                out.append(self.f("NAME_MISMATCH", f"Name on {d.doc_type} does not match application",
                                  f"'{name}' vs '{form['full_name']}'", d.path.name))
            dob = d.fields.get("dob")
            if dob and dob != dob_form:
                out.append(self.f("DOB_MISMATCH", f"Date of birth on {d.doc_type} does not match application",
                                  f"{dob:%d-%m-%Y} vs {dob_form:%d-%m-%Y}", d.path.name))
            pin = d.fields.get("pin")
            if pin and pin != form["pin_code"]:
                out.append(self.f("ADDRESS_PIN_MISMATCH", f"PIN code on {d.doc_type} does not match application",
                                  f"{pin} vs {form['pin_code']}", d.path.name))

        # 5. Address proof recency
        for d in by_type.get("UTILITY_BILL", []):
            bd = d.fields.get("bill_date")
            if bd and (submitted - bd).days > self.r["address_proof_max_age_days"]:
                out.append(self.f("STALE_ADDRESS_PROOF", f"Utility bill older than {self.r['address_proof_max_age_days']} days at submission",
                                  f"bill dated {bd:%d-%b-%Y}, {(submitted - bd).days} days before submission", d.path.name))

        # 6. Income reasonableness
        for d in by_type.get("SALARY_SLIP", []):
            net = d.fields.get("net_pay")
            if net:
                ratio = form["declared_annual_income"] / (net * 12)
                if ratio > self.r["income_tolerance_ratio"]:
                    out.append(self.f("INCOME_MISMATCH", "Declared income well above income evidenced by salary slip",
                                      f"declared Rs. {form['declared_annual_income']:,} vs net pay x 12 = Rs. {net * 12:,} ({ratio:.2f}x)",
                                      d.path.name))

        # 7. Privacy: Aadhaar must be masked
        for d in by_type.get("AADHAAR", []):
            if re.search(r"\b[2-9]\d{3}\s\d{4}\s\d{4}\b", d.text):
                out.append(self.f("UNMASKED_AADHAAR", "Full Aadhaar number visible; document must be masked before storage",
                                  "12-digit number present (not reproduced here)", d.path.name))

        # 8. Watchlist screening
        best = max(((watchlist_score(form["full_name"], w["name"]), w) for w in self.watchlist), key=lambda x: x[0])
        if best[0] >= self.r["watchlist_similarity"]:
            out.append(self.f("WATCHLIST_HIT", "Applicant name is a potential match to a watchlist entry",
                              f"'{form['full_name']}' ~ '{best[1]['name']}' ({best[1]['list']}), score {best[0]:.2f}"))

        # 9. Document tampering indicators from PDF metadata
        for d in docs:
            prod = d.producer.lower()
            edited = d.created and d.modified and (d.modified - d.created).days > self.r["metadata_max_edit_gap_days"]
            tool = next((t for t in self.r["known_editing_tools"] if t in prod), None)
            if tool or edited:
                why = f"producer '{d.producer}'" if tool else f"created {d.created}, modified {d.modified}"
                out.append(self.f("METADATA_TAMPER", f"{d.doc_type} PDF shows signs of editing", why, d.path.name))
        return out


def route(findings: list[Finding], rules: dict) -> str:
    sev = {f.severity for f in findings}
    if "High" in sev:
        return rules["routing"]["High"]
    if "Medium" in sev:
        return rules["routing"]["Medium"]
    return rules["routing"]["none"]
