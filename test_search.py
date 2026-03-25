import yt_dlp

try:
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'default_search': 'ytsearch',
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        res = ydl.extract_info('ytsearch10:bad bunny', download=False)
    print('entries', len(res.get('entries', [])))
    first = res.get('entries', [None])[0]
    print('first', first)
except Exception as e:
    import traceback
    traceback.print_exc()
