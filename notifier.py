"""
ntfy-based human approval gate for the publishing pipeline.

Flow:
  1. send_approval_request() pushes a notification to the ntfy topic with two
     action buttons, Approve and Reject. Each button is an HTTP action that
     publishes a marker message ("APPROVE <token>" / "REJECT <token>") back to
     the same topic. The token guards against anyone else on the public topic
     triggering an approval.
  2. wait_for_approval() polls the topic every POLL_INTERVAL seconds for up to
     TIMEOUT_SECONDS, looking for one of those marker messages.

Returns one of: "approve", "reject", "timeout".
"""

import os
import time
import json

import requests

NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "reelpipelineethan1212")
APPROVE_TRIGGER_TOKEN = os.environ.get("APPROVE_TRIGGER_TOKEN", "")

POLL_INTERVAL = 30          # seconds between polls
TIMEOUT_SECONDS = 30 * 60   # 30 minutes

APPROVE_MARKER = "APPROVE"
REJECT_MARKER = "REJECT"


def _topic_url() -> str:
    return f"{NTFY_SERVER}/{NTFY_TOPIC}"


def _approve_body() -> str:
    return f"{APPROVE_MARKER} {APPROVE_TRIGGER_TOKEN}"


def _reject_body() -> str:
    return f"{REJECT_MARKER} {APPROVE_TRIGGER_TOKEN}"


def send_approval_request(topic: str) -> int:
    """
    Send the approval notification with Approve / Reject buttons.
    Returns a unix timestamp to use as the polling 'since' anchor.
    """
    if not APPROVE_TRIGGER_TOKEN:
        raise ValueError("APPROVE_TRIGGER_TOKEN not set")

    # Anchor polling just before publishing so we never miss the response.
    since = int(time.time())

    payload = {
        "topic": NTFY_TOPIC,
        "title": f"Cryptid Files: {topic} ready to publish",
        "message": "Tap Approve to upload to YouTube",
        "priority": 4,
        "tags": ["clapper"],
        "actions": [
            {
                "action": "http",
                "label": "Approve",
                "url": _topic_url(),
                "method": "POST",
                "body": _approve_body(),
                "clear": True,
            },
            {
                "action": "http",
                "label": "Reject",
                "url": _topic_url(),
                "method": "POST",
                "body": _reject_body(),
                "clear": True,
            },
        ],
    }

    resp = requests.post(NTFY_SERVER, json=payload, timeout=30)
    resp.raise_for_status()
    print(f"  [ntfy] approval request sent to topic '{NTFY_TOPIC}'")
    return since


def poll_for_response(since: int):
    """
    Poll the topic for messages since `since`. Returns "approve", "reject",
    or None if no decision has arrived yet.
    """
    url = f"{NTFY_SERVER}/{NTFY_TOPIC}/json?poll=1&since={since}"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()

    decision = None
    for line in resp.text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("event") != "message":
            continue
        body = (msg.get("message") or "").strip()
        # Take the latest decision in the stream (messages are time-ordered).
        if body == _approve_body():
            decision = "approve"
        elif body == _reject_body():
            decision = "reject"
    return decision


def wait_for_approval(topic: str) -> str:
    """
    Send the request and poll until a decision arrives or we time out.
    Returns "approve", "reject", or "timeout".
    """
    since = send_approval_request(topic)
    deadline = since + TIMEOUT_SECONDS

    while time.time() < deadline:
        time.sleep(POLL_INTERVAL)
        decision = poll_for_response(since)
        if decision in ("approve", "reject"):
            print(f"  [ntfy] decision received: {decision}")
            return decision
        remaining = int((deadline - time.time()) / 60)
        print(f"  [ntfy] no response yet, ~{remaining} min left...")

    print("  [ntfy] no response within timeout window")
    return "timeout"


if __name__ == "__main__":
    import sys
    topic = sys.argv[1] if len(sys.argv) > 1 else "Test Cryptid"
    print(wait_for_approval(topic))
