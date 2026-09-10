"""
analysis-service
-----------------
A deliberately separate, different-language (Python vs the Node services)
microservice. It does NOT get called directly by game-service. Instead it
subscribes to a Redis pub/sub channel and reacts to moves asynchronously.

Why this matters for the interview: this is the concrete answer to
"why would you use async messaging instead of a direct REST call between
services?" — if this service is slow, crashed, or being redeployed, the
chess game itself is completely unaffected. Only the win-probability
number stops updating.

Win probability here is a simple material-count heuristic (not a real
chess engine). To make this "for real" later, swap `estimate_win_probability`
for a call to a Stockfish binary (python-chess + stockfish) and evaluate
centipawns instead of raw material — the pub/sub plumbing around it doesn't
need to change at all, which is itself a nice lesson in why decoupling
services pays off.
"""
import json
import math
import os
import threading

import redis
import requests
from flask import Flask, jsonify, request

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379")
PORT = int(os.environ.get("PORT", 3004))

# Set only when game-service is the Cloudflare Worker/Durable Object version
# instead of the docker-compose one — a DO can't hold a persistent Redis
# SUBSCRIBE across hibernation, so it calls POST /internal/moves here instead
# of publishing to Redis, and expects the result pushed back over HTTP too.
WORKER_CALLBACK_URL = os.environ.get("WORKER_CALLBACK_URL", "")
ANALYSIS_SHARED_SECRET = os.environ.get("ANALYSIS_SHARED_SECRET", "")

PIECE_VALUES = {"p": 1, "n": 3, "b": 3, "r": 5, "q": 9, "k": 0}

app = Flask(__name__)
r = redis.Redis.from_url(REDIS_URL, decode_responses=True)


def material_balance(fen: str) -> int:
    """Positive = white ahead in material, negative = black ahead."""
    board_part = fen.split(" ")[0]
    balance = 0
    for ch in board_part:
        if ch.isalpha():
            value = PIECE_VALUES.get(ch.lower(), 0)
            balance += value if ch.isupper() else -value
    return balance


def estimate_win_probability(fen: str) -> dict:
    diff = material_balance(fen)
    # Logistic curve centered at 0 material difference — same shape used by
    # real engines to convert an evaluation score into a win percentage.
    white_prob = 1 / (1 + math.exp(-diff / 3))
    return {
        "white_win_pct": round(white_prob * 100, 1),
        "black_win_pct": round((1 - white_prob) * 100, 1),
        "material_balance": diff,
    }


def process_move(data: dict) -> dict:
    """Shared by both the Redis listener and the /internal/moves HTTP route
    so a docker-compose game-service (Redis pub/sub) and a Cloudflare
    Worker/DO game-service (HTTP) get identical analysis behavior."""
    room_id = data["roomId"]
    result = estimate_win_probability(data["fen"])
    result["roomId"] = room_id
    r.set(f"analysis:latest:{room_id}", json.dumps(result))
    r.publish(f"analysis:{room_id}", json.dumps(result))
    notify_worker_callback(room_id, result)
    return result


def notify_worker_callback(room_id: str, result: dict) -> None:
    if not WORKER_CALLBACK_URL:
        return
    try:
        requests.post(
            f"{WORKER_CALLBACK_URL}/internal/analysis-callback/{room_id}",
            json=result,
            headers={"x-internal-secret": ANALYSIS_SHARED_SECRET},
            timeout=2,
        )
    except Exception as exc:  # noqa: BLE001 - best-effort, never blocks the Redis path
        print(f"analysis-service: worker callback failed: {exc}")


def listen_for_moves():
    pubsub = r.pubsub()
    pubsub.psubscribe("moves:*")
    print("analysis-service: subscribed to moves:*")
    for message in pubsub.listen():
        if message["type"] != "pmessage":
            continue
        try:
            process_move(json.loads(message["data"]))
        except Exception as exc:  # noqa: BLE001 - log and keep the loop alive
            print(f"analysis-service error processing message: {exc}")


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "analysis-service"})


@app.get("/rooms/<room_id>/analysis")
def latest_analysis(room_id):
    cached = r.get(f"analysis:latest:{room_id}")
    if not cached:
        return jsonify({"error": "no analysis yet"}), 404
    return jsonify(json.loads(cached))


@app.post("/internal/moves")
def internal_moves():
    if request.headers.get("x-internal-secret") != ANALYSIS_SHARED_SECRET:
        return jsonify({"error": "unauthorized"}), 401
    try:
        result = process_move(request.get_json(force=True))
        return jsonify(result)
    except Exception as exc:  # noqa: BLE001 - bad payload from the caller
        return jsonify({"error": str(exc)}), 400


if __name__ == "__main__":
    thread = threading.Thread(target=listen_for_moves, daemon=True)
    thread.start()
    app.run(host="0.0.0.0", port=PORT)
