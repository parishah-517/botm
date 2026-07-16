"""
End-to-end Reader's Guarantee ticket triage.

Usage:
    python3 run.py                 # process data/tickets.csv against data/member-activity.csv
    python3 run.py --limit 3       # just the first 3 (by processing order) for a quick look

Requires ANTHROPIC_API_KEY in the environment (or an `ant auth login` profile).
Writes output/results.csv (one row per ticket, machine-readable) and
output/drafts/<ticket_id>.txt (the drafted member-facing email for each ticket).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

import anthropic

from policy import Extraction, MemberState, decide
from prompts import (
    DRAFT_SYSTEM,
    EXTRACTION_SCHEMA,
    EXTRACTION_SYSTEM,
    draft_user_prompt,
    extraction_user_prompt,
)

MODEL = "claude-opus-4-8"
DATA_DIR = Path(__file__).parent / "data"
OUTPUT_DIR = Path(__file__).parent / "output"


def load_members() -> dict[str, MemberState]:
    members: dict[str, MemberState] = {}
    with open(DATA_DIR / "member-activity.csv", newline="") as f:
        for row in csv.DictReader(f):
            last_req = row["last_request_date"]
            members[row["member_id"]] = MemberState(
                member_id=row["member_id"],
                plan_type=row["plan_type"],
                status=row["status"],
                months_active=float(row["months_active"]),
                ship_rate=float(row["ship_rate"]),
                total_guarantee_requests=float(row["total_guarantee_requests"]),
                guarantee_requests_last_12mo=float(row["guarantee_requests_last_12mo"]),
                last_request_date=(
                    datetime.strptime(last_req, "%Y-%m-%d").date() if last_req else None
                ),
            )
    return members


def load_tickets() -> list[dict]:
    with open(DATA_DIR / "tickets.csv", newline="") as f:
        tickets = list(csv.DictReader(f))
    # Process in chronological order (then ticket_id) so that same-batch
    # state updates (see policy.py / WRITEUP.md) apply in the right order.
    tickets.sort(key=lambda t: (t["received_date"], t["ticket_id"]))
    return tickets


def extract(client: anthropic.Anthropic, ticket: dict, member: MemberState) -> Extraction:
    resp = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=EXTRACTION_SYSTEM,
        output_config={
            "format": {
                "type": "json_schema",
                "schema": EXTRACTION_SCHEMA,
            }
        },
        messages=[{"role": "user", "content": extraction_user_prompt(ticket, member)}],
    )
    text_block = next(b for b in resp.content if b.type == "text")
    data = json.loads(text_block.text)
    return Extraction(**data)


def draft_reply(client: anthropic.Anthropic, ticket, member, decision, extraction) -> str:
    resp = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=DRAFT_SYSTEM,
        messages=[
            {"role": "user", "content": draft_user_prompt(ticket, member, decision, extraction)}
        ],
    )
    return "\n".join(b.text for b in resp.content if b.type == "text").strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    client = anthropic.Anthropic()
    members = load_members()
    tickets = load_tickets()
    if args.limit:
        tickets = tickets[: args.limit]

    OUTPUT_DIR.mkdir(exist_ok=True)
    (OUTPUT_DIR / "drafts").mkdir(exist_ok=True)

    rows = []
    for ticket in tickets:
        member_id = ticket["member_id"]
        member = members.get(member_id)
        if member is None:
            print(f"WARN: {ticket['ticket_id']} references unknown member {member_id}", file=sys.stderr)
            continue

        ticket_date = datetime.strptime(ticket["received_date"], "%Y-%m-%d").date()

        extraction = extract(client, ticket, member)
        decision = decide(ticket_date, member, extraction)
        reply = draft_reply(client, ticket, member, decision, extraction)

        if decision.action == "auto_issue_coupon":
            member.record_redemption(ticket_date)

        rows.append(
            {
                "ticket_id": ticket["ticket_id"],
                "member_id": member_id,
                "received_date": ticket["received_date"],
                "category": decision.category,
                "action": decision.action,
                "is_guarantee_request": extraction.is_guarantee_request,
                "complaint_specificity": extraction.complaint_specificity,
                "monthly_cap_hit": decision.monthly_cap_hit,
                "annual_cap_hit": decision.annual_cap_hit,
                "reasons": " | ".join(decision.reasons),
                "book_titles": "; ".join(extraction.book_titles),
                "self_reported_same_cycle_conflict": extraction.self_reported_same_cycle_conflict,
                "multiple_distinct_requests": extraction.multiple_distinct_requests,
                "drafted_response": reply,
            }
        )
        (OUTPUT_DIR / "drafts" / f"{ticket['ticket_id']}.txt").write_text(reply + "\n")
        print(f"{ticket['ticket_id']} ({member_id}): {decision.category} -> {decision.action}")

    with open(OUTPUT_DIR / "results.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nWrote {len(rows)} results to {OUTPUT_DIR / 'results.csv'}")
    print(f"Drafted replies in {OUTPUT_DIR / 'drafts/'}")


if __name__ == "__main__":
    main()
