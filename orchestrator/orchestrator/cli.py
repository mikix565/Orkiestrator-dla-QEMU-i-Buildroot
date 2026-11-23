import argparse
import sys
from dataclasses import asdict, replace
from pathlib import Path

from orchestrator.analysis import AnalysisError, run_analysis
from orchestrator.builder import BuilderError, build_all
from orchestrator.check import run_check
from orchestrator.config import (
    Config,
    ConfigError,
    find_project_dir,
    load_config,
    parse_experiment,
)
from orchestrator.experiment import ExperimentError, run_experiment
from orchestrator.qemu_runner import RunnerError
from orchestrator.storage import StorageError

DEFAULT_PROJECT_DIR = Path(__file__).resolve().parents[2]


def _add_variants(parser: argparse.ArgumentParser, action: str) -> None:
    parser.add_argument(
        "--variant",
        action="append",
        dest="variants",
        help=f"wariant do {action} (można podać wielokrotnie)",
    )


def _add_clean(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--clean",
        action="store_true",
        help="usuń out/<wariant> przed budową (czysta budowa, mierzalny czas)",
    )


def _add_run_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--reps", type=int, help="liczba powtórzeń pomiarowych")
    parser.add_argument("--warmup", type=int, help="liczba przebiegów rozgrzewkowych")
    parser.add_argument("--qemu", default="qemu-system-x86_64", help="plik wykonywalny QEMU")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="orchestrator")
    parser.add_argument(
        "--project-dir",
        help="katalog projektu (domyślnie wyliczany z położenia programu)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="plik konfiguracji (domyślnie orchestrator/variants.toml w katalogu projektu)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="uruchom eksperyment na zbudowanych obrazach")
    _add_variants(run, "zmierzenia")
    _add_run_options(run)

    build = subparsers.add_parser("build", help="skonfiguruj i zbuduj obrazy wariantów")
    _add_variants(build, "zbudowania")
    _add_clean(build)

    analyze = subparsers.add_parser(
        "analyze", help="pokaż średnie wyniki benchmarków i czasu rozruchu (tabela i wykres)"
    )
    analyze.add_argument(
        "--experiment",
        help="identyfikator eksperymentu (domyślnie najnowszy)",
    )

    subparsers.add_parser(
        "check",
        help="wypisz czynniki, warianty i scenariusze oraz sprawdź konfigurację (bez budowania)",
    )

    everything = subparsers.add_parser(
        "all", help="zbuduj warianty, zmierz je i pokaż średnie wyniki jednym poleceniem"
    )
    _add_variants(everything, "przetworzenia")
    _add_clean(everything)
    _add_run_options(everything)
    return parser


def apply_overrides(config: Config, reps: int | None, warmup: int | None) -> Config:
    changes = {}
    if reps is not None:
        changes["repetitions"] = reps
    if warmup is not None:
        changes["warmup_runs"] = warmup
    if not changes:
        return config
    experiment = parse_experiment({**asdict(config.experiment), **changes})
    return replace(config, experiment=experiment)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ask = input if sys.stdin.isatty() else None
    try:
        project = find_project_dir(args.project_dir, DEFAULT_PROJECT_DIR, ask)
        config = load_config(project, args.config)
        if args.command == "check":
            return run_check(config)
        if args.command == "analyze":
            run_analysis(config, args.experiment)
            return 0
        if args.command in ("build", "all"):
            results = build_all(config, args.variants, args.clean)
            failed = [result.variant for result in results if result.status != "ok"]
            if failed:
                print(f"Błąd: nie zbudowano: {', '.join(failed)}", file=sys.stderr)
                return 1
            if args.command == "build":
                print(f"Gotowe: obrazy w {config.out_dir}")
                return 0
        config = apply_overrides(config, args.reps, args.warmup)
        store = run_experiment(config, args.variants, args.qemu)
        if args.command == "run":
            print(f"Gotowe: {store.directory}")
            return 0
        print(f"Pomiary zakończone: {store.directory}")
        run_analysis(config, store.experiment_id)
        print(f"Gotowe: {store.directory}")
        return 0
    except (AnalysisError, BuilderError, ConfigError, ExperimentError, RunnerError, StorageError) as error:
        print(f"Błąd: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nPrzerwano; zapisane przebiegi zostają w katalogu wyników.", file=sys.stderr)
        return 130
