#!/usr/bin/env python3
"""Create a video index, inspect representative frames, and hash completed evidence."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('root',type=Path)
    p.add_argument('--allow-partial',action='store_true',help='Index only accepted stages of a stopped failed study')
    a = p.parse_args()
    root = a.root.resolve()
    status = json.loads((root/'state.json').read_text())['status']
    if status != 'completed' and not (a.allow_partial and status in ('failed','interrupted')):
        raise RuntimeError('Finalize only after all hardware workloads have finished')
    rows = json.loads((root/'comparison.json').read_text())
    output = root/'deliverables'
    output.mkdir(exist_ok=True)
    index, groups = [], []
    for row in rows:
        if not row.get('videos'):
            continue
        step = Path(row['videos'][0]).parent.parent
        stats = json.loads((step/'summary.json').read_text())
        verified = stats['outputs']
        if len(verified) != 32 or not all(s['ok'] for s in verified):
            raise RuntimeError('Invalid encoded video group')
        durations = [s['verified_encoded_frames']/25 for s in verified]
        for video in row['videos']:
            path = Path(video)
            stream = next(s for s in verified if s['stream'] == path.parent.name)
            index.append({'stage':row['stage'],'device':row['device'],'path':str(path.relative_to(root)),
                          'frames':stream['verified_encoded_frames'],'playback_seconds':stream['verified_encoded_frames']/25})
        sample = output/(row['stage']+'_stream00.mp4')
        shutil.copy2(row['videos'][0],sample)
        env = dict(os.environ,LD_LIBRARY_PATH='/usr/lib/aarch64-linux-gnu')
        frame = sample.with_suffix('.png')
        subprocess.run(['/usr/bin/ffmpeg','-nostdin','-v','error','-y','-i',str(sample),
                        '-vf','select=eq(n\\,10)','-frames:v','1','-threads','1',str(frame)],
                       env=env,check=True,timeout=60)
        if not frame.is_file():
            raise RuntimeError('Representative frame extraction failed')
        groups.append({'stage':row['stage'],'count':len(verified),'min_playback_s':min(durations),
                       'max_playback_s':max(durations),'sample':str(sample.relative_to(root))})
    (root/'video-index.json').write_text(json.dumps({'groups':groups,'videos':index},indent=2)+'\n')
    lines = ['# Result videos', '',
             'BM1684X hardware encoding, H.264 1920x1080 at nominal 25 FPS. Each run has 32 independent files.',
             'Playback duration is not measurement duration. Slow analysis processes only a short source segment.',
             'Frame counts include warmup and shutdown; all files were checked against detection/encoder counts.', '',
             '| Run | Files | Playback seconds per file | Representative stream |',
             '|---|---:|---:|---|']
    for g in groups:
        lines.append(f"| {g['stage']} | {g['count']} | {g['min_playback_s']:.2f}–{g['max_playback_s']:.2f} | [MP4]({g['sample']}) |")
    lines += ['', 'Full per-stream paths and verified frame counts: [video-index.json](video-index.json).']
    (root/'video-index.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    manifest = []
    for file in sorted(root.rglob('*')):
        if not file.is_file() or file.name == 'sha256-manifest.json':
            continue
        h = hashlib.sha256()
        with file.open('rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''):
                h.update(block)
        manifest.append({'path':str(file.relative_to(root)),'bytes':file.stat().st_size,'sha256':h.hexdigest()})
    (root/'sha256-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps({'videos':len(index),'groups':groups,'hashed_files':len(manifest)},indent=2))


if __name__ == '__main__':
    main()
