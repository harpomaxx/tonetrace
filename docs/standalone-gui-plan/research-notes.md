# Research notes: standalone GUI examples and libraries

These notes combine targeted web searches with the current repository constraints. The goal is a Linux standalone GUI resembling the NeuralNote screenshot while preserving the implemented `notegrabber` CLI/server/viewer features.

## Recommended stack: PySide6 + custom Qt widgets

### PySide6 / Qt for Python

- Official project/package: https://pypi.org/project/PySide6/
- Qt for Python docs: https://doc.qt.io/qtforpython-6/
- License: PySide6 is available under LGPLv3/GPL/commercial terms. Good fit for free software if license obligations are respected.

Useful docs/examples:

- QGraphicsView docs: https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QGraphicsView.html
- QGraphicsScene docs: https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QGraphicsScene.html
- PythonGUIs QGraphicsView tutorial: https://www.pythonguis.com/tutorials/pyside6-qgraphics-vector-graphics/
- QDial docs for knob-like controls: https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QDial.html
- PySide6 widgets tutorial: https://www.pythonguis.com/tutorials/pyside6-widgets/
- PySide6 custom widgets tutorial: https://www.pythonguis.com/tutorials/pyside6-creating-your-own-custom-widgets/

How this applies:

- `QMainWindow` for the app shell.
- `QDockWidget`/left `QWidget` for controls.
- `QGraphicsView` or custom `QWidget.paintEvent` for piano roll/heatmap.
- `QDial` or styled sliders for NeuralNote-like knobs.
- `QTableView` for detected sequence/chord table.

## Waveform and heatmap drawing

### PyQtGraph

- Homepage: https://www.pyqtgraph.org/
- Docs/how-to-use: https://pyqtgraph.readthedocs.io/en/latest/how_to_use.html
- Image display docs: https://pyqtgraph.readthedocs.io/en/latest/getting_started/images.html
- ImageView docs: https://pyqtgraph.readthedocs.io/en/latest/api_reference/widgets/imageview.html
- Audio plotting example article: https://www.pythonguis.com/faq/interactive-audio-editor-plotting/

Pros:

- fast plotting with NumPy
- easy waveform view
- heatmap/image support
- integrates with PySide6/PyQt

Cons:

- piano roll editing may be cleaner with custom Qt painting/QGraphicsView than generic plot widgets
- styling to match NeuralNote may take work

Recommendation:

- Use PyQtGraph for early waveform and maybe heatmap prototype.
- Move the piano-roll note overlay to custom Qt painting or `QGraphicsView` if editing/selecting/resizing notes becomes central.

## Piano roll / MIDI visualization references

- Pypianoroll docs: https://hermandong.com/pypianoroll/
- Pypianoroll GitHub: https://github.com/salu133445/pypianoroll
- GitHub piano-roll topic: https://github.com/topics/piano-roll
- Qt forum discussion on timeline/piano-roll choices: https://forum.qt.io/topic/111455/qabstractscrollarea-or-qgraphicsview
- Example C++ Qt piano roll source (`QGraphicsView`-style reference): https://github.com/waddlesplash/ragingmidi/blob/master/src/Gui/PianoRoll.cpp

Usefulness:

- Pypianoroll is useful for data conversion/visualization ideas, not necessarily for interactive editing.
- Existing piano-roll apps confirm the basic layout: note grid, keyboard axis, rectangles, timeline.
- For our app, implement a focused `PianoRollView` rather than importing a large sequencer.

## Audio playback

### Qt Multimedia

- QMediaPlayer docs: https://doc.qt.io/qtforpython-6/PySide6/QtMultimedia/QMediaPlayer.html
- Qt Multimedia overview: https://doc.qt.io/qtforpython-6/PySide6/QtMultimedia/index.html
- Player example: https://doc.qt.io/qtforpython-6/examples/example_multimedia_player.html
- QSoundEffect docs: https://doc.qt.io/qtforpython-6/PySide6/QtMultimedia/QSoundEffect.html

Recommended use:

- `QMediaPlayer` + `QAudioOutput` for original audio and rendered MIDI WAV playback.
- Keep using existing `visualizer.render_midi_to_wav()` initially for MIDI audio rendering.

### MIDI synthesis/rendering

Current project uses TiMidity++ when available. Alternatives:

- FluidSynth: https://www.fluidsynth.org/
- pyFluidSynth package: https://pypi.org/project/pyfluidsynth/
- midi2audio wrapper: https://github.com/bzamecnik/midi2audio
- mido + python-rtmidi for MIDI I/O: https://mido.readthedocs.io/en/latest/backends/rtmidi.html and https://pypi.org/project/python-rtmidi/

Recommendation:

- Keep TiMidity++ rendering for milestone 1 because it is already integrated.
- Add FluidSynth later if bundled playback/rendering becomes important.

## Alternative GUI routes

### GTK4/libadwaita

- GTK Python guide: https://www.gtk.org/docs/language-bindings/python
- PyGObject docs: https://pygobject.gnome.org/
- GTK4 Python tutorial: https://github.com/Taiko2k/GTK4PythonTutorial

Pros:

- very Linux/GNOME-native
- LGPL stack
- good Flatpak story

Cons:

- custom heatmap/piano-roll editing has fewer immediately reusable examples than Qt/PyQtGraph
- packaging Python + ONNX + GTK can still be involved

Verdict: good Linux-native option, but slower for this project than PySide6.

### pywebview / Tauri

pywebview:

- Homepage/docs: https://pywebview.flowrl.com/
- GitHub: https://github.com/r0x0r/pywebview
- License: BSD
- Linux backends: GTK or Qt WebKit/WebEngine depending setup

Tauri:

- Tauri site: https://tauri.app/
- Linux bundles: https://v1.tauri.app/v1/guides/building/linux/
- Sidecars: https://v2.tauri.app/develop/sidecar/
- Example Python sidecar app: https://github.com/dieharders/example-tauri-v2-python-server-sidecar

Pros:

- maximum reuse of the current HTML viewer
- fast path to a windowed desktop app
- Tauri can produce Linux AppImage/deb/rpm bundles

Cons:

- requires a bridge between JS and Python analysis/export
- still inherits web UI limitations unless substantial JS/editor work is added
- Python sidecar packaging with Basic Pitch/ONNX is non-trivial

Verdict: useful fallback if we want a quick “standalone shell”, but not the best long-term editing architecture.

## Packaging research

- Qt for Python deployment: https://doc.qt.io/qtforpython-6/deployment/index.html
- Qt for Python + PyInstaller: https://doc.qt.io/qtforpython-6/deployment/deployment-pyinstaller.html
- PyInstaller docs: https://pyinstaller.org/
- PythonGUIs packaging overview: https://www.pythonguis.com/faq/linux-packaging-prefered-formats/
- PythonGUIs PyInstaller/PySide6 notes: https://www.pythonguis.com/faq/pyinstaller-4-2-pyside6/
- Tauri Linux bundle docs: https://v1.tauri.app/v1/guides/building/linux/

Packaging recommendation:

1. Development: editable install + `notegrabber-gui` console script.
2. First distributable: PyInstaller one-folder build for Linux.
3. User-friendly binary: wrap PyInstaller output as AppImage.
4. Later distro-friendly package: Flatpak manifest, with care for Python wheels and ONNX Runtime.

## Licensing notes

- PySide6: LGPLv3/GPL/commercial. OK for free software; keep dynamic linking and license notices.
- PyQtGraph: MIT.
- pywebview: BSD.
- PyGObject: LGPLv2.1+.
- Basic Pitch: Apache-2.0.
- ONNX Runtime: MIT.
- TiMidity++/FluidSynth: check distribution/system-package licenses when bundling.

## Recommended decision

Start with **PySide6 + PyQtGraph/custom painting**.

Milestone 1 should be a native desktop shell that reuses existing Python backends and models, not a full rewrite. It should prove the core workflow: open file, analyze in worker thread, show waveform + heatmap + note rectangles, adjust thresholds, export tuned MIDI.
