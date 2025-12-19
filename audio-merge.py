import sys
import os
import json
import subprocess
import tempfile
import shutil
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QPushButton, QCheckBox, 
                             QScrollArea, QFileDialog, QMessageBox, QProgressBar, QFrame)
from PyQt6.QtCore import Qt, QUrl, QSize
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QIcon, QAction
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput

class AudioTrackWidget(QFrame):
    """
    Widget che rappresenta una singola traccia audio.
    Gestisce la checkbox, il nome e il player per l'anteprima.
    """
    def __init__(self, track_info, index, file_path, temp_dir):
        super().__init__()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.track_info = track_info
        self.index = index # Indice dello stream audio nel file originale
        self.file_path = file_path
        self.temp_file = None
        
        # Layout orizzontale
        layout = QHBoxLayout()
        self.setLayout(layout)
        
        # Checkbox per attivare/disattivare
        self.checkbox = QCheckBox()
        self.checkbox.setChecked(True)
        self.checkbox.setToolTip("Includi questa traccia nel mix finale")
        layout.addWidget(self.checkbox)
        
        # Info Traccia
        lang = track_info.get('tags', {}).get('language', 'unk')
        codec = track_info.get('codec_name', 'unknown')
        title = track_info.get('tags', {}).get('title', f"Track {index}")
        label_text = f"<b>Traccia {index}</b> ({codec}) - Lingua: {lang.upper()}<br>{title}"
        
        self.label = QLabel(label_text)
        layout.addWidget(self.label)
        
        # Player Anteprima
        self.play_btn = QPushButton("▶ Play")
        self.play_btn.setFixedWidth(80)
        self.play_btn.clicked.connect(self.toggle_playback)
        layout.addWidget(self.play_btn)
        
        # Setup Audio Player
        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)
        self.player.playbackStateChanged.connect(self.on_state_changed)
        
        # Estrazione asincrona (simulata qui per brevità, meglio farla on-demand o thread)
        self.extract_preview(temp_dir)

    def extract_preview(self, temp_dir):
        """
        Estrae una porzione audio in un file temporaneo WAV per l'anteprima
        senza dover gestire stream complessi in tempo reale.
        """
        self.temp_file = os.path.join(temp_dir, f"preview_{self.index}.wav")
        # Estrae i primi 30 secondi dell'audio specifico per l'anteprima
        cmd = [
            'ffmpeg', '-y', '-i', self.file_path,
            '-map', f'0:a:{self.index}', # Seleziona questo specifico stream audio
            '-t', '30', # Solo 30 secondi per l'anteprima
            '-ac', '2', # Downmix a stereo per compatibilità player
            '-f', 'wav', 
            self.temp_file
        ]
        # Eseguiamo in background silenziosamente
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def toggle_playback(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.stop()
        else:
            if os.path.exists(self.temp_file):
                self.player.setSource(QUrl.fromLocalFile(self.temp_file))
                self.audio_output.setVolume(1.0)
                self.player.play()
            else:
                self.play_btn.setText("Loading...")

    def on_state_changed(self, state):
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.play_btn.setText("⏹ Stop")
        else:
            self.play_btn.setText("▶ Play")

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Video Audio Mixer (FFmpeg)")
        self.resize(600, 500)
        self.setAcceptDrops(True)
        
        self.current_video_path = None
        self.track_widgets = []
        self.temp_dir = tempfile.mkdtemp()
        
        # Main Widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        self.main_layout = QVBoxLayout(central_widget)
        
        # Drop Area UI
        self.drop_label = QLabel("DRAG & DROP UN VIDEO QUI")
        self.drop_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_label.setStyleSheet("border: 2px dashed #aaa; border-radius: 10px; padding: 30px; font-size: 16px; color: #555;")
        self.main_layout.addWidget(self.drop_label)
        
        # Scroll Area per le tracce
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.tracks_layout = QVBoxLayout(self.scroll_content)
        self.tracks_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.scroll_content)
        self.main_layout.addWidget(self.scroll)
        
        # Export Button
        self.export_btn = QPushButton("Esporta Video Mixato")
        self.export_btn.setFixedHeight(50)
        self.export_btn.setStyleSheet("font-size: 14px; font-weight: bold; background-color: #0078d7; color: white;")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self.export_video)
        self.main_layout.addWidget(self.export_btn)
        
        # Check FFmpeg
        self.check_ffmpeg()

    def check_ffmpeg(self):
        try:
            subprocess.run(['ffmpeg', '-version'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            QMessageBox.critical(self, "Errore", "FFmpeg non è stato trovato nel sistema.\nInstallalo e aggiungilo al PATH.")
            sys.exit(1)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent):
        files = [u.toLocalFile() for u in event.mimeData().urls()]
        if files:
            self.load_video(files[0])

    def load_video(self, path):
        self.current_video_path = path
        self.drop_label.setText(f"File caricato: {os.path.basename(path)}")
        self.drop_label.setStyleSheet("border: 2px solid #4CAF50; border-radius: 10px; padding: 10px; color: #000;")
        
        # Pulisci vecchie tracce
        for i in reversed(range(self.tracks_layout.count())): 
            widget = self.tracks_layout.itemAt(i).widget()
            if widget: widget.deleteLater()
        self.track_widgets = []
        
        # Analizza file con ffprobe
        try:
            cmd = [
                'ffprobe', '-v', 'quiet', '-print_format', 'json', 
                '-show_streams', '-select_streams', 'a', path
            ]
            result = subprocess.check_output(cmd)
            data = json.loads(result)
            audio_streams = data.get('streams', [])
            
            if not audio_streams:
                QMessageBox.warning(self, "Attenzione", "Nessuna traccia audio trovata in questo video.")
                return

            for idx, stream in enumerate(audio_streams):
                # idx è l'indice relativo delle tracce audio (0, 1, 2...) 
                # ffmpeg usa questo indice con -map 0:a:idx
                widget = AudioTrackWidget(stream, idx, path, self.temp_dir)
                self.tracks_layout.addWidget(widget)
                self.track_widgets.append(widget)
            
            self.export_btn.setEnabled(True)
            
        except Exception as e:
            QMessageBox.critical(self, "Errore", f"Impossibile analizzare il file:\n{str(e)}")

    def export_video(self):
        if not self.current_video_path: return
        
        selected_indices = []
        for w in self.track_widgets:
            # Ferma riproduzione se attiva
            w.player.stop()
            if w.checkbox.isChecked():
                selected_indices.append(w.index)
        
        if not selected_indices:
            QMessageBox.warning(self, "Attenzione", "Devi selezionare almeno una traccia audio.")
            return

        # Finestra salvataggio
        output_path, _ = QFileDialog.getSaveFileName(self, "Salva Video", "", "Video Files (*.mp4 *.mkv *.mov)")
        if not output_path: return

        # Costruzione comando FFmpeg complesso
        # Logica: 
        # 1. Copia flusso video
        # 2. Prendi flussi audio selezionati
        # 3. Usa filtro 'amix' per unirli
        
        cmd = ['ffmpeg', '-y', '-i', self.current_video_path]
        
        # Mappa il video originale
        cmd.extend(['-map', '0:v'])
        cmd.extend(['-c:v', 'copy']) # Copia video senza re-encoding
        
        # Filtro complesso per audio
        # Esempio: [0:a:0][0:a:2]amix=inputs=2[aout]
        filter_inputs = ""
        for idx in selected_indices:
            filter_inputs += f"[0:a:{idx}]"
            
        filter_complex = f"{filter_inputs}amix=inputs={len(selected_indices)}[aout]"
        
        cmd.extend(['-filter_complex', filter_complex])
        cmd.extend(['-map', '[aout]'])
        cmd.extend(['-c:a', 'aac', '-b:a', '192k']) # Ricodifica il mix in AAC
        
        cmd.append(output_path)
        
        # Esecuzione
        self.drop_label.setText("Esportazione in corso... Attendere.")
        QApplication.processEvents() # Aggiorna UI
        
        try:
            subprocess.run(cmd, check=True)
            QMessageBox.information(self, "Successo", f"Video esportato correttamente:\n{output_path}")
            self.drop_label.setText("Esportazione completata.")
        except subprocess.CalledProcessError:
            QMessageBox.critical(self, "Errore", "C'è stato un errore durante l'esportazione con FFmpeg.")
            self.drop_label.setText("Errore esportazione.")

    def closeEvent(self, event):
        # Pulizia file temporanei alla chiusura
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        event.accept()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())