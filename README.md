# Illinois mail monitoring agent

A local, read-only Outlook/Microsoft 365 inbox monitor for an `@illinois.edu` account. Checks every five minutes, prints new mail as JSON, and shows macOS desktop notifications. First sync silently establishes a baseline; later syncs follow Microsoft Graph delta pagination and track message IDs across restarts. Messages are never sent, modified, or marked read. Filters inspect basic sender/subject metadata only.

## Local setup

Copy `.env.example` to `.env` (`cp .env.example .env`), then fill in the private `.env` file with your full `MAIL_EMAIL`, Microsoft Entra application `MS_CLIENT_ID`, and optionally `MS_TENANT_ID`. Environment variables override `.env`, which overrides optional legacy `config.json` values. `.env` is ignored by Git.

Use an approved public desktop/mobile Entra application with redirect URI `http://localhost` and delegated Graph permissions `User.Read` and `Mail.ReadBasic`. If app registration or consent is blocked, ask university IT for an approved application.

```sh
cd email-assistant-agent
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python monitor.py --login --once
.venv/bin/python monitor.py
```

Complete university sign-in and MFA in your browser. OAuth tokens are stored in the OS keyring (macOS Keychain), separate from `.env`. No mailbox password or client secret is needed. Renew sign-in with `--login --once` when requested.

The monitor runs on your computer until Ctrl+C. It pauses when the process exits or your computer sleeps. It is not installed as a background service. Initial setup requires browser sign-in from a terminal with access to macOS Keychain and the internet.

## Notifications and filters

`DESKTOP_NOTIFICATIONS=true` enables local macOS notifications. Console output includes message ID, received time, sender, subject, and link. `POLL_SECONDS` defaults to 300 and must be at least 5.

`MAIL_SENDERS` and `MAIL_KEYWORDS` accept comma-separated filters. Sender addresses match exactly; keywords match subjects case-insensitively. Entries within each filter are ORed; the two filters are ANDed. Empty filters match all. Changing filters does not replay historical messages.

State is kept in `.state/<email>/monitor.sqlite3`, or under `STATE_DIR` if set. Keep it between runs to preserve duplicate tracking. A crash between displaying and recording a message can repeat one alert. Protect private state and logs.

`--once` syncs once with diagnostics. `--env-file PATH` selects another environment file. After an expired Graph cursor, `--reset --once` establishes a fresh silent baseline; messages received during the gap will not generate alerts.

## Verification

```sh
python3 -m unittest discover -s tests -v
```

References: [Microsoft browser sign-in](https://learn.microsoft.com/en-us/entra/msal/python/getting-started/acquiring-tokens), [message delta](https://learn.microsoft.com/en-us/graph/api/message-delta).

## Telegram email assistant

The assistant runs locally on your Mac and uses Telegram for commands and new-mail alerts. Your Mac must remain awake and the process must remain running. Email information requested in Telegram is transmitted to your configured Telegram private chat.

1. Open the official [@BotFather](https://t.me/BotFather) in Telegram, send `/newbot`, and follow its instructions. Put the token it gives you in `.env` as `TELEGRAM_BOT_TOKEN`. Do not share it in chat or commit it.
2. Open your new bot's private chat and send `/start`. Stop the existing `monitor.py` process with Ctrl+C, then discover your chat ID:

```sh
.venv/bin/python telegram_assistant.py --setup
```

Verify the printed username/ID corresponds to your own private chat, and put that numeric ID in `.env` as `TELEGRAM_CHAT_ID`. The setup command does not read mail or send email information. Only this private chat's user can run commands; group chats are rejected.

3. In your Microsoft Entra app, add **delegated** Graph `Mail.Read` permission alongside `User.Read`. The assistant needs this to read bodies. Complete any required university consent and sign in again:

```sh
.venv/bin/python telegram_assistant.py --login
```

Use `/help`, `/inbox`, `/search words`, `/read 1`, and `/status` in Telegram. `/read N` refers to the latest inbox/search result list; run the list command again after restarting. Search returns up to ten mailbox matches. Read displays up to 12,000 body characters. No email is sent or modified; AI summaries/replies are not implemented yet.

The assistant shares the monitor's baseline and message tracking. Existing mail is not replayed as alerts, including mail already processed by `monitor.py`. Failed Telegram alert delivery is retried; a crash or partial send can produce duplicate notifications. Polling and command offsets persist in the local SQLite state. Run one process only; the shared lock prevents the monitor and Telegram assistant running together. Telegram API errors are redacted to avoid exposing the bot token. If the bot already has a webhook or another polling process, remove/stop that configuration before using this local polling interface.

References: [Telegram bot creation](https://core.telegram.org/bots/features#botfather), [Telegram Bot API](https://core.telegram.org/bots/api), [Microsoft message access](https://learn.microsoft.com/en-us/graph/api/user-list-messages).

## Conversational Codex connection

Ordinary Telegram messages now use the installed Codex CLI. For example: “Find emails from my professor,” “Summarize the second one,” or “Draft a polite reply asking for an extension.” Codex chooses bounded inbox/search/read operations; the Python application performs the actual Microsoft requests. Replies and drafts are returned in Telegram. Sending, archiving, deleting, or Outlook draft creation are not supported by this version.

Run `codex login` in your terminal if the CLI is not signed in, then restart `telegram_assistant.py`. Existing slash commands remain available. `/clear` clears the in-memory conversational context. Context remembers the last three exchanges and disappears when the process restarts. Requests are limited to six read/search steps and ten results per list. Email bodies are truncated to 16,000 characters per retrieval, and model requests time out after 120 seconds. Model requests currently block this single worker, so mail checks and other commands wait until they finish.

Email content needed to answer your request is passed to Codex/OpenAI for processing, and the answer is sent through Telegram. The bridge does not pass the Telegram bot token or Microsoft credentials to the Codex child environment. Codex runs ephemerally in a temporary directory with read-only sandboxing, shell tools disabled, and user configuration excluded. The application accepts only structured inbox/search/read/answer actions and exposes no mailbox write operations. Email content is treated as untrusted data in the model instructions.

This uses a separate local Codex CLI invocation, not this existing Codex app conversation. Access and usage limits depend on your Codex authentication. See [Codex non-interactive mode](https://developers.openai.com/codex/noninteractive).

Daily triage: “What important emails from today need a reply?” can retrieve up to 30 of today's inbox messages with their bodies in one step, using your Mac's local timezone. It discloses when additional messages exist or text is truncated. List reviews can read ten bodies per step. After the tool budget is exhausted, the model gets a final answer-only turn to report what it found and remaining gaps.

User constraint: this assistant must never send or forward email to anyone. Mail.Send permission and email-delivery tools must not be added. Reply suggestions remain text for the user to send manually.

The Telegram assistant explicitly uses `gpt-6.1-sol` with `medium` reasoning effort for every Codex invocation. Restart the running process after changing this setting.
