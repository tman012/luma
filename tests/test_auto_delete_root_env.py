"""auto_delete_guard._root resolves the delete folder exactly as
storage_cleanup.downloads_root() does, LUMA_DOWNLOADS_ROOT included."""
from app import auto_delete_guard as g, storage_cleanup


def _s(root=None, enabled=False):
    d = {'automation': {'downloads_guard': {'enabled': enabled}}}
    if root is not None:
        d['media'] = {'downloads_root': root}
    return d


def test_env_root_decides_like_storage_cleanup(tmp_path, monkeypatch):
    dl = tmp_path / 'dl'
    dl.mkdir()
    monkeypatch.setenv('LUMA_DOWNLOADS_ROOT', str(dl))
    import os
    assert g._root(g._View(_s('/elsewhere'))) == os.path.realpath(storage_cleanup.downloads_root()) == str(dl)
    # the setting moves nothing while the env var decides: not a widening
    assert g._widens_auto_delete(_s('/downloads'), _s('/somewhere/else')) == []
    assert g._widens_auto_delete(_s('/downloads', True), _s('/somewhere/else', True)) == []


def test_without_env_a_settings_move_still_widens(monkeypatch):
    monkeypatch.delenv('LUMA_DOWNLOADS_ROOT', raising=False)
    assert g._widens_auto_delete(_s('/downloads'), _s('/somewhere/else')) == ['media.downloads_root']
    assert g._widens_auto_delete(_s('/downloads'), _s('/downloads/')) == []
