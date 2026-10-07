# Email Assistant Agent

A local email assistant for an `@illinois.edu` Outlook/Microsoft 365 account. Use Telegram, WhatsApp, or both for conversational Codex requests and new-mail summaries. It checks for new mail every five minutes. First sync silently establishes a baseline; later syncs follow Microsoft Graph delta pagination and track message IDs across restarts. Existing mail is never sent, modified, or marked read. The assistant can create unsent new-message and reply drafts when explicitly asked. Filters inspect basic sender/subject metadata only.

## Local setup

Create an empty private `.env` file with `touch .env`, then add the settings you need from `.env.example`, including your full `MAIL_EMAIL` and Microsoft Entra application `MS_CLIENT_ID`; add `MS_TENANT_ID` if needed. Find the client ID on your app's Overview page in the [Microsoft Entra admin center](https://entra.microsoft.com) under **App registrations**. Environment variables override `.env`, which overrides optional legacy `config.json` values. `.env` is ignored by Git.

Use an approved public desktop/mobile Entra application with redirect URI `http://localhost` and delegated Graph permissions `User.Read` and `Mail.ReadWrite`. This permission is needed for draft creation; the assistant has no email-sending or forwarding feature. If app registration or consent is blocked, ask university IT for an approved application.

```sh
cd email-assistant-agent
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

Complete university sign-in and MFA in your browser. OAuth tokens are stored in the OS keyring (macOS Keychain), separate from `.env`. No mailbox password or client secret is needed. Renew sign-in with `.venv/bin/assistant --login` when requested.

The assistant runs on your computer until Ctrl+C. It pauses when the process exits or your computer sleeps. It is not installed as a background service. Initial setup requires browser sign-in from a terminal with access to macOS Keychain and the internet.

## Notifications and filters

`POLL_SECONDS` defaults to 300 and must be at least 5.

`MAIL_SENDERS` and `MAIL_KEYWORDS` accept comma-separated filters. Sender addresses match exactly; keywords match subjects case-insensitively. Entries within each filter are ORed; the two filters are ANDed. Empty filters match all. Changing filters does not replay historical messages.

State is kept in `.state/<email>/assistant.sqlite3`, or under `STATE_DIR` if set. Keep it between runs to preserve duplicate tracking. The assistant stores validated style preferences in the adjacent `AGENTS.md`; it updates this file only with supported style settings, not safety rules. It saves direct preferences and corrections, and can learn from three consistent signals in chat. It never learns preferences from email content. A crash between displaying and recording a message can repeat one alert. Protect private state and logs.

`--env-file PATH` selects another environment file. After an expired Graph cursor, restart with `.venv/bin/assistant --reset` to establish a fresh silent baseline; messages received during the gap will not generate alerts.

## Verification

```sh
.venv/bin/python -m unittest discover -s tests -v
```

References: [Microsoft browser sign-in](https://learn.microsoft.com/en-us/entra/msal/python/getting-started/acquiring-tokens), [message delta](https://learn.microsoft.com/en-us/graph/api/message-delta).

## Chat setup

The assistant runs locally on your Mac. Configure Telegram, WhatsApp, or both for chat and new-mail alerts. Your Mac must remain awake and the process must remain running. Email information requested in chat is transmitted to the configured private chat channel.

1. To use Telegram, create a bot with [@BotFather](https://t.me/BotFather), set `TELEGRAM_BOT_TOKEN` in `.env`, stop any running assistant process, send `/start` to the bot privately, and run:

```sh
.venv/bin/assistant --setup
```

Verify the displayed private chat ID is yours and put it in `.env` as `TELEGRAM_CHAT_ID`. Group chats are rejected.

2. To use WhatsApp, create a Meta app with the WhatsApp Business Platform Cloud API and set these values in `.env`: `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN`, and `WHATSAPP_ALLOWED_SENDER`. Use the sender's international phone number with country code. Configure the Meta webhook callback as your public HTTPS URL ending in `/webhook`, with the same verify token, and subscribe to the `messages` field. The assistant listens on `127.0.0.1:8766`; expose that local port through an HTTPS tunnel or reverse proxy and set `WHATSAPP_WEBHOOK_PORT` if needed. Only the configured sender is accepted. The webhook verifies Meta's `X-Hub-Signature-256` before parsing messages.

Configure either channel, or both. When both are enabled they share one Codex conversation and receive new-mail alerts. WhatsApp replies use the Cloud API and require a valid business account, access token, and a recent user message under Meta's customer-service messaging window. The assistant does not send template messages or initiate WhatsApp conversations.

3. If your Entra app currently has `Mail.Read`, replace it with **delegated** Graph `Mail.ReadWrite` alongside `User.Read`. Microsoft requires `Mail.ReadWrite` for draft operations. Complete any required university consent and sign in again:

```sh
.venv/bin/assistant --login
```

Chat naturally: “Find emails from my professor,” “Summarize the second one,” or “Suggest a reply asking for an extension.” To save one, explicitly ask to create a draft. You can also ask for a new draft with recipients, subject, and body. If a request is ambiguous or lacks details needed to act, Codex asks a concise question rather than guessing. Every incoming chat message goes to Codex CLI, including `/help` and older slash commands. Only `/clear` is handled locally to start a new conversation. Drafts remain unsent in Outlook; the assistant never sends or forwards email.

For each newly detected message that passes the filters, Codex reads that email and sends a concise summary with the sender and subject to the configured chat channels. Event summaries include stated date, time, location, and RSVP or meeting links when present. In all summaries, relative dates use the date of the text where they appear; the assistant uses sent or received timestamps for the email and the original dated header for quoted or forwarded text. If it cannot determine a date, it says so instead of guessing. It copies links from the email without opening them. If the email asks for a reply or clearly needs one, the summary asks whether you want a reply draft. Codex creates the draft only after you say yes. Other summaries do not include this offer. The first sync establishes a silent baseline; existing mail is not summarized retroactively. The assistant preserves mailbox tracking across restarts. Failed chat delivery or summary generation is logged; a crash or partial send can produce duplicate notifications. Polling and command offsets persist in local SQLite state. Run one process only; the account lock prevents multiple assistant processes running together. Channel API errors are redacted to avoid exposing credentials. If the Telegram bot already has a webhook or another polling process, remove/stop that configuration before using this local polling interface.

References: [Telegram bot creation](https://core.telegram.org/bots/features#botfather), [Telegram Bot API](https://core.telegram.org/bots/api), [Meta WhatsApp webhook verification](https://whatsapp.github.io/WhatsApp-Nodejs-SDK/api-reference/webhooks/start/), [Meta WhatsApp send message](https://www.postman.com/meta/whatsapp-business-platform/request/8gvd47s/send-text-message), [Microsoft message access](https://learn.microsoft.com/en-us/graph/api/user-list-messages).

## Conversational Codex connection

Run `codex login` in your terminal if the CLI is not signed in, install the dependencies above, then start `.venv/bin/assistant`. It starts one Codex CLI app-server and keeps it running alongside the configured chat channels. Each message becomes a new turn in the same conversation; Codex controls its own reasoning and tool calls. The conversation ID is saved in local SQLite state so it can resume after an assistant restart. `/clear` starts a fresh Codex conversation.

The Codex session retains its retrieved email context. Codex also stores conversation history locally; `/clear` starts a fresh thread but does not delete earlier history. Email content needed to answer your request is processed by Codex/OpenAI, and answers are transmitted through the configured chat channel.

The local mailbox MCP server provides three read tools and two explicit draft tools: `list_messages` (inbox or mailbox search, date filters, and pagination), `read_message` (body retrieval by ID), `mailbox_status`, `create_draft`, and `create_reply_draft`. Draft tools are used only when you ask to save/create a draft. Pages contain up to 30 messages; list bodies are limited to 4,000 characters, and individual reads return 16,000-character chunks with an offset for retrieving the rest. Codex chooses the queries and follows pagination as needed, using the supplied Mac local date/time for requests about today.

The bridge strips Telegram, WhatsApp, and Microsoft environment variables from the Codex child environment. The mailbox server independently uses the Microsoft token cache in the OS keyring and verifies the configured account. Codex runs in a temporary working directory with read-only sandboxing and shell tools disabled. The app-server uses your installed Codex login and profile. Email content is treated as untrusted data. Model turns time out after 300 seconds and block this single worker, so mail checks and other messages wait until the turn finishes.

This uses a separate local Codex CLI conversation from the Codex app conversation. Access and usage limits depend on your Codex authentication. See [Codex non-interactive mode](https://developers.openai.com/codex/noninteractive) and [official OpenAI MCP documentation](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).

This assistant must never send or forward email to anyone. Do not add `Mail.Send` permission or email-delivery tools. Reply suggestions remain text previews unless you explicitly ask to save one as a draft.

The assistant explicitly uses `gpt-6-luna` with `low` reasoning effort for every Codex invocation. It applies the project's Simplified Technical English skill to Telegram and WhatsApp replies and automatic email summaries. Restart the running process after changing this setting.

## Project layout

The application uses a `src/` package layout. `assistant.py` owns application startup, shared conversation routing, and alerts, `codex_agent.py` manages the persistent Codex app-server, `mailbox_tools.py` exposes read and draft MCP tools, `mailbox.py` handles Microsoft Graph access and synchronization, `telegram.py` and `whatsapp.py` implement chat transports, and `config.py` loads local settings. Run it with the `assistant` command or `python -m email_assistant` from the project directory.
