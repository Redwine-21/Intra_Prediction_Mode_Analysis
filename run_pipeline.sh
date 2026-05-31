#!/usr/bin/env bash
################################################################################
# PROJECT 2502M - REAL ENCODER CSV PIPELINE
#
# Official flow:
#   input video
#     -> ffmpeg converts to YUV
#     -> modified SVT-AV1 writes av1_stats.csv by fprintf
#     -> modified VTM EncoderApp writes vvc_stats.csv by fprintf
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
VVC_STATS_FILE="vvc_stats.csv"
PYTHON_BIN="${PYTHON_BIN:-$(if [ -x ./.venv/bin/python3 ]; then echo ./.venv/bin/python3; else echo python3; fi)}"
VVC_BASE_CFG="encoder_intra_vtm.cfg"

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
  --vvc-stats PATH     VVC real stats CSV path. Default: vvc_stats.csv
  --vvc-base-cfg PATH   VTM base config. Default: encoder_intra_vtm.cfg
EOF
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
      --input) INPUT_VIDEO="$2"; shift 2 ;;
      --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
      --width) WIDTH="$2"; shift 2 ;;
      --height) HEIGHT="$2"; shift 2 ;;
      --fps) FPS="$2"; shift 2 ;;
      --frames) FRAMES="$2"; shift 2 ;;
      --duration) DURATION="$2"; shift 2 ;;
      --qp) QP="$2"; shift 2 ;;
      --generate) GENERATE="$2"; shift 2 ;;
      --av1-stats) AV1_STATS_FILE="$2"; shift 2 ;;
      --vvc-stats) VVC_STATS_FILE="$2"; shift 2 ;;
      --vvc-base-cfg) VVC_BASE_CFG="$2"; shift 2 ;;
      -h|--help) usage; exit 0 ;;
      *) print_error "Unknown argument: $1"; usage; exit 2 ;;
    esac
  done
fi

mkdir -p "$OUTPUT_DIR" "$OUTPUT_DIR/av1" "$OUTPUT_DIR/vvc"
LOG_FILE="$OUTPUT_DIR/pipeline.log"
: > "$LOG_FILE"

export LD_LIBRARY_PATH="$PWD:${LD_LIBRARY_PATH:-}"

log_cmd(){
  print_info "$*"
  "$@" 2>&1 | tee -a "$LOG_FILE"
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
  rm -f "$OUTPUT_DIR/output_av1.ivf" "$OUTPUT_DIR/output_vvc.bin" "$OUTPUT_DIR/recon_vvc.yuv"
  rm -f "$OUTPUT_DIR/analysis_summary.json" "$OUTPUT_DIR/ANALYSIS_REPORT.txt" "$OUTPUT_DIR/vvc_runtime.cfg"
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

write_vvc_runtime_cfg(){
  local cfg_root="vvc_runtime.cfg"
  local cfg_out="$OUTPUT_DIR/vvc_runtime.cfg"

  cat > "$cfg_root" <<EOF
# Runtime input/output generated by run_pipeline.sh
InputFile                    : $YUV_INPUT
BitstreamFile                : $OUTPUT_DIR/output_vvc.bin
ReconFile                    : $OUTPUT_DIR/recon_vvc.yuv

# Video format
FrameRate                    : $FPS
FrameSkip                    : 0
SourceWidth                  : $WIDTH
SourceHeight                 : $HEIGHT
InputBitDepth                : 8
InternalBitDepth             : 8
InputChromaFormat            : 420
FramesToBeEncoded            : $FRAMES

# Rate control / all-intra
QP                           : $QP
GOPSize                      : 1
IntraPeriod                  : 1
DecodingRefreshType          : 1

# Safe defaults
ConformanceWindowMode        : 1
EOF

  cp "$cfg_root" "$cfg_out"
  print_success "VVC runtime config written: $cfg_root"
}

encode_vvc(){
  print_header "STEP 3: ENCODING WITH VVC (VTM)"

  local bin=""
  bin=$(resolve_binary EncoderApp || resolve_binary VTMEncoderApp || true)
  [[ -n "$bin" ]] || { print_error "EncoderApp/VTMEncoderApp not found"; exit 1; }

  rm -f "$VVC_STATS_FILE"
  write_vvc_runtime_cfg

  if [[ -f "$VVC_BASE_CFG" ]]; then
    log_cmd "$bin" -c "$VVC_BASE_CFG" -c vvc_runtime.cfg
  else
    print_info "Base VTM config not found: $VVC_BASE_CFG. Running with runtime config only."
    log_cmd "$bin" -c vvc_runtime.cfg
  fi

  [[ -s "$OUTPUT_DIR/output_vvc.bin" ]] || { print_error "VVC bitstream was not created"; exit 1; }
  [[ -s "$VVC_STATS_FILE" ]] || { print_error "Missing VVC real CSV: $VVC_STATS_FILE. Check fprintf in VTM source and run the rebuilt EncoderApp."; exit 1; }

  print_success "VVC bitstream: $OUTPUT_DIR/output_vvc.bin"
  print_success "VVC real CSV: $VVC_STATS_FILE"
}

validate_csv(){
  print_header "STEP 4: VALIDATING REAL ENCODER CSV"
  "$PYTHON_BIN" - "$AV1_STATS_FILE" "$VVC_STATS_FILE" <<'PY'
import csv
import sys
from pathlib import Path

for path in sys.argv[1:]:
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
- $VVC_STATS_FILE    (must be dumped by modified VTM source)
- $OUTPUT_DIR/output_av1.ivf
- $OUTPUT_DIR/output_vvc.bin
- $OUTPUT_DIR/analysis_summary.json
- $OUTPUT_DIR/ANALYSIS_REPORT.txt
- $OUTPUT_DIR/av1/*.png
- $OUTPUT_DIR/vvc/*.png

Important:
This pipeline does not create fallback/synthetic/content-derived CSV files.
If either CSV is missing, the pipeline fails.
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
