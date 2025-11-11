import argparse
import csv
import json
import os
import sys
from datetime import datetime
from itertools import combinations
from pathlib import Path

VIDEO_EXTS = {
    "mp4",
    "mkv",
    "avi",
    "mov",
    "m4v",
    "mpg",
    "mpeg",
    "ts",
    "m2ts",
    "webm",
    "wmv",
    "flv",
}

AUDIO_EXTS = {
    "mp3",
    "m4a",
    "aac",
    "flac",
    "wav",
    "ogg",
    "oga",
    "wma",
    "opus",
    "aiff",
    "aif",
    "alac",
    "mka",
    "ape",
    "mpc",
}

DOCUMENT_EXTS = {
    "pdf",
    "doc",
    "docx",
    "xls",
    "xlsx",
    "ppt",
    "pptx",
    "txt",
    "rtf",
    "odt",
    "ods",
    "odp",
    "epub",
    "mobi",
    "csv",
    "tsv",
    "md",
    "log",
    "pages",
    "numbers",
    "key",
}

VIDEO_CHOICES = sorted(list(VIDEO_EXTS))


def normalize_ext(ext: str) -> str:
    if not ext:
        return ""
    e = ext.lower()
    if e.startswith("."):
        e = e[1:]
    return e


def classify_extension(ext: str) -> str:
    e = normalize_ext(ext)
    if e in VIDEO_EXTS:
        return "video"
    if e in AUDIO_EXTS:
        return "audio"
    if e in DOCUMENT_EXTS:
        return "document"
    return "unknown"


def allowed_size(value: str) -> float:
    try:
        v = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("allowed-size-difference must be a number between 0 and 1")
    if not (0.0 <= v <= 1.0):
        raise argparse.ArgumentTypeError("allowed-size-difference must be between 0 and 1")
    return v


def unique_log_path(directory: Path, base_name: str, ext: str) -> Path:
    candidate = directory / f"{base_name}.{ext}"
    if not candidate.exists():
        return candidate
    i = 1
    while True:
        p = directory / f"{base_name}_{i}.{ext}"
        if not p.exists():
            return p
        i += 1


def relative_size_diff(a: int, b: int) -> float:
    if a == b:
        return 0.0
    m = max(a, b)
    if m == 0:
        return 0.0
    return abs(a - b) / m


def format_bytes(n: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    size = float(max(0, n))
    i = 0
    while size >= 1024 and i < len(units) - 1:
        size /= 1024.0
        i += 1
    if i == 0:
        return f"{int(size)} {units[i]}"
    return f"{size:.2f} {units[i]}"


def prompt_yes_no(prompt: str) -> bool:
    try:
        resp = input(prompt).strip().lower()
    except EOFError:
        return False
    return resp in {"y", "yes"}


def scan_directory(directory: Path):
    items = []
    with os.scandir(directory) as it:
        for entry in it:
            if not entry.is_file(follow_symlinks=False):
                continue
            name = entry.name
            if "." in name:
                base = name[: name.rfind(".")]
                ext = name[name.rfind(".") + 1 :]
            else:
                base = name
                ext = ""
            ext_norm = normalize_ext(ext)
            ftype = classify_extension(ext_norm)
            try:
                size = entry.stat(follow_symlinks=False).st_size
            except OSError:
                size = -1
            items.append(
                {
                    "path": str(directory / name),
                    "name": name,
                    "basename": base,
                    "ext": ext_norm,
                    "type": ftype,
                    "size": size,
                }
            )
    return items


def write_log(records, directory: Path, log_type: str) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = f"deduplicate_log_{ts}"
    if log_type == "csv":
        path = unique_log_path(directory, base, "csv")
        fieldnames = [
            "timestamp",
            "path",
            "name",
            "basename",
            "extension",
            "filetype",
            "size_bytes",
            "action",
            "details",
            "group",
        ]
        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            for r in records:
                w.writerow(
                    {
                        "timestamp": r.get("timestamp", ""),
                        "path": r.get("path", ""),
                        "name": r.get("name", ""),
                        "basename": r.get("basename", ""),
                        "extension": r.get("extension", ""),
                        "filetype": r.get("filetype", ""),
                        "size_bytes": r.get("size_bytes", ""),
                        "action": r.get("action", ""),
                        "details": r.get("details", ""),
                        "group": r.get("group", ""),
                    }
                )
        return path
    else:
        path = unique_log_path(directory, base, "json")
        with path.open("w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
        return path


def process(args) -> int:
    directory = Path(args.directory).expanduser().resolve()
    if not directory.exists() or not directory.is_dir():
        print("directory must be an existing directory", file=sys.stderr)
        return 2

    items = scan_directory(directory)
    now = datetime.now().isoformat(timespec="seconds")

    target_type = args.filetype
    if target_type == "video":
        preferred = normalize_ext(args.preferred_video_type)
    elif target_type == "audio":
        preferred = normalize_ext(args.preferred_audio_type)
    else:
        preferred = normalize_ext(args.preferred_document_type)

    records = {}
    for it in items:
        key = it["path"]
        ftype = it["type"]
        if ftype == "unknown":
            action = "skipped_unknown_extension"
            details = ""
        elif ftype != target_type:
            action = "skipped_not_target_type"
            details = ""
        else:
            action = "pending"
            details = ""
        records[key] = {
            "timestamp": now,
            "path": it["path"],
            "name": it["name"],
            "basename": it["basename"],
            "extension": it["ext"],
            "filetype": it["type"],
            "size_bytes": it["size"],
            "action": action,
            "details": details,
            "group": "",
        }

    groups = {}
    for it in items:
        if it["type"] != target_type:
            continue
        group_key = f"{target_type}:{it['basename'].lower()}"
        groups.setdefault(group_key, []).append(it)

    for gk, files in groups.items():
        for it in files:
            records[it["path"]]["group"] = gk
        if len(files) <= 1:
            it = files[0]
            if records[it["path"]]["action"] == "pending":
                records[it["path"]]["action"] = "kept_no_duplicate"
            continue
        sizes = [f["size"] for f in files]
        if any(s < 0 for s in sizes):
            for it in files:
                if records[it["path"]]["action"] == "pending":
                    records[it["path"]]["action"] = "skipped_stat_error"
            continue
        bad = False
        for a, b in combinations(files, 2):
            if relative_size_diff(a["size"], b["size"]) > args.allowed_size_difference:
                bad = True
                break
        if bad:
            for it in files:
                if records[it["path"]]["action"] == "pending":
                    records[it["path"]]["action"] = "skipped_size_mismatch"
            continue
        have_pref = [f for f in files if normalize_ext(f["ext"]) == preferred]
        if not have_pref:
            for it in files:
                if records[it["path"]]["action"] == "pending":
                    records[it["path"]]["action"] = "skipped_preferred_missing"
            continue
        keep = max(have_pref, key=lambda x: x["size"])
        for it in files:
            if it["path"] == keep["path"]:
                records[it["path"]]["action"] = "kept_preferred"
                continue
            if args.dry_run:
                records[it["path"]]["action"] = "would_delete"
                continue
            if args.confirm:
                yn = prompt_yes_no(f"Delete {it['path']} ({it['size']} bytes)? [y/N]: ")
                if not yn:
                    records[it["path"]]["action"] = "skipped_user_declined"
                    continue
            try:
                os.remove(it["path"])
                records[it["path"]]["action"] = "deleted"
            except Exception as e:
                records[it["path"]]["action"] = "error"
                records[it["path"]]["details"] = str(e)

    for rec in records.values():
        if rec["action"] == "pending":
            rec["action"] = "kept_no_duplicate"

    out_path = None
    if args.log:
        out_path = write_log(list(records.values()), directory, args.log_type)
        print(f"Log written to: {out_path}")

    deleted = sum(1 for r in records.values() if r["action"] == "deleted")
    would_delete = sum(1 for r in records.values() if r["action"] == "would_delete")
    kept = sum(1 for r in records.values() if r["action"].startswith("kept"))
    skipped = sum(1 for r in records.values() if r["action"].startswith("skipped"))
    errored = sum(1 for r in records.values() if r["action"] == "error")
    freed_bytes = sum(r["size_bytes"] for r in records.values() if r["action"] == "deleted")
    would_free_bytes = sum(r["size_bytes"] for r in records.values() if r["action"] == "would_delete")

    print(
        f"Summary: deleted={deleted}, would_delete={would_delete}, kept={kept}, skipped={skipped}, error={errored}"
    )
    if args.dry_run:
        print(
            f"Total space that would be freed: {format_bytes(would_free_bytes)} ({would_free_bytes} bytes)"
        )
    else:
        print(
            f"Total space freed: {format_bytes(freed_bytes)} ({freed_bytes} bytes)"
        )
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="deduplicate")
    p.add_argument(
        "--filetype",
        required=True,
        choices=["video", "audio", "document"],
        help="Type of files to process",
    )
    p.add_argument(
        "--preferred-video-type",
        dest="preferred_video_type",
        choices=VIDEO_CHOICES,
        help="Preferred extension for videos",
    )
    p.add_argument(
        "--preferred-audio-type",
        dest="preferred_audio_type",
        help="Preferred extension for audio",
    )
    p.add_argument(
        "--preferred-document-type",
        dest="preferred_document_type",
        help="Preferred extension for documents",
    )
    p.add_argument(
        "--directory",
        required=True,
        help="Directory to scan (non-recursive)",
    )
    p.add_argument(
        "--allowed-size-difference",
        type=allowed_size,
        default=0.01,
        help="0-1 threshold for relative size difference",
    )
    p.add_argument("--confirm", action="store_true", default=False)
    p.add_argument("--dry-run", action="store_true", default=False)
    p.add_argument("--log", dest="log", action="store_true")
    p.add_argument("--no-log", dest="log", action="store_false")
    p.set_defaults(log=True)
    p.add_argument("--log-type", choices=["csv", "json"], default="csv")
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.filetype == "video" and not args.preferred_video_type:
        parser.error("--preferred-video-type is required when --filetype=video")
    if args.filetype == "audio" and not args.preferred_audio_type:
        parser.error("--preferred-audio-type is required when --filetype=audio")
    if args.filetype == "document" and not args.preferred_document_type:
        parser.error("--preferred-document-type is required when --filetype=document")
    return_code = process(args)
    sys.exit(return_code)


if __name__ == "__main__":
    main()

