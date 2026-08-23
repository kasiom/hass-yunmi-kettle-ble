"""Regression tests for package metadata and translations."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "yunmi_kettle"


def _key_tree(value):
    if isinstance(value, dict):
        return {key: _key_tree(child) for key, child in value.items()}
    return None


class MetadataTests(unittest.TestCase):
    def test_manifest_and_hacs_versions(self) -> None:
        manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
        hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], "0.3.0")
        self.assertEqual(manifest["iot_class"], "local_push")
        self.assertEqual(hacs["homeassistant"], "2025.3.0")

    def test_translation_structure_matches_english(self) -> None:
        translations = COMPONENT / "translations"
        english = json.loads((translations / "en.json").read_text(encoding="utf-8"))
        for language in ("cs", "ru"):
            translated = json.loads((translations / f"{language}.json").read_text(encoding="utf-8"))
            self.assertEqual(_key_tree(translated), _key_tree(english), language)


if __name__ == "__main__":
    unittest.main()
