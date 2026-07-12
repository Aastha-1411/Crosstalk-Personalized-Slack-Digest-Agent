"""
Interactive Block Kit Formatter for Slack Digest Agent.

Uses Slack's Block Kit interactivity to create a tabbed digest experience:
- Initial card shows a TL;DR highlight + 4 navigation buttons
- Clicking each button updates the message to show that section
- Feels like a native app, not a wall of text

Requires: Interactivity enabled in your Slack app settings
"""

import os
import json
import re
from slack_sdk import WebClient
from dotenv import load_dotenv

from digest_state_store import set_digest_state

load_dotenv()

client = WebClient(token=os.environ.get("SLACK_BOT_TOKEN"))

# If set (e.g. your ngrok URL during a demo), a "View Graph" link button
# gets added to the digest card, pointing at {GRAPH_SERVER_BASE_URL}/graph/<user_id>.
# Left unset, the button is simply omitted — this feature is fully optional.
GRAPH_SERVER_BASE_URL = os.environ.get("GRAPH_SERVER_BASE_URL")


def _to_slack_mrkdwn(text: str) -> str:
    """Converts standard markdown **bold** to Slack's *bold* mrkdwn syntax.
    Claude's digest output uses standard markdown; Slack ignores double
    asterisks and displays them literally, so this must run before any
    digest text reaches a Block Kit mrkdwn field."""
    return re.sub(r'\*\*(.+?)\*\*', r'*\1*', text)


def parse_digest_sections(digest_text: str) -> dict:
    """Parses Claude's digest output into structured sections."""
    digest_text = _to_slack_mrkdwn(digest_text)
    sections = {
        "cross_channel": [],
        "mentions": [],
        "actions": [],
        "channels": [],
    }

    current_section = None
    for line in digest_text.split("\n"):
        line = line.strip()
        if not line:
            continue

        if "CROSS-CHANNEL" in line.upper():
            current_section = "cross_channel"
        elif "MENTIONED" in line.upper() or "YOU WERE" in line.upper():
            current_section = "mentions"
        elif "ACTION" in line.upper():
            current_section = "actions"
        elif "CHANNELS TODAY" in line.upper():
            current_section = "channels"
        elif line.startswith("•") and current_section:
            content = line.lstrip("•").strip()
            if content and "nothing today" not in content.lower():
                # Keep the full bullet rather than truncating to its first
                # sentence — truncation was cutting off the payoff when a
                # bullet opened with a question or setup clause, producing
                # orphaned fragments (e.g. ending on a bare "?").
                sections[current_section].append(content)

    return sections


def _button(text: str, value: str, action_id: str, style: str | None = None, url: str | None = None) -> dict:
    """Builds a Block Kit button dict. Slack rejects a 'style' key whose
    value is None — it must be omitted entirely unless it's 'primary' or
    'danger' — so we only add the key when there's an actual value. A
    button can carry a 'url' too — Slack still requires action_id even
    for pure link buttons, but no server-side handler is needed for it;
    Slack just opens the URL in the browser when clicked."""
    button = {
        "type": "button",
        "text": {"type": "plain_text", "text": text, "emoji": True},
        "value": value,
        "action_id": action_id,
    }
    if style:
        button["style"] = style
    if url:
        button["url"] = url
    return button


def _graph_link_button(slack_user_id: str | None) -> dict | None:
    """
    Returns a "🧠 View Graph" link button pointing at the live graph
    server, or None if GRAPH_SERVER_BASE_URL isn't configured or no
    user_id was passed through. Kept as its own function so both card
    builders can add it identically without duplicating the condition.
    """
    if not GRAPH_SERVER_BASE_URL or not slack_user_id:
        return None
    return _button(
        "🧠 View Graph",
        value=slack_user_id,
        action_id="view_graph_link",
        url=f"{GRAPH_SERVER_BASE_URL}/graph/{slack_user_id}",
    )


def extract_highlight(sections: dict) -> str:
    """Pulls the single most important insight for the TL;DR block."""
    if sections["cross_channel"]:
        return sections["cross_channel"][0]
    if sections["actions"]:
        return sections["actions"][0]
    if sections["mentions"]:
        return sections["mentions"][0]
    return "Check your digest sections below for today's updates."


def build_home_card(user_name: str, sections: dict, slack_user_id: str | None = None) -> list:
    """Builds the initial home view with TL;DR + 4 tab buttons."""
    highlight = extract_highlight(sections)

    cc_count = len(sections["cross_channel"])
    mention_count = len(sections["mentions"])
    action_count = len(sections["actions"])
    channel_count = len(sections["channels"])

    tab_buttons = [
        _button(f"🔗 Cross-Channel  {cc_count}", "cross_channel", "digest_tab_cross_channel",
                style="primary" if cc_count > 0 else None),
        _button(f"💬 Mentions  {mention_count}", "mentions", "digest_tab_mentions"),
        _button(f"⚡ Actions  {action_count}", "actions", "digest_tab_actions",
                style="danger" if action_count > 0 else None),
        _button(f"📌 Channels  {channel_count}", "channels", "digest_tab_channels"),
    ]
    graph_button = _graph_link_button(slack_user_id)
    if graph_button:
        tab_buttons.append(graph_button)

    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "📬  Your Daily Digest", "emoji": True}
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"Personalized for *{user_name}* · Powered by your team's knowledge graph"}]
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*⚡ Today's Highlight*\n{highlight}"}
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*Explore your digest:*"}
        },
        {
            "type": "actions",
            "elements": tab_buttons
        },
        {"type": "divider"},
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "🧠 *Slack Digest Agent* · Neo4j knowledge graph + Claude AI · Type `/digest` anytime"}]
        }
    ]


def build_section_card(user_name: str, section_name: str, items: list, sections: dict, slack_user_id: str | None = None) -> list:
    """Builds a section detail view when a tab button is clicked."""
    SECTION_CONFIG = {
        "cross_channel": {"emoji": "🔗", "title": "Cross-Channel Updates", "subtitle": "Things connected across channels that affect your work", "empty": "No cross-channel connections today."},
        "mentions": {"emoji": "💬", "title": "You Were Mentioned", "subtitle": "People referencing you or waiting on a response", "empty": "No mentions today — you're in the clear ✅"},
        "actions": {"emoji": "⚡", "title": "Action Items", "subtitle": "Things that need your attention today", "empty": "No action items — clear day ahead ✅"},
        "channels": {"emoji": "📌", "title": "Your Channels Today", "subtitle": "Quick snapshot of what happened where", "empty": "No channel activity to report."},
    }

    config = SECTION_CONFIG[section_name]
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"{config['emoji']}  {config['title']}", "emoji": True}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"_{config['subtitle']}_ · Digest for *{user_name}*"}]},
        {"type": "divider"},
    ]

    if items:
        for item in items:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"› {item}"}})
    else:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"_{config['empty']}_"}})

    blocks.append({"type": "divider"})

    cc_count = len(sections["cross_channel"])
    mention_count = len(sections["mentions"])
    action_count = len(sections["actions"])
    channel_count = len(sections["channels"])

    nav_buttons = [
        _button("← Back", "home", "digest_tab_home"),
        _button(f"🔗  {cc_count}", "cross_channel", "digest_tab_cross_channel",
                style="primary" if section_name == "cross_channel" else None),
        _button(f"💬  {mention_count}", "mentions", "digest_tab_mentions",
                style="primary" if section_name == "mentions" else None),
        _button(f"⚡  {action_count}", "actions", "digest_tab_actions",
                style="primary" if section_name == "actions" else None),
        _button(f"📌  {channel_count}", "channels", "digest_tab_channels",
                style="primary" if section_name == "channels" else None),
    ]
    graph_button = _graph_link_button(slack_user_id)
    if graph_button:
        nav_buttons.append(graph_button)

    blocks.append({"type": "actions", "elements": nav_buttons})

    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": "🧠 *Slack Digest Agent* · Neo4j knowledge graph + Claude AI"}]})
    return blocks


def send_digest_dm(slack_user_id: str, user_name: str, digest_text: str, graph_user_id: str | None = None) -> dict:
    """Sends the interactive tabbed digest as a DM.

    graph_user_id controls whose graph the "View Connections" button
    links to — defaults to slack_user_id (the normal case: a person's
    own digest links to their own graph). Override it when the DM
    recipient and the digest's subject differ, e.g. preview scripts
    that deliver every persona's digest to one real account but should
    still link each button to that persona's own graph.
    """
    if graph_user_id is None:
        graph_user_id = slack_user_id
    try:
        sections = parse_digest_sections(digest_text)
        dm_response = client.conversations_open(users=slack_user_id)
        dm_channel = dm_response["channel"]["id"]
        blocks = build_home_card(user_name, sections, graph_user_id)
        response = client.chat_postMessage(
            channel=dm_channel,
            text=f"📬 Your Daily Digest is ready, {user_name}!",
            blocks=blocks,
        )

        # Save state so button clicks on this message know what to show.
        set_digest_state(
            user_id=slack_user_id,
            channel=dm_channel,
            ts=response["ts"],
            user_name=user_name,
            sections=sections,
        )

        print(f"  ✅ Interactive digest sent to {user_name}")
        return {"channel": dm_channel, "ts": response["ts"], "sections": sections, "user_name": user_name}
    except Exception as e:
        print(f"  ❌ Failed to send digest to {user_name}: {e}")
        return {}


def handle_tab_click(action_id: str, channel: str, message_ts: str, user_name: str, sections: dict, slack_user_id: str | None = None):
    """Called when a user clicks a tab button — updates the message in place."""
    section_map = {
        "digest_tab_cross_channel": "cross_channel",
        "digest_tab_mentions": "mentions",
        "digest_tab_actions": "actions",
        "digest_tab_channels": "channels",
        "digest_tab_home": "home",
    }

    section_name = section_map.get(action_id, "home")

    if section_name == "home":
        blocks = build_home_card(user_name, sections, slack_user_id)
    else:
        blocks = build_section_card(user_name, section_name, sections[section_name], sections, slack_user_id)

    client.chat_update(
        channel=channel,
        ts=message_ts,
        blocks=blocks,
        text=f"📬 Digest — {section_name.replace('_', ' ').title()}",
    )


if __name__ == "__main__":
    SAMPLE_DIGEST = """
🔗 CROSS-CHANNEL UPDATES (things connected across channels that affect you)
• The dashboard fix you thanked "whoever" for? That was Tom (Backend) — he found the analytics endpoint was missing an index on user_id.
• Priya's signup flow changes are bigger than you might expect — she updated them in #design before you two connected.

💬 YOU WERE MENTIONED (people waiting on you or referencing you)
• John (Backend) tagged you in #backend asking you to verify that user stats are loading correctly.

⚡ ACTION ITEMS (things that need your attention)
• Reply to John in #backend — confirm whether user stats are displaying correctly.
• Complete Priya's signup flow review — she's on a this-week ship timeline.

📌 YOUR CHANNELS TODAY (brief snapshot)
• #frontend: Dashboard went from broken to fixed across three of your messages this morning.
• #backend: Full incident thread on the DB performance issue and John's open request.
• #design: Priya posted her signup flow changes — context you'll want before your review.
"""

    TEST_USER_ID = "U0BBS00TYV9"
    TEST_USER_NAME = "Maria"

    if TEST_USER_ID != "YOUR_REAL_SLACK_USER_ID":
        result = send_digest_dm(TEST_USER_ID, TEST_USER_NAME, SAMPLE_DIGEST)
        print(f"\n📋 Message sent: channel={result.get('channel')}, ts={result.get('ts')}")
    else:
        print("⚠️  Replace TEST_USER_ID with your real Slack user ID")
        sections = parse_digest_sections(SAMPLE_DIGEST)
        blocks = build_home_card(TEST_USER_NAME, sections)
        print(json.dumps(blocks, indent=2))