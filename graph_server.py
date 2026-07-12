"""
graph_server.py

A minimal live web server for the knowledge graph — renders a person's
subgraph fresh from Neo4j on every request, instead of generating a
static file per person. This is what a Slack link button should point
at: click it, browser opens, graph is always current.

For a hackathon demo, run this locally and expose it with ngrok:

    python graph_server.py
    # in another terminal:
    ngrok http 5000

ngrok will print a public https URL (e.g. https://abcd1234.ngrok-free.app).
Put that in your .env as GRAPH_SERVER_BASE_URL, then blockkit_formatter.py
can build real link buttons pointing at {GRAPH_SERVER_BASE_URL}/graph/<user_id>.

NOTE: this has no authentication — anyone with the URL + a user_id can
view that person's graph. Fine for a demo on a temporary ngrok URL that
only you and judges see; NOT fine to leave running/public afterward.
That auth gap is exactly the kind of thing that separates "demo version"
from "production version" — worth saying out loud if a judge asks.
"""

from flask import Flask, abort
from graph_export import get_person_subgraph, build_html

app = Flask(__name__)


@app.route("/")
def health_check():
    return "Graph server is running. Try /graph/<slack_user_id>"


@app.route("/graph/<user_id>")
def graph_view(user_id):
    try:
        subgraph = get_person_subgraph(user_id)
    except Exception as e:
        abort(500, description=f"Failed to build subgraph: {e}")

    # get_person_subgraph always includes at least the person node itself,
    # even with zero messages — so "no data" means just that one bare node,
    # not literally zero nodes.
    if len(subgraph["nodes"]) <= 1:
        return (
            f"<h2>No graph data found for user_id '{user_id}'</h2>"
            f"<p>Check that this user has seeded messages in Neo4j.</p>",
            404,
        )

    html = build_html(user_id, subgraph)
    return html


if __name__ == "__main__":
    print("🌐 Starting graph server on http://localhost:5000")
    print("   Try http://localhost:5000/graph/U_MARIA in your browser")
    print("   For a public URL (needed for Slack buttons), run in another terminal:")
    print("     ngrok http 5000")
    app.run(host="0.0.0.0", port=5000, debug=True)
