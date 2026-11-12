import json
import random
from datetime import date
from pathlib import Path

import pytest

from kycscreen.checks import Screener, names_match, route, watchlist_score
from kycscreen.extract import Document, classify, parse_fields, read_document
from kycscreen.generate import pdf

ROOT = Path(__file__).resolve().parent.parent
RULES = json.loads((ROOT / "config" / "rules.json").read_text())
WATCH = [{"name": "Viktor Aleksandr Petrenko", "list": "Test"}]
FORM = {"application_id": "T1", "full_name": "Rahul Kumar Sharma", "date_of_birth": "1990-05-17", "pan": "ABCPS1234Z",
        "pin_code": "110045", "declared_annual_income": 1_200_000, "submitted_date": "2026-10-01", "product": "Gold Card"}


def doc(dtype, created=date(2020, 1, 1), modified=None, producer="ReportLab PDF Library", text="x", **fields):
    return Document(path=Path(f"{dtype}.pdf"), doc_type=dtype, text=text, fields=fields, producer=producer,
                    created=created, modified=modified or created)


def clean_docs():
    return [doc("PAN", name="RAHUL K. SHARMA", dob=date(1990, 5, 17), pan="ABCPS1234Z"),
            doc("AADHAAR", name="Rahul Sharma", dob=date(1990, 5, 17), pin="110045", text="Aadhaar No.: XXXX XXXX 1234"),
            doc("UTILITY_BILL", name="R. Kumar Sharma", pin="110045", bill_date=date(2026, 9, 10)),
            doc("SALARY_SLIP", name="Rahul Kumar Sharma", net_pay=90_000)]


def codes(docs, form=FORM):
    return {f.code for f in Screener(RULES, WATCH).run(form, docs)}


@pytest.mark.parametrize("a,b,ok", [
    ("Rahul Kumar Sharma", "RAHUL KUMAR SHARMA", True),
    ("Rahul Kumar Sharma", "Rahul K. Sharma", True),
    ("Rahul Kumar Sharma", "R. Sharma", True),
    ("Rahul Kumar Sharma", "Rahul Sharma", True),
    ("Rahul Kumar Sharma", "Amit Sharma", False),
    ("Rahul Kumar Sharma", "Rahul Verma", False),
])
def test_name_matching(a, b, ok):
    assert names_match(a, b) is ok


def test_watchlist_fuzzy():
    assert watchlist_score("Viktor Petrenko", "Viktor Aleksandr Petrenko") >= 0.85
    assert watchlist_score("Viktor Aleksandr Petrenko", "Viktor Aleksandr Petrinko") >= 0.85
    assert watchlist_score("Rahul Kumar Sharma", "Viktor Aleksandr Petrenko") < 0.6


def test_clean_packet_clears():
    assert codes(clean_docs()) == set()
    assert route([], RULES) == "CLEAR"


def test_each_defect_is_caught():
    d = clean_docs()
    d[0].fields["pan"] = "ABCPS9999Z"
    d[1].text = "Aadhaar No.: 2345 6789 0123"
    d[2].fields["bill_date"] = date(2026, 4, 1)
    d[2].fields["pin"] = "560001"
    d[3].fields["net_pay"] = 40_000
    d[3].producer = "QuickPDF Editor 3.2"
    assert codes(d) == {"PAN_MISMATCH", "UNMASKED_AADHAAR", "STALE_ADDRESS_PROOF", "ADDRESS_PIN_MISMATCH",
                        "INCOME_MISMATCH", "METADATA_TAMPER"}


def test_missing_and_unreadable():
    d = clean_docs()[1:]                        # no PAN
    assert "MISSING_ID_PROOF" in codes(d)
    d2 = clean_docs()[1:] + [doc("UNREADABLE", text="")]
    assert codes(d2) == {"UNREADABLE_DOC"}      # missing PAN explained by the unreadable document


def test_routing_severity():
    fs = Screener(RULES, WATCH).run({**FORM, "full_name": "Viktor Petrenko"}, [])
    assert route(fs, RULES) == "ESCALATE"


def test_pdf_roundtrip(tmp_path):
    p = tmp_path / "x.pdf"
    p.write_bytes(pdf(["Name: RAHUL SHARMA", "Date of Birth: 17/05/1990", "Permanent Account Number: ABCPS1234Z"],
                      "INCOME TAX DEPARTMENT", created=date(2020, 1, 1), producer="Sejda PDF Editor", modified=date(2026, 9, 1)))
    d = read_document(p)
    assert d.doc_type == "PAN" and d.fields["pan"] == "ABCPS1234Z" and d.fields["dob"] == date(1990, 5, 17)
    assert d.producer == "Sejda PDF Editor" and d.modified == date(2026, 9, 1)
    assert classify("") == "UNREADABLE"
