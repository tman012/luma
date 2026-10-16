"""PR #3 Ops/Tester fixes: SPA outside ./static, writable-dir startup check,
single hero startup refresh/heal, legacy /static/hero_cache URLs."""
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]


def _tmpdir(test, prefix):
    d = Path(tempfile.mkdtemp(prefix=prefix))
    test.addCleanup(lambda: (os.chmod(d, 0o755), shutil.rmtree(d, True)))
    return d


class TestSpaOutsideStatic(unittest.TestCase):
    def setUp(self):
        from app import main
        self.main = main
        self.client = main.app.test_client()
        self.static = _tmpdir(self, 'static_')          # like a bind-mounted ./static: no spa/
        self.spa = _tmpdir(self, 'spa_')
        (self.spa / 'index.html').write_text('<html>luma-spa</html>')
        (self.spa / 'assets').mkdir()
        (self.spa / 'assets' / 'app.js').write_text('console.log(1)')
        self._use_static(main.app, self.static)
        p = patch.dict(main.app.config, {'SPA_DIR': str(self.spa)}); p.start(); self.addCleanup(p.stop)

    def _use_static(self, app, d):
        old = app.static_folder   # a Flask property: patch.object cannot restore it
        app.static_folder = str(d)
        self.addCleanup(setattr, app, 'static_folder', old)

    def test_index_served_when_static_has_no_spa(self):
        self.assertFalse((self.static / 'spa').exists())
        r = self.client.get('/')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'luma-spa', r.data)
        r = self.client.get('/assets/app.js')
        self.assertEqual(r.status_code, 200)

    def test_resolve_prefers_env_then_image_path_then_static(self):
        resolve = self.main._resolve_spa_dir
        with patch.dict(os.environ, {'LUMA_SPA_DIR': '/x/spa'}):
            self.assertEqual(resolve(str(self.static), str(self.spa)), '/x/spa')
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('LUMA_SPA_DIR', None)
            self.assertEqual(resolve(str(self.static), str(self.spa)), str(self.spa))
            self.assertEqual(resolve(str(self.static), '/nonexistent/spa'), os.path.join(str(self.static), 'spa'))


class TestWritableStartupCheck(unittest.TestCase):
    def setUp(self):
        if os.geteuid() == 0:
            self.skipTest('permission bits are not enforced for root')
        from app import paths
        self.paths = paths

    def _populated(self):
        d = _tmpdir(self, 'luma_dir_')
        con = sqlite3.connect(str(d / 'library.db')); con.execute('create table t(x)'); con.commit(); con.close()
        (d / 'hero').mkdir()
        return d

    def test_writable_dirs_pass(self):
        d = self._populated()
        self.paths.check_writable_dirs({'LUMA_DATA_DIR': d, 'LUMA_CACHE_DIR': d / 'cache'})
        self.assertEqual(sorted(p.name for p in d.iterdir()), ['cache', 'hero', 'library.db'], 'temp file removed')

    def test_populated_read_only_dir_fails(self):
        d = self._populated()
        os.chmod(d, 0o555)
        with self.assertRaises(self.paths.StartupCheckError) as cm:
            self.paths.check_writable_dirs({'LUMA_CACHE_DIR': d})
        self.assertIn(f'LUMA_CACHE_DIR={d} is not writable', str(cm.exception))

    def test_read_only_database_fails(self):
        d = self._populated()
        os.chmod(d / 'library.db', 0o444)
        with self.assertRaises(self.paths.StartupCheckError) as cm:
            self.paths.check_writable_dirs({'LUMA_DATA_DIR': d})
        self.assertIn('library.db cannot be opened for write', str(cm.exception))

    def test_process_exits_nonzero_with_clear_error(self):
        data, cache = _tmpdir(self, 'data_'), self._populated()
        os.chmod(cache, 0o555)
        env = dict(os.environ, LUMA_DATA_DIR=str(data), LUMA_CACHE_DIR=str(cache), HERO_CACHE_DIR=str(cache / 'hero'))
        r = subprocess.run([sys.executable, '-c', 'from app.paths import ensure_writable_or_exit; ensure_writable_or_exit()'],
                           cwd=REPO, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn('Luma startup check failed', r.stderr)
        self.assertIn(f'LUMA_CACHE_DIR={cache} is not writable', r.stderr)


class TestHeroStartupOnce(unittest.TestCase):
    def setUp(self):
        from app.hero_cache import HeroCache
        self.dir = _tmpdir(self, 'hero_')
        self.HeroCache = HeroCache
        self.hc = self._new()

    def _new(self):
        hc = self.HeroCache(cache_dir=self.dir / 'hero')
        hc.cache_json = self.dir / 'hero_content.json'
        return hc

    def _write(self, movies):
        self.hc.cache_json.write_text(json.dumps({'movies': movies}))

    def test_concurrent_refresh_runs_once_threads_and_instances(self):
        calls = []
        def slow(self_):
            calls.append(1); time.sleep(0.3); return []
        other = self._new()   # second process stand-in: same cache dir, own locks
        with patch.object(self.HeroCache, '_refresh_hero_content_unlocked', slow):
            ts = [threading.Thread(target=self.hc.refresh_hero_content) for _ in range(3)]
            ts.append(threading.Thread(target=other.refresh_hero_content))
            for t in ts: t.start()
            for t in ts: t.join()
        self.assertEqual(len(calls), 1)

    def test_scheduler_started_twice_refreshes_once(self):
        with patch.object(self.hc, '_refresh_hero_content_unlocked', return_value=[]) as refresh:
            self.hc.start_scheduler(heal_interval=3600)
            self.hc.start_scheduler(heal_interval=3600)
            time.sleep(0.3)
            self.hc.stop_scheduler()
        self.assertEqual(refresh.call_count, 1)

    def test_stale_json_with_missing_files_heals_on_startup(self):
        self._write([{'rating_key': 1, 'title': 'T', 'backdrop_url': 'movie_1_backdrop.jpg',
                      'backdrop_cache_file': 'movie_1_backdrop.jpg',
                      'backdrop_upstream_url': 'http://fanart.invalid/b.jpg'}])
        with patch.object(self.hc, 'download_image', return_value='movie_1_backdrop.jpg') as dl, \
                patch.object(self.hc, '_refresh_hero_content_unlocked') as refresh:
            self.assertEqual(self.hc.startup_sync(), 'heal')
        dl.assert_called_once()
        refresh.assert_not_called()

    def test_stale_legacy_json_triggers_single_refresh(self):
        self._write([{'rating_key': 1, 'title': 'T', 'backdrop_url': '/static/hero_cache/old.jpg'}])
        with patch.object(self.hc, '_refresh_hero_content_unlocked', return_value=[]) as refresh:
            self.assertEqual(self.hc.startup_sync(), 'heal')
        self.assertEqual(refresh.call_count, 1)

    def test_complete_cache_does_nothing(self):
        (self.dir / 'hero' / 'p.jpg').write_bytes(b'x')
        self._write([{'rating_key': 1, 'title': 'T', 'poster_url': 'p.jpg', 'poster_cache_file': 'p.jpg',
                      'poster_upstream_url': '/library/metadata/1/thumb'}])
        with patch.object(self.hc, '_refresh_hero_content_unlocked') as refresh, \
                patch.object(self.hc, 'download_image') as dl:
            self.assertIsNone(self.hc.startup_sync())
        refresh.assert_not_called(); dl.assert_not_called()

    def test_empty_cache_refreshes_once(self):
        with patch.object(self.hc, '_refresh_hero_content_unlocked', return_value=[]) as refresh:
            self.assertEqual(self.hc.startup_sync(), 'refresh')
        self.assertEqual(refresh.call_count, 1)


class TestLegacyHeroUrls(unittest.TestCase):
    def setUp(self):
        from app import main
        self.client = main.app.test_client()
        self.static = _tmpdir(self, 'static_')
        self.hero = _tmpdir(self, 'hero_')
        (self.static / 'hero_cache').mkdir()
        (self.static / 'hero_cache' / 'old.jpg').write_bytes(b'legacy')
        (self.static / 'secret.txt').write_text('no')
        (self.hero / 'new.jpg').write_bytes(b'new')
        (self.hero / 'both.jpg').write_bytes(b'new-both')
        (self.static / 'hero_cache' / 'both.jpg').write_bytes(b'legacy-both')
        TestSpaOutsideStatic._use_static(self, main.app, self.static)
        p = patch.dict(main.app.config, {'HERO_CACHE_DIR': str(self.hero)}); p.start(); self.addCleanup(p.stop)

    def test_new_location_served(self):
        r = self.client.get('/static/hero_cache/new.jpg')
        self.assertEqual((r.status_code, r.data), (200, b'new'))

    def test_new_location_wins(self):
        self.assertEqual(self.client.get('/static/hero_cache/both.jpg').data, b'new-both')

    def test_legacy_file_served(self):
        r = self.client.get('/static/hero_cache/old.jpg')
        self.assertEqual((r.status_code, r.data), (200, b'legacy'))

    def test_missing_and_traversal_404(self):
        self.assertEqual(self.client.get('/static/hero_cache/nope.jpg').status_code, 404)
        for bad in ('../secret.txt', '..%2fsecret.txt', '%2e%2e/secret.txt'):
            r = self.client.get('/static/hero_cache/' + bad)
            self.assertEqual(r.status_code, 404, bad)
            self.assertNotEqual(r.data, b'no')
