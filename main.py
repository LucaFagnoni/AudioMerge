import sys
import os
import json
import subprocess
import tempfile
import shutil
import wave
import struct
import bisect 

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QPushButton, QCheckBox, 
                             QScrollArea, QFileDialog, QMessageBox, QFrame, 
                             QSizePolicy, QSlider, QDoubleSpinBox, QStackedWidget, 
                             QGraphicsView, QGraphicsScene, QStyle, QGridLayout)
from PyQt6.QtCore import Qt, QUrl, pyqtSignal, QSize, QEvent, QRectF
from PyQt6.QtGui import (QPainter, QColor, QPen, QBrush, QAction, QKeySequence, 
                         QDragEnterEvent, QDropEvent, QDragMoveEvent, QIcon, QFont, 
                         QLinearGradient, QPainterPath)
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QGraphicsVideoItem

# Import moduli locali
from utils import FFMPEG_BIN, FFPROBE_BIN, format_time
from workers import AudioExtractorThread, ExportThread, KeyframeLoaderThread

# --- HELPER ICONE ---
def load_custom_icon(name, fallback_text, system_icon=None):
    base_path = os.path.dirname(os.path.abspath(__file__))
    icon_path = os.path.join(base_path, "icons", name)
    if hasattr(sys, '_MEIPASS'):
        icon_path = os.path.join(sys._MEIPASS, "icons", name)

    if os.path.exists(icon_path): return QIcon(icon_path), ""
    if system_icon:
        style = QApplication.style()
        icon = style.standardIcon(system_icon)
        if not icon.isNull(): return icon, ""
    return QIcon(), fallback_text

# --- TIMELINE WIDGET ---
class MainTimeline(QWidget):
    seek_requested = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self.setFixedHeight(40)
        self.duration = 0
        self.position = 0
        self.in_point = 0
        self.out_point = 0
        self.nearest_keyframe = -1
        self.setStyleSheet("background-color: #222; border-top: 1px solid #555;")

    def set_duration(self, duration):
        self.duration = duration
        self.reset_points()
        self.update()

    def reset_points(self):
        self.in_point = 0
        self.out_point = self.duration
        self.nearest_keyframe = -1
        self.update()

    def set_position(self, pos):
        self.position = pos
        self.update()
    
    def set_nearest_keyframe(self, ms):
        self.nearest_keyframe = ms
        self.update()

    def set_in_point(self):
        self.in_point = self.position
        if self.in_point > self.out_point: self.out_point = self.duration
        self.update()

    def set_out_point(self):
        self.out_point = self.position
        if self.out_point < self.in_point: self.in_point = 0
        self.update()

    def mousePressEvent(self, event):
        if self.duration > 0 and event.button() == Qt.MouseButton.LeftButton:
            self._handle_click(event.pos().x())

    def mouseMoveEvent(self, event):
        if self.duration > 0 and (event.buttons() & Qt.MouseButton.LeftButton):
            self._handle_click(event.pos().x())

    def _handle_click(self, x):
        pct = x / self.width()
        ms = int(self.duration * pct)
        self.seek_requested.emit(ms)

    def paintEvent(self, event):
        painter = QPainter(self)
        w = self.width()
        h = self.height()
        painter.fillRect(0, 10, w, h-20, QColor("#333"))

        if self.duration <= 0: return

        x_in = int((self.in_point / self.duration) * w) if self.duration > 0 else 0
        x_out = int((self.out_point / self.duration) * w) if self.duration > 0 else w
        sel_width = x_out - x_in
        
        if sel_width > 0:
            painter.fillRect(x_in, 10, sel_width, h-20, QColor("#0078d7"))

        # Keyframe Marker (Giallo)
        if self.nearest_keyframe >= 0:
            x_key = int((self.nearest_keyframe / self.duration) * w)
            painter.setPen(QPen(QColor("#ffd700"), 2))
            painter.drawLine(x_key, 0, x_key, h)

        # Markers In/Out
        painter.setPen(QPen(QColor("#00ff00"), 2)); painter.drawLine(x_in, 0, x_in, h)
        painter.setPen(QPen(QColor("#ff0000"), 2)); painter.drawLine(x_out, 0, x_out, h)

        # Cursor
        x_pos = int((self.position / self.duration) * w)
        painter.setPen(QPen(QColor("#ffffff"), 2)); painter.drawLine(x_pos, 0, x_pos, h)


# --- WAVEFORM WIDGET ---
class WaveformWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(50)
        self.samples = []
        self.duration_ms = 0
        self.current_position_ms = 0
        self.gain_linear = 1.0
        self.setStyleSheet("background-color: #1a1a1a; border: 1px solid #333;")

    def set_gain_db(self, db):
        self.gain_linear = 10 ** (db / 20.0)
        self.update()

    def load_data(self, file_path):
        if not os.path.exists(file_path): return
        try:
            with wave.open(file_path, 'r') as wf:
                n_frames = wf.getnframes()
                fr = wf.getframerate()
                self.duration_ms = (n_frames / fr) * 1000
                raw = wf.readframes(n_frames)
                count = len(raw) // 2
                fmt = f"<{count}h"
                samples = struct.unpack(fmt, raw)
                target = 2000
                step = max(1, count // target)
                self.samples = []
                for i in range(0, count, step):
                    chunk = samples[i:i+step]
                    if chunk:
                        val = max(abs(x) for x in chunk) / 32768.0
                        self.samples.append(val)
                self.update()
        except: pass

    def set_position(self, ms):
        self.current_position_ms = ms
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#1a1a1a"))
        if not self.samples: return
        w, h, mid = self.width(), self.height(), self.height() / 2
        total = len(self.samples)
        step = total / w
        
        pen = QPen(QColor("#00bcd4"))
        if self.gain_linear > 1.0: pen.setColor(QColor("#00e5ff"))
        painter.setPen(pen)

        for x in range(w):
            idx = int(x * step)
            if idx >= total: break
            val = self.samples[idx] * self.gain_linear
            if val > 1.0: val = 1.0
            bar_h = val * (h - 4)
            painter.drawLine(int(x), int(mid - bar_h/2), int(x), int(mid + bar_h/2))

        if self.duration_ms > 0:
            x_pos = int((self.current_position_ms / self.duration_ms) * w)
            painter.setPen(QPen(QColor("white"), 1, Qt.PenStyle.DashLine))
            painter.drawLine(x_pos, 0, x_pos, h)


# --- AUDIO TRACK WIDGET ---
class AudioTrackWidget(QFrame):
    track_loaded = pyqtSignal(object) 

    def __init__(self, track_info, index, file_path, temp_dir):
        super().__init__()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.index = index
        self.temp_file = os.path.join(temp_dir, f"track_{index}.wav")
        self.current_db = 0.0
        self.is_active = True
        
        self.setFixedHeight(110)
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(5,5,5,5); self.layout.setSpacing(10)

        left = QVBoxLayout()
        self.checkbox = QCheckBox(f"Track {index}")
        self.checkbox.setChecked(True)
        self.checkbox.toggled.connect(self.on_toggle_active)
        lang = track_info.get('tags', {}).get('language', 'unk').upper()
        left.addWidget(self.checkbox)
        left.addWidget(QLabel(f"{lang} ({track_info.get('codec_name', '')})", styleSheet="color: #888; font-size: 10px;"))
        self.layout.addLayout(left)

        self.waveform = WaveformWidget()
        self.layout.addWidget(self.waveform, stretch=1)

        right = QVBoxLayout()
        self.slider = QSlider(Qt.Orientation.Vertical)
        self.slider.setRange(-30, 30); self.slider.setValue(0)
        self.slider.valueChanged.connect(self.on_gain_change)
        
        self.spin = QDoubleSpinBox()
        self.spin.setRange(-30, 30); self.spin.setSuffix(" dB"); self.spin.setDecimals(0)
        self.spin.setFixedWidth(55); self.spin.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        self.spin.valueChanged.connect(self.on_spin_change)
        
        right.addWidget(self.slider, alignment=Qt.AlignmentFlag.AlignHCenter)
        right.addWidget(self.spin, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.layout.addLayout(right)

        self.player = QMediaPlayer()
        self.audio_out = QAudioOutput()
        self.player.setAudioOutput(self.audio_out)
        self.update_volume()

        self.extractor = AudioExtractorThread(file_path, index, self.temp_file)
        self.extractor.finished_extraction.connect(self.on_ready)
        self.extractor.start()

    def on_ready(self, path, idx):
        self.waveform.load_data(path)
        if os.path.exists(path): 
            self.player.setSource(QUrl.fromLocalFile(path))
            self.track_loaded.emit(self)

    def on_toggle_active(self, checked):
        self.is_active = checked
        self.update_volume()

    def on_gain_change(self, val):
        self.spin.blockSignals(True); self.spin.setValue(val); self.spin.blockSignals(False)
        self.current_db = val
        self.waveform.set_gain_db(val)
        self.update_volume()

    def on_spin_change(self, val):
        self.slider.blockSignals(True); self.slider.setValue(int(val)); self.slider.blockSignals(False)
        self.current_db = val
        self.waveform.set_gain_db(val)
        self.update_volume()

    def update_volume(self):
        if not self.is_active:
            self.audio_out.setVolume(0.0)
        else:
            HEADROOM_FACTOR = 0.25 
            linear = (10 ** (self.current_db / 20.0)) * HEADROOM_FACTOR
            self.audio_out.setVolume(min(1.0, linear))

    def get_db(self): return self.current_db
    
    def sync_play(self):
        if self.player.source().isValid(): self.player.play()

    def sync_pause(self): self.player.pause()
    def sync_stop(self): self.player.stop()

    def sync_position(self, ms):
        self.waveform.set_position(ms)
        diff = abs(self.player.position() - ms)
        if diff > 50: self.player.setPosition(ms)

    def cleanup(self):
        self.player.stop()
        self.player.setSource(QUrl())
        try: self.extractor.finished_extraction.disconnect()
        except: pass


# --- VIDEO OVERLAY WIDGET ---
class VideoOverlay(QWidget):
    close_clicked = pyqtSignal()
    file_dropped = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.is_hovering = False
        self.is_dragging = False
        
        self.info_keyframe = 0
        self.info_current_frame = 0
        self.info_total_frames = 0
        
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)

    def update_info(self, key, curr, tot):
        self.info_keyframe = key
        self.info_current_frame = curr
        self.info_total_frames = tot
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        rect = self.rect()
        
        if self.info_total_frames > 0:
            self._draw_info_box(painter, rect)

        if self.is_dragging:
            # Scurisce sfondo per Drag
            painter.fillRect(rect, QColor(0, 0, 0, 160))
            self._draw_central_feedback(painter, rect, QColor("#00e5ff"), "DROP VIDEO", is_drop=True)
        elif self.is_hovering:
            # Scurisce sfondo per Close
            painter.fillRect(rect, QColor(0, 0, 0, 160))
            self._draw_central_feedback(painter, rect, QColor("#ff5252"), "CLOSE VIDEO", is_drop=False)

    def _draw_info_box(self, painter, rect):
        key, curr, tot = self.info_keyframe, self.info_current_frame, self.info_total_frames
        text_full = f"({key}) {curr} / {tot}"
        
        font = QFont("Consolas", 12, QFont.Weight.Bold)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        
        text_w = metrics.horizontalAdvance(text_full) + 20
        text_h = metrics.height() + 10
        
        x = rect.width() - text_w - 20
        y = rect.height() - text_h - 20
        
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 180))
        painter.drawRoundedRect(QRectF(x, y, text_w, text_h), 5, 5)
        
        painter.setPen(QColor("#ffd700"))
        painter.drawText(int(x + 10), int(y + metrics.ascent() + 5), f"({key})")
        key_w = metrics.horizontalAdvance(f"({key}) ")
        painter.setPen(QColor("white"))
        painter.drawText(int(x + 10 + key_w), int(y + metrics.ascent() + 5), f"{curr} / {tot}")

    def _draw_central_feedback(self, painter, rect, accent_color, text, is_drop):
        box_w, box_h = 280, 180
        center = rect.center()
        box_rect = QRectF(center.x() - box_w/2, center.y() - box_h/2, box_w, box_h)
        
        pen = QPen(accent_color)
        pen.setWidth(4)
        pen.setStyle(Qt.PenStyle.DashLine if is_drop else Qt.PenStyle.SolidLine)
        painter.setPen(pen)
        
        painter.setBrush(QColor(30, 30, 30, 200))
        painter.drawRoundedRect(box_rect, 20, 20)
        
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(accent_color)
        cx, cy = center.x(), center.y() - 15
        
        if is_drop:
            path = QPainterPath()
            path.moveTo(cx - 20, cy - 30); path.lineTo(cx + 20, cy - 30)
            path.lineTo(cx + 20, cy + 5); path.lineTo(cx + 40, cy + 5)
            path.lineTo(cx, cy + 45);     path.lineTo(cx - 40, cy + 5)
            path.lineTo(cx - 20, cy + 5); path.closeSubpath()
            painter.drawPath(path)
        else:
            painter.setPen(QPen(accent_color, 8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            off = 25
            painter.drawLine(int(cx - off), int(cy - off), int(cx + off), int(cy + off))
            painter.drawLine(int(cx + off), int(cy - off), int(cx - off), int(cy + off))

        painter.setPen(QColor("white"))
        font = QFont("Segoe UI", 20, QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(QRectF(box_rect.left(), cy + 50, box_w, 40), Qt.AlignmentFlag.AlignCenter, text)

    def enterEvent(self, event):
        self.is_hovering = True
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.is_hovering = False
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton: self.close_clicked.emit()
        super().mousePressEvent(event)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            self.is_dragging = True
            self.update()
            event.acceptProposedAction()
        else: event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent):
        if event.mimeData().hasUrls(): event.acceptProposedAction()
        else: event.ignore()

    def dragLeaveEvent(self, event):
        self.is_dragging = False
        self.update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent):
        self.is_dragging = False
        self.is_hovering = False
        self.update()
        files = [u.toLocalFile() for u in event.mimeData().urls()]
        if files: self.file_dropped.emit(files[0])


# --- VIDEO PLAYER VIEW ---
class VideoPlayerView(QGraphicsView):
    file_dropped = pyqtSignal(str)
    close_clicked = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.video_item = QGraphicsVideoItem()
        self.video_item.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
        self.scene.addItem(self.video_item)
        self.video_item.nativeSizeChanged.connect(self._on_native_size_changed)
        
        self.setStyleSheet("background: black; border: none;")
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setAcceptDrops(True)
        
        self.overlay = VideoOverlay(self)
        self.overlay.close_clicked.connect(self.close_clicked)
        self.overlay.file_dropped.connect(self.file_dropped)

    def update_overlay_info(self, key, curr, tot):
        self.overlay.update_info(key, curr, tot)

    def _on_native_size_changed(self, size):
        if size.isValid():
            self.video_item.setSize(size)
            self.scene.setSceneRect(0, 0, size.width(), size.height())
            self.fitInView(self.video_item, Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event):
        if self.video_item.size().isValid():
            self.fitInView(self.video_item, Qt.AspectRatioMode.KeepAspectRatio)
        self.overlay.resize(event.size())
        super().resizeEvent(event)

    def dragEnterEvent(self, event): self.overlay.dragEnterEvent(event)
    def dragMoveEvent(self, event): self.overlay.dragMoveEvent(event)
    def dragLeaveEvent(self, event): self.overlay.dragLeaveEvent(event)
    def dropEvent(self, event): self.overlay.dropEvent(event)


# --- START SCREEN ---
class StartScreen(QWidget):
    def __init__(self, load_callback):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_title = QLabel("MixCut")
        lbl_title.setStyleSheet("font-size: 24px; font-weight: bold; color: #00bcd4; margin-bottom: 20px;")
        lbl_desc = QLabel("Drag & Drop a video here")
        lbl_desc.setStyleSheet("color: #aaa; font-size: 16px; margin: 10px;")
        btn_load = QPushButton("Open Video")
        btn_load.setFixedSize(200, 50)
        btn_load.setStyleSheet("background-color: #0078d7; font-size: 16px; font-weight: bold; border-radius: 8px;")
        btn_load.clicked.connect(load_callback)
        layout.addWidget(lbl_title, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl_desc, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(btn_load, alignment=Qt.AlignmentFlag.AlignCenter)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MixCut")
        self.resize(1000, 800)
        self.setAcceptDrops(True)
        
        self.video_path = None
        self.duration = 0
        self.fps = 30.0
        self.total_frames = 0
        self.keyframes = []
        self.tracks = []
        self.temp_dir = tempfile.mkdtemp()

        self.setStyleSheet("""
            QMainWindow { background-color: #2b2b2b; color: #eee; }
            QLabel { color: #eee; }
            QPushButton { background: #444; border-radius: 4px; padding: 5px; color: white; }
            QPushButton:hover { background: #555; }
            QSlider::groove:vertical { background: #333; width: 4px; }
            QSlider::handle:vertical { background: #00bcd4; height: 10px; margin: 0 -4px; border-radius: 5px; }
        """)

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.start_screen = StartScreen(self.open_file_dialog)
        self.stack.addWidget(self.start_screen)
        self.editor_widget = QWidget()
        self.setup_editor_ui()
        self.stack.addWidget(self.editor_widget)
        self.stack.setCurrentIndex(0)

        self.check_ffmpeg()

    def setup_editor_ui(self):
        main_layout = QVBoxLayout(self.editor_widget)
        main_layout.setSpacing(5)

        self.video_view = VideoPlayerView()
        self.video_view.close_clicked.connect(self.close_video)
        self.video_view.file_dropped.connect(self.load_video)
        main_layout.addWidget(self.video_view, stretch=2)

        # --- CONTROLS LAYOUT (GRID FOR PERFECT CENTERING) ---
        controls_container = QWidget()
        controls_layout = QGridLayout(controls_container)
        controls_layout.setContentsMargins(0, 0, 0, 0)

        # Left Group
        left_widget = QWidget()
        left_box = QHBoxLayout(left_widget); left_box.setContentsMargins(0,0,0,0)
        
        self.lbl_in_frame = QLabel("Start: 0")
        self.lbl_in_frame.setStyleSheet("color: #00ff00; font-weight: bold;")
        
        self.lbl_out_frame = QLabel("End: 0")
        self.lbl_out_frame.setStyleSheet("color: #ff0000; font-weight: bold;")

        def create_btn(icon_file, system_icon, text, func, shortcut=None, tooltip=""):
            btn = QPushButton()
            icon, icon_text = load_custom_icon(icon_file, text, system_icon)
            if not icon.isNull():
                btn.setIcon(icon); btn.setIconSize(QSize(24, 24))
            else: btn.setText(icon_text)
            btn.setToolTip(tooltip)
            btn.clicked.connect(func)
            if shortcut: btn.setShortcut(QKeySequence(shortcut))
            btn.setFixedSize(40, 40)
            return btn

        self.btn_in = create_btn("in.png", QStyle.StandardPixmap.SP_MediaSkipBackward, "[ I ]", self.set_in_point, "I", "Set IN Point")
        self.btn_prev = create_btn("prev.png", QStyle.StandardPixmap.SP_MediaSeekBackward, "<", self.step_back, None, "Previous Frame")
        self.btn_play = create_btn("play.png", QStyle.StandardPixmap.SP_MediaPlay, "Play", self.toggle_play, Qt.Key.Key_Space, "Play/Pause")
        self.btn_next = create_btn("next.png", QStyle.StandardPixmap.SP_MediaSeekForward, ">", self.step_fwd, None, "Next Frame")
        self.btn_out = create_btn("out.png", QStyle.StandardPixmap.SP_MediaSkipForward, "[ O ]", self.set_out_point, "O", "Set OUT Point")

        left_box.addWidget(self.btn_in)
        left_box.addWidget(self.lbl_in_frame)
        
        # Center Group
        center_widget = QWidget()
        center_box = QHBoxLayout(center_widget); center_box.setContentsMargins(0,0,0,0)
        center_box.addWidget(self.btn_prev)
        center_box.addWidget(self.btn_play)
        center_box.addWidget(self.btn_next)

        # Right Group
        right_widget = QWidget()
        right_box = QHBoxLayout(right_widget); right_box.setContentsMargins(0,0,0,0)
        right_box.addWidget(self.lbl_out_frame)
        right_box.addWidget(self.btn_out)

        # Add to Grid
        controls_layout.addWidget(left_widget, 0, 0, Qt.AlignmentFlag.AlignLeft)
        controls_layout.addWidget(center_widget, 0, 1, Qt.AlignmentFlag.AlignCenter)
        controls_layout.addWidget(right_widget, 0, 2, Qt.AlignmentFlag.AlignRight)
        
        # Stretches
        controls_layout.setColumnStretch(0, 1)
        controls_layout.setColumnStretch(2, 1)
        
        main_layout.addWidget(controls_container)

        self.timeline = MainTimeline()
        self.timeline.seek_requested.connect(self.seek_all)
        main_layout.addWidget(self.timeline)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("background: transparent; border: none;")
        self.scroll_content = QWidget()
        self.tracks_layout = QVBoxLayout(self.scroll_content)
        self.tracks_layout.setSpacing(2)
        self.tracks_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.scroll_content)
        main_layout.addWidget(self.scroll, stretch=1)

        footer = QHBoxLayout()
        
        self.chk_autosave = QCheckBox("Export to source folder (_cut)")
        self.chk_autosave.setChecked(True)
        
        self.chk_precise = QCheckBox("Precise Cut (Re-encode)")
        self.chk_precise.setChecked(False)
        
        self.btn_export = QPushButton("EXPORT")
        self.btn_export.setFixedHeight(40)
        self.btn_export.setStyleSheet("background-color: #0078d7; font-weight: bold;")
        self.btn_export.clicked.connect(self.export)
        
        footer.addWidget(self.chk_precise)
        footer.addSpacing(20)
        footer.addWidget(self.chk_autosave)
        footer.addStretch()
        footer.addWidget(self.btn_export)
        main_layout.addLayout(footer)

        self.player = QMediaPlayer()
        self.audio_out = QAudioOutput()
        self.player.setAudioOutput(self.audio_out)
        self.player.setVideoOutput(self.video_view.video_item)
        self.player.positionChanged.connect(self.on_position_changed)
        self.player.durationChanged.connect(self.on_duration_changed)
        self.player.playbackStateChanged.connect(self.update_play_icon)

    def check_ffmpeg(self):
        try: subprocess.run([FFMPEG_BIN, '-version'], stdout=subprocess.DEVNULL)
        except: QMessageBox.critical(self, "Error", "FFmpeg not found!")

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls(): e.accept()
        else: e.ignore()
    
    def dropEvent(self, e: QDropEvent):
        files = [u.toLocalFile() for u in e.mimeData().urls()]
        if files: self.load_video(files[0])

    def open_file_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open Video", "", "Video Files (*.mp4 *.mkv *.mov *.avi)")
        if path: self.load_video(path)

    def close_video(self):
        self.player.stop()
        self.player.setSource(QUrl())
        for t in self.tracks: 
            t.cleanup()
            t.deleteLater()
        self.tracks = []
        self.keyframes = []
        self.video_path = None
        self.stack.setCurrentIndex(0)

    def load_video(self, path):
        self.video_path = path
        
        self.player.setSource(QUrl.fromLocalFile(path))
        self.audio_out.setVolume(0.0) 
        
        for t in self.tracks: 
            t.cleanup()
            t.deleteLater()
        self.tracks = []
        self.stack.setCurrentIndex(1)
        
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        try:
            cmd = [FFPROBE_BIN, '-v', 'quiet', '-print_format', 'json', '-show_streams', path]
            out = subprocess.check_output(cmd, startupinfo=si)
            data = json.loads(out)
            
            v_stream = next((s for s in data['streams'] if s['codec_type'] == 'video'), {})
            if 'r_frame_rate' in v_stream:
                num, den = map(int, v_stream['r_frame_rate'].split('/'))
                self.fps = num / den if den > 0 else 30.0
            
            a_streams = [s for s in data['streams'] if s['codec_type'] == 'audio']
            for i, s in enumerate(a_streams):
                w = AudioTrackWidget(s, i, path, self.temp_dir)
                w.track_loaded.connect(self.on_track_sync_request)
                self.tracks_layout.addWidget(w)
                self.tracks.append(w)
                
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Error loading: {e}")
            return

        self.kf_loader = KeyframeLoaderThread(path)
        self.kf_loader.keyframes_found.connect(self.on_keyframes_loaded)
        self.kf_loader.start()

        self.player.play()

    def on_keyframes_loaded(self, keyframes):
        self.keyframes = sorted(keyframes)
        self.on_position_changed(self.player.position())

    def on_track_sync_request(self, track_widget):
        track_widget.player.setPosition(self.player.position())
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            track_widget.player.play()
        else:
            track_widget.player.pause()
        track_widget.update_volume()

    def on_duration_changed(self, dur):
        self.duration = dur
        self.total_frames = int((dur / 1000.0) * self.fps)
        self.timeline.set_duration(dur)
        self.lbl_out_frame.setText(f"Out: {self.total_frames}")

    def on_position_changed(self, pos):
        self.timeline.set_position(pos)
        
        current_frame = int((pos / 1000.0) * self.fps)
        key_frame = 0
        
        if self.keyframes:
            idx = bisect.bisect_left(self.keyframes, pos)
            closest_ms = 0
            if idx == 0: closest_ms = self.keyframes[0]
            elif idx == len(self.keyframes): closest_ms = self.keyframes[-1]
            else:
                before = self.keyframes[idx - 1]
                after = self.keyframes[idx]
                closest_ms = before if abs(pos - before) < abs(after - pos) else after
            
            key_frame = int((closest_ms / 1000.0) * self.fps)
            self.timeline.set_nearest_keyframe(closest_ms)
        
        self.video_view.update_overlay_info(key_frame, current_frame, self.total_frames)
        for t in self.tracks: t.sync_position(pos)

    def update_play_icon(self, state):
        if state == QMediaPlayer.PlaybackState.PlayingState:
            icon, txt = load_custom_icon("pause.png", "PAUSE", QStyle.StandardPixmap.SP_MediaPause)
        else:
            icon, txt = load_custom_icon("play.png", "PLAY", QStyle.StandardPixmap.SP_MediaPlay)
        
        if not icon.isNull():
            self.btn_play.setIcon(icon)
            self.btn_play.setText("")
        else:
            self.btn_play.setIcon(QIcon())
            self.btn_play.setText(txt)

    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            for t in self.tracks: t.sync_pause()
        else:
            self.player.play()
            for t in self.tracks: t.sync_play()

    def seek_all(self, ms):
        self.player.setPosition(ms)
        for t in self.tracks: t.player.setPosition(ms)

    def step_fwd(self):
        self.player.pause()
        for t in self.tracks: t.sync_pause()
        new_pos = self.player.position() + int(1000/self.fps)
        self.seek_all(new_pos)

    def step_back(self):
        self.player.pause()
        for t in self.tracks: t.sync_pause()
        new_pos = self.player.position() - int(1000/self.fps)
        self.seek_all(new_pos)

    def set_in_point(self): 
        self.timeline.set_in_point()
        frame = int((self.timeline.in_point / 1000.0) * self.fps)
        self.lbl_in_frame.setText(f"In: {frame}")

    def set_out_point(self): 
        self.timeline.set_out_point()
        frame = int((self.timeline.out_point / 1000.0) * self.fps)
        self.lbl_out_frame.setText(f"Out: {frame}")

    def export(self):
        if not self.video_path: return
        
        start_ms = self.timeline.in_point
        end_ms = self.timeline.out_point
        duration_ms = end_ms - start_ms
        if duration_ms <= 0:
            QMessageBox.warning(self, "Error", "Invalid selection.")
            return

        src_dir = os.path.dirname(self.video_path)
        base = os.path.splitext(os.path.basename(self.video_path))[0]
        if self.chk_autosave.isChecked():
            out_path = os.path.join(src_dir, f"{base}_cut.mp4")
        else:
            out_path, _ = QFileDialog.getSaveFileName(self, "Save", "", "Video (*.mp4)")
        if not out_path: return

        ss = format_time(start_ms)
        to = format_time(end_ms)

        active = [t for t in self.tracks if t.checkbox.isChecked()]
        if not active:
            QMessageBox.warning(self, "Warning", "No active tracks.")
            return

        self.player.pause()
        for t in self.tracks: t.sync_pause()

        cmd = [FFMPEG_BIN, '-y', '-ss', ss, '-to', to, '-i', self.video_path]
        
        if self.chk_precise.isChecked():
            cmd.extend(['-map', '0:v', '-c:v', 'libx264', '-crf', '18', '-preset', 'fast'])
        else:
            cmd.extend(['-map', '0:v', '-c:v', 'copy'])
        
        filter_parts = []
        mix_ins = ""
        for i, t in enumerate(active):
            db = t.get_db()
            filter_parts.append(f"[0:a:{t.index}]volume={db}dB[a{i}]")
            mix_ins += f"[a{i}]"
        
        complex_filter = f"{';'.join(filter_parts)};{mix_ins}amix=inputs={len(active)}[outa];[outa]dynaudnorm[a_final]"
        cmd.extend(['-filter_complex', complex_filter, '-map', '[a_final]', '-c:a', 'aac', '-b:a', '192k', out_path])

        self.btn_export.setEnabled(False); self.btn_export.setText("EXPORTING...")
        self.exporter = ExportThread(cmd, duration_ms / 1000.0)
        self.exporter.finished.connect(self.on_export_done)
        self.exporter.start()

    def on_export_done(self, success, msg):
        self.btn_export.setEnabled(True); self.btn_export.setText("EXPORT")
        if success: QMessageBox.information(self, "Done", msg)
        else: QMessageBox.critical(self, "Error", msg)

    def closeEvent(self, e):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        e.accept()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())