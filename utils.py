import sys
import os

def get_ffmpeg_path(exe_name):
    """Gestisce i percorsi per FFmpeg sia in dev che in build EXE"""
    if hasattr(sys, '_MEIPASS'):
        path = os.path.join(sys._MEIPASS, exe_name)
        if os.path.exists(path): return path
    
    local_path = os.path.join(os.path.abspath("."), exe_name)
    if os.path.exists(local_path): return local_path
    
    return exe_name

FFMPEG_BIN = get_ffmpeg_path("ffmpeg.exe")
FFPROBE_BIN = get_ffmpeg_path("ffprobe.exe")

def format_time(ms):
    """Converte millisecondi in formato MM:SS.ms"""
    seconds = (ms / 1000) % 60
    minutes = (ms / (1000 * 60)) % 60
    return f"{int(minutes):02d}:{seconds:05.2f}"

def time_str_to_seconds(time_str):
    try:
        parts = time_str.split(':')
        return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    except:
        return 0.0