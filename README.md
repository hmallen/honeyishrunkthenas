# honeyishrunkthenas

Transcode large video files in a directory tree to H.265/H.264 with optional GPU acceleration. Includes dry-run estimation, CSV/JSON reporting, and a safeguard to keep originals without overwriting.

## Requirements

- Python 3.8+
- ffmpeg and ffprobe available on PATH

## Quick start

Dry run with CSV report:

```powershell
python transcode_large_videos.py "X:\path\to\videos" --dry-run --report-format csv --report-file "X:\path\to\report.csv"
```

Transcode one file, keep original, write CSV, show live ffmpeg progress:

```powershell
python transcode_large_videos.py "X:\path\to\videos" --max-transcodes 1 --keep-original --report-format csv --report-file "X:\path\to\report.csv" --verbose
```

## Command reference

Unless noted, values are case-insensitive and units support K/M/G (e.g., `160k`, `2.5M`, `3gbps`).

- **path** (positional)
  - Root directory to scan recursively.

- **--min-size SIZE** (default: `1GB`)
  - Skip files smaller than this size.
  - Examples: `500MB`, `1.5GB`, `2000MB`, `800K`, `123456B`.

- **--min-bitrate RATE** (default: unset)
  - Skip files whose bitrate is below this threshold.
  - Examples: `3Mbps`, `3000k`, `2.5M`.

- **--bitrate-only**
  - Requires `--min-bitrate`. If duration can’t be probed, skip the file rather than falling back to size threshold.

- **--extensions CSV** (default: `mp4,mkv,avi,mov,m4v,mpg,mpeg,ts,m2ts,webm,wmv,flv`)
  - File extensions to include.

- **--codec {libx265,libx264}** (default: `libx265`)
  - Video codec (CPU encoders unless a GPU backend is selected).

- **--crf INT** (default: `28` for x265, `23` for x264)
  - Quality factor: lower = higher quality/larger outputs.
  - Typical ranges: x265 `22–30`; x264 `18–26`.

- **--preset NAME** (default: `medium`)
  - Speed/efficiency tradeoff.
  - Typical values: `ultrafast, superfast, veryfast, faster, fast, medium, slow, slower, veryslow`.

- **--audio-codec NAME** (default: `aac`)
  - Audio codec to encode with. Use `copy` to stream-copy original audio.

- **--audio-bitrate RATE** (default: `160k`)
  - Target audio bitrate for encoding and for size estimation.

- **--container {mkv,mp4}** (default: `mkv`)
  - Output container. With `mp4` + HEVC, the script sets `-tag:v hvc1` and `-movflags +faststart`.

- **--gpu {none,auto,nvenc,qsv,amf}** (default: `auto`)
  - Hardware encoder selection.
  - `auto` picks the first available of NVENC (NVIDIA), QSV (Intel), AMF (AMD). Falls back to CPU if none found.

- **--min-saving PERCENT** (default: `5.0`)
  - If the transcoded output is not at least this much smaller, the original is kept and the transcode is discarded.

- **--max-transcodes INT** (default: unset)
  - Stop after this many successful transcodes.

- **--dry-run**
  - Do not transcode; print what would happen. Bitrate/duration are still probed for estimates.

- **--no-delete** / **--keep-original**
  - Preserve the original file. When the computed output path would collide with the input (e.g., same extension), a unique `*.transcoded.*` filename is used so the original is never overwritten.

- **--print-bitrate**
  - Print per-file bitrate information without performing a transcode.

- **--verbose**
  - Prints per-file info (duration, bitrate, estimated sizes), the ffmpeg command, chosen target path, and streams real-time ffmpeg progress to the console (`-loglevel info -stats`).

- **--report-format {text,csv,json}** (default: `text`)
  - Choose reporting format.

- **--report-file PATH** (default: unset)
  - Write the report to a file. If omitted, prints to stdout. For `--report-format csv` without `--report-file`, the CSV is written to stdout; in dry-run a summary line is appended to stdout.

- **--est-mode {ratio,target}** (default: `ratio`)
  - Estimation mode for dry-run/reporting (does not change actual encoder settings).

- **--est-ratio FLOAT** (default: `0.6` for x265, `0.85` for x264)
  - Ratio of current bitrate used to estimate output size in ratio mode.

- **--est-target-v-bitrate RATE** (default: unset)
  - Video bitrate to target in `target` mode; audio bitrate is added on top for total size estimate.

## CSV / JSON report schema

For CSV, the header includes:

```csv
path,size_bytes,duration_sec,bitrate_bps,action,reason,time_sec,saved_bytes,output_path,output_size_bytes,estimated_output_size_bytes,estimated_saved_bytes,estimated_total_saved_bytes,would_transcode
```

Notes:

- `action` is one of `processed`, `skipped`, `failed`, `dry_run`.
- In dry-run, estimated fields are populated when available; in real runs, `saved_bytes`/`output_*` are populated.
- JSON output contains an array of per-file record objects with the same keys.

## Examples

- Dry run, CSV to file:

```powershell
python transcode_large_videos.py "X:\media" --dry-run --report-format csv --report-file "X:\tmp\report.csv"
```

- Process one file, keep original, verbose with live progress, CSV to file:

```powershell
python transcode_large_videos.py "X:\media" --max-transcodes 1 --keep-original --report-format csv --report-file "X:\tmp\report.csv" --verbose
```

- Use MP4 container with x265 and NVIDIA NVENC:

```powershell
python transcode_large_videos.py "X:\media" --container mp4 --codec libx265 --gpu nvenc
```

- Only consider high-bitrate sources and require 10% savings:

```powershell
python transcode_large_videos.py "X:\media" --min-bitrate 3Mbps --min-saving 10
```

## Implementation notes

- ffmpeg command-lines are constructed to map all streams, transcode video, encode audio, and copy subtitles. A simple `scale=iw:ih` is applied to normalize scaling behavior.
- When `--container mp4` with HEVC, `-tag:v hvc1` and `-movflags +faststart` are added for compatibility.
- `--verbose` switches to real-time ffmpeg output using `subprocess.Popen` and `-loglevel info -stats`.