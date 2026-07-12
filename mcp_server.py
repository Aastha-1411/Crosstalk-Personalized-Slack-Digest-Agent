"""
MCP Server for Slack Digest Agent.

Exposes the Neo4j knowledge graph as 3 queryable tools that Claude
calls during digest generation:

1. get_relevant_context    — everything relevant to a person today
2. get_cross_channel_connections — same topic appearing in multiple channels
3. get_unresolved_mentions — threads where person was mentioned but hasn't replied
"""

import json
import os
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from graph_db import GraphDB

load_dotenv()

# Initialize MCP server and graph connection
mcp = FastMCP("slack-digest-graph")
db = GraphDB()


# ─────────────────────────────────────────────
# Tool 1: Get everything relevant to a person today
# ─────────────────────────────────────────────
@mcp.tool()
def get_relevant_context(user_id: str) -> str:
    """
    Returns all graph context relevant to a specific Slack user.
    Includes: topics they discussed, messages they sent,
    channels they're active in, and people they interacted with.

    Args:
        user_id: The Slack user ID (e.g. 'U_MARIA' or real Slack ID like 'U0BC1234')
    """
    with db.driver.session() as session:

        # Messages this person sent and what topics they're about
        sent = session.run(
            """
            MATCH (p:Person {slack_user_id: $user_id})-[:SENT]->(m:Message)
            OPTIONAL MATCH (m)-[:ABOUT]->(t:Topic)
            RETURN m.text AS text, m.channel AS channel,
                   m.timestamp AS timestamp, collect(t.name) AS topics
            ORDER BY m.timestamp DESC
            LIMIT 20
            """,
            user_id=user_id,
        ).data()

        # Topics this person is connected to (via messages they sent)
        topics = session.run(
            """
            MATCH (p:Person {slack_user_id: $user_id})-[:SENT]->(m:Message)-[:ABOUT]->(t:Topic)
            RETURN t.name AS topic, count(m) AS message_count
            ORDER BY message_count DESC
            """,
            user_id=user_id,
        ).data()

        # Messages that mention this person
        mentions = session.run(
            """
            MATCH (m:Message)-[:MENTIONS]->(p:Person {slack_user_id: $user_id})
            MATCH (sender:Person)-[:SENT]->(m)
            RETURN m.text AS text, m.channel AS channel,
                   m.timestamp AS timestamp, sender.name AS from_person
            ORDER BY m.timestamp DESC
            LIMIT 10
            """,
            user_id=user_id,
        ).data()

    result = {
        "user_id": user_id,
        "messages_sent": sent,
        "topics_involved_in": topics,
        "mentions_received": mentions,
    }
    return json.dumps(result, indent=2)


# ─────────────────────────────────────────────
# Tool 2: Find cross-channel connections on topics
# ─────────────────────────────────────────────
@mcp.tool()
def get_cross_channel_connections(user_id: str) -> str:
    """
    Finds topics THIS person has sent a message about that ALSO appear in
    other channels, and groups the evidence by channel — one row per
    topic, with the messages from each side collected together.

    This is the core "wow" feature — surfacing connections humans missed
    because they were spread across channels. Scoping by user_id makes it
    personalized (not a generic workspace-wide top-10). Grouping by
    channel instead of returning every individual message-pair avoids a
    combinatorial blowup: if 2 people discuss something in #frontend and
    3 people discuss it in #backend, that's ONE real connection, not 6
    near-duplicate pairs — returning 6 rows for one incident just wastes
    tokens without adding information.

    Args:
        user_id: The Slack user ID to find cross-channel connections for
    """
    with db.driver.session() as session:
        results = session.run(
            """
            MATCH (p:Person {slack_user_id: $user_id})-[:SENT]->(um:Message)-[:ABOUT]->(t:Topic)
            WITH DISTINCT t
            MATCH (m:Message)-[:ABOUT]->(t)
            MATCH (s:Person)-[:SENT]->(m)
            WITH t, m.channel AS channel, collect({text: m.text, sender: s.name}) AS messages
            WITH t, collect({channel: channel, messages: messages}) AS channel_groups
            WHERE size(channel_groups) > 1
            CALL (t) {
                MATCH (any:Message)-[:ABOUT]->(t)
                RETURN count(any) AS total_messages_on_topic
            }
            RETURN
                t.name AS topic,
                channel_groups,
                total_messages_on_topic
            ORDER BY t.name
            LIMIT 20
            """,
            user_id=user_id,
        ).data()

    return json.dumps({"cross_channel_connections": results}, indent=2)


# ─────────────────────────────────────────────
# Tool 3: Find unresolved mentions for a person
# ─────────────────────────────────────────────
@mcp.tool()
def get_unresolved_mentions(user_id: str) -> str:
    """
    Finds messages that mention a specific person where they haven't replied yet.
    Useful for surfacing things the person was tagged in but may have missed.

    Args:
        user_id: The Slack user ID to check unresolved mentions for
    """
    with db.driver.session() as session:
        results = session.run(
            """
            MATCH (m:Message)-[:MENTIONS]->(p:Person {slack_user_id: $user_id})
            MATCH (sender:Person)-[:SENT]->(m)
            WHERE NOT EXISTS {
                MATCH (p)-[:SENT]->(reply:Message)-[:IN_THREAD]->(t:Thread)
                      <-[:IN_THREAD]-(m)
            }
            RETURN
                m.text AS message,
                m.channel AS channel,
                m.timestamp AS timestamp,
                sender.name AS mentioned_by
            ORDER BY m.timestamp DESC
            LIMIT 10
            """,
            user_id=user_id,
        ).data()

    return json.dumps({"unresolved_mentions": results}, indent=2)


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("🔌 Starting Slack Digest Agent MCP Server...")
    mcp.run(transport="stdio")