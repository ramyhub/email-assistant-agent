"""Telegram Bot API transport."""


class TelegramChannel:
    name = 'Telegram'

    def __init__(self, token, chat_id):
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
            raise RuntimeError('Telegram request failed. Check connectivity.') from None
        if not response.ok or not data.get('ok'):
            raise RuntimeError(f'Telegram {method} failed (HTTP {response.status_code}). Check bot token/chat and any existing webhook.')
        return data['result']

    def updates(self, offset=None):
        payload = {'timeout': 1, 'allowed_updates': ['message']}
        if offset is not None:
            payload['offset'] = offset
        return self.call('getUpdates', payload)

    def send(self, text, recipient=None):
        recipient = str(recipient or self.chat_id)
        if not self.chat_id or recipient != self.chat_id:
            raise ValueError('Telegram recipient is not the configured private chat.')
        for start in range(0, len(text), 2000):
            self.call('sendMessage', {'chat_id': recipient, 'text': text[start:start + 2000],
                'link_preview_options': {'is_disabled': True}})

    def authorized(self, message):
        chat = message.get('chat', {})
        return (chat.get('type') == 'private' and str(chat.get('id')) == self.chat_id
                and str(message.get('from', {}).get('id')) == self.chat_id
                and not message.get('from', {}).get('is_bot', False))

    def incoming(self, message):
        if not self.authorized(message):
            return None
        text = message.get('text', '').strip()
        if text:
            return {'recipient': self.chat_id, 'text': text}
        return None
