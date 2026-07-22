#!/usr/bin/env python3
"""Pos/neg unit tests for app.py _parse_comp_grade + _flag_price_outliers.

2026-07-14 fixes under test:
  - grade parser: "PSA9" (no separator) must parse as PSA 9, not Raw/Ungraded
    (the \\bPSA\\b word-boundary bug that put a $7,950 graded /10 gold into a
    raw chart's average);
  - price fence: comps >=3x or <=1/3 their grade group's median get flagged
    price_outlier and dropped from the average flags, while staying visible.

Run: venv/bin/python tests/comp_fence_tests.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import (  # noqa: E402
    PRICE_OUTLIER_MIN_COMPS,
    PRICE_OUTLIER_RATIO,
    _flag_price_outliers,
    _parse_comp_grade,
)

GRADE_CASES = [
    # (title, expected (company, value, group))
    ("2023 Mosaic Wemby International Gold /10 PSA9", ("PSA", "9", "PSA 9")),
    ("2023 Mosaic Wemby Silver PSA 10 RC", ("PSA", "10", "PSA 10")),
    ("Jaylen Waddle Mosaic Pink BGS9.5", ("BGS", "9.5", "BGS 9.5")),
    ("Kobe Bryant SGC10 GEM", ("SGC", "10", "SGC 10")),
    ("1980 Topps NOLAN RYAN PSA", ("PSA", "", "PSA")),
    ("2023 Prizm Wemby Silver RC", ("", "", "Raw/Ungraded")),
    ("Becket graded 9.5 vintage", ("BECKETT", "9.5", "BECKETT 9.5")),
    # company token inside a word must NOT match
    ("UPSA league card", ("", "", "Raw/Ungraded")),
    ("", ("", "", "Raw/Ungraded")),
]


def comp(price, group="Raw/Ungraded", obo=False, internal=False):
    return {
        "sale_price": price,
        "grade_group": group,
        "is_obo": obo,
        "is_internal_whatnot": internal,
        "included_in_average": True,
        "is_context_only": False,
        "average_policy": "average_safe",
        "display_context": "average_safe",
        "reason_excluded": "",
    }


def fence_case_wemby():
    """The live raw-tab scenario: cheap sales + one $7,950 spike + one OBO."""
    comps = [comp(30), comp(29.99), comp(36.95), comp(14.5), comp(7950), comp(250, obo=True)]
    _flag_price_outliers(comps)
    flags = [c["price_outlier"] for c in comps]
    spike = comps[4]
    return (
        flags == ["", "", "", "", "high", "high"]
        and spike["included_in_average"] is False
        and spike["is_context_only"] is True
        and spike["average_policy"] == "price_outlier_context_only"
        and spike["reason_excluded"] == "price_outlier_high_vs_grade_group_median"
        # OBO keeps its own average treatment (never averaged anyway)
        and comps[5]["average_policy"] == "average_safe"
    )


def fence_case_low_side():
    comps = [comp(30), comp(28), comp(33), comp(31), comp(0.5)]
    _flag_price_outliers(comps)
    return [c["price_outlier"] for c in comps] == ["", "", "", "", "low"]


def fence_case_too_few():
    """Below PRICE_OUTLIER_MIN_COMPS sold comps: no fence, nothing flagged."""
    comps = [comp(5), comp(6), comp(900)]
    _flag_price_outliers(comps)
    return all(c["price_outlier"] == "" for c in comps)


def fence_case_per_group():
    """Median is per grade group: a PSA 10 price never fences the raw group."""
    comps = [comp(5), comp(6), comp(7), comp(8),
             comp(500, group="PSA 10"), comp(520, group="PSA 10"),
             comp(510, group="PSA 10"), comp(505, group="PSA 10")]
    _flag_price_outliers(comps)
    return all(c["price_outlier"] == "" for c in comps)


def fence_case_internal_skipped():
    """THIS SALE (internal Whatnot anchor) is never flagged nor in the median."""
    comps = [comp(30), comp(31), comp(32), comp(33), comp(9000, internal=True)]
    _flag_price_outliers(comps)
    return comps[4]["price_outlier"] == "" and all(c["price_outlier"] == "" for c in comps[:4])


def fence_case_obo_not_in_median():
    """OBO prices never shape the median (4 sold + 1 huge OBO)."""
    comps = [comp(10), comp(11), comp(12), comp(13), comp(5000, obo=True)]
    _flag_price_outliers(comps)
    return comps[4]["price_outlier"] == "high" and all(c["price_outlier"] == "" for c in comps[:4])


CASES = [
    ("fence_wemby_raw_tab", fence_case_wemby),
    ("fence_low_side", fence_case_low_side),
    ("fence_too_few_comps_skipped", fence_case_too_few),
    ("fence_per_grade_group", fence_case_per_group),
    ("fence_internal_anchor_skipped", fence_case_internal_skipped),
    ("fence_obo_not_in_median", fence_case_obo_not_in_median),
]


def _main() -> int:
    total = passed = 0
    print("== _parse_comp_grade ==")
    for title, want in GRADE_CASES:
        total += 1
        got = _parse_comp_grade(title)
        ok = got == want
        passed += ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {title[:52]:<54} want={want} got={got}")
    print("== _flag_price_outliers ==")
    for name, fn in CASES:
        total += 1
        try:
            ok = fn()
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"  [FAIL] {name}: EXC {exc}")
        else:
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        passed += ok
    print(f"\n{passed}/{total} passed  (ratio={PRICE_OUTLIER_RATIO}, min_comps={PRICE_OUTLIER_MIN_COMPS})")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(_main())
