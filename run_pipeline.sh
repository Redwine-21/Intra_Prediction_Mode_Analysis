#!/usr/bin/env bash
################################################################################
# PROJECT 2502M - REAL ENCODER CSV PIPELINE
#
# Official flow:
#   input video
#     -> ffmpeg converts to YUV
#     -> modified SVT-AV1 writes av1_stats.csv by fprintf
#     -> modified VVenC vvencapp writes vvc_intra_modes.csv
#     -> intra_prediction_analysis.py reads both CSV files and generates plots/report
#
# This script DOES NOT create synthetic/content-derived CSV files.
################################################################################

set -Eeuo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_header(){ echo -e "${BLUE}================================${NC}"; echo -e "${BLUE}$1${NC}"; echo -e "${BLUE}================================${NC}"; }
print_success(){ echo -e "${GREEN}[✓]${NC} $1"; }
print_error(){ echo -e "${RED}[✗]${NC} $1"; }
print_info(){ echo -e "${YELLOW}[i]${NC} $1"; }

INPUT_VIDEO="input.mp4"
OUTPUT_DIR="results"
WIDTH=""
HEIGHT=""
FPS=30
FRAMES=3
QP=30
DURATION=3
GENERATE=""
AV1_STATS_FILE="av1_stats.csv"
VVC_STATS_FILE="vvc_intra_modes.csv"
PYTHON_BIN="${PYTHON_BIN:-$(if [ -x ./.venv/bin/python3 ]; then echo ./.venv/bin/python3; else echo python3; fi)}"

usage(){
  cat <<EOF
PROJECT 2502M - REAL ENCODER CSV PIPELINE

Options:
  --input PATH         Input video or raw YUV file. Default: input.mp4
  --output-dir DIR     Output directory. Default: results
  --width N            Width for generated/converted/raw YUV input
  --height N           Height for generated/converted/raw YUV input
  --fps N              FPS. Default: 30
  --frames N           Frames to encode/analyze. Default: 3
  --duration SEC       Duration for generated input. Default: 3
  --qp N               Encoder QP. Default: 30
  --generate NAME      Generate input with ffmpeg lavfi: testsrc, smptebars, color, mandelbrot
  --av1-stats PATH     AV1 real stats CSV path. Default: av1_stats.csv
  --vvc-stats PATH     VVC real stats CSV path. Default: vvc_intra_modes.csv
EOF
}

require_option_value(){
  local opt="$1"
  local value="${2:-}"
  if [[ -z "$value" || "$value" == --* ]]; then
    print_error "Missing value for $opt"
    usage
    exit 2
  fi
}

if [[ $# -gt 0 && "${1:-}" != --* ]]; then
  INPUT_VIDEO="${1:-$INPUT_VIDEO}"
  OUTPUT_DIR="${2:-$OUTPUT_DIR}"
  WIDTH="${3:-$WIDTH}"
  HEIGHT="${4:-$HEIGHT}"
  FPS="${5:-$FPS}"
  FRAMES="${6:-$FRAMES}"
  QP="${7:-$QP}"
else
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --input) require_option_value "$1" "${2:-}"; INPUT_VIDEO="$2"; shift 2 ;;
      --output-dir) require_option_value "$1" "${2:-}"; OUTPUT_DIR="$2"; shift 2 ;;
      --width) require_option_value "$1" "${2:-}"; WIDTH="$2"; shift 2 ;;
      --height) require_option_value "$1" "${2:-}"; HEIGHT="$2"; shift 2 ;;
      --fps) require_option_value "$1" "${2:-}"; FPS="$2"; shift 2 ;;
      --frames) require_option_value "$1" "${2:-}"; FRAMES="$2"; shift 2 ;;
      --duration) require_option_value "$1" "${2:-}"; DURATION="$2"; shift 2 ;;
      --qp) require_option_value "$1" "${2:-}"; QP="$2"; shift 2 ;;
      --generate) require_option_value "$1" "${2:-}"; GENERATE="$2"; shift 2 ;;
      --av1-stats) require_option_value "$1" "${2:-}"; AV1_STATS_FILE="$2"; shift 2 ;;
      --vvc-stats) require_option_value "$1" "${2:-}"; VVC_STATS_FILE="$2"; shift 2 ;;
      -h|--help) usage; exit 0 ;;
      *) print_error "Unknown argument: $1"; usage; exit 2 ;;
    esac
  done
fi

mkdir -p "$OUTPUT_DIR" "$OUTPUT_DIR/av1" "$OUTPUT_DIR/vvc"
if [[ -z "${MPLCONFIGDIR:-}" ]]; then
  export MPLCONFIGDIR="$OUTPUT_DIR/.matplotlib"
  mkdir -p "$MPLCONFIGDIR"
fi
LOG_FILE="$OUTPUT_DIR/pipeline.log"
: > "$LOG_FILE"

export LD_LIBRARY_PATH="$PWD:${LD_LIBRARY_PATH:-}"

log_cmd(){
  print_info "$*"
  "$@" 2>&1 | tee -a "$LOG_FILE"
}

log_cmd_in_dir(){
  local dir="$1"
  shift
  print_info "(cd $dir && $*)"
  (cd "$dir" && "$@") 2>&1 | tee -a "$LOG_FILE"
}

abs_path(){
  local path="$1"
  if [[ "$path" == /* ]]; then
    printf '%s\n' "$path"
  else
    printf '%s/%s\n' "$PWD" "$path"
  fi
}

resolve_binary(){
  local name="$1"
  if [[ -x "./$name" ]]; then
    echo "./$name"
    return 0
  fi
  command -v "$name" 2>/dev/null || return 1
}

require_cmd(){
  command -v "$1" >/dev/null 2>&1 || { print_error "$1 not found"; exit 1; }
  print_success "$1 found"
}

clean_old_outputs(){
  print_header "STEP 0: CLEANING OLD OUTPUTS"

  rm -f "$AV1_STATS_FILE" "$VVC_STATS_FILE"
  rm -f analysis_summary.json
  rm -f vvc_runtime.cfg

  rm -f "$OUTPUT_DIR/output_av1.ivf"
  rm -f "$OUTPUT_DIR/output_vvc.266"
  rm -f "$OUTPUT_DIR/output_vvc.bin"
  rm -f "$OUTPUT_DIR/output_vvc.vvc"
  rm -f "$OUTPUT_DIR/output_vvc.bit"
  rm -f "$OUTPUT_DIR/recon_vvc.yuv"

  rm -f "$OUTPUT_DIR/analysis_summary.json"
  rm -f "$OUTPUT_DIR/ANALYSIS_REPORT.txt"
  rm -f "$OUTPUT_DIR/PIPELINE_SUMMARY.txt"
  rm -f "$OUTPUT_DIR/vvc_runtime.cfg"
  rm -f "$OUTPUT_DIR/pipeline.log"

  rm -f "$OUTPUT_DIR/converted_input_"*.yuv
  rm -f "$OUTPUT_DIR/generated_"*.mp4
  rm -rf "$OUTPUT_DIR/vvc_per_frame_csv"

  rm -rf "$OUTPUT_DIR/av1" "$OUTPUT_DIR/vvc"
  mkdir -p "$OUTPUT_DIR/av1" "$OUTPUT_DIR/vvc"

  print_success "Old CSV/results removed"
}

probe_dimensions(){
  local path="$1"
  if [[ -z "$WIDTH" || -z "$HEIGHT" ]]; then
    if [[ "$path" != *.yuv ]] && command -v ffprobe >/dev/null 2>&1; then
      local dims
      dims=$(ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=s=x:p=0 "$path" 2>/dev/null || true)
      if [[ "$dims" =~ ^[0-9]+x[0-9]+$ ]]; then
        WIDTH="${dims%x*}"
        HEIGHT="${dims#*x}"
      fi
    fi
  fi
  WIDTH="${WIDTH:-1920}"
  HEIGHT="${HEIGHT:-1080}"
}

create_input(){
  print_header "STEP 1: PREPARING INPUT"

  if [[ -n "$GENERATE" ]]; then
    WIDTH="${WIDTH:-1280}"
    HEIGHT="${HEIGHT:-720}"
    INPUT_VIDEO="$OUTPUT_DIR/generated_${GENERATE}_${WIDTH}x${HEIGHT}_${DURATION}s.mp4"

    local source
    case "$GENERATE" in
      testsrc) source="testsrc=size=${WIDTH}x${HEIGHT}:rate=${FPS}:duration=${DURATION}" ;;
      smptebars) source="smptebars=size=${WIDTH}x${HEIGHT}:rate=${FPS}:duration=${DURATION}" ;;
      mandelbrot) source="mandelbrot=size=${WIDTH}x${HEIGHT}:rate=${FPS}" ;;
      color) source="color=c=blue:size=${WIDTH}x${HEIGHT}:rate=${FPS}:duration=${DURATION}" ;;
      *) print_error "Unsupported generator: $GENERATE"; exit 2 ;;
    esac

    log_cmd ffmpeg -hide_banner -y -f lavfi -i "$source" -t "$DURATION" -pix_fmt yuv420p "$INPUT_VIDEO"
    print_success "Generated input: $INPUT_VIDEO"
  fi

  [[ -f "$INPUT_VIDEO" ]] || { print_error "Input video not found: $INPUT_VIDEO"; exit 1; }
  probe_dimensions "$INPUT_VIDEO"

  if [[ "$INPUT_VIDEO" == *.yuv ]]; then
    YUV_INPUT="$INPUT_VIDEO"
  else
    YUV_INPUT="$OUTPUT_DIR/converted_input_${WIDTH}x${HEIGHT}.yuv"
    log_cmd ffmpeg -hide_banner -y -i "$INPUT_VIDEO" -vf "scale=${WIDTH}:${HEIGHT}" -pix_fmt yuv420p -frames:v "$FRAMES" "$YUV_INPUT"
  fi

  [[ -s "$YUV_INPUT" ]] || { print_error "YUV input was not created: $YUV_INPUT"; exit 1; }
  print_success "Raw YUV input: $YUV_INPUT (${WIDTH}x${HEIGHT}, fps=$FPS, frames=$FRAMES)"
}

encode_av1(){
  print_header "STEP 2: ENCODING WITH SVT-AV1"

  local bin=""
  bin=$(resolve_binary SvtAv1EncApp || true)
  [[ -n "$bin" ]] || { print_error "SvtAv1EncApp not found"; exit 1; }

  rm -f "$AV1_STATS_FILE"

  log_cmd "$bin" \
    -i "$YUV_INPUT" \
    -w "$WIDTH" \
    -h "$HEIGHT" \
    --fps-num "$FPS" \
    --fps-denom 1 \
    --frames "$FRAMES" \
    -b "$OUTPUT_DIR/output_av1.ivf" \
    --preset 8 \
    --rc 0 \
    --qp "$QP" \
    --keyint 1 \
    --irefresh-type 2 \
    --pred-struct 1 \
    --enable-intrabc 0

  [[ -s "$OUTPUT_DIR/output_av1.ivf" ]] || { print_error "AV1 bitstream was not created"; exit 1; }
  [[ -s "$AV1_STATS_FILE" ]] || { print_error "Missing AV1 real CSV: $AV1_STATS_FILE. Check fprintf in SVT-AV1 source and run the rebuilt SvtAv1EncApp."; exit 1; }

  print_success "AV1 bitstream: $OUTPUT_DIR/output_av1.ivf"
  print_success "AV1 real CSV: $AV1_STATS_FILE"
}

csv_frame_count(){
  "$PYTHON_BIN" - "$1" <<'PY'
import csv
import sys
from pathlib import Path

path = Path(sys.argv[1])
frames = set()
if path.exists():
    with path.open(newline="", encoding="utf-8", errors="replace") as fh:
        for row in csv.reader(fh):
            if not row or row[0].strip().startswith("#"):
                continue
            if row[0].strip().lower() in {"frame_num", "frame", "poc"}:
                continue
            if len(row) < 7:
                continue
            try:
                frames.add(int(row[0]))
            except ValueError:
                continue
print(len(frames))
PY
}

append_remapped_vvc_csv(){
  local src_csv="$1"
  local frame_num="$2"
  local dst_csv="$3"

  "$PYTHON_BIN" - "$src_csv" "$frame_num" "$dst_csv" <<'PY'
import csv
import sys
from pathlib import Path

src = Path(sys.argv[1])
frame_num = sys.argv[2]
dst = Path(sys.argv[3])

if not src.exists() or src.stat().st_size == 0:
    raise SystemExit(f"missing per-frame VVC CSV: {src}")

rows = 0
with src.open(newline="", encoding="utf-8", errors="replace") as in_fh, dst.open(
    "a", newline="", encoding="utf-8"
) as out_fh:
    reader = csv.reader(in_fh)
    writer = csv.writer(out_fh, lineterminator="\n")
    for row in reader:
        if not row or row[0].strip().startswith("#"):
            continue
        if row[0].strip().lower() in {"frame_num", "frame", "poc"}:
            continue
        if len(row) < 7:
            continue
        row[0] = frame_num
        writer.writerow(row)
        rows += 1

if rows == 0:
    raise SystemExit(f"no valid rows in per-frame VVC CSV: {src}")
PY
}

rebuild_vvc_csv_per_frame(){
  local bin="$1"
  local bin_abs
  local yuv_abs
  local output_abs
  local fallback_dir
  local merged_csv
  local frame

  bin_abs="$(abs_path "$bin")"
  yuv_abs="$(abs_path "$YUV_INPUT")"
  output_abs="$(abs_path "$OUTPUT_DIR")"
  fallback_dir="$output_abs/vvc_per_frame_csv"
  merged_csv="$fallback_dir/merged_vvc_intra_modes.csv"

  rm -rf "$fallback_dir"
  mkdir -p "$fallback_dir"
  : > "$merged_csv"

  print_info "Rebuilding VVC CSV with one real VVenC encode per frame. This is slower, but avoids missing-frame CSV dumps."

  for ((frame = 0; frame < FRAMES; frame++)); do
    local frame_dir="$fallback_dir/frame_${frame}"
    mkdir -p "$frame_dir"

    log_cmd_in_dir "$frame_dir" "$bin_abs" \
      -i "$yuv_abs" \
      -s "${WIDTH}x${HEIGHT}" \
      -r "$FPS" \
      --preset medium \
      -q "$QP" \
      -f 1 \
      -fs "$frame" \
      --intraperiod 1 \
      --refreshtype idr \
      --threads 1 \
      --mtprofile 0 \
      --ifp 0 \
      -o "$frame_dir/output_vvc_${frame}.266"

    append_remapped_vvc_csv "$frame_dir/vvc_intra_modes.csv" "$frame" "$merged_csv"
    print_success "VVC per-frame CSV appended for frame $frame"
  done

  cp "$merged_csv" "$VVC_STATS_FILE"
}

ensure_vvc_csv_frame_count(){
  local bin="$1"
  local actual_frames

  actual_frames="$(csv_frame_count "$VVC_STATS_FILE")"
  if [[ "$actual_frames" == "$FRAMES" ]]; then
    print_success "VVC CSV frame count matches requested frames: $actual_frames/$FRAMES"
    return 0
  fi

  print_info "VVC CSV contains $actual_frames frame(s), expected $FRAMES. Running per-frame VVC CSV fallback."
  rebuild_vvc_csv_per_frame "$bin"

  actual_frames="$(csv_frame_count "$VVC_STATS_FILE")"
  [[ "$actual_frames" == "$FRAMES" ]] || {
    print_error "VVC CSV still has $actual_frames frame(s), expected $FRAMES after per-frame fallback"
    exit 1
  }
  print_success "VVC CSV rebuilt with complete frame count: $actual_frames/$FRAMES"
}

encode_vvc(){
  print_header "STEP 3: ENCODING WITH VVC (VVenC)"

  local bin=""
  bin=$(resolve_binary vvencapp || true)
  [[ -n "$bin" ]] || { print_error "vvencapp not found"; exit 1; }

  rm -f "$VVC_STATS_FILE"

  log_cmd "$bin" \
    -i "$YUV_INPUT" \
    -s "${WIDTH}x${HEIGHT}" \
    -r "$FPS" \
    --preset medium \
    -q "$QP" \
    -f "$FRAMES" \
    --intraperiod 1 \
    --refreshtype idr \
    -o "$OUTPUT_DIR/output_vvc.266"

  [[ -s "$OUTPUT_DIR/output_vvc.266" ]] || { print_error "VVC bitstream was not created"; exit 1; }
  [[ -s "$VVC_STATS_FILE" ]] || { print_error "Missing VVC real CSV: $VVC_STATS_FILE. Check fprintf in VVenC source and run the rebuilt vvencapp."; exit 1; }
  ensure_vvc_csv_frame_count "$bin"

  print_success "VVC bitstream: $OUTPUT_DIR/output_vvc.266"
  print_success "VVC real CSV: $VVC_STATS_FILE"
}

validate_csv(){
  print_header "STEP 4: VALIDATING REAL ENCODER CSV"
  "$PYTHON_BIN" - "$FRAMES" "$AV1_STATS_FILE" "$VVC_STATS_FILE" <<'PY'
import csv
import sys
from pathlib import Path

expected_frames = int(sys.argv[1])

for path in sys.argv[2:]:
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        raise SystemExit(f"{path}: missing or empty")

    ok = 0
    frames = set()
    modes = set()
    with p.open(newline="", encoding="utf-8", errors="replace") as fh:
        for row in csv.reader(fh):
            if not row or row[0].strip().startswith("#"):
                continue
            if row[0].strip().lower() in {"frame_num", "frame", "poc"}:
                continue
            if len(row) < 7:
                continue
            frame = int(row[0])
            frame_type = row[1].strip().upper()
            x = int(row[2]); y = int(row[3]); w = int(row[4]); h = int(row[5]); mode = int(row[6])
            if frame_type not in {"I", "P", "B"}:
                raise SystemExit(f"{path}: invalid frame type: {frame_type}")
            if w <= 0 or h <= 0:
                raise SystemExit(f"{path}: invalid block size: {w}x{h}")
            frames.add(frame)
            modes.add(mode)
            ok += 1

    if ok == 0:
        raise SystemExit(f"{path}: no valid 7-column rows")
    if len(frames) != expected_frames:
        frame_list = ",".join(str(v) for v in sorted(frames))
        raise SystemExit(
            f"{path}: expected {expected_frames} frame(s), found {len(frames)} "
            f"frame(s): [{frame_list}]"
        )
    print(f"{path}: OK ({ok} rows, {len(frames)} frames, {len(modes)} modes)")
PY
}

run_analysis(){
  print_header "STEP 5: RUNNING ANALYSIS AND VISUALIZATIONS"
  local analysis_script="./intra_prediction_analysis.py"
  [[ -f "$analysis_script" ]] || { print_error "Cannot find intra_prediction_analysis.py"; exit 1; }

  log_cmd "$PYTHON_BIN" "$analysis_script" \
    --av1-stats "$AV1_STATS_FILE" \
    --vvc-stats "$VVC_STATS_FILE" \
    --num-frames "$FRAMES" \
    --output-dir "$OUTPUT_DIR"
}

generate_report_note(){
  print_header "STEP 6: REPORT"
  local report="$OUTPUT_DIR/PIPELINE_SUMMARY.txt"
  cat > "$report" <<EOF
PROJECT 2502M - REAL ENCODER CSV PIPELINE SUMMARY
Date: $(date)
Input: $INPUT_VIDEO
Raw YUV used: $YUV_INPUT
Resolution: ${WIDTH}x${HEIGHT}
FPS: $FPS
Frames encoded/analyzed: $FRAMES
QP: $QP

Outputs:
- $AV1_STATS_FILE    (must be dumped by modified SVT-AV1 source)
- $VVC_STATS_FILE    (must be dumped by modified VVenC source)
- $OUTPUT_DIR/output_av1.ivf
- $OUTPUT_DIR/output_vvc.266
- $OUTPUT_DIR/analysis_summary.json
- $OUTPUT_DIR/ANALYSIS_REPORT.txt
- $OUTPUT_DIR/av1/*.png
- $OUTPUT_DIR/vvc/*.png

Important:
This pipeline does not create synthetic/content-derived CSV files.
If VVenC dumps too few frames in a multi-frame run, the pipeline rebuilds the
VVC CSV by running real one-frame VVenC encodes and remapping frame indices.
If either real encoder CSV is missing or incomplete after that, the pipeline fails.
EOF
  cat "$report"
  print_success "Pipeline summary written: $report"
}

main(){
  print_header "PROJECT 2502M - REAL INTRA PREDICTION MODE PIPELINE"
  require_cmd ffmpeg
  command -v ffprobe >/dev/null 2>&1 && print_success "ffprobe found" || print_error "ffprobe not found; using default dimensions when needed"

  clean_old_outputs
  create_input
  encode_av1
  encode_vvc
  validate_csv
  run_analysis
  generate_report_note

  print_header "SUMMARY"
  print_success "Pipeline completed successfully. See: $OUTPUT_DIR"
}

main "$@"
