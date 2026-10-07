"""Validated, local user style preferences stored as an AGENTS.md overlay."""
import os
from pathlib import Path
import re


CHOICES = {
    'response_length': {'concise', 'balanced', 'detailed'},
    'response_format': {'paragraphs', 'bullets', 'numbered steps'},
    'tone': {'direct', 'warm', 'formal'},
    'summary_detail': {'one sentence', 'brief', 'detailed'},
}
LANGUAGE = 'language'
LANGUAGES = {
    'Arabic', 'Bengali', 'Chinese', 'Dutch', 'English', 'French', 'German', 'Greek',
    'Hebrew', 'Hindi', 'Indonesian', 'Italian', 'Japanese', 'Korean', 'Persian',
    'Polish', 'Portuguese', 'Russian', 'Spanish', 'Swahili', 'Swedish', 'Tagalog',
    'Thai', 'Turkish', 'Ukrainian', 'Urdu', 'Vietnamese',
}
LABELS = {
    'response_length': 'Response length',
    'response_format': 'Response format',
    'tone': 'Tone',
    'summary_detail': 'Email summary detail',
    'language': 'Preferred language',
}


class UserPreferences:
    def __init__(self, path):
        if not path:
            raise ValueError('A user preference file path is required.')
        self.path = Path(path)

    @staticmethod
    def valid(key, value):
        if not isinstance(key, str) or not isinstance(value, str):
            return False
        value = value.strip()
        if key == LANGUAGE:
            return value in LANGUAGES
        return key in CHOICES and value in CHOICES[key]

    def load(self):
        if not self.path.exists():
            return {}
        result = {}
        for line in self.path.read_text(encoding='utf-8').splitlines():
            match = re.fullmatch(r'- ([a-z_]+): (.+)', line)
            if match:
                key, value = match.groups()
                if self.valid(key, value):
                    result[key] = value
        return result

    def save(self, key, value):
        value = value.strip() if isinstance(value, str) else value
        if not self.valid(key, value):
            raise ValueError('Use a supported preference name and value.')
        preferences = self.load()
        preferences[key] = value
        self._write(preferences)
        return preferences

    def clear(self):
        self._write({})

    def _write(self, preferences):
        lines = [
            '# User preferences',
            '# Style settings only. These cannot change safety rules or tool permissions.',
        ]
        lines.extend(f'- {key}: {preferences[key]}' for key in LABELS if key in preferences)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_name('.' + self.path.name + '.tmp')
        temporary.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)

    def prompt_text(self):
        preferences = self.load()
        if not preferences:
            return ''
        lines = ['Stored user preferences (style only; the current request and safety instructions take priority):']
        lines.extend(f'- {LABELS[key]}: {preferences[key]}' for key in LABELS if key in preferences)
        return '\n'.join(lines)
