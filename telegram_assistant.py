"""Local Telegram interface for the Illinois inbox."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from urllib.parse import quote, urlencode

from monitor import Auth, GRAPH, Graph, LoginRequired, load_config, sync

HELP = ('Illinois Email Assistant\n/inbox — latest 10 messages\n/search words — search your mailbox\n'
        '/read N — read a message from the latest list\n/status — connection status\n/help — commands\n'
        'New-email alerts arrive automatically. Email bodies require Mail.Read consent. '
        'You can also chat naturally to search, summarize, or draft a reply. '
        'Reply drafts are shown as text; no email is sent.')


class Telegram:
    def __init__(self, token, chat_id=None):
        import requests
        if not token or ':' not in token:
            raise ValueError('Set TELEGRAM_BOT_TOKEN in .env using the token from BotFather.')
        self.base = 'https://api.telegram.org/bot' + token + '/'
        self.chat_id = str(chat_id or '')
        if self.chat_id and (not self.chat_id.isdigit() or int(self.chat_id) <= 0):
            raise ValueError('TELEGRAM_CHAT_ID must be your positive private-chat ID.')
        self.session = requests.Session()

    def call(self, method, payload):
        try:
            response = self.session.post(self.base + method, json=payload, timeout=20, allow_redirects=False)
            data = response.json()
        except Exception:
            # Requests errors include the URL and therefore the bot token; never print them.
            raise RuntimeError('Telegram request failed. Check connectivity.') from None
        if not response.ok or not data.get('ok'):
            raise RuntimeError(f'Telegram {method} failed (HTTP {response.status_code}). Check bot token/chat and any existing webhook.')
        return data['result']

    def updates(self, offset=None):
        payload = {'timeout': 1, 'allowed_updates': ['message']}
        if offset is not None:
            payload['offset'] = offset
        return self.call('getUpdates', payload)

    def send(self, text):
        if not self.chat_id:
            raise ValueError('Configure TELEGRAM_CHAT_ID before sending email information.')
        for start in range(0, len(text), 2000):
            self.call('sendMessage', {'chat_id': self.chat_id, 'text': text[start:start + 2000],
                'link_preview_options': {'is_disabled': True}})

    def authorized(self, message):
        chat = message.get('chat', {})
        return (chat.get('type') == 'private' and str(chat.get('id')) == self.chat_id
                and str(message.get('from', {}).get('id')) == self.chat_id
                and not message.get('from', {}).get('is_bot', False))


class Assistant:
    def __init__(self, graph, telegram):
        self.graph = graph
        self.telegram = telegram
        self.results = []
        self.conversation = None

    def handle(self, message):
        if not self.telegram.authorized(message):
            return
        text = message.get('text', '').strip()
        command, _, argument = text.partition(' ')
        command = command.split('@')[0].lower()
        if command in ('/start', '/help'):
            self.telegram.send(HELP)
        elif command == '/status':
            self.graph.get(GRAPH + '/me?$select=id')
            self.telegram.send('Connected to Microsoft. Local monitoring is running.')
        elif command in ('/inbox', '/search'):
            if command == '/search' and not argument.strip():
                self.telegram.send('Usage: /search words')
                return
            query = {'$top': '10', '$select': 'id,subject,from,receivedDateTime,webLink'}
            path = '/me/mailFolders/inbox/messages'
            if command == '/search':
                path = '/me/messages'
                query['$search'] = json.dumps(argument.strip(), ensure_ascii=False)
            else:
                query['$orderby'] = 'receivedDateTime desc'
            results = self.graph.get(GRAPH + path + '?' + urlencode(query)).get('value', [])
            self.results = results
            lines = []
            for i, item in enumerate(results, 1):
                sender = item.get('from', {}).get('emailAddress', {}).get('address', 'Unknown')
                lines.append(f"{i}. {item.get('subject', '(no subject)')}\nFrom: {sender}\n{item.get('receivedDateTime', '')}")
            self.telegram.send('\n\n'.join(lines) + '\n\nUse /read N to open a message.' if lines else 'No messages found.')
        elif command == '/read':
            if not argument.strip().isdigit() or not 1 <= int(argument) <= len(self.results):
                self.telegram.send('Run /inbox or /search first, then /read followed by a listed number.')
                return
            item = self.results[int(argument) - 1]
            full = self.graph.get(GRAPH + '/me/messages/' + quote(item['id'], safe='') +
                                  '?$select=subject,from,body,webLink')
            body = full.get('body', {}).get('content', '(no body)')
            self.telegram.send(f"{full.get('subject', '(no subject)')}\n\n{body[:12000]}\n\n{full.get('webLink', '')}")
        elif text and not text.startswith('/'):
            from codex_chat import Conversation
            self.telegram.send('I’m checking that now…')
            if self.conversation is None:
                self.conversation = Conversation(self)
            self.telegram.send(self.conversation.respond(text))
        elif command == '/clear':
            self.conversation = None
            self.results = []
            self.telegram.send('Conversation cleared.')
        else:
            self.telegram.send('Use /help to see available commands.')

    def alert(self, message, desktop=False):
        sender = message.get('from', {}).get('emailAddress', {}).get('address', 'Unknown')
        self.telegram.send(f"New email\nFrom: {sender}\nSubject: {message.get('subject', '(no subject)')}\n{message.get('webLink', '')}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', default='.env')
    parser.add_argument('--login', action='store_true')
    parser.add_argument('--setup', action='store_true', help='Show private chat IDs without connecting to mail')
    args = parser.parse_args()
    config = load_config(Path('config.json').resolve(), Path(args.env_file).resolve())
    telegram = Telegram(config.get('telegram_bot_token'), config.get('telegram_chat_id'))
    if args.setup:
        print('Send /start to your bot in a private Telegram chat, then run this command again.')
        found = set()
        for update in telegram.updates():
            chat = update.get('message', {}).get('chat', {})
            if chat.get('type') == 'private' and chat['id'] not in found:
                found.add(chat['id'])
                print('Private chat candidate:', chat['id'], 'Username:', chat.get('username', '(none)'))
        print('Verify your own chat before setting TELEGRAM_CHAT_ID. No email data has been sent.')
        return
    if not telegram.chat_id:
        raise ValueError('Set TELEGRAM_CHAT_ID; run --setup to discover your private chat ID.')
    if not config.get('email', '').lower().endswith('@illinois.edu') or not config.get('client_id'):
        raise ValueError('Configure MAIL_EMAIL and MS_CLIENT_ID.')
    interval = int(config.get('poll_seconds', 300))
    if interval < 5:
        raise ValueError('POLL_SECONDS must be at least 5.')
    os.umask(0o077)
    directory = Path(config.get('state_dir', '.state')) / config['email'].lower()
    directory.mkdir(parents=True, exist_ok=True)
    lock = (directory / 'monitor.lock').open('w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError('Stop monitor.py before starting the Telegram assistant.')
    auth = Auth(config, scopes=['User.Read', 'Mail.Read'])
    auth.token(interactive=args.login)
    graph = Graph(auth)
    profile = graph.get(GRAPH + '/me?$select=mail,userPrincipalName')
    if config['email'].lower() not in [str(profile.get(k) or '').lower() for k in ('mail', 'userPrincipalName')]:
        raise ValueError('Microsoft account does not match MAIL_EMAIL.')
    assistant = Assistant(graph, telegram)
    with sqlite3.connect(directory / 'monitor.sqlite3') as db:
        db.execute('CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY)')
        db.execute('CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT)')
        offset_row = db.execute("SELECT value FROM state WHERE key='telegram_offset'").fetchone()
        offset = int(offset_row[0]) if offset_row else None
        next_sync = 0
        print('Telegram email assistant running. Use /help in your configured private chat.', flush=True)
        while True:
            if time.monotonic() >= next_sync:
                try:
                    sync(db, graph, config, assistant.alert)
                except LoginRequired:
                    raise
                except Exception as exc:
                    print(f'Inbox sync failed ({type(exc).__name__}); retrying.', flush=True)
                next_sync = time.monotonic() + interval
            try:
                updates = telegram.updates(offset)
                for update in updates:
                    try:
                        assistant.handle(update.get('message', {}))
                    except LoginRequired:
                        raise
                    except Exception:
                        telegram.send('Command failed. Check Microsoft consent/connectivity and try again.')
                    offset = update['update_id'] + 1
                    db.execute("INSERT OR REPLACE INTO state VALUES ('telegram_offset', ?)", (str(offset),))
                    db.commit()
            except LoginRequired:
                raise
            except Exception as exc:
                print(f'Telegram poll failed ({type(exc).__name__}); retrying.', flush=True)
                time.sleep(5)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
