import argparse
import json
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.config import get_settings
from app.schemas.request import PlanPackRequest
from app.services.document_generator import generate_document_pack
from app.services.planner import create_course_plan
from app.services.storage_client import ensure_dir, write_json_file
from app.services.validator import validate_files
from app.schemas.sokqa import GeneratedFile


DEFAULT_MODELS = [
    "gemini-2.5-flash",
    "gemini-3-flash-preview",
    "gemini-3.5-flash",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare Gemini document generation models.")
    parser.add_argument("--theme", default="Git基礎講座")
    parser.add_argument("--target-user", default="Gitを初めて使う開発者")
    parser.add_argument("--sections", type=int, default=50)
    parser.add_argument("--models", nargs="*", default=DEFAULT_MODELS)
    parser.add_argument("--out-dir", default="model_comparison_outputs")
    return parser.parse_args()


def avg(values: list[int]) -> float:
    return sum(values) / len(values) if values else 0.0


def run_model(model: str, args: argparse.Namespace, out_dir: Path) -> dict:
    settings = get_settings()
    original_provider = settings.gemini_provider
    original_doc_model = settings.gemini_model_doc
    settings.gemini_provider = "gemini"
    settings.gemini_model_doc = model

    started = time.perf_counter()
    error = ""
    pack = None
    valid = False
    try:
        plan = create_course_plan(
            PlanPackRequest(
                theme=args.theme,
                targetUser=args.target_user,
                scale="quick",
                documentCount=1,
                sectionsPerDocument=args.sections,
                includeTts=False,
                enableTtsOptimize=False,
            )
        )
        plan.id = f"compare_{safe_name(model)}"
        plan.documents = plan.documents[:1]
        plan.documents[0].targetSectionCount = args.sections
        pack = generate_document_pack(plan, plan.documents[0])
        file = GeneratedFile(name=f"{safe_name(model)}.json", kind="document", content=pack.model_dump(exclude_none=True))
        validation = validate_files([file])
        valid = validation.valid
        if not valid:
            error = "; ".join(f"{item.path}: {item.message}" for item in validation.errors)
    except Exception as exc:
        error = str(exc)
    elapsed = time.perf_counter() - started

    settings.gemini_provider = original_provider
    settings.gemini_model_doc = original_doc_model

    sections = pack.documents if pack else []
    lengths = [len(section.text) for section in sections]
    tail = sections[-5:]
    out_path = out_dir / f"{safe_name(model)}.json"
    if pack:
        write_json_file(out_path, pack.model_dump(exclude_none=True))

    return {
        "model": model,
        "requested": args.sections,
        "actual": len(sections),
        "valid": valid,
        "elapsed": elapsed,
        "avg_len": avg(lengths),
        "tail_avg_len": avg([len(section.text) for section in tail]),
        "tail_texts": [section.text for section in tail],
        "file": str(out_path) if pack else "",
        "error": error,
    }


def safe_name(value: str) -> str:
    return value.replace("/", "_").replace(":", "_").replace(".", "_")


def print_table(results: list[dict]) -> None:
    headers = ["model", "sections", "valid", "seconds", "avg_len", "tail_avg_len", "file"]
    rows = []
    for result in results:
        rows.append(
            [
                result["model"],
                f"{result['actual']}/{result['requested']}",
                str(result["valid"]),
                f"{result['elapsed']:.1f}",
                f"{result['avg_len']:.1f}",
                f"{result['tail_avg_len']:.1f}",
                result["file"],
            ]
        )
    widths = [max(len(str(row[index])) for row in [headers, *rows]) for index in range(len(headers))]
    print(" | ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    print("-+-".join("-" * width for width in widths))
    for row in rows:
        print(" | ".join(str(value).ljust(widths[index]) for index, value in enumerate(row)))


def main() -> None:
    args = parse_args()
    out_dir = Path(args.out_dir)
    ensure_dir(out_dir)
    print("Run command:")
    print("python scripts/compare_gemini_document_models.py --sections", args.sections)
    print()

    results = [run_model(model, args, out_dir) for model in args.models]
    print_table(results)
    print()
    for result in results:
        print(f"## {result['model']}")
        if result["error"]:
            print("error:", result["error"])
        for index, text in enumerate(result["tail_texts"], start=max(1, result["actual"] - 4)):
            print(f"[section {index}] {text}")
        print()


if __name__ == "__main__":
    main()
