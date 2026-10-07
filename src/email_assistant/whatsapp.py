"""Meta WhatsApp Cloud API transport and signature-checked webhook."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import hmac
import json
import re
from queue import Queue
import sqlite3
import threading
from urllib.parse import parse_qs, urlparse


class WhatsAppCloudChannel:
    name = 'WhatsApp'

    def __init__(self, config, database):
        import requests
        self.access_token = config['whatsapp_access_token']
        self.phone_number_id = str(config['whatsapp_phone_number_id'])
        if not self.phone_number_id.isdigit():
            raise ValueError('WHATSAPP_PHONE_NUMBER_ID must contain only digits.')
        self.app_secret = config['whatsapp_app_secret']
        self.verify_token = config['whatsapp_verify_token']
        self.allowed_sender = ''.join(c for c in config['whatsapp_allowed_sender'] if c.isdigit())
        if not self.allowed_sender:
            raise ValueError('Set WHATSAPP_ALLOWED_SENDER as an international phone number.')
        version = config.get('whatsapp_graph_version', 'v26.0')
        if not re.fullmatch(r'v[0-9]+\.[0-9]+', version):
            raise ValueError('WHATSAPP_GRAPH_VERSION must look like v26.0.')
        self.api_url = f'https://graph.facebook.com/{version}/{self.phone_number_id}/messages'
        self.webhook_port = int(config.get('whatsapp_webhook_port', 8766))
        if not 1 <= self.webhook_port <= 65535:
            raise ValueError('WHATSAPP_WEBHOOK_PORT must be between 1 and 65535.')
        self.database = str(database)
        self.session = requests.Session()
        self.server = None
        self.thread = None

    @classmethod
    def from_config(cls, config, database):
        required = ('whatsapp_access_token', 'whatsapp_phone_number_id', 'whatsapp_app_secret',
                    'whatsapp_verify_token', 'whatsapp_allowed_sender')
        configured = [bool(config.get(key)) for key in required]
        if not any(configured):
            return None
        if not all(configured):
            raise ValueError('Configure all WhatsApp credentials and WHATSAPP_ALLOWED_SENDER, or leave them all empty.')
        return cls(config, database)

    def send(self, text, recipient=None):
        recipient = ''.join(c for c in str(recipient or self.allowed_sender) if c.isdigit())
        if recipient != self.allowed_sender:
            raise ValueError('WhatsApp recipient is not the configured private sender.')
        for start in range(0, len(text), 4000):
            try:
                response = self.session.post(self.api_url, headers={
                    'Authorization': 'Bearer ' + self.access_token,
                    'Content-Type': 'application/json'}, json={
                    'messaging_product': 'whatsapp', 'recipient_type': 'individual',
                    'to': recipient, 'type': 'text',
                    'text': {'body': text[start:start + 4000], 'preview_url': False}},
                    timeout=20, allow_redirects=False)
            except Exception:
                raise RuntimeError('WhatsApp send failed. Check connectivity.') from None
            if not response.ok:
                raise RuntimeError(f'WhatsApp send failed (HTTP {response.status_code}). Check Cloud API setup and the customer-service messaging window.')

    def _verify_signature(self, body, signature):
        expected = 'sha256=' + hmac.new(self.app_secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature or '')

    def _messages(self, payload):
        if payload.get('object') != 'whatsapp_business_account':
            return
        for entry in payload.get('entry', []):
            for change in entry.get('changes', []):
                value = change.get('value', {})
                metadata = value.get('metadata', {})
                if str(metadata.get('phone_number_id', '')) != self.phone_number_id:
                    continue
                for message in value.get('messages', []):
                    if message.get('type') != 'text':
                        continue
                    sender = ''.join(c for c in str(message.get('from', '')) if c.isdigit())
                    text = message.get('text', {}).get('body', '').strip()
                    message_id = message.get('id')
                    if sender != self.allowed_sender or not text or not message_id:
                        continue
                    with sqlite3.connect(self.database, timeout=10) as db:
                        db.execute('CREATE TABLE IF NOT EXISTS whatsapp_messages (id TEXT PRIMARY KEY)')
                        inserted = db.execute('INSERT OR IGNORE INTO whatsapp_messages VALUES (?)', (message_id,)).rowcount
                    if inserted:
                        yield {'recipient': sender, 'text': text}

    def start(self, incoming: Queue):
        channel = self

        class WebhookHandler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                return

            def respond(self, status, body=b''):
                self.send_response(status)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if urlparse(self.path).path != '/webhook':
                    self.respond(404)
                    return
                query = parse_qs(urlparse(self.path).query)
                if (query.get('hub.mode') == ['subscribe'] and
                        hmac.compare_digest(query.get('hub.verify_token', [''])[0], channel.verify_token)):
                    self.respond(200, query.get('hub.challenge', [''])[0].encode())
                else:
                    self.respond(403)

            def do_POST(self):
                if urlparse(self.path).path != '/webhook':
                    self.respond(404)
                    return
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if length <= 0 or length > 1_000_000:
                        self.respond(413)
                        return
                    body = self.rfile.read(length)
                    signature = self.headers.get('X-Hub-Signature-256', '')
                    if not channel._verify_signature(body, signature):
                        self.respond(401)
                        return
                    payload = json.loads(body)
                    for message in channel._messages(payload):
                        incoming.put(message)
                except (ValueError, TypeError, json.JSONDecodeError, sqlite3.Error):
                    self.respond(400)
                    return
                self.respond(200)

        self.server = ThreadingHTTPServer(('127.0.0.1', self.webhook_port), WebhookHandler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=3)
