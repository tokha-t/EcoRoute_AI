from __future__ import annotations

from src.i18n import TRANSLATIONS, t


def test_all_v2_translations_have_ru_and_en() -> None:
    assert TRANSLATIONS
    for key, translations in TRANSLATIONS.items():
        assert set(translations) == {"ru", "en"}, key
        assert t(key, "ru", real=1, synthetic=1)
        assert t(key, "en", real=1, synthetic=1)
