"""Load private environment and legacy JSON configuration."""
import json
import os


def load_config(config_path, env_path):
    from dotenv import load_dotenv
    load_dotenv(env_path, override=False)
    config = json.loads(config_path.read_text()) if config_path.exists() else {}
    fields = {'MAIL_EMAIL': 'email', 'MS_CLIENT_ID': 'client_id',
              'MS_TENANT_ID': 'tenant_id', 'STATE_DIR': 'state_dir',
              'TELEGRAM_BOT_TOKEN': 'telegram_bot_token', 'TELEGRAM_CHAT_ID': 'telegram_chat_id',
              'WHATSAPP_ACCESS_TOKEN': 'whatsapp_access_token',
              'WHATSAPP_PHONE_NUMBER_ID': 'whatsapp_phone_number_id',
              'WHATSAPP_APP_SECRET': 'whatsapp_app_secret',
              'WHATSAPP_VERIFY_TOKEN': 'whatsapp_verify_token',
              'WHATSAPP_ALLOWED_SENDER': 'whatsapp_allowed_sender',
              'WHATSAPP_GRAPH_VERSION': 'whatsapp_graph_version',
              'WHATSAPP_WEBHOOK_PORT': 'whatsapp_webhook_port'}
    for env, field in fields.items():
        if env in os.environ:
            config[field] = os.environ[env]
    if 'POLL_SECONDS' in os.environ:
        config['poll_seconds'] = int(os.environ['POLL_SECONDS'])
    for env, field in [('MAIL_SENDERS', 'senders'), ('MAIL_KEYWORDS', 'keywords')]:
        if env in os.environ:
            config[field] = [s.strip() for s in os.environ[env].split(',') if s.strip()]
    return config
