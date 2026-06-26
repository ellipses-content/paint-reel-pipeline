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


def _topic_slug(topic: str) -> str:
    """Mirror the output-dir slug logic used in generate.py."""
    return topic.lower().replace(" ", "_").replace("(", "").replace(")", "")


def _thumbnail_path(topic: str) -> str:
    """First scene image used as the notification preview thumbnail."""
    return os.path.join("output", _topic_slug(topic), "images", "scene_00.png")


def _actions_header() -> str:
    """
    Build the Actions value in ntfy's header (string) format. The JSON action
    form can't be combined with a binary file body, so we use the header form
    when attaching the thumbnail.
    """
    approve = (
        f"http, Approve, {_topic_url()}, method=POST, "
        f"body={_approve_body()}, clear=true"
    )
    reject = (
        f"http, Reject, {_topic_url()}, method=POST, "
        f"body={_reject_body()}, clear=true"
    )
    return "; ".join([approve, reject])


def send_approval_request(topic: str) -> int:
    """
    Send the approval notification with Approve / Reject buttons and, when
    available, the first scene image as a preview thumbnail.
    Returns a unix timestamp to use as the polling 'since' anchor.
    """
    if not APPROVE_TRIGGER_TOKEN:
        raise ValueError("APPROVE_TRIGGER_TOKEN not set")

    # Anchor polling just before publishing so we never miss the response.
    since = int(time.time())

    # Title/message/actions go in headers so we can send the image as the body.
    headers = {
        "Title": f"Cryptid Files: {topic} ready to publish",
        "Message": "Tap Approve to upload to YouTube",
        "Priority": "4",
        "Tags": "clapper",
        "Actions": _actions_header(),
    }

    thumb = _thumbnail_path(topic)
    if os.path.exists(thumb):
        headers["Filename"] = "scene_00.png"
        with open(thumb, "rb") as f:
            image_data = f.read()
        resp = requests.put(_topic_url(), data=image_data, headers=headers, timeout=60)
        print(f"  [ntfy] approval request sent to '{NTFY_TOPIC}' with thumbnail {thumb}")
    else:
        # No thumbnail available — send the notification without an attachment.
        resp = requests.put(_topic_url(), data=b"", headers=headers, timeout=30)
        print(f"  [ntfy] thumbnail not found at {thumb}; sent without image")

    resp.raise_for_status()
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
