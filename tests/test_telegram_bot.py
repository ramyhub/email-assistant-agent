import sqlite3
import unittest
from unittest.mock import Mock, patch

from email_assistant.assistant import Assistant
from email_assistant.telegram import TelegramChannel


class TelegramTests(unittest.TestCase):
    def setUp(self):
        self.telegram = TelegramChannel.__new__(TelegramChannel)
        self.telegram.chat_id = '123'
        self.telegram.base = 'https://api.telegram.org/botTOKEN/'
        self.telegram.session = Mock()
        response = Mock(ok=True)
        response.json.return_value = {'ok': True, 'result': {}}
        self.telegram.session.post.return_value = response

    def test_only_configured_private_user_is_authorized(self):
        good = {'chat': {'id': 123, 'type': 'private'}, 'from': {'id': 123}}
        self.assertTrue(self.telegram.authorized(good))
        self.assertFalse(self.telegram.authorized({'chat': {'id': 999, 'type': 'private'}, 'from': {'id': 999}}))
        self.assertFalse(self.telegram.authorized({'chat': {'id': 123, 'type': 'group'}, 'from': {'id': 123}}))
        self.assertIsNone(self.telegram.incoming({'chat': {'id': 999, 'type': 'private'},
                                                  'from': {'id': 999}, 'text': '/inbox'}))

    def assistant(self, conversation):
        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT)')
        with patch('email_assistant.codex_agent.Conversation', return_value=conversation):
            assistant = Assistant([self.telegram], {'email': 'test@example.edu'}, db)
        assistant.conversation = conversation
        return assistant

    def test_user_text_and_slash_commands_are_relayed_to_codex(self):
        conversation = Mock()
        conversation.respond.return_value = 'Codex response'
        assistant = self.assistant(conversation)
        for text in ('/help', '/search professor', 'Summarize today'):
            assistant.handle_message(self.telegram, {'recipient': '123', 'text': text})
            conversation.respond.assert_called_with(text)
            self.telegram.session.post.assert_called_with(
                self.telegram.base + 'sendMessage',
                json={'chat_id': '123', 'text': 'Codex response',
                      'link_preview_options': {'is_disabled': True}},
                timeout=20, allow_redirects=False)

    def test_clear_starts_new_conversation_and_replies_to_private_chat(self):
        conversation = Mock()
        assistant = self.assistant(conversation)
        assistant.handle_message(self.telegram, {'recipient': '123', 'text': '/clear'})
        conversation.clear.assert_called_once_with()
        conversation.respond.assert_not_called()
        self.assertEqual(self.telegram.session.post.call_args.args[0],
                         self.telegram.base + 'sendMessage')

    def test_telegram_network_error_does_not_expose_token(self):
        self.telegram.base = 'https://api.telegram.org/botSECRET/'
        self.telegram.session.post.side_effect = RuntimeError('URL includes SECRET')
        with self.assertRaises(RuntimeError) as caught:
            self.telegram.call('getUpdates', {})
        self.assertNotIn('SECRET', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
