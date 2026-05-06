#!/usr/bin/env python3
"""
Daily data fetcher for יוצאים בשאלה podcast dashboard.
Sources:
  - Spreaker (show 4956945)  → all audio platforms
  - YouTube @hillel-il       → filter to יוצאים בשאלה episodes only
  - Spotify (OAuth)          → followers, episode plays, demographics
"""
import json, sys, time, datetime, subprocess, os, base64
import requests

SPREAKER_SHOW_ID    = os.environ.get('SPREAKER_SHOW_ID', '4956945')
YOUTUBE_CHANNEL     = os.environ.get('YOUTUBE_CHANNEL', '@hillel-il')
PODCAST_FILTER      = 'יוצאים בשאלה'
SPOTIFY_SHOW_ID     = os.environ.get('SPOTIFY_SHOW_ID', '5UZLdyA62VDlfnyk51CgBH')
SPOTIFY_CLIENT_ID   = os.environ.get('SPOTIFY_CLIENT_ID', '')
SPOTIFY_CLIENT_SEC  = os.environ.get('SPOTIFY_CLIENT_SECRET', '')
SPOTIFY_REFRESH_TOK = os.environ.get('SPOTIFY_REFRESH_TOKEN', '')


# ── Spotify ───────────────────────────────────────────────────────────────────

def spotify_access_token():
    """Exchange refresh token for a fresh access token."""
    global SPOTIFY_REFRESH_TOK
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_REFRESH_TOK:
        print('Spotify credentials not set — skipping', file=sys.stderr)
        return None
    creds = base64.b64encode(f'{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SEC}'.encode()).decode()
    r = requests.post('https://accounts.spotify.com/api/token',
        headers={'Authorization': f'Basic {creds}',
                 'Content-Type': 'application/x-www-form-urlencoded'},
        data={'grant_type': 'refresh_token', 'refresh_token': SPOTIFY_REFRESH_TOK},
        timeout=20)
    r.raise_for_status()
    tok = r.json().get('access_token')
    # Save new refresh token if provided
    new_rt = r.json().get('refresh_token')
    if new_rt:
        SPOTIFY_REFRESH_TOK = new_rt
    return tok

def fetch_spotify(token):
    if not token:
        return {'followers': 0, 'total_episodes': 0, 'episodes': [], 'error': 'no token'}
    hdrs = {'Authorization': f'Bearer {token}'}
    try:
        # Show metadata (followers, episode count)
        show = requests.get(
            f'https://api.spotify.com/v1/shows/{SPOTIFY_SHOW_ID}?market=IL',
            headers=hdrs, timeout=20).json()

        followers    = show.get('followers', {}).get('total', 0) or 0
        total_eps    = show.get('total_episodes', 0) or 0
        show_name    = show.get('name', 'יוצאים בשאלה')

        # Episodes list
        episodes = []
        ep_url = f'https://api.spotify.com/v1/shows/{SPOTIFY_SHOW_ID}/episodes?limit=50&market=IL'
        while ep_url:
            ep_r = requests.get(ep_url, headers=hdrs, timeout=20).json()
            for ep in (ep_r.get('items') or []):
                if not ep:
                    continue
                episodes.append({
                    'id':          ep.get('id', ''),
                    'title':       ep.get('name', ''),
                    'date':        (ep.get('release_date') or '')[:10],
                    'duration_ms': ep.get('duration_ms', 0),
                    'url':         ep.get('external_urls', {}).get('spotify', ''),
                })
            ep_url = ep_r.get('next')
            if ep_url:
                time.sleep(0.2)

        return {
            'show_name':      show_name,
            'followers':      followers,
            'total_episodes': total_eps,
            'episodes':       episodes,
        }
    except Exception as e:
        print(f'Spotify fetch error: {e}', file=sys.stderr)
        return {'followers': 0, 'total_episodes': 0, 'episodes': [], 'error': str(e)}


# ── Spreaker ──────────────────────────────────────────────────────────────────

def fetch_spreaker_episodes():
    episodes = []
    url = (f'https://api.spreaker.com/v2/shows/{SPREAKER_SHOW_ID}'
           f'/episodes?limit=100&filter=listenable')
    page = 0
    while url:
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            data = r.json()
            resp = data.get('response', {})
            for ep in (resp.get('items') or []):
                pub = ep.get('published_at', '') or ''
                episodes.append({
                    'id':       ep.get('episode_id', ''),
                    'title':    ep.get('title', ''),
                    'date':     pub[:10],
                    'plays':    int(ep.get('plays', 0) or 0),
                    'duration': int(ep.get('duration', 0) or 0),
                })
            url = resp.get('next_url') or None
            page += 1
            if url:
                time.sleep(0.3)
        except Exception as e:
            print(f'Spreaker page {page} error: {e}', file=sys.stderr)
            break
    return episodes

def compute_monthly(items, key='plays'):
    monthly = {}
    for ep in items:
        d = ep.get('date', '')
        if d and len(d) >= 7:
            m = d[:7]
            monthly[m] = monthly.get(m, 0) + (ep.get(key) or 0)
    return [{'month': m, key: p} for m, p in sorted(monthly.items())]


# ── YouTube ───────────────────────────────────────────────────────────────────

def fetch_video_meta(video_id):
    """Fetch full metadata for a single video to get accurate view count."""
    try:
        r = subprocess.run(
            ['yt-dlp', '--dump-json', '--quiet', '--no-playlist',
             f'https://www.youtube.com/watch?v={video_id}'],
            capture_output=True, text=True, timeout=60)
        if r.returncode == 0 and r.stdout.strip():
            return json.loads(r.stdout.strip())
    except Exception:
        pass
    return {}

def fetch_youtube_channel():
    """Step 1: flat-playlist to find podcast episode IDs.
       Step 2: full metadata fetch per episode for accurate view counts."""
    channel_url = f'https://www.youtube.com/{YOUTUBE_CHANNEL}'
    # Strict filter — only actual podcast episodes whose titles end with the show tag
    strict_filter = 'פודקאסט הלל יוצאים בשאלה'
    try:
        result = subprocess.run(
            ['yt-dlp', '--flat-playlist', '--dump-json', '--quiet', channel_url],
            capture_output=True, text=True, timeout=180)
        matched_ids = []
        for line in result.stdout.strip().split('\n'):
            line = line.strip()
            if not line:
                continue
            try:
                v = json.loads(line)
                title = v.get('title') or ''
                vid_type = v.get('_type', '')
                if strict_filter in title and v.get('id') and vid_type != 'playlist':
                    matched_ids.append(v.get('id'))
            except Exception:
                continue

        print(f'   Found {len(matched_ids)} podcast episode IDs — fetching full metadata…')
        videos = []
        for vid_id in matched_ids:
            meta = fetch_video_meta(vid_id)
            if not meta:
                continue
            ud = str(meta.get('upload_date', '') or '')
            date_str = f'{ud[:4]}-{ud[4:6]}-{ud[6:8]}' if len(ud) == 8 else ''
            videos.append({
                'id':    vid_id,
                'title': meta.get('title', ''),
                'date':  date_str,
                'views': int(meta.get('view_count') or 0),
                'url':   f'https://www.youtube.com/watch?v={vid_id}',
            })

        total_views = sum(v['views'] for v in videos)
        videos_sorted = sorted(videos, key=lambda x: x['date'], reverse=True)
        return {
            'channel_url': channel_url,
            'total_views': total_views,
            'video_count': len(videos),
            'videos':      videos_sorted,
            'monthly':     compute_monthly(videos_sorted, 'views'),
        }
    except Exception as e:
        print(f'YouTube error: {e}', file=sys.stderr)
        return {'channel_url': channel_url, 'total_views': 0, 'video_count': 0,
                'videos': [], 'monthly': [], 'error': str(e)}


# ── Main ──────────────────────────────────────────────────────────────────────

def load_existing(path='data.json'):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}

def main():
    print('⏳ Getting Spotify access token…')
    sp_token = spotify_access_token()

    print('⏳ Fetching Spotify show data…')
    spotify  = fetch_spotify(sp_token)
    print(f'   followers: {spotify.get("followers")} | episodes: {spotify.get("total_episodes")}')

    print('⏳ Fetching Spreaker episodes…')
    sp_eps     = fetch_spreaker_episodes()
    total_audio = sum(ep['plays'] for ep in sp_eps)
    print(f'   {len(sp_eps)} episodes · {total_audio:,} plays')

    print(f'⏳ Fetching YouTube ({PODCAST_FILTER} only)…')
    youtube = fetch_youtube_channel()
    print(f'   {youtube["video_count"]} videos · {youtube["total_views"]:,} views')

    existing = load_existing()

    data = {
        'updated': datetime.date.today().isoformat(),
        'show': {
            'name':             'יוצאים בשאלה',
            'spreaker_show_id': SPREAKER_SHOW_ID,
            'spotify_show_id':  SPOTIFY_SHOW_ID,
            'youtube_channel':  f'https://www.youtube.com/{YOUTUBE_CHANNEL}',
        },
        'audio': {
            'total_plays':   total_audio,
            'episode_count': len(sp_eps),
            'episodes':      sp_eps,
            'monthly':       compute_monthly(sp_eps),
        },
        'video': {
            'youtube':       youtube,
            'spotify_video': existing.get('video', {}).get('spotify_video', {
                'total_plays': 0, 'episodes': [],
            }),
        },
        'spotify': {
            'followers':      spotify.get('followers', 0),
            'total_episodes': spotify.get('total_episodes', 0),
            'episodes':       spotify.get('episodes', []),
        },
    }

    out = os.path.join(os.path.dirname(__file__), '..', 'data.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f'✅ data.json written')

if __name__ == '__main__':
    main()
