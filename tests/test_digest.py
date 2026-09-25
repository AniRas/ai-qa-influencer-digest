"""Unit tests for the digest. No network calls: X and Telegram are faked."""

import sys
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import digest  # noqa: E402

YVN = ZoneInfo("Asia/Yerevan")
UTC = timezone.utc


def utc(*a):
    return datetime(*a, tzinfo=UTC)


# ---------------------------------------------------------------- target day

def test_target_day_normal_run_2323_yerevan():
    # 19:23 UTC = 23:23 Yerevan on 25 Sep
    assert digest.target_day(utc(2026, 9, 25, 19, 23), YVN) == date(2026, 9, 25)


def test_target_day_late_run_after_midnight_reports_previous_day():
    # GitHub started 1h late: 00:23 Yerevan on 26 Sep -> still the 25th
    assert digest.target_day(utc(2026, 9, 25, 20, 23), YVN) == date(2026, 9, 25)


def test_target_day_morning_run_reports_same_day():
    # 08:00 Yerevan on 25 Sep -> 25th
    assert digest.target_day(utc(2026, 9, 25, 4, 0), YVN) == date(2026, 9, 25)


# ---------------------------------------------------------------- window

def test_window_starts_at_local_midnight_and_caps_at_now():
    now = utc(2026, 9, 25, 19, 23)
    start, end = digest.day_window_utc(date(2026, 9, 25), YVN, now)
    assert start == utc(2026, 9, 24, 20, 0)   # 00:00 Yerevan
    assert end == now


def test_window_end_is_local_midnight_for_late_run():
    now = utc(2026, 9, 25, 21, 0)             # 01:00 Yerevan next day
    _, end = digest.day_window_utc(date(2026, 9, 25), YVN, now)
    assert end == utc(2026, 9, 25, 20, 0)     # 00:00 Yerevan 26 Sep


def test_is_in_window_boundaries():
    s, e = utc(2026, 9, 24, 20, 0), utc(2026, 9, 25, 20, 0)
    assert digest.is_in_window(s, s, e)                              # start included
    assert not digest.is_in_window(e, s, e)                          # end excluded
    assert not digest.is_in_window(utc(2026, 9, 24, 19, 59), s, e)  # yesterday


# ---------------------------------------------------------------- collect

def fake_x(posts_by_user, users=None):
    x = MagicMock()
    x.lookup_user_ids.return_value = users or {}
    x.posts_between.side_effect = lambda uid, *a: posts_by_user.get(uid, [])
    return x


START, END = utc(2026, 9, 24, 20, 0), utc(2026, 9, 25, 19, 23)


def test_old_posts_are_skipped_even_if_api_returns_them():
    x = fake_x({"1": [
        {"id": "a", "text": "today", "created_at": "2026-09-25T08:00:00.000Z"},
        {"id": "b", "text": "yesterday", "created_at": "2026-09-24T10:00:00.000Z"},
    ]})
    posts, errors = digest.collect_posts(
        x, [{"name": "A", "username": "a", "id": "1"}], START, END, False)
    assert [p.post_id for p in posts] == ["a"]
    assert errors == []


def test_unknown_handle_is_reported_and_others_continue():
    x = fake_x({"1": [{"id": "a", "text": "hi", "created_at": "2026-09-25T08:00:00.000Z"}]},
               users={"good": "1"})
    infl = [{"name": "Good", "username": "good", "id": ""},
            {"name": "Bad", "username": "no_such_user", "id": ""}]
    posts, errors = digest.collect_posts(x, infl, START, END, False)
    assert len(posts) == 1
    assert errors == ["@no_such_user: user not found (check the handle)"]


def test_duplicate_account_is_fetched_once():
    x = fake_x({"1": [{"id": "a", "text": "hi", "created_at": "2026-09-25T08:00:00.000Z"}]})
    infl = [{"name": "A", "username": "a", "id": "1"},
            {"name": "A again", "username": "a", "id": "1"}]
    posts, _ = digest.collect_posts(x, infl, START, END, False)
    assert len(posts) == 1
    assert x.posts_between.call_count == 1


def test_api_error_for_one_user_does_not_stop_others():
    x = MagicMock()
    x.posts_between.side_effect = [RuntimeError("boom"),
                                   [{"id": "b", "text": "ok", "created_at": "2026-09-25T09:00:00.000Z"}]]
    infl = [{"name": "A", "username": "a", "id": "1"}, {"name": "B", "username": "b", "id": "2"}]
    posts, errors = digest.collect_posts(x, infl, START, END, False)
    assert [p.post_id for p in posts] == ["b"]
    assert errors == ["@a: boom"]


def test_long_post_uses_note_tweet_full_text():
    x = fake_x({"1": [{"id": "a", "text": "short…", "note_tweet": {"text": "full long text"},
                       "created_at": "2026-09-25T08:00:00.000Z"}]})
    posts, _ = digest.collect_posts(
        x, [{"name": "A", "username": "a", "id": "1"}], START, END, False)
    assert posts[0].text == "full long text"


def test_posts_sorted_oldest_first():
    x = fake_x({"1": [{"id": "late", "text": "x", "created_at": "2026-09-25T12:00:00.000Z"},
                      {"id": "early", "text": "y", "created_at": "2026-09-25T06:00:00.000Z"}]})
    posts, _ = digest.collect_posts(
        x, [{"name": "A", "username": "a", "id": "1"}], START, END, False)
    assert [p.post_id for p in posts] == ["early", "late"]


# ---------------------------------------------------------------- format

def make_post(text="Hello <world> & QA"):
    return digest.Post("Angie Jones", "techgirl1908", "123", text, utc(2026, 9, 25, 8, 5))


def test_message_has_author_text_and_link():
    msg = digest.format_post(make_post(), YVN)
    assert "Angie Jones" in msg and "@techgirl1908" in msg
    assert "https://x.com/techgirl1908/status/123" in msg
    assert "12:05" in msg                       # 08:05 UTC shown as Yerevan time


def test_message_escapes_html():
    msg = digest.format_post(make_post(), YVN)
    assert "&lt;world&gt; &amp; QA" in msg


def test_message_excerpt_is_truncated():
    msg = digest.format_post(make_post("a" * 5000), YVN)
    assert len(msg) < 4096                      # Telegram message limit
    assert "…" in msg


# ---------------------------------------------------------------- main flow

def run_main(monkeypatch, posts, errors, n_infl=2):
    monkeypatch.setenv("X_BEARER_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "@c")
    monkeypatch.delenv("DRY_RUN", raising=False)
    sent = []
    monkeypatch.setattr(digest, "collect_posts", lambda *a, **k: (posts, errors))
    monkeypatch.setattr(digest, "send_telegram", lambda b, c, m: sent.append(m))
    monkeypatch.setattr(digest.time, "sleep", lambda s: None)
    monkeypatch.setattr(digest.json, "loads",
                        lambda _: {"influencers": [{"username": str(i)} for i in range(n_infl)]})
    return digest.main(), sent


def test_empty_day_sends_one_no_posts_message(monkeypatch):
    code, sent = run_main(monkeypatch, [], [])
    assert code == 0
    assert len(sent) == 1 and sent[0].startswith("No new posts today")


def test_one_message_per_post(monkeypatch):
    code, sent = run_main(monkeypatch, [make_post(), make_post()], [])
    assert code == 0 and len(sent) == 2


def test_all_accounts_failed_sends_nothing_and_fails(monkeypatch):
    code, sent = run_main(monkeypatch, [], ["@0: 401", "@1: 401"])
    assert code == 1 and sent == []


def test_missing_secret_fails(monkeypatch):
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
    assert digest.main() == 2
