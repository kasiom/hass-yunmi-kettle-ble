"""Regression test for config-entry migration compatibility."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
import unittest
from enum import StrEnum
from pathlib import Path

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "yunmi_kettle"
PACKAGE = "yunmi_kettle_migration_test"


class _Platform(StrEnum):
    SENSOR = "sensor"
    NUMBER = "number"
    SWITCH = "switch"
    SELECT = "select"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(
        name,
        path,
        submodule_search_locations=[str(path.parent)] if path.name == "__init__.py" else None,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_integration():
    homeassistant = types.ModuleType("homeassistant")
    const = types.ModuleType("homeassistant.const")
    const.Platform = _Platform
    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = object
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    homeassistant.const = const
    sys.modules["homeassistant"] = homeassistant
    sys.modules["homeassistant.const"] = const
    sys.modules["homeassistant.config_entries"] = config_entries
    sys.modules["homeassistant.core"] = core

    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(COMPONENT)]
    sys.modules[PACKAGE] = package
    _load_module(f"{PACKAGE}.const", COMPONENT / "const.py")
    coordinator = types.ModuleType(f"{PACKAGE}.coordinator")
    coordinator.YunmiKettleCoordinator = object
    sys.modules[f"{PACKAGE}.coordinator"] = coordinator
    return _load_module(PACKAGE, COMPONENT / "__init__.py")


integration = _load_integration()


class MigrationTests(unittest.TestCase):
    def test_version_one_entry_keeps_identity_and_removes_dead_option(self) -> None:
        entry = types.SimpleNamespace(
            version=1,
            data={
                "mac": "AA:BB:CC:DD:EE:FF",
                "token": "00112233445566778899aabb",
                "name": "My kettle",
                "target_temp": 90,
            },
            options={"poll_interval": 60, "target_temp": 85},
        )
        updates = []

        class _Entries:
            @staticmethod
            def async_update_entry(updated_entry, **changes) -> None:
                updates.append((updated_entry, changes))

        hass = types.SimpleNamespace(config_entries=_Entries())

        migrated = asyncio.run(integration.async_migrate_entry(hass, entry))

        self.assertTrue(migrated)
        self.assertEqual(len(updates), 1)
        updated_entry, changes = updates[0]
        self.assertIs(updated_entry, entry)
        self.assertEqual(changes["version"], 2)
        self.assertEqual(changes["data"]["mac"], entry.data["mac"])
        self.assertEqual(changes["data"]["name"], "My kettle")
        self.assertNotIn("target_temp", changes["data"])
        self.assertEqual(changes["options"], {"poll_interval": 60})


if __name__ == "__main__":
    unittest.main()
