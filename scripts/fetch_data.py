#!/usr/bin/env python3
"""
Daily data fetcher for יוצאים בשאלה podcast dashboard.
Fetches only episodes tagged "יוצאים בשאלה" from the @hillel-il YouTube channel.
Spreaker / Spotify sections are left intact from existing data.json (manual update).
"""
import json, sys, time, datetime, subprocess, os

import requests

SPREAKER_SHOW_ID = os.environ.get('SPREAKER_SHOW_ID', '4956945')
YOUTUBE_CHANNEL  = os.environ.get('YOUTUBE_CHANNEL', '@hillel-il')
PODCAST_FILTER   = 'יוצאים בשאלה'   # only videos whose title contains this


# ── YouTube ───────────────────────────────────────────────────────────────────

def fetch_youtube_channel():
    channel_url = f'https://www.youtube.com/{YOUTUBE_CHANNEL}'
    try:
        result = subprocess.run(
            ['yt-dlp', '--flat-playlist', '--dump-json', '--quiet', channel_url],
            capture_output=True, text=True, timeout=180
        )
        all_videos = []
        for line in result.stdout.strip().split('\n'):
            line = line.strip()
            if not line:
                continue
            try:
                v = json.loads(line)
                view_count   = int(v.get('view_count') or 0)
                upload_date  = str(v.get('upload_date', '') or '')
                date_str = (
                    f'{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}'
                    if len(upload_date) == 8 else ''
                )
                all_videos.append({
                    'id':    v.get('id', ''),
                    'title': v.get('title', ''),
                    'date':  date_str,
                    'views': view_count,
                    'url':   f'https://www.youtube.com/watch?v={v.get("id", "")}',
                })
            except Exception:
                continue

        # Filter to only יוצאים בשאלה episodes
        podcast_videos = [v for v in all_videos if PODCAST_FILTER in v.get('title', '')]
        podcast_videos_sorted = sorted(podcast_videos, key=lambda x: x['date'], reverse=True)

        total_views = sum(v['views'] for v in podcast_videos)
        return {
            'channel_url':   channel_url,
            'total_views':   total_views,
            'video_count':   len(podcast_videos),
            'videos':        podcast_videos_sorted,
            'monthly':       _youtube_monthly(podcast_videos),
        }
    except Exception as e:
        print(f'YouTube error: {e}', file=sys.stderr)
        return {
            'channel_url':  channel_url,
            'total_views':  0,
            'video_count':  0,
            'videos':       [],
            'monthly':      [],
            'error':        str(e),
        }


def _youtube_monthly(videos):
    monthly = {}
    for v in videos:
        d = v.get('date', '')
        if d and len(d) >= 7:
            m = d[:7]
            monthly[m] = monthly.get(m, 0) + v['views']
    return [{'month': m, 'views': p} for m, p in sorted(monthly.items())]


# ── Spreaker (audio) ──────────────────────────────────────────────────────────

def fetch_spreaker_episodes():
    """Fetch Spreaker episodes — returns empty list if show has no content yet."""
    episodes = []
    url = (
        f'https://api.spreaker.com/v2/shows/{SPREAKER_SHOW_ID}'
        f'/episodes?limit=100&filter=listenable'
    )
    page = 0
    while url:
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            data     = r.json()
            response = data.get('response', {})
            items    = response.get('items', [])
            for ep in items:
                pub = ep.get('published_at', '') or ''
                episodes.append({
                    'id':       ep.get('episode_id', ''),
                    'title':    ep.get('title', ''),
                    'date':     pub[:10],
                    'plays':    int(ep.get('plays', 0) or 0),
                    'duration': int(ep.get('duration', 0) or 0),
                })
            url = response.get('next_url') or None
            page += 1
            if url:
                time.sleep(0.3)
        except Exception as e:
            print(f'Spreaker page {page} error: {e}', file=sys.stderr)
            break
    return episodes


def compute_monthly(episodes):
    monthly = {}
    for ep in episodes:
        d = ep.get('date', '')
        if d and len(d) >= 7:
            m = d[:7]
            monthly[m] = monthly.get(m, 0) + ep['plays']
    return [{'month': m, 'plays': p} for m, p in sorted(monthly.items())]


# ── Main ──────────────────────────────────────────────────────────────────────

def load_existing(path='data.json'):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def main():
    print(f'⏳ Fetching YouTube ({PODCAST_FILTER} only from {YOUTUBE_CHANNEL}) …')
    youtube = fetch_youtube_channel()
    print(f'   {youtube["video_count"]} episodes · {youtube["total_views"]:,} total views')

    print('⏳ Fetching Spreaker episodes …')
    episodes    = fetch_spreaker_episodes()
    total_audio = sum(ep['plays'] for ep in episodes)
    print(f'   {len(episodes)} episodes · {total_audio:,} total plays')

    existing = load_existing()

    data = {
        'updated': datetime.date.today().isoformat(),
        'show': {
            'name':             'יוצאים בשאלה',
            'spreaker_show_id': SPREAKER_SHOW_ID,
            'youtube_channel':  f'https://www.youtube.com/{YOUTUBE_CHANNEL}',
        },
        'audio': {
            'total_plays':   total_audio,
            'episode_count': len(episodes),
            'episodes':      episodes,
            'monthly':       compute_monthly(episodes),
        },
        'video': {
            'youtube': youtube,
            'spotify_video': existing.get('video', {}).get('spotify_video', {
                'total_plays': 0,
                'episodes':    [],
                'note':        'עדכן ידנית מ-Spotify for Creators',
            }),
        },
    }

    out_path = os.path.join(os.path.dirname(__file__), '..', 'data.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f'✅ data.json written ({len(episodes)} audio eps, {youtube["video_count"]} YT podcast eps)')


if __name__ == '__main__':
    main()
