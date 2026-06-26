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
import unicodedata

import requests

NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "reelpipelineethan1212")
APPROVE_TRIGGER_TOKEN = os.environ.get("APPROVE_TRIGGER_TOKEN", "")

POLL_INTERVAL = 30          # seconds between polls
# Approval window. Kept safely below the CI job's timeout-minutes (360) so the
# pipeline reaches its own timeout branch and advances to the next topic
# gracefully, instead of being hard-killed by GitHub mid-wait (which would
# leave no chance to move on). ~5.5 hours leaves headroom for generation and
# the progress commit inside the 6-hour job ceiling. Set to None for no limit.
TIMEOUT_SECONDS = int(5.5 * 60 * 60)   # 5.5 hours

APPROVE_MARKER = "APPROVE"
REJECT_MARKER = "REJECT"


# Common non-latin-1 characters mapped to safe ASCII equivalents. requests
# encodes HTTP headers as latin-1, so anything outside it raises
# UnicodeEncodeError unless we transliterate first.
_HEADER_REPLACEMENTS = {
    "—": "-",    # em dash
    "–": "-",    # en dash
    "‒": "-",    # figure dash
    "―": "-",    # horizontal bar
    "‘": "'",    # left single quote
    "’": "'",    # right single quote
    "“": '"',    # left double quote
    "”": '"',    # right double quote
    "…": "...",  # ellipsis
    " ": " ",    # non-breaking space
}


def _ascii_safe(value: str) -> str:
    """Make a string safe for latin-1 HTTP headers using ASCII equivalents."""
    if value is None:
        return value
    for bad, good in _HEADER_REPLACEMENTS.items():
        value = value.replace(bad, good)
    # Transliterate any remaining non-ASCII (e.g. accents, emoji) to ASCII,
    # dropping characters with no equivalent.
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")


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


def send_approval_request(topic: str, video_id: str = None) -> int:
    """
    Send the approval notification with Approve / Reject buttons and, when
    available, the first scene image as a preview thumbnail. When video_id is
    given, the unlisted YouTube link is included so it can be watched first.
    Returns a unix timestamp to use as the polling 'since' anchor.
    """
    if not APPROVE_TRIGGER_TOKEN:
        raise ValueError("APPROVE_TRIGGER_TOKEN not set")

    # Anchor polling just before publishing so we never miss the response.
    since = int(time.time())

    # HTTP header values must stay single-line, so keep the link inline.
    if video_id:
        message = (
            f"Watch: https://youtu.be/{video_id}  —  "
            f"Approve to publish, Reject to delete"
        )
    else:
        message = "Tap Approve to upload to YouTube"

    # Title/message/actions go in headers so we can send the image as the body.
    headers = {
        "Title": f"Cryptid Files: {topic} ready to publish",
        "Message": message,
        "Priority": "4",
        "Tags": "clapper",
        "Actions": _actions_header(),
    }
    # Sanitize every header value so non-latin-1 characters (em dashes, emoji,
    # accents) can't raise UnicodeEncodeError when requests encodes the headers.
    headers = {key: _ascii_safe(value) for key, value in headers.items()}

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


def wait_for_approval(topic: str, video_id: str = None) -> str:
    """
    Send the request and poll until a decision arrives or we time out.
    Returns "approve", "reject", or "timeout".
    """
    since = send_approval_request(topic, video_id)
    unlimited = TIMEOUT_SECONDS is None
    deadline = None if unlimited else since + TIMEOUT_SECONDS

    while unlimited or time.time() < deadline:
        time.sleep(POLL_INTERVAL)
        decision = poll_for_response(since)
        if decision in ("approve", "reject"):
            print(f"  [ntfy] decision received: {decision}")
            return decision
        if unlimited:
            print("  [ntfy] no response yet, waiting indefinitely...")
        else:
            remaining = int((deadline - time.time()) / 60)
            print(f"  [ntfy] no response yet, ~{remaining} min left...")

    print("  [ntfy] no response within timeout window")
    return "timeout"


if __name__ == "__main__":
    import sys
    topic = sys.argv[1] if len(sys.argv) > 1 else "Test Cryptid"
    print(wait_for_approval(topic))
