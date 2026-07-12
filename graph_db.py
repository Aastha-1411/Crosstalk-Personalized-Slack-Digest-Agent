"""
Neo4j connection handler for Slack Digest Agent.
Handles connecting to AuraDB and basic graph operations.
"""

import os
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

URI = os.environ.get("NEO4J_URI")
USERNAME = os.environ.get("NEO4J_USERNAME")
PASSWORD = os.environ.get("NEO4J_PASSWORD")


class GraphDB:
    def __init__(self):
        self.driver = GraphDatabase.driver(URI, auth=(USERNAME, PASSWORD))

    def close(self):
        self.driver.close()

    def verify_connection(self):
        """Quick test to confirm the connection works."""
        with self.driver.session() as session:
            result = session.run("RETURN 'Connection successful!' AS message")
            return result.single()["message"]

    def setup_constraints(self):
        """
        Create uniqueness constraints so we don't get duplicate
        Person/Topic/Thread nodes as messages come in.
        """
        with self.driver.session() as session:
            session.run(
                "CREATE CONSTRAINT person_id IF NOT EXISTS "
                "FOR (p:Person) REQUIRE p.slack_user_id IS UNIQUE"
            )
            session.run(
                "CREATE CONSTRAINT message_id IF NOT EXISTS "
                "FOR (m:Message) REQUIRE m.id IS UNIQUE"
            )
            session.run(
                "CREATE CONSTRAINT topic_name IF NOT EXISTS "
                "FOR (t:Topic) REQUIRE t.name IS UNIQUE"
            )
            session.run(
                "CREATE CONSTRAINT thread_id IF NOT EXISTS "
                "FOR (th:Thread) REQUIRE th.id IS UNIQUE"
            )
        print("✅ Constraints created (or already existed).")


if __name__ == "__main__":
    db = GraphDB()
    try:
        message = db.verify_connection()
        print(f"🎉 {message}")
        db.setup_constraints()
    finally:
        db.close()
