"""
Descasio Market Intelligence — Web Dashboard
Small Flask app that visualises market intelligence signals.

Currently served from sample/demo data (dashboard/sample_data.py).
To go live later, swap dashboard.sample_data.load_signals() for either:
  - delivery/zoho_crm.py ZohoCRMClient pulling from the Market_Intelligence module, or
  - a query against a local DB populated by orchestrator/pipeline.py

Run with: python -m dashboard.app
"""

from flask import Flask, jsonify, render_template

from dashboard.sample_data import load_signals

app = Flask(__name__)

PRIORITY_ORDER = {"URGENT": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/signals")
def api_signals():
    signals = sorted(
        load_signals(),
        key=lambda s: PRIORITY_ORDER.get(s.get("priority", "LOW"), 3),
    )
    return jsonify(signals)


@app.route("/api/stats")
def api_stats():
    signals = load_signals()
    countries = {}
    priorities = {}
    for s in signals:
        countries[s["country"]] = countries.get(s["country"], 0) + 1
        priorities[s["priority"]] = priorities.get(s["priority"], 0) + 1
    return jsonify(
        {
            "total": len(signals),
            "by_country": countries,
            "by_priority": priorities,
        }
    )


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
