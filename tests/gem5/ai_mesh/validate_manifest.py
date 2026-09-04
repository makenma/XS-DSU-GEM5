#!/usr/bin/env python3

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "util/mesh_ir"))

from mesh_ir.acceptance import read_yaml_document, validate_manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "manifest",
        nargs="?",
        default=str(Path(__file__).with_name("mandatory_case_manifest.yaml")),
    )
    arguments = parser.parse_args()
    document = read_yaml_document(Path(arguments.manifest))
    digest = validate_manifest(document)
    subcases = sum(len(case["subcases"]) for case in document["cases"])
    print(f"PASS: 150 logical IDs, {subcases} subcases, digest={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
