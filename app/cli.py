from __future__ import annotations

import argparse
from pathlib import Path

from .agent import FacilityInspectionAgent
from .config import settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze one facility image.")
    parser.add_argument("image", type=Path)
    args = parser.parse_args()

    result = FacilityInspectionAgent(settings).analyze(args.image.read_bytes())
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
