"""Log security: the whole formatted record is redacted (traceback included) on file and stream
handlers; log files are 0600 from creation and through rotation; old luma.log* are scrubbed at boot."""
import io
import logging
import os
import stat

import pytest

from app import log_files

SECRETS = ('abc123SECRET', 'deadbeefKEY', 'hunter2PW', 'eyJtokenBEARER', 'plexTOK9')


def _mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


@pytest.fixture
def logdir(tmp_path, monkeypatch):
    monkeypatch.setenv('LUMA_LOG_DIR', str(tmp_path / 'logs'))
    return tmp_path / 'logs'


@pytest.fixture
def root():
    lg = logging.getLogger('luma-sec-test')
    lg.setLevel(logging.INFO)
    lg.propagate = False
    yield lg
    for h in list(lg.handlers):
        lg.removeHandler(h)
        h.close()


def _boom():
    url = 'https://api.themoviedb.org/3/movie/1?api_key=abc123SECRET'
    headers = {'X-Api-Key': 'deadbeefKEY', 'Authorization': 'Bearer eyJtokenBEARER'}
    raise RuntimeError(f'401 for url: {url} headers={headers} password=hunter2PW X-Plex-Token=plexTOK9')


def test_an_api_key_in_an_exception_never_reaches_the_file_or_stdout(logdir, root):
    fh = log_files.attach(root=root)
    buf = io.StringIO()
    sh = logging.StreamHandler(buf)
    root.addHandler(sh)
    log_files.redact_handlers(root)
    try:
        _boom()
    except RuntimeError:
        root.exception('TMDB failed')                      # message + traceback (exc_text)
    root.info('sent %s', {'password': 'hunter2PW', 'apikey': 'deadbeefKEY'})
    fh.flush()
    text = (logdir / 'luma.log').read_text()
    for out in (text, buf.getvalue()):
        assert 'Traceback' in out and 'RuntimeError' in out and '_boom' in out    # the traceback is kept...
        for s in SECRETS:
            assert s not in out                                              # ...with every secret masked
        assert 'api_key=***' in out and '***' in out


def test_files_are_0600_on_create_and_after_rollover(logdir, root, monkeypatch):
    monkeypatch.setattr(log_files, 'MAX_BYTES', 300)
    os.umask(0o022)
    h = log_files.attach(root=root)
    root.info('first line')
    h.flush()
    assert _mode(logdir / 'luma.log') == 0o600
    assert _mode(logdir) == 0o700
    for i in range(40):
        root.info('line %d %s', i, 'x' * 40)
    h.flush()
    rotated = sorted(p for p in logdir.iterdir() if p.name.startswith('luma.log.'))
    assert len(rotated) == log_files.BACKUPS                    # rolled over (and capped at 5)
    for p in [logdir / 'luma.log', *rotated]:
        assert _mode(p) == 0o600, p


def test_boot_scrubs_old_logs_redacted_and_0600(logdir, root):
    logdir.mkdir(parents=True)
    old = logdir / 'luma.log'
    old.write_text('2026-10-17 ERROR GET /3/x?api_key=abc123SECRET\nTraceback ...\nX-Api-Key: deadbeefKEY\nok line\n')
    os.chmod(old, 0o644)
    r1 = logdir / 'luma.log.1'
    r1.write_text('{"password": "hunter2PW"}\n')
    os.chmod(r1, 0o640)
    other = logdir / 'other.txt'
    other.write_text('api_key=keepme\n')
    log_files.attach(root=root)
    for p in (old, r1):
        t = p.read_text()
        assert _mode(p) == 0o600 and not any(s in t for s in SECRETS)
    assert 'ok line' in old.read_text() and 'Traceback' in old.read_text()
    assert other.read_text() == 'api_key=keepme\n'                 # only luma.log*
    assert not [p for p in logdir.iterdir() if '.scrub-' in p.name]


def test_redact_keeps_ordinary_text():
    for t in ('tvsid: 5', 'Insider: x', '3 tokens left', 'Season 2 of Severance'):
        assert log_files.redact(t) == t
