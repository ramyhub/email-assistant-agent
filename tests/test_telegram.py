import unittest
from unittest.mock import Mock
from telegram_assistant import Assistant, Telegram


class TelegramTests(unittest.TestCase):
    def test_only_configured_private_user_is_authorized(self):
        bot = Telegram.__new__(Telegram)
        bot.chat_id = '123'
        good = {'chat': {'id': 123, 'type': 'private'}, 'from': {'id': 123}}
        self.assertTrue(bot.authorized(good))
        self.assertFalse(bot.authorized({'chat': {'id': 999, 'type': 'private'}, 'from': {'id': 999}}))
        self.assertFalse(bot.authorized({'chat': {'id': 123, 'type': 'group'}, 'from': {'id': 123}}))

    def test_unauthorized_command_never_reads_mail(self):
        graph, bot = Mock(), Mock()
        bot.authorized.return_value = False
        Assistant(graph, bot).handle({'text': '/inbox'})
        graph.get.assert_not_called()
        bot.send.assert_not_called()

    def test_read_requires_list_and_encodes_message_id(self):
        graph, bot = Mock(), Mock()
        bot.authorized.return_value = True
        assistant = Assistant(graph, bot)
        assistant.handle({'text': '/read 1'})
        graph.get.assert_not_called()
        graph.get.return_value = {'value': [{'id': 'a/b+c', 'subject': 'Hello'}]}
        assistant.handle({'text': '/inbox'})
        graph.get.return_value = {'subject': 'Hello', 'body': {'content': 'Text'}}
        assistant.handle({'text': '/read 1'})
        self.assertIn('/messages/a%2Fb%2Bc?', graph.get.call_args[0][0])

    def test_telegram_network_error_does_not_expose_token(self):
        bot = Telegram.__new__(Telegram)
        bot.base = 'https://api.telegram.org/botSECRET/'
        bot.session = Mock()
        bot.session.post.side_effect = RuntimeError('URL includes SECRET')
        with self.assertRaises(RuntimeError) as caught:
            bot.call('getUpdates', {})
        self.assertNotIn('SECRET', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
