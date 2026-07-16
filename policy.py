"""
Deterministic policy layer for the Reader's Guarantee triage system.

Design principle: Claude is used for judgment calls that require reading and
understanding free text (is this actually a guarantee request? how specific/
genuine is the complaint? did the member claim something about their own
history?). Everything that can be computed from the ground-truth member
ledger (member-activity.csv) is computed here, deterministically, in code --
never inferred by the model. This keeps the "did we violate our own policy"
question auditable and keeps the LLM from having to do arithmetic on dates.

Thresholds (documented here, discussed at length in WRITEUP.md):

  MONTHLY_CAP_WINDOW_DAYS = 30
      A Reader's Guarantee redemption is tied to "this box" -- it doesn't
      make sense to grant a second one for the same shipment cycle. We treat
      any new request arriving within 30 days of the member's last approved
      request as hitting the monthly cap, independent of the annual count.

  ANNUAL_CAP = 3
      Looking at member-activity.csv, guarantee_requests_last_12mo tops out
      at 3 across all 500 members -- i.e. 3/year is already behaving like a
      soft ceiling in practice. We make it an explicit policy: a member who
      has already redeemed 3 times in the trailing 12 months is at the
      annual cap. A 4th request in that window is an annual_cap_violation
      *unless* the complaint is specific/genuine, in which case it becomes
      legitimate_complaint_past_cap and gets a human's judgment instead of a
      form-letter no.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

MONTHLY_CAP_WINDOW_DAYS = 30
ANNUAL_CAP = 3

CATEGORIES = (
    "eligible_request",
    "monthly_cap_violation",
    "annual_cap_violation",
    "legitimate_complaint_past_cap",
    "other",
)
ACTIONS = ("auto_issue_coupon", "auto_deny_with_explanation", "escalate_to_human")


@dataclass
class MemberState:
    """Mutable, in-memory view of a member's guarantee history.

    Starts as a copy of the ground-truth row from member-activity.csv and is
    updated as we walk through the ticket batch in chronological order, so
    that a second ticket from the same member later in the same batch sees
    the effect of the first ticket's decision (see WRITEUP.md, "same-batch
    double dip").
    """

    member_id: str
    plan_type: str
    status: str
    months_active: float
    ship_rate: float
    total_guarantee_requests: float
    guarantee_requests_last_12mo: float
    last_request_date: Optional[date]

    def record_redemption(self, on_date: date) -> None:
        self.last_request_date = on_date
        self.total_guarantee_requests += 1
        self.guarantee_requests_last_12mo += 1


@dataclass
class Extraction:
    """What we ask Claude to pull out of the raw ticket text.

    This is the *only* thing Claude decides based on reading the ticket --
    everything else (cap math, final category/action) is computed below in
    `decide`. Field meanings are documented in prompts.py where the schema
    is defined.
    """

    is_guarantee_request: bool
    book_titles: list[str]
    complaint_specificity: str  # "vague" | "specific"
    complaint_summary: str
    self_reported_same_cycle_conflict: bool
    self_reported_usage_note: Optional[str]
    multiple_distinct_requests: bool
    other_issue_type: Optional[str]


@dataclass
class Decision:
    category: str
    action: str
    reasons: list[str] = field(default_factory=list)
    monthly_cap_hit: bool = False
    annual_cap_hit: bool = False


def decide(
    ticket_date: date,
    member: MemberState,
    extraction: Extraction,
) -> Decision:
    reasons: list[str] = []

    # 1. Not actually a guarantee redemption request at all.
    if not extraction.is_guarantee_request:
        reasons.append(
            f"Not a Reader's Guarantee request (looks like: "
            f"{extraction.other_issue_type or 'general account/service question'})."
        )
        return Decision(category="other", action="escalate_to_human", reasons=reasons)

    # 2. Deterministic cap math against the (possibly batch-updated) ledger.
    monthly_hit = (
        member.last_request_date is not None
        and (ticket_date - member.last_request_date).days < MONTHLY_CAP_WINDOW_DAYS
    )
    annual_hit = member.guarantee_requests_last_12mo >= ANNUAL_CAP

    if monthly_hit:
        gap = (ticket_date - member.last_request_date).days
        reasons.append(
            f"Member already redeemed the guarantee {gap} day(s) ago "
            f"(within the {MONTHLY_CAP_WINDOW_DAYS}-day monthly window)."
        )
        category, action = "monthly_cap_violation", "auto_deny_with_explanation"
    elif annual_hit:
        reasons.append(
            f"Member has {int(member.guarantee_requests_last_12mo)} redemptions "
            f"in the trailing 12 months (cap is {ANNUAL_CAP})."
        )
        if extraction.complaint_specificity == "specific":
            reasons.append(
                "Complaint is specific/genuine, not a generic 'didn't like it' -- "
                "routing to a human for a discretionary call rather than a form denial."
            )
            category, action = "legitimate_complaint_past_cap", "escalate_to_human"
        else:
            reasons.append(
                "Complaint is generic/low-specificity -- a clean policy denial, "
                "no judgment call needed."
            )
            category, action = "annual_cap_violation", "auto_deny_with_explanation"
    else:
        category, action = "eligible_request", "auto_issue_coupon"
        reasons.append("Under both the monthly and annual caps; complaint reads as genuine.")

    # 3. Overrides that can bump the *action* to escalate without changing
    #    the underlying category -- these are about operational risk /
    #    ambiguity, not about whether the complaint itself is valid.
    if extraction.multiple_distinct_requests:
        action = "escalate_to_human"
        reasons.append(
            "Ticket bundles more than one distinct book/month request in a single "
            "message -- needs a human to confirm which request(s) apply before we "
            "issue anything."
        )

    if extraction.self_reported_same_cycle_conflict and not monthly_hit:
        action = "escalate_to_human"
        reasons.append(
            "Member explicitly states they already used the guarantee this same "
            "cycle, but that isn't reflected in our records -- verify in the order "
            "system before deciding rather than trusting either side blindly."
        )

    return Decision(
        category=category,
        action=action,
        reasons=reasons,
        monthly_cap_hit=monthly_hit,
        annual_cap_hit=annual_hit,
    )
