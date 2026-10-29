"""The Terms of Use and Privacy Notice, and the record that someone agreed.

All the wording lives here so it can be replaced wholesale when Legal has
reviewed it, without going near the server. Bump VERSION when the text
changes in a way people should see: everyone is asked again, because consent
to the old wording is not consent to the new.

Everything below is a placeholder written by an engineer. It says so at the
top of both pages, and it should not survive contact with a lawyer.
"""

import os

# Shown on both pages and stored with each consent. Any change people should
# re-agree to is a new version; fixing a typo is not.
VERSION = os.getenv("POLICY_VERSION", "2026-10-draft-1")

DRAFT_BANNER = ("Draft, pending review by SambaNova Legal. "
                "Written by the engineering team, not by lawyers.")

# Who receives what. Checked against the code rather than remembered:
# server.py and sambanova.py for the model, cartesia_tts.py for the voice,
# and search.py for the providers. The typefaces used to be on this list,
# loaded from Google on every page view; they are served from static/fonts
# now, so nothing about a visitor reaches Google before they have agreed to
# anything. If you add a service, add it here in the same breath.
PROCESSORS = [
    ("SambaNova",
     "Runs the language model and reads any photo you send. Receives what "
     "you say, what the agent says back, the documents it is answering from, "
     "and the images."),
    ("Cartesia",
     "Turns the reply into speech. Receives the text of the reply and "
     "nothing else — not your questions, not your details."),
    ("Your browser's speech recognition",
     "Turns your voice into text. In Chrome this is not done on your "
     "computer: the audio goes to Google. The agent only ever sees the text "
     "that comes back."),
    ("A web search provider",
     "Only when the agent searches the web, and only the search phrase it "
     "composed. Which company depends on configuration — Tavily, Brave, "
     "Serper, Exa or DuckDuckGo."),
]

COLLECTED = [
    ("What you say", "The text of your side of the call, after your browser "
                     "has transcribed it. The audio itself is never sent to "
                     "us and never stored."),
    ("What the agent says", "The text of the replies."),
    ("Your name and email", "Only if you give them, and only so the agent "
                            "can recognise you on a later call."),
    ("A short summary of the call", "Written when the call ends: what you "
                                    "wanted, what was agreed, and anything "
                                    "the agent could not answer."),
    ("Images you send", "Used to answer the question you sent them with. "
                        "Not stored after the call."),
]


def terms_text():
    return f"""
# Terms of Use

{DRAFT_BANNER}

## What this is

VoxNova is an internal demonstration of a voice support agent built on
SambaNova inference and Cartesia speech. It is not a support service. Nobody
is on the other end, no ticket is raised, and nothing you ask here reaches a
support team.

## The answers are generated, and may be wrong

Every reply is produced by a language model. It can be confidently wrong. It
has been built to say when it does not know something rather than guess, but
that is a tendency, not a guarantee. Do not rely on anything it tells you.

## The order data is invented

Every order, customer, address and delivery date in this demonstration is
made up for the purpose. The orders are a small fixed list in a file. None of
it describes a real person or a real purchase.

## Please do not enter real personal or sensitive information

Do not give it real payment details, real identity documents, real health
information, or anything about a real customer. It is a demonstration, and it
is not built to hold that kind of thing. Use the made-up customers.

## Acceptable use

Use it to see what it does. Do not try to break it, overload it, extract its
configuration, or use it to generate abusive or illegal material. Access is
shared with other teams; treat it as a shared machine.

## No warranty

It is provided as is, for internal evaluation, and may be changed or switched
off at any time.

Version {VERSION}
""".strip()


# Only true when the site sits behind Auth0 sign-in (AUTH_PROXY, set by the
# chart's auth.enabled), so only said then. The consent record lives in the
# server's memory with the session and goes when it does; the consent line
# in the log carries the time and version, never the email.
SIGNED_IN = [
    ("Your sign-in email", "When you accept these terms, the email you signed "
                           "in with is recorded with the time and the version "
                           "of the terms you accepted. It is kept as long as "
                           "the other consent records: until your session "
                           "ends, at most {hours} hours, or sooner if the "
                           "service restarts."),
    ("Your calls, under your sign-in email", "Signed in, you do not have to "
                                             "give your email on a call: the "
                                             "agent remembers your calls under "
                                             "the address you signed in with, "
                                             "for as long as any other call "
                                             "summary, and \"forget me\" "
                                             "deletes them."),
]
SIGNED_IN_BY = [
    ("Auth0 (Okta)", "Handles signing in. Sees your email address and your "
                     "login activity: when you sign in, and from where."),
]


def _signed_in():
    return os.getenv("AUTH_PROXY", "").lower() in ("1", "true", "yes", "on")


def privacy_text():
    hours = os.getenv("SESSION_HOURS", "12")
    extra = _signed_in()
    processors = "\n".join(f"- **{who}** — {what}" for who, what
                           in PROCESSORS + (SIGNED_IN_BY if extra else []))
    collected = "\n".join(f"- **{what}** — {why.format(hours=hours)}"
                          for what, why in COLLECTED + (SIGNED_IN if extra else []))
    days = os.getenv("RETENTION_DAYS", "30")
    return f"""
# Privacy Notice

{DRAFT_BANNER}

## The short version

It is a demonstration. Talk to it about made-up orders, not about yourself.
What you say is sent to other companies to be processed, and a summary of the
call is kept so the agent can recognise you next time. You can have it all
deleted by asking.

## What is collected

{collected}

## Why

So the agent can answer you, and so it can remember a previous call when you
come back — which is one of the things this demonstration exists to show.

## Who else processes it

Using this sends what you say to other companies. They are:

{processors}

## How long it is kept

Call summaries and the name and email attached to them are deleted after
{days} days. Images and audio are not kept at all. The transcript of a call
exists only while the call is open.

## Having your data deleted

Tell the agent to forget you — "delete my data" or "forget me" — and
everything held against your email is removed, and it will confirm. You can
also ask whoever runs this deployment to delete it for you.

## What is not done with it

It is not used to train any model, not sold, and not shared beyond the
companies listed above.

Version {VERSION}
""".strip()
