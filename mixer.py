import sys
import os
import json
import subprocess
import tempfile
import shutil
import wave
import numpy as np

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QPushButton, QCheckBox, 
                             QScrollArea, QFileDialog, QMessageBox, QFrame, QSizePolicy)
from PyQt6.QtCore import Qt, QUrl, pyqtSignal, QThread, QSize
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QPainter, QColor, QPen, QIcon
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput

# --- Thread per estrarre audio ---
class AudioExtractorThread(QThread):
    finished_extraction = pyqtSignal(str, str) # file_path, index

    def __init__(self, input_video, track_index, output_path):
        super().__init__()
        self.input_video = input_video
        self.track_index = track_index
        self.output_path = output_path

    def run(self):
        cmd = [
            'ffmpeg', '-y', '-i', self.input_video,
            '-map', f'0:a:{self.track_index}',
            '-t', '30', 
            '-ac', '1', 
            '-ar', '44100', 
            '-f', 'wav', 
            self.output_path
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.finished_extraction.emit(self.output_path, str(self.track_index))

# --- Widget Waveform ---
class WaveformWidget(QWidget):
    seek_requested = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self.setFixedHeight(60)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.samples = None
        self.duration_ms = 0
        self.current_position_ms = 0
        self.is_loaded = False
        self.setStyleSheet("background-color: #222; border: 1px solid #444;")

    def load_audio_data(self, file_path):
        if not os.path.exists(file_path): return
        try:
            with wave.open(file_path, 'r') as wf:
                self.n_frames = wf.getnframes()
                self.framerate = wf.getframerate()
                self.duration_ms = (self.n_frames / self.framerate) * 1000
                raw_data = wf.readframes(self.n_frames)
                y = np.frombuffer(raw_data, dtype=np.int16)
                self.samples = y / 32768.0 
                self.is_loaded = True
                self.update()
        except Exception as e:
            print(f"Error reading waveform: {e}")

    def set_position(self, ms):
        self.current_position_ms = ms
        self.update()

    def mousePressEvent(self, event):
        if not self.is_loaded or self.duration_ms == 0: return
        x = event.pos().x()
        pct = x / self.width()
        ms = int(self.duration_ms * pct)
        self.seek_requested.emit(ms)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#1e1e1e"))
        
        if not self.is_loaded or self.samples is None:
            return

        rect_w = self.width()
        rect_h = self.height()
        mid_h = rect_h / 2
        
        total_samples = len(self.samples)
        if total_samples > 0:
            step = max(1, total_samples // rect_w)
            painter.setPen(QPen(QColor("#00bcd4"), 1))
            
            for x in range(rect_w):
                idx = x * step
                if idx >= total_samples: break
                chunk = self.samples[idx : idx + step]
                if len(chunk) == 0: continue
                val = np.max(np.abs(chunk))
                bar_h = val * (rect_h - 4)
                y1 = mid_h - (bar_h / 2)
                y2 = mid_h + (bar_h / 2)
                painter.drawLine(int(x), int(y1), int(x), int(y2))

        if self.duration_ms > 0:
            cursor_x = (self.current_position_ms / self.duration_ms) * rect_w
            painter.setPen(QPen(QColor("#ff4081"), 2))
            painter.drawLine(int(cursor_x), 0, int(cursor_x), rect_h)

# --- Widget Traccia Audio ---
class AudioTrackWidget(QFrame):
    def __init__(self, track_info, index, file_path, temp_dir):
        super().__init__()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.track_info = track_info
        self.index = index
        self.file_path = file_path
        self.temp_dir = temp_dir
        self.temp_file = os.path.join(temp_dir, f"preview_{self.index}.wav")
        
        main_layout = QVBoxLayout()
        self.setLayout(main_layout)
        
        top_row = QHBoxLayout()
        self.checkbox = QCheckBox()
        self.checkbox.setChecked(True)
        top_row.addWidget(self.checkbox)
        
        self.play_btn = QPushButton("▶")
        self.play_btn.setFixedSize(40, 40)
        self.play_btn.setStyleSheet("font-weight: bold; font-size: 16px;")
        self.play_btn.clicked.connect(self.toggle_playback)
        self.play_btn.setEnabled(False)
        top_row.addWidget(self.play_btn)
        
        lang = track_info.get('tags', {}).get('language', 'unk')
        codec = track_info.get('codec_name', 'unknown')
        title = track_info.get('tags', {}).get('title', f"Track {index}")
        label_text = f"<b>Track {index}</b> ({codec}) - {lang.upper()}<br>{title}"
        self.label = QLabel(label_text)
        self.label.setStyleSheet("font-size: 14px;")
        top_row.addWidget(self.label)
        
        main_layout.addLayout(top_row)
        
        self.waveform = WaveformWidget()
        self.waveform.seek_requested.connect(self.seek_audio)
        main_layout.addWidget(self.waveform)
        
        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)
        self.player.playbackStateChanged.connect(self.on_state_changed)
        self.player.positionChanged.connect(self.on_position_changed)
        
        self.extractor = AudioExtractorThread(file_path, index, self.temp_file)
        self.extractor.finished_extraction.connect(self.on_extraction_finished)
        self.extractor.start()

    def on_extraction_finished(self, path, idx):
        if os.path.exists(path):
            self.play_btn.setEnabled(True)
            self.waveform.load_audio_data(path)
            self.player.setSource(QUrl.fromLocalFile(path))
            self.audio_output.setVolume(1.0)

    def toggle_playback(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def on_state_changed(self, state):
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.play_btn.setText("⏸")
        else:
            self.play_btn.setText("▶")

    def on_position_changed(self, position):
        self.waveform.set_position(position)
        
    def seek_audio(self, ms):
        self.player.setPosition(ms)

# --- NUOVO: Widget Drop Area con Bottone Sovrapposto ---
class DropSection(QWidget):
    file_dropped = pyqtSignal(str)
    close_clicked = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        # Layout principale che contiene solo la Label
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        
        # 1. La Label di sfondo
        self.label = QLabel("\nDrag & Drop video here\n")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.reset_style()
        self.layout.addWidget(self.label)
        
        # 2. Il bottone "X" (Non aggiunto al layout, ma figlio di self)
        self.close_btn = QPushButton("✖", self)
        self.close_btn.setFixedSize(30, 30)
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.clicked.connect(self.close_clicked)
        self.close_btn.hide() # Nascosto all'inizio
        
        # Stile del bottone rotondo
        self.close_btn.setStyleSheet("""
            QPushButton {
                background-color: #ff3b30;
                color: white;
                border-radius: 15px; /* Metà della dimensione (30px) per farlo rotondo */
                font-weight: bold;
                font-size: 14px;
                border: 2px solid #2b2b2b;
            }
            QPushButton:hover {
                background-color: #d32f2f;
            }
        """)

    def set_text(self, text):
        self.label.setText(text)

    def set_active_style(self):
        self.label.setStyleSheet("""
            QLabel {
                border: 2px solid #00bcd4;
                border-radius: 15px;
                background-color: #1e3a1f;
                color: #ffffff;
                font-size: 20px;
                font-weight: bold;
                padding: 30px;
            }
        """)

    def reset_style(self):
        self.label.setStyleSheet("""
            QLabel {
                border: 2px dashed #00bcd4;
                border-radius: 15px;
                background-color: #333333;
                color: #ffffff;
                font-size: 24px;
                font-weight: bold;
                padding: 30px;
            }
        """)

    def resizeEvent(self, event):
        # Questo evento viene chiamato ogni volta che la finestra cambia dimensione.
        # Calcoliamo la posizione per ancorare il bottone in basso a destra.
        
        # Margini per posizionarlo "a cavallo" del bordo o appena dentro
        margin_right = 0
        margin_bottom = 0
        
        x = self.width() - self.close_btn.width() - margin_right
        y = self.height() - self.close_btn.height() - margin_bottom
        
        self.close_btn.move(x, y)
        super().resizeEvent(event)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls(): event.accept()
        else: event.ignore()

    def dropEvent(self, event: QDropEvent):
        files = [u.toLocalFile() for u in event.mimeData().urls()]
        if files: self.file_dropped.emit(files[0])


# --- Finestra Principale ---
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Audio Merge")
        self.resize(800, 750)
        self.setAcceptDrops(True)
        
        self.current_video_path = None
        self.track_widgets = []
        self.temp_dir = tempfile.mkdtemp()
        
        self.setStyleSheet("""
            QMainWindow { background-color: #2b2b2b; }
            QLabel { color: #e0e0e0; }
            QCheckBox { color: #e0e0e0; font-size: 14px; }
            QScrollArea { border: none; background-color: #2b2b2b; }
        """)
        
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # --- DROP SECTION (CUSTOM WIDGET) ---
        self.drop_section = DropSection()
        self.drop_section.file_dropped.connect(self.load_video)
        self.drop_section.close_clicked.connect(self.close_clip)
        layout.addWidget(self.drop_section)
        
        # Scroll Area
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("background-color: transparent;")
        self.scroll_content = QWidget()
        self.scroll_content.setStyleSheet("background-color: #2b2b2b;")
        self.tracks_layout = QVBoxLayout(self.scroll_content)
        self.tracks_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.scroll_content)
        layout.addWidget(self.scroll)
        
        # --- ZONA ESPORTAZIONE ---
        export_layout = QVBoxLayout()
        export_layout.setContentsMargins(10, 10, 10, 0)
        
        self.export_btn = QPushButton("Export")
        self.export_btn.setFixedHeight(50) 
        self.export_btn.setStyleSheet("""
            QPushButton {
                background-color: #555;
                color: #aaa;
                font-size: 16px;
                font-weight: bold;
                border-radius: 8px;
            }
            QPushButton:enabled {
                background-color: #0078d7;
                color: white;
            }
            QPushButton:hover:enabled { background-color: #008ae6; }
        """)
        self.export_btn.clicked.connect(self.export_video)
        self.export_btn.setEnabled(False)
        export_layout.addWidget(self.export_btn)
        
        self.auto_save_chk = QCheckBox("Same path as origin")
        export_layout.addWidget(self.auto_save_chk, alignment=Qt.AlignmentFlag.AlignCenter)
        
        layout.addLayout(export_layout)
        
        self.check_ffmpeg()

    def check_ffmpeg(self):
        try:
            subprocess.run(['ffmpeg', '-version'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            QMessageBox.critical(self, "Error", "FFmpeg not found! Install and add to PATH environment variable.")
            sys.exit(1)

    # Gestiamo il drop anche sulla Main Window per sicurezza
    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls(): event.accept()
        else: event.ignore()

    def dropEvent(self, event: QDropEvent):
        files = [u.toLocalFile() for u in event.mimeData().urls()]
        if files: self.load_video(files[0])

    def close_clip(self):
        """Chiude il video corrente e resetta l'interfaccia"""
        for w in self.track_widgets:
            w.player.stop()
            w.deleteLater()
        self.track_widgets = []
        
        self.current_video_path = None
        
        # Reset UI DropSection
        self.drop_section.set_text("\nDrag & Drop video here\n")
        self.drop_section.reset_style()
        self.drop_section.close_btn.hide() # Nascondi la X rossa
        
        self.export_btn.setEnabled(False)

    def load_video(self, path):
        self.close_clip()

        self.current_video_path = path
        filename = os.path.basename(path)
        
        # Aggiorna UI DropSection
        self.drop_section.set_text(f"Loaded:\n{filename}")
        self.drop_section.set_active_style()
        self.drop_section.close_btn.show() # Mostra la X rossa
        
        try:
            cmd = ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_streams', '-select_streams', 'a', path]
            data = json.loads(subprocess.check_output(cmd))
            streams = data.get('streams', [])
            
            if not streams:
                QMessageBox.warning(self, "Info", "No audio tracks found.")
                self.close_clip() # Resetta se non ci sono tracce
                return

            for idx, stream in enumerate(streams):
                w = AudioTrackWidget(stream, idx, path, self.temp_dir)
                self.tracks_layout.addWidget(w)
                self.track_widgets.append(w)
            
            self.export_btn.setEnabled(True)
            
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            self.close_clip()

    def export_video(self):
        if not self.current_video_path: return
        
        selected = [w.index for w in self.track_widgets if w.checkbox.isChecked()]
        if not selected:
            QMessageBox.warning(self, "No Audio", "Select at least one track.")
            return
            
        for w in self.track_widgets: w.player.stop()

        src_dir = os.path.dirname(self.current_video_path)
        src_filename = os.path.basename(self.current_video_path)
        name_no_ext, ext = os.path.splitext(src_filename)

        if self.auto_save_chk.isChecked():
            new_filename = f"{name_no_ext}_mix{ext}"
            out_path = os.path.join(src_dir, new_filename)
        else:
            out_path, _ = QFileDialog.getSaveFileName(self, "Save Video", src_dir, "Video Files (*.mp4 *.mkv *.mov)")

        if not out_path: return

        cmd = ['ffmpeg', '-y', '-i', self.current_video_path, '-map', '0:v', '-c:v', 'copy']
        
        filter_str = "".join([f"[0:a:{i}]" for i in selected])
        filter_str += f"amix=inputs={len(selected)}[mixed];[mixed]dynaudnorm[aout]"
        
        cmd.extend(['-filter_complex', filter_str, '-map', '[aout]', '-c:a', 'aac', '-b:a', '192k', out_path])
        
        self.drop_section.set_text("Esportazione in corso...")
        QApplication.processEvents()
        
        try:
            subprocess.run(cmd, check=True)
            QMessageBox.information(self, "Done", f"Video succesfully exported:\n{out_path}")
            self.drop_section.set_text(f"Export completed!\n{os.path.basename(out_path)}")
        except subprocess.CalledProcessError:
            QMessageBox.critical(self, "Error", "FFmpeg error while exporting.")

    def closeEvent(self, event):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        event.accept()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())