"""Shared mailbox access and synchronization for the assistant."""
import re
from urllib.parse import quote, urlparse

GRAPH = 'https://graph.microsoft.com/v1.0'
INITIAL = GRAPH + '/me/mailFolders/inbox/messages/delta?$select=id,subject,from,receivedDateTime,webLink'
SCOPES = ['User.Read', 'Mail.ReadWrite']


class LoginRequired(RuntimeError):
    pass


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
            raise LoginRequired('Sign-in required. Run: .venv/bin/assistant --login')
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
            raise LoginRequired('Microsoft requires renewed sign-in. Run .venv/bin/assistant --login.')
        if response.status_code in (429, 503):
            raise RuntimeError('Microsoft temporarily unavailable; retry next polling cycle.')
        if response.status_code == 410:
            raise RuntimeError('Delta cursor expired. Stop and run .venv/bin/assistant --reset to establish a new baseline.')
        if not response.ok:
            raise RuntimeError(f'Graph returned HTTP {response.status_code}. Check app consent and mailbox access.')
        return response.json()

    def _write(self, method, url, payload=None):
        parsed = urlparse(url)
        if parsed.scheme != 'https' or parsed.netloc != 'graph.microsoft.com':
            raise ValueError('Unexpected Graph URL')
        message_path = r'/v1\.0/me/messages/[^/]+'
        allowed = ((method == 'post' and parsed.path == '/v1.0/me/messages') or
                   (method == 'post' and re.fullmatch(message_path + r'/createReply', parsed.path)) or
                   (method == 'patch' and re.fullmatch(message_path, parsed.path)))
        if not allowed:
            raise ValueError('Only draft creation and draft content updates are allowed.')
        try:
            response = getattr(self.session, method)(url, headers={
                'Authorization': 'Bearer ' + self.auth.token(),
                'Prefer': 'IdType="ImmutableId", outlook.body-content-type="text"'},
                json=payload, timeout=30, allow_redirects=False)
        except Exception:
            raise RuntimeError('Microsoft Graph draft operation failed. Check connectivity.') from None
        if response.status_code == 401:
            raise LoginRequired('Microsoft requires renewed sign-in. Run .venv/bin/assistant --login.')
        if response.status_code in (429, 503):
            raise RuntimeError('Microsoft temporarily unavailable; retry the draft request.')
        if not response.ok:
            raise RuntimeError(f'Graph draft operation returned HTTP {response.status_code}. Check Mail.ReadWrite consent and mailbox access.')
        return response.json()

    def create_message_draft(self, recipients, subject, body):
        payload = {
            'subject': subject,
            'body': {'contentType': 'text', 'content': body},
            'toRecipients': [{'emailAddress': {'address': address}} for address in recipients],
        }
        draft = self._write('post', GRAPH + '/me/messages', payload)
        if not draft.get('id') or draft.get('isDraft') is not True:
            raise RuntimeError('Microsoft did not confirm creation of an unsent draft.')
        return draft

    def create_reply_draft(self, message_id, body):
        reply = self._write('post', GRAPH + '/me/messages/' + quote(message_id, safe='') + '/createReply')
        draft_id = reply.get('id')
        if not draft_id or reply.get('isDraft') is False:
            raise RuntimeError('Microsoft did not return a reply draft.')
        path = GRAPH + '/me/messages/' + quote(draft_id, safe='')
        existing = self.get(path + '?$select=id,isDraft')
        if existing.get('isDraft') is not True:
            raise RuntimeError('Reply draft could not be verified; no content was added.')
        return self._write('patch', path, {'body': {'contentType': 'text', 'content': body}})


def matches(message, config):
    sender = message.get('from', {}).get('emailAddress', {}).get('address', '').lower()
    senders = [s.lower() for s in config.get('senders', [])]
    keywords = [s.lower() for s in config.get('keywords', [])]
    return ((not senders or sender in senders) and
            (not keywords or any(k in message.get('subject', '').lower() for k in keywords)))


def sync(db, graph, config, emit):
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
                emit(message)
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
