import json
import os
import subprocess
import time
from pathlib import Path, PureWindowsPath
from zipfile import ZIP_DEFLATED, ZipFile

import streamlit as st

PROJECT_DIR = Path(__file__).resolve().parent
PIPELINE = PROJECT_DIR / "run_pipeline.sh"
RESULTS_DIR = PROJECT_DIR / "results"
UPLOAD_DIR = PROJECT_DIR / "demo_uploads"
DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 720
DEFAULT_FPS = 30

SVT_APP = PROJECT_DIR / "SvtAv1EncApp"
VVENC_APP = PROJECT_DIR / "vvencapp"
SVT_LIB_NAMES = ["libSvtAv1Enc.so.4", "libSvtAv1Enc.so", "libSvtAv1Enc.so.4.1.0"]

st.set_page_config(
    page_title="Project 2502M - Real Encoder CSV Demo",
    page_icon="🎬",
    layout="wide",
)

st.title("🎬 Project 2502M: Live Intra Prediction Mode Analysis")
st.caption(
    "Upload/generate a video → modified AV1/VVC encoders dump real CSV stats → "
    "normalize to 64×64 grid → visualize intra mode behavior."
)


def file_size_mb(path: Path) -> float:
    return path.stat().st_size / (1024 * 1024) if path.exists() else 0.0


def read_summary() -> dict:
    summary_path = RESULTS_DIR / "analysis_summary.json"
    if not summary_path.exists():
        return {}
    with summary_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def show_image(path: Path, caption: str) -> None:
    if path.exists():
        st.image(str(path), caption=caption, use_container_width=True)
    else:
        st.warning(f"Missing output image: {path}")


def count_csv_rows(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        return sum(1 for line in f if line.strip() and not line.lower().startswith("frame_num"))


def check_required_files() -> list[str]:
    problems: list[str] = []
    required = [
        (PIPELINE, "run_pipeline.sh"),
        (PROJECT_DIR / "intra_prediction_analysis.py", "intra_prediction_analysis.py"),
        (PROJECT_DIR / "intra_mode_utilities.py", "intra_mode_utilities.py"),
        (SVT_APP, "SvtAv1EncApp"),
        (VVENC_APP, "vvencapp"),
    ]
    for path, name in required:
        if not path.exists():
            problems.append(f"Missing {name}")

    if not any((PROJECT_DIR / name).exists() for name in SVT_LIB_NAMES):
        problems.append("Missing SVT-AV1 shared library: one of " + ", ".join(SVT_LIB_NAMES))
    return problems


def make_outputs_zip() -> Path:
    zip_path = PROJECT_DIR / "project2502m_outputs.zip"

    include_files = [
        PROJECT_DIR / "av1_stats.csv",
        PROJECT_DIR / "vvc_intra_modes.csv",
        RESULTS_DIR / "analysis_summary.json",
        RESULTS_DIR / "ANALYSIS_REPORT.txt",
        RESULTS_DIR / "PIPELINE_SUMMARY.txt",
        RESULTS_DIR / "pipeline.log",
        RESULTS_DIR / "output_av1.ivf",
        RESULTS_DIR / "output_vvc.266",
    ]

    with ZipFile(zip_path, "w", ZIP_DEFLATED) as zf:
        for file_path in include_files:
            if file_path.exists():
                zf.write(file_path, arcname=str(file_path.relative_to(PROJECT_DIR)))
        if RESULTS_DIR.exists():
            for image_path in RESULTS_DIR.rglob("*.png"):
                zf.write(image_path, arcname=str(image_path.relative_to(PROJECT_DIR)))
    return zip_path


def run_pipeline(command: list[str]):
    log_box = st.empty()
    progress = st.progress(0)
    lines: list[str] = []

    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = f"{PROJECT_DIR}:{env.get('LD_LIBRARY_PATH', '')}"

    process = subprocess.Popen(
        command,
        cwd=str(PROJECT_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
    )

    step_keywords = [
        "CLEANING OLD OUTPUTS",
        "PREPARING INPUT",
        "ENCODING WITH SVT-AV1",
        "ENCODING WITH VVC",
        "VALIDATING REAL ENCODER CSV",
        "RUNNING ANALYSIS",
        "REPORT",
        "SUMMARY",
    ]
    step = 0

    assert process.stdout is not None
    for line in process.stdout:
        clean = line.rstrip()
        lines.append(clean)
        if any(k in clean for k in step_keywords):
            step = min(step + 1, len(step_keywords))
            progress.progress(int(step / len(step_keywords) * 100))
        log_box.code("\n".join(lines[-45:]), language="bash")

    return_code = process.wait()
    progress.progress(100 if return_code == 0 else 0)
    return return_code, "\n".join(lines)


def get_nested(d: dict, *keys, default="N/A"):
    cur = d
    for key in keys:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


with st.sidebar:
    st.header("Input")
    input_mode = st.radio("Source", ["Upload video", "Generate test video"], index=0)

    uploaded_file = None
    generator = "testsrc"
    if input_mode == "Upload video":
        uploaded_file = st.file_uploader("Choose an input video", type=["mp4", "mkv", "mov", "avi", "webm"])
    else:
        generator = st.selectbox("FFmpeg generator", ["testsrc", "smptebars", "mandelbrot", "color"])

    st.header("Encoding / Analysis Settings")
    frames = st.slider("Frames to encode/analyze", min_value=1, max_value=180, value=10, step=1)
    qp = st.slider("QP", min_value=18, max_value=45, value=30, step=1)
    duration = st.slider("Generated duration (seconds)", min_value=1, max_value=10, value=3, step=1)

    st.header("System Check")
    problems = check_required_files()
    if problems:
        for p in problems:
            st.error(p)
    else:
        st.success("Required pipeline files found")

    analyze = st.button("▶ Analyze", type="primary", use_container_width=True, disabled=bool(problems))

st.markdown("### Live Demo Flow")
st.write(
    "User input → modified SVT-AV1/VVenC encoding → real CSV validation → "
    "64×64 grid normalization → near real-time visualization."
)

st.info(
    "The raw number of AV1 and VVC blocks/PUs may differ because each codec partitions frames differently. "
    "For fair spatial comparison, the analyzer normalizes both outputs to fixed 64×64 grid cells."
)

if analyze:
    command = [
        "bash",
        str(PIPELINE),
        "--width", str(DEFAULT_WIDTH),
        "--height", str(DEFAULT_HEIGHT),
        "--fps", str(DEFAULT_FPS),
        "--frames", str(frames),
        "--qp", str(qp),
        "--output-dir", "results",
    ]

    if input_mode == "Upload video":
        if uploaded_file is None:
            st.error("Please upload a video first.")
            st.stop()
        UPLOAD_DIR.mkdir(exist_ok=True)
        upload_name = PureWindowsPath(Path(uploaded_file.name).name).name or "uploaded_video.mp4"
        input_path = UPLOAD_DIR / upload_name
        with input_path.open("wb") as f:
            f.write(uploaded_file.getbuffer())
        command += ["--input", str(input_path)]
    else:
        command += ["--generate", generator, "--duration", str(duration)]

    st.subheader("Processing log")
    start = time.time()
    code, _ = run_pipeline(command)
    elapsed = time.time() - start

    if code != 0:
        st.error(f"Pipeline failed after {elapsed:.1f}s. See log above.")
        st.stop()

    st.success(f"Analysis completed in {elapsed:.1f}s")

summary = read_summary()
if summary:
    st.markdown("## Output Dashboard")

    av1 = summary.get("av1", {})
    vvc = summary.get("vvc", {})
    av1_grid = av1.get("normalized_grid", {})
    vvc_grid = vvc.get("normalized_grid", {})

    av1_size = file_size_mb(RESULTS_DIR / "output_av1.ivf")
    vvc_size = file_size_mb(RESULTS_DIR / "output_vvc.266")
    gain = ((av1_size - vvc_size) / av1_size * 100) if av1_size > 0 and vvc_size > 0 else None

    av1_csv = PROJECT_DIR / "av1_stats.csv"
    vvc_csv = PROJECT_DIR / "vvc_intra_modes.csv"

    st.markdown("### Raw encoder output")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("AV1 raw units", av1.get("raw_unit_count", count_csv_rows(av1_csv) or "N/A"))
    c2.metric("VVC raw units", vvc.get("raw_unit_count", count_csv_rows(vvc_csv) or "N/A"))
    c3.metric("AV1 bitstream", f"{av1_size:.2f} MB" if av1_size else "N/A")
    c4.metric(
        "VVC bitstream",
        f"{vvc_size:.2f} MB" if vvc_size else "N/A",
        delta=(f"{gain:.1f}% vs AV1" if gain is not None else None),
    )

    st.markdown("### Normalized 64×64 grid comparison")
    g1, g2, g3, g4 = st.columns(4)
    g1.metric("AV1 grid cells", av1_grid.get("grid_cell_count", "N/A"))
    g2.metric("VVC grid cells", vvc_grid.get("grid_cell_count", "N/A"))

    st.markdown("### Real CSV Validation")
    v1, v2 = st.columns(2)
    v1.write(f"`av1_stats.csv`: {count_csv_rows(av1_csv):,} rows")
    v2.write(f"`vvc_intra_modes.csv`: {count_csv_rows(vvc_csv):,} rows")

    st.markdown("### Raw Mode Distribution")
    col1, col2 = st.columns(2)
    with col1:
        show_image(RESULTS_DIR / "av1" / "01_raw_mode_distribution.png", "AV1 Raw Mode Distribution")
    with col2:
        show_image(RESULTS_DIR / "vvc" / "01_raw_mode_distribution.png", "VVC Raw Mode Distribution")

    st.markdown("### Normalized 64×64 Grid Mode Distribution")
    col1, col2 = st.columns(2)
    with col1:
        show_image(RESULTS_DIR / "av1" / "02_normalized_grid_mode_distribution.png", "AV1 Normalized Grid Distribution")
    with col2:
        show_image(RESULTS_DIR / "vvc" / "02_normalized_grid_mode_distribution.png", "VVC Normalized Grid Distribution")

    st.markdown("### Spatial Dominant Share")
    col1, col2 = st.columns(2)
    with col1:
        show_image(RESULTS_DIR / "av1" / "03_spatial_dominant_share.png", "AV1 Dominant Mode Share")
    with col2:
        show_image(RESULTS_DIR / "vvc" / "03_spatial_dominant_share.png", "VVC Dominant Mode Share")

    st.markdown("### Spatial Dominant Mode ID")
    col1, col2 = st.columns(2)
    with col1:
        show_image(RESULTS_DIR / "av1" / "04_spatial_dominant_mode_id.png", "AV1 Dominant Mode ID")
    with col2:
        show_image(RESULTS_DIR / "vvc" / "04_spatial_dominant_mode_id.png", "VVC Dominant Mode ID")

    st.markdown("### Raw Block/PU Size Distribution")
    col1, col2 = st.columns(2)
    with col1:
        show_image(RESULTS_DIR / "av1" / "05_raw_blocksize_distribution.png", "AV1 Raw Size Distribution")
    with col2:
        show_image(RESULTS_DIR / "vvc" / "05_raw_blocksize_distribution.png", "VVC Raw Size Distribution")

    st.markdown("### Raw Frame Type Distribution")
    col1, col2 = st.columns(2)
    with col1:
        show_image(RESULTS_DIR / "av1" / "06_raw_frametype_distribution.png", "AV1 Frame Type Distribution")
    with col2:
        show_image(RESULTS_DIR / "vvc" / "06_raw_frametype_distribution.png", "VVC Frame Type Distribution")

    st.markdown("### Download Outputs")
    d1, d2, d3 = st.columns(3)
    report_path = RESULTS_DIR / "ANALYSIS_REPORT.txt"
    summary_path = RESULTS_DIR / "analysis_summary.json"

    if report_path.exists():
        d1.download_button("Download report", report_path.read_bytes(), file_name="ANALYSIS_REPORT.txt")
    if summary_path.exists():
        d2.download_button("Download summary JSON", summary_path.read_bytes(), file_name="analysis_summary.json")

    zip_path = make_outputs_zip()
    if zip_path.exists():
        d3.download_button("Download outputs ZIP", zip_path.read_bytes(), file_name="project2502m_outputs.zip")
else:
    st.info("Upload or generate a video, adjust QP/frames, then click Analyze to start the live demo.")
