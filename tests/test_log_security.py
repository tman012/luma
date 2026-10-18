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


# ---------------------------------------------------------------- v158a (Reviewer follow-ups)

def test_v158a_a_pin_is_redacted_as_a_json_key_and_a_param(logdir, root, capsys):
    import sys
    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(log_files.RedactingFormatter(log_files.FORMAT))
    root.addHandler(stream)
    h = log_files.attach(root=root)
    root.info('{"apikey": "x", "pin": "S"}')
    root.info("plex link pin=4821 and {'pin': 'Q7'} done")
    h.flush()
    text = (logdir / 'luma.log').read_text()
    err = capsys.readouterr().err
    for out in (text, err):
        assert '"pin": "***"' in out and '"apikey": "***"' in out
        assert '"S"' not in out and '4821' not in out and 'Q7' not in out
    assert log_files.redact('spinning pinned pins: 3') == 'spinning pinned pins: 3'          # words, not keys


def test_v158a_handlers_added_after_import_redact_even_with_a_plain_formatter(root, monkeypatch):
    pristine = log_files._orig_handler_format or logging.Handler.format                  # app.main may have installed it
    monkeypatch.setattr(logging.Handler, 'format', pristine)                             # restored after
    log_files.install()
    patched = logging.Handler.format
    log_files.install()
    assert logging.Handler.format is patched                                             # idempotent
    buf = io.StringIO()
    late = logging.StreamHandler(buf)
    late.setFormatter(logging.Formatter('%(message)s'))                                  # not ours
    root.addHandler(late)
    try:
        raise RuntimeError('GET /x?api_key=abc123SECRET')
    except RuntimeError:
        root.exception('boom pin=1234')
    out = buf.getvalue()
    assert 'abc123SECRET' not in out and '1234' not in out and 'api_key=***' in out and 'Traceback' in out
    assert out.count('***') == 3                                                        # message, exception line, traceback line
    assert '******' not in out


def test_v158a_gunicorn_error_and_access_handlers_redact(monkeypatch):
    from gunicorn.config import Config
    from gunicorn.glogging import Logger
    monkeypatch.setattr(logging.Handler, 'format', log_files._orig_handler_format or logging.Handler.format)
    saved = {n: (list(logging.getLogger(n).handlers), logging.getLogger(n).propagate) for n in log_files.GUNICORN_LOGGERS}

    def restore():
        for n, (hs, prop) in saved.items():
            lg = logging.getLogger(n)
            lg.handlers[:] = hs
            lg.propagate = prop
    cfg = Config()
    cfg.set('accesslog', '-')
    cfg.set('errorlog', '-')
    glog = Logger(cfg)
    bufs = {}
    for name in log_files.GUNICORN_LOGGERS:
        lg = logging.getLogger(name)
        assert lg.handlers and lg.propagate is False
        bufs[name] = io.StringIO()
        for h in lg.handlers:
            h.setStream(bufs[name])
    log_files.redact_gunicorn()
    for name in log_files.GUNICORN_LOGGERS:
        assert all(isinstance(h.formatter, log_files.RedactingFormatter) for h in logging.getLogger(name).handlers)
    glog.error('upstream said X-Api-Key: deadbeefKEY')
    logging.getLogger('gunicorn.access').info('%s', '"GET /api/x?apikey=abc123SECRET&token=plexTOK9 HTTP/1.1" 200')
    allout = bufs['gunicorn.error'].getvalue() + bufs['gunicorn.access'].getvalue()
    restore()
    assert 'deadbeefKEY' not in allout and 'abc123SECRET' not in allout and 'plexTOK9' not in allout
    assert 'apikey=***&token=***' in allout


def test_v158a_gunicorn_conf_wires_the_redaction():
    src = open(os.path.join(os.path.dirname(__file__), '..', 'gunicorn.conf.py')).read()
    assert '_log_files.install()' in src
    for hook in ('def on_starting', 'def post_fork', 'def post_worker_init'):
        body = src.split(hook, 1)[1].split('\ndef ', 1)[0]
        assert '_log_files.redact_gunicorn()' in body, hook


def test_v158a_scrub_keeps_invalid_bytes_as_they_were(logdir):
    logdir.mkdir(parents=True)
    raw = b'2026 - a - INFO - bad \xff\xfe bytes api_key=abc123SECRET \xc3\x28 end\r\nnext line\n'
    (logdir / 'luma.log.1').write_bytes(raw)
    assert log_files.scrub(str(logdir)) == 1
    got = (logdir / 'luma.log.1').read_bytes()
    assert got == raw.replace(b'abc123SECRET', b'***')                                   # every other byte intact, \r\n kept
    assert _mode(logdir / 'luma.log.1') == 0o600
