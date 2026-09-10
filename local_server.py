"""
Minimal Flask wrapper for local testing.

Install: pip install flask
Run:     python local_server.py
Listens: http://localhost:8000
"""

from flask import Flask, request, jsonify, Response
import json
from app import app_handler

app = Flask(__name__)


@app.route("/", methods=["POST", "OPTIONS"])
def handler():
    if request.method == "OPTIONS":
        return Response(
            status=204,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "Content-Type",
                "Access-Control-Allow-Methods": "POST, OPTIONS",
            },
        )
    event = request.get_json(force=True)
    result = app_handler(event, None)
    body = json.loads(result["body"])
    response = jsonify(body)
    response.status_code = result["statusCode"]
    for key, value in result.get("headers", {}).items():
        response.headers[key] = value
    return response


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
