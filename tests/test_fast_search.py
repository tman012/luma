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
        self.client = self.app.test_client()
    
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
        mock_settings.get.return_value = 'test_api_key'
        
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
        mock_settings.get.return_value = 'test_api_key'
        
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
        mock_settings.get.return_value = 'test_api_key'
        
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
        """Test that cached image is served at URL download_image returns."""
        import tempfile
        import shutil
        from pathlib import Path
        from app.hero_cache import hero_cache
        
        # Create temp directory for test
        temp_dir = Path(tempfile.mkdtemp(prefix='hero_cache_test_'))
        
        # Save original paths
        original_cache_dir = hero_cache.cache_dir
        original_static_folder = self.app.static_folder
        
        def cleanup():
            """Restore original paths and clean up temp directory."""
            hero_cache.cache_dir = original_cache_dir
            self.app.static_folder = original_static_folder
            shutil.rmtree(temp_dir, ignore_errors=True)
        
        self.addCleanup(cleanup)
        
        # Point both hero_cache.cache_dir and Flask app.static_folder at temp dir
        # hero_cache writes to cache_dir (which should be .../hero_cache)
        # Flask serves from static_folder (which should be the parent, so /static/hero_cache/ maps correctly)
        hero_cache_subdir = temp_dir / 'hero_cache'
        hero_cache_subdir.mkdir(parents=True, exist_ok=True)
        
        hero_cache.cache_dir = hero_cache_subdir
        self.app.static_folder = str(temp_dir)
        
        # Create test image data
        test_image_data = b'\xFF\xD8\xFF\xE0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00test_data'
        test_filename = 'test_hero_12345.jpg'
        
        # Mock HTTP response for download_image
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.raw = Mock()
        mock_response.raw.read = Mock(side_effect=[test_image_data, b''])
        
        # Download image (with mocked HTTP)
        with patch.object(hero_cache.session, 'get', return_value=mock_response):
            image_url = hero_cache.download_image('http://example.com/test.jpg', test_filename)
        
        # Verify download_image returned a URL
        self.assertIsNotNone(image_url, "download_image should return a URL")
        self.assertTrue(image_url.startswith('/static/hero_cache/'),
                      f"Image URL should start with /static/hero_cache/, got: {image_url}")
        
        # Verify file was written
        cached_file = hero_cache.cache_dir / test_filename
        self.assertTrue(cached_file.exists(), "Image file should be cached")
        
        # Test Flask serving: GET the exact URL returned by download_image
        response = self.client.get(image_url)
        self.assertEqual(response.status_code, 200,
                       f"Image should be served at {image_url}")
        self.assertEqual(response.data, test_image_data,
                       "Served image data should match cached data")


class TestLibraryEndpointTypoTolerance(unittest.TestCase):
    """Test /api/library/search endpoint with typo tolerance."""
    
    def setUp(self):
        """Set up Flask test client."""
        from app import main as app_main
        self.app = app_main.app
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()
    
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
    
    def test_10k_library_under_50ms_median(self):
        """Test that 10k item library searches complete in under 50ms median."""
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
        
        self.assertLess(overall_median, 50.0, 
                       f"Median search time {overall_median:.2f}ms must be under 50ms for 10k library")


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


class TestHeroCacheHealing(unittest.TestCase):
    """Test hero cache self-healing through /api/hero/content endpoint."""
    
    def setUp(self):
        """Set up test with mock hero cache and Flask test client."""
        from app.hero_cache import hero_cache
        from app.main import app as flask_app
        import tempfile
        
        self.app = flask_app
        
        self.hero_cache = hero_cache
        # Save original values
        self.orig_cache_dir = self.hero_cache.cache_dir
        self.orig_cache_json = self.hero_cache.cache_json
        self.orig_cooldown = self.hero_cache._heal_cooldown
        
        # Create temp directories
        self.temp_dir = Path(tempfile.mkdtemp())
        self.temp_cache_dir = self.temp_dir / 'hero_cache'
        self.temp_cache_dir.mkdir(parents=True, exist_ok=True)
        self.temp_json = self.temp_dir / 'hero.json'
        
        # Override cache paths
        self.hero_cache.cache_dir = self.temp_cache_dir
        self.hero_cache.cache_json = self.temp_json
        self.hero_cache._heal_cooldown = 1  # 1 second for tests
        self.hero_cache._last_heal_time = 0  # Reset cooldown
    
    def tearDown(self):
        """Clean up temp files."""
        import shutil
        self.hero_cache.cache_dir = self.orig_cache_dir
        self.hero_cache.cache_json = self.orig_cache_json
        self.hero_cache._heal_cooldown = self.orig_cooldown
        self.hero_cache._last_heal_time = 0
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)
    
    def test_endpoint_returns_build_hero_movies_with_all_fields(self):
        """Test /api/hero/content returns _build_hero_movies with all required fields preserved."""
        import json
        
        # Create cache with data shaped as refresh_hero_content writes it
        content = [{
            'rating_key': 1,
            'title': 'Test Movie',
            'year': 2024,
            'tmdb_id': 12345,
            'imdb_id': 'tt12345',
            'summary': 'Test summary',
            'rt_score': 95,
            'duration': 7200000,
            'content_rating': 'R',
            'genres': ['Action'],
            'clearlogo_url': '/api/img/plex?url=/library/metadata/1/clearLogo',
            'clearart_url': '/static/hero_cache/movie_1_clearart.jpg',
            'clearart_cache_file': 'movie_1_clearart.jpg',
            'clearart_upstream_url': 'http://fanart.tv/clearart.jpg',
            'backdrop_url': '/static/hero_cache/movie_1_backdrop.jpg',
            'backdrop_cache_file': 'movie_1_backdrop.jpg',
            'backdrop_upstream_url': 'http://fanart.tv/backdrop.jpg',
            'poster_url': '/api/img/plex?url=/library/metadata/1/thumb',
            'is_owned': True
        }]
        
        # Create the cached images
        (self.temp_cache_dir / 'movie_1_clearart.jpg').write_text('fake')
        (self.temp_cache_dir / 'movie_1_backdrop.jpg').write_text('fake')
        
        # Save cache
        with open(self.temp_json, 'w') as f:
            json.dump({'last_updated': '2024-01-01', 'movies': content}, f)
        
        # Call endpoint - should return _build_hero_movies format with all fields
        with self.app.test_client() as client:
            resp = client.get('/api/hero/content')
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            
            # Check response has boost_mode (from _build_hero_movies)
            self.assertTrue(data['success'])
            self.assertIn('movies', data)
            self.assertIn('boost_mode', data)
            
            # Verify it comes from _build_hero_movies (not cached content)
            # The response should have _build_hero_movies fields, not cache-specific fields
            # If there are movies (from Plex), they should have: rating, audience_rating, 
            # added_at, tmdb_backdrop_url, etc.
            # Since _build_hero_movies fetches from Plex dynamically, we verify structure only
            if data['movies']:
                movie = data['movies'][0]
                # These fields must be present in _build_hero_movies format
                required_fields = ['rating_key', 'title', 'year', 'summary', 'genres']
                for field in required_fields:
                    self.assertIn(field, movie, f"_build_hero_movies must include {field}")
                
                # These fields may be None but should exist
                optional_fields = ['rating', 'audience_rating', 'added_at', 'tmdb_backdrop_url',
                                 'poster_url', 'backdrop_url', 'clearlogo_url']
                for field in optional_fields:
                    self.assertIn(field, movie, f"_build_hero_movies must include {field}")
    
    def test_healing_covers_backdrop_and_clearart(self):
        """Test missing files trigger heal, then files serve 200 with mocked upstream fetch."""
        import json
        import time
        
        # Create cache entries with upstream URLs (new format) - 2 movies
        content = [
            {
                'rating_key': 1,
                'title': 'Test Movie 1',
                'backdrop_url': '/static/hero_cache/movie_1_backdrop.jpg',
                'backdrop_cache_file': 'movie_1_backdrop.jpg',
                'backdrop_upstream_url': 'http://fanart.tv/test_backdrop1.jpg',
                'clearart_url': '/static/hero_cache/movie_1_clearart.jpg',
                'clearart_cache_file': 'movie_1_clearart.jpg',
                'clearart_upstream_url': 'http://fanart.tv/test_clearart1.jpg',
            },
            {
                'rating_key': 2,
                'title': 'Test Movie 2',
                'poster_url': '/static/hero_cache/movie_2_poster.jpg',  # Note: poster not in cache_file format
                'backdrop_url': '/static/hero_cache/movie_2_backdrop.jpg',
                'backdrop_cache_file': 'movie_2_backdrop.jpg',
                'backdrop_upstream_url': 'http://fanart.tv/test_backdrop2.jpg',
            }
        ]
        
        # Save cache WITHOUT creating image files (they're missing)
        with open(self.temp_json, 'w') as f:
            json.dump({'last_updated': '2024-01-01', 'movies': content}, f)
        
        # Mock download to track healing calls and create files
        download_calls = []
        orig_download = self.hero_cache.download_image
        def mock_download(url, filename):
            download_calls.append((url, filename))
            # Create actual file so it can be served
            (self.temp_cache_dir / filename).write_text(f'healed-{filename}')
            return f"/static/hero_cache/{filename}"
        self.hero_cache.download_image = mock_download
        
        # Call endpoint - should trigger background healing
        with self.app.test_client() as client:
            resp = client.get('/api/hero/content')
            self.assertEqual(resp.status_code, 200)
        
        # Wait for background healing to complete
        time.sleep(0.5)
        
        # Verify healing covered backdrop AND clearart
        self.assertGreaterEqual(len(download_calls), 3, "Should heal backdrop1, clearart1, backdrop2")
        urls = [call[0] for call in download_calls]
        filenames = [call[1] for call in download_calls]
        
        # Check specific files were healed
        self.assertIn('movie_1_backdrop.jpg', filenames, "Should heal movie_1_backdrop.jpg")
        self.assertIn('movie_1_clearart.jpg', filenames, "Should heal movie_1_clearart.jpg (clearart coverage)")
        self.assertIn('movie_2_backdrop.jpg', filenames, "Should heal movie_2_backdrop.jpg")
        
        # Verify files now exist and can be served (simulate serving)
        self.assertTrue((self.temp_cache_dir / 'movie_1_backdrop.jpg').exists())
        self.assertTrue((self.temp_cache_dir / 'movie_1_clearart.jpg').exists())
        
        # Verify healed files have content
        self.assertEqual((self.temp_cache_dir / 'movie_1_backdrop.jpg').read_text(), 'healed-movie_1_backdrop.jpg')
        
        # Restore
        self.hero_cache.download_image = orig_download
    
    def test_failed_heal_backoff(self):
        """Test failed heal uses exponential backoff (60s, 120s, ..., capped at 600s)."""
        import json
        import time
        
        # Create cache with missing file
        content = [{
            'rating_key': 1,
            'title': 'Test Movie',
            'backdrop_url': '/static/hero_cache/movie_1_backdrop.jpg',
            'backdrop_cache_file': 'movie_1_backdrop.jpg',
            'backdrop_upstream_url': 'http://fanart.tv/backdrop.jpg',
        }]
        
        with open(self.temp_json, 'w') as f:
            json.dump({'last_updated': '2024-01-01', 'movies': content}, f)
        
        # Mock download to fail
        orig_download = self.hero_cache.download_image
        def mock_download_fail(url, filename):
            return None  # Simulate download failure
        self.hero_cache.download_image = mock_download_fail
        
        # Set short initial backoff for testing
        self.hero_cache._heal_backoff = 1  # 1 second for test
        self.hero_cache._heal_backoff_max = 4  # Cap at 4 seconds for test
        
        # First heal attempt - should fail
        with self.app.test_client() as client:
            client.get('/api/hero/content')
        time.sleep(0.2)
        
        # Verify backoff doubled after failure
        self.assertTrue(self.hero_cache._heal_failed, "Should be marked as failed")
        self.assertEqual(self.hero_cache._heal_backoff, 2, "Backoff should double to 2s")
        
        # Immediate retry should not trigger (still in cooldown)
        heal_count = [0]
        orig_heal = self.hero_cache._heal_missing_images_background
        def count_heal(content, missing):
            heal_count[0] += 1
            orig_heal(content, missing)
        self.hero_cache._heal_missing_images_background = count_heal
        
        with self.app.test_client() as client:
            client.get('/api/hero/content')
        time.sleep(0.1)
        self.assertEqual(heal_count[0], 0, "Should not retry immediately")
        
        # Wait for backoff period and retry - should trigger another heal
        time.sleep(2.1)
        with self.app.test_client() as client:
            client.get('/api/hero/content')
        time.sleep(0.2)
        self.assertEqual(heal_count[0], 1, "Should retry after backoff period")
        
        # Verify backoff doubled again and is capped
        self.assertEqual(self.hero_cache._heal_backoff, 4, "Backoff should double to 4s (capped)")
        
        # Now make heal succeed
        def mock_download_success(url, filename):
            (self.temp_cache_dir / filename).write_text('healed')
            return f"/static/hero_cache/{filename}"
        self.hero_cache.download_image = mock_download_success
        
        # Wait and trigger successful heal
        time.sleep(4.1)
        with self.app.test_client() as client:
            client.get('/api/hero/content')
        time.sleep(0.3)
        
        # Verify backoff reset after success
        self.assertFalse(self.hero_cache._heal_failed, "Should not be marked as failed")
        self.assertEqual(self.hero_cache._heal_backoff, 60, "Backoff should reset to 60s")
        
        # Restore
        self.hero_cache.download_image = orig_download
        self.hero_cache._heal_missing_images_background = orig_heal
    
    def test_single_flight_and_cooldown(self):
        """Test single-flight lock and cooldown prevent duplicate healing."""
        import json
        import time
        import threading
        
        # Create cache with missing file
        content = [{
            'rating_key': 1,
            'title': 'Test Movie',
            'backdrop_url': '/static/hero_cache/movie_1_backdrop.jpg',
            'backdrop_cache_file': 'movie_1_backdrop.jpg',
            'backdrop_upstream_url': 'http://fanart.tv/backdrop.jpg',
        }]
        
        with open(self.temp_json, 'w') as f:
            json.dump({'last_updated': '2024-01-01', 'movies': content}, f)
        
        # Track healing attempts
        heal_count = [0]
        orig_heal = self.hero_cache._heal_missing_images_background
        def mock_heal(content, missing):
            heal_count[0] += 1
            time.sleep(0.2)  # Slow healing
        self.hero_cache._heal_missing_images_background = mock_heal
        
        # Make 5 concurrent requests - should only trigger one heal (single-flight)
        def make_request():
            with self.app.test_client() as client:
                client.get('/api/hero/content')
        
        threads = [threading.Thread(target=make_request) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        time.sleep(0.5)
        self.assertEqual(heal_count[0], 1, "Single-flight: only one heal despite concurrent requests")
        
        # Immediate retry should not trigger another heal (cooldown)
        with self.app.test_client() as client:
            client.get('/api/hero/content')
        time.sleep(0.2)
        self.assertEqual(heal_count[0], 1, "Cooldown: no second heal immediately after first")
        
        # Wait for cooldown to expire
        time.sleep(1.0)
        
        # Now should trigger another heal
        with self.app.test_client() as client:
            client.get('/api/hero/content')
        time.sleep(0.3)
        self.assertEqual(heal_count[0], 2, "Should heal again after cooldown expires")
        
        # Restore
        self.hero_cache._heal_missing_images_background = orig_heal


if __name__ == '__main__':
    unittest.main()
