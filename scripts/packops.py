# -*- coding: utf-8 -*-
"""packs/<slug>/ ドラフト源のヘッドレス運用 CLI（docs/QUALITY_OPS_IMPLEMENTATION_PLAN.md §2）。

サイト UI と同じ service 関数（app.services.packops 経由で import_pack_files /
validate_files / quality_checker）を呼ぶ単一口径。裏口は作らない。

使い方（リポジトリルートから）:
  python scripts/packops.py validate pm_zeroichi_v1
  python scripts/packops.py review pm_zeroichi_v1 [--format markdown] [--text-source corrected]
  python scripts/packops.py pull pm_zeroichi_v1
  python scripts/packops.py import pm_zeroichi_v1 [--force]
  python scripts/packops.py quality-check pm_zeroichi_v1 --mode text --file quiz_pm_zeroichi_v1.json

課金は quality-check のみ（Gemini 呼び出し）。validate / pull / import は
LLM を使わない。review はローカル読み取り専用で、Gemini/TTS API・R2 を呼ばない。
import は packops.lock.json と R2 latest の乖離を検出したら拒否し、
先行して pull を要求する。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services import packops  # noqa: E402

PACKS_ROOT = ROOT / "packs"


def _slug_dir(slug: str) -> Path:
    path = PACKS_ROOT / slug
    if not path.is_dir():
        raise SystemExit(f"pack not found: {path}")
    return path


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):  # Windows cp932 対策
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("validate", "pull", "import"):
        p = sub.add_parser(name)
        p.add_argument("slug")
        if name == "import":
            p.add_argument("--creator", default=None, help="省略時は packops.lock.json から推定")
            p.add_argument("--content", default=None, help="省略時は lock か slug 名")
            p.add_argument("--force", action="store_true", help="乖離検出を無視して上書き配置")
    p = sub.add_parser("quality-check")
    p.add_argument("slug")
    p.add_argument("--mode", choices=("text", "tts"), required=True)
    p.add_argument("--file", required=True, help="パック JSON ファイル名（ドラフト源内）")
    p.add_argument("--max-issues", type=int, default=50)

    p = sub.add_parser("review", help="読み取り専用の教材レビュー補助検査（外部API不使用）")
    p.add_argument("target", help="packs配下のslug、またはパックJSONを含むディレクトリ")
    p.add_argument("--format", choices=("json", "markdown"), default="json")
    p.add_argument("--text-source", choices=("raw", "corrected"), default="raw",
                   help="録音対象の想定。correctedは補正がない箇所を本文にフォールバック")
    p.add_argument("--max-findings", type=int, default=200)

    args = parser.parse_args(argv)
    if args.command == "review":
        from app.services.pack_review import render_review_markdown, review_pack_dir

        target = Path(args.target)
        if not target.exists():
            target = PACKS_ROOT / target
        try:
            result = review_pack_dir(target, text_source=args.text_source, max_findings=args.max_findings)
        except (OSError, ValueError) as exc:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
            return 2
        print(render_review_markdown(result) if args.format == "markdown" else json.dumps(result, ensure_ascii=False, indent=2))
        # 法的所見・品質候補で公開を禁止しない。技術エラーだけ既存validateと同じ終了値にする。
        return 0 if result["existingValidation"]["valid"] else 1
    if args.command == "validate":
        result = packops.validate_pack_dir(_slug_dir(args.slug))
    elif args.command == "pull":
        result = packops.pull_pack_dir(_slug_dir(args.slug))
    elif args.command == "import":
        result = packops.import_pack_dir(
            _slug_dir(args.slug), creator_id=args.creator, content_id=args.content, force=args.force
        )
    else:
        result = packops.quality_check_pack_dir(
            _slug_dir(args.slug), mode=args.mode, pack_name=args.file, max_issues=args.max_issues
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.command == "validate" and not result["valid"]:
        print("validation failed (technical errors present)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
