import argparse
import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MARS — Multi-Agent Research System")
    parser.add_argument("--topic", required=True, help="Research topic")
    parser.add_argument(
        "--output",
        default="output/report.md",
        help="Output path for the generated report (default: output/report.md)",
    )
    parser.add_argument(
        "--docs",
        nargs="*",
        default=[],
        help="Optional document file paths to include in analysis",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="Max parallel subagent calls (default: 1). Increase for higher API rate limit tiers.",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()

    from mars.coordinator import Coordinator

    coordinator = Coordinator(max_concurrency=args.concurrency)
    report = await coordinator.run(topic=args.topic, doc_paths=args.docs)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    print(f"Report saved to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
