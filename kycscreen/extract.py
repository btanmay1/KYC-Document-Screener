"""Read a PDF, classify the document type from its text, and extract fields with regular expressions."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from pypdf import PdfReader

DOC_SIGNATURES = {
    "PAN": ["INCOME TAX DEPARTMENT", "PERMANENT ACCOUNT NUMBER"],
    "AADHAAR": ["UNIQUE IDENTIFICATION AUTHORITY", "AADHAAR"],
    "UTILITY_BILL": ["ELECTRICITY BILL", "CONSUMER NAME"],
    "SALARY_SLIP": ["SALARY SLIP", "NET PAY"],
}

PATTERNS = {
    "PAN": {"name": r"^Name:\s*(.+)$", "dob": r"Date of Birth:\s*(\d{2}/\d{2}/\d{4})", "pan": r"Permanent Account Number:\s*(\S+)"},
    "AADHAAR": {"name": r"^Name:\s*(.+)$", "dob": r"DOB:\s*(\d{2}/\d{2}/\d{4})", "aadhaar": r"Aadhaar No\.:\s*([X\d ]{14})",
                "pin": r"PIN Code:\s*(\d{6})"},
    "UTILITY_BILL": {"name": r"Consumer Name:\s*(.+)$", "pin": r"PIN Code:\s*(\d{6})", "bill_date": r"Bill Date:\s*(\d{2}-[A-Za-z]{3}-\d{4})"},
    "SALARY_SLIP": {"name": r"Employee Name:\s*(.+)$", "net_pay": r"Net Pay:\s*Rs\.\s*([\d,]+)", "period": r"Pay Period:\s*(.+)$"},
}


@dataclass
class Document:
    path: Path
    doc_type: str                     # PAN / AADHAAR / UTILITY_BILL / SALARY_SLIP / UNREADABLE / UNKNOWN
    text: str
    fields: dict = field(default_factory=dict)
    producer: str = ""
    created: date | None = None
    modified: date | None = None


def _pdf_date(v) -> date | None:
    if not v:
        return None
    m = re.match(r"D:(\d{8})", str(v))
    return datetime.strptime(m.group(1), "%Y%m%d").date() if m else None


def classify(text: str) -> str:
    if not text.strip():
        return "UNREADABLE"
    up = text.upper()
    scores = {t: sum(sig in up for sig in sigs) for t, sigs in DOC_SIGNATURES.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "UNKNOWN"


def parse_fields(doc_type: str, text: str) -> dict:
    out = {}
    for key, pat in PATTERNS.get(doc_type, {}).items():
        m = re.search(pat, text, flags=re.M)
        if m:
            out[key] = m.group(1).strip()
    if "dob" in out:
        out["dob"] = datetime.strptime(out["dob"], "%d/%m/%Y").date()
    if "bill_date" in out:
        out["bill_date"] = datetime.strptime(out["bill_date"], "%d-%b-%Y").date()
    if "net_pay" in out:
        out["net_pay"] = int(out["net_pay"].replace(",", ""))
    return out


def read_document(path: Path) -> Document:
    reader = PdfReader(str(path))
    text = "\n".join((p.extract_text() or "") for p in reader.pages)
    meta = reader.metadata or {}
    dtype = classify(text)
    return Document(path=path, doc_type=dtype, text=text, fields=parse_fields(dtype, text),
                    producer=str(meta.get("/Producer", "") or ""), created=_pdf_date(meta.get("/CreationDate")),
                    modified=_pdf_date(meta.get("/ModDate")))
