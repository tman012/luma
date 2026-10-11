import copy
import json
import os
import tempfile
import unittest

from app import automation_features as af
from app import settings_manager as sm


class _Settings:
    """Minimal dotted-path getter, like SettingsManager.get."""

    def __init__(self, data):
        self.data = data

    def get(self, path, default=None):
        cur = self.data
        for part in path.split('.'):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur


# Shape of an install from before the feature switches: every job enabled,
# upgrade_scheduler has no block (it was default-on).
LEGACY_ALL_ON = {
    "automation": {
        "interval_hours": 2,
        "trending_watchlist": {"enabled": True, "limit": 5},
        "trending_tv_watchlist": {"enabled": True},
        "anticipated_watchlist": {"enabled": True},
        "now_available_watchlist": {"enabled": True},
        "latest_auto_grab": {"enabled": True, "min_seeders": 200},
        "upgrade_4k_auto_grab": {"enabled": True, "plex_4k_section_id": 4},
        "genre_roulette": {"enabled": True, "hour": 22, "minute": 50},
        "movie_night": {"enabled": True, "weekday": 4},
        "franchise_completer": {"enabled": True},
        "watchlist_digital_dates": {"enabled": True, "hour": 6},
        "downloads_guard": {"enabled": True},
    }
}


class TestIsEnabled(unittest.TestCase):
    def test_group_switch_gates_members(self):
        s = _Settings({"automation": {"auto_grab": {"enabled": False},
                                      "trending_watchlist": {"enabled": True}}})
        self.assertFalse(af.is_enabled(s, "trending_watchlist"))
        s.data["automation"]["auto_grab"]["enabled"] = True
        self.assertTrue(af.is_enabled(s, "trending_watchlist"))

    def test_member_flag_still_required(self):
        s = _Settings({"automation": {"auto_grab": {"enabled": True},
                                      "latest_auto_grab": {"enabled": False}}})
        self.assertFalse(af.is_enabled(s, "latest_auto_grab"))

    def test_upgrade_scan_member_defaults_on_but_group_off(self):
        s = _Settings({"automation": {}})
        self.assertFalse(af.is_enabled(s, "upgrade_scheduler"))
        s.data["automation"]["upgrades"] = {"enabled": True}
        self.assertTrue(af.is_enabled(s, "upgrade_scheduler"))

    def test_single_feature(self):
        s = _Settings({"automation": {"genre_roulette": {"enabled": True}}})
        self.assertTrue(af.is_enabled(s, "genre_roulette"))


class TestMigration(unittest.TestCase):
    def test_existing_install_keeps_everything_running(self):
        data = copy.deepcopy(LEGACY_ALL_ON)
        self.assertTrue(sm._migrate_automation_features(data))
        s = _Settings(data)
        for group, members in af.GROUPS.items():
            self.assertTrue(data["automation"][group]["enabled"], group)
            for job in members:
                self.assertTrue(af.is_enabled(s, job), job)
        for feature in af.SINGLE_FEATURES:
            self.assertTrue(af.is_enabled(s, feature), feature)
        # options are untouched
        self.assertEqual(data["automation"]["latest_auto_grab"]["min_seeders"], 200)
        self.assertEqual(data["automation"]["genre_roulette"]["minute"], 50)
        # second run is a no-op
        self.assertFalse(sm._migrate_automation_features(data))

    def test_partial_install_only_turns_on_what_ran(self):
        data = {"automation": {"trending_watchlist": {"enabled": True},
                               "upgrade_scheduler": {"enabled": False}}}
        sm._migrate_automation_features(data)
        a = data["automation"]
        self.assertTrue(a["auto_grab"]["enabled"])
        self.assertFalse(a["latest_auto_grab"]["enabled"])
        self.assertFalse(a["upgrades"]["enabled"])
        self.assertTrue(a["watchlist_digital_dates"]["enabled"])  # was default-on

    def test_default_on_upgrade_scan_is_preserved(self):
        data = {"automation": {"interval_hours": 2}}
        sm._migrate_automation_features(data)
        a = data["automation"]
        self.assertTrue(a["upgrades"]["enabled"])
        self.assertTrue(a["upgrade_scheduler"]["enabled"])
        self.assertFalse(a["upgrade_4k_auto_grab"]["enabled"])
        self.assertFalse(a["auto_grab"]["enabled"])

    def test_new_install_is_all_off(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "settings.json")
            env_keep = {k: os.environ.pop(k) for k in ("SHIELD_PLEX_IP",) if k in os.environ}
            try:
                mgr = sm.SettingsManager(settings_path=path)
            finally:
                os.environ.update(env_keep)
            for group, members in af.GROUPS.items():
                self.assertFalse(mgr.get(f"automation.{group}.enabled"), group)
                for job in members:
                    self.assertFalse(af.is_enabled(mgr, job), job)
            for feature in af.SINGLE_FEATURES:
                self.assertFalse(af.is_enabled(mgr, feature), feature)

    def test_load_of_legacy_file_migrates_and_saves(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "settings.json")
            with open(path, "w") as f:
                json.dump(LEGACY_ALL_ON, f)
            mgr = sm.SettingsManager(settings_path=path)
            self.assertTrue(mgr.get("automation.auto_grab.enabled"))
            self.assertTrue(mgr.get("automation.upgrades.enabled"))
            self.assertTrue(af.is_enabled(mgr, "upgrade_scheduler"))
            with open(path) as f:
                saved = json.load(f)
            self.assertTrue(saved["automation"]["auto_grab"]["enabled"])


if __name__ == "__main__":
    unittest.main()
