"""Example state + typed questions (from the Laya model card) used for reference checks and calibration."""

STATE = {
    "from": "user@acme.com",
    "subject": "Duplicate charge on invoice #4411",
    "body": "Hi, we were billed twice for March. Please refund the duplicate today or we will cancel our plan.",
}

QUESTIONS = {
    "department": {
        "type": "choice",
        "instructions": "Which department should handle this request?",
        "criteria": {"billing": "invoices, payments, refunds", "technical": "bugs, outages, system errors",
                     "sales": "pricing, new contracts", "other": "everything else"},
    },
    "urgency": {
        "type": "score",
        "instructions": "How urgent is this request?",
        "criteria": ["not urgent", "soon", "critical deadline or blocking issue"],
    },
    "churn_risk": {"type": "noul", "instructions": "Does the user threaten to cancel or leave?"},
    "refund_requested": {"type": "noul", "instructions": "Does the user explicitly request a refund?"},
}

# Extra varied inputs for PTQ calibration / sanity sweeps.
EXTRA = [
    ("The app crashes every time I open settings on Android 14. Logs attached.", QUESTIONS),
    ("Can you send me pricing for 50 seats on the enterprise plan? We'd like to sign this quarter.", QUESTIONS),
    ("Thanks for the quick help yesterday, everything works now!", QUESTIONS),
    ({"ticket": 991, "text": "Our production API has been returning 500s for an hour, this is blocking all customers."},
     QUESTIONS),
]
