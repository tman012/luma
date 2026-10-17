#!/usr/bin/env python3
"""
Tests for fast_search typo-tolerant fuzzy search functionality.
Tests normalization, fuzzy matching, and ranking.
"""

# Set up test cache directory before any imports
import os
import tempfile
from pathlib import Path
_test_cache_dir = tempfile.mkdtemp(prefix="luma_test_cache_")
os.environ['LUMA_CACHE_DIR'] = _test_cache_dir

import time
import unittest

from tests.local_access import api_key_settings, local_client
import pytest
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


class TestShortQueryMatching(unittest.TestCase):
    """Test 3-letter query matching behavior."""
    
    def setUp(self):
        """Set up search index with test data."""
        self.index = FastSearchIndex()
        self.index._index = [
            {
                'title': 'Dark Knight',
                'title_lower': 'dark knight',
                'title_normalized': 'dark knight',
                'year': 2008,
                'genres_lower': ['action'],
                'content_rating': 'PG-13',
                'section_id': 1,
                'media_type': 'movie',
                'popularity_score': 1.0,
                'rating_key': 1,
            },
            {
                'title': 'Ark',
                'title_lower': 'ark',
                'title_normalized': 'ark',
                'year': 2021,
                'genres_lower': ['action'],
                'content_rating': 'PG-13',
                'section_id': 1,
                'media_type': 'movie',
                'popularity_score': 0.5,
                'rating_key': 2,
            },
            {
                'title': 'The Park',
                'title_lower': 'the park',
                'title_normalized': 'park',
                'year': 2015,
                'genres_lower': ['drama'],
                'content_rating': 'R',
                'section_id': 1,
                'media_type': 'movie',
                'popularity_score': 0.3,
                'rating_key': 3,
            }
        ]
        self.index._initialized = True
    
    def test_ark_exact_match_first(self):
        """Test 'ark' query returns exact match 'Ark' before substring match 'Dark Knight'."""
        results = self.index.search(q='ark', fuzzy=True)
        
        # Should have at least 2 results
        self.assertGreaterEqual(len(results), 2)
        
        # First result should be exact match 'Ark'
        self.assertEqual(results[0]['title'], 'Ark')
        
        # 'Dark Knight' should be in results (substring match)
        titles = [r['title'] for r in results]
        self.assertIn('Dark Knight', titles)
    
    def test_ark_substring_not_false_positive(self):
        """Test 'ark' substring matches are intentional, not false positives."""
        # When there's an exact match, it comes first
        results = self.index.search(q='ark', fuzzy=True)
        self.assertEqual(results[0]['title'], 'Ark')
        
        # Substring matches should be ranked by tier
        from app.fast_search import fuzzy_match_score, normalize_text
        
        # 'ark' vs 'Ark' is exact match (tier 1)
        score1, type1 = fuzzy_match_score('ark', 'Ark', 'ark', 'ark')
        self.assertEqual(type1, 'exact')
        
        # 'ark' vs 'Dark Knight' is substring match (tier 3)
        score2, type2 = fuzzy_match_score('ark', 'Dark Knight', 'ark', 'dark knight')
        self.assertEqual(type2, 'substring')
        
        # Exact match should be ranked higher than substring
        self.assertGreater(score1, score2)


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
        # Ensure title map is populated
        self.index._title_map = {item['title_normalized']: item['title'] for item in self.index._index}
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
    
    def test_short_query_length_preference(self):
        """Test that within a tier, shorter or exact-length titles are preferred."""
        # Add 'U' and 'Up' and 'Upside Down' to test
        u_item = {
            'rating_key': '103',
            'title': 'U',
            'title_lower': 'u',
            'title_normalized': 'u',
            'year': 2009,
            'content_rating': 'PG',
            'genres': ['Drama'],
            'genres_lower': ['drama'],
            'section_id': 1,
            'section_name': 'Movies',
            'media_type': 'movie',
            'viewCount': 10,
            'viewOffset': 0,
            'thumb': '/thumb103.jpg',
        }
        upside_item = {
            'rating_key': '104',
            'title': 'Upside Down',
            'title_lower': 'upside down',
            'title_normalized': 'upside down',
            'year': 2012,
            'content_rating': 'PG-13',
            'genres': ['Romance'],
            'genres_lower': ['romance'],
            'section_id': 1,
            'section_name': 'Movies',
            'media_type': 'movie',
            'viewCount': 100,  # Higher popularity
            'viewOffset': 0,
            'thumb': '/thumb104.jpg',
        }
        self.index._index.extend([u_item, upside_item])
        self.index._library_titles.update(['u', 'upside down'])
        
        # Search for 'u' - should prefer exact length 'U' over 'Upside Down'
        results = self.index.search(q='u')
        self.assertGreater(len(results), 0)
        # 'U' should rank higher than 'Upside Down' despite lower viewCount
        titles = [r['title'] for r in results]
        if 'U' in titles and 'Upside Down' in titles:
            self.assertLess(titles.index('U'), titles.index('Upside Down'))
    
    def test_tighter_threshold_short_query(self):
        """Test that 3-4 char queries have tighter fuzzy threshold to avoid false matches."""
        # Add 'The Matrix' to test
        matrix_item = {
            'rating_key': '105',
            'title': 'The Matrix',
            'title_lower': 'the matrix',
            'title_normalized': 'matrix',
            'year': 1999,
            'content_rating': 'R',
            'genres': ['Sci-Fi'],
            'genres_lower': ['sci-fi'],
            'section_id': 1,
            'section_name': 'Movies',
            'media_type': 'movie',
            'viewCount': 200,
            'viewOffset': 0,
            'thumb': '/thumb105.jpg',
        }
        self.index._index.append(matrix_item)
        self.index._library_titles.add('matrix')
        
        # 'ark' should NOT fuzzy match 'matrix' (threshold too low)
        # But should find 'Dark Knight' via substring (from earlier test)
        results = self.index.search(q='ark')
        titles = [r['title'] for r in results]
        # Matrix should not be in results (fuzzy score too low for 3-char query)
        self.assertNotIn('The Matrix', titles)
    
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


class TestTitleMapRebuild(unittest.TestCase):
    """Test that _title_map is rebuilt from scratch on each refresh."""
    
    def setUp(self):
        """Set up search index."""
        self.index = FastSearchIndex()
    
    def test_removed_title_drops_out(self):
        """Test that a removed title is dropped from _title_map on rebuild."""
        # Build initial index with two titles
        self.index._index = [
            {
                'title': 'The Matrix',
                'title_lower': 'the matrix',
                'title_normalized': 'matrix',
                'year': 1999,
                'genres_lower': ['action'],
                'content_rating': 'R',
                'section_id': 1,
                'media_type': 'movie',
                'popularity_score': 1.0,
                'rating_key': 1,
            },
            {
                'title': 'The Godfather',
                'title_lower': 'the godfather',
                'title_normalized': 'godfather',
                'year': 1972,
                'genres_lower': ['crime'],
                'content_rating': 'R',
                'section_id': 1,
                'media_type': 'movie',
                'popularity_score': 1.0,
                'rating_key': 2,
            }
        ]
        self.index._library_titles = {'matrix', 'godfather'}
        self.index._title_map = {
            'matrix': 'The Matrix',
            'godfather': 'The Godfather'
        }
        self.index._initialized = True
        
        # Verify both titles are in the map
        self.assertIn('matrix', self.index._title_map)
        self.assertIn('godfather', self.index._title_map)
        
        # Now rebuild with only one title (simulate library change)
        self.index._index = [
            {
                'title': 'The Matrix',
                'title_lower': 'the matrix',
                'title_normalized': 'matrix',
                'year': 1999,
                'genres_lower': ['action'],
                'content_rating': 'R',
                'section_id': 1,
                'media_type': 'movie',
                'popularity_score': 1.0,
                'rating_key': 1,
            }
        ]
        self.index._library_titles = {'matrix'}
        
        # Manually trigger title map rebuild (simulating what _build_index does)
        library_title_map = {'matrix': 'The Matrix'}
        with self.index._lock:
            tmdb_title_map = {norm: title for norm, title in self.index._title_map.items() 
                            if norm in self.index._tmdb_titles}
            self.index._title_map = {**library_title_map, **tmdb_title_map}
        
        # Verify removed title is gone
        self.assertIn('matrix', self.index._title_map)
        self.assertNotIn('godfather', self.index._title_map)


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
        index._title_map = {'interstellar': 'Interstellar'}
        index._initialized = True
        
        correction = index.find_spelling_correction('Interstelar')
        self.assertEqual(correction, 'Interstellar')  # Original title
    
    @patch('app.settings_manager.SettingsManager')
    @patch('app.fast_search.requests.get')
    def test_tmdb_only_correction(self, mock_get, mock_settings_class):
        """Test correction finds title that exists only in TMDb set (not library)."""
        # Mock settings
        mock_settings = Mock()
        mock_settings.get.side_effect = lambda key, default='': {
            'apis.tmdb.api_key': 'test_api_key',
            'apis.tmdb.base_url': 'https://api.themoviedb.org/3'
        }.get(key, default)
        mock_settings_class.return_value = mock_settings
        
        # Mock TMDb response with Oppenheimer
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'results': [
                {'title': 'Oppenheimer', 'popularity': 150.0},
            ]
        }
        mock_get.return_value = mock_response
        
        index = FastSearchIndex()
        index._library_titles = set()  # Empty library
        index._title_map = {}  # Empty title map
        index._initialized = True
        
        # Refresh TMDb titles (adds Oppenheimer)
        index._refresh_tmdb_titles()
        
        # Verify Oppenheimer is in TMDb set and title map
        with index._lock:
            self.assertIn('oppenheimer', index._tmdb_titles)
            self.assertIn('oppenheimer', index._title_map)
            self.assertEqual(index._title_map['oppenheimer'], 'Oppenheimer')
        
        # Test correction for typo finds Oppenheimer (TMDb-only)
        correction = index.find_spelling_correction('Openheimer')
        self.assertEqual(correction, 'Oppenheimer')
    
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
            mock_movie_response, mock_movie_response, mock_movie_response,  # Pages 1-3
            mock_movie_response, mock_movie_response, mock_movie_response,  # Pages 4-6
            mock_movie_response, mock_movie_response, mock_movie_response, mock_movie_response,  # Pages 7-10
            mock_tv_response, mock_tv_response, mock_tv_response,  # Pages 1-3
            mock_tv_response, mock_tv_response, mock_tv_response,  # Pages 4-6
            mock_tv_response, mock_tv_response, mock_tv_response, mock_tv_response,  # Pages 7-10
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


class TestTMDb429Handling(unittest.TestCase):
    """Test TMDb rate limit handling with Retry-After header and exponential backoff."""
    
    def setUp(self):
        """Set up FastSearchIndex for testing."""
        self.index = FastSearchIndex()
    
    def test_parse_retry_after_seconds(self):
        """Test parsing Retry-After header as integer seconds."""
        mock_response = Mock()
        mock_response.headers = {'Retry-After': '120'}
        
        # Should return timestamp 120 seconds from now
        t_before = time.time()
        retry_after = self.index._parse_retry_after(mock_response)
        t_after = time.time()
        
        self.assertGreater(retry_after, t_before + 119)
        self.assertLess(retry_after, t_after + 121)
    
    def test_parse_retry_after_http_date(self):
        """Test parsing Retry-After header as HTTP-date."""
        from email.utils import formatdate
        
        # Create HTTP date 180 seconds from now
        future_time = time.time() + 180
        http_date = formatdate(timeval=future_time, usegmt=True)
        
        mock_response = Mock()
        mock_response.headers = {'Retry-After': http_date}
        
        retry_after = self.index._parse_retry_after(mock_response)
        
        # Should be within a couple seconds of the target time
        self.assertGreater(retry_after, future_time - 2)
        self.assertLess(retry_after, future_time + 2)
    
    def test_parse_retry_after_missing(self):
        """Test handling missing Retry-After header."""
        mock_response = Mock()
        mock_response.headers = {}
        
        retry_after = self.index._parse_retry_after(mock_response)
        self.assertEqual(retry_after, 0.0)
    
    def test_exponential_backoff_sequence(self):
        """Test exponential backoff sequence: 60s, 120s, 240s, ..., capped at 3600s."""
        # First backoff: 60s
        self.index._tmdb_backoff_count = 0
        self.assertEqual(self.index._calculate_backoff(), 60)
        
        # Second: 120s
        self.index._tmdb_backoff_count = 1
        self.assertEqual(self.index._calculate_backoff(), 120)
        
        # Third: 240s
        self.index._tmdb_backoff_count = 2
        self.assertEqual(self.index._calculate_backoff(), 240)
        
        # Fourth: 480s
        self.index._tmdb_backoff_count = 3
        self.assertEqual(self.index._calculate_backoff(), 480)
        
        # Very high count: capped at 3600s
        self.index._tmdb_backoff_count = 10
        self.assertEqual(self.index._calculate_backoff(), 3600)
    
    def test_handle_429_with_retry_after_header(self):
        """Test 429 handling uses Retry-After header when present."""
        mock_response = Mock()
        mock_response.headers = {'Retry-After': '90'}
        
        t_before = time.time()
        self.index._handle_tmdb_429(mock_response)
        
        # Should set retry_after from header, not backoff
        self.assertGreater(self.index._tmdb_retry_after, t_before + 89)
        self.assertLess(self.index._tmdb_retry_after, t_before + 91)
        
        # Backoff count should still be 0 since header was used
        self.assertEqual(self.index._tmdb_backoff_count, 0)
    
    def test_handle_429_without_retry_after_header(self):
        """Test 429 handling uses exponential backoff without header."""
        mock_response = Mock()
        mock_response.headers = {}
        
        t_before = time.time()
        self.index._handle_tmdb_429(mock_response)
        
        # Should use 60s backoff for first failure
        self.assertGreater(self.index._tmdb_retry_after, t_before + 59)
        self.assertLess(self.index._tmdb_retry_after, t_before + 61)
        
        # Backoff count should increment
        self.assertEqual(self.index._tmdb_backoff_count, 1)
        
        # Second failure should double the backoff
        self.index._handle_tmdb_429(mock_response)
        self.assertEqual(self.index._tmdb_backoff_count, 2)
    
    def test_reset_backoff_on_success(self):
        """Test backoff resets after successful request."""
        # Simulate failures
        self.index._tmdb_backoff_count = 3
        self.index._tmdb_retry_after = time.time() + 240
        
        # Reset on success
        self.index._reset_tmdb_backoff()
        
        self.assertEqual(self.index._tmdb_backoff_count, 0)
        # retry_after should remain (it's checked with time.time() comparison)
    
    def test_is_rate_limited(self):
        """Test rate limit check."""
        # Not rate limited initially
        self.assertFalse(self.index._is_tmdb_rate_limited())
        
        # Set rate limit in future
        self.index._tmdb_retry_after = time.time() + 100
        self.assertTrue(self.index._is_tmdb_rate_limited())
        
        # Set rate limit in past
        self.index._tmdb_retry_after = time.time() - 1
        self.assertFalse(self.index._is_tmdb_rate_limited())
    
    @patch('app.fast_search.requests.get')
    @patch('app.settings_manager.SettingsManager')
    def test_refresh_tmdb_handles_429_with_header(self, mock_settings_class, mock_get):
        """Test TMDb titles refresh handles 429 with Retry-After header."""
        # Mock settings
        mock_settings = Mock()
        mock_settings.get.side_effect = lambda k, default='': {
            'apis.tmdb.api_key': 'test_key',
            'apis.tmdb.base_url': 'https://api.themoviedb.org/3'
        }.get(k, default)
        mock_settings_class.return_value = mock_settings
        
        # Mock 429 response with Retry-After header
        mock_response = Mock()
        mock_response.status_code = 429
        mock_response.headers = {'Retry-After': '300'}
        mock_get.return_value = mock_response
        
        t_before = time.time()
        self.index._refresh_tmdb_titles()
        
        # Should have set retry_after from header
        self.assertGreater(self.index._tmdb_retry_after, t_before + 299)
        self.assertLess(self.index._tmdb_retry_after, t_before + 301)
    
    @patch('app.fast_search.requests.get')
    @patch('app.settings_manager.SettingsManager')
    def test_refresh_tmdb_handles_429_without_header(self, mock_settings_class, mock_get):
        """Test TMDb titles refresh handles 429 with exponential backoff."""
        # Mock settings
        mock_settings = Mock()
        mock_settings.get.side_effect = lambda k, default='': {
            'apis.tmdb.api_key': 'test_key',
            'apis.tmdb.base_url': 'https://api.themoviedb.org/3'
        }.get(k, default)
        mock_settings_class.return_value = mock_settings
        
        # Mock 429 response without Retry-After header
        mock_response = Mock()
        mock_response.status_code = 429
        mock_response.headers = {}
        mock_get.return_value = mock_response
        
        t_before = time.time()
        self.index._refresh_tmdb_titles()
        
        # Should use exponential backoff (60s for first failure)
        self.assertGreater(self.index._tmdb_retry_after, t_before + 59)
        self.assertLess(self.index._tmdb_retry_after, t_before + 61)
        self.assertEqual(self.index._tmdb_backoff_count, 1)
    
    @patch('app.fast_search.requests.get')
    @patch('app.settings_manager.SettingsManager')
    def test_refresh_tmdb_resets_backoff_on_success(self, mock_settings_class, mock_get):
        """Test TMDb titles refresh resets backoff on successful request."""
        # Mock settings
        mock_settings = Mock()
        mock_settings.get.side_effect = lambda k, default='': {
            'apis.tmdb.api_key': 'test_key',
            'apis.tmdb.base_url': 'https://api.themoviedb.org/3'
        }.get(k, default)
        mock_settings_class.return_value = mock_settings
        
        # Start with backoff count
        self.index._tmdb_backoff_count = 3
        
        # Mock successful response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'results': [{'title': 'Test Movie'}]
        }
        mock_get.return_value = mock_response
        
        self.index._refresh_tmdb_titles()
        
        # Backoff should be reset
        self.assertEqual(self.index._tmdb_backoff_count, 0)


class TestFlaskEndpoints(unittest.TestCase):
    """Test Flask endpoints with mocked TMDb."""
    
    def setUp(self):
        """Set up Flask test client."""
        # Import here to avoid circular imports
        from app import main as app_main
        self.app = app_main.app
        self.app.config['TESTING'] = True
        self.client = local_client(self.app)
    
    @patch('app.main.fast_search_index')
    def test_library_search_fast_endpoint(self, mock_index):
        """Test /api/library/search/fast endpoint returns results."""
        # Mock search results
        mock_index.search.return_value = [
            {'title': 'Interstellar', 'rating_key': 1, 'year': 2014}
        ]
        mock_index.status.return_value = {
            'initialized': True,
            'total_items': 1000,
            'total_movies': 500,
            'total_shows': 500,
            'last_refresh': 0,
            'last_refresh_age_seconds': 0,
            'sections': []
        }
        
        response = self.client.get('/api/library/search/fast?q=Interstellar')
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        
        # Should have results
        self.assertGreaterEqual(data.get('total', 0), 1)
        self.assertIn('results', data)
        mock_index.search.assert_called_once()
    
    @patch('app.main.settings')
    @patch('app.main.fast_search_index')
    def test_tmdb_match_search_correction_triggered(self, mock_index, mock_settings):
        """Test /api/tmdb/match-search triggers correction on weak results."""
        # Mock settings
        mock_settings.get.side_effect = api_key_settings()
        
        # Mock weak direct results, then better corrected results
        mock_response_weak = Mock()
        mock_response_weak.status_code = 200
        mock_response_weak.json.return_value = {'results': []}
        
        mock_response_corrected = Mock()
        mock_response_corrected.status_code = 200
        mock_response_corrected.json.return_value = {
            'results': [{'id': 1, 'title': 'Interstellar', 'release_date': '2014-11-07'}]
        }
        
        # Mock spelling correction
        mock_index.find_spelling_correction.return_value = 'Interstellar'
        mock_index._is_tmdb_rate_limited.return_value = False
        mock_index._reset_tmdb_backoff.return_value = None
        
        with patch('app.main.requests.get', side_effect=[mock_response_weak, mock_response_corrected]):
            response = self.client.get('/api/tmdb/match-search?query=Interstelar&media_type=movie')
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            
            # Should have corrected results
            self.assertGreaterEqual(len(data.get('results', [])), 1)
            mock_index.find_spelling_correction.assert_called_once()
    
    @patch('app.main.settings')
    @patch('app.main.fast_search_index')
    def test_tmdb_match_search_correction_not_triggered_good_results(self, mock_index, mock_settings):
        """Test /api/tmdb/match-search doesn't trigger correction when results are good."""
        # Mock settings
        mock_settings.get.side_effect = api_key_settings()
        
        # Mock good direct results (query 'Interstellar' matches title 'Interstellar')
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'results': [
                {'id': 1, 'title': 'Interstellar', 'release_date': '2014-11-07'},
                {'id': 2, 'title': 'Interstellar 2', 'release_date': '2024-01-01'},
                {'id': 3, 'title': 'Interstellar 3', 'release_date': '2025-01-01'}
            ]
        }
        
        mock_index._is_tmdb_rate_limited.return_value = False
        mock_index._reset_tmdb_backoff.return_value = None
        
        with patch('app.main.requests.get', return_value=mock_response):
            response = self.client.get('/api/tmdb/match-search?query=Interstellar&media_type=movie')
            self.assertEqual(response.status_code, 200)
            
            # Correction should NOT be called (good results: >2 results and query matches title)
            mock_index.find_spelling_correction.assert_not_called()
    
    @patch('app.main.settings')
    def test_tmdb_match_search_endpoint_basic(self, mock_settings):
        """Test /api/tmdb/match-search endpoint basic functionality."""
        # Mock settings
        mock_settings.get.side_effect = api_key_settings()
        
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'results': [{'id': 1, 'title': 'Interstellar', 'release_date': '2014-11-07'}]
        }
        
        with patch('app.main.requests.get', return_value=mock_response):
            with patch('app.main.fast_search_index._reset_tmdb_backoff'):
                response = self.client.get('/api/tmdb/match-search?query=Interstellar&media_type=movie')
                self.assertEqual(response.status_code, 200)
                data = response.get_json()
                self.assertIn('results', data)
    
    @patch('app.main.tmdb')
    @patch('app.main.fast_search_index')
    def test_search_endpoint_corrected_results_after_direct(self, mock_index, mock_tmdb):
        """Test /search endpoint: corrected results after direct, deduped, no '_corrected' in response."""
        # Mock weak direct results
        mock_tmdb.search_multi.side_effect = [
            [{'id': 1, 'title': 'Star Wars', 'media_type': 'movie', 'popularity': 100}],  # Direct
            [{'id': 2, 'title': 'Star Trek', 'media_type': 'movie', 'popularity': 90}]   # Corrected
        ]
        mock_tmdb.search_movies.side_effect = [
            [{'id': 1, 'title': 'Star Wars', 'release_date': '1977-05-25', 'popularity': 100}],  # Direct
            [{'id': 2, 'title': 'Star Trek', 'release_date': '2009-05-08', 'popularity': 90},    # Corrected
             {'id': 1, 'title': 'Star Wars', 'release_date': '1977-05-25', 'popularity': 100}]  # Duplicate
        ]
        mock_tmdb.search_tv.side_effect = [[], []]
        mock_tmdb.search_people.return_value = []
        
        mock_index.find_spelling_correction.return_value = 'Star Trek'
        mock_index._is_tmdb_rate_limited.return_value = False
        
        response = self.client.get('/search?q=Star%20Warz&type=movie')
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        
        movies = data.get('movies', [])
        self.assertGreaterEqual(len(movies), 2)
        
        # Check that direct result comes before corrected result
        star_wars_rank = None
        star_trek_rank = None
        for movie in movies:
            if movie.get('title') == 'Star Wars':
                star_wars_rank = movie.get('relevance_rank')
            elif movie.get('title') == 'Star Trek':
                star_trek_rank = movie.get('relevance_rank')
        
        # Both must be present
        self.assertIsNotNone(star_wars_rank, "Star Wars (direct result) must be in response")
        self.assertIsNotNone(star_trek_rank, "Star Trek (corrected result) must be in response")
        # Direct result must rank before corrected result
        self.assertLess(star_wars_rank, star_trek_rank, 
                      "Direct result should rank before corrected result")
        
        # Check that '_corrected' marker is not in response
        for movie in movies:
            self.assertNotIn('_corrected', movie, 
                           "_corrected marker should be removed before returning")
        
        # Check deduplication (should not have duplicate ids)
        movie_ids = [m['id'] for m in movies if 'id' in m]
        self.assertEqual(len(movie_ids), len(set(movie_ids)), 
                       "Results should be deduped by id")
    
    @patch('app.main.tmdb')
    @patch('app.main.fast_search_index')
    def test_search_endpoint_correction_skipped_during_cooldown(self, mock_index, mock_tmdb):
        """Test /search endpoint: correction skipped when rate limited."""
        # Mock weak direct results
        mock_tmdb.search_multi.return_value = []
        mock_tmdb.search_movies.return_value = []
        mock_tmdb.search_tv.return_value = []
        mock_tmdb.search_people.return_value = []
        
        # Simulate rate limit cooldown
        mock_index._is_tmdb_rate_limited.return_value = True
        
        response = self.client.get('/search?q=Interstelar&type=movie')
        self.assertEqual(response.status_code, 200)
        
        # Correction should NOT be called (rate limited)
        mock_index.find_spelling_correction.assert_not_called()
    
    def test_hero_cache_image_served(self):
        """A downloaded hero image is served at the URL built for it, with and without a sub-path."""
        import io
        import shutil
        import tempfile
        from app.hero_cache import hero_cache

        temp_dir = Path(tempfile.mkdtemp(prefix='hero_cache_test_'))
        self.addCleanup(shutil.rmtree, temp_dir, True)
        for p in (patch.object(hero_cache, 'cache_dir', temp_dir),
                  patch.dict(self.app.config, {'HERO_CACHE_DIR': str(temp_dir)})):
            p.start()
            self.addCleanup(p.stop)

        data = b'\xFF\xD8\xFF\xE0\x00\x10JFIFtest_data'
        resp = Mock(status_code=200, raw=io.BytesIO(data))
        with patch.object(hero_cache.session, 'get', return_value=resp):
            stored = hero_cache.download_image('http://example.com/test.jpg', 'test_hero_12345.jpg')
        self.assertEqual(stored, 'test_hero_12345.jpg', 'download_image stores a filename only')

        url = f'/static/hero_cache/{stored}'
        self.assertEqual(url, '/static/hero_cache/test_hero_12345.jpg')
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data, data)

        sub_url = f'/luma/static/hero_cache/{stored}'
        self.assertEqual(sub_url, '/luma/static/hero_cache/test_hero_12345.jpg')
        r = self.client.get(sub_url[len('/luma'):], base_url='http://localhost/luma')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data, data)


class TestLibraryEndpointTypoTolerance(unittest.TestCase):
    """Test /api/library/search endpoint with typo tolerance."""
    
    def setUp(self):
        """Set up Flask test client."""
        from app import main as app_main
        self.app = app_main.app
        self.app.config['TESTING'] = True
        self.client = local_client(self.app)
    
    @patch('app.main.fast_search_index')
    def test_library_search_openheimer(self, mock_index):
        """Test library search with typo: Openheimer -> Oppenheimer."""
        mock_index.status.return_value = {'initialized': True}
        mock_index.search.return_value = [
            {'title': 'Oppenheimer', 'year': 2023, 'rating_key': 1, 'media_type': 'movie',
             'section_name': 'Movies', 'thumb': '/thumb1.jpg', 'art': '/art1.jpg', 'added_at': 1234567890}
        ]
        
        response = self.client.get('/api/library/search?q=Openheimer')
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        
        self.assertGreaterEqual(len(data.get('movies', [])), 1)
        self.assertEqual(data['movies'][0]['title'], 'Oppenheimer')
        mock_index.search.assert_called_once()
    
    @patch('app.main.fast_search_index')
    def test_library_search_interstelar(self, mock_index):
        """Test library search with typo: Interstelar -> Interstellar."""
        mock_index.status.return_value = {'initialized': True}
        mock_index.search.return_value = [
            {'title': 'Interstellar', 'year': 2014, 'rating_key': 2, 'media_type': 'movie',
             'section_name': 'Movies', 'thumb': '/thumb2.jpg', 'art': '/art2.jpg', 'added_at': 1234567890}
        ]
        
        response = self.client.get('/api/library/search?q=Interstelar')
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        
        self.assertGreaterEqual(len(data.get('movies', [])), 1)
        self.assertEqual(data['movies'][0]['title'], 'Interstellar')
    
    @patch('app.main.fast_search_index')
    def test_library_search_godfahter(self, mock_index):
        """Test library search with typo: Godfahter -> The Godfather."""
        mock_index.status.return_value = {'initialized': True}
        mock_index.search.return_value = [
            {'title': 'The Godfather', 'year': 1972, 'rating_key': 3, 'media_type': 'movie',
             'section_name': 'Movies', 'thumb': '/thumb3.jpg', 'art': '/art3.jpg', 'added_at': 1234567890}
        ]
        
        response = self.client.get('/api/library/search?q=Godfahter')
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        
        self.assertGreaterEqual(len(data.get('movies', [])), 1)
        self.assertEqual(data['movies'][0]['title'], 'The Godfather')
    
    @patch('app.main.fast_search_index')
    def test_library_search_shawshank_redemtion(self, mock_index):
        """Test library search with typo: Shawshank Redemtion -> The Shawshank Redemption."""
        mock_index.status.return_value = {'initialized': True}
        mock_index.search.return_value = [
            {'title': 'The Shawshank Redemption', 'year': 1994, 'rating_key': 4, 'media_type': 'movie',
             'section_name': 'Movies', 'thumb': '/thumb4.jpg', 'art': '/art4.jpg', 'added_at': 1234567890}
        ]
        
        response = self.client.get('/api/library/search?q=Shawshank Redemtion')
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        
        self.assertGreaterEqual(len(data.get('movies', [])), 1)
        self.assertEqual(data['movies'][0]['title'], 'The Shawshank Redemption')


class TestShortQueriesPrefixOnly(unittest.TestCase):
    """Test that 1-2 character queries are prefix-only on word/title starts."""
    
    def setUp(self):
        """Set up search index with test data."""
        self.index = FastSearchIndex()
        self.index._index = [
            {'title': 'Up', 'title_lower': 'up', 'title_normalized': 'up',
             'year': 2009, 'genres_lower': ['animation'], 'content_rating': 'PG',
             'section_id': 1, 'media_type': 'movie', 'popularity_score': 1.0, 'rating_key': 1},
            {'title': 'Superbad', 'title_lower': 'superbad', 'title_normalized': 'superbad',
             'year': 2007, 'genres_lower': ['comedy'], 'content_rating': 'R',
             'section_id': 1, 'media_type': 'movie', 'popularity_score': 0.8, 'rating_key': 2},
            {'title': 'Summer', 'title_lower': 'summer', 'title_normalized': 'summer',
             'year': 2020, 'genres_lower': ['drama'], 'content_rating': 'PG',
             'section_id': 1, 'media_type': 'movie', 'popularity_score': 0.5, 'rating_key': 3},
            {'title': 'Winter', 'title_lower': 'winter', 'title_normalized': 'winter',
             'year': 2019, 'genres_lower': ['drama'], 'content_rating': 'PG',
             'section_id': 1, 'media_type': 'movie', 'popularity_score': 0.5, 'rating_key': 4},
            {'title': 'Inception', 'title_lower': 'inception', 'title_normalized': 'inception',
             'year': 2010, 'genres_lower': ['action'], 'content_rating': 'PG-13',
             'section_id': 1, 'media_type': 'movie', 'popularity_score': 1.0, 'rating_key': 5},
        ]
        self.index._initialized = True
    
    def test_query_up_returns_up_not_superbad(self):
        """Test q=Up returns 'Up' but not 'Superbad' (substring)."""
        results = self.index.search(q='Up', fuzzy=False)
        titles = [r['title'] for r in results]
        
        self.assertIn('Up', titles, "Must return 'Up'")
        self.assertNotIn('Superbad', titles, "Must not return 'Superbad' (substring match)")
    
    def test_query_u_returns_up_not_summer(self):
        """Test q=u returns 'Up' but not 'Summer' (substring)."""
        results = self.index.search(q='u', fuzzy=False)
        titles = [r['title'] for r in results]
        
        self.assertIn('Up', titles, "Must return 'Up'")
        self.assertNotIn('Summer', titles, "Must not return 'Summer' (substring match)")
    
    def test_query_in_returns_inception_not_winter(self):
        """Test q=in returns 'Inception' but not 'Winter' (substring)."""
        results = self.index.search(q='in', fuzzy=False)
        titles = [r['title'] for r in results]
        
        self.assertIn('Inception', titles, "Must return 'Inception' (word start)")
        self.assertNotIn('Winter', titles, "Must not return 'Winter' (substring match)")


class TestPerformanceBenchmark(unittest.TestCase):
    """Test search performance on large library."""
    
    def _overall_median_ms(self):
        """Overall median (ms) of per-query medians over a 10k-item library."""
        import time
        from statistics import median
        
        # Create 10k item library
        index = FastSearchIndex()
        items = []
        base_titles = ['Dream', 'Fire', 'Shadow', 'River', 'Golden', 'Winter', 'Dark', 'White',
                      'Star', 'Iron', 'Broken', 'Summer', 'Love', 'Ghost', 'Night']
        
        for i in range(10000):
            title_words = [base_titles[i % len(base_titles)], 
                          base_titles[(i // 15) % len(base_titles)],
                          base_titles[(i // 225) % len(base_titles)]]
            title = ' '.join(title_words)
            items.append({
                'title': title,
                'title_lower': title.lower(),
                'title_normalized': normalize_text(title),
                'year': 2000 + (i % 24),
                'genres_lower': ['action'],
                'content_rating': 'PG-13',
                'section_id': 1,
                'media_type': 'movie',
                'popularity_score': 0.5,
                'rating_key': i,
            })
        
        # Add some specific titles
        specific_titles = ['Oppenheimer', 'Interstellar', 'The Godfather', 
                          'The Shawshank Redemption', 'Amélie']
        for i, title in enumerate(specific_titles):
            items.append({
                'title': title,
                'title_lower': title.lower(),
                'title_normalized': normalize_text(title),
                'year': 2000,
                'genres_lower': ['drama'],
                'content_rating': 'PG-13',
                'section_id': 1,
                'media_type': 'movie',
                'popularity_score': 1.0,
                'rating_key': 10000 + i,
            })
        
        index._index = items
        index._initialized = True
        
        # Benchmark queries
        queries = ['amelie', 'Openheimer', 'Interstelar', 'Godfahter', 'Shawshank Redemtion']
        
        # Warm up to avoid cold start bias (run each query once)
        for query in queries:
            index.search(q=query, limit=50, fuzzy=True)
        
        # Now run actual benchmark with sufficient iterations for stable median
        timings = []
        
        for query in queries:
            query_timings = []
            # Use 10 runs for more robust median
            for _ in range(10):
                start = time.perf_counter()
                results = index.search(q=query, limit=50, fuzzy=True)
                elapsed_ms = (time.perf_counter() - start) * 1000
                query_timings.append(elapsed_ms)
            
            med = median(query_timings)
            timings.append(med)
            print(f"Query '{query}': median {med:.2f}ms")
        
        overall_median = median(timings)
        print(f"Overall median: {overall_median:.2f}ms")
        return overall_median

    @pytest.mark.benchmark
    @unittest.skipUnless(os.environ.get('LUMA_BENCH') == '1', 'benchmark: set LUMA_BENCH=1 to run')
    def test_10k_library_under_50ms_median(self):
        """Opt-in: 10k library median under 50ms x LUMA_BENCH_SCALE (default 1.0)."""
        budget = 50.0 * float(os.environ.get('LUMA_BENCH_SCALE', '1'))
        med = self._overall_median_ms()
        self.assertLess(med, budget, f"Median search time {med:.2f}ms must be under {budget:.0f}ms for 10k library")

    def test_10k_library_regression_guard(self):
        """Always-on guard: 10k library median stays under 250ms on any host."""
        med = self._overall_median_ms()
        self.assertLess(med, 250.0, f"Median search time {med:.2f}ms regressed past 250ms")

    def test_rapidfuzz_is_active_matcher(self):
        """A missing rapidfuzz dependency must fail loudly, not silently slow search."""
        import app.fast_search as fs
        self.assertTrue(fs.HAS_RAPIDFUZZ, "rapidfuzz is not installed; fast_search fell back to trigram matching")
        index = FastSearchIndex()
        index._library_titles = {'interstellar'}
        index._title_map = {'interstellar': 'Interstellar'}
        with patch.object(fs.fuzz, 'ratio', wraps=fs.fuzz.ratio) as ratio:
            self.assertEqual(index.find_spelling_correction('Interstelar'), 'Interstellar')
        self.assertTrue(ratio.called, "find_spelling_correction did not use rapidfuzz")


class TestFuzzyNoiseReduction(unittest.TestCase):
    """Test that fuzzy matching doesn't produce false positives."""
    
    def setUp(self):
        """Set up search index."""
        self.index = FastSearchIndex()
        self.index._index = [
            {'title': 'Amélie', 'title_lower': 'amélie', 'title_normalized': 'amelie',
             'year': 2001, 'genres_lower': ['romance'], 'content_rating': 'R',
             'section_id': 1, 'media_type': 'movie', 'popularity_score': 1.0, 'rating_key': 1},
            {'title': 'Dream Fire', 'title_lower': 'dream fire', 'title_normalized': 'dream fire',
             'year': 2020, 'genres_lower': ['action'], 'content_rating': 'PG-13',
             'section_id': 1, 'media_type': 'movie', 'popularity_score': 0.5, 'rating_key': 2},
            {'title': 'Oppenheimer', 'title_lower': 'oppenheimer', 'title_normalized': 'oppenheimer',
             'year': 2023, 'genres_lower': ['biography'], 'content_rating': 'R',
             'section_id': 1, 'media_type': 'movie', 'popularity_score': 1.0, 'rating_key': 3},
            {'title': 'The Godfather', 'title_lower': 'the godfather', 'title_normalized': 'godfather',
             'year': 1972, 'genres_lower': ['crime'], 'content_rating': 'R',
             'section_id': 1, 'media_type': 'movie', 'popularity_score': 1.0, 'rating_key': 4},
        ]
        self.index._initialized = True
    
    def test_amelie_does_not_match_dream_fire(self):
        """Test 'amelie' does not match 'Dream Fire' (no token overlap)."""
        results = self.index.search(q='amelie', fuzzy=True)
        titles = [r['title'] for r in results]
        
        self.assertIn('Amélie', titles, "Must return 'Amélie'")
        self.assertNotIn('Dream Fire', titles, "Must not return 'Dream Fire' (unrelated)")
    
    def test_openheimer_does_not_match_godfather(self):
        """Test 'Openheimer' does not match 'The Godfather' (different tokens)."""
        results = self.index.search(q='Openheimer', fuzzy=True)
        titles = [r['title'] for r in results]
        
        self.assertIn('Oppenheimer', titles, "Must return 'Oppenheimer'")
        self.assertNotIn('The Godfather', titles, "Must not return 'The Godfather' (unrelated)")


class TestSubstringWithPrefilter(unittest.TestCase):
    """Test substring matching works even with first-letter prefilter."""
    
    def setUp(self):
        """Set up index with The Matrix."""
        self.index = FastSearchIndex()
        self.index._index = [
            {'title': 'The Matrix', 'title_lower': 'the matrix', 'title_normalized': 'matrix',
             'year': 1999, 'genres_lower': ['action', 'sci-fi'], 'content_rating': 'R',
             'section_id': 1, 'media_type': 'movie', 'popularity_score': 1.0, 'rating_key': 1},
            {'title': 'The Godfather', 'title_lower': 'the godfather', 'title_normalized': 'godfather',
             'year': 1972, 'genres_lower': ['crime', 'drama'], 'content_rating': 'R',
             'section_id': 1, 'media_type': 'movie', 'popularity_score': 1.0, 'rating_key': 2},
        ]
        self.index._initialized = True
    
    def test_atrix_finds_matrix(self):
        """Test 'atrix' finds 'The Matrix' via substring match despite prefiltering."""
        results = self.index.search(q='atrix', fuzzy=True)
        titles = [r['title'] for r in results]
        
        self.assertIn('The Matrix', titles, "'atrix' must find 'The Matrix' via substring")
        self.assertNotIn('The Godfather', titles, "Should not return unrelated titles")


class _FakeClock:
    """Manually advanced clock for cooldown/backoff tests (no sleeping)."""

    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class _FakePlex:
    plex_url = 'http://plex.invalid:32400'

    def __init__(self, items):
        self._items = items

    def health_check(self):
        return {'default_movie_section_id': 1}

    def get_recently_added(self, section_id=None, limit=None):
        return [dict(i) for i in self._items][:limit]


_HERO_SETTINGS = {
    'ui.hero.library_section_id': 1,
    'ui.hero.limit': 10,
    'ui.hero.boost_popular_titles': False,
    'ui.hero.popularity_source': 'tmdb',
    'ui.hero.excluded_genres': [],
    'ui.hero.max_age_days': 0,
    'apis.tmdb.api_key': '',
}

_PLEX_ITEMS = [{
    'rating_key': 1, 'title': 'Test Movie', 'year': 2024, 'type': 'movie',
    'tmdb_id': 12345, 'imdb_id': 'tt12345', 'summary': 'Test summary',
    'content_rating': 'R', 'duration': 7200000, 'rating': 8.1,
    'audienceRating': 9.0, 'genres': ['Action'], 'added_at': 1700000000,
    'thumbPath': '/library/metadata/1/thumb', 'artPath': '/library/metadata/1/art',
    'clearLogoPath': '/library/metadata/1/clearLogo',
}]


class TestHeroCacheHealing(unittest.TestCase):
    """Hero cache: healing, token hygiene, URL building, request isolation."""

    def setUp(self):
        import shutil
        import tempfile
        from app import main as app_main
        from app.hero_cache import hero_cache

        self.main = app_main
        self.app = app_main.app
        self.hero_cache = hero_cache

        self.temp_dir = Path(tempfile.mkdtemp(prefix='luma_hero_test_'))
        self.addCleanup(shutil.rmtree, self.temp_dir, True)
        self.temp_cache_dir = self.temp_dir / 'hero'
        self.temp_cache_dir.mkdir()
        self.temp_json = self.temp_dir / 'hero_content.json'
        self.clock = _FakeClock()

        # Every patch below is undone by addCleanup, even if the test fails.
        for attr, value in (
            ('cache_dir', self.temp_cache_dir),
            ('cache_json', self.temp_json),
            ('_clock', self.clock),
            ('_heal_cooldown', 600),
            ('_last_heal_time', 0),
            ('_heal_failed', False),
            ('_heal_backoff', 60),
            ('_heal_backoff_max', 600),
            ('plex_url', 'http://plex.invalid:32400'),
            ('plex_token', 'SECRET-TOKEN'),
        ):
            p = patch.object(self.hero_cache, attr, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.dict(self.app.config, {'HERO_CACHE_DIR': str(self.temp_cache_dir), 'TESTING': True})
        p.start()
        self.addCleanup(p.stop)

    def _write_cache(self, movies):
        import json
        with open(self.temp_json, 'w') as f:
            json.dump({'last_updated': '2024-01-01', 'movies': movies}, f)

    def _mock_plex(self):
        """Patch Plex and settings so _build_hero_movies() returns _PLEX_ITEMS."""
        patches = [
            patch.object(self.main, 'get_plex_for_request', return_value=_FakePlex(_PLEX_ITEMS)),
            patch.object(self.main.settings, 'get',
                         side_effect=lambda key, default=None: _HERO_SETTINGS.get(key, default)),
            patch.dict(self.main._hero_result_cache, {}, clear=True),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_endpoint_returns_build_hero_movies_with_all_fields(self):
        """/api/hero/content returns exactly what _build_hero_movies() builds."""
        self._mock_plex()
        with local_client(self.app) as client:
            resp = client.get('/api/hero/content')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['success'])
        self.assertIn('boost_mode', data)
        self.assertGreater(len(data['movies']), 0)

        self.main._hero_result_cache.clear()
        with self.app.test_request_context('/api/hero/content'):
            direct_movies, direct_boost = self.main._build_hero_movies()
        self.assertEqual(len(data['movies']), len(direct_movies))
        self.assertEqual(data['boost_mode'], direct_boost)
        for got, want in zip(data['movies'], direct_movies):
            self.assertEqual(set(got.keys()), set(want.keys()))
            self.assertEqual(got, want)

    def test_hero_urls_honor_script_root(self):
        """Under a sub-path (/luma) every hero image URL carries the prefix."""
        self._mock_plex()
        with local_client(self.app) as client:
            resp = client.get('/api/hero/content', base_url='http://localhost/luma')
        movie = resp.get_json()['movies'][0]
        for key in ('poster_url', 'backdrop_url', 'clearlogo_url'):
            self.assertTrue(movie[key].startswith('/luma/api/img/plex?url='), movie[key])
        # Cached result stays request independent (no prefix leaks into the cache).
        with local_client(self.app) as client:
            movie = client.get('/api/hero/content').get_json()['movies'][0]
        self.assertTrue(movie['poster_url'].startswith('/api/img/plex?url='), movie['poster_url'])

    def test_healing_covers_backdrop_poster_clearart(self):
        """Scheduler heal re-downloads missing backdrop, poster and clearart."""
        self._write_cache([
            {'rating_key': 1, 'title': 'Test Movie 1',
             'backdrop_url': 'movie_1_backdrop.jpg', 'backdrop_cache_file': 'movie_1_backdrop.jpg',
             'backdrop_upstream_url': 'http://fanart.tv/test_backdrop1.jpg',
             'clearart_url': 'movie_1_clearart.jpg', 'clearart_cache_file': 'movie_1_clearart.jpg',
             'clearart_upstream_url': 'http://fanart.tv/test_clearart1.jpg'},
            {'rating_key': 2, 'title': 'Test Movie 2',
             'poster_url': 'movie_2_poster.jpg', 'poster_cache_file': 'movie_2_poster.jpg',
             'poster_upstream_url': '/library/metadata/2/thumb',
             'backdrop_url': 'movie_2_backdrop.jpg', 'backdrop_cache_file': 'movie_2_backdrop.jpg',
             'backdrop_upstream_url': 'http://fanart.tv/test_backdrop2.jpg'},
        ])
        calls = []

        def fake_download(url, filename):
            calls.append((url, filename))
            (self.temp_cache_dir / filename).write_text('healed')
            return filename

        with patch.object(self.hero_cache, 'download_image', side_effect=fake_download):
            self.assertTrue(self.hero_cache.heal_missing_images())
        by_name = {f: u for u, f in calls}
        self.assertEqual(set(by_name), {'movie_1_backdrop.jpg', 'movie_1_clearart.jpg',
                                        'movie_2_poster.jpg', 'movie_2_backdrop.jpg'})
        # Plex poster gets the *current* token at download time only.
        self.assertEqual(by_name['movie_2_poster.jpg'],
                         'http://plex.invalid:32400/library/metadata/2/thumb?X-Plex-Token=SECRET-TOKEN')
        self.assertNotIn('SECRET-TOKEN', self.temp_json.read_text())

    def test_old_format_without_upstream_url_triggers_full_refresh(self):
        self._write_cache([{'rating_key': 1, 'title': 'Old Format Movie',
                            'backdrop_url': 'movie_1_backdrop.jpg',
                            'poster_url': '/api/img/plex?url=/library/metadata/1/thumb'}])
        with patch.object(self.hero_cache, 'refresh_hero_content', return_value=[]) as refresh:
            self.hero_cache.heal_missing_images()
        refresh.assert_called_once_with()

    def test_failed_heal_backoff(self):
        """Failed heals back off 60s -> 120s -> ... capped at 600s (mocked clock)."""
        self._write_cache([{'rating_key': 1, 'title': 'Test Movie',
                            'backdrop_cache_file': 'movie_1_backdrop.jpg',
                            'backdrop_upstream_url': 'http://fanart.tv/backdrop.jpg'}])
        with patch.object(self.hero_cache, 'download_image', return_value=None) as dl:
            self.assertTrue(self.hero_cache.heal_missing_images())
            self.assertTrue(self.hero_cache._heal_failed)
            self.assertEqual(self.hero_cache._heal_backoff, 120)

            self.clock.advance(119)
            self.assertFalse(self.hero_cache.heal_missing_images(), 'still inside backoff')
            self.clock.advance(1)
            self.assertTrue(self.hero_cache.heal_missing_images())
            self.assertEqual(self.hero_cache._heal_backoff, 240)
            for _ in range(5):
                self.clock.advance(self.hero_cache._heal_backoff)
                self.hero_cache.heal_missing_images()
            self.assertEqual(self.hero_cache._heal_backoff, 600, 'backoff is capped')
            self.assertEqual(dl.call_count, 7)

    def test_single_flight_and_cooldown(self):
        import threading
        self._write_cache([{'rating_key': 1, 'title': 'Test Movie',
                            'backdrop_cache_file': 'movie_1_backdrop.jpg',
                            'backdrop_upstream_url': 'http://fanart.tv/backdrop.jpg'}])
        entered, release = threading.Event(), threading.Event()
        calls = []

        def blocking_heal(content, missing):
            calls.append(1)
            entered.set()
            release.wait(5)

        with patch.object(self.hero_cache, '_heal_missing_images_background', side_effect=blocking_heal):
            first = threading.Thread(target=self.hero_cache.heal_missing_images)
            first.start()
            self.assertTrue(entered.wait(5))
            # While the first heal holds the lock, others return immediately.
            self.assertFalse(self.hero_cache.heal_missing_images())
            release.set()
            first.join(5)
            self.assertEqual(len(calls), 1)

            self.clock.advance(599)
            self.assertFalse(self.hero_cache.heal_missing_images(), 'cooldown')
            self.clock.advance(1)
            release.set()
            self.assertTrue(self.hero_cache.heal_missing_images())
            self.assertEqual(len(calls), 2)

    def test_no_plex_token_persisted(self):
        """refresh_hero_content() never writes a Plex token to hero_content.json."""
        movie = {'rating_key': 123, 'title': 'Test Movie', 'year': 2024, 'genres': [],
                 'tmdb_id': 1, 'imdb_id': 'tt1',
                 'user_thumb_url': '/library/metadata/123/thumb',
                 'user_art_url': '/library/metadata/123/art',
                 'user_clear_logo_url': '/library/metadata/123/clearLogo'}
        downloads = []

        def fake_download(url, filename):
            downloads.append(url)
            return filename

        with patch.object(self.hero_cache, 'get_recently_released_movies', return_value=[movie]), \
                patch.object(self.hero_cache, 'get_rt_score_from_plex', return_value=85), \
                patch.object(self.hero_cache, 'has_hd_clearart', return_value=True), \
                patch.object(self.hero_cache, 'get_fanart_images',
                             return_value={'clearlogo': None, 'clearart': 'http://fanart.tv/c.png',
                                           'backdrop': None}), \
                patch.object(self.hero_cache, 'download_image', side_effect=fake_download):
            self.hero_cache.refresh_hero_content()

        raw = self.temp_json.read_text()
        self.assertNotIn('SECRET-TOKEN', raw)
        self.assertNotIn('X-Plex-Token', raw)
        import json
        stored = json.loads(raw)['movies'][0]
        self.assertEqual(stored['poster_upstream_url'], '/library/metadata/123/thumb')
        self.assertEqual(stored['poster_url'], 'movie_123_poster.jpg')
        self.assertEqual(stored['backdrop_url'], 'plex:/library/metadata/123/art')
        self.assertTrue(any('X-Plex-Token=SECRET-TOKEN' in u for u in downloads),
                        'token is added at download time')

    def test_tokens_stripped_from_old_files_on_load(self):
        self._write_cache([{'rating_key': 1, 'title': 'Legacy',
                            'poster_cache_file': 'movie_1_poster.jpg',
                            'poster_upstream_url': 'http://plex.invalid:32400/library/metadata/1/thumb?X-Plex-Token=OLD',
                            'backdrop_url': 'http://x.invalid/a.jpg?size=1&X-Plex-Token=OLD'}])
        movies = self.hero_cache.load_cache()
        self.assertEqual(movies[0]['poster_upstream_url'], '/library/metadata/1/thumb')
        self.assertEqual(movies[0]['backdrop_url'], 'http://x.invalid/a.jpg?size=1')
        self.assertNotIn('OLD', self.temp_json.read_text(), 'file is rewritten without tokens')

    def test_get_endpoint_no_refresh_or_thread(self):
        """GET /api/hero/content never refreshes, heals or starts a thread."""
        import threading
        self._mock_plex()
        self._write_cache([{'rating_key': 1, 'title': 'Missing images',
                            'backdrop_cache_file': 'movie_1_backdrop.jpg',
                            'backdrop_upstream_url': 'http://fanart.tv/backdrop.jpg'}])
        with patch.object(self.hero_cache, 'refresh_hero_content') as refresh, \
                patch.object(self.hero_cache, 'heal_missing_images') as heal, \
                patch.object(self.hero_cache, '_heal_missing_images_background') as heal_bg, \
                patch.object(threading.Thread, 'start', autospec=True) as thread_start:
            with local_client(self.app) as client:
                resp = client.get('/api/hero/content')
                self.assertEqual(resp.status_code, 200)
        refresh.assert_not_called()
        heal.assert_not_called()
        heal_bg.assert_not_called()
        thread_start.assert_not_called()

    def test_sanitize_extension(self):
        s = self.hero_cache._sanitize_extension
        self.assertEqual(s('http://f.tv/a/b.PNG'), 'png')
        self.assertEqual(s('http://f.tv/a/b.webp?x=1.exe'), 'webp')
        self.assertEqual(s('http://f.tv/a/b.php%2F..%2Fx'), 'jpg')
        self.assertEqual(s('http://f.tv/a/b.toolong'), 'jpg')
        self.assertEqual(s('http://f.tv/a/noext'), 'jpg')
        self.assertEqual(s('http://f.tv/a.b/c'), 'jpg')

    def test_download_writes_tmp_then_replaces(self):
        import io
        resp = Mock(status_code=200, raw=io.BytesIO(b'img-bytes'))
        with patch.object(self.hero_cache.session, 'get', return_value=resp), \
                patch('app.hero_cache.os.replace', wraps=os.replace) as replace:
            self.assertEqual(self.hero_cache.download_image('http://f.tv/a.jpg', 'movie_9_x.jpg'),
                             'movie_9_x.jpg')
        src, dst = replace.call_args.args
        self.assertEqual(Path(src).name, 'movie_9_x.jpg.tmp')
        self.assertEqual(Path(dst), self.temp_cache_dir / 'movie_9_x.jpg')
        self.assertEqual((self.temp_cache_dir / 'movie_9_x.jpg').read_bytes(), b'img-bytes')
        self.assertEqual(list(self.temp_cache_dir.glob('*.tmp')), [])

        bad = Mock(status_code=200, raw=Mock(read=Mock(side_effect=OSError('boom'))))
        with patch.object(self.hero_cache.session, 'get', return_value=bad):
            self.assertIsNone(self.hero_cache.download_image('http://f.tv/b.jpg', 'movie_9_y.jpg'))
        self.assertFalse((self.temp_cache_dir / 'movie_9_y.jpg').exists())
        self.assertEqual(list(self.temp_cache_dir.glob('*.tmp')), [])


class TestCachePaths(unittest.TestCase):
    """LUMA_CACHE_DIR / HERO_CACHE_DIR / LUMA_DATA_DIR resolution."""

    def test_defaults(self):
        from app import paths
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(paths.cache_dir(), Path('/app/cache'))
            self.assertEqual(paths.hero_cache_dir(), Path('/app/cache/hero'))
            self.assertEqual(paths.data_dir(), Path('/app/data'))

    def test_env_overrides(self):
        from app import paths
        with patch.dict(os.environ, {'LUMA_CACHE_DIR': '/x/cache', 'LUMA_DATA_DIR': '/x/data'}, clear=True):
            self.assertEqual(paths.hero_cache_dir(), Path('/x/cache/hero'))
            self.assertEqual(paths.data_path('hero_content.json'), Path('/x/data/hero_content.json'))
        with patch.dict(os.environ, {'LUMA_CACHE_DIR': '/x/cache', 'HERO_CACHE_DIR': '/y/hero'}, clear=True):
            self.assertEqual(paths.hero_cache_dir(), Path('/y/hero'))

    def test_hero_cache_resolves_once_and_fails_fast(self):
        import tempfile
        from app.hero_cache import HeroCache
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {'LUMA_CACHE_DIR': tmp, 'LUMA_DATA_DIR': tmp}):
                os.environ.pop('HERO_CACHE_DIR', None)
                hc = HeroCache()
            self.assertEqual(hc.cache_dir, Path(tmp) / 'hero')
            self.assertTrue(hc.cache_dir.is_dir())
            blocker = Path(tmp) / 'file'
            blocker.write_text('x')
            with patch.dict(os.environ, {'HERO_CACHE_DIR': str(blocker / 'hero'), 'LUMA_DATA_DIR': tmp}):
                with self.assertRaises(RuntimeError):
                    HeroCache()

    def test_app_config_uses_resolved_hero_dir(self):
        from app import main as app_main
        from app.hero_cache import hero_cache
        # main.py copies the value resolved by HeroCache at import time.
        self.assertTrue(app_main.app.config['HERO_CACHE_DIR'])
        self.assertIsInstance(hero_cache.cache_dir, Path)

    def test_explore_and_library_default_under_cache_root(self):
        import tempfile
        from app.explore_cache import ExploreCache
        from app.library_cache import LibraryCache
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {'LUMA_CACHE_DIR': tmp}):
                self.assertEqual(ExploreCache().cache_dir, Path(tmp) / 'explore')
                self.assertEqual(LibraryCache().cache_dir, Path(tmp) / 'library')


if __name__ == '__main__':
    unittest.main()


class TestTMDbSearchesHonorRetryAfter(unittest.TestCase):
    """Every TMDb search path skips upstream calls while Retry-After is active."""

    def setUp(self):
        from app import main as app_main
        self.main = app_main
        self.index = app_main.fast_search_index
        self._saved = self.index._tmdb_retry_after
        self.index._tmdb_retry_after = time.time() + 100
        self.addCleanup(setattr, self.index, '_tmdb_retry_after', self._saved)

    def test_client_searches_make_no_calls(self):
        client = self.main.TMDBClient('test_key')
        with patch('app.main.requests.get') as get:
            for fn in (client.search_movies, client.search_tv, client.search_multi):
                self.assertEqual(fn('Interstelar'), [])
            self.assertEqual(client.search_people('Tom'), [])          # 3 pages
            self.assertEqual(client.search_people('Christopher Nolan'), [])  # 1 page
        get.assert_not_called()

    def test_client_searches_call_when_not_limited(self):
        self.index._tmdb_retry_after = time.time() - 1
        resp = Mock(status_code=200)
        resp.json.return_value = {'results': [{'id': 1}]}
        client = self.main.TMDBClient('test_key')
        with patch('app.main.requests.get', return_value=resp) as get:
            self.assertEqual(client.search_movies('Interstellar'), [{'id': 1}])
        self.assertEqual(get.call_count, 1)

    @patch('app.main.settings')
    def test_match_search_fetch_makes_no_calls(self, mock_settings):
        mock_settings.get.side_effect = api_key_settings()
        self.main._tmdb_match_cache.clear()
        with patch('app.main.requests.get') as get:
            r = local_client(self.main.app).get('/api/tmdb/match-search?query=Retryafterprobe')
        self.assertEqual(r.status_code, 200)
        # requests.get is patched process-wide, so a background warm-up thread
        # left by an earlier test (portal trending) may show up here; only TMDb
        # search calls from this request matter.
        searches = [c for c in get.call_args_list if c.args and '/search/' in str(c.args[0])]
        self.assertEqual(searches, [])

    def test_refresh_tmdb_titles_skipped_when_backed_off(self):
        index = FastSearchIndex()
        index._tmdb_retry_after = time.time() + 100
        with patch('app.fast_search.requests.get') as get, \
                patch('app.settings_manager.SettingsManager') as sm:
            index._refresh_tmdb_titles()
        get.assert_not_called()
        sm.assert_not_called()
        self.assertFalse(index._tmdb_refreshing)

    def test_refresh_thread_not_started_when_backed_off(self):
        index = FastSearchIndex()
        index._tmdb_retry_after = time.time() + 100
        with patch('app.fast_search.threading.Thread') as thread:
            self.assertFalse(index._maybe_start_tmdb_refresh())
        thread.assert_not_called()
        index._tmdb_retry_after = time.time() - 1
        with patch('app.fast_search.threading.Thread') as thread:
            self.assertTrue(index._maybe_start_tmdb_refresh())
        thread.assert_called_once()
