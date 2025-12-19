import sys
import os
import json
import subprocess
import tempfile
import shutil
import wave
import struct
import numpy as np

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QPushButton, QCheckBox, 
                             QScrollArea, QFileDialog, QMessageBox, QFrame, QSizePolicy)
from PyQt6.QtCore import Qt, QUrl, QTimer, pyqtSignal, QThread, QPoint
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QPainter, QColor, QPen, QBrush
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput

# --- Thread per estrarre audio senza bloccare la UI ---
class AudioExtractorThread(QThread):
    finished_extraction = pyqtSignal(str, str) # file_path, index

    def __init__(self, input_video, track_index, output_path):
        super().__init__()
        self.input_video = input_video
        self.track_index = track_index
        self.output_path = output_path

    def run(self):
        # Estrae i primi 30 secondi, converte in WAV Mono 16bit (facile da leggere per la waveform)
        cmd = [
            'ffmpeg', '-y', '-i', self.input_video,
            '-map', f'0:a:{self.track_index}',
            '-t', '30', 
            '-ac', '1', # Mono per semplificare la waveform
            '-ar', '44100', # Sample rate standard
            '-f', 'wav', 
            self.output_path
        ]
        # Eseguiamo in background
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.finished_extraction.emit(self.output_path, str(self.track_index))

# --- Widget Personalizzato per la Waveform ---
class WaveformWidget(QWidget):
    seek_requested = pyqtSignal(int) # Emette ms quando cliccato

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
                
                # Leggiamo i dati grezzi
                raw_data = wf.readframes(self.n_frames)
                # Convertiamo in array numpy (int16)
                y = np.frombuffer(raw_data, dtype=np.int16)
                
                # Normalizziamo per il disegno (range 0-1 approx)
                self.samples = y / 32768.0 
                self.is_loaded = True
                self.update()
        except Exception as e:
            print(f"Errore lettura waveform: {e}")

    def set_position(self, ms):
        self.current_position_ms = ms
        self.update()

    def mousePressEvent(self, event):
        if not self.is_loaded or self.duration_ms == 0: return
        
        x = event.pos().x()
        width = self.width()
        # Calcola percentuale cliccata
        pct = x / width
        ms = int(self.duration_ms * pct)
        self.seek_requested.emit(ms)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Sfondo
        painter.fillRect(self.rect(), QColor("#1e1e1e"))
        
        if not self.is_loaded or self.samples is None:
            painter.setPen(QColor("#555"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Loading Waveform...")
            return

        # Disegno Waveform
        rect_w = self.width()
        rect_h = self.height()
        mid_h = rect_h / 2
        
        # Downsampling: Abbiamo troppi campioni per i pixel dello schermo.
        # Prendiamo un campione ogni 'step'
        total_samples = len(self.samples)
        if total_samples > 0:
            step = max(1, total_samples // rect_w)
            
            # Scegliamo un colore ciano/blu tech
            painter.setPen(QPen(QColor("#00bcd4"), 1))
            
            # Disegniamo linee verticali per simulare la waveform
            # Per performance, iteriamo sui pixel x
            for x in range(rect_w):
                idx = x * step
                if idx >= total_samples: break
                
                # Prendiamo un chunk e troviamo il picco massimo in quel chunk
                chunk = self.samples[idx : idx + step]
                if len(chunk) == 0: continue
                
                val = np.max(np.abs(chunk)) # Valore assoluto massimo nel chunk
                bar_h = val * (rect_h - 4) # Altezza barra (-4 padding)
                
                # Disegna linea dal centro in su e in giù
                y1 = mid_h - (bar_h / 2)
                y2 = mid_h + (bar_h / 2)
                painter.drawLine(int(x), int(y1), int(x), int(y2))

        # Disegno Cursore (Playhead)
        if self.duration_ms > 0:
            cursor_x = (self.current_position_ms / self.duration_ms) * rect_w
            painter.setPen(QPen(QColor("#ff4081"), 2)) # Rosso/Rosa
            painter.drawLine(int(cursor_x), 0, int(cursor_x), rect_h)

class AudioTrackWidget(QFrame):
    """
    Widget che rappresenta una singola traccia audio.
    """
    def __init__(self, track_info, index, file_path, temp_dir):
        super().__init__()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.track_info = track_info
        self.index = index
        self.file_path = file_path
        self.temp_dir = temp_dir
        self.temp_file = os.path.join(temp_dir, f"preview_{self.index}.wav")
        
        # Layout principale verticale
        main_layout = QVBoxLayout()
        self.setLayout(main_layout)
        
        # Riga Superiore: Controlli e Info
        top_row = QHBoxLayout()
        
        # Checkbox
        self.checkbox = QCheckBox()
        self.checkbox.setChecked(True)
        top_row.addWidget(self.checkbox)
        
        # Play Button
        self.play_btn = QPushButton("▶")
        self.play_btn.setFixedSize(40, 40)
        self.play_btn.clicked.connect(self.toggle_playback)
        self.play_btn.setEnabled(False) # Disabilitato finché non estrae
        top_row.addWidget(self.play_btn)
        
        # Info Traccia
        lang = track_info.get('tags', {}).get('language', 'unk')
        codec = track_info.get('codec_name', 'unknown')
        title = track_info.get('tags', {}).get('title', f"Track {index}")
        label_text = f"<b>Traccia {index}</b> ({codec}) - {lang.upper()}<br>{title}"
        self.label = QLabel(label_text)
        top_row.addWidget(self.label)
        
        main_layout.addLayout(top_row)
        
        # Riga Inferiore: Waveform
        self.waveform = WaveformWidget()
        self.waveform.seek_requested.connect(self.seek_audio)
        main_layout.addWidget(self.waveform)
        
        # Setup Audio Player
        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)
        self.player.playbackStateChanged.connect(self.on_state_changed)
        self.player.positionChanged.connect(self.on_position_changed)
        
        # Avvia estrazione in thread separato
        self.extractor = AudioExtractorThread(file_path, index, self.temp_file)
        self.extractor.finished_extraction.connect(self.on_extraction_finished)
        self.extractor.start()

    def on_extraction_finished(self, path, idx):
        if os.path.exists(path):
            self.play_btn.setEnabled(True)
            # Carica dati nella waveform per il disegno
            self.waveform.load_audio_data(path)
            # Imposta sorgente player
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
        # Aggiorna cursore waveform
        self.waveform.set_position(position)
        
    def seek_audio(self, ms):
        self.player.setPosition(ms)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Video Audio Mixer + Waveform")
        self.resize(700, 600)
        self.setAcceptDrops(True)
        
        self.current_video_path = None
        self.track_widgets = []
        self.temp_dir = tempfile.mkdtemp()
        
        # UI
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        
        self.drop_label = QLabel("DRAG & DROP VIDEO")
        self.drop_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_label.setStyleSheet("border: 2px dashed #666; padding: 20px; font-size: 16px; color: #888;")
        layout.addWidget(self.drop_label)
        
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.tracks_layout = QVBoxLayout(self.scroll_content)
        self.tracks_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.scroll_content)
        layout.addWidget(self.scroll)
        
        self.export_btn = QPushButton("Esporta Mix")
        self.export_btn.setFixedHeight(45)
        self.export_btn.clicked.connect(self.export_video)
        self.export_btn.setEnabled(False)
        layout.addWidget(self.export_btn)
        
        self.check_ffmpeg()

    def check_ffmpeg(self):
        try:
            subprocess.run(['ffmpeg', '-version'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            QMessageBox.critical(self, "Errore", "FFmpeg non trovato! Installalo e aggiungilo al PATH.")
            sys.exit(1)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls(): event.accept()
        else: event.ignore()

    def dropEvent(self, event: QDropEvent):
        files = [u.toLocalFile() for u in event.mimeData().urls()]
        if files: self.load_video(files[0])

    def load_video(self, path):
        self.current_video_path = path
        self.drop_label.setText(f"File: {os.path.basename(path)}")
        self.drop_label.setStyleSheet("border: 2px solid #00bcd4; padding: 10px; color: #000;")
        
        # Pulizia
        for w in self.track_widgets: 
            w.player.stop()
            w.deleteLater()
        self.track_widgets = []
        
        # Analisi
        try:
            cmd = ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_streams', '-select_streams', 'a', path]
            data = json.loads(subprocess.check_output(cmd))
            streams = data.get('streams', [])
            
            if not streams:
                QMessageBox.warning(self, "Info", "Nessuna traccia audio trovata.")
                return

            for idx, stream in enumerate(streams):
                w = AudioTrackWidget(stream, idx, path, self.temp_dir)
                self.tracks_layout.addWidget(w)
                self.track_widgets.append(w)
            
            self.export_btn.setEnabled(True)
            
        except Exception as e:
            QMessageBox.critical(self, "Errore", str(e))

    def export_video(self):
        if not self.current_video_path: return
        
        selected = [w.index for w in self.track_widgets if w.checkbox.isChecked()]
        if not selected:
            QMessageBox.warning(self, "No Audio", "Seleziona almeno una traccia.")
            return
            
        out_path, _ = QFileDialog.getSaveFileName(self, "Salva", "", "Video (*.mp4 *.mkv)")
        if not out_path: return
        
        # Ferma tutti i player prima di esportare
        for w in self.track_widgets: w.player.stop()

        cmd = ['ffmpeg', '-y', '-i', self.current_video_path, '-map', '0:v', '-c:v', 'copy']
        
        filter_str = "".join([f"[0:a:{i}]" for i in selected])
        filter_str += f"amix=inputs={len(selected)}[aout]"
        
        cmd.extend(['-filter_complex', filter_str, '-map', '[aout]', '-c:a', 'aac', out_path])
        
        self.drop_label.setText("Esportazione in corso...")
        QApplication.processEvents()
        
        try:
            subprocess.run(cmd, check=True)
            QMessageBox.information(self, "Fatto", "Video esportato con successo!")
            self.drop_label.setText("Esportazione completata.")
        except subprocess.CalledProcessError:
            QMessageBox.critical(self, "Errore", "Errore FFmpeg durante l'esportazione.")

    def closeEvent(self, event):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        event.accept()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())