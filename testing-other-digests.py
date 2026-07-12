"""
Digest Generator for Slack Digest Agent.

Two-stage Claude pipeline:
  Stage 1 (topic_extractor.py) — extracts topic from a single message
  Stage 2 (this file)          — generates personalized digest from MCP tool output

This file handles Stage 2: taking structured graph context
and turning it into a genuinely insightful, personalized digest.
"""

import json
import os
from anthropic import Anthropic
from dotenv import load_dotenv
from mcp_server import (
    get_relevant_context,
    get_cross_channel_connections,
    get_unresolved_mentions,
)
from blockkit_formatter import send_digest_dm

load_dotenv()

client = Anthropic()

DIGEST_SYSTEM_PROMPT = """
You are the Slack Digest Agent — an intelligent assistant that generates 
personalized daily digests for team members based on their Slack activity.

You have access to structured data from a knowledge graph that tracks:
- Messages each person sent and what topics they discussed
- Cross-channel connections (same topic appearing in different channels)
- Mentions the person received but may not have seen or replied to

IMPORTANT: the cross-channel connections and mentions you receive have
ALREADY been scoped by the graph to this specific person's own topics and
mentions — this isn't a generic "top connections in the workspace" list,
it's everything structurally tied to what THEY personally discussed. Don't
assume anything was filtered out for being "less important" — if it's in
the data, it's relevant to this person and worth considering including.

Your job is to write a concise, genuinely useful digest for ONE specific person.

CRITICAL RULES:
1. Be specific — reference actual messages, actual people, actual channels
2. Surface connections the person would NOT easily notice themselves
3. Highlight the cross-channel connections prominently — this is your most valuable insight
4. Flag unresolved mentions clearly — these are action items
5. Keep it SHORT — 5-8 bullet points max, each one genuinely useful. If
   there are more relevant items than fit, use your judgment on what THIS
   person would care about most — but every item you receive is already
   confirmed relevant to them, not noise to filter further
6. Write in a warm, professional tone — like a smart colleague briefing them
7. NEVER write generic summaries like "there was discussion about X" 
   — always say WHO said WHAT and WHY it matters to THIS person
8. If there are cross-channel connections, lead with them — they are the most impressive insight

FORMAT your response EXACTLY like this:
🔗 CROSS-CHANNEL UPDATES (things connected across channels that affect you)
• [specific insight about cross-channel connection]

💬 YOU WERE MENTIONED (people waiting on you or referencing you)
• [specific mention with context]

⚡ ACTION ITEMS (things that need your attention)
• [specific action item]

📌 YOUR CHANNELS TODAY (brief snapshot)
• [channel]: [one-line summary of what happened]
"""


def generate_digest(user_id: str, user_name: str) -> str:
    """
    Generates a personalized digest for a specific user by:
    1. Calling MCP tools to get their graph context — each tool is scoped
       to this user_id, so results are already personalized by the graph
       itself (not a generic list that then gets filtered/ranked down)
    2. Passing that context to Claude to write the actual digest
    """

    # Step 1: Pull context from MCP tools — all three calls are scoped to
    # this specific user, so nothing here is generic/workspace-wide.
    print(f"  📊 Fetching graph context for {user_name}...")

    relevant_context = json.loads(get_relevant_context(user_id))
    cross_channel = json.loads(get_cross_channel_connections(user_id))
    unresolved = json.loads(get_unresolved_mentions(user_id))

    # Step 2: Build the user prompt with all context
    user_prompt = f"""
Generate a personalized daily digest for: {user_name} (Slack ID: {user_id})

Here is their knowledge graph context for today — already scoped to this
person's own topics and mentions by the graph itself:

=== MESSAGES THEY SENT ===
{json.dumps(relevant_context.get("messages_sent", []), indent=2)}

=== TOPICS THEY'RE INVOLVED IN ===
{json.dumps(relevant_context.get("topics_involved_in", []), indent=2)}

=== MENTIONS THEY RECEIVED ===
{json.dumps(relevant_context.get("mentions_received", []), indent=2)}

=== CROSS-CHANNEL CONNECTIONS ON THEIR OWN TOPICS (grouped by channel) ===
{json.dumps(cross_channel.get("cross_channel_connections", []), indent=2)}

=== UNRESOLVED MENTIONS (tagged but no reply yet) ===
{json.dumps(unresolved.get("unresolved_mentions", []), indent=2)}

Now write {user_name}'s personalized digest. Focus on what's most relevant 
and actionable for THEM specifically. Lead with any cross-channel connections 
that directly involve their work or blockers.
"""

    # Step 3: Call Claude to generate the digest
    print(f"  🤖 Generating digest with Claude...")

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        system=DIGEST_SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": user_prompt}
        ],
    )

    return response.content[0].text


def generate_all_digests(users: dict, send_dm: bool = False) -> dict:
    """
    Generates digests for all users.
    users = {slack_user_id: display_name}
    send_dm = True to actually deliver via Slack DM (use for real runs)
    Returns {slack_user_id: digest_text}
    """
    digests = {}
    for user_id, user_name in users.items():
        print(f"\n🔄 Generating digest for {user_name}...")
        try:
            digest = generate_digest(user_id, user_name)
            digests[user_id] = digest
            print(f"  ✅ Done for {user_name}")

            # Send via Slack DM if requested
            if send_dm:
                send_digest_dm(user_id, user_name, digest)

        except Exception as e:
            print(f"  ❌ Failed for {user_name}: {e}")
            digests[user_id] = None
    return digests


if __name__ == "__main__":
    # Test with your actual seeded personas — must match the IDs used in
    # seed_demo_data.py exactly, or these will come back empty.
    TEST_USERS = {
        "U0BBS00TYV9": "Maria",  # confirm this matches your real test user ID
        "FAKE_TOM": "Tom Rivera",
        "FAKE_PRIYA": "Priya Shah",
        "FAKE_JOHN": "John Kim",
        "FAKE_ALEX": "Alex Chen",
        "FAKE_SAM": "Sam Okafor",
    }

    print("🚀 Starting digest generation test...\n")
    digests = generate_all_digests(TEST_USERS)

    print("\n" + "="*60)
    print("DIGEST OUTPUTS")
    print("="*60)

    for user_id, digest in digests.items():
        name = TEST_USERS[user_id]
        print(f"\n📬 DIGEST FOR {name.upper()}")
        print("-"*40)
        print(digest or "❌ Failed to generate")
        print()