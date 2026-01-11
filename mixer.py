import sys
import os
import json
import subprocess
import tempfile
import shutil
import wave
import struct
import math
import re

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QPushButton, QCheckBox, 
                             QScrollArea, QFileDialog, QMessageBox, QFrame, 
                             QSizePolicy, QSlider, QDoubleSpinBox)
from PyQt6.QtCore import Qt, QUrl, pyqtSignal, QThread, QSize, QEvent, QRect
from PyQt6.QtGui import (QDragEnterEvent, QDropEvent, QPainter, QColor, QPen, 
                         QIcon, QCursor, QBrush, QPainterPath)
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput

# --- RISORSE ESTERNE ---
def get_ffmpeg_path(exe_name):
    if hasattr(sys, '_MEIPASS'):
        path = os.path.join(sys._MEIPASS, exe_name)
        if os.path.exists(path): return path
    local_path = os.path.join(os.path.abspath("."), exe_name)
    if os.path.exists(local_path): return local_path
    return exe_name

FFMPEG_BIN = get_ffmpeg_path("ffmpeg.exe")
FFPROBE_BIN = get_ffmpeg_path("ffprobe.exe")

# --- UTILS ---
def time_str_to_seconds(time_str):
    try:
        parts = time_str.split(':')
        return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    except:
        return 0.0

# --- THREAD ESTRAZIONE ---
class AudioExtractorThread(QThread):
    finished_extraction = pyqtSignal(str, str)

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
            '-t', '30', '-ac', '1', '-ar', '44100', '-f', 'wav', 
            self.output_path
        ]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, startupinfo=si)
        except FileNotFoundError:
            cmd[0] = 'ffmpeg'
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, startupinfo=si)
        self.finished_extraction.emit(self.output_path, str(self.track_index))

# --- THREAD ESPORTAZIONE ---
class ExportThread(QThread):
    progress_update = pyqtSignal(int)
    finished = pyqtSignal(bool, str)

    def __init__(self, cmd, total_duration):
        super().__init__()
        self.cmd = cmd
        self.total_duration = total_duration
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
            for line in self.process.stdout:
                if not self.is_running:
                    self.process.terminate()
                    return
                match = time_pattern.search(line)
                if match and self.total_duration > 0:
                    current_seconds = time_str_to_seconds(match.group(1))
                    percent = int((current_seconds / self.total_duration) * 100)
                    self.progress_update.emit(min(99, percent))
            self.process.wait()
            if self.process.returncode == 0:
                self.progress_update.emit(100)
                self.finished.emit(True, "Export completed!")
            else:
                self.finished.emit(False, "Error during export.")
        except Exception as e:
            self.finished.emit(False, f"Error exception: {str(e)}")

    def stop(self):
        self.is_running = False
        if self.process:
            self.process.terminate()

# --- PROGRESS BUTTON ---
class ProgressButton(QPushButton):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.default_text = text
        self.progress = 0
        self.is_exporting = False
        self.setStyleSheet("QPushButton { border: none; border-radius: 8px; color: white; font-size: 16px; font-weight: bold; background-color: transparent; }")
        self.setFixedHeight(50)

    def set_progress(self, val):
        self.progress = val
        self.update()

    def start_export_mode(self):
        self.is_exporting = True
        self.progress = 0
        self.setEnabled(False)
        self.update()

    def reset_mode(self):
        self.is_exporting = False
        self.progress = 0
        self.setEnabled(True)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        
        if not self.isEnabled() and not self.is_exporting: bg_color = QColor("#555555")
        elif not self.is_exporting: bg_color = QColor("#0078d7")
        else: bg_color = QColor("#333333")

        path = QPainterPath()
        path.addRoundedRect(0, 0, rect.width(), rect.height(), 8, 8)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(bg_color))
        painter.drawPath(path)

        if self.is_exporting and self.progress > 0:
            fill_width = int(rect.width() * (self.progress / 100))
            if fill_width > 0:
                progress_rect = QRect(0, 0, fill_width, rect.height())
                painter.save()
                painter.setClipPath(path)
                painter.fillRect(progress_rect, QColor("#2e7d32"))
                painter.restore()

        painter.setPen(QColor("white"))
        font = self.font()
        font.setBold(True)
        painter.setFont(font)
        text_to_draw = f"{self.progress}%" if self.is_exporting else self.default_text
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text_to_draw)

# --- WAVEFORM WIDGET ---
class WaveformWidget(QWidget):
    seek_requested = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self.setFixedHeight(50) 
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.samples = []
        self.duration_ms = 0
        self.current_position_ms = 0
        self.is_loaded = False
        self.gain_linear = 1.0 
        self.setStyleSheet("background-color: #222; border: 1px solid #444;")

    def set_gain_db(self, db_value):
        self.gain_linear = 10 ** (db_value / 20.0)
        self.update()

    def load_audio_data(self, file_path):
        if not os.path.exists(file_path): return
        try:
            with wave.open(file_path, 'r') as wf:
                self.n_frames = wf.getnframes()
                self.framerate = wf.getframerate()
                self.duration_ms = (self.n_frames / self.framerate) * 1000
                raw_data = wf.readframes(self.n_frames)
                count = len(raw_data) // 2
                fmt = f"<{count}h" 
                raw_samples = struct.unpack(fmt, raw_data)
                
                target_width = 2000 
                step = max(1, count // target_width)
                self.samples = []
                for i in range(0, count, step):
                    chunk = raw_samples[i:i+step]
                    if chunk:
                        val = max(abs(x) for x in chunk) / 32768.0
                        self.samples.append(val)
                self.is_loaded = True
                self.update()
        except: pass

    def set_position(self, ms):
        self.current_position_ms = ms
        self.update()

    def _handle_input(self, x):
        if not self.is_loaded or self.duration_ms == 0: return
        x = max(0, min(x, self.width()))
        pct = x / self.width()
        ms = int(self.duration_ms * pct)
        self.seek_requested.emit(ms)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton: self._handle_input(event.pos().x())
    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton: self._handle_input(event.pos().x())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#1e1e1e"))
        if not self.is_loaded or not self.samples: return
        rect_w, rect_h, mid_h = self.width(), self.height(), self.height() / 2
        total = len(self.samples)
        step = total / rect_w
        
        pen_color = QColor("#00bcd4")
        if self.gain_linear > 1.0:
            pen_color = QColor("#00bcd4") 

        painter.setPen(QPen(pen_color, 1))
        
        for x in range(rect_w):
            idx = int(x * step)
            if idx >= total: break
            
            val = self.samples[idx] * self.gain_linear
            if val > 1.0: val = 1.0
            
            bar_h = val * (rect_h - 4)
            painter.drawLine(int(x), int(mid_h - bar_h/2), int(x), int(mid_h + bar_h/2))
            
        if self.duration_ms > 0:
            cx = (self.current_position_ms / self.duration_ms) * rect_w
            painter.setPen(QPen(QColor("#ff4081"), 2))
            painter.drawLine(int(cx), 0, int(cx), rect_h)

# --- TRACK WIDGET ---
class AudioTrackWidget(QFrame):
    def __init__(self, track_info, index, file_path, temp_dir):
        super().__init__()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.track_info = track_info
        self.index = index
        self.file_path = file_path
        self.temp_dir = temp_dir
        self.temp_file = os.path.join(temp_dir, f"preview_{self.index}.wav")
        
        self.setFixedHeight(110)
        
        # MAIN LAYOUT
        self.main_layout = QHBoxLayout(self)
        self.main_layout.setContentsMargins(5, 5, 5, 5) 
        self.main_layout.setSpacing(10)
        
        # --- COLONNA SINISTRA ---
        left_container = QWidget()
        left_layout = QVBoxLayout(left_container)
        left_layout.setContentsMargins(0,0,0,0)
        left_layout.setSpacing(2) 
        
        top_row = QHBoxLayout()
        top_row.setSpacing(10)
        
        self.checkbox = QCheckBox()
        self.checkbox.setChecked(True)
        top_row.addWidget(self.checkbox)
        
        self.play_btn = QPushButton("▶")
        self.play_btn.setFixedSize(30, 30)
        self.play_btn.setStyleSheet("font-weight: bold; font-size: 14px;")
        self.play_btn.clicked.connect(self.toggle_playback)
        self.play_btn.setEnabled(False)
        top_row.addWidget(self.play_btn)
        
        lang = track_info.get('tags', {}).get('language', 'unk')
        codec = track_info.get('codec_name', 'unknown')
        title = track_info.get('tags', {}).get('title', f"Track {index}")
        top_row.addWidget(QLabel(f"<b>Track {index}</b> ({codec}) - {lang.upper()}<br>{title}"))
        
        left_layout.addLayout(top_row)
        
        self.waveform = WaveformWidget()
        self.waveform.seek_requested.connect(self.seek_audio)
        left_layout.addWidget(self.waveform)
        
        self.main_layout.addWidget(left_container, stretch=1)
        
        # --- COLONNA DESTRA (Slider + Spinbox) ---
        right_container = QWidget()
        right_container.setFixedWidth(60) 
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(0,0,0,0)
        right_layout.setSpacing(2)
        right_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Slider Verticale
        self.volume_slider = QSlider(Qt.Orientation.Vertical)
        self.volume_slider.setRange(-30, 30)
        self.volume_slider.setValue(0)
        self.volume_slider.setTickPosition(QSlider.TickPosition.TicksLeft)
        self.volume_slider.setTickInterval(10)
        self.volume_slider.setStyleSheet("""
            QSlider::groove:vertical { background: #444; width: 4px; border-radius: 2px; }
            QSlider::handle:vertical { background: #00bcd4; height: 10px; margin: 0 -4px; border-radius: 5px; }
            QSlider::add-page:vertical { background: #444; }
            QSlider::sub-page:vertical { background: #00bcd4; }
        """)
        
        # SpinBox dB
        self.db_spin = QDoubleSpinBox()
        self.db_spin.setRange(-30.0, 30.0)
        self.db_spin.setValue(0.0)
        self.db_spin.setSuffix(" dB")
        self.db_spin.setDecimals(0)
        self.db_spin.setFixedWidth(55)
        self.db_spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.db_spin.setStyleSheet("background-color: #333; color: white; border: 1px solid #555; border-radius: 4px; font-size: 10px;")
        self.db_spin.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)

        self.volume_slider.valueChanged.connect(self.on_slider_change)
        self.db_spin.valueChanged.connect(self.on_spin_change)

        right_layout.addWidget(self.volume_slider, alignment=Qt.AlignmentFlag.AlignHCenter)
        right_layout.addWidget(self.db_spin, alignment=Qt.AlignmentFlag.AlignHCenter)
        
        self.main_layout.addWidget(right_container)

        # PLAYER
        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)
        self.player.playbackStateChanged.connect(self.on_state_changed)
        self.player.positionChanged.connect(self.on_position_changed)
        
        self.extractor = AudioExtractorThread(file_path, index, self.temp_file)
        self.extractor.finished_extraction.connect(self.on_extraction_finished)
        self.extractor.start()

    # --- FIX: VOLUME CON MAGGIORE HEADROOM ---
    def update_realtime_volume(self, db_val):
        """
        Abbiamo cambiato il fattore di headroom a 0.25 (circa -12dB).
        Questo significa che lo 0dB dello slider corrisponde al 25% del volume di sistema.
        Questo permette di alzare il volume fino a +12dB (che corrisponderà al 100% sistema).
        Oltre i +12dB, l'anteprima non aumenterà più (limite sistema), ma l'export sì.
        """
        HEADROOM_FACTOR = 0.25  # 0dB = 25% volume. Max boost udibile = +12dB.
        linear_volume = (10 ** (db_val / 20.0)) * HEADROOM_FACTOR
        self.audio_output.setVolume(min(1.0, linear_volume))
        self.waveform.set_gain_db(db_val)

    def on_slider_change(self, val):
        self.db_spin.blockSignals(True)
        self.db_spin.setValue(float(val))
        self.db_spin.blockSignals(False)
        self.update_realtime_volume(float(val))

    def on_spin_change(self, val):
        self.volume_slider.blockSignals(True)
        self.volume_slider.setValue(int(val))
        self.volume_slider.blockSignals(False)
        self.update_realtime_volume(float(val))

    def get_current_db(self):
        return self.db_spin.value()

    def on_extraction_finished(self, path, idx):
        if os.path.exists(path):
            self.play_btn.setEnabled(True)
            self.waveform.load_audio_data(path)
            self.player.setSource(QUrl.fromLocalFile(path))
            current_db = self.db_spin.value()
            self.update_realtime_volume(current_db)

    def toggle_playback(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else: self.player.play()

    def on_state_changed(self, state):
        self.play_btn.setText("⏸" if state == QMediaPlayer.PlaybackState.PlayingState else "▶")
    
    def on_position_changed(self, position): self.waveform.set_position(position)
    def seek_audio(self, ms): self.player.setPosition(ms)
    def cleanup(self):
        self.player.stop()
        self.player.setSource(QUrl())
        try: self.extractor.finished_extraction.disconnect()
        except: pass

# --- DROP SECTION ---
class DropSection(QWidget):
    file_dropped = pyqtSignal(str)
    close_clicked = pyqtSignal()
    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.clip_loaded = False
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0,0,0,0)
        self.label = QLabel("\nDrag & Drop video here\n")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.reset_style()
        self.layout.addWidget(self.label)
        
        self.overlay = QLabel(self)
        self.overlay.setText("✖ CLOSE")
        self.overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.overlay.hide()
        self.overlay.setStyleSheet("background-color: rgba(0,0,0,180); color: #ff5555; font-size: 24px; font-weight: bold; border-radius: 15px;")
        self.overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def set_loaded_state(self, loaded, filename=""):
        self.clip_loaded = loaded
        if loaded:
            self.label.setText(f"Loaded:\n{filename}")
            self.label.setStyleSheet("border: 3px solid #4CAF50; border-radius: 15px; background-color: #1e3a1f; color: #fff; font-size: 20px; font-weight: bold; padding: 30px;")
        else:
            self.label.setText("\nDrag & Drop video here\n")
            self.reset_style()
            self.overlay.hide()

    def reset_style(self):
        self.label.setStyleSheet("border: 3px dashed #00bcd4; border-radius: 15px; background-color: #333; color: #fff; font-size: 24px; font-weight: bold; padding: 30px;")

    def resizeEvent(self, event):
        self.overlay.resize(self.size())
        super().resizeEvent(event)
    def enterEvent(self, event):
        if self.clip_loaded: 
            self.overlay.show()
            self.setCursor(Qt.CursorShape.PointingHandCursor)
    def leaveEvent(self, event):
        if self.clip_loaded: 
            self.overlay.hide()
            self.setCursor(Qt.CursorShape.ArrowCursor)
    def mousePressEvent(self, event):
        if self.clip_loaded and event.button() == Qt.MouseButton.LeftButton: self.close_clicked.emit()
    def dragEnterEvent(self, event): event.accept() if event.mimeData().hasUrls() else event.ignore()
    def dropEvent(self, event): self.file_dropped.emit([u.toLocalFile() for u in event.mimeData().urls()][0])

# --- MAIN WINDOW ---
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Audio Merge")
        self.resize(800, 750)
        self.setAcceptDrops(True)
        self.current_video_path = None
        self.video_duration = 0
        self.track_widgets = []
        self.temp_dir = tempfile.mkdtemp()
        self.export_thread = None
        
        self.setStyleSheet("QMainWindow { background-color: #2b2b2b; } QLabel, QCheckBox { color: #e0e0e0; } QScrollArea { border: none; background-color: #2b2b2b; }")
        
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(20, 20, 20, 20)
        
        self.drop_section = DropSection()
        self.drop_section.file_dropped.connect(self.load_video)
        self.drop_section.close_clicked.connect(self.close_clip)
        layout.addWidget(self.drop_section)
        
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("background-color: transparent;")
        self.scroll_content = QWidget()
        self.scroll_content.setStyleSheet("background-color: #2b2b2b;")
        self.tracks_layout = QVBoxLayout(self.scroll_content)
        self.tracks_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.scroll_content)
        layout.addWidget(self.scroll)
        
        export_layout = QVBoxLayout()
        export_layout.setContentsMargins(10, 10, 10, 0)
        
        self.export_btn = ProgressButton("EXPORT")
        self.export_btn.clicked.connect(self.start_export)
        self.export_btn.setEnabled(False)
        export_layout.addWidget(self.export_btn)
        
        self.auto_save_chk = QCheckBox("Export in origin folder (suffix: _mix)")
        export_layout.addWidget(self.auto_save_chk, alignment=Qt.AlignmentFlag.AlignCenter)
        
        layout.addLayout(export_layout)
        self.check_ffmpeg()

    def check_ffmpeg(self):
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        try:
            subprocess.run([FFMPEG_BIN, '-version'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, startupinfo=si)
        except FileNotFoundError:
            try:
                subprocess.run(['ffmpeg', '-version'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, startupinfo=si)
            except FileNotFoundError:
                QMessageBox.critical(self, "Error", f"FFmpeg not found!\nMake sure that 'ffmpeg.exe' is in the same folder as this executable or installed in your system.")
                sys.exit(1)

    def close_clip(self):
        for w in self.track_widgets:
            w.cleanup() 
            w.deleteLater()
        QApplication.processEvents()
        self.track_widgets = []
        self.current_video_path = None
        self.video_duration = 0
        self.drop_section.set_loaded_state(False)
        self.export_btn.reset_mode()
        self.export_btn.setEnabled(False)

    def load_video(self, path):
        self.close_clip()
        self.current_video_path = path
        filename = os.path.basename(path)
        self.drop_section.set_loaded_state(True, filename)
        
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        try:
            cmd = [FFPROBE_BIN, '-v', 'quiet', '-print_format', 'json', '-show_format', '-show_streams', '-select_streams', 'a', path]
            try:
                output = subprocess.check_output(cmd, startupinfo=si)
            except FileNotFoundError:
                cmd[0] = 'ffprobe'
                output = subprocess.check_output(cmd, startupinfo=si)

            data = json.loads(output)
            try: self.video_duration = float(data.get('format', {}).get('duration', 0))
            except: self.video_duration = 0

            streams = data.get('streams', [])
            if not streams:
                QMessageBox.warning(self, "Info", "No audio tracks found.")
                self.close_clip()
                return
            for idx, stream in enumerate(streams):
                w = AudioTrackWidget(stream, idx, path, self.temp_dir)
                self.tracks_layout.addWidget(w)
                self.track_widgets.append(w)
            self.export_btn.setEnabled(True)
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            self.close_clip()

    def start_export(self):
        if not self.current_video_path: return
        
        active_tracks = [w for w in self.track_widgets if w.checkbox.isChecked()]
        if not active_tracks:
            QMessageBox.warning(self, "No Audio", "Select at least one track.")
            return
        
        for w in self.track_widgets: w.player.stop()

        src_dir = os.path.dirname(self.current_video_path)
        src_filename = os.path.basename(self.current_video_path)
        name_no_ext, ext = os.path.splitext(src_filename)
        
        if self.auto_save_chk.isChecked():
            out_path = os.path.join(src_dir, f"{name_no_ext}_mix{ext}")
        else:
            out_path, _ = QFileDialog.getSaveFileName(self, "Save Video", src_dir, "Video Files (*.mp4 *.mkv *.mov)")
        if not out_path: return

        cmd = [FFMPEG_BIN, '-y', '-i', self.current_video_path, '-map', '0:v', '-c:v', 'copy']
        if not os.path.exists(FFMPEG_BIN) and shutil.which('ffmpeg'): cmd[0] = 'ffmpeg'

        filter_parts = []
        mix_inputs = ""
        
        for i, track in enumerate(active_tracks):
            db_val = track.get_current_db()
            input_label = f"0:a:{track.index}"
            output_label = f"a{i}"
            filter_parts.append(f"[{input_label}]volume={db_val}dB[{output_label}]")
            mix_inputs += f"[{output_label}]"

        volume_filters = ";".join(filter_parts)
        final_filter = f"{volume_filters};{mix_inputs}amix=inputs={len(active_tracks)}[mixed];[mixed]dynaudnorm[aout]"
        
        cmd.extend(['-filter_complex', final_filter, '-map', '[aout]', '-c:a', 'aac', '-b:a', '192k', out_path])

        self.export_btn.start_export_mode()
        self.export_thread = ExportThread(cmd, self.video_duration)
        self.export_thread.progress_update.connect(self.export_btn.set_progress)
        self.export_thread.finished.connect(self.on_export_finished)
        self.export_thread.start()

    def on_export_finished(self, success, message):
        self.export_btn.reset_mode()
        if success: QMessageBox.information(self, "Success", message)
        else: QMessageBox.critical(self, "Error", message)

    def closeEvent(self, event):
        if self.export_thread and self.export_thread.isRunning():
            self.export_thread.stop()
            self.export_thread.wait()
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        event.accept()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())