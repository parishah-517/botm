# Reader's Guarantee Ticket Triage

Given a batch of member-service tickets and a member activity ledger, this
automatically:

1. Classifies each ticket (`eligible_request`, `monthly_cap_violation`,
   `annual_cap_violation`, `legitimate_complaint_past_cap`, or `other`)
2. Decides an action (`auto_issue_coupon`, `auto_deny_with_explanation`, or
   `escalate_to_human`)
3. Drafts a personalized reply to the member

Claude is used to *read and understand* each ticket's free text. The
business rule (caps, categories, actions) is plain Python -- Claude never
decides policy, it only reports facts about the ticket for the policy code
to act on. See **How it works** below for why.

## Setup

```bash
python3 -m pip install --user anthropic pandas openpyxl
export ANTHROPIC_API_KEY=sk-ant-...
```

## Running it

```bash
python3 run.py              # all tickets in data/tickets.csv
python3 run.py --limit 3     # just the first 3, for a quick/cheap check
```

This calls Claude twice per ticket (once to extract facts, once to draft the
reply), so a full run of N tickets costs 2N API calls.

**Output:**

- `output/results.csv` -- one row per ticket: category, action, the
  reasoning behind the decision, the extracted facts, and the drafted
  reply, all in one file.

To check the decision logic itself with no API calls at all:

```bash
python3 test_policy.py
```

## Files

| File | What it does |
|---|---|
| `policy.py` | The actual business rule. Pure functions, no API calls -- given facts about a ticket and a member's ledger, decides category + action. |
| `prompts.py` | The two Claude prompts: one to extract structured facts from a ticket, one to draft the reply once a decision is made. |
| `run.py` | Orchestration -- loads the CSVs, calls Claude, calls the policy, writes `output/results.csv`. |
| `test_policy.py` | Unit tests for `policy.py`, including the trickier scenarios (see below). Runs in under a second, no network. |
| `data/tickets.csv`, `data/member-activity.csv` | Input data (exported from the provided `.xlsx`). |

## How it works

For each ticket, in order:

```
ticket text ──► Claude (extract facts) ──► policy.decide() ──► Claude (draft reply) ──► output/results.csv
                                                 ▲
                                    member's ledger (member-activity.csv)
```

**Step 1 -- extract.** Claude reads the raw ticket and returns a small
structured object: is this actually a guarantee request, how specific is
the complaint (a real reason vs. "just didn't like it"), did the member
claim they'd already used the guarantee recently, does the ticket bundle
more than one request. Claude never says what *should happen* -- only what
the ticket *says*.

**Step 2 -- decide (no LLM, just code).** `policy.decide()` applies the
actual rule:

1. Not a guarantee request at all (damaged book, wrong item, billing,
   cancellation question, etc.) → `other`, escalate to a human. A coupon
   can't fix a billing question.
2. Already used the guarantee within the last 30 days (checked against the
   member's actual ledger, not their word) → `monthly_cap_violation`, deny.
   This is a hard, objective rule -- doesn't matter how good the complaint
   is, you don't get a second swap for the same box.
3. Already at 3 redemptions in the trailing 12 months → depends on the
   complaint:
   - Generic ("wasn't for me," no detail) → `annual_cap_violation`, deny.
   - Specific and genuine (names what didn't work and why) →
     `legitimate_complaint_past_cap`, escalate to a human instead of
     auto-denying. This is the deliberate judgment call: a member who is
     over the cap but clearly engaging honestly deserves a person's
     discretion, not a form letter.
4. Otherwise → `eligible_request`, auto-approve.

Two overrides apply on top of that, regardless of category:

- **Multiple asks in one ticket** (e.g. two different books/months
  bundled into one email) → force escalation. Don't guess which one(s) to
  approve.
- **Self-reported history that contradicts our records** (member says "I
  already used this earlier this month" but the ledger shows nothing) →
  force escalation. Don't blindly trust our data (could be a lag or a
  missed sync) or blindly trust the member's word -- flag it for a person
  to actually check.

**Step 3 -- draft.** Once the decision is fixed, a second Claude call
writes the reply, told what was decided and why, with instructions to:
reference the member's actual book/complaint (never a generic template),
be warm but firm on denials (explain the real reason plainly, no
over-apologizing, don't leave the door open), and keep escalation replies
as a holding note rather than a promise of a particular outcome.

### Why caps and thresholds are what they are

- **30-day monthly window:** a guarantee redemption is tied to *this* box.
  A second one 30+ days later is a different box/cycle; a second one days
  later is the same one.
- **3 redemptions / trailing 12 months:** not given by the assignment --
  inferred from the member ledger itself, where `guarantee_requests_last_12mo`
  never exceeds 3 across all 500 members. That ceiling is already acting
  like the de facto policy in the data, so it's made explicit here.

### A subtlety worth calling out: same-batch double-dipping

Tickets are processed in chronological order, and each member's ledger is
updated *in memory* the moment a request is approved -- not just read once
from the CSV. This matters because two tickets from the *same* member,
filed a day apart, can each look "under cap" if checked independently
against the static CSV snapshot. Processing in order and updating state as
you go means the second ticket correctly sees that the first one was just
approved, and gets caught as a monthly cap violation instead of being
double-approved.

## Limitations / what's not handled

- The "specific vs. vague complaint" call is a judgment Claude makes per
  ticket -- it isn't checked against human-labeled examples. Before trusting
  it unsupervised, it'd be worth building a small labeled eval set.
- Self-reported conflicts and multi-request tickets are escalated, not
  resolved -- there's no integration with an actual order system to verify
  what really happened, so a human still has to look.
- Cap thresholds (30 days, 3/year) are this system's own inference from the
  data, not a given business rule -- worth confirming against the actual
  policy before using this in production.
