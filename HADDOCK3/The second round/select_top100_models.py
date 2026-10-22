#!/usr/bin/env python3
"""
Select top HADDOCK cluster representative models for MD simulations.

For each HADDOCK task:
  1. read capri_clt.tsv to confirm cluster formation and get cluster scores
  2. read capri_ss.tsv to inspect the models in each cluster
  3. keep clusters whose first 4 models contain at least 3 models with dockq > 0.75
  4. choose the task's best cluster by the lowest cluster score from capri_clt.tsv
  5. rank tasks by best-cluster score and keep the top 100
  6. choose each selected cluster's representative model by:
       dockq > 0.75 first, then lowest single-model score

Default root is /fs2/home/gengcl/haddock-210, where part_00 ... part_27 are expected.
The final representative structures are copied to <root>/top100_md_structures by default.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import math
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


TASK_DIR_RE = re.compile(r"^\d+[-_]\d+$")


@dataclass(frozen=True)
class ModelRow:
    row: dict[str, str]
    cluster_id: str
    model: str
    model_path: Path | None
    model_exists: bool
    score: float
    dockq: float
    cluster_model_rank: int


@dataclass(frozen=True)
class ClusterCandidate:
    cluster_row: dict[str, str]
    capri_dir: Path
    capri_clt_path: Path
    capri_ss_path: Path
    cluster_id: str
    cluster_rank: str
    cluster_score: float
    cluster_n: int | None
    models: list[ModelRow]
    passing_models: list[ModelRow]
    representative: ModelRow


@dataclass(frozen=True)
class ClusterAttempt:
    capri_clt_path: Path
    capri_ss_path: Path
    cluster_id: str
    cluster_score: float
    cluster_n: int | None
    considered_count: int
    passing_count: int
    max_dockq: float | None


@dataclass(frozen=True)
class TaskSelection:
    rank: int
    part_dir: str
    task_dir: str
    task_relpath: str
    task_path: Path
    cluster: ClusterCandidate


@dataclass(frozen=True)
class FailedTask:
    part_dir: str
    task_dir: str
    task_relpath: str
    reason: str
    best_cluster_id: str
    best_cluster_score: str
    best_cluster_n: str
    considered_models: str
    passing_models: str
    max_dockq: str
    capri_clt_path: str
    capri_ss_path: str


@dataclass
class ScanStats:
    part_dirs: int = 0
    task_dirs: int = 0
    tasks_without_capri_pair: int = 0
    tasks_without_cluster: int = 0
    tasks_without_passing_cluster: int = 0
    invalid_rows: int = 0
    missing_model_files: int = 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Select top cluster representative HADDOCK models for MD."
    )
    parser.add_argument(
        "-r",
        "--root",
        default="/fs2/home/gengcl/haddock-210",
        help="Folder containing part_00 ... part_27. Default: /fs2/home/gengcl/haddock-210.",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="top100_md_models.tsv",
        help="Output TSV for selected top models. Default: top100_md_models.tsv.",
    )
    parser.add_argument(
        "--failed-output",
        default="cluster_filter_failed_tasks.tsv",
        help="Output TSV for tasks that fail filtering. Default: cluster_filter_failed_tasks.tsv.",
    )
    parser.add_argument(
        "--copy-models",
        default=None,
        help=(
            "Folder to copy the top representative models into. "
            "Default: <root>/top100_md_structures."
        ),
    )
    parser.add_argument(
        "--no-copy-models",
        action="store_true",
        help="Do not copy the final representative structures.",
    )
    parser.add_argument(
        "--keep-gz",
        action="store_true",
        help="When copying .gz models, keep them compressed instead of decompressing to .pdb.",
    )
    parser.add_argument(
        "--clean-copy-dir",
        action="store_true",
        help="Before copying, delete old rank_* model files from the copy folder.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite output TSVs and copied model files.",
    )
    parser.add_argument(
        "--min-dockq",
        type=float,
        default=0.75,
        help="DockQ threshold. Comparisons use dockq > threshold. Default: 0.75.",
    )
    parser.add_argument(
        "--models-per-cluster",
        type=int,
        default=4,
        help="Number of model rows considered per cluster. Default: 4.",
    )
    parser.add_argument(
        "--min-passing-models",
        type=int,
        default=3,
        help="Required number of models with dockq > threshold among the considered models. Default: 3.",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=100,
        help="Number of ranked tasks to keep for MD. Default: 100.",
    )
    parser.add_argument(
        "--num-parts",
        type=int,
        default=28,
        help="Expected number of part folders, starting from part_00. Default: 28.",
    )
    parser.add_argument(
        "--scan-existing-parts",
        action="store_true",
        help="Scan existing part_* folders instead of requiring part_00 ... part_27.",
    )
    return parser.parse_args()


def normalized_columns(fieldnames: list[str] | None) -> dict[str, str]:
    if not fieldnames:
        return {}
    return {name.strip().lower(): name for name in fieldnames if name is not None}


def row_value(row: dict[str, str], column_name: str) -> str:
    target = column_name.strip().lower()
    for key, value in row.items():
        if key is not None and key.strip().lower() == target:
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


def parse_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def normalize_id(value: str | None) -> str:
    return (value or "").strip()


def read_tsv(path: Path) -> tuple[list[dict[str, str]], dict[str, str]]:
    data_lines: list[str] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            data_lines.append(line)

    if not data_lines:
        return [], {}

    reader = csv.DictReader(data_lines, delimiter="\t")
    return list(reader), normalized_columns(reader.fieldnames)


def part_dirs(root: Path, num_parts: int, scan_existing: bool) -> list[Path]:
    if scan_existing:
        return sorted(path for path in root.glob("part_*") if path.is_dir())
    return [root / f"part_{part_id:02d}" for part_id in range(num_parts)]


def task_sort_key(path: Path) -> tuple[int, int, str]:
    left, right = re.split(r"[-_]", path.name, maxsplit=1)
    try:
        return int(left), int(right), path.name
    except ValueError:
        return sys.maxsize, sys.maxsize, path.name


def task_dirs(part_dir: Path) -> list[Path]:
    if not part_dir.is_dir():
        return []
    dirs = [path for path in part_dir.iterdir() if path.is_dir() and TASK_DIR_RE.match(path.name)]
    return sorted(dirs, key=task_sort_key)


def capri_pairs(task_dir: Path) -> list[tuple[Path, Path]]:
    pairs: list[tuple[Path, Path]] = []
    for capri_clt_path in sorted(task_dir.rglob("capri_clt.tsv")):
        capri_ss_path = capri_clt_path.parent / "capri_ss.tsv"
        if capri_ss_path.is_file():
            pairs.append((capri_clt_path, capri_ss_path))
    return pairs


def resolve_model_path(capri_ss_path: Path, model: str) -> tuple[Path | None, bool]:
    if not model:
        return None, False

    model_path = (capri_ss_path.parent / model).resolve()
    if model_path.is_file():
        return model_path, True

    gz_model_path = Path(f"{model_path}.gz")
    if gz_model_path.is_file():
        return gz_model_path, True

    return model_path, False


def model_rank(row: dict[str, str]) -> int:
    for column in ("model-cluster_ranking", "caprieval_rank"):
        value = parse_int(row_value(row, column))
        if value is not None:
            return value
    return sys.maxsize


def load_models_by_cluster(
    capri_ss_path: Path,
    min_dockq: float,
    stats: ScanStats,
) -> dict[str, list[ModelRow]]:
    rows, columns = read_tsv(capri_ss_path)
    required = {"cluster_id", "score", "dockq", "model"}
    if required.difference(columns):
        stats.invalid_rows += len(rows)
        return {}

    models_by_cluster: dict[str, list[ModelRow]] = {}
    for row in rows:
        cluster_id = normalize_id(row.get(columns["cluster_id"]))
        score = parse_float(row.get(columns["score"]))
        dockq = parse_float(row.get(columns["dockq"]))
        model = row.get(columns["model"], "") or ""

        if not cluster_id or cluster_id == "-":
            continue
        if score is None or dockq is None:
            stats.invalid_rows += 1
            continue

        model_path, model_exists = resolve_model_path(capri_ss_path, model)
        if model and not model_exists:
            stats.missing_model_files += 1

        model_row = ModelRow(
            row=row,
            cluster_id=cluster_id,
            model=model,
            model_path=model_path,
            model_exists=model_exists,
            score=score,
            dockq=dockq,
            cluster_model_rank=model_rank(row),
        )
        models_by_cluster.setdefault(cluster_id, []).append(model_row)

    for cluster_models in models_by_cluster.values():
        cluster_models.sort(key=lambda item: (item.cluster_model_rank, item.score))

    return models_by_cluster


def cluster_candidates_from_pair(
    capri_clt_path: Path,
    capri_ss_path: Path,
    min_dockq: float,
    models_per_cluster: int,
    min_passing_models: int,
    stats: ScanStats,
) -> tuple[list[ClusterCandidate], bool, list[ClusterAttempt]]:
    cluster_rows, columns = read_tsv(capri_clt_path)
    if not cluster_rows:
        return [], False, []

    required = {"cluster_id", "score"}
    if required.difference(columns):
        stats.invalid_rows += len(cluster_rows)
        return [], True, []

    models_by_cluster = load_models_by_cluster(capri_ss_path, min_dockq, stats)
    candidates: list[ClusterCandidate] = []
    attempts: list[ClusterAttempt] = []

    for cluster_row in cluster_rows:
        cluster_id = normalize_id(cluster_row.get(columns["cluster_id"]))
        cluster_score = parse_float(cluster_row.get(columns["score"]))
        if not cluster_id or cluster_score is None:
            stats.invalid_rows += 1
            continue

        cluster_n = parse_int(cluster_row.get(columns.get("n", "")))
        under_eval = row_value(cluster_row, "under_eval").strip().lower()
        cluster_models = models_by_cluster.get(cluster_id, [])
        considered_models = cluster_models[:models_per_cluster]
        passing_models = [model for model in considered_models if model.dockq > min_dockq]
        max_dockq = max((model.dockq for model in considered_models), default=None)
        attempts.append(
            ClusterAttempt(
                capri_clt_path=capri_clt_path,
                capri_ss_path=capri_ss_path,
                cluster_id=cluster_id,
                cluster_score=cluster_score,
                cluster_n=cluster_n,
                considered_count=len(considered_models),
                passing_count=len(passing_models),
                max_dockq=max_dockq,
            )
        )

        if cluster_n is not None and cluster_n < models_per_cluster:
            continue
        if under_eval in {"yes", "true", "1"}:
            continue

        if len(considered_models) < models_per_cluster:
            continue

        if len(passing_models) < min_passing_models:
            continue

        representative = min(passing_models, key=lambda model: (model.score, -model.dockq))
        candidates.append(
            ClusterCandidate(
                cluster_row=cluster_row,
                capri_dir=capri_clt_path.parent,
                capri_clt_path=capri_clt_path,
                capri_ss_path=capri_ss_path,
                cluster_id=cluster_id,
                cluster_rank=row_value(cluster_row, "cluster_rank"),
                cluster_score=cluster_score,
                cluster_n=cluster_n,
                models=considered_models,
                passing_models=passing_models,
                representative=representative,
            )
        )

    return candidates, True, attempts


def best_attempt(attempts: list[ClusterAttempt]) -> ClusterAttempt | None:
    if not attempts:
        return None
    return max(
        attempts,
        key=lambda attempt: (
            attempt.passing_count,
            attempt.max_dockq if attempt.max_dockq is not None else -math.inf,
            -attempt.cluster_score,
        ),
    )


def best_cluster_for_task(
    task_dir: Path,
    min_dockq: float,
    models_per_cluster: int,
    min_passing_models: int,
    stats: ScanStats,
) -> tuple[ClusterCandidate | None, str, ClusterAttempt | None]:
    pairs = capri_pairs(task_dir)
    if not pairs:
        stats.tasks_without_capri_pair += 1
        return None, "missing capri_clt.tsv/capri_ss.tsv pair", None

    all_candidates: list[ClusterCandidate] = []
    all_attempts: list[ClusterAttempt] = []
    saw_cluster_rows = False
    for capri_clt_path, capri_ss_path in pairs:
        candidates, has_cluster_rows, attempts = cluster_candidates_from_pair(
            capri_clt_path=capri_clt_path,
            capri_ss_path=capri_ss_path,
            min_dockq=min_dockq,
            models_per_cluster=models_per_cluster,
            min_passing_models=min_passing_models,
            stats=stats,
        )
        saw_cluster_rows = saw_cluster_rows or has_cluster_rows
        all_candidates.extend(candidates)
        all_attempts.extend(attempts)

    if not saw_cluster_rows:
        stats.tasks_without_cluster += 1
        return None, "no cluster rows in capri_clt.tsv", None

    if not all_candidates:
        stats.tasks_without_passing_cluster += 1
        return None, (
            f"no cluster with at least {min_passing_models}/{models_per_cluster} "
            f"models having dockq > {min_dockq}"
        ), best_attempt(all_attempts)

    return min(all_candidates, key=lambda cluster: (cluster.cluster_score, cluster.cluster_id)), "", None


def display_path(path: Path | None, root: Path) -> str:
    if path is None:
        return ""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def output_path(path_arg: str) -> Path:
    return Path(path_arg).resolve()


def ensure_can_write(path: Path, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"Output already exists: {path}. Use --overwrite to replace it.")
    path.parent.mkdir(parents=True, exist_ok=True)


def write_selected_tsv(root: Path, selections: list[TaskSelection], output: Path, overwrite: bool) -> None:
    ensure_can_write(output, overwrite)
    fieldnames = [
        "rank",
        "part_dir",
        "task_dir",
        "task_relpath",
        "cluster_score",
        "cluster_id",
        "cluster_rank",
        "cluster_n",
        "passing_models",
        "models_considered",
        "representative_model",
        "representative_model_path",
        "representative_model_exists",
        "representative_score",
        "representative_dockq",
        "capri_clt_path",
        "capri_ss_path",
    ]

    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        for selection in selections:
            cluster = selection.cluster
            representative = cluster.representative
            writer.writerow(
                {
                    "rank": selection.rank,
                    "part_dir": selection.part_dir,
                    "task_dir": selection.task_dir,
                    "task_relpath": selection.task_relpath,
                    "cluster_score": f"{cluster.cluster_score:.6f}",
                    "cluster_id": cluster.cluster_id,
                    "cluster_rank": cluster.cluster_rank,
                    "cluster_n": cluster.cluster_n if cluster.cluster_n is not None else "",
                    "passing_models": len(cluster.passing_models),
                    "models_considered": len(cluster.models),
                    "representative_model": representative.model,
                    "representative_model_path": display_path(representative.model_path, root),
                    "representative_model_exists": "yes" if representative.model_exists else "no",
                    "representative_score": f"{representative.score:.6f}",
                    "representative_dockq": f"{representative.dockq:.6f}",
                    "capri_clt_path": display_path(cluster.capri_clt_path, root),
                    "capri_ss_path": display_path(cluster.capri_ss_path, root),
                }
            )


def write_failed_tsv(root: Path, failures: list[FailedTask], output: Path, overwrite: bool) -> None:
    ensure_can_write(output, overwrite)
    fieldnames = [
        "part_dir",
        "task_dir",
        "task_relpath",
        "reason",
        "best_cluster_id",
        "best_cluster_score",
        "best_cluster_n",
        "considered_models",
        "passing_models",
        "max_dockq",
        "capri_clt_path",
        "capri_ss_path",
    ]

    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        for failure in failures:
            writer.writerow(
                {
                    "part_dir": failure.part_dir,
                    "task_dir": failure.task_dir,
                    "task_relpath": failure.task_relpath,
                    "reason": failure.reason,
                    "best_cluster_id": failure.best_cluster_id,
                    "best_cluster_score": failure.best_cluster_score,
                    "best_cluster_n": failure.best_cluster_n,
                    "considered_models": failure.considered_models,
                    "passing_models": failure.passing_models,
                    "max_dockq": failure.max_dockq,
                    "capri_clt_path": failure.capri_clt_path,
                    "capri_ss_path": failure.capri_ss_path,
                }
            )


def copied_model_name(selection: TaskSelection, source: Path, decompress_gz: bool) -> str:
    suffixes = source.suffixes
    if decompress_gz and suffixes and suffixes[-1] == ".gz":
        suffix = "".join(suffixes[:-1]) or ".pdb"
    else:
        suffix = "".join(suffixes) or source.suffix or ".pdb"

    return (
        f"rank_{selection.rank:03d}_{selection.part_dir}_{selection.task_dir}_"
        f"cluster_{selection.cluster.cluster_id}_dockq_{selection.cluster.representative.dockq:.3f}_"
        f"score_{selection.cluster.representative.score:.3f}{suffix}"
    )


def copy_selected_models(selections: list[TaskSelection], copy_dir: Path, overwrite: bool, decompress_gz: bool) -> int:
    copy_dir.mkdir(parents=True, exist_ok=True)
    copied = 0

    for selection in selections:
        source = selection.cluster.representative.model_path
        if source is None or not source.is_file():
            continue

        destination = copy_dir / copied_model_name(selection, source, decompress_gz)
        if destination.exists() and not overwrite:
            raise FileExistsError(
                f"Copied model already exists: {destination}. Use --overwrite to replace it."
            )

        if decompress_gz and source.suffix == ".gz":
            with gzip.open(source, "rb") as in_handle, destination.open("wb") as out_handle:
                shutil.copyfileobj(in_handle, out_handle)
        else:
            shutil.copy2(source, destination)

        copied += 1

    return copied


def clean_copy_dir(copy_dir: Path) -> int:
    if not copy_dir.is_dir():
        return 0

    removed = 0
    for pattern in ("rank_*.pdb", "rank_*.pdb.gz"):
        for path in copy_dir.glob(pattern):
            if path.is_file():
                path.unlink()
                removed += 1
    return removed


def scan_tasks(args: argparse.Namespace) -> tuple[Path, list[TaskSelection], list[FailedTask], ScanStats]:
    root = Path(args.root).resolve()
    if not root.is_dir():
        raise SystemExit(f"Root folder does not exist: {root}")
    if args.models_per_cluster < 1:
        raise SystemExit("--models-per-cluster must be at least 1")
    if args.min_passing_models < 1:
        raise SystemExit("--min-passing-models must be at least 1")
    if args.min_passing_models > args.models_per_cluster:
        raise SystemExit("--min-passing-models cannot be greater than --models-per-cluster")

    stats = ScanStats()
    all_task_selections: list[TaskSelection] = []
    failures: list[FailedTask] = []

    parts = part_dirs(root, args.num_parts, args.scan_existing_parts)
    stats.part_dirs = sum(1 for part_dir in parts if part_dir.is_dir())

    for part_dir in parts:
        for task_dir in task_dirs(part_dir):
            stats.task_dirs += 1
            task_relpath = task_dir.relative_to(root).as_posix()
            best_cluster, reason, nearest_attempt = best_cluster_for_task(
                task_dir=task_dir,
                min_dockq=args.min_dockq,
                models_per_cluster=args.models_per_cluster,
                min_passing_models=args.min_passing_models,
                stats=stats,
            )

            if best_cluster is None:
                best_cluster_score = ""
                best_cluster_n = ""
                considered_models = ""
                passing_models = ""
                max_dockq = ""
                best_cluster_id = ""
                capri_clt_path = ""
                capri_ss_path = ""
                if nearest_attempt is not None:
                    best_cluster_id = nearest_attempt.cluster_id
                    best_cluster_score = f"{nearest_attempt.cluster_score:.6f}"
                    best_cluster_n = (
                        str(nearest_attempt.cluster_n)
                        if nearest_attempt.cluster_n is not None
                        else ""
                    )
                    considered_models = str(nearest_attempt.considered_count)
                    passing_models = str(nearest_attempt.passing_count)
                    max_dockq = (
                        f"{nearest_attempt.max_dockq:.6f}"
                        if nearest_attempt.max_dockq is not None
                        else ""
                    )
                    capri_clt_path = display_path(nearest_attempt.capri_clt_path, root)
                    capri_ss_path = display_path(nearest_attempt.capri_ss_path, root)

                failures.append(
                    FailedTask(
                        part_dir=part_dir.name,
                        task_dir=task_dir.name,
                        task_relpath=task_relpath,
                        reason=reason,
                        best_cluster_id=best_cluster_id,
                        best_cluster_score=best_cluster_score,
                        best_cluster_n=best_cluster_n,
                        considered_models=considered_models,
                        passing_models=passing_models,
                        max_dockq=max_dockq,
                        capri_clt_path=capri_clt_path,
                        capri_ss_path=capri_ss_path,
                    )
                )
                continue

            all_task_selections.append(
                TaskSelection(
                    rank=0,
                    part_dir=part_dir.name,
                    task_dir=task_dir.name,
                    task_relpath=task_relpath,
                    task_path=task_dir,
                    cluster=best_cluster,
                )
            )

    all_task_selections.sort(
        key=lambda item: (item.cluster.cluster_score, item.part_dir, item.task_dir)
    )

    top_selections: list[TaskSelection] = []
    for rank, selection in enumerate(all_task_selections[: args.top_n], start=1):
        top_selections.append(
            TaskSelection(
                rank=rank,
                part_dir=selection.part_dir,
                task_dir=selection.task_dir,
                task_relpath=selection.task_relpath,
                task_path=selection.task_path,
                cluster=selection.cluster,
            )
        )

    return root, top_selections, failures, stats


def main() -> int:
    args = parse_args()
    root, selections, failures, stats = scan_tasks(args)

    selected_output = output_path(args.output)
    failed_output = output_path(args.failed_output)
    write_selected_tsv(root, selections, selected_output, args.overwrite)
    write_failed_tsv(root, failures, failed_output, args.overwrite)

    copied = 0
    removed = 0
    copy_dir = None
    if not args.no_copy_models:
        copy_dir = Path(args.copy_models).resolve() if args.copy_models else root / "top100_md_structures"
        if args.clean_copy_dir:
            removed = clean_copy_dir(copy_dir)
        copied = copy_selected_models(
            selections=selections,
            copy_dir=copy_dir,
            overwrite=args.overwrite,
            decompress_gz=not args.keep_gz,
        )

    print(f"Root: {root}")
    print(f"Scanned {stats.part_dirs} part folders and {stats.task_dirs} task folders.")
    print(f"Selected {len(selections)} top task representative models.")
    print(f"Failed tasks written: {len(failures)}")
    print(f"Wrote selected TSV: {selected_output}")
    print(f"Wrote failed-task TSV: {failed_output}")
    if copy_dir is not None:
        print(f"Copied representative models: {copied}")
        print(f"Copied model folder: {copy_dir}")
        if args.clean_copy_dir:
            print(f"Removed old copied model files: {removed}")
    if stats.tasks_without_capri_pair:
        print(f"Tasks without capri_clt.tsv/capri_ss.tsv pair: {stats.tasks_without_capri_pair}")
    if stats.tasks_without_cluster:
        print(f"Tasks without clusters: {stats.tasks_without_cluster}")
    if stats.tasks_without_passing_cluster:
        print(f"Tasks without passing cluster: {stats.tasks_without_passing_cluster}")
    if stats.invalid_rows:
        print(f"Invalid rows skipped: {stats.invalid_rows}")
    if stats.missing_model_files:
        print(f"Representative/source model path warnings: {stats.missing_model_files}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
