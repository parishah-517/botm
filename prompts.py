"""Prompt templates and the structured-output schema for the extraction call."""

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "is_guarantee_request": {
            "type": "boolean",
            "description": (
                "True only if the member is asking to redeem the Reader's Guarantee "
                "(a coupon/credit for a book they didn't like) for a specific box. "
                "False for damaged/wrong item, billing, cancellation, reactivation, "
                "or general account questions -- even if the word 'guarantee' appears."
            ),
        },
        "book_titles": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Book title(s) the member references in connection with a guarantee ask. Empty if none.",
        },
        "complaint_specificity": {
            "type": "string",
            "enum": ["vague", "specific"],
            "description": (
                "'specific' if the member explains a concrete, checkable reason the book "
                "didn't work (pacing, genre/tone mismatch vs. the description, DNF at a "
                "specific point, content they weren't expecting). 'vague' for generic "
                "statements like 'didn't enjoy it', 'not what I wanted', 'wasn't for me' "
                "with no supporting detail. If not a guarantee request, use 'vague'."
            ),
        },
        "complaint_summary": {
            "type": "string",
            "description": "One sentence paraphrasing the member's specific situation, for use in a personalized reply. Empty string if not applicable.",
        },
        "self_reported_same_cycle_conflict": {
            "type": "boolean",
            "description": (
                "True ONLY if the member explicitly claims to have already used the "
                "guarantee THIS SAME box/cycle (e.g. 'I already used it this month', "
                "'I just used the guarantee earlier this month') in a way that would "
                "matter for a same-cycle policy check. False for vague/general references "
                "to past usage ('I've used this before', 'a couple times recently')."
            ),
        },
        "self_reported_usage_note": {
            "type": ["string", "null"],
            "description": "If the member mentions their own guarantee usage history at all (any specificity), a short paraphrase of what they claimed. Null if they didn't.",
        },
        "multiple_distinct_requests": {
            "type": "boolean",
            "description": "True if the ticket bundles more than one distinct guarantee request (different books and/or different months) in a single message.",
        },
        "other_issue_type": {
            "type": ["string", "null"],
            "description": (
                "If is_guarantee_request is false, a short label for what this actually is: "
                "one of 'damaged_item', 'wrong_item_shipped', 'billing_question', "
                "'cancellation_retention', 'reactivation_policy_question', "
                "'account_question', or a brief custom label. Null if is_guarantee_request is true."
            ),
        },
    },
    "required": [
        "is_guarantee_request",
        "book_titles",
        "complaint_specificity",
        "complaint_summary",
        "self_reported_same_cycle_conflict",
        "self_reported_usage_note",
        "multiple_distinct_requests",
        "other_issue_type",
    ],
    "additionalProperties": False,
}

EXTRACTION_SYSTEM = """You triage member-service tickets for a monthly book subscription box's \
Reader's Guarantee program (a one-time-per-box credit/coupon for members who didn't like \
their book). Read the ticket and extract structured facts about it. Do not decide the \
outcome -- another system applies the redemption policy. Just report what the ticket says, \
accurately and conservatively. Distinguish a genuine, specific complaint (names what didn't \
work and why) from a vague one (just says they didn't like it). Only flag \
self_reported_same_cycle_conflict when the member is unambiguous about "this exact box/cycle," \
not general "I've done this before" statements."""


def extraction_user_prompt(ticket, member) -> str:
    return f"""Ticket ID: {ticket['ticket_id']}
Received: {ticket['received_date']}
Subject: {ticket['subject']}
Body:
\"\"\"
{ticket['body']}
\"\"\"

Member context (for reference only -- do not use this to decide policy, just to \
understand plausibility of what they're claiming):
- Plan: {member.plan_type}, status: {member.status}
- Member for {member.months_active} months
- Ship rate (fraction of boxes actually shipped/kept): {member.ship_rate}
- Guarantee redemptions on file: {int(member.total_guarantee_requests)} total, \
{int(member.guarantee_requests_last_12mo)} in the trailing 12 months
- Last redemption on file: {member.last_request_date or 'none on record'}
"""


DRAFT_SYSTEM = """You draft member-service email replies for a monthly book subscription box \
company's Reader's Guarantee program. A policy decision has already been made by another \
system -- your only job is to write the reply. Ground rules:

- Reference the member's actual, specific situation (the book they named, what they said \
didn't work, their tenure if relevant) -- never a generic template. A reply that could be \
sent to any member unchanged is a failure.
- If denying, be warm but firm: acknowledge their specific frustration, explain the actual \
policy reason plainly (don't hide behind vague corporate language), and don't over-apologize \
or imply the decision might change if they push back.
- If approving, confirm the credit/coupon clearly and keep it short -- don't over-explain.
- If escalating, write the reply as a holding note that lets the member know a person will \
follow up, without pre-committing to any particular outcome (don't promise an exception, don't \
promise a denial).
- Never mention internal policy names like "annual cap" or system field names. Speak like a \
person, not a policy document.
- Sign off as "The Member Experience Team" (no invented individual name).
- Output only the email body -- no subject line, no explanation of your reasoning."""


def draft_user_prompt(ticket, member, decision, extraction) -> str:
    reasons_block = "\n".join(f"- {r}" for r in decision.reasons)
    return f"""Member's original message:
\"\"\"
{ticket['body']}
\"\"\"

Decision already made: {decision.action}
Category: {decision.category}
Why (internal reasoning -- do not quote this verbatim, use it to inform tone/specifics):
{reasons_block}

Facts you can reference naturally if helpful:
- Book(s) mentioned: {', '.join(extraction.book_titles) or 'none specifically named'}
- Member has been with us {member.months_active} months, plan: {member.plan_type}
- Their complaint, paraphrased: {extraction.complaint_summary or 'n/a'}

Write the email reply now."""
