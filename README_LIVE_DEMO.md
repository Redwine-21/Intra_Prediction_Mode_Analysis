# Project 2502M Live Demo System

## Files

```text
project/
├── run_pipeline.sh
├── intra_prediction_analysis_complete.py
├── intra_mode_utilities.py
├── demo_app.py
└── requirements.txt
```

## Install

```bash
pip install -r requirements.txt
chmod +x run_pipeline.sh
```

External tools recommended:

```bash
ffmpeg
ffprobe
SvtAv1EncApp
EncoderApp
```

The demo can still create content-derived CSV analysis when encoder binaries are not present, but strict codec-level results require real encoder instrumentation or CSV dumps.

## Run live demo

```bash
streamlit run demo_app.py
```

## What the demo shows

- User input: video upload or generated test video
- Interactive controls: QP, FPS, frame count, resolution
- Near real-time processing: pipeline log and progress bar
- Output visualization: bitrate/size metrics, mode distributions, heatmaps, block-size charts
- Downloadable report and outputs
