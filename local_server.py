from flask import Flask, request, Response
import json
from app import app_handler

app = Flask(__name__)

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,x-app-password",
    "Access-Control-Allow-Methods": "POST,OPTIONS",
    "Access-Control-Max-Age": "86400",  # Keep OPTION cache for up to max 24 hours
}


@app.route("/", methods=["POST", "OPTIONS"])
def handler():
    # Handle OPTIONS locally (API Gateway does this in prod, Lambda never sees it)
    if request.method == "OPTIONS":
        return Response("", status=204, headers=CORS_HEADERS)

    # Simulate HTTP API payload format 2.0
    event = {
        "version": "2.0",
        "requestContext": {"http": {"method": "POST"}},
        "headers": {k.lower(): v for k, v in request.headers.items()},
        "body": request.get_data(as_text=True),
        "isBase64Encoded": False,
    }

    result = app_handler(event, None)

    return Response(
        result.get("body", ""),
        status=result.get("statusCode", 200),
        headers=result.get("headers", {}),
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
