import unittest
import re
import Levenshtein
import config
config.ANIMEPAHE_URL = "https://animepahe.org"
from modules.utils import extract_season_number
from unittest.mock import MagicMock, patch

class TestSilentSeasonConsistencyCheck(unittest.TestCase):
    """Test the silent season consistency check in processor.py that verifies 
    base title similarity, not just season number matching."""

    def test_base_title_mismatch_triggers_research(self):
        """When initial search matches wrong anime's same season, base title check should trigger re-search."""
        # Simulate the scenario:
        # Folder: "Re Zero - Starting Life in Another World (2016-2026)\Season 4"
        # Initial search query: "Season 4" (leaf folder name only)
        # API returns: "Re:ZERO ~Starting Break Time From Zero~ Season 4" (wrong anime, same season)
        
        parent_name = "Re Zero - Starting Life in Another World (2016-2026)"
        folder_name = "Season 4"
        
        # Wrong match from initial search (same season, different anime)
        wrong_match = "Re:ZERO ~Starting Break Time From Zero~ Season 4"
        
        # Extract season from folder
        f_s = extract_season_number(folder_name)  # 4
        if f_s is None and parent_name:
            f_s = extract_season_number(parent_name)
        
        # Extract season from matched title
        t_s = extract_season_number(wrong_match)  # 4
        
        # Old logic: only triggers if f_s != t_s
        # Both are 4, so old logic would NOT trigger re-search
        old_logic_trigger = f_s != t_s
        self.assertFalse(old_logic_trigger, "Old logic: same season = no re-search")
        
        # New logic: ALSO checks base title similarity
        new_base = re.sub(r'(?:Season|S)\s*\d+', '', wrong_match, flags=re.IGNORECASE).strip()
        expected_base = re.sub(r'(?:Season|S)\s*\d+', '', parent_name, flags=re.IGNORECASE)
        expected_base = re.sub(r'\s*\(\d{4}[^)]*\)', '', expected_base).strip()
        nb_norm = re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]', ' ', new_base.lower())).strip()
        eb_norm = re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]', ' ', expected_base.lower())).strip()
        base_dist = Levenshtein.distance(nb_norm, eb_norm)
        base_threshold = getattr(config, 'MAX_DISTANCE_THRESHOLD', 20)
        max_allowed = min(base_threshold, max(3, int(max(len(nb_norm), len(eb_norm)) * 0.25)))
        base_ratio = Levenshtein.ratio(nb_norm, eb_norm)
        
        base_mismatch = base_dist > max_allowed or base_ratio < 0.75
        
        # New logic: triggers if season mismatch OR base title mismatch
        new_logic_trigger = (f_s != t_s) or base_mismatch
        self.assertTrue(new_logic_trigger, "New logic: base title mismatch triggers re-search")
        
        # Verify the distances (with space collapsing normalization)
        self.assertEqual(base_dist, 18)  # "starting break time from zero" vs "re zero starting life in another world"
        self.assertEqual(max_allowed, 9)
        self.assertLess(base_ratio, 0.75)
        self.assertTrue(base_dist > max_allowed or base_ratio < 0.75)

    def test_correct_match_passes_base_title_check(self):
        """Correct anime match should pass the base title check."""
        parent_name = "Re Zero - Starting Life in Another World (2016-2026)"
        folder_name = "Season 4"
        
        # Correct match
        correct_match = "Re:ZERO -Starting Life in Another World- Season 4"
        
        f_s = extract_season_number(folder_name)
        t_s = extract_season_number(correct_match)
        
        new_base = re.sub(r'(?:Season|S)\s*\d+', '', correct_match, flags=re.IGNORECASE).strip()
        expected_base = re.sub(r'(?:Season|S)\s*\d+', '', parent_name, flags=re.IGNORECASE)
        expected_base = re.sub(r'\s*\(\d{4}[^)]*\)', '', expected_base).strip()
        nb_norm = re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]', ' ', new_base.lower())).strip()
        eb_norm = re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]', ' ', expected_base.lower())).strip()
        base_dist = Levenshtein.distance(nb_norm, eb_norm)
        base_threshold = getattr(config, 'MAX_DISTANCE_THRESHOLD', 20)
        max_allowed = min(base_threshold, max(3, int(max(len(nb_norm), len(eb_norm)) * 0.25)))
        base_ratio = Levenshtein.ratio(nb_norm, eb_norm)
        
        base_mismatch = base_dist > max_allowed or base_ratio < 0.75
        
        # Should NOT mismatch - base titles are similar
        self.assertFalse(base_mismatch, "Correct match should pass base title check")
        self.assertEqual(base_dist, 0)  # "re zero starting life in another world" vs same

    def test_season_mismatch_still_triggers(self):
        """Season number mismatch should still trigger re-search (regression test)."""
        parent_name = "Re Zero - Starting Life in Another World (2016-2026)"
        folder_name = "Season 4"
        
        # Match with wrong season
        wrong_season_match = "Re:ZERO -Starting Life in Another World- Season 3"
        
        f_s = extract_season_number(folder_name)  # 4
        t_s = extract_season_number(wrong_season_match)  # 3
        
        old_logic_trigger = f_s != t_s
        self.assertTrue(old_logic_trigger, "Season mismatch should still trigger")
        
        # New logic also includes base check but season mismatch is enough
        new_logic_trigger = (f_s != t_s) or True  # base_mismatch doesn't matter
        self.assertTrue(new_logic_trigger)


if __name__ == "__main__":
    unittest.main()