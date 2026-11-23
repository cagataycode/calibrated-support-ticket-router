"""All Jev question wording and thresholds for ticket routing.

Every question, criterion and threshold lives in this file so it can be audited
and changed in one place. Questions are plain JSON dicts in the shape the
/v1/systemone API expects, so they work with the SDK or a raw HTTP call.

State sent with every request (one request per ticket):

    {
      "company": {"name": str, "description": str},
      "ticket": {"title": str, "description": str, "reporter_role": str},
    }

Department descriptions are not in the state. Each one goes into the criteria
of the questions that need it, so the state only holds the ticket.
"""

MODEL = "jev-latest"

# Noul reading (skill default): above YES = yes, below NO = no, in between = unsettled.
YES = 0.7
NO = 0.3

# A Choice's top option is used as the main inbox only if its probability is at least this.
MAIN_INBOX_MIN = 0.6


def build_questions(departments: dict[str, str], first_line: bool = False) -> dict:
    """Return every question for one ticket. All of them share the ticket as state.

    first_line adds the tier judgment for organisations whose first-line desk keeps routine requests.
    """
    questions = {
        # Main inbox: exactly one owner.
        "main_department": {
            "type": "choice",
            "instructions": "Which team should own `ticket` and do most of the work on it?",
            "criteria": dict(departments),
        },
        # Companion Noul: a Choice always picks something, so ask separately whether anything fits.
        "fits_a_department": {
            "type": "noul",
            "instructions": "Is `ticket` work that one of these teams does? "
            + "; ".join(f"{name}: {desc}" for name, desc in departments.items()),
            "criteria": {
                "true": "The ticket clearly matches the work of at least one listed team.",
                "false": "The ticket is about something none of the listed teams handle, or is empty or unreadable.",
            },
        },
        # Only the parts a person needs to see before routing.
        "has_enough_detail": {
            "type": "noul",
            "instructions": "Does `ticket` say enough for the owning team to start work without asking the reporter a question first?",
            "criteria": {
                "true": "It says what happened or what is wanted, and where.",
                "false": "For example: only 'it doesn't work' or 'please help', with no hint of what or where.",
            },
        },
        "is_confidential_personal_matter": {
            "type": "noul",
            "instructions": "Is `ticket` about the reporter's own pay, health, contract, or a complaint about a named colleague?",
            "criteria": {
                "true": "Something a person would expect only the responsible team to read.",
                "false": "A work request that anyone in the company could see without harm.",
            },
        },
        # Urgency is split in two: how many people are blocked (Score), and whether harm keeps
        # growing until someone acts (Noul). Security is already covered by needs_security.
        "urgency": {
            "type": "score",
            "instructions": "How many people does the problem in `ticket` stop from working?",
            "criteria": [
                "Nobody is blocked; it can wait a week or more.",
                "One person is slowed down but can keep working.",
                "One person or one customer cannot do their work.",
                "Many people or many customers cannot do their work.",
            ],
        },
        "ongoing_loss": {
            "type": "noul",
            "instructions": "Does `ticket` describe money or data being lost or exposed right now, so that more is lost every hour nobody acts?",
            "criteria": {
                "true": "For example: a secret still public online, customers being charged wrongly as it happens, records being deleted.",
                "false": "The harm already happened once and is waiting to be fixed, such as one past charge to refund, or there is no loss.",
            },
        },
    }

    # Ticket fields that are separate from the team. Real helpdesks keep these apart: mixing them
    # into one department list (as the public dataset does) is what makes the departments overlap.
    questions["request_type"] = {
        "type": "choice",
        "instructions": "What kind of request is `ticket`?",
        "criteria": {
            "problem": "Something is broken, failing, slow or giving wrong results.",
            "question": "The writer asks how something works or asks for information, and nothing is broken.",
            "change_request": "The writer asks for a new feature, an improvement, or a change to how something is set up.",
            "service_request": "The writer asks the company to do something for them: a quote, a refund, a document, a piece of work.",
        },
    }
    questions["requester"] = {
        "type": "choice",
        "instructions": "Who wrote `ticket`?",
        "criteria": {
            "customer": "Someone at a client company who uses or buys our products.",
            "employee": "One of our own employees, writing about their own work, equipment or employment.",
            "job_candidate": "Someone applying for or asking about a job with us.",
        },
    }

    if first_line:
        # Tier, not topic: whether this needs a specialist at all.
        questions["first_line_can_resolve"] = {
            "type": "noul",
            "instructions": "Could a first-line service desk agent fully resolve `ticket` by following a standard procedure, without a specialist team?",
            "criteria": {
                "true": "A routine request or simple fix for one person, such as forwarding a phone, granting a standard folder, or replacing a faulty accessory.",
                "false": "It needs a specialist to investigate: a failure with an unknown cause, something affecting many people, a recurring problem, or a change to systems.",
            },
        }

    # Copies to other inboxes: several teams can be needed, so one Noul per team (speculative fan-out).
    for name, desc in departments.items():
        questions[f"needs_{name}"] = {
            "type": "noul",
            "instructions": f"Does `ticket` need some work from the {name} team?",
            "criteria": {"true": desc, "false": f"Nothing in the ticket is work for the {name} team."},
        }
    return questions
