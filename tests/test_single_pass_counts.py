#!/usr/bin/env python3
"""Offline invariants for the derived single-pass tab-counts SQL (2026-09-19).

QUEUE_COUNTS_SQL stays the source of truth; QUEUE_COUNTS_FAST_SQL is generated
from it. These assertions are what make that derivation safe to trust: they
would have caught every mistake that was actually available to make here.
Run: venv/bin/python3 -m pytest tests/test_single_pass_counts.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from queues import (  # noqa: E402
    HIDDEN_EXCLUSION,
    QUEUE_COUNTS_FAST_SQL,
    QUEUE_COUNTS_SQL,
    _HIDDEN_KEYS_REF,
)

CHIPS = re.findall(r"SELECT\s+'([a-z_]+)'", QUEUE_COUNTS_SQL)


def _blocks(sql):
    return re.split(r"\n\s*UNION ALL\s*\n", sql.strip())


def test_generation_did_not_fall_back():
    assert QUEUE_COUNTS_FAST_SQL != QUEUE_COUNTS_SQL, "generator fell back to the original"


def test_same_sixteen_chip_names():
    fast = re.findall(r"SELECT\s+'([a-z_]+)' AS name", QUEUE_COUNTS_FAST_SQL)
    assert len(CHIPS) == 16, f"original has {len(CHIPS)} chips"
    assert sorted(fast) == sorted(CHIPS)
    assert len(fast) == len(set(fast)), "a chip is emitted twice"


def test_every_predicate_round_trips_to_the_original():
    """Each chip's predicate must be the ORIGINAL text, with only the repeated
    hidden-exclusion block swapped for the CTE reference."""
    for b in _blocks(QUEUE_COUNTS_SQL):
        name = re.search(r"SELECT\s+'([a-z_]+)'", b).group(1)
        tail = b.split("FROM", 1)[1]
        if "WHERE" not in tail:
            continue
        pred = tail.split("WHERE", 1)[1].strip()
        swapped = pred.replace(HIDDEN_EXCLUSION, _HIDDEN_KEYS_REF)
        assert swapped.replace(_HIDDEN_KEYS_REF, HIDDEN_EXCLUSION) == pred, name
        assert swapped in QUEUE_COUNTS_FAST_SQL, f"{name}: predicate text not carried over verbatim"


def test_not_in_semantics_preserved():
    """An anti-join would count differently if any excluded source_key were NULL."""
    assert _HIDDEN_KEYS_REF in QUEUE_COUNTS_FAST_SQL
    assert "NOT EXISTS (SELECT source_key FROM hidden_keys" not in QUEUE_COUNTS_FAST_SQL
    assert QUEUE_COUNTS_FAST_SQL.count(_HIDDEN_KEYS_REF) == QUEUE_COUNTS_SQL.count(HIDDEN_EXCLUSION)


def test_both_distinct_on_tiebreaks_are_unique():
    """created_at DEFAULTs to now(), which is the TRANSACTION timestamp, so a batch
    write leaves many rows tied. Without a unique tiebreak DISTINCT ON picks an
    arbitrary row, and the four event arms in the original could each pick a
    DIFFERENT one -- letting one source_key be counted by two chips at once. Both
    blocks must therefore order by (created_at DESC, id DESC)."""
    hidden = QUEUE_COUNTS_FAST_SQL.split("latest_decision AS")[0]
    latest = QUEUE_COUNTS_FAST_SQL.split("latest_decision AS")[1].split("pend AS")[0]
    for block, label in ((hidden, "hidden_keys"), (latest, "latest_decision")):
        assert "ORDER BY source_key, created_at DESC, id DESC" in block, label
    assert QUEUE_COUNTS_FAST_SQL.count("DISTINCT ON (source_key)") == 2
    # ...and the source-of-truth query must carry the same tiebreak, or the two
    # sides are not equivalent no matter how faithfully the rest is copied.
    assert QUEUE_COUNTS_SQL.count("ORDER BY source_key, created_at DESC, id DESC") == 16
    assert not re.search(r"ORDER BY source_key, created_at DESC\s*\n", QUEUE_COUNTS_SQL)


def test_event_cte_is_derived_from_the_original_not_hardcoded():
    """The builder used to hardcode this CTE, so an edit to the event subquery in
    QUEUE_COUNTS_SQL would have been silently ignored. Every non-blank line of the
    original subquery body must appear verbatim in the generated CTE."""
    # HIDDEN_EXCLUSION opens with the same words, so take the LAST DISTINCT ON
    # block before the first `) latest` -- that one is the event subquery.
    head = QUEUE_COUNTS_SQL.split(") latest", 1)[0]
    body = "SELECT DISTINCT ON (source_key)" + head.rsplit("SELECT DISTINCT ON (source_key)", 1)[1]
    cte = QUEUE_COUNTS_FAST_SQL.split("latest_decision AS MATERIALIZED (", 1)[1].split("pend AS")[0]
    for line in (ln.strip() for ln in body.splitlines()):
        if line:
            assert line in cte, f"dropped from derived CTE: {line!r}"


def test_each_relation_is_scanned_once():
    for rel, want in (("FROM operational_pending_sales", 1),
                      ("FROM identified_sales_current", 1),
                      ("FROM stage1_trusted_sales_current", 1),
                      ("FROM review_decision_events", 2)):  # the two CTEs
        assert QUEUE_COUNTS_FAST_SQL.count(rel) == want, rel


def test_counts_are_emitted_as_name_value_rows():
    """The endpoint does `for name, n in cur.fetchall()`."""
    assert QUEUE_COUNTS_FAST_SQL.count("::bigint AS n FROM") == 16


def test_no_comment_swallows_a_filter_closing_paren():
    """Predicates carry -- comments, so each FILTER's own closing paren must sit
    on its own line. If it ever shared a line with a comment it would be
    commented out and the SQL would not parse."""
    for chip in CHIPS:
        assert re.search(rf"^\s*\) AS {chip}\b", QUEUE_COUNTS_FAST_SQL, re.M), chip
    for line in QUEUE_COUNTS_FAST_SQL.splitlines():
        if "--" in line:
            assert not re.search(r"\)\s*AS\s+[a-z_]+\s*,?\s*$", line.split("--", 1)[1]), line
