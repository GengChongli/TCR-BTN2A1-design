#!/usr/bin/env python3
"""
Extract best HADDOCK models from capri_ss.tsv files.


For each docking task directory, it recursively finds capri_ss.tsv, keeps rows
with DockQ greater than the threshold, selects the lowest-score row, then writes
all selected models sorted by score.
"""

from __future__ import annotations

import argparse
import csv
import math
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


TASK_DIR_RE = re.compile(r"^\d+-\d+$")


@dataclass(frozen=True)
class Candidate:
    row: dict[str, str]
    capri_path: Path
    score: float
    dockq: float


@dataclass(frozen=True)
class SelectedModel:
    part_dir: str
    task_dir: str
    task_relpath: str
    task_path: Path
    capri_path: Path
    model: str
    model_path: Path | None
    model_exists: bool
    caprieval_rank: str
    score: float
    dockq: float


@dataclass
class ScanStats:
    part_dirs: int = 0
    task_dirs: int = 0
    capri_files: int = 0
    missing_parts: int = 0
    missing_columns: int = 0
    invalid_rows: int = 0
    tasks_without_capri: int = 0
    tasks_without_passing_model: int = 0
    missing_model_files: int = 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Scan HADDOCK part/task folders, filter capri_ss.tsv rows by DockQ, "
            "select the lowest score model per task, and output a sorted TSV."
        )
    )
    parser.add_argument(
        "-r",
        "--root",
        default=None,
        help=(
            "Folder containing part_00 ... part_27. Default: use "
            "generated_haddock_jobs if it exists, otherwise current folder."
        ),
    )
    parser.add_argument(
        "-o",
        "--output",
        default="best_capri_models.tsv",
        help="Output TSV file. Use '-' to write to stdout. Default: best_capri_models.tsv.",
    )
    parser.add_argument(
        "--min-dockq",
        type=float,
        default=0.75,
        help="Keep only rows with dockq greater than this value. Default: 0.75.",
    )
    parser.add_argument(
        "--num-parts",
        type=int,
        default=28,
        help="Expected number of part folders, starting from part_00. Default: 28.",
    )
    parser.add_argument(
        "--part-prefix",
        default="part_",
        help="Prefix for part folders. Default: part_.",
    )
    parser.add_argument(
        "--scan-existing-parts",
        action="store_true",
        help="Scan every existing part_* folder instead of requiring part_00 ... part_27.",
    )
    parser.add_argument(
        "--capri-name",
        default="capri_ss.tsv",
        help="CAPRI summary filename to search for. Default: capri_ss.tsv.",
    )
    parser.add_argument(
        "--copy-models",
        default=None,
        help="Optional folder to copy selected model files into.",
    )
    return parser.parse_args()


def resolve_root(root_arg: str | None) -> Path:
    if root_arg:
        return Path(root_arg).resolve()

    generated = Path("generated_haddock_jobs")
    if generated.is_dir() and any(generated.glob("part_*")):
        return generated.resolve()

    return Path(".").resolve()


def normalized_columns(fieldnames: list[str] | None) -> dict[str, str]:
    if not fieldnames:
        return {}
    return {name.strip().lower(): name for name in fieldnames if name is not None}


def row_value(row: dict[str, str], column_name: str) -> str:
    target = column_name.strip().lower()
    for key, value in row.items():
        if key is None:
            continue
        if key.strip().lower() == target:
            return value or ""
    return ""


def parse_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    if not math.isfinite(number):
        return None
    return number


def discover_part_dirs(
    root: Path, prefix: str, num_parts: int, scan_existing_parts: bool, stats: ScanStats
) -> list[Path]:
    if scan_existing_parts:
        part_dirs = sorted(path for path in root.glob(f"{prefix}*") if path.is_dir())
        stats.part_dirs = len(part_dirs)
        return part_dirs

    part_dirs: list[Path] = []
    for part_id in range(num_parts):
        part_dir = root / f"{prefix}{part_id:02d}"
        if part_dir.is_dir():
            part_dirs.append(part_dir)
        else:
            stats.missing_parts += 1

    stats.part_dirs = len(part_dirs)
    return part_dirs


def discover_task_dirs(part_dir: Path) -> list[Path]:
    task_dirs = [
        path for path in part_dir.iterdir() if path.is_dir() and TASK_DIR_RE.match(path.name)
    ]
    return sorted(task_dirs, key=task_sort_key)


def task_sort_key(path: Path) -> tuple[int, int, str]:
    left, right = path.name.split("-", 1)
    try:
        return int(left), int(right), path.name
    except ValueError:
        return sys.maxsize, sys.maxsize, path.name


def candidates_from_capri(
    capri_path: Path, min_dockq: float, stats: ScanStats
) -> list[Candidate]:
    candidates: list[Candidate] = []

    with capri_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        columns = normalized_columns(reader.fieldnames)
        score_col = columns.get("score")
        dockq_col = columns.get("dockq")

        if score_col is None or dockq_col is None:
            stats.missing_columns += 1
            return candidates

        for row in reader:
            score = parse_float(row.get(score_col))
            dockq = parse_float(row.get(dockq_col))
            if score is None or dockq is None:
                stats.invalid_rows += 1
                continue
            if dockq > min_dockq:
                candidates.append(
                    Candidate(row=row, capri_path=capri_path, score=score, dockq=dockq)
                )

    return candidates


def resolve_model_path(capri_path: Path, model: str) -> Path | None:
    if not model:
        return None

    model_path = (capri_path.parent / model).resolve()
    if model_path.is_file():
        return model_path

    gz_model_path = Path(f"{model_path}.gz")
    if gz_model_path.is_file():
        return gz_model_path

    return model_path


def best_model_from_task(
    root: Path, part_dir: Path, task_dir: Path, capri_name: str, min_dockq: float, stats: ScanStats
) -> SelectedModel | None:
    capri_files = sorted(task_dir.rglob(capri_name))
    if not capri_files:
        stats.tasks_without_capri += 1
        return None

    all_candidates: list[Candidate] = []
    for capri_path in capri_files:
        stats.capri_files += 1
        all_candidates.extend(candidates_from_capri(capri_path, min_dockq, stats))

    if not all_candidates:
        stats.tasks_without_passing_model += 1
        return None

    best = min(all_candidates, key=lambda item: (item.score, -item.dockq))
    model = row_value(best.row, "model")
    model_path = resolve_model_path(best.capri_path, model)
    model_exists = model_path.is_file() if model_path else False
    if model and not model_exists:
        stats.missing_model_files += 1

    return SelectedModel(
        part_dir=part_dir.name,
        task_dir=task_dir.name,
        task_relpath=task_dir.relative_to(root).as_posix(),
        task_path=task_dir,
        capri_path=best.capri_path,
        model=model,
        model_path=model_path,
        model_exists=model_exists,
        caprieval_rank=row_value(best.row, "caprieval_rank"),
        score=best.score,
        dockq=best.dockq,
    )


def select_models(args: argparse.Namespace) -> tuple[Path, list[SelectedModel], ScanStats]:
    root = resolve_root(args.root)
    if not root.is_dir():
        raise SystemExit(f"Root folder does not exist: {root}")

    stats = ScanStats()
    selected: list[SelectedModel] = []

    part_dirs = discover_part_dirs(
        root=root,
        prefix=args.part_prefix,
        num_parts=args.num_parts,
        scan_existing_parts=args.scan_existing_parts,
        stats=stats,
    )
    if not part_dirs:
        if args.scan_existing_parts:
            expected = f"{args.part_prefix}*"
        else:
            expected = f"{args.part_prefix}00 ... {args.part_prefix}{args.num_parts - 1:02d}"
        raise SystemExit(f"No part folders found under {root}. Expected: {expected}")

    for part_dir in part_dirs:
        task_dirs = discover_task_dirs(part_dir)
        stats.task_dirs += len(task_dirs)

        for task_dir in task_dirs:
            best_model = best_model_from_task(
                root=root,
                part_dir=part_dir,
                task_dir=task_dir,
                capri_name=args.capri_name,
                min_dockq=args.min_dockq,
                stats=stats,
            )
            if best_model is not None:
                selected.append(best_model)

    selected.sort(key=lambda item: (item.score, -item.dockq, item.part_dir, item.task_dir))
    return root, selected, stats


def display_path(path: Path | None, root: Path) -> str:
    if path is None:
        return ""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def write_tsv(root: Path, selected: list[SelectedModel], output: str) -> None:
    fieldnames = [
        "part_dir",
        "task_dir",
        "task_relpath",
        "score",
        "dockq",
        "caprieval_rank",
        "model",
        "model_path",
        "model_exists",
        "capri_path",
    ]

    if output == "-":
        handle = sys.stdout
        close_handle = False
    else:
        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        handle = output_path.open("w", encoding="utf-8", newline="")
        close_handle = True

    try:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        for item in selected:
            writer.writerow(
                {
                    "part_dir": item.part_dir,
                    "task_dir": item.task_dir,
                    "task_relpath": item.task_relpath,
                    "score": f"{item.score:.6f}",
                    "dockq": f"{item.dockq:.6f}",
                    "caprieval_rank": item.caprieval_rank,
                    "model": item.model,
                    "model_path": display_path(item.model_path, root),
                    "model_exists": "yes" if item.model_exists else "no",
                    "capri_path": display_path(item.capri_path, root),
                }
            )
    finally:
        if close_handle:
            handle.close()


def copy_selected_models(selected: list[SelectedModel], destination: Path) -> int:
    destination.mkdir(parents=True, exist_ok=True)
    copied = 0

    for item in selected:
        if item.model_path is None or not item.model_path.is_file():
            continue

        suffix = "".join(item.model_path.suffixes) or item.model_path.suffix
        filename = f"{item.part_dir}_{item.task_dir}_score_{item.score:.3f}_dockq_{item.dockq:.3f}{suffix}"
        shutil.copy2(item.model_path, destination / filename)
        copied += 1

    return copied


def print_summary(root: Path, output: str, selected: list[SelectedModel], stats: ScanStats) -> None:
    print(f"Root: {root}", file=sys.stderr)
    print(
        (
            f"Scanned {stats.part_dirs} part folders, {stats.task_dirs} task folders, "
            f"{stats.capri_files} capri_ss.tsv files."
        ),
        file=sys.stderr,
    )
    print(f"Selected {len(selected)} models.", file=sys.stderr)

    if stats.missing_parts:
        print(f"Missing expected part folders: {stats.missing_parts}", file=sys.stderr)
    if stats.tasks_without_capri:
        print(f"Tasks without capri_ss.tsv: {stats.tasks_without_capri}", file=sys.stderr)
    if stats.tasks_without_passing_model:
        print(
            f"Tasks without models passing DockQ filter: {stats.tasks_without_passing_model}",
            file=sys.stderr,
        )
    if stats.missing_columns:
        print(f"capri_ss.tsv files missing score/dockq columns: {stats.missing_columns}", file=sys.stderr)
    if stats.invalid_rows:
        print(f"Rows with invalid score/dockq values: {stats.invalid_rows}", file=sys.stderr)
    if stats.missing_model_files:
        print(
            f"Selected rows whose model files were not found: {stats.missing_model_files}",
            file=sys.stderr,
        )
    if output != "-":
        print(f"Wrote: {output}", file=sys.stderr)


def main() -> int:
    args = parse_args()
    root, selected, stats = select_models(args)
    write_tsv(root, selected, args.output)

    if args.copy_models:
        copied = copy_selected_models(selected, Path(args.copy_models))
        print(f"Copied {copied} model files to: {args.copy_models}", file=sys.stderr)

    print_summary(root, args.output, selected, stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
