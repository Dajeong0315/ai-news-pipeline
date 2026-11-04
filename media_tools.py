"""Resolve system FFmpeg or the pinned imageio-ffmpeg binary."""
import os
import shutil
import subprocess
import re

def ffmpeg_path():
    path=os.environ.get('FFMPEG_BINARY') or shutil.which('ffmpeg')
    if path:
        return path
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()

def video_duration(video):
    probe=shutil.which('ffprobe')
    if probe:
        result=subprocess.run([probe,'-v','error','-show_entries','format=duration','-of','csv=p=0',str(video)],capture_output=True,text=True,timeout=30,check=True)
        return float(result.stdout.strip())
    result=subprocess.run([ffmpeg_path(),'-hide_banner','-i',str(video)],capture_output=True,text=True,timeout=30,errors='replace')
    match=re.search(r'Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)',result.stderr)
    if not match:
        raise RuntimeError('Could not determine video duration')
    hours,minutes,seconds=map(float,match.groups())
    return hours*3600+minutes*60+seconds
