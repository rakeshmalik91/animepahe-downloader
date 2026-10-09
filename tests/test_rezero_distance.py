import unittest
import re
import Levenshtein
import config
config.ANIMEPAHE_URL = "https://animepahe.org"
from modules.utils import extract_season_number
from modules.scraper import search_anime
from unittest.mock import MagicMock

class TestReZeroDistance(unittest.TestCase):
    def test_calculate_and_verify_distances(self):
        setattr(config, 'ANIMEPAHE_URL', 'https://animepahe.org')
        query = "Re Zero - Starting Life in Another World Season 4"
        q_s = extract_season_number(query)
        q_lower = query.lower()
        q_clean = re.sub(r'[^a-z0-9 ]', ' ', q_lower).strip()
        q_clean = re.sub(r'\s+', ' ', q_clean)

        self.assertEqual(q_s, 4)
        self.assertEqual(q_clean, "re zero starting life in another world season 4")

        # Candidate A: Desired Season 4
        cand_a = "Re:ZERO -Starting Life in Another World- Season 4"
        clean_a = re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]', ' ', cand_a.lower()).strip())
        dist_a = Levenshtein.distance(q_clean, clean_a)

        # Candidate B: Break Time Season 4
        cand_b = "Re:ZERO ~Starting Break Time From Zero~ Season 4"
        clean_b = re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]', ' ', cand_b.lower()).strip())
        dist_b = Levenshtein.distance(q_clean, clean_b)

        # Candidate C: Main Season 3
        cand_c = "Re:ZERO -Starting Life in Another World- Season 3"
        clean_c = re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]', ' ', cand_c.lower()).strip())
        dist_c = Levenshtein.distance(q_clean, clean_c)

        # Candidate D: Main Season 1 (Base title)
        cand_d = "Re:ZERO -Starting Life in Another World-"
        clean_d = re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]', ' ', cand_d.lower()).strip())
        dist_d = Levenshtein.distance(q_clean, clean_d)

        # Confirm exact raw distances:
        self.assertEqual(dist_a, 0)
        self.assertEqual(dist_b, 18)
        self.assertEqual(dist_c, 1)
        self.assertEqual(dist_d, 9)

        # Scenario 1: Candidate A EXISTS in API results
        mock_client_with_a = MagicMock()
        mock_client_with_a.get.return_value.status_code = 200
        mock_client_with_a.get.return_value.json.return_value = {
            "data": [
                {"session": "sess_cand_a", "title": cand_a, "type": "TV"},
                {"session": "sess_cand_b", "title": cand_b, "type": "Special"},
                {"session": "sess_cand_c", "title": cand_c, "type": "TV"},
                {"session": "sess_cand_d", "title": cand_d, "type": "TV"},
            ]
        }
        aid, title, ok, best_score = search_anime(mock_client_with_a, query)
        self.assertEqual(aid, "sess_cand_a")
        self.assertEqual(best_score, -20) # 0 dist - 20 exact match bonus

        # Scenario 2: Candidate A DOES NOT EXIST in API results
        # cand_b gets generic +40 type penalty because its type is "Special" and query is for TV series
        mock_client_without_a = MagicMock()
        mock_client_without_a.get.return_value.status_code = 200
        mock_client_without_a.get.return_value.json.return_value = {
            "data": [
                {"session": "sess_cand_b", "title": cand_b, "type": "Special"},
                {"session": "sess_cand_c", "title": cand_c, "type": "TV"},
                {"session": "sess_cand_d", "title": cand_d, "type": "TV"},
            ]
        }
        aid_no_a, title_no_a, ok_no_a, score_no_a = search_anime(mock_client_without_a, query)
        # 18 raw + 40 type penalty = 58
        self.assertEqual(score_no_a, 58)
        # In processor.py: dist (58) > MAX_DISTANCE_THRESHOLD (20), so it is REJECTED from updating!
        self.assertGreater(score_no_a, config.MAX_DISTANCE_THRESHOLD)

        # Scenario 3: Base Title Guard in processor.py
        parent_name = "Re Zero - Starting Life in Another World (2016-2026)"
        expected_base = re.sub(r'(?:Season|S)\s*\d+', '', parent_name, flags=re.IGNORECASE)
        expected_base = re.sub(r'\s*\(\d{4}[^)]*\)', '', expected_base).strip()
        eb_norm = re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]', ' ', expected_base.lower())).strip()

        new_base = re.sub(r'(?:Season|S)\s*\d+', '', cand_b, flags=re.IGNORECASE).strip()
        nb_norm = re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]', ' ', new_base.lower())).strip()
        base_dist = Levenshtein.distance(nb_norm, eb_norm)
        max_len = max(len(nb_norm), len(eb_norm))
        base_ratio = Levenshtein.ratio(nb_norm, eb_norm)
        max_allowed_dist = min(getattr(config, 'MAX_DISTANCE_THRESHOLD', 20), max(3, int(max_len * 0.25)))

        self.assertEqual(base_dist, 18)
        self.assertEqual(max_allowed_dist, 9)
        self.assertLess(base_ratio, 0.75)
        # Verified: Guard rejects it because base_dist > max_allowed_dist AND base_ratio < 0.75
        self.assertTrue(base_dist > max_allowed_dist or base_ratio < 0.75)

if __name__ == "__main__":
    unittest.main()
