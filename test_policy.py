"""Sanity checks for policy.py that don't require the API."""

from datetime import date

from policy import Extraction, MemberState, decide


def member(**overrides):
    base = dict(
        member_id="M0",
        plan_type="monthly",
        status="active",
        months_active=10,
        ship_rate=0.5,
        total_guarantee_requests=0,
        guarantee_requests_last_12mo=0,
        last_request_date=None,
    )
    base.update(overrides)
    return MemberState(**base)


def extraction(**overrides):
    base = dict(
        is_guarantee_request=True,
        book_titles=["Some Book"],
        complaint_specificity="specific",
        complaint_summary="Pacing was too slow, DNF at chapter 6.",
        self_reported_same_cycle_conflict=False,
        self_reported_usage_note=None,
        multiple_distinct_requests=False,
        other_issue_type=None,
    )
    base.update(overrides)
    return Extraction(**base)


def test_eligible():
    d = decide(date(2026, 5, 10), member(), extraction())
    assert d.category == "eligible_request"
    assert d.action == "auto_issue_coupon"


def test_monthly_cap():
    m = member(last_request_date=date(2026, 5, 1))
    d = decide(date(2026, 5, 10), m, extraction())
    assert d.category == "monthly_cap_violation"
    assert d.action == "auto_deny_with_explanation"
    assert d.monthly_cap_hit


def test_annual_cap_vague_complaint_denies():
    m = member(guarantee_requests_last_12mo=3, last_request_date=date(2026, 1, 1))
    d = decide(date(2026, 5, 10), m, extraction(complaint_specificity="vague"))
    assert d.category == "annual_cap_violation"
    assert d.action == "auto_deny_with_explanation"


def test_annual_cap_specific_complaint_escalates():
    m = member(guarantee_requests_last_12mo=3, last_request_date=date(2026, 1, 1))
    d = decide(date(2026, 5, 10), m, extraction(complaint_specificity="specific"))
    assert d.category == "legitimate_complaint_past_cap"
    assert d.action == "escalate_to_human"


def test_not_a_guarantee_request():
    d = decide(date(2026, 5, 10), member(), extraction(is_guarantee_request=False, other_issue_type="damaged_item"))
    assert d.category == "other"
    assert d.action == "escalate_to_human"


def test_multiple_requests_override_forces_escalate():
    d = decide(date(2026, 5, 10), member(), extraction(multiple_distinct_requests=True))
    assert d.category == "eligible_request"  # underlying facts still say eligible
    assert d.action == "escalate_to_human"  # but action is overridden


def test_self_reported_same_cycle_conflict_forces_escalate_even_if_records_show_eligible():
    d = decide(date(2026, 5, 10), member(), extraction(self_reported_same_cycle_conflict=True))
    assert d.category == "eligible_request"
    assert d.action == "escalate_to_human"


def test_monthly_cap_is_calendar_month_not_rolling_window():
    """Jan 31 -> Feb 1 is one day apart but a different calendar month, so it
    should NOT trip the monthly cap, even though it's well within 30 days."""
    m = member(last_request_date=date(2026, 1, 31))
    d = decide(date(2026, 2, 1), m, extraction())
    assert d.category == "eligible_request"
    assert d.action == "auto_issue_coupon"
    assert not d.monthly_cap_hit


def test_same_batch_double_dip():
    """Two tickets from the same member, 1 day apart, both individually 'under cap'
    against the static ledger -- but the second must see the first's approval."""
    m = member()
    d1 = decide(date(2026, 5, 10), m, extraction())
    assert d1.action == "auto_issue_coupon"
    m.record_redemption(date(2026, 5, 10))

    d2 = decide(date(2026, 5, 11), m, extraction())
    assert d2.category == "monthly_cap_violation"
    assert d2.action == "auto_deny_with_explanation"


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"OK  {t.__name__}")
    print(f"\n{len(tests)} tests passed.")
