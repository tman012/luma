"""BeyondHD API answers (POST /api/torrents/<key>, action=search) for a title
with many uploads: page 1 is 100 newer Remux/Encode rows, the WEB-DL/WEBRip
rows (uploaded first, at the digital release) are on page 2."""

RK = 'rsskey0123456789abcdef'


def row(i, name, bhd_type, created):
    return {
        'id': 900000 + i, 'name': name, 'folder_name': name.replace(' ', '.'),
        'info_hash': f'{i:040x}', 'size': 10_000_000_000 + i, 'uploaded_by': 'Anonymous',
        'category': 'Movies', 'type': bhd_type, 'seeders': 50 + i % 40, 'leechers': i % 5,
        'times_completed': 100 + i, 'imdb_id': 'tt15239678', 'tmdb_id': '693134',
        'bhd_rating': 8.4, 'tmdb_rating': 8.2, 'imdb_rating': 8.5, 'tv_pack': 0,
        'promo25': 0, 'promo50': 0, 'promo75': 0, 'freeleech': 0, 'rewind': 0, 'refund': 0,
        'limited': 0, 'rescue': 0, 'bumped_at': created, 'created_at': created,
        'url': f'https://beyond-hd.me/details/{900000 + i}',
        'download_url': f'https://beyond-hd.me/torrent/download/auto.{900000 + i}.{RK}',
    }


def page1():
    rows = []
    for i in range(100):
        if i % 2:
            rows.append(row(i, f'Dune Part Two 2024 2160p UHD Blu-ray Remux DV HDR HEVC TrueHD Atmos 7 1-G{i}',
                            'UHD Remux', '2024-07-20 10:00:00'))
        else:
            rows.append(row(i, f'Dune Part Two 2024 1080p BluRay DD+ 7 1 x264-E{i}', '1080p',
                            '2024-07-10 10:00:00'))
    return {'status_code': 1, 'page': 1, 'results': rows, 'total_pages': 2, 'total_results': 104,
            'success': True}


WEB_NAMES = [
    ('Dune Part Two 2024 2160p MA WEB-DL DDP 5 1 Atmos DV HDR H 265-FLUX', '2160p'),
    ('Dune Part Two 2024 1080p MA WEB-DL DDP 5 1 Atmos H 264-FLUX', '1080p'),
    ('Dune Part Two 2024 1080p AMZN WEBRip DDP 5 1 x264-NTb', '1080p'),
    ('Dune Part Two 2024 720p MA WEB-DL DDP 5 1 H 264-FLUX', '720p'),
]


def page2():
    rows = [row(200 + i, n, t, '2024-04-16 10:00:00') for i, (n, t) in enumerate(WEB_NAMES)]
    return {'status_code': 1, 'page': 2, 'results': rows, 'total_pages': 2, 'total_results': 104,
            'success': True}
