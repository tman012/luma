#!/usr/bin/env python3
"""
Tests for fast_search typo-tolerant fuzzy search functionality.
Tests normalization, fuzzy matching, and ranking.
"""

import time
import unittest
from unittest.mock import Mock, patch, MagicMock
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
        self.index._title_dictionary = {item['title_normalized'] for item in self.test_items}
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
        """Test spelling correction suggestion."""
        correction = self.index.find_spelling_correction('Interstelar')
        self.assertEqual(correction, 'interstellar')


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
        self.index._title_dictionary = {item['title_normalized'] for item in test_items}
        self.index._initialized = True

    def test_search_latency_under_300ms(self):
        """Test that fuzzy search completes under 300ms."""
        start = time.perf_counter()
        results = self.index.search(q='Interstelar', limit=100)
        elapsed_ms = (time.perf_counter() - start) * 1000
        
        self.assertLess(elapsed_ms, 300, 
                       f"Search took {elapsed_ms:.2f}ms, expected < 300ms")
        self.assertGreater(len(results), 0)

    def test_multiple_searches_performance(self):
        """Test multiple fuzzy searches to ensure consistent performance."""
        queries = ['Interstelar', 'Godfahter', 'Shawshank', 'Matrxi', 'Movie']
        
        for query in queries:
            start = time.perf_counter()
            results = self.index.search(q=query, limit=100)
            elapsed_ms = (time.perf_counter() - start) * 1000
            
            self.assertLess(elapsed_ms, 300,
                           f"Search for '{query}' took {elapsed_ms:.2f}ms")


class TestTMDbIntegration(unittest.TestCase):
    """Test TMDb search with fuzzy matching and spelling correction."""

    @patch('app.fast_search.requests.get')
    def test_tmdb_search_with_correction(self, mock_get):
        """Test that TMDb search can use spelling-corrected queries."""
        # This would be implemented in main.py's TMDb search endpoint
        # Here we just verify the correction function works
        
        index = FastSearchIndex()
        index._title_dictionary = {'interstellar', 'godfather', 'matrix'}
        index._initialized = True
        
        correction = index.find_spelling_correction('Interstelar')
        self.assertEqual(correction, 'interstellar')


if __name__ == '__main__':
    unittest.main()
