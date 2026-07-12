"""
Topic Extractor for Slack Digest Agent.

Stage 1 of the Claude pipeline — extracts semantic topics from
individual Slack messages so they can be stored in the knowledge graph.

This runs on EVERY incoming message (via app.py listener).
Kept lightweight and fast — single short Claude call per message.
"""

import os
import json
import re
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

client = Anthropic()

EXTRACTION_SYSTEM_PROMPT = """
You are a topic extraction system for a workplace Slack knowledge graph.

Given a Slack message, extract 1-2 core topics that capture what the message 
is semantically ABOUT — not just keywords, but the underlying concept.

RULES:
- Topics should be 2-4 words max, lowercase
- Focus on the PROBLEM or SUBJECT being discussed, not the action
- Use consistent naming — "api rate limiting" not "rate limit issue" or "429 errors"
- If a message is social/off-topic (greetings, reactions, random chat), return []
- Resolution messages like 'it works now', 'fixed!', 'back to normal' should still
  be tagged with the topic they're resolving — infer it from context if possible
- Be semantic — "dashboard loading slowly" and "frontend timeout" 
  should both map to something like "frontend performance"
- CRITICAL — merge symptom and root cause into ONE topic: a message describing
  a SYMPTOM of an incident (e.g. "the dashboard keeps spinning and never loads")
  and a message describing the underlying ROOT CAUSE of that same incident
  (e.g. "the DB connection pool is maxing out") should map to the SAME topic,
  even though one is phrased from the user/frontend side and the other from
  the backend/systems side. Do not split one real incident into a
  frontend-flavored topic and a backend-flavored topic just because different
  people describe it from different angles or don't mention the cause.
- If you're given the thread-starting message as context, a short or vague
  reply ("yeah that fixed it", "still happening", "on it") should be tagged
  with whatever topic the thread-starting message is about — don't return []
  just because the reply itself has no topic words.

When you're given a list of topics that already exist in the graph, treat it
as your first priority: before inventing a new topic, check whether this
message could plausibly be a symptom of, a cause of, a fix for, or a
follow-up to one of the existing topics. If so, reuse that exact string.
Only create a new topic when the message is genuinely about something else.

Be conservative about returning a SECOND topic. A wrong extra topic creates
a false connection between two unrelated conversations, which is worse than
missing a real one. Only include a second topic if the message is clearly
and substantially about two distinct subjects — not because it loosely
shares a word or a general theme (e.g. "login" and "signup" are DIFFERENT
topics even though both involve account access; don't tag one with the
other's topic just because they're in the same general area).

Concrete example of that distinction: "people can't log back in after a
password reset" is about LOGIN/AUTHENTICATION (an existing user failing to
access their account) — it is NOT "onboarding" or "signup flow" (a new user
joining), even though both are technically "account access" topics. Match
on what actually happened, not the general category.

Be conservative about REUSING an existing topic too, not just about adding
a second one. Only reuse an existing topic name if you're genuinely
confident this message is the same real-world incident or subject. If it's
only a loose or plausible-sounding match, create a new, more specific topic
instead — a wrong reuse merges two unrelated conversations, which is worse
than temporarily having two topics that a later, clearer message can still
consolidate.

Respond with ONLY a JSON array of topic strings — nothing else. No
explanation, no markdown code fences, no text before or after the array.
Examples:
["api rate limiting"]
["user authentication", "session management"]  
["deployment pipeline"]
[]
"""


def _parse_topics_response(raw: str) -> list[str]:
    """
    Parses Claude's response into a list of topic strings.

    Models occasionally add a stray line before/after the JSON array even
    when told not to (e.g. a trailing aside), which breaks a naive
    json.loads(). This tries a direct parse first, then falls back to
    pulling out just the [...] array via regex if there's extra text
    around it, instead of failing the whole extraction.
    """
    raw = raw.strip()

    # Strip markdown code fences if present, e.g. ```json [...] ```
    if raw.startswith("```"):
        raw = raw.strip("`").strip()
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()

    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        pass

    match = re.search(r"\[.*?\]", raw, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            pass

    return []


def extract_topics(
    message_text: str,
    existing_topics: list[str] | None = None,
    thread_context: str | None = None,
) -> list[str]:
    """
    Extracts semantic topics from a single Slack message.
    Returns a list of topic strings (may be empty for off-topic messages).

    existing_topics: topic names already in the graph. When provided, the
    model is told to reuse an existing name if the message is about the
    same underlying subject, instead of inventing a near-duplicate label
    (e.g. "jwt" vs "user authentication" vs "login issue"). This is what
    makes cross-channel [:ABOUT] connections actually fire in practice.

    thread_context: the text of the thread-starting message, if this
    message is a reply in a thread. Short replies like "yeah that fixed
    it" or "still happening" have no topic signal on their own — without
    the parent message, extraction on them is a coin flip.
    """
    if not message_text or (len(message_text.strip()) < 10 and not thread_context):
        return []

    existing_topics = existing_topics or []
    parts = []

    if thread_context:
        parts.append(
            f"This message is a reply in a thread. The thread-starting "
            f"message was:\n\"{thread_context}\"\n\n"
            f"Use that context to understand what this reply refers to, "
            f"especially if the reply itself is short or vague."
        )

    if existing_topics:
        topics_list = "\n".join(f"- {t}" for t in existing_topics)
        parts.append(
            f"Topics that already exist in the graph:\n{topics_list}\n\n"
            f"If this message is about the SAME underlying subject as one of "
            f"the topics above, return that exact string verbatim (same "
            f"spelling, same words) instead of inventing a new one. Only "
            f"create a new topic if this message is genuinely about "
            f"something different."
        )

    parts.append(f"Extract topics from: {message_text}")
    user_content = "\n\n".join(parts)

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=100,  # Topics are short — keep this cheap
            system=EXTRACTION_SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": user_content}
            ],
        )

        raw = response.content[0].text.strip()
        return _parse_topics_response(raw)

    except Exception as e:
        print(f"⚠️ Topic extraction failed: {e}")
        return []


if __name__ == "__main__":
    # Test with your messy, realistic messages
    test_messages = [
        "Dashboard keeps timing out when loading user stats, super frustrating, been happening since this morning",
        "Pushed a config change to throttle the analytics endpoint — was hammering the DB too hard",
        "Whatever you did worked, dashboard is back 🙌",
        "Hey everyone, lunch is at 12:30 today!",
        "The new auth flow is broken, users can't log in after password reset",
        "Fixed the token refresh logic, deploying hotfix now",
        "Can someone review my PR before EOD? Been waiting 2 days",
    ]

    print("🧪 Testing topic extraction (no existing topics yet)...\n")
    known_topics: list[str] = []
    for msg in test_messages:
        topics = extract_topics(msg, existing_topics=known_topics)
        print(f"Message: {msg[:60]}...")
        print(f"Topics:  {topics}\n")
        known_topics.extend(t for t in topics if t not in known_topics)

    # Demonstrates reuse: this message describes the same subject as the
    # auth messages above using totally different words ("JWT"). With the
    # existing-topics list passed in, it should reuse the same topic name
    # instead of creating a near-duplicate.
    print("🧪 Testing reuse on a differently-worded message about the same subject...\n")
    followup = "JWT tokens keep expiring way too fast, people are getting logged out mid-session"
    topics = extract_topics(followup, existing_topics=known_topics)
    print(f"Known topics so far: {known_topics}")
    print(f"Message: {followup}")
    print(f"Topics:  {topics}")