#!/usr/bin/env python3
"""Run the condensation eval suite."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.condensation.eval import run_eval, print_report


async def main():
    schema_file = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    results = await run_eval(schema_file)
    print_report(results)
    failing = [r for r in results if r.overall_score < 0.7]
    sys.exit(1 if failing else 0)


if __name__ == "__main__":
    asyncio.run(main())
