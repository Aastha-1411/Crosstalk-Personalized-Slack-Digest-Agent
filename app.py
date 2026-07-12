"""
Slack Digest Agent - Message Ingestion + Interactive Digest
Listens to channel messages, writes to knowledge graph,
and handles interactive digest button clicks.
"""

import os
import threading
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from flask import Flask

from graph_db import GraphDB
from graph_writer import MessageGraphWriter, extract_mentions
from topic_extractor import extract_topics
from blockkit_formatter import handle_tab_click, parse_digest_sections, send_digest_dm
from digest_state_store import get_digest_state
from digest_generator import generate_digest

load_dotenv()

# Sample digest text used only for manually testing the interactive card.
# Remove once the real /digest command (and real digest_generator output) is wired in.
_TEST_DIGEST_TEXT = """
🔗 CROSS-CHANNEL UPDATES (things connected across channels that affect you)
• The dashboard fix you thanked "whoever" for? That was Tom (Backend) — he found the analytics endpoint was missing an index on user_id.

💬 YOU WERE MENTIONED (people waiting on you or referencing you)
• John (Backend) tagged you in #backend asking you to verify that user stats are loading correctly.

⚡ ACTION ITEMS (things that need your attention)
• Reply to John in #backend — confirm whether user stats are displaying correctly.

📌 YOUR CHANNELS TODAY (brief snapshot)
• #frontend: Dashboard went from broken to fixed across three of your messages this morning.
"""

app = App(
    token=os.environ.get("SLACK_BOT_TOKEN"),
    signing_secret=os.environ.get("SLACK_SIGNING_SECRET"),
)

graph_db = GraphDB()
writer = MessageGraphWriter(graph_db)


# ── Message listener ──────────────────────────────────────
@app.event("message")
def handle_message_events(event, say, logger):
    text = event.get("text", "")
    user = event.get("user", "unknown")
    channel = event.get("channel", "unknown")
    timestamp = event.get("ts", "unknown")
    thread_ts = event.get("thread_ts")

    if not text or user == "unknown":
        return

    logger.info(f"Message received from {user} in {channel}: {text}")

    try:
        writer.write_message(
            message_id=timestamp,
            sender_id=user,
            channel=channel,
            text=text,
            timestamp=timestamp,
            thread_id=thread_ts,
            mentioned_user_ids=extract_mentions(text),
        )

        # If this is a reply in a thread, pull the thread-starting message
        # as context — a reply like "yeah that fixed it" has no topic
        # signal on its own.
        thread_context = writer.get_message_text(thread_ts) if thread_ts else None

        topics = extract_topics(
            text,
            existing_topics=writer.get_existing_topics(),
            thread_context=thread_context,
        )
        for topic in topics:
            writer.link_message_to_topic(timestamp, topic)
            logger.info(f"  🏷️ Topic linked: {topic}")

    except Exception as e:
        logger.error(f"⚠️ Failed to write message to graph (continuing anyway): {e}")

    if "hello bot" in text.lower():
        say(f"👋 Hey <@{user}>! I'm alive, listening, and logging this to the graph.")

    if text.strip().lower() == "test digest":
        # Sends within THIS process, so digest_state_store shares state
        # with the button-click handlers below. Type this in any channel
        # the bot is in, then check your DMs and click a tab button.
        say(f"📬 Sending you a test digest, <@{user}>...")
        send_digest_dm(user, "Test User", _TEST_DIGEST_TEXT)


# ── Button click handlers ─────────────────────────────────
def _handle_digest_tab(action_id: str, body: dict, ack, logger):
    """Generic handler for all digest tab button clicks."""
    ack()

    user_id = body["user"]["id"]
    channel = body["channel"]["id"]
    message_ts = body["message"]["ts"]

    state = get_digest_state(user_id)
    if not state:
        logger.warning(f"No digest state found for user {user_id}")
        return

    try:
        handle_tab_click(
            action_id=action_id,
            channel=channel,
            message_ts=message_ts,
            user_name=state["user_name"],
            sections=state["sections"],
            slack_user_id=user_id,
        )
    except Exception as e:
        logger.error(f"⚠️ Failed to handle tab click: {e}")


@app.action("digest_tab_cross_channel")
def handle_tab_cross_channel(body, ack, logger):
    _handle_digest_tab("digest_tab_cross_channel", body, ack, logger)


@app.action("digest_tab_mentions")
def handle_tab_mentions(body, ack, logger):
    _handle_digest_tab("digest_tab_mentions", body, ack, logger)


@app.action("digest_tab_actions")
def handle_tab_actions(body, ack, logger):
    _handle_digest_tab("digest_tab_actions", body, ack, logger)


@app.action("digest_tab_channels")
def handle_tab_channels(body, ack, logger):
    _handle_digest_tab("digest_tab_channels", body, ack, logger)


@app.action("digest_tab_home")
def handle_tab_home(body, ack, logger):
    _handle_digest_tab("digest_tab_home", body, ack, logger)


@app.action("view_graph_link")
def handle_view_graph_link(ack):
    # This button just opens a URL — Slack handles that client-side.
    # We still need to register a handler and ack(), or Bolt logs an
    # "unhandled request" warning every time someone clicks it.
    ack()


# ── /digest slash command ─────────────────────────────────
@app.command("/digest")
def handle_digest_command(ack, body, logger):
    ack()  # must ack within 3 seconds, before any slow work

    user_id = body["user_id"]
    user_name = body.get("user_name", "there")

    try:
        # Real pipeline: query the graph via MCP tools, generate the
        # actual digest with Claude, then send it. Replaces the
        # hardcoded _TEST_DIGEST_TEXT now that the card mechanics work.
        digest_text = generate_digest(user_id, user_name)
        send_digest_dm(user_id, user_name, digest_text)
    except Exception as e:
        logger.error(f"⚠️ /digest command failed: {e}")


if __name__ == "__main__":
    # Tiny health-check server so an external pinger (UptimeRobot,
    # cron-job.org, etc.) can hit this every ~5-10 min and keep Render's
    # free tier from spinning this process down due to inactivity.
    # Socket Mode itself produces no inbound traffic, so without this,
    # Render would sleep the bot even while it's actively listening.
    health_app = Flask(__name__)

    @health_app.route("/")
    def health():
        return "Crosstalk bot is alive", 200

    def run_health_server():
        port = int(os.environ.get("PORT", 10000))
        print(f"🩺 Health server attempting to bind on 0.0.0.0:{port}...")
        try:
            # use_reloader=False and threaded=True explicitly, to avoid
            # any signal-handling quirks from running Flask's dev server
            # outside the main thread, and to handle pings without
            # blocking on a slow request.
            health_app.run(host="0.0.0.0", port=port, use_reloader=False, threaded=True)
        except Exception:
            # A crash here was previously SILENT — a daemon thread dying
            # doesn't stop the process, it just quietly closes the port,
            # which then fails Render's port scan with no visible reason.
            # Print the full traceback so it shows up in Render logs.
            import traceback
            print("🩺 ❌ Health server crashed:")
            traceback.print_exc()

    threading.Thread(target=run_health_server, daemon=True).start()

    print("⚡️ Slack Digest Agent is starting...")
    handler = SocketModeHandler(app, os.environ.get("SLACK_APP_TOKEN"))
    handler.start()