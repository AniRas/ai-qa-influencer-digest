"""Daily AI-in-QA Influencer Digest: X (Twitter) -> Telegram channel.

Runs once per day (GitHub Actions cron). Sends only posts published on the
target day (Asia/Yerevan calendar day). Older posts are skipped.

Environment variables:
  X_BEARER_TOKEN      required  X API v2 app Bearer token
  TELEGRAM_BOT_TOKEN  required  token from @BotFather
  TELEGRAM_CHAT_ID    required  channel username (e.g. @my_channel) or numeric id
  DIGEST_TZ           optional  default "Asia/Yerevan"
  LATE_RUN_GRACE_HOURS optional default 3 (see target_day())
  INCLUDE_REPLIES     optional  "true" to include replies (default: false)
  DRY_RUN             optional  "true" = print messages, do not send to Telegram
"""

from __future__ import annotations

import html
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

X_API = "https://api.x.com/2"
TG_API = "https://api.telegram.org"
EXCERPT_LIMIT = 700          # characters of post text shown in Telegram
HTTP_TIMEOUT = 30            # seconds
CONFIG_FILE = Path(__file__).with_name("influencers.json")


@dataclass
class Post:
    author_name: str
    username: str
    post_id: str
    text: str
    created_at: datetime     # timezone-aware, UTC

    @property
    def url(self) -> str:
        return f"https://x.com/{self.username}/status/{self.post_id}"


# ---------------------------------------------------------------- date logic

def target_day(now_utc: datetime, tz: ZoneInfo, grace_hours: int = 3) -> date:
    """Return the local calendar day the digest is for.

    GitHub Actions cron can start late. If a run planned for 23:30 starts
    after midnight, it must still report the *previous* day. Any run in the
    first `grace_hours` after local midnight is treated as a late run.
    """
    local_now = now_utc.astimezone(tz)
    return (local_now - timedelta(hours=grace_hours)).date()


def day_window_utc(day: date, tz: ZoneInfo, now_utc: datetime) -> tuple[datetime, datetime]:
    """Start and end (UTC) of the local day; end is capped at 'now'."""
    start = datetime.combine(day, dtime.min, tzinfo=tz).astimezone(timezone.utc)
    end = (datetime.combine(day + timedelta(days=1), dtime.min, tzinfo=tz)
           .astimezone(timezone.utc))
    return start, min(end, now_utc)


def is_in_window(created_at: datetime, start: datetime, end: datetime) -> bool:
    """Second, independent date check (the API filter is the first)."""
    return start <= created_at < end


def parse_x_time(value: str) -> datetime:
    # X returns e.g. "2026-09-25T10:15:00.000Z"
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def to_x_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- X API

class XClient:
    def __init__(self, bearer_token: str, session: requests.Session | None = None):
        self.s = session or requests.Session()
        self.s.headers["Authorization"] = f"Bearer {bearer_token}"

    def _get(self, path: str, params: dict) -> dict:
        r = self.s.get(f"{X_API}{path}", params=params, timeout=HTTP_TIMEOUT)
        if r.status_code == 429:
            raise RuntimeError("X API rate limit (429). Try again later.")
        if r.status_code in (401, 403):
            raise RuntimeError(f"X API auth/access error {r.status_code}: {r.text[:300]}")
        if r.status_code == 402:
            raise RuntimeError("X API: payment required (402). Add credits in the X developer console.")
        r.raise_for_status()
        return r.json()

    def lookup_user_ids(self, usernames: list[str]) -> dict[str, str]:
        """username(lower) -> user id. Unknown usernames are left out."""
        if not usernames:
            return {}
        data = self._get("/users/by", {"usernames": ",".join(usernames)})
        for err in data.get("errors", []):
            print(f"WARNING: user lookup failed: {err.get('value')} - {err.get('detail')}")
        return {u["username"].lower(): u["id"] for u in data.get("data", [])}

    def posts_between(self, user_id: str, start: datetime, end: datetime,
                      include_replies: bool) -> list[dict]:
        exclude = "retweets" if include_replies else "retweets,replies"
        params = {
            "start_time": to_x_time(start),
            "end_time": to_x_time(end),
            "max_results": 100,
            "exclude": exclude,
            "tweet.fields": "created_at,note_tweet",
        }
        posts: list[dict] = []
        while True:
            data = self._get(f"/users/{user_id}/tweets", params)
            posts.extend(data.get("data", []))
            token = data.get("meta", {}).get("next_token")
            if not token:
                return posts
            params["pagination_token"] = token


# ---------------------------------------------------------------- Telegram

def format_post(p: Post, tz: ZoneInfo) -> str:
    text = p.text.strip()
    if len(text) > EXCERPT_LIMIT:
        text = text[:EXCERPT_LIMIT].rstrip() + "…"
    local = p.created_at.astimezone(tz).strftime("%H:%M")
    return (
        f"<b>{html.escape(p.author_name)}</b> (@{html.escape(p.username)}) · {local}\n\n"
        f"{html.escape(text)}\n\n"
        f'<a href="{html.escape(p.url)}">Open original post</a>'
    )


def send_telegram(bot_token: str, chat_id: str, text: str,
                  session: requests.Session | None = None, attempts: int = 3) -> None:
    s = session or requests
    for _ in range(attempts):
        r = s.post(
            f"{TG_API}/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=HTTP_TIMEOUT,
        )
        if r.status_code == 429:  # Telegram flood control
            wait = r.json().get("parameters", {}).get("retry_after", 5)
            time.sleep(wait)
            continue
        if not r.ok:
            # never print the URL: it contains the bot token
            raise RuntimeError(f"Telegram error {r.status_code}: {r.text[:300]}")
        return
    raise RuntimeError("Telegram: still rate-limited after retries")


# ---------------------------------------------------------------- main flow

def collect_posts(x: XClient, influencers: list[dict], start: datetime, end: datetime,
                  include_replies: bool) -> tuple[list[Post], list[str]]:
    """Return (posts sorted oldest->newest, list of error messages)."""
    errors: list[str] = []

    # 1) resolve user ids (only for entries without a saved id)
    missing = [i["username"] for i in influencers if not i.get("id")]
    if missing:
        found = x.lookup_user_ids(missing)
        for inf in influencers:
            if not inf.get("id") and inf["username"].lower() in found:
                inf["id"] = found[inf["username"].lower()]
                print(f"INFO: save to influencers.json -> {inf['username']}: id \"{inf['id']}\"")

    # 2) fetch posts; de-duplicate by user id
    posts: list[Post] = []
    seen_users: set[str] = set()
    for inf in influencers:
        uid = inf.get("id")
        if not uid:
            errors.append(f"@{inf['username']}: user not found (check the handle)")
            continue
        if uid in seen_users:
            print(f"WARNING: duplicate account @{inf['username']} skipped")
            continue
        seen_users.add(uid)
        try:
            raw = x.posts_between(uid, start, end, include_replies)
        except Exception as e:  # one bad account must not stop the others
            errors.append(f"@{inf['username']}: {e}")
            continue
        for item in raw:
            created = parse_x_time(item["created_at"])
            if not is_in_window(created, start, end):
                continue  # older/newer post -> skip
            text = (item.get("note_tweet") or {}).get("text") or item.get("text", "")
            text = html.unescape(text)  # X returns &amp; &lt; &gt; already encoded
            posts.append(Post(inf["name"], inf["username"], item["id"], text, created))

    posts.sort(key=lambda p: p.created_at)
    return posts, errors


def main() -> int:
    for var in ("X_BEARER_TOKEN", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        if not os.environ.get(var):
            print(f"ERROR: missing environment variable {var}")
            return 2

    tz = ZoneInfo(os.environ.get("DIGEST_TZ", "Asia/Yerevan"))
    grace = int(os.environ.get("LATE_RUN_GRACE_HOURS", "3"))
    include_replies = os.environ.get("INCLUDE_REPLIES", "").lower() == "true"
    dry_run = os.environ.get("DRY_RUN", "").lower() == "true"
    bot, chat = os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]

    influencers = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))["influencers"]
    now = datetime.now(timezone.utc)
    day = target_day(now, tz, grace)
    start, end = day_window_utc(day, tz, now)
    print(f"INFO: digest day {day} ({tz.key}); window {to_x_time(start)} -> {to_x_time(end)}")

    posts, errors = collect_posts(XClient(os.environ["X_BEARER_TOKEN"]),
                                  influencers, start, end, include_replies)
    print(f"INFO: {len(posts)} post(s) found, {len(errors)} error(s)")
    for e in errors:
        print(f"ERROR: {e}")

    # every account failed (e.g. bad token): do NOT send a false "no posts"
    if errors and len(errors) == len(influencers):
        print("ERROR: all accounts failed; nothing sent")
        return 1

    messages = [format_post(p, tz) for p in posts] or [
        f"No new posts today ({day.strftime('%d %b %Y')})."
    ]
    for msg in messages:
        if dry_run:
            print("---- DRY RUN message ----\n" + msg)
        else:
            send_telegram(bot, chat, msg)
            time.sleep(1.1)  # stay under Telegram channel limits

    return 0


if __name__ == "__main__":
    sys.exit(main())
