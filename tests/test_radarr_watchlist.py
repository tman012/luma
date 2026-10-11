"""
Tests for the Radarr watchlist "ensure" flow.

Covers:
  Case 1: Movie already in Radarr, unmonitored, wrong profile → update + search
  Case 2: Movie already in Radarr, already monitored, wrong profile → update profile + search
  Case 3: Movie NOT in Radarr → add new + search
  Case 4: Radarr connection failure → surface error
  Case 5: Update PUT rejected by Radarr → surface error
  Case 6: Quality profile not found → fallback behavior
"""

import unittest
from unittest.mock import patch, MagicMock
from app.radarr_api import RadarrAPI


def make_radarr():
    return RadarrAPI('http://radarr:7878', 'test-api-key')


def make_movie(radarr_id=42, tmdb_id=550, monitored=False, quality_profile_id=1):
    """Build a realistic Radarr movie object."""
    return {
        'id': radarr_id,
        'title': 'Fight Club',
        'year': 1999,
        'tmdbId': tmdb_id,
        'imdbId': 'tt0137523',
        'monitored': monitored,
        'qualityProfileId': quality_profile_id,
        'titleSlug': 'fight-club-550',
        'rootFolderPath': '/movies/',
        'path': '/movies/Fight Club (1999)',
        'images': [],
        'hasFile': False,
        'status': 'released',
        'minimumAvailability': 'announced',
    }


class TestEnsureMovieMonitored(unittest.TestCase):
    """Tests for RadarrAPI.ensure_movie_monitored()."""

    # ---- Case 1: Existing, unmonitored, wrong profile → update + search ----
    @patch.object(RadarrAPI, 'command_search')
    @patch.object(RadarrAPI, 'update_movie')
    @patch.object(RadarrAPI, 'get_movie')
    @patch.object(RadarrAPI, 'get_movie_by_tmdb_id')
    def test_existing_unmonitored_updates_and_searches(
        self, mock_find, mock_get, mock_update, mock_search
    ):
        radarr = make_radarr()
        movie = make_movie(monitored=False, quality_profile_id=1)
        target_profile_id = 5

        mock_find.return_value = movie
        mock_get.return_value = dict(movie)  # full re-fetch
        # Simulate Radarr returning the updated movie
        updated_movie = dict(movie, monitored=True, qualityProfileId=target_profile_id)
        mock_update.return_value = updated_movie
        mock_search.return_value = {'id': 123}

        result = radarr.ensure_movie_monitored(
            tmdb_id=550, quality_profile_id=target_profile_id,
            title='Fight Club', year=1999,
        )

        self.assertTrue(result['success'])
        self.assertEqual(result['action'], 'updated')
        self.assertEqual(result['radarr_id'], 42)
        self.assertTrue(result['search_issued'])

        # Verify before state captured
        self.assertFalse(result['before']['monitored'])
        self.assertEqual(result['before']['qualityProfileId'], 1)

        # Verify after state
        self.assertTrue(result['after']['monitored'])
        self.assertEqual(result['after']['qualityProfileId'], target_profile_id)

        # Verify update was called with correct values
        update_call = mock_update.call_args[0][0]
        self.assertTrue(update_call['monitored'])
        self.assertEqual(update_call['qualityProfileId'], target_profile_id)

        # Verify search was issued
        mock_search.assert_called_once_with([42])

    # ---- Case 2: Existing, already monitored, wrong profile → update profile ----
    @patch.object(RadarrAPI, 'command_search')
    @patch.object(RadarrAPI, 'update_movie')
    @patch.object(RadarrAPI, 'get_movie')
    @patch.object(RadarrAPI, 'get_movie_by_tmdb_id')
    def test_existing_monitored_wrong_profile_updates(
        self, mock_find, mock_get, mock_update, mock_search
    ):
        radarr = make_radarr()
        movie = make_movie(monitored=True, quality_profile_id=1)
        target_profile_id = 5

        mock_find.return_value = movie
        mock_get.return_value = dict(movie)
        updated_movie = dict(movie, monitored=True, qualityProfileId=target_profile_id)
        mock_update.return_value = updated_movie
        mock_search.return_value = {'id': 124}

        result = radarr.ensure_movie_monitored(
            tmdb_id=550, quality_profile_id=target_profile_id,
        )

        self.assertTrue(result['success'])
        self.assertEqual(result['action'], 'updated')
        self.assertEqual(result['after']['qualityProfileId'], target_profile_id)
        mock_search.assert_called_once_with([42])

    # ---- Case 3: Movie NOT in Radarr → add new ----
    @patch.object(RadarrAPI, 'add_movie')
    @patch.object(RadarrAPI, 'get_root_folders')
    @patch.object(RadarrAPI, 'get_movie_by_tmdb_id')
    def test_new_movie_adds_to_radarr(
        self, mock_find, mock_folders, mock_add
    ):
        radarr = make_radarr()
        mock_find.return_value = None  # not in Radarr
        mock_folders.return_value = [{'path': '/movies/'}]
        added = make_movie(radarr_id=99, monitored=True, quality_profile_id=5)
        mock_add.return_value = added

        result = radarr.ensure_movie_monitored(
            tmdb_id=550, quality_profile_id=5,
            title='Fight Club', year=1999,
        )

        self.assertTrue(result['success'])
        self.assertEqual(result['action'], 'added')
        self.assertEqual(result['radarr_id'], 99)
        self.assertTrue(result['search_issued'])  # add_movie includes searchForMovie=True

        # Verify add_movie was called with correct params
        mock_add.assert_called_once_with(
            tmdb_id=550, title='Fight Club', year=1999,
            quality_profile_id=5, root_folder_path='/movies/',
            monitored=True, search_for_movie=True,
        )

    # ---- Case 4: Radarr connection failure on find ----
    @patch.object(RadarrAPI, 'get_movie_by_tmdb_id')
    def test_connection_failure_returns_error(self, mock_find):
        radarr = make_radarr()
        mock_find.return_value = None  # can't find = treats as "not exists"

        # But add_movie will also fail
        with patch.object(radarr, 'get_root_folders', return_value=[{'path': '/movies/'}]):
            with patch.object(radarr, 'add_movie', return_value=None):
                result = radarr.ensure_movie_monitored(
                    tmdb_id=550, quality_profile_id=5,
                    title='Fight Club', year=1999,
                )

        self.assertFalse(result['success'])
        self.assertEqual(result['error'], 'Failed to add movie to Radarr')

    # ---- Case 5: Update PUT fails → error surfaced ----
    @patch.object(RadarrAPI, 'update_movie')
    @patch.object(RadarrAPI, 'get_movie')
    @patch.object(RadarrAPI, 'get_movie_by_tmdb_id')
    def test_update_failure_surfaces_error(
        self, mock_find, mock_get, mock_update
    ):
        radarr = make_radarr()
        movie = make_movie(monitored=False, quality_profile_id=1)

        mock_find.return_value = movie
        mock_get.return_value = dict(movie)
        mock_update.return_value = None  # PUT failed

        result = radarr.ensure_movie_monitored(
            tmdb_id=550, quality_profile_id=5,
        )

        self.assertFalse(result['success'])
        self.assertIn('rejected', result['error'])
        self.assertFalse(result['search_issued'])

    # ---- Case 6: No root folders configured ----
    @patch.object(RadarrAPI, 'get_root_folders')
    @patch.object(RadarrAPI, 'get_movie_by_tmdb_id')
    def test_no_root_folders_returns_error(self, mock_find, mock_folders):
        radarr = make_radarr()
        mock_find.return_value = None
        mock_folders.return_value = []

        result = radarr.ensure_movie_monitored(
            tmdb_id=550, quality_profile_id=5,
            title='Fight Club', year=1999,
        )

        self.assertFalse(result['success'])
        self.assertIn('root folders', result['error'])

    # ---- Verify tmdb_id is cast to int ----
    @patch.object(RadarrAPI, 'command_search')
    @patch.object(RadarrAPI, 'update_movie')
    @patch.object(RadarrAPI, 'get_movie')
    @patch.object(RadarrAPI, 'get_movie_by_tmdb_id')
    def test_string_tmdb_id_cast_to_int(
        self, mock_find, mock_get, mock_update, mock_search
    ):
        radarr = make_radarr()
        movie = make_movie()
        mock_find.return_value = movie
        mock_get.return_value = dict(movie)
        updated = dict(movie, monitored=True, qualityProfileId=5)
        mock_update.return_value = updated
        mock_search.return_value = {'id': 1}

        # Pass tmdb_id as string (simulating bad frontend data)
        result = radarr.ensure_movie_monitored(
            tmdb_id='550', quality_profile_id=5,
        )

        self.assertTrue(result['success'])
        # get_movie_by_tmdb_id should have received an int
        mock_find.assert_called_once_with(550)

    # ---- Verify search command failure doesn't break success ----
    @patch.object(RadarrAPI, 'command_search')
    @patch.object(RadarrAPI, 'update_movie')
    @patch.object(RadarrAPI, 'get_movie')
    @patch.object(RadarrAPI, 'get_movie_by_tmdb_id')
    def test_search_failure_still_reports_success(
        self, mock_find, mock_get, mock_update, mock_search
    ):
        radarr = make_radarr()
        movie = make_movie()
        mock_find.return_value = movie
        mock_get.return_value = dict(movie)
        updated = dict(movie, monitored=True, qualityProfileId=5)
        mock_update.return_value = updated
        mock_search.return_value = None  # search command failed

        result = radarr.ensure_movie_monitored(
            tmdb_id=550, quality_profile_id=5,
        )

        self.assertTrue(result['success'])
        self.assertFalse(result['search_issued'])


class TestGetMovieByTmdbId(unittest.TestCase):
    """Tests for the improved get_movie_by_tmdb_id."""

    @patch('requests.Session')
    def test_filtered_endpoint_returns_movie(self, _):
        radarr = make_radarr()
        movie = make_movie()

        # Mock the filtered GET
        mock_resp = MagicMock()
        mock_resp.json.return_value = [movie]
        mock_resp.raise_for_status = MagicMock()
        radarr.session.get = MagicMock(return_value=mock_resp)

        result = radarr.get_movie_by_tmdb_id(550)

        self.assertIsNotNone(result)
        self.assertEqual(result['tmdbId'], 550)

    @patch('requests.Session')
    def test_fallback_to_scan_when_filtered_fails(self, _):
        radarr = make_radarr()
        movie = make_movie()

        # Filtered endpoint fails
        radarr.session.get = MagicMock(side_effect=Exception('timeout'))

        # Fallback: get_movies returns all
        with patch.object(radarr, 'get_movies', return_value=[movie]):
            result = radarr.get_movie_by_tmdb_id(550)

        self.assertIsNotNone(result)
        self.assertEqual(result['tmdbId'], 550)


if __name__ == '__main__':
    unittest.main()
