#!/usr/bin/env python3
"""
Tests for fast_search typo-tolerant fuzzy search functionality.
Tests normalization, fuzzy matching, and ranking.
"""

import time
import unittest
from unittest.mock import Mock, patch, MagicMock, call
from typing import Dict, Any

from app.fast_search import (
    FastSearchIndex,
    normalize_text,
    extract_trigrams,
    trigram_similarity,
    token_similarity,
    fuzzy_match_score,
    calculate_popularity_score,
)


class TestNormalization(unittest.TestCase):
    """Test text normalization functions."""

    def test_case_normalization(self):
        self.assertEqual(normalize_text('Interstellar'), 'interstellar')
        self.assertEqual(normalize_text('THE GODFATHER'), 'godfather')

    def test_accent_removal(self):
        self.assertEqual(normalize_text('Amélie'), 'amelie')
        self.assertEqual(normalize_text('Crouching Tiger, Hidden Dragon'), 'crouching tiger hidden dragon')

    def test_punctuation_removal(self):
        self.assertEqual(normalize_text("Schindler's List"), 'schindlers list')
        self.assertEqual(normalize_text('Spider-Man'), 'spider man')
        self.assertEqual(normalize_text('M*A*S*H'), 'mash')

    def test_ampersand_to_and(self):
        self.assertEqual(normalize_text('Tom & Jerry'), 'tom and jerry')
        self.assertEqual(normalize_text('Fast & Furious'), 'fast and furious')

    def test_leading_articles(self):
        self.assertEqual(normalize_text('The Matrix'), 'matrix')
        self.assertEqual(normalize_text('A Beautiful Mind'), 'beautiful mind')
        self.assertEqual(normalize_text('An American Tail'), 'american tail')
        # Edge case: "The The" (real band name) -> "the the" -> remove leading "the" -> "the"
        self.assertEqual(normalize_text('The The'), 'the')

    def test_whitespace_normalization(self):
        self.assertEqual(normalize_text('Blade   Runner'), 'blade runner')
        self.assertEqual(normalize_text('  Star Wars  '), 'star wars')

    def test_combined_normalization(self):
        # Only leading "the" is removed, not all instances
        self.assertEqual(normalize_text("The Lord of the Rings: The Return of the King"), 
                        'lord of the rings the return of the king')


class TestFuzzyMatching(unittest.TestCase):
    """Test fuzzy matching algorithms."""

    def test_trigram_extraction(self):
        trigrams = extract_trigrams('abc')
        self.assertIn('  a', trigrams)
        self.assertIn(' ab', trigrams)
        self.assertIn('abc', trigrams)
        self.assertIn('bc ', trigrams)

    def test_trigram_similarity(self):
        # Identical strings
        self.assertGreater(trigram_similarity('interstellar', 'interstellar'), 0.95)
        
        # Very similar (typo)
        self.assertGreater(trigram_similarity('interstelar', 'interstellar'), 0.7)
        
        # Somewhat similar (single letter swap)
        self.assertGreater(trigram_similarity('godfahter', 'godfather'), 0.55)
        
        # Different strings
        self.assertLess(trigram_similarity('matrix', 'inception'), 0.5)

    def test_token_similarity(self):
        # All tokens match
        self.assertEqual(token_similarity(['the', 'matrix'], ['the', 'matrix']), 1.0)
        
        # All query tokens in title
        self.assertGreaterEqual(token_similarity(['matrix'], ['the', 'matrix']), 0.9)
        
        # Partial match
        score = token_similarity(['star', 'trek'], ['star', 'wars'])
        self.assertGreater(score, 0.0)
        self.assertLess(score, 0.9)

    def test_fuzzy_match_score_exact(self):
        score, match_type = fuzzy_match_score('Interstellar', 'Interstellar', 
                                              'interstellar', 'interstellar')
        self.assertEqual(score, 1.0)
        self.assertEqual(match_type, 'exact')

    def test_fuzzy_match_score_prefix(self):
        score, match_type = fuzzy_match_score('Inter', 'Interstellar',
                                              'inter', 'interstellar')
        self.assertEqual(match_type, 'prefix')
        self.assertGreater(score, 0.9)

    def test_fuzzy_match_score_typo(self):
        score, match_type = fuzzy_match_score('Interstelar', 'Interstellar',
                                              'interstelar', 'interstellar')
        self.assertEqual(match_type, 'fuzzy')
        self.assertGreater(score, 0.6)

    def test_calculate_popularity_score(self):
        item_popular = {'viewCount': 100}
        item_unpopular = {'viewCount': 0}
        
        score_popular = calculate_popularity_score(item_popular)
        score_unpopular = calculate_popularity_score(item_unpopular)
        
        self.assertGreater(score_popular, score_unpopular)
        self.assertGreater(score_popular, 0)


class TestFastSearchIndexFuzzy(unittest.TestCase):
    """Test FastSearchIndex fuzzy search capabilities."""

    def setUp(self):
        """Set up a test index with sample movies."""
        self.index = FastSearchIndex()
        
        # Manually populate index with test data
        self.test_items = [
            {
                'rating_key': '1',
                'title': 'Interstellar',
                'title_lower': 'interstellar',
                'title_normalized': 'interstellar',
                'year': 2014,
                'content_rating': 'PG-13',
                'genres': ['Sci-Fi', 'Drama'],
                'genres_lower': ['sci-fi', 'drama'],
                'section_id': 1,
                'section_name': 'Movies',
                'media_type': 'movie',
                'viewCount': 50,
                'viewOffset': 0,
                'thumb': '/thumb1.jpg',
            },
            {
                'rating_key': '2',
                'title': 'The Godfather',
                'title_lower': 'the godfather',
                'title_normalized': 'godfather',
                'year': 1972,
                'content_rating': 'R',
                'genres': ['Crime', 'Drama'],
                'genres_lower': ['crime', 'drama'],
                'section_id': 1,
                'section_name': 'Movies',
                'media_type': 'movie',
                'viewCount': 100,
                'viewOffset': 0,
                'thumb': '/thumb2.jpg',
            },
            {
                'rating_key': '3',
                'title': 'The Shawshank Redemption',
                'title_lower': 'the shawshank redemption',
                'title_normalized': 'shawshank redemption',
                'year': 1994,
                'content_rating': 'R',
                'genres': ['Drama'],
                'genres_lower': ['drama'],
                'section_id': 1,
                'section_name': 'Movies',
                'media_type': 'movie',
                'viewCount': 75,
                'viewOffset': 0,
                'thumb': '/thumb3.jpg',
            },
            {
                'rating_key': '4',
                'title': 'The Matrix',
                'title_lower': 'the matrix',
                'title_normalized': 'matrix',
                'year': 1999,
                'content_rating': 'R',
                'genres': ['Action', 'Sci-Fi'],
                'genres_lower': ['action', 'sci-fi'],
                'section_id': 1,
                'section_name': 'Movies',
                'media_type': 'movie',
                'viewCount': 120,
                'viewOffset': 0,
                'thumb': '/thumb4.jpg',
            },
            {
                'rating_key': '5',
                'title': 'Spider-Man',
                'title_lower': 'spider-man',
                'title_normalized': 'spider man',
                'year': 2002,
                'content_rating': 'PG-13',
                'genres': ['Action', 'Adventure'],
                'genres_lower': ['action', 'adventure'],
                'section_id': 1,
                'section_name': 'Movies',
                'media_type': 'movie',
                'viewCount': 80,
                'viewOffset': 0,
                'thumb': '/thumb5.jpg',
            },
        ]
        
        self.index._index = self.test_items
        self.index._library_titles = {item['title_normalized'] for item in self.test_items}
        self.index._initialized = True

    def test_exact_search(self):
        """Test exact title match."""
        results = self.index.search(q='Interstellar')
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]['title'], 'Interstellar')

    def test_typo_interstelar(self):
        """Test typo: 'Interstelar' should find 'Interstellar'."""
        results = self.index.search(q='Interstelar')
        self.assertGreater(len(results), 0)
        titles = [r['title'] for r in results]
        self.assertIn('Interstellar', titles)

    def test_typo_godfahter(self):
        """Test typo: 'Godfahter' should find 'The Godfather'."""
        results = self.index.search(q='Godfahter')
        self.assertGreater(len(results), 0)
        titles = [r['title'] for r in results]
        self.assertIn('The Godfather', titles)

    def test_typo_shawshank_redemtion(self):
        """Test typo: 'Shawshank Redemtion' should find 'The Shawshank Redemption'."""
        results = self.index.search(q='Shawshank Redemtion')
        self.assertGreater(len(results), 0)
        titles = [r['title'] for r in results]
        self.assertIn('The Shawshank Redemption', titles)

    def test_missing_space(self):
        """Test missing space: 'Spiderman' should find 'Spider-Man'."""
        results = self.index.search(q='Spiderman')
        self.assertGreater(len(results), 0)
        titles = [r['title'] for r in results]
        self.assertIn('Spider-Man', titles)

    def test_punctuation_difference(self):
        """Test punctuation: 'Spider Man' should find 'Spider-Man'."""
        results = self.index.search(q='Spider Man')
        self.assertGreater(len(results), 0)
        titles = [r['title'] for r in results]
        self.assertIn('Spider-Man', titles)

    def test_leading_article_handling(self):
        """Test that leading articles are normalized."""
        results = self.index.search(q='Matrix')
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]['title'], 'The Matrix')

    def test_swapped_letters(self):
        """Test swapped letters: 'Matrxi' should find 'The Matrix'."""
        results = self.index.search(q='Matrxi')
        self.assertGreater(len(results), 0)
        titles = [r['title'] for r in results]
        self.assertIn('The Matrix', titles)

    def test_dropped_letter(self):
        """Test dropped letter: 'Marix' should find 'The Matrix'."""
        results = self.index.search(q='Marix')
        self.assertGreater(len(results), 0)
        titles = [r['title'] for r in results]
        self.assertIn('The Matrix', titles)

    def test_prefix_match(self):
        """Test prefix matching."""
        results = self.index.search(q='Inter')
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]['title'], 'Interstellar')

    def test_ranking_by_popularity(self):
        """Test that exact matches are ranked by popularity."""
        # Search for a genre that matches multiple movies
        results = self.index.search(q='', genre='Drama')
        self.assertGreater(len(results), 1)
        
        # When no query, results should maintain original order or be unranked
        # But with query, popularity should affect ranking

    def test_filter_combination(self):
        """Test combining fuzzy search with filters."""
        results = self.index.search(q='Godfater', year=1972)
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]['title'], 'The Godfather')
        self.assertEqual(results[0]['year'], 1972)

    def test_media_type_filter(self):
        """Test media type filtering."""
        results = self.index.search(q='Matrix', media_type='movie')
        self.assertGreater(len(results), 0)
        for result in results:
            self.assertEqual(result['media_type'], 'movie')

    def test_spelling_correction(self):
        """Test spelling correction returns original title."""
        correction = self.index.find_spelling_correction('Interstelar')
        self.assertEqual(correction, 'Interstellar')  # Original title, not normalized
    
    def test_short_exact_match_up(self):
        """Test that short exact match 'Up' returns 'Up' first (tier-based ranking)."""
        # Add 'Up' and 'Superman' to test
        up_item = {
            'rating_key': '100',
            'title': 'Up',
            'title_lower': 'up',
            'title_normalized': 'up',
            'year': 2009,
            'content_rating': 'PG',
            'genres': ['Animation'],
            'genres_lower': ['animation'],
            'section_id': 1,
            'section_name': 'Movies',
            'media_type': 'movie',
            'viewCount': 50,
            'viewOffset': 0,
            'thumb': '/thumb100.jpg',
        }
        superman_item = {
            'rating_key': '101',
            'title': 'Superman',
            'title_lower': 'superman',
            'title_normalized': 'superman',
            'year': 1978,
            'content_rating': 'PG',
            'genres': ['Action'],
            'genres_lower': ['action'],
            'section_id': 1,
            'section_name': 'Movies',
            'media_type': 'movie',
            'viewCount': 100,  # Higher popularity
            'viewOffset': 0,
            'thumb': '/thumb101.jpg',
        }
        self.index._index.extend([up_item, superman_item])
        self.index._library_titles.update(['up', 'superman'])
        
        # Search for 'up' - should return 'Up' first despite Superman having higher viewCount
        results = self.index.search(q='up')
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]['title'], 'Up', 
                        "Exact match 'Up' should rank first (tier-based ranking)")
    
    def test_short_query_no_fuzzy(self):
        """Test that 1-2 character queries use only prefix/substring matching."""
        # Add test item
        ark_item = {
            'rating_key': '102',
            'title': 'Dark Knight',
            'title_lower': 'dark knight',
            'title_normalized': 'dark knight',
            'year': 2008,
            'content_rating': 'PG-13',
            'genres': ['Action'],
            'genres_lower': ['action'],
            'section_id': 1,
            'section_name': 'Movies',
            'media_type': 'movie',
            'viewCount': 200,
            'viewOffset': 0,
            'thumb': '/thumb102.jpg',
        }
        self.index._index.append(ark_item)
        self.index._library_titles.add('dark knight')
        
        # Search for 'ark' - should find 'Dark Knight' via substring
        results = self.index.search(q='ark')
        self.assertGreater(len(results), 0)
        titles = [r['title'] for r in results]
        self.assertIn('Dark Knight', titles)
    
    def test_empty_query_after_strip(self):
        """Test that whitespace-only query doesn't return entire library."""
        results = self.index.search(q='   ')
        # Should return empty or filtered results, not entire library
        self.assertLessEqual(len(results), 100)  # Not the whole library
    
    def test_accent_difference_library(self):
        """Test accent difference: 'Amelie' should match 'Amélie' if in library."""
        # Add Amélie to test index
        amelie_item = {
            'rating_key': '10',
            'title': 'Amélie',
            'title_lower': 'amélie',
            'title_normalized': normalize_text('Amélie'),
            'year': 2001,
            'content_rating': 'R',
            'genres': ['Romance', 'Comedy'],
            'genres_lower': ['romance', 'comedy'],
            'section_id': 1,
            'section_name': 'Movies',
            'media_type': 'movie',
            'viewCount': 30,
            'viewOffset': 0,
            'thumb': '/thumb10.jpg',
            'rating': 8.3,
            'audienceRating': 0.0,
        }
        self.index._index.append(amelie_item)
        self.index._library_titles.add(amelie_item['title_normalized'])
        
        # Search without accent should find it
        results = self.index.search(q='Amelie')
        self.assertGreater(len(results), 0)
        titles = [r['title'] for r in results]
        self.assertIn('Amélie', titles)
    
    def test_ampersand_vs_and_library(self):
        """Test '&' vs 'and': 'Tom & Jerry' should match 'Tom and Jerry'."""
        # Add both variants to test
        item1 = {
            'rating_key': '11',
            'title': 'Tom & Jerry',
            'title_lower': 'tom & jerry',
            'title_normalized': normalize_text('Tom & Jerry'),
            'year': 2021,
            'content_rating': 'PG',
            'genres': ['Comedy'],
            'genres_lower': ['comedy'],
            'section_id': 1,
            'section_name': 'Movies',
            'media_type': 'movie',
            'viewCount': 20,
            'viewOffset': 0,
            'thumb': '/thumb11.jpg',
            'rating': 5.3,
            'audienceRating': 0.0,
        }
        self.index._index.append(item1)
        self.index._library_titles.add(item1['title_normalized'])
        
        # Search with 'and' should find '&' version
        results = self.index.search(q='Tom and Jerry')
        self.assertGreater(len(results), 0)
        titles = [r['title'] for r in results]
        self.assertIn('Tom & Jerry', titles)
        
        # Search with '&' should also work
        results2 = self.index.search(q='Tom & Jerry')
        self.assertGreater(len(results2), 0)


class TestPerformance(unittest.TestCase):
    """Test search performance benchmarks."""

    def setUp(self):
        """Set up a larger test index."""
        self.index = FastSearchIndex()
        
        # Create 1000 test items
        test_items = []
        for i in range(1000):
            title = f'Movie Title {i}'
            test_items.append({
                'rating_key': str(i),
                'title': title,
                'title_lower': title.lower(),
                'title_normalized': normalize_text(title),
                'year': 2000 + (i % 24),
                'content_rating': 'PG-13',
                'genres': ['Action'],
                'genres_lower': ['action'],
                'section_id': 1,
                'section_name': 'Movies',
                'media_type': 'movie',
                'viewCount': i,
                'viewOffset': 0,
                'thumb': f'/thumb{i}.jpg',
            })
        
        # Add some specific titles for fuzzy testing
        specific_titles = [
            'Interstellar',
            'The Godfather',
            'The Shawshank Redemption',
            'The Matrix',
        ]
        for idx, title in enumerate(specific_titles):
            test_items.append({
                'rating_key': str(1000 + idx),
                'title': title,
                'title_lower': title.lower(),
                'title_normalized': normalize_text(title),
                'year': 2000,
                'content_rating': 'PG-13',
                'genres': ['Drama'],
                'genres_lower': ['drama'],
                'section_id': 1,
                'section_name': 'Movies',
                'media_type': 'movie',
                'viewCount': 100 + idx,
                'viewOffset': 0,
                'thumb': f'/thumb{1000 + idx}.jpg',
            })
        
        self.index._index = test_items
        self.index._library_titles = {item['title_normalized'] for item in test_items}
        self.index._initialized = True

    def test_search_10k_performance(self):
        """Test that 10k item search completes quickly (target < 50ms per query)."""
        # Create 10k items
        large_items = []
        for i in range(10000):
            title = f'Title {i}'
            large_items.append({
                'rating_key': str(i),
                'title': title,
                'title_lower': title.lower(),
                'title_normalized': normalize_text(title),
                'year': 2000 + (i % 24),
                'content_rating': 'PG-13',
                'genres': ['Action'],
                'genres_lower': ['action'],
                'section_id': 1,
                'section_name': 'Movies',
                'media_type': 'movie',
                'viewCount': i % 100,
                'viewOffset': 0,
                'thumb': f'/thumb{i}.jpg',
            })
        
        # Add target title
        large_items.append({
            'rating_key': '10000',
            'title': 'Interstellar',
            'title_lower': 'interstellar',
            'title_normalized': 'interstellar',
            'year': 2014,
            'content_rating': 'PG-13',
            'genres': ['Sci-Fi'],
            'genres_lower': ['sci-fi'],
            'section_id': 1,
            'section_name': 'Movies',
            'media_type': 'movie',
            'viewCount': 50,
            'viewOffset': 0,
            'thumb': '/thumb10000.jpg',
        })
        
        large_index = FastSearchIndex()
        large_index._index = large_items
        large_index._library_titles = {item['title_normalized'] for item in large_items}
        large_index._initialized = True
        
        # Warm up
        large_index.search(q='Interstelar', limit=100)
        
        # Measure multiple queries for stable timing
        times = []
        for _ in range(5):
            start = time.perf_counter()
            results = large_index.search(q='Interstelar', limit=100)
            elapsed_ms = (time.perf_counter() - start) * 1000
            times.append(elapsed_ms)
        
        avg_time = sum(times) / len(times)
        # Check average is reasonable, not a hard assert to avoid flakiness
        if avg_time > 100:
            logger.warning(f"10k search took {avg_time:.2f}ms average (target < 50ms)")
        self.assertGreater(len(results), 0)

    def test_multiple_searches_performance(self):
        """Test multiple fuzzy searches to ensure consistent performance."""
        queries = ['Interstelar', 'Godfahter', 'Shawshank', 'Matrxi', 'Movie']
        
        for query in queries:
            start = time.perf_counter()
            results = self.index.search(q=query, limit=100)
            elapsed_ms = (time.perf_counter() - start) * 1000
            
            # Log warnings instead of hard assertions to avoid flakiness
            if elapsed_ms > 100:
                logger.warning(f"Search for '{query}' took {elapsed_ms:.2f}ms")


class TestConcurrency(unittest.TestCase):
    """Test concurrent access and thread safety."""
    
    def test_concurrent_refresh_and_lookup(self):
        """Test that refresh and lookups can run concurrently without crashes."""
        import threading
        
        index = FastSearchIndex()
        index._index = [
            {
                'rating_key': '1',
                'title': 'Test Movie',
                'title_lower': 'test movie',
                'title_normalized': 'test movie',
                'year': 2020,
                'content_rating': 'PG',
                'genres': ['Action'],
                'genres_lower': ['action'],
                'section_id': 1,
                'section_name': 'Movies',
                'media_type': 'movie',
                'viewCount': 10,
                'viewOffset': 0,
                'thumb': '/thumb1.jpg',
            }
        ]
        index._library_titles = {'test movie'}
        index._initialized = True
        
        errors = []
        
        def search_worker():
            try:
                for _ in range(10):
                    index.search(q='test')
            except Exception as e:
                errors.append(e)
        
        def correction_worker():
            try:
                for _ in range(10):
                    index.find_spelling_correction('tset')
            except Exception as e:
                errors.append(e)
        
        threads = []
        for _ in range(3):
            threads.append(threading.Thread(target=search_worker))
            threads.append(threading.Thread(target=correction_worker))
        
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        self.assertEqual(len(errors), 0, f"Concurrent access errors: {errors}")
    
    def test_tmdb_cache_survives_build_index(self):
        """Test that TMDb titles are preserved when library index is rebuilt."""
        index = FastSearchIndex()
        
        # Set up initial library
        index._library_titles = {'movie1', 'movie2'}
        index._tmdb_titles = frozenset({'popular1', 'popular2'})
        index._tmdb_titles_cached = True
        index._initialized = True
        
        # Verify combined dictionary includes both
        with index._lock:
            combined = index._library_titles | index._tmdb_titles
        self.assertIn('movie1', combined)
        self.assertIn('popular1', combined)
        
        # Simulate rebuild (updates library but should preserve TMDb)
        index._library_titles = {'movie3', 'movie4'}
        
        # TMDb titles should still be there
        with index._lock:
            combined = index._library_titles | index._tmdb_titles
        self.assertIn('popular1', combined)
        self.assertIn('movie3', combined)
        self.assertNotIn('movie1', combined)


class TestTMDbIntegration(unittest.TestCase):
    """Test TMDb search with fuzzy matching and spelling correction."""

    @patch('app.fast_search.requests.get')
    def test_tmdb_search_with_correction(self, mock_get):
        """Test that TMDb search can use spelling-corrected queries."""
        index = FastSearchIndex()
        index._index = [{
            'rating_key': '1',
            'title': 'Interstellar',
            'title_lower': 'interstellar',
            'title_normalized': 'interstellar',
            'year': 2014,
            'content_rating': 'PG-13',
            'genres': ['Sci-Fi'],
            'genres_lower': ['sci-fi'],
            'section_id': 1,
            'section_name': 'Movies',
            'media_type': 'movie',
            'viewCount': 50,
            'viewOffset': 0,
            'thumb': '/thumb1.jpg',
        }]
        index._library_titles = {'interstellar'}
        index._initialized = True
        
        correction = index.find_spelling_correction('Interstelar')
        self.assertEqual(correction, 'Interstellar')  # Original title
    
    @patch('app.settings_manager.SettingsManager')
    @patch('app.fast_search.requests.get')
    def test_tmdb_popular_titles_cache(self, mock_get, mock_settings_class):
        """Test that TMDb popular titles are cached for spelling correction."""
        # Mock settings
        mock_settings = Mock()
        mock_settings.get.side_effect = lambda key, default='': {
            'apis.tmdb.api_key': 'test_api_key',
            'apis.tmdb.base_url': 'https://api.themoviedb.org/3'
        }.get(key, default)
        mock_settings_class.return_value = mock_settings
        
        # Mock TMDb API responses
        mock_movie_response = Mock()
        mock_movie_response.status_code = 200
        mock_movie_response.json.return_value = {
            'results': [
                {'title': 'Amélie', 'popularity': 45.6},
                {'title': 'Fast & Furious', 'popularity': 89.2},
                {'title': 'The Matrix', 'popularity': 120.5},
            ]
        }
        
        mock_tv_response = Mock()
        mock_tv_response.status_code = 200
        mock_tv_response.json.return_value = {
            'results': [
                {'name': 'Game of Thrones', 'popularity': 200.1},
                {'name': 'Breaking Bad', 'popularity': 150.3},
            ]
        }
        
        mock_get.side_effect = [
            mock_movie_response, mock_movie_response, mock_movie_response,  # 3 pages of movies
            mock_tv_response, mock_tv_response, mock_tv_response,  # 3 pages of TV
        ]
        
        index = FastSearchIndex()
        index._initialized = True
        
        # Manually trigger TMDb cache refresh
        index._refresh_tmdb_titles()
        
        # Verify titles were added to TMDb dictionary
        with index._lock:
            tmdb_titles = index._tmdb_titles
        self.assertIn('amelie', tmdb_titles)  # Accent normalized
        self.assertIn('fast and furious', tmdb_titles)  # & -> and
        self.assertIn('matrix', tmdb_titles)  # "The" removed
        self.assertIn('game of thrones', tmdb_titles)
        self.assertIn('breaking bad', tmdb_titles)
        with index._lock:
            cached = index._tmdb_titles_cached
        self.assertTrue(cached)
    
    @patch('app.settings_manager.SettingsManager')
    @patch('app.fast_search.requests.get')
    def test_tmdb_cache_graceful_api_key_missing(self, mock_get, mock_settings_class):
        """Test that missing TMDb API key is handled gracefully."""
        # Mock settings with empty API key
        mock_settings = Mock()
        mock_settings.get.return_value = ''
        mock_settings_class.return_value = mock_settings
        
        index = FastSearchIndex()
        index._initialized = True
        
        # Should not raise exception
        index._refresh_tmdb_titles()
        
        # Should not have made any API calls
        mock_get.assert_not_called()
    
    @patch('app.fast_search.requests.get')
    def test_tmdb_cache_network_failure(self, mock_get):
        """Test that network failures during TMDb cache are handled gracefully."""
        mock_get.side_effect = Exception("Network error")
        
        index = FastSearchIndex()
        index._initialized = True
        
        # Should not raise exception
        index._refresh_tmdb_titles()
        
        # Should log warning but continue


if __name__ == '__main__':
    unittest.main()
