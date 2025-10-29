import argparse
import os
import re
import shutil
import subprocess
import sys
import time
import json
import csv
from pathlib import Path


def _parse_size(s: str) -> int:
    u = {
        "B": 1,
        "KB": 1024,
        "K": 1024,
        "MB": 1024 ** 2,
        "M": 1024 ** 2,
        "GB": 1024 ** 3,
        "G": 1024 ** 3,
        "TB": 1024 ** 4,
        "T": 1024 ** 4,
    }
    m = re.match(r"^(\d+(?:\.\d+)?)([KMGTP]?B?)$", s.strip(), re.IGNORECASE)
    if not m:
        raise ValueError("invalid size")
    v = float(m.group(1))
    suf = m.group(2).upper()
    if suf == "":
        suf = "B"
    if suf in ("K", "M", "G", "T"):
        suf += "B"
    return int(v * u[suf])


def _human(n: int) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024 or unit == "TB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}{unit}"
        n /= 1024
    return f"{n}B"


def _human_bitrate(bps: float) -> str:
    units = [(1e9, "Gbps"), (1e6, "Mbps"), (1e3, "kbps")]
    for factor, name in units:
        if bps >= factor:
            return f"{bps / factor:.2f} {name}"
    return f"{bps:.0f} bps"


def _parse_bitrate(s: str) -> int:
    s = s.strip().lower().replace(" ", "")
    m = re.match(r"^(\d+(?:\.\d+)?)([kmg]?b?p?s?)$", s)
    if not m:
        raise ValueError("invalid bitrate")
    v = float(m.group(1))
    suf = m.group(2)
    if suf in ("k", "kb", "kbps"):
        v *= 1_000
    elif suf in ("m", "mb", "mbps"):
        v *= 1_000_000
    elif suf in ("g", "gb", "gbps"):
        v *= 1_000_000_000
    return int(v)


def _ffprobe_duration(p: Path) -> float:
    try:
        proc = subprocess.run([
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(p),
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        out = (proc.stdout or "").strip()
        if not out:
            return 0.0
        return float(out)
    except Exception:
        return 0.0


def _exts_list(s: str) -> set:
    parts = [p.strip().lower().lstrip(".") for p in s.split(",") if p.strip()]
    return set(parts)


def _which_ffmpeg() -> str:
    p = shutil.which("ffmpeg")
    if not p:
        print("ffmpeg not found in PATH", file=sys.stderr)
        sys.exit(1)
    return p


def _unique_path(p: Path) -> Path:
    if not p.exists():
        return p
    i = 1
    while True:
        cand = p.with_name(f"{p.stem}.{i}{p.suffix}")
        if not cand.exists():
            return cand
        i += 1


_ENCODERS_CACHE = None


def _ffmpeg_encoders():
    global _ENCODERS_CACHE
    if _ENCODERS_CACHE is not None:
        return _ENCODERS_CACHE
    names = set()
    try:
        proc = subprocess.run([
            "ffmpeg",
            "-hide_banner",
            "-encoders",
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        out = proc.stdout or ""
        for line in out.splitlines():
            line = line.strip()
            if not line or line.startswith("Encoders:") or line.startswith("------"):
                continue
            parts = line.split()
            if len(parts) >= 2:
                names.add(parts[1])
    except Exception:
        pass
    _ENCODERS_CACHE = names
    return names


def _choose_gpu_backend(cpu_codec: str, gpu: str):
    if not gpu or gpu == "none":
        return None
    hevc = cpu_codec == "libx265"
    if gpu == "auto":
        encs = _ffmpeg_encoders()
        for backend in ("nvenc", "qsv", "amf"):
            name = ("hevc_" if hevc else "h264_") + backend
            if name in encs:
                return backend
        return None
    return gpu


def _build_cmd(inp: Path, outp: Path, codec: str, crf: int, preset: str, a_codec: str, a_bitrate: str, container: str, gpu: str, verbose: bool) -> list:
    cmd = [
        "ffmpeg",
        "-hide_banner",
    ]
    if verbose:
        cmd += ["-loglevel", "info", "-stats"]
    else:
        cmd += ["-loglevel", "error"]
    cmd += [
        "-y",
        "-i",
        str(inp),
        "-map",
        "0",
    ]

    backend = _choose_gpu_backend(codec, gpu)
    hevc_selected = False
    if backend:
        if codec == "libx265":
            hevc_selected = True
            if backend == "nvenc":
                cmd += [
                    "-c:v",
                    "hevc_nvenc",
                    "-preset",
                    preset,
                    "-tune",
                    "hq",
                    "-rc",
                    "vbr",
                    "-cq",
                    str(crf),
                    "-b:v",
                    "0",
                    "-multipass",
                    "fullres",
                ]
            elif backend == "qsv":
                cmd += [
                    "-c:v",
                    "hevc_qsv",
                    "-preset",
                    preset,
                    "-rc",
                    "vbr",
                    "-global_quality",
                    str(crf),
                    "-look_ahead",
                    "1",
                ]
            else:
                amf_q = "balanced"
                pl = preset.lower()
                if pl in ("veryslow", "slow"):
                    amf_q = "quality"
                elif pl in ("veryfast", "faster", "fast", "ultrafast", "superfast"):
                    amf_q = "speed"
                cmd += [
                    "-c:v",
                    "hevc_amf",
                    "-quality",
                    amf_q,
                    "-rc",
                    "cqp",
                    "-qp_i",
                    str(crf),
                    "-qp_p",
                    str(crf),
                    "-qp_b",
                    str(crf),
                ]
        else:
            if backend == "nvenc":
                cmd += [
                    "-c:v",
                    "h264_nvenc",
                    "-preset",
                    preset,
                    "-tune",
                    "hq",
                    "-rc",
                    "vbr",
                    "-cq",
                    str(crf),
                    "-b:v",
                    "0",
                    "-multipass",
                    "fullres",
                ]
            elif backend == "qsv":
                cmd += [
                    "-c:v",
                    "h264_qsv",
                    "-preset",
                    preset,
                    "-rc",
                    "vbr",
                    "-global_quality",
                    str(crf),
                    "-look_ahead",
                    "1",
                ]
            else:
                amf_q = "balanced"
                pl = preset.lower()
                if pl in ("veryslow", "slow"):
                    amf_q = "quality"
                elif pl in ("veryfast", "faster", "fast", "ultrafast", "superfast"):
                    amf_q = "speed"
                cmd += [
                    "-c:v",
                    "h264_amf",
                    "-quality",
                    amf_q,
                    "-rc",
                    "cqp",
                    "-qp_i",
                    str(crf),
                    "-qp_p",
                    str(crf),
                    "-qp_b",
                    str(crf),
                ]
    else:
        hevc_selected = codec == "libx265"
        cmd += [
            "-c:v",
            codec,
            "-preset",
            preset,
            "-crf",
            str(crf),
        ]

    cmd += [
        "-vf",
        "scale=iw:ih",
    ]

    cmd += [
        "-c:a",
        a_codec,
        "-b:a",
        a_bitrate,
        "-c:s",
        "copy",
    ]
    if container == "mp4" and hevc_selected:
        cmd += ["-tag:v", "hvc1", "-movflags", "+faststart"]
    cmd += [str(outp)]
    return cmd


def main(): 
    parser = argparse.ArgumentParser()
    parser.add_argument("path", help="target directory to scan")
    parser.add_argument("--min-size", default="1GB")
    parser.add_argument("--min-bitrate", default=None)
    parser.add_argument("--codec", choices=["libx265", "libx264"], default="libx265")
    parser.add_argument("--crf", type=int, default=None)
    parser.add_argument("--preset", default="medium")
    parser.add_argument("--audio-codec", default="aac")
    parser.add_argument("--audio-bitrate", default="160k")
    parser.add_argument("--container", choices=["mkv", "mp4"], default="mkv")
    parser.add_argument("--gpu", choices=["none", "auto", "nvenc", "qsv", "amf"], default="auto")
    parser.add_argument("--extensions", default="mp4,mkv,avi,mov,m4v,mpg,mpeg,ts,m2ts,webm,wmv,flv")
    parser.add_argument("--min-saving", type=float, default=5.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-delete", action="store_true")
    parser.add_argument("--keep-original", dest="no_delete", action="store_true")
    parser.add_argument("--print-bitrate", action="store_true")
    parser.add_argument("--max-transcodes", type=int, default=None)
    parser.add_argument("--bitrate-only", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--report-format", choices=["text", "csv", "json"], default="text")
    parser.add_argument("--report-file", action="store_true")
    parser.add_argument("--confirm-delete", action="store_true")
    parser.add_argument("--report-dir", default=None)
    parser.add_argument("--est-mode", choices=["ratio", "target"], default="ratio")
    parser.add_argument("--est-ratio", type=float, default=None)
    parser.add_argument("--est-target-v-bitrate", default=None)
    args = parser.parse_args()

    _which_ffmpeg()

    try:
        min_bytes = _parse_size(args.min_size)
    except Exception:
        print("invalid --min-size", file=sys.stderr)
        sys.exit(2)

    min_bps = None
    if args.min_bitrate:
        try:
            min_bps = _parse_bitrate(args.min_bitrate)
        except Exception:
            print("invalid --min-bitrate", file=sys.stderr)
            sys.exit(2)
    if args.bitrate_only and min_bps is None:
        print("--bitrate-only requires --min-bitrate", file=sys.stderr)
        sys.exit(2)

    try:
        audio_bps = _parse_bitrate(args.audio_bitrate)
    except Exception:
        audio_bps = None

    exts = _exts_list(args.extensions)
    root = Path(args.path)
    report_dir = Path(args.report_dir) if args.report_dir else Path.cwd()
    if not root.exists() or not root.is_dir():
        print("path is not a directory", file=sys.stderr)
        sys.exit(2)

    crf = args.crf
    if crf is None:
        crf = 28 if args.codec == "libx265" else 23
    default_est_ratio = 0.6 if args.codec == "libx265" else 0.85

    total = 0
    processed = 0
    skipped = 0
    saved_bytes = 0
    est_total_saved_bytes = 0
    est_total_count = 0
    records = []

    for p in root.rglob("*"):
        if args.max_transcodes is not None and processed >= args.max_transcodes:
            print("Reached max transcodes; stopping.")
            break
        if not p.is_file():
            continue
        if p.suffix.lower().lstrip(".") not in exts:
            continue
        name_lower = p.name.lower()
        if ("transcoded" in name_lower) or ("transcoding" in name_lower):
            continue
        try:
            orig_size = p.stat().st_size
        except OSError:
            continue
        total += 1
        dur = 0.0
        bps = None
        if args.print_bitrate or (min_bps is not None) or (args.report_format in ("csv", "json")) or args.dry_run:
            dur = _ffprobe_duration(p)
            if dur > 0:
                bps = (orig_size * 8.0) / dur
        if args.print_bitrate:
            if bps is not None:
                print(f"[bitrate] {p} -> {_human_bitrate(bps)}")
            else:
                print(f"[bitrate] {p} -> unknown")
        est_out = None
        est_saved = None
        if dur > 0:
            if args.est_mode == "target" and args.est_target_v_bitrate:
                try:
                    v_target_bps = _parse_bitrate(args.est_target_v_bitrate)
                except Exception:
                    v_target_bps = None
                if v_target_bps is not None:
                    total_bps = v_target_bps + (audio_bps or 0)
                    est_out = int(total_bps * dur / 8.0)
            elif bps is not None:
                ratio = args.est_ratio if args.est_ratio is not None else default_est_ratio
                total_bps = bps * ratio
                if audio_bps is not None and total_bps < audio_bps:
                    total_bps = audio_bps
                est_out = int(total_bps * dur / 8.0)
        if est_out is not None:
            est_saved = int(orig_size - est_out)
        if args.verbose:
            lbl = _human_bitrate(bps) if bps is not None else "unknown"
            est_out_lbl = _human(est_out) if est_out is not None else "unknown"
            est_saved_lbl = _human(est_saved) if est_saved is not None else "unknown"
            print(f"[info] {p} duration={dur:.1f}s bitrate={lbl} est_out={est_out_lbl} est_saved={est_saved_lbl}")
        skip_reason = None
        if min_bps is not None:
            if bps is not None:
                if bps < min_bps:
                    skip_reason = "bitrate_below_threshold"
            else:
                if args.bitrate_only:
                    skip_reason = "no_duration"
                elif orig_size < min_bytes:
                    skip_reason = "size_below_threshold"
        else:
            if orig_size < min_bytes:
                skip_reason = "size_below_threshold"
        if skip_reason:
            if args.dry_run:
                lbl = _human_bitrate(bps) if bps is not None else "unknown"
                est_out_lbl = _human(est_out) if est_out is not None else "unknown"
                est_saved_lbl = _human(est_saved) if est_saved is not None else "unknown"
                print(f"DRY-RUN: skip {p} reason={skip_reason} bitrate={lbl} est_out={est_out_lbl} est_saved={est_saved_lbl}")
            skipped += 1
            if args.report_format in ("csv", "json"):
                records.append({
                    "path": str(p),
                    "size_bytes": int(orig_size),
                    "duration_sec": float(dur) if dur else None,
                    "bitrate_bps": float(bps) if bps is not None else None,
                    "action": "skipped",
                    "reason": skip_reason,
                    "estimated_output_size_bytes": int(est_out) if est_out is not None else None,
                    "estimated_saved_bytes": int(est_saved) if est_saved is not None else None,
                    "would_transcode": False,
                })
            continue
        if args.dry_run:
            lbl = _human_bitrate(bps) if bps is not None else "unknown"
            est_out_lbl = _human(est_out) if est_out is not None else "unknown"
            est_saved_lbl = _human(est_saved) if est_saved is not None else "unknown"
            print(f"DRY-RUN: would transcode {p} bitrate={lbl} est_out={est_out_lbl} est_saved={est_saved_lbl}")
            if args.report_format in ("csv", "json"):
                records.append({
                    "path": str(p),
                    "size_bytes": int(orig_size),
                    "duration_sec": float(dur) if dur else None,
                    "bitrate_bps": float(bps) if bps is not None else None,
                    "action": "dry_run",
                    "estimated_output_size_bytes": int(est_out) if est_out is not None else None,
                    "estimated_saved_bytes": int(est_saved) if est_saved is not None else None,
                    "would_transcode": True,
                })
            if est_saved is not None and est_saved > 0:
                est_total_saved_bytes += est_saved
                est_total_count += 1
            continue
        out_ext = "." + (args.container if args.container else ("mp4" if p.suffix.lower() in (".mp4", ".m4v") else "mkv"))
        final_path = p.with_name(p.stem + ".transcoded" + out_ext)
        tmp_path = p.with_name(p.stem + ".transcoding" + out_ext)
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                tmp_path = _unique_path(tmp_path)
        cmd = _build_cmd(p, tmp_path, args.codec, crf, args.preset, args.audio_codec, args.audio_bitrate, out_ext.lstrip("."), args.gpu, args.verbose)
        print(f"Transcoding {p} -> {tmp_path}")
        if args.verbose and not args.dry_run:
            print("ffmpeg cmd: " + " ".join(cmd))
        if args.dry_run:
            print("dry-run")
            skipped += 1
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            if args.report_format in ("csv", "json"):
                records.append({
                    "path": str(p),
                    "size_bytes": int(orig_size),
                    "duration_sec": float(dur) if dur else None,
                    "bitrate_bps": float(bps) if bps is not None else None,
                    "action": "dry_run",
                })
            continue
        t0 = time.time()
        if args.verbose:
            proc = subprocess.Popen(cmd)
            ret_code = proc.wait()
        else:
            proc_run = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            ret_code = proc_run.returncode
        dt = time.time() - t0
        if ret_code != 0:
            print(f"ffmpeg failed for {p}")
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            if args.report_format in ("csv", "json"):
                records.append({
                    "path": str(p),
                    "size_bytes": int(orig_size),
                    "duration_sec": float(dur) if dur else None,
                    "bitrate_bps": float(bps) if bps is not None else None,
                    "action": "failed",
                    "reason": "ffmpeg_error",
                    "time_sec": round(dt, 3),
                })
            continue
        try:
            new_size = tmp_path.stat().st_size
        except OSError:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            continue
        if new_size >= orig_size * (1.0 - args.min_saving / 100.0):
            print(f"Skipped (insufficient saving): {p} old={_human(orig_size)} new={_human(new_size)} time={dt:.1f}s")
            try:
                tmp_path.unlink()
            except OSError:
                pass
            skipped += 1
            if args.report_format in ("csv", "json"):
                records.append({
                    "path": str(p),
                    "size_bytes": int(orig_size),
                    "duration_sec": float(dur) if dur else None,
                    "bitrate_bps": float(bps) if bps is not None else None,
                    "action": "skipped",
                    "reason": "insufficient_saving",
                    "time_sec": round(dt, 3),
                    "output_size_bytes": int(new_size),
                })
            continue
        # Verify durations match before deciding deletion
        orig_dur_check = _ffprobe_duration(p)
        out_dur_check = _ffprobe_duration(tmp_path)
        durations_match = (orig_dur_check > 0 and out_dur_check > 0 and abs(orig_dur_check - out_dur_check) <= 0.5)
        if not durations_match:
            try:
                print(f"Duration mismatch; keeping original: {p} orig={orig_dur_check:.3f}s new={out_dur_check:.3f}s")
            except Exception:
                print("Duration mismatch; keeping original")

        effect_no_delete = args.no_delete or (not durations_match)
        if not effect_no_delete and args.confirm_delete:
            try:
                resp = input(f"Delete original file '{p}'? Type 'y' to confirm: ").strip().lower()
            except Exception:
                resp = ""
            if resp != "y":
                effect_no_delete = True

        target_path = final_path
        if effect_no_delete:
            if target_path == p:
                target_path = _unique_path(p.with_name(p.stem + ".transcoded" + out_ext))
            elif target_path.exists():
                target_path = _unique_path(target_path)
        else:
            if target_path.exists() and target_path != p:
                target_path = _unique_path(target_path)
        if args.verbose:
            print(f"Target path: {target_path}")
        if not effect_no_delete and p.exists():
            try:
                p.unlink()
            except OSError:
                pass
        try:
            tmp_path.rename(target_path)
        except OSError:
            try:
                shutil.move(str(tmp_path), str(target_path))
            except Exception:
                pass
        processed += 1
        saved = max(0, orig_size - (target_path.stat().st_size if target_path.exists() else new_size))
        saved_bytes += saved
        print(f"Done: {p.name} -> {_human(saved)} saved in {dt:.1f}s")
        if args.report_format in ("csv", "json"):
            out_sz = (target_path.stat().st_size if target_path.exists() else new_size)
            records.append({
                "path": str(p),
                "size_bytes": int(orig_size),
                "duration_sec": float(dur) if dur else None,
                "bitrate_bps": float(bps) if bps is not None else None,
                "action": "processed",
                "time_sec": round(dt, 3),
                "saved_bytes": int(saved),
                "output_path": str(target_path),
                "output_size_bytes": int(out_sz),
                "reason": ("duration_mismatch" if not durations_match else None),
            })

    print(f"Files scanned: {total}")
    print(f"Processed: {processed}")
    print(f"Skipped: {skipped}")
    print(f"Total saved: {_human(saved_bytes)}")

    if args.dry_run:
        print(f"Estimated total savings (would transcode): {_human(est_total_saved_bytes)} across {est_total_count} files")

    # Reporting
    if args.report_format in ("csv", "json"):
        if args.report_format == "json":
            data = records
            if args.report_file:
                try:
                    ts = time.strftime("%Y%m%d_%H%M%S")
                    report_dir.mkdir(parents=True, exist_ok=True)
                    report_path = report_dir / f"transcode_report_{ts}.json"
                    report_path = _unique_path(report_path)
                    with open(report_path, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2)
                    print(f"Report written to {report_path}")
                except Exception as e:
                    print(f"Failed to write report: {e}")
            else:
                print(json.dumps(data, indent=2))
        else:  # csv 
            fieldnames = [
                "path",
                "size_bytes",
                "duration_sec",
                "bitrate_bps",
                "action",
                "reason",
                "time_sec",
                "saved_bytes",
                "output_path",
                "output_size_bytes",
                "estimated_output_size_bytes",
                "estimated_saved_bytes",
                "estimated_total_saved_bytes",
                "would_transcode",
            ]
            if args.report_file:
                try:
                    ts = time.strftime("%Y%m%d_%H%M%S")
                    report_dir.mkdir(parents=True, exist_ok=True)
                    report_path = report_dir / f"transcode_report_{ts}.csv"
                    report_path = _unique_path(report_path)
                    with open(report_path, "w", newline="", encoding="utf-8") as f:
                        w = csv.DictWriter(f, fieldnames=fieldnames)
                        w.writeheader()
                        for r in records:
                            # ensure all keys present
                            row = {k: r.get(k) for k in fieldnames}
                            w.writerow(row)
                    print(f"Report written to {report_path}")
                except Exception as e:
                    print(f"Failed to write report: {e}")
            else:
                w = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
                w.writeheader()
                for r in records:
                    row = {k: r.get(k) for k in fieldnames}
                    w.writerow(row)
        if args.dry_run:
            if args.report_format == "json":
                # print summary to stdout in addition to the JSON file/printout
                print(json.dumps({
                    "action": "summary",
                    "estimated_total_saved_bytes": int(est_total_saved_bytes),
                    "would_transcode_count": int(est_total_count),
                }, indent=2))
            else:
                # append a CSV-like summary line to stdout only when not writing to file
                if not args.report_file and args.report_format == "csv":
                    print(f"summary,,,,,,,{''},{''},{''},{''},{est_total_saved_bytes},{''}")
    elif args.report_format == "text" and args.report_file:
        try:
            ts = time.strftime("%Y%m%d_%H%M%S")
            report_dir.mkdir(parents=True, exist_ok=True)
            report_path = report_dir / f"transcode_report_{ts}.txt"
            report_path = _unique_path(report_path)
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(f"Files scanned: {total}\n")
                f.write(f"Processed: {processed}\n")
                f.write(f"Skipped: {skipped}\n")
                f.write(f"Total saved: {_human(saved_bytes)}\n")
                if args.dry_run:
                    f.write(f"Estimated total savings (would transcode): {_human(est_total_saved_bytes)} across {est_total_count} files\n")
            print(f"Report written to {report_path}")
        except Exception as e:
            print(f"Failed to write report: {e}")


if __name__ == "__main__":
    main()
