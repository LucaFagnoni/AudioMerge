# MixCut - Video Cutter & Audio Mixer

**MixCut** is a modern, dark-themed desktop application built with Python and PyQt6. It allows you to cut video clips with frame precision, mix multiple audio tracks, and normalize volume levels before exporting.

![MixCut Screenshot]("pictures/mixcut-screenshot.png")
*(Replace this link with an actual screenshot of your app)*

## ✨ Key Features

*   **🎬 Drag & Drop Interface:** Easily load videos with a modern overlay UI.
*   **✂️ Advanced Cutting:**
    *   **Precise Cut (Re-encode):** Frame-perfect cutting using `libx264`.
    *   **Fast Cut (Stream Copy):** Instant export without re-encoding (cuts at keyframes).
*   **🎹 Multi-Track Audio Mixing:**
    *   Visualize waveforms for every audio stream.
    *   Adjust volume per track (-30dB to +30dB) with real-time preview.
    *   Enable/Disable specific tracks.
    *   **Auto-Normalization:** Applies `dynaudnorm` filter on export for balanced audio.
*   **⏱️ Professional Timeline:**
    *   Frame-by-frame stepping.
    *   Visual indicators for **Keyframes (I-Frames)** (Yellow marker) vs Current Frame (White marker).
    *   IN/OUT point selection.

## 🛠️ Requirements

To run from source, you need **Python 3.10+** and **FFmpeg**.

1.  **FFmpeg & FFprobe:**
    *   Download [FFmpeg builds](https://www.gyan.dev/ffmpeg/builds/).
    *   Place `ffmpeg.exe` and `ffprobe.exe` in the project root folder (or add them to your system PATH).

2.  **Python Dependencies:**
    ```bash
    pip install PyQt6
    ```

## 🚀 How to Run

1.  Clone the repository.
2.  Ensure `ffmpeg.exe` and `ffprobe.exe` are present.
3.  Run the main script:
    ```bash
    python main.py
    ```

## 📦 Building the Executable

You can create a standalone `.exe` using PyInstaller.

1.  Install PyInstaller:
    ```bash
    pip install pyinstaller
    ```
2.  Run the build command (ensure you have the `icons` folder and `app_icon.ico`):
    ```bash
    python -m PyInstaller --noconsole --onefile --name "MixCut" ^
      --icon "app_icon.ico" ^
      --add-data "app_icon.ico;." ^
      --add-data "icons;icons" ^
      --add-binary "ffmpeg.exe;." ^
      --add-binary "ffprobe.exe;." ^
      --exclude-module numpy ^
      --exclude-module matplotlib ^
      --exclude-module PyQt6.QtWebEngine ^
      main.py
    ```

## 📝 License

This project is open-source. Feel free to modify and distribute.