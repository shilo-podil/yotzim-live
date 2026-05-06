#!/usr/bin/env python3
"""
Daily data fetcher for podcast dashboard.
Spreaker stats via the private CMS OAuth token (SPREAKER_STATS_TOKEN).
Spotify via OAuth refresh token.  YouTube via yt-dlp flat-playlist.
"""
import json, sys, time, datetime, subprocess, os, requests

# Config
SPREAKER_SHOW_ID    = os.environ.get('SPREAKER_SHOW_ID', '4956945')
SPREAKER_STATS_TOK  = os.environ.get('SPREAKER_STATS_TOKEN', '')
YOUTUBE_CHANNEL     = os.environ.get('YOUTUBE_CHANNEL', '@hillel-il')
PODCAST_FILTER      = 'יוצאים בשאלה'

SPOTIFY_CLIENT_ID   = os.environ.get('SPOTIFY_CLIENT_ID', '')
SPOTIFY_CLIENT_SEC  = os.environ.get('SPOTIFY_CLIENT_SECRET', '')
SPOTIFY_REFRESH_TOK = os.environ.get('SPOTIFY_REFRESH_TOKEN', '')
SPOTIFY_SHOW_ID     = '5UZLdyA62VDlfnyk51CgBH'

KNOWN_YT_IDS = ['xbq8AYlRHc4']


def _spreaker_get(path, params=None):
    if not SPREAKER_STATS_TOK:
        return None
    p = dict(params or {})
    p['oauth2_access_token'] = SPREAKER_STATS_TOK
    try:
        r = requests.get(f'https://api.spreaker.com/v2{path}', params=p, timeout=30)
        r.raise_for_status()
        return r.json().get('response', {})
    except Exception as e:
        print(f'Spreaker API error {path}: {e}', file=sys.stderr)
        return None


def fetch_spreaker_stats():
    today    = datetime.date.today().isoformat()
    from_30d = (datetime.date.today() - datetime.timedelta(days=29)).isoformat()
    from_all = '2016-01-01'

    r_all = _spreaker_get(f'/shows/{SPREAKER_SHOW_ID}/statistics/plays/totals',
                          {'from': from_all, 'to': today})
    total_all_time = (r_all or {}).get('statistics', {}).get('downloads_count', 0)

    r_30 = _spreaker_get(f'/shows/{SPREAKER_SHOW_ID}/statistics/plays/totals',
                         {'from': from_30d, 'to': today})
    total_30d = (r_30 or {}).get('statistics', {}).get('downloads_count', 0)

    r_daily = _spreaker_get(f'/shows/{SPREAKER_SHOW_ID}/statistics/plays',
                            {'from': from_30d, 'to': today, 'group': 'day'})
    daily = [{'date': d['date'], 'downloads': d['downloads_count']}
             for d in (r_daily or {}).get('statistics', [])]

    r_eps = _spreaker_get(f'/shows/{SPREAKER_SHOW_ID}/episodes/statistics/plays/totals',
                          {'from': from_30d, 'to': today, 'limit': 20})
    episode_stats_30d = {}
    for item in (r_eps or {}).get('items', []):
        episode_stats_30d[item['episode_id']] = item['downloads_count']

    episodes = []
    url = (f'https://api.spreaker.com/v2/shows/{SPREAKER_SHOW_ID}'
           f'/episodes?limit=100&filter=listenable')
    while url:
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            data = r.json().get('response', {})
            for ep in data.get('items', []):
                eid = ep.get('episode_id', '')
                episodes.append({
                    'id':       eid,
                    'title':    ep.get('title', ''),
                    'date':     (ep.get('published_at', '') or '')[:10],
                    'plays':    episode_stats_30d.get(eid, 0),
                    'duration': int(ep.get('duration', 0) or 0),
                })
            url = data.get('next_url') or None
            if url:
                time.sleep(0.3)
        except Exception as e:
            print(f'Spreaker episodes error: {e}', file=sys.stderr)
            break

    episodes.sort(key=lambda x: x['date'], reverse=True)
    return {
        'total_downloads_all_time': total_all_time,
        'total_downloads_30d':      total_30d,
        'daily':                    daily,
        'episode_count':            len(episodes),
        'episodes':                 episodes,
    }


def fetch_youtube():
    channel_url = f'https://www.youtube.com/{YOUTUBE_CHANNEL}'
    try:
        result = subprocess.run(
            ['yt-dlp', '--flat-playlist', '--dump-json', '--quiet', channel_url],
            capture_output=True, text=True, timeout=180)
        videos = {}
        for line in result.stdout.strip().split('\n'):
            if not line.strip():
                continue
            try:
                v = json.loads(line)
                title  = v.get('title') or ''
                vid_id = v.get('id', '')
                vtype  = v.get('_type', '')
                if (PODCAST_FILTER in title and 'פודקאסט' in title
                        and vid_id and vtype != 'playlist'):
                    ud = str(v.get('upload_date', '') or '')
                    videos[vid_id] = {
                        'id': vid_id, 'title': title,
                        'date': (f'{ud[:4]}-{ud[4:6]}-{ud[6:8]}' if len(ud) == 8 else ''),
                        'views': int(v.get('view_count') or 0),
                        'url': f'https://www.youtube.com/watch?v={vid_id}',
                    }
            except Exception:
                continue
        for vid_id in KNOWN_YT_IDS:
            if vid_id not in videos:
                videos[vid_id] = {'id': vid_id, 'title': '', 'date': '', 'views': 0,
                                  'url': f'https://www.youtube.com/watch?v={vid_id}'}
        vlist = sorted(videos.values(), key=lambda x: x['date'], reverse=True)
        return {'channel_url': channel_url, 'total_views': sum(v['views'] for v in vlist),
                'video_count': len(vlist), 'videos': vlist}
    except Exception as e:
        print(f'YouTube error: {e}', file=sys.stderr)
        return {'channel_url': channel_url, 'total_views': 0, 'video_count': 0,
                'videos': [], 'error': str(e)}


def _spotify_token():
    global SPOTIFY_REFRESH_TOK
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_REFRESH_TOK:
        return None
    try:
        r = requests.post('https://accounts.spotify.com/api/token', data={
            'grant_type': 'refresh_token', 'refresh_token': SPOTIFY_REFRESH_TOK,
            'client_id': SPOTIFY_CLIENT_ID, 'client_secret': SPOTIFY_CLIENT_SEC,
        }, timeout=20)
        r.raise_for_status()
        d = r.json()
        if 'refresh_token' in d:
            SPOTIFY_REFRESH_TOK = d['refresh_token']
        return d.get('access_token')
    except Exception as e:
        print(f'Spotify token error: {e}', file=sys.stderr)
        return None


def fetch_spotify():
    token = _spotify_token()
    if not token:
        return {'followers': 0, 'total_episodes': 0, 'episodes': [], 'error': 'no token'}
    try:
        r = requests.get(f'https://api.spotify.com/v1/shows/{SPOTIFY_SHOW_ID}?market=IL',
                         headers={'Authorization': f'Bearer {token}'}, timeout=20)
        r.raise_for_status()
        show = r.json()
        eps = [{'id': ep.get('id', ''), 'title': ep.get('name', ''),
                'release_date': ep.get('release_date', ''),
                'duration_ms': ep.get('duration_ms', 0)}
               for ep in show.get('episodes', {}).get('items', [])[:20]]
        return {'followers': show.get('followers', {}).get('total', 0),
                'total_episodes': show.get('total_episodes', 0), 'episodes': eps}
    except Exception as e:
        print(f'Spotify show error: {e}', file=sys.stderr)
        return {'followers': 0, 'total_episodes': 0, 'episodes': [], 'error': str(e)}


def main():
    print('Fetching Spreaker stats...')
    spreaker = fetch_spreaker_stats()
    print(f'  {spreaker["episode_count"]} episodes, '
          f'{spreaker["total_downloads_all_time"]:,} all-time downloads, '
          f'{spreaker["total_downloads_30d"]:,} last 30d, '
          f'{len(spreaker["daily"])} daily points')

    print('Fetching YouTube...')
    youtube = fetch_youtube()
    print(f'  {youtube["video_count"]} videos, {youtube["total_views"]:,} views')

    print('Fetching Spotify...')
    spotify = fetch_spotify()
    print(f'  {spotify["total_episodes"]} episodes, {spotify["followers"]:,} followers')

    data = {
        'updated': datetime.date.today().isoformat(),
        'show': {'name': 'יוצאים בשאלה',
                 'spreaker_show_id': SPREAKER_SHOW_ID},
        'audio': {
            'total_downloads_all_time': spreaker['total_downloads_all_time'],
            'total_downloads_30d':      spreaker['total_downloads_30d'],
            'episode_count':            spreaker['episode_count'],
            'daily':                    spreaker['daily'],
            'episodes':                 spreaker['episodes'],
        },
        'video': {'youtube': youtube},
        'spotify': spotify,
    }

    out_path = os.path.join(os.path.dirname(__file__), '..', 'data.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print('data.json written')


if __name__ == '__main__':
    main()
