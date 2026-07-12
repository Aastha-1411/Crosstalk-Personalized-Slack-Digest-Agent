"""
graph_export.py

Builds an interactive HTML visualization of a specific person's subgraph:
their own messages, the topics those messages are about, and any OTHER
messages (different sender, possibly different channel) connected via
the same topic — i.e. exactly the cross-channel connections your digest
surfaces as text, but rendered as an actual visual graph.

Usage:
    python graph_export.py U_MARIA

Outputs graph_view_U_MARIA.html — open it directly in a browser. No web
server needed; the graph data is embedded directly in the file (avoids
fetch()/CORS issues that happen when loading local JSON via file://).
"""

import sys
import os
import json
from graph_db import GraphDB


def get_person_subgraph(user_id: str) -> dict:
    """
    Returns {"nodes": [...], "edges": [...]} for this person's subgraph:
      - the Person themselves
      - every Message they sent, and the Topic each is ABOUT
      - every OTHER Message (different sender) ABOUT the same Topic —
        this is what makes cross-channel connections visible as edges
      - the Person who sent each of those other messages
    """
    db = GraphDB()
    nodes = {}
    edges = []

    def add_node(node_id, label, group, title=None):
        if node_id not in nodes:
            nodes[node_id] = {"id": node_id, "label": label, "group": group, "title": title or label}

    with db.driver.session() as session:
        person_row = session.run(
            "MATCH (p:Person {slack_user_id: $uid}) RETURN p.name AS name",
            uid=user_id,
        ).single()
        person_name = (person_row["name"] if person_row and person_row["name"] else user_id)
        add_node(user_id, person_name, "target_person")

        own = session.run(
            """
            MATCH (p:Person {slack_user_id: $uid})-[:SENT]->(m:Message)
            OPTIONAL MATCH (m)-[:ABOUT]->(t:Topic)
            RETURN m.id AS mid, m.text AS text, m.channel AS channel, t.name AS topic
            """,
            uid=user_id,
        ).data()

        topic_names = set()
        for row in own:
            mid = row["mid"]
            add_node(mid, (row["text"] or "")[:40], "own_message", title=f"[{row['channel']}] {row['text']}")
            edges.append({"from": user_id, "to": mid, "label": "sent"})
            if row["topic"]:
                topic_names.add(row["topic"])
                add_node(row["topic"], row["topic"], "topic")
                edges.append({"from": mid, "to": row["topic"], "label": "about"})

        MAX_OTHER_MESSAGES_PER_TOPIC = 4  # keeps the graph readable — this
        # is a proof-of-connection visual, not a full transcript, so we
        # don't need every message, just enough to show the link is real

        for topic in topic_names:
            others = session.run(
                """
                MATCH (m:Message)-[:ABOUT]->(t:Topic {name: $topic})
                MATCH (s:Person)-[:SENT]->(m)
                WHERE NOT (:Person {slack_user_id: $uid})-[:SENT]->(m)
                RETURN m.id AS mid, m.text AS text, m.channel AS channel,
                       s.slack_user_id AS sender_id, s.name AS sender_name,
                       m.timestamp AS timestamp
                ORDER BY m.timestamp ASC
                """,
                topic=topic, uid=user_id,
            ).data()

            # Keep only each person's FIRST message per topic — their
            # other messages on the same topic are usually a follow-up
            # thread, not a separate connection worth its own node.
            seen_senders = set()
            deduped = []
            for row in others:
                if row["sender_id"] not in seen_senders:
                    seen_senders.add(row["sender_id"])
                    deduped.append(row)

            for row in deduped[:MAX_OTHER_MESSAGES_PER_TOPIC]:
                mid = row["mid"]
                sid = row["sender_id"]
                add_node(mid, (row["text"] or "")[:40], "other_message", title=f"[{row['channel']}] {row['text']}")
                add_node(sid, row["sender_name"] or sid, "other_person")
                edges.append({"from": sid, "to": mid, "label": "sent"})
                edges.append({"from": mid, "to": topic, "label": "about"})

    db.close()
    return {"nodes": list(nodes.values()), "edges": edges}


HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Knowledge Graph — {person_name}</title>
  <script>{vis_network_js}</script>
  <style>
    body {{ margin: 0; font-family: -apple-system, sans-serif; background: #0f1117; color: #e6e6e6; }}
    #header {{ padding: 16px 24px; border-bottom: 1px solid #2a2d3a; }}
    #header h1 {{ margin: 0; font-size: 18px; }}
    #header p {{ margin: 4px 0 0; color: #9098a8; font-size: 13px; }}
    #graph {{ width: 100vw; height: calc(100vh - 70px); }}
    .legend {{ position: absolute; top: 80px; left: 20px; background: #1a1d29; padding: 12px 16px;
               border-radius: 8px; font-size: 12px; line-height: 1.8; border: 1px solid #2a2d3a; }}
    .dot {{ display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; }}
  </style>
</head>
<body>
  <div id="header">
    <h1>🧠 Knowledge Graph — {person_name}</h1>
    <p>Their own messages, the topics involved, and cross-channel connections to other people's messages on the same topics.</p>
  </div>
  <div class="legend">
    <div><span class="dot" style="background:#4f8ef7"></span>{person_name} (target person)</div>
    <div><span class="dot" style="background:#6dd47e"></span>Their messages</div>
    <div><span class="dot" style="background:#f7b84f"></span>Topics</div>
    <div><span class="dot" style="background:#e05c5c"></span>Other people's messages</div>
    <div><span class="dot" style="background:#c77dff"></span>Other people</div>
  </div>
  <div id="graph"></div>
  <script>
    const rawNodes = {nodes_json};
    const rawEdges = {edges_json};

    const groupColors = {{
      target_person: "#4f8ef7",
      own_message: "#6dd47e",
      topic: "#f7b84f",
      other_message: "#e05c5c",
      other_person: "#c77dff",
    }};

    const nodes = new vis.DataSet(rawNodes.map(n => ({{
      id: n.id, label: n.label, title: n.title,
      color: groupColors[n.group] || "#999",
      shape: n.group.includes("person") ? "dot" : (n.group === "topic" ? "diamond" : "box"),
      font: {{ color: "#e6e6e6", size: 12 }},
      size: n.group === "target_person" ? 24 : (n.group === "topic" ? 18 : 14),
    }})));
    const edges = new vis.DataSet(rawEdges.map((e, i) => ({{
      id: i, from: e.from, to: e.to, label: e.label,
      color: {{ color: "#3a3d4a" }}, font: {{ color: "#6a6d7a", size: 10 }},
      arrows: "to",
    }})));

    const container = document.getElementById("graph");
    const data = {{ nodes, edges }};
    const options = {{
      physics: {{ stabilization: true, barnesHut: {{ gravitationalConstant: -3000 }} }},
      interaction: {{ hover: true }},
    }};
    new vis.Network(container, data, options);
  </script>
</body>
</html>
"""


def build_html(user_id: str, subgraph: dict) -> str:
    person_node = next((n for n in subgraph["nodes"] if n["id"] == user_id), None)
    person_name = person_node["label"] if person_node else user_id

    # Embed vis-network directly so the file works with zero internet
    # access — important for a demo where you don't want to depend on a
    # CDN being reachable at the exact moment judges are watching.
    vis_js_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vis-network.min.js")
    try:
        with open(vis_js_path, "r", encoding="utf-8") as f:
            vis_network_js = f.read()
    except FileNotFoundError:
        print(f"⚠️  {vis_js_path} not found — falling back to loading vis-network from a CDN, "
              f"which requires internet access to work. Keep vis-network.min.js next to this "
              f"script to avoid depending on that.")
        vis_network_js = ""  # HTML below falls back to a CDN <script> tag if this is empty

    html = HTML_TEMPLATE.format(
        person_name=person_name,
        vis_network_js=vis_network_js,
        nodes_json=json.dumps(subgraph["nodes"]),
        edges_json=json.dumps(subgraph["edges"]),
    )

    if not vis_network_js:
        html = html.replace(
            "<script></script>",
            '<script src="https://cdnjs.cloudflare.com/ajax/libs/vis-network/9.1.9/vis-network.min.js"></script>',
        )

    return html


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python graph_export.py <slack_user_id>")
        sys.exit(1)

    target_user_id = sys.argv[1]
    print(f"📊 Building subgraph for {target_user_id}...")
    subgraph = get_person_subgraph(target_user_id)
    print(f"   {len(subgraph['nodes'])} nodes, {len(subgraph['edges'])} edges")

    html = build_html(target_user_id, subgraph)
    out_path = f"graph_view_{target_user_id}.html"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ Wrote {out_path} — open it in a browser")