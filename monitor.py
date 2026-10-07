"""Read-only Illinois inbox monitor. Python 3.10+."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from urllib.parse import urlparse

GRAPH = 'https://graph.microsoft.com/v1.0'
INITIAL = GRAPH + '/me/mailFolders/inbox/messages/delta?$select=id,subject,from,receivedDateTime,webLink'
SCOPES = ['User.Read', 'Mail.ReadBasic']


class LoginRequired(RuntimeError):
    pass


def load_config(config_path, env_path):
    from dotenv import load_dotenv
    load_dotenv(env_path, override=False)
    config = json.loads(config_path.read_text()) if config_path.exists() else {}
    fields = {'MAIL_EMAIL': 'email', 'MS_CLIENT_ID': 'client_id',
              'MS_TENANT_ID': 'tenant_id', 'STATE_DIR': 'state_dir',
              'TELEGRAM_BOT_TOKEN': 'telegram_bot_token', 'TELEGRAM_CHAT_ID': 'telegram_chat_id'}
    for env, field in fields.items():
        if env in os.environ:
            config[field] = os.environ[env]
    if 'POLL_SECONDS' in os.environ:
        config['poll_seconds'] = int(os.environ['POLL_SECONDS'])
    if 'DESKTOP_NOTIFICATIONS' in os.environ:
        config['desktop_notifications'] = os.environ['DESKTOP_NOTIFICATIONS'].lower() == 'true'
    for env, field in [('MAIL_SENDERS', 'senders'), ('MAIL_KEYWORDS', 'keywords')]:
        if env in os.environ:
            config[field] = [s.strip() for s in os.environ[env].split(',') if s.strip()]
    return config


class Auth:
    def __init__(self, config, scopes=None):
        import keyring
        import msal
        self.keyring = keyring
        self.scopes = scopes or SCOPES
        self.key = config['client_id'] + ':' + config['email'].lower()
        self.cache = msal.SerializableTokenCache()
        saved = keyring.get_password('illinois-mail-agent', self.key)
        if saved:
            self.cache.deserialize(saved)
        self.email = config['email'].lower()
        self.app = msal.PublicClientApplication(config['client_id'],
            authority='https://login.microsoftonline.com/' + config.get('tenant_id', 'organizations'),
            token_cache=self.cache)

    def token(self, interactive=False):
        accounts = self.app.get_accounts(username=self.email)
        result = self.app.acquire_token_silent(self.scopes, account=accounts[0]) if accounts else None
        if not result and interactive:
            result = self.app.acquire_token_interactive(scopes=self.scopes, login_hint=self.email)
        if self.cache.has_state_changed:
            self.keyring.set_password('illinois-mail-agent', self.key, self.cache.serialize())
        if not result or 'access_token' not in result:
            raise LoginRequired('Sign-in required. Run: python monitor.py --login --once')
        return result['access_token']


class Graph:
    def __init__(self, auth):
        import requests
        self.session = requests.Session()
        self.auth = auth

    def get(self, url):
        parsed = urlparse(url)
        if parsed.scheme != 'https' or parsed.netloc != 'graph.microsoft.com':
            raise ValueError('Unexpected Graph URL')
        response = self.session.get(url, headers={
            'Authorization': 'Bearer ' + self.auth.token(),
            'Prefer': 'IdType="ImmutableId", outlook.body-content-type="text"'}, timeout=30, allow_redirects=False)
        if response.status_code == 401:
            raise LoginRequired('Microsoft requires renewed sign-in. Run with --login --once.')
        if response.status_code in (429, 503):
            raise RuntimeError('Microsoft temporarily unavailable; retry next polling cycle.')
        if response.status_code == 410:
            raise RuntimeError('Delta cursor expired. Stop and run with --reset --once to establish a new baseline.')
        if not response.ok:
            raise RuntimeError(f'Graph returned HTTP {response.status_code}. Check app consent and mailbox access.')
        return response.json()


def matches(message, config):
    sender = message.get('from', {}).get('emailAddress', {}).get('address', '').lower()
    senders = [s.lower() for s in config.get('senders', [])]
    keywords = [s.lower() for s in config.get('keywords', [])]
    return ((not senders or sender in senders) and
            (not keywords or any(k in message.get('subject', '').lower() for k in keywords)))


def notify(message, desktop):
    sender = message.get('from', {}).get('emailAddress', {}).get('address', 'Unknown sender')
    subject = message.get('subject') or '(no subject)'
    payload = {'id': message.get('id'), 'received': message.get('receivedDateTime'),
               'sender': sender, 'subject': subject, 'url': message.get('webLink')}
    print(json.dumps(payload, ensure_ascii=False), flush=True)
    if desktop and sys.platform == 'darwin':
        # Pass email text as argv; never interpret it as AppleScript or shell code.
        script = 'on run argv\ndisplay notification (item 1 of argv) with title "Illinois Mail" subtitle (item 2 of argv)\nend run'
        result = subprocess.run(['osascript', '-e', script, subject[:200], sender[:120]],
                                capture_output=True, timeout=10)
        if result.returncode:
            print('Desktop notification failed; message is available in console output.', file=sys.stderr)


def sync(db, graph, config, emit=notify):
    cursor = db.execute("SELECT value FROM state WHERE key='cursor'").fetchone()
    baseline = cursor is None
    url = cursor[0] if cursor else INITIAL
    while True:
        page = graph.get(url)
        for message in page.get('value', []):
            if '@removed' in message:
                continue
            message_id = message['id']
            if db.execute('SELECT 1 FROM seen WHERE id=?', (message_id,)).fetchone():
                continue
            if not baseline and matches(message, config):
                emit(message, config.get('desktop_notifications', True))
            db.execute('INSERT OR IGNORE INTO seen VALUES (?)', (message_id,))
            db.commit()
        if '@odata.nextLink' in page:
            url = page['@odata.nextLink']
        else:
            db.execute("INSERT OR REPLACE INTO state VALUES ('cursor', ?)", (page['@odata.deltaLink'],))
            db.commit()
            break
    if baseline:
        print('Inbox baseline saved. Monitoring subsequent new messages.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='config.json', help='Optional legacy JSON config')
    parser.add_argument('--env-file', default='.env')
    parser.add_argument('--login', action='store_true', help='Allow browser sign-in')
    parser.add_argument('--once', action='store_true', help='Sync once and exit')
    parser.add_argument('--reset', action='store_true', help='Rebuild baseline while preserving duplicate tracking')
    args = parser.parse_args()
    config_path = Path(args.config).resolve()
    config = load_config(config_path, Path(args.env_file).resolve())
    if not config.get('email', '').lower().endswith('@illinois.edu'):
        raise ValueError('Configure your full @illinois.edu address.')
    if not config.get('client_id') or config['client_id'].startswith('YOUR_'):
        raise ValueError('Configure the application client ID from Microsoft Entra.')
    interval = int(config.get('poll_seconds', 300))
    if interval < 5:
        raise ValueError('poll_seconds must be at least 5.')
    os.umask(0o077)
    auth = Auth(config)
    auth.token(interactive=args.login)
    graph = Graph(auth)
    profile = graph.get(GRAPH + '/me?$select=mail,userPrincipalName')
    identities = [str(profile.get(k) or '').lower() for k in ('mail', 'userPrincipalName')]
    if config['email'].lower() not in identities:
        raise ValueError('Signed-in account does not match configured email.')
    state_dir = Path(config.get('state_dir', str(config_path.parent / '.state'))) / config['email'].lower()
    state_dir.mkdir(parents=True, exist_ok=True)
    import fcntl
    lock = (state_dir / 'monitor.lock').open('w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError('Another monitor is already running for this account.')
    with sqlite3.connect(state_dir / 'monitor.sqlite3') as db:
        db.execute('CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY)')
        db.execute('CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT)')
        if args.reset:
            db.execute("DELETE FROM state WHERE key='cursor'")
            db.commit()
        while True:
            try:
                sync(db, graph, config)
            except LoginRequired:
                raise
            except Exception as exc:
                if args.once:
                    raise
                print(f'Sync failed ({type(exc).__name__}); retrying in {interval}s. Run --once for diagnostics.', file=sys.stderr)
            if args.once:
                break
            time.sleep(interval)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
