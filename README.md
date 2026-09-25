# Daily AI-in-QA Influencer Digest

A scheduled job that collects **today's X posts** from 8 AI-in-QA thought leaders
(Session 1, slide 21) and sends them to a **Telegram channel** through a bot.

| Requirement | How it is met |
|---|---|
| Runs once a day, no manual trigger | GitHub Actions `schedule` cron, 23:23 Yerevan time |
| Only today's posts | X API `start_time`/`end_time` = today 00:00 to now (Yerevan), plus a second check in code |
| Author, text, link | One Telegram message per post: name, @handle, time, excerpt (max 700 chars), link |
| Empty days | One message: "No new posts today (date)." |

## Files

```
digest.py                          main script
influencers.json                   the 8 people and their X handles
tests/test_digest.py               19 unit tests (no network)
.github/workflows/daily-digest.yml daily schedule
requirements.txt
```

## Setup (about 20 minutes)

### 1. Telegram bot and channel
1. In Telegram, open **@BotFather** → `/newbot` → choose a name → copy the **bot token**.
2. Create a **public channel** whose name contains your name and surname
   (e.g. "Ani Rayisyan – AI in QA Digest", link `@ani_rayisyan_qa_digest`).
3. Channel → Administrators → Add Admin → your bot → allow **Post Messages**.

### 2. X API Bearer token
1. Go to https://developer.x.com and sign in.
2. Create a Project and an App. Open **Keys and tokens** → generate the **Bearer Token**.
3. Add credits in billing (e.g. $5). Reading costs about $0.005 per post and $0.01 per user lookup
   (check the current prices: https://docs.x.com/x-api/getting-started/pricing).

### 3. GitHub repository
1. Create a new repository (e.g. `ai-qa-influencer-digest`) and upload all these files,
   keeping the folder structure (`.github/workflows/...` too).
2. Settings → Secrets and variables → Actions → **New repository secret**, add three:

| Secret name | Value |
|---|---|
| `X_BEARER_TOKEN` | Bearer token from step 2 |
| `TELEGRAM_BOT_TOKEN` | bot token from step 1 |
| `TELEGRAM_CHAT_ID` | channel username, e.g. `@ani_rayisyan_qa_digest` |

### 4. Test it
1. Actions tab → **Daily AI-in-QA Influencer Digest** → **Run workflow**.
2. First run with **Dry run = checked**: messages are printed in the log only. Check the log:
   - `INFO: digest day ...` shows the correct date
   - no `ERROR: @... user not found` (if there is one, fix that handle)
   - lines `INFO: save to influencers.json -> ...: id "..."`
3. Copy those IDs into the `id` fields of `influencers.json` and commit.
   This skips the paid user lookup on every run (saves about $0.08/day).
4. Run again with **Dry run unchecked** → the posts appear in your channel.

After that the job runs by itself every day.

## Test cases covered (tests/test_digest.py)

- Correct day for a normal run, a late run after midnight, and a morning run
- Day window: starts at 00:00 Yerevan, never goes into the future
- A post from yesterday is skipped even if the API returns it
- Wrong handle: reported, other accounts still processed
- Duplicate account: fetched only once
- API error for one account: others still processed
- Long posts: full text is used
- Posts sent oldest first
- Message has author, @handle, time, link; HTML is escaped; long text is cut below Telegram's 4096 limit
- Empty day: exactly one "No new posts today" message
- All accounts fail (e.g. bad token): nothing sent, run marked as failed (GitHub emails you)
- Missing secret: run fails with a clear error

Run locally: `pip install -r requirements.txt && python -m pytest -q`

## Known limits

- **GitHub cron is not exact.** Runs can start late, and GitHub may skip a run when it is busy.
  A run that starts after midnight still reports the previous day (3-hour grace window).
- **Scheduled workflows stop after 60 days without repository activity** (GitHub rule for public repos).
  Make any commit to keep it active.
- **Manual re-runs send the same posts again.** There is no "already sent" memory.
- **Posts from 23:23 to 24:00 Yerevan time are not included.** To change the time, edit the `cron`
  line (it is in UTC: Yerevan = UTC+4).
- Retweets and replies are excluded. Set `INCLUDE_REPLIES: "true"` in the workflow to include replies.
- Joe Colantonio's handle on the slide was wrong (`@jarbon`). It is set to `@joecolantonio`; confirm it.
