"""
Graph operations for Slack Digest Agent.
Converts raw Slack message events into Neo4j nodes and relationships.
"""

import re
from graph_db import GraphDB


class MessageGraphWriter:
    def __init__(self, graph_db: GraphDB):
        self.db = graph_db

    def ensure_person(self, slack_user_id: str, name: str = None):
        """
        Create a Person node if it doesn't exist yet.
        Uses MERGE so re-running this for the same user is safe (no duplicates).
        """
        with self.db.driver.session() as session:
            session.run(
                """
                MERGE (p:Person {slack_user_id: $slack_user_id})
                ON CREATE SET p.name = $name
                ON MATCH SET p.name = coalesce($name, p.name)
                """,
                slack_user_id=slack_user_id,
                name=name,
            )

    def write_message(
        self,
        message_id: str,
        sender_id: str,
        channel: str,
        text: str,
        timestamp: str,
        thread_id: str = None,
        mentioned_user_ids: list = None,
    ):
        """
        Writes a full message into the graph:
        - Creates the Message node
        - Links Person -[:SENT]-> Message
        - Links Message -[:IN_THREAD]-> Thread (if part of a thread)
        - Links Message -[:MENTIONS]-> Person (for each @mention)
        """
        mentioned_user_ids = mentioned_user_ids or []

        with self.db.driver.session() as session:
            # Ensure sender exists, create the message, link them
            session.run(
                """
                MERGE (sender:Person {slack_user_id: $sender_id})
                MERGE (m:Message {id: $message_id})
                ON CREATE SET
                    m.text = $text,
                    m.channel = $channel,
                    m.timestamp = $timestamp
                MERGE (sender)-[:SENT]->(m)
                """,
                sender_id=sender_id,
                message_id=message_id,
                text=text,
                channel=channel,
                timestamp=timestamp,
            )

            # Link to thread if this message is part of one
            if thread_id:
                session.run(
                    """
                    MATCH (m:Message {id: $message_id})
                    MERGE (t:Thread {id: $thread_id})
                    ON CREATE SET t.channel = $channel
                    MERGE (m)-[:IN_THREAD]->(t)
                    """,
                    message_id=message_id,
                    thread_id=thread_id,
                    channel=channel,
                )

            # Link mentions
            for mentioned_id in mentioned_user_ids:
                session.run(
                    """
                    MATCH (m:Message {id: $message_id})
                    MERGE (mentioned:Person {slack_user_id: $mentioned_id})
                    MERGE (m)-[:MENTIONS]->(mentioned)
                    """,
                    message_id=message_id,
                    mentioned_id=mentioned_id,
                )

    def link_message_to_topic(self, message_id: str, topic_name: str):
        """
        Links a Message to a Topic node (created by Claude extraction step later).
        Kept separate so topic extraction can run async/after ingestion.
        """
        with self.db.driver.session() as session:
            session.run(
                """
                MATCH (m:Message {id: $message_id})
                MERGE (t:Topic {name: $topic_name})
                MERGE (m)-[:ABOUT]->(t)
                """,
                message_id=message_id,
                topic_name=topic_name.lower().strip(),
            )

    def get_existing_topics(self) -> list:
        """
        Returns every topic name currently in the graph.

        Used to tell the topic extractor what topics already exist, so it
        can reuse "user authentication" instead of inventing "login issue"
        or "jwt" for the same underlying subject. Without this, semantically
        identical messages end up on different Topic nodes and the
        cross-channel [:ABOUT] connections — the core feature — silently
        stop firing.
        """
        with self.db.driver.session() as session:
            result = session.run("MATCH (t:Topic) RETURN t.name AS name")
            return [record["name"] for record in result]

    def get_message_text(self, message_id: str) -> str:
        """
        Returns the text of a single message by id, or None if it isn't
        in the graph yet.

        In Slack, a thread reply's `thread_ts` equals the root message's
        own `ts` — so the root message is just MATCH (m:Message {id: thread_ts}).
        We use this to pull the thread-starting message as context for
        elliptical replies ("yeah that fixed it", "still happening") that
        have no topic signal on their own.
        """
        with self.db.driver.session() as session:
            result = session.run(
                "MATCH (m:Message {id: $message_id}) RETURN m.text AS text",
                message_id=message_id,
            )
            record = result.single()
            return record["text"] if record else None


def extract_mentions(text: str) -> list:
    """
    Extracts Slack user IDs from <@U12345> style mentions in message text.
    """
    return re.findall(r"<@([A-Z0-9]+)>", text)


if __name__ == "__main__":
    # Quick manual test
    db = GraphDB()
    writer = MessageGraphWriter(db)

    writer.write_message(
        message_id="test_msg_001",
        sender_id="U_TEST_1",
        channel="general",
        text="Hey <@U_TEST_2>, can you check the API rate limit issue?",
        timestamp="1718900000.000100",
        mentioned_user_ids=extract_mentions(
            "Hey <@U_TEST_2>, can you check the API rate limit issue?"
        ),
    )
    writer.link_message_to_topic("test_msg_001", "rate limiting")

    print("✅ Test message written to graph successfully.")
    db.close()