"""
seed_demo_data.py

Posts ~35 realistic messages across #frontend, #backend, #design, #general
as fake personas (via chat.postMessage username override), AND writes them
directly into Neo4j so the graph correctly attributes each message to a
distinct fake Person node (since the username override is cosmetic only —
Slack still tags the underlying event with your real bot user).

Requires:
  - SLACK_BOT_TOKEN with chat:write and chat:write.customize scopes
  - Bot invited to #frontend, #backend, #design, #general
  - .env loaded with your usual Neo4j creds

Run once to seed the demo:
    python seed_demo_data.py

Re-running will add duplicate messages (each gets a fresh timestamp), so
if you want a clean re-seed, wipe the graph first via your usual reset
script before running this again.
"""

import os
import time
from dotenv import load_dotenv
from slack_sdk import WebClient

from graph_db import GraphDB
from graph_writer import MessageGraphWriter, extract_mentions
from topic_extractor import extract_topics

load_dotenv()

client = WebClient(token=os.environ.get("SLACK_BOT_TOKEN"))
db = GraphDB()
writer = MessageGraphWriter(db)

# ── Fake personas ──────────────────────────────────────────
# Maria is your real target user (swap U0BBS00TYV9 for your actual test
# user's real Slack ID if different) — everyone else is a fake persona
# that only exists in the graph + as a display name in Slack.
MARIA = {"id": "U0BBS00TYV9", "name": "Maria"}
# Judges — assign their REAL Slack member IDs directly to Tom and Sam,
# since those two already have the richest, most central storylines
# (DB pool fix and the JWT/logout thread). This means whichever judge
# runs /digest gets a full, naturally-earned digest with no extra
# scripted messages needed. Get IDs via Profile -> ... -> Copy member ID.
TOM = {"id": "U0BGKHZ9T6Z", "name": "Tom Rivera", "icon": ":male-technologist:"}
PRIYA = {"id": "FAKE_PRIYA", "name": "Priya Shah", "icon": ":female-artist:"}
JOHN = {"id": "FAKE_JOHN", "name": "John Kim", "icon": ":male-technologist:"}
ALEX = {"id": "FAKE_ALEX", "name": "Alex Chen", "icon": ":male-office-worker:"}
SAM = {"id": "U0BG7G1V8S3", "name": "Sam Okafor", "icon": ":female-office-worker:"}

# ── Channels ────────────────────────────────────────────────
# Replace with your real channel IDs or #names (bot must be a member).
FRONTEND = "#frontend"
BACKEND = "#backend"
DESIGN = "#design"
GENERAL = "#general"

# ── Message list ────────────────────────────────────────────
# Each entry: (channel, persona, text)
# Ordered roughly as a single demo "day" — post them with small delays
# so timestamps aren't identical and Slack doesn't rate-limit.
MESSAGES = [
    # ── Thread A: Dashboard slowness -> DB pool root cause ──
    (FRONTEND, MARIA, "anyone else seeing the dashboard take forever to load this morning? like 8-10 seconds"),
    (FRONTEND, ALEX, "yeah same here, thought it was just my wifi lol"),
    (FRONTEND, MARIA, "nope just checked on ethernet, still slow. filing this as a bug"),
    (BACKEND, TOM, "seeing elevated response times on the analytics endpoint, looking into it"),
    (BACKEND, TOM, "found it — connection pool is maxing out, we're not releasing connections after the analytics query"),
    (BACKEND, TOM, "pushing a fix now, adding an index on user_id too since the query was doing a full scan"),
    (BACKEND, JOHN, "nice catch, that pool exhaustion would explain the timeouts we saw last week too"),
    (FRONTEND, MARIA, "dashboard is fast again! thanks whoever fixed that 🙏"),
    (FRONTEND, ALEX, "yeah that fixed it for me too, loads instantly now"),

    # ── Thread B: Signup flow redesign -> affects frontend, mentions Maria ──
    (DESIGN, PRIYA, "posted updated mockups for the signup flow — simplified from 4 steps down to 2"),
    (DESIGN, PRIYA, "biggest change: we're moving email verification to happen async after signup instead of blocking it"),
    (DESIGN, SAM, "love this, should reduce drop-off a lot. any concerns on the frontend side?"),
    (DESIGN, PRIYA, "@Maria could you take a look when you get a chance? want frontend eyes before we finalize"),
    (BACKEND, JOHN, "heads up frontend team, the signup API contract is changing to support the async verification flow — new field `verification_pending: bool`"),
    (FRONTEND, MARIA, "just saw the signup mockups, this is a bigger change than I expected — will need to rework the confirmation screen state"),
    (FRONTEND, ALEX, "want help splitting up the signup flow work? I can take the confirmation screen if you take the form validation"),
    (FRONTEND, MARIA, "yes please, that'd help a lot given the timeline"),

    # ── Thread C: Login/auth JWT bug (general -> backend) ──
    (GENERAL, SAM, "getting logged out randomly today, anyone else?"),
    (GENERAL, ALEX, "yeah happened to me twice this morning"),
    (GENERAL, SAM, "reported it, seems like sessions are dying way earlier than they should"),
    (BACKEND, JOHN, "looking into the random logout reports — checking token expiry config"),
    (BACKEND, JOHN, "found it, JWT expiry got set to 15 min instead of 24h in yesterday's config deploy, classic typo"),
    (BACKEND, TOM, "ouch, that'll do it. want me to review the deploy script so this doesn't slip through again?"),
    (BACKEND, JOHN, "yeah that'd be great, fix is live now though"),
    (GENERAL, SAM, "confirmed, not getting logged out anymore, thanks team"),

    # ── Thread D: False-positive "database" mention (should NOT merge with Thread A) ──
    (GENERAL, ALEX, "random question — anyone have a good recommendation for a lightweight database for storing local analytics events? not the main app db, just a side project"),
    (GENERAL, TOM, "sqlite is probably overkill-proof for that, or duckdb if you want something analytics-flavored"),
    (GENERAL, ALEX, "duckdb, nice, hadn't heard of that one, thanks!"),

    # ── Action items directly involving Maria ──
    (BACKEND, JOHN, "@Maria can you verify user stats are displaying correctly on your end now that the pool fix is live?"),
    (DESIGN, PRIYA, "@Maria following up — did you get a chance to look at the signup mockups yet?"),

    # ── Casual banter / realism filler ──
    (GENERAL, SAM, "friday feels like it's never coming this week 😅"),
    (FRONTEND, ALEX, "anyone want to pair on the confirmation screen work this afternoon?"),
    (DESIGN, PRIYA, "updated the design file with the latest color tokens btw, check figma"),
    (BACKEND, TOM, "deploying the connection pool monitoring dashboard now so we catch this earlier next time"),
    (GENERAL, MARIA, "excited for the demo this week, this cross-channel stuff is going to look great"),
    (FRONTEND, MARIA, "starting on the confirmation screen rework now"),
    (BACKEND, JOHN, "user stats confirmed working on my end, all endpoints looking healthy"),
]


def post_as_persona(channel: str, persona: dict, text: str):
    """Posts to Slack with a cosmetic username/icon override so it reads
    as a distinct person in the channel. Note: the bot token can NEVER
    actually post as another real user's account — every message here
    is posted by the bot itself, so ALL personas (including Maria) need
    the override, or they'll all show up as the bot's own name."""
    return client.chat_postMessage(
        channel=channel,
        text=text,
        username=persona["name"],
        icon_emoji=persona.get("icon", ":bust_in_silhouette:"),
    )


def seed_message(channel: str, persona: dict, text: str):
    # Post visibly in Slack for the demo video.
    try:
        post_as_persona(channel, persona, text)
    except Exception as e:
        print(f"  ⚠️  Slack post failed ({persona['name']} in {channel}): {e}")

    # Write directly to the graph so attribution is correct regardless
    # of the cosmetic Slack username override.
    ts = str(time.time())
    channel_clean = channel.lstrip("#")
    try:
        writer.write_message(
            message_id=ts,
            sender_id=persona["id"],
            channel=channel_clean,
            text=text,
            timestamp=ts,
            mentioned_user_ids=extract_mentions(text),
        )

        # write_message only sets slack_user_id on the Person node, not a
        # display name — set it here directly so the graph viewer shows
        # real names instead of "None" / blank labels.
        with db.driver.session() as session:
            session.run(
                "MATCH (p:Person {slack_user_id: $uid}) SET p.name = $name",
                uid=persona["id"],
                name=persona["name"],
            )

        topics = extract_topics(text, existing_topics=writer.get_existing_topics())
        for topic in topics:
            writer.link_message_to_topic(ts, topic)
        print(f"  ✅ [{channel_clean}] {persona['name']}: {text[:50]}...")
    except Exception as e:
        print(f"  ⚠️  Graph write failed ({persona['name']} in {channel}): {e}")


if __name__ == "__main__":
    print(f"🌱 Seeding {len(MESSAGES)} demo messages across Slack + Neo4j...\n")
    for channel, persona, text in MESSAGES:
        seed_message(channel, persona, text)
        time.sleep(1.2)  # stay well under Slack's rate limits, and keeps
                          # timestamps distinct in the graph

    print("\n✅ Done seeding. Run `/digest` as Maria to see the cross-channel digest.")
    db.close()