#!/usr/bin/env python3
import json, sys

with open('metadata/captured_apis/api_traffic.jsonl') as f:
    entries = [json.loads(l) for l in f if l.strip()]

# Find next-episode-autoplay response
for e in entries:
    if 'api.kukufm.com' in e.get('host','') and 'next-episode-autoplay' in e.get('path',''):
        body = e.get('res_body', {})
        next_eps = body.get('next_episodes', [])
        if next_eps:
            ep = next_eps[0]
            print('Episode keys:', list(ep.keys()))
            print()
            print('Full episode JSON (first):')
            print(json.dumps(ep, indent=2)[:3000])
        break

print("\n\n=== Looking for video_url / uuid in any episode ===")
for e in entries:
    body = e.get('res_body', {})
    # deep search for uuid and video_id patterns
    body_str = json.dumps(body)
    if 'video-episode' in body_str or 'video_url' in body_str.lower() or '"uuid"' in body_str:
        host = e.get('host','')
        path = e.get('path','')
        print(f"Found in: {e['method']} {host}{path}")
        # find the relevant keys
        if isinstance(body, dict):
            for k, v in body.items():
                if isinstance(v, dict):
                    if 'uuid' in v or 'video_url' in str(v).lower() or 'video-episode' in str(v):
                        print(f"  key: {k} -> {json.dumps(v)[:300]}")
                elif isinstance(v, list) and v:
                    for item in v[:3]:
                        if isinstance(item, dict) and ('uuid' in item or 'video_url' in str(item).lower()):
                            print(f"  list item keys: {list(item.keys())}")
                            print(f"  sample: {json.dumps(item)[:500]}")
                            break
        break

