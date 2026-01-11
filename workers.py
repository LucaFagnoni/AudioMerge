import subprocess
import re
from PyQt6.QtCore import QThread, pyqtSignal
from utils import FFMPEG_BIN, FFPROBE_BIN

class AudioExtractorThread(QThread):
    finished_extraction = pyqtSignal(str, str) # path, index

    def __init__(self, input_video, track_index, output_path):
        super().__init__()
        self.input_video = input_video
        self.track_index = track_index
        self.output_path = output_path

    def run(self):
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        
        cmd = [
            FFMPEG_BIN, '-y', '-i', self.input_video,
            '-map', f'0:a:{self.track_index}',
            '-ac', '1', '-ar', '8000', '-f', 'wav', 
            self.output_path
        ]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, startupinfo=si)
        except FileNotFoundError:
            cmd[0] = 'ffmpeg'
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, startupinfo=si)
            
        self.finished_extraction.emit(self.output_path, str(self.track_index))

class ExportThread(QThread):
    progress_update = pyqtSignal(int)
    finished = pyqtSignal(bool, str)

    def __init__(self, cmd, total_duration_sec):
        super().__init__()
        self.cmd = cmd
        self.total_duration = total_duration_sec
        self.process = None
        self.is_running = True

    def run(self):
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        try:
            self.process = subprocess.Popen(
                self.cmd, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.STDOUT, 
                universal_newlines=True,
                encoding='utf-8',
                errors='replace',
                startupinfo=si
            )

            time_pattern = re.compile(r"time=(\d{2}:\d{2}:\d{2}\.\d{2})")
            
            def parse_time(t_str):
                try:
                    h, m, s = t_str.split(':')
                    return float(h)*3600 + float(m)*60 + float(s)
                except: return 0.0

            for line in self.process.stdout:
                if not self.is_running:
                    self.process.terminate()
                    return

                match = time_pattern.search(line)
                if match and self.total_duration > 0:
                    current_seconds = parse_time(match.group(1))
                    percent = int((current_seconds / self.total_duration) * 100)
                    self.progress_update.emit(min(99, percent))
            
            self.process.wait()
            
            if self.process.returncode == 0:
                self.progress_update.emit(100)
                self.finished.emit(True, "Export completed successfully!")
            else:
                self.finished.emit(False, "Export failed (ffmpeg error).")

        except Exception as e:
            self.finished.emit(False, f"Exception: {str(e)}")

    def stop(self):
        self.is_running = False
        if self.process:
            self.process.terminate()

class KeyframeLoaderThread(QThread):
    keyframes_found = pyqtSignal(list)

    def __init__(self, video_path):
        super().__init__()
        self.video_path = video_path

    def run(self):
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        
        # Metodo ottimizzato: Legge i pacchetti invece dei frame.
        # Filtra solo i pacchetti video che hanno il flag 'K' (Keyframe)
        # Molto più veloce e affidabile del metodo precedente.
        cmd = [
            FFPROBE_BIN, 
            "-v", "error",
            "-select_streams", "v:0", 
            "-show_entries", "packet=pts_time,flags", 
            "-of", "csv=p=0", 
            self.video_path
        ]
        
        try:
            process = subprocess.Popen(
                cmd, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.DEVNULL,
                text=True,
                startupinfo=si
            )
            
            stdout, _ = process.communicate()
            
            kf_list = []
            for line in stdout.splitlines():
                # Esempio output: "12.045000,K__" oppure "12.045000,K"
                parts = line.strip().split(',')
                if len(parts) >= 2:
                    timestamp = parts[0]
                    flags = parts[1]
                    # Se c'è la K nei flags, è un keyframe
                    if 'K' in flags:
                        try:
                            # Salva in millisecondi
                            ms = int(float(timestamp) * 1000)
                            kf_list.append(ms)
                        except ValueError:
                            pass
            
            self.keyframes_found.emit(kf_list)
            
        except Exception as e:
            print(f"Keyframe load error: {e}")
            self.keyframes_found.emit([])