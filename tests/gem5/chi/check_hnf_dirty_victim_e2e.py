#!/usr/bin/env python3
"""Verify the forced-small-SLC HNF dirty-victim end-to-end run.

The trace contract is intentionally machine readable.  A debug prefix (for
example, a tick and SimObject name) may precede a marker, but the marker and
its fields must have one of these exact forms::

    HNF_DV_START victim=V txn=T addr=A requester_src=S requester_txn=R data_hash=H
    SN_DV_DAT txn=T addr=A dbid=D bytes=64 data_hash=H
    SN_DV_COMP txn=T dbid=D error=0
    HNF_DV_RELEASE victim=V txn=T release_req=Q
    HNF_DV_REQUESTER_DONE src=S txn=R

Integer fields accept unsigned decimal or ``0x``-prefixed hexadecimal values.
``data_hash`` is hexadecimal, with an optional ``0x`` prefix.  ``D`` and ``Q``
must be nonzero, and the downstream ``T`` must differ from requester ``R``.
Multiple dirty
victim transactions may be interleaved; identifiers must be unique while a
transaction is live, and every transaction must form one complete ordered
chain with no duplicate or orphan marker.
"""

import argparse
import gzip
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Mapping, Sequence, Tuple


PASS_MARKER = "HNF_DIRTY_VICTIM_E2E_PASS"
FAIL_MARKER = "HNF_DIRTY_VICTIM_E2E_FAIL"
EXPECTED_ALIAS_CHECKSUM = int("50000001f0", 16)
EXPECTED_PRESSURE_CHECKSUM = int("2800000000ffc000", 16)


class CheckError(ValueError):
    """Raised when an input is well formed but fails the checker contract."""


@dataclass(frozen=True)
class TraceRecord:
    kind: str
    fields: Mapping[str, object]
    line_no: int


@dataclass
class DirtyVictimChain:
    start: TraceRecord
    dat: TraceRecord = None
    comp: TraceRecord = None
    release: TraceRecord = None
    requester_done: TraceRecord = None


@dataclass(frozen=True)
class ConfigEvidence:
    hnf_path: str
    bridge_path: str
    slc_path: str


_MARKER_FIELDS = {
    "HNF_DV_START": (
        "victim",
        "txn",
        "addr",
        "requester_src",
        "requester_txn",
        "data_hash",
    ),
    "SN_DV_DAT": ("txn", "addr", "dbid", "bytes", "data_hash"),
    "SN_DV_COMP": ("txn", "dbid", "error"),
    "HNF_DV_RELEASE": ("victim", "txn", "release_req"),
    "HNF_DV_REQUESTER_DONE": ("src", "txn"),
}
_HASH_FIELDS = {"data_hash"}
_UNSIGNED_RE = re.compile(r"(?:0[xX][0-9a-fA-F]+|[0-9]+)\Z")
_HASH_RE = re.compile(r"(?:0[xX])?([0-9a-fA-F]+)\Z")
_TRACE_MARKER_RE = re.compile(
    r"\b(" + "|".join(re.escape(name) for name in _MARKER_FIELDS) + r")\b(.*)\Z"
)
_ANY_DV_MARKER_RE = re.compile(r"\b(?:HNF|SN)_DV_[A-Z0-9_]+\b")


def _parse_unsigned(value: str, field: str, line_no: int) -> int:
    if not _UNSIGNED_RE.fullmatch(value):
        raise CheckError(
            "trace line {} has invalid unsigned {}={!r}".format(
                line_no, field, value
            )
        )
    return int(value, 16 if value.lower().startswith("0x") else 10)


def _parse_hash(value: str, line_no: int) -> str:
    match = _HASH_RE.fullmatch(value)
    if not match:
        raise CheckError(
            "trace line {} has invalid data_hash={!r}".format(line_no, value)
        )
    # Numeric normalization makes 0x00ab and AB comparable without weakening
    # the requirement that both producers report the same hash value.
    return format(int(match.group(1), 16), "x")


def parse_trace_lines(lines: Iterable[str]) -> List[TraceRecord]:
    """Parse all dirty-victim machine markers from *lines*.

    Non-marker debug lines are ignored.  A line containing a marker-family
    token that is unknown or malformed is rejected instead of silently lost.
    """

    records = []
    for line_no, raw_line in enumerate(lines, 1):
        line = raw_line.rstrip("\r\n")
        marker_match = _TRACE_MARKER_RE.search(line)
        family_match = _ANY_DV_MARKER_RE.search(line)
        if marker_match is None:
            if family_match is not None:
                raise CheckError(
                    "trace line {} has unknown dirty-victim marker {}".format(
                        line_no, family_match.group(0)
                    )
                )
            continue

        kind = marker_match.group(1)
        payload = marker_match.group(2).strip()
        pairs: Dict[str, str] = {}
        for token in payload.split():
            if token.count("=") != 1:
                raise CheckError(
                    "trace line {} has malformed {} field {!r}".format(
                        line_no, kind, token
                    )
                )
            key, value = token.split("=", 1)
            if not key or not value:
                raise CheckError(
                    "trace line {} has malformed {} field {!r}".format(
                        line_no, kind, token
                    )
                )
            if key in pairs:
                raise CheckError(
                    "trace line {} repeats {} field {}".format(
                        line_no, kind, key
                    )
                )
            pairs[key] = value

        required = set(_MARKER_FIELDS[kind])
        actual = set(pairs)
        if actual != required:
            missing = sorted(required - actual)
            extra = sorted(actual - required)
            raise CheckError(
                "trace line {} {} fields differ: missing={} extra={}".format(
                    line_no, kind, missing, extra
                )
            )

        parsed: Dict[str, object] = {}
        for field in _MARKER_FIELDS[kind]:
            if field in _HASH_FIELDS:
                parsed[field] = _parse_hash(pairs[field], line_no)
            else:
                parsed[field] = _parse_unsigned(
                    pairs[field], field, line_no
                )
        records.append(TraceRecord(kind, parsed, line_no))
    return records


def parse_trace_text(text: str) -> List[TraceRecord]:
    return parse_trace_lines(text.splitlines())


def validate_trace(records: Sequence[TraceRecord]) -> List[DirtyVictimChain]:
    """Validate correlation, uniqueness, ordering, payload, and completion."""

    active_txns: Dict[int, DirtyVictimChain] = {}
    active_victims: Dict[int, DirtyVictimChain] = {}
    pending_requesters: Dict[Tuple[int, int], DirtyVictimChain] = {}
    completed: List[DirtyVictimChain] = []

    def finish_if_ready(chain: DirtyVictimChain) -> None:
        # Requester completion and VictimBuffer release are independent after
        # downstream Comp.  Accept either order, but complete the evidence
        # chain exactly once only when both have arrived.
        if chain.release is not None and chain.requester_done is not None:
            completed.append(chain)

    for record in records:
        fields = record.fields
        if record.kind == "HNF_DV_START":
            txn = fields["txn"]
            victim = fields["victim"]
            requester = (fields["requester_src"], fields["requester_txn"])
            if txn in active_txns:
                raise CheckError(
                    "trace line {} reuses live downstream txn {}".format(
                        record.line_no, txn
                    )
                )
            if victim in active_victims:
                raise CheckError(
                    "trace line {} reuses live victim {}".format(
                        record.line_no, victim
                    )
                )
            if requester in pending_requesters:
                raise CheckError(
                    "trace line {} reuses unfinished requester src={} txn={}".format(
                        record.line_no, requester[0], requester[1]
                    )
                )
            if fields["addr"] % 64 != 0:
                raise CheckError(
                    "trace line {} dirty-victim address {:#x} is not 64-byte aligned".format(
                        record.line_no, fields["addr"]
                    )
                )
            if txn == fields["requester_txn"]:
                raise CheckError(
                    "trace line {} downstream txn {} equals requester txn".format(
                        record.line_no, txn
                    )
                )
            chain = DirtyVictimChain(start=record)
            active_txns[txn] = chain
            active_victims[victim] = chain
            pending_requesters[requester] = chain
            continue

        if record.kind == "HNF_DV_REQUESTER_DONE":
            requester = (fields["src"], fields["txn"])
            chain = pending_requesters.get(requester)
            if chain is None:
                raise CheckError(
                    "trace line {} has orphan/duplicate requester completion "
                    "src={} txn={}".format(
                        record.line_no, requester[0], requester[1]
                    )
                )
            chain.requester_done = record
            del pending_requesters[requester]
            finish_if_ready(chain)
            continue

        txn = fields["txn"]
        chain = active_txns.get(txn)
        if chain is None:
            raise CheckError(
                "trace line {} has orphan/duplicate {} for downstream txn {}".format(
                    record.line_no, record.kind, txn
                )
            )
        start_fields = chain.start.fields

        if record.kind == "SN_DV_DAT":
            if chain.dat is not None:
                raise CheckError(
                    "trace line {} duplicates DAT for downstream txn {}".format(
                        record.line_no, txn
                    )
                )
            if fields["bytes"] != 64:
                raise CheckError(
                    "trace line {} DAT for txn {} has {} bytes, expected 64".format(
                        record.line_no, txn, fields["bytes"]
                    )
                )
            if fields["dbid"] == 0:
                raise CheckError(
                    "trace line {} DAT for txn {} has zero DBID".format(
                        record.line_no, txn
                    )
                )
            if fields["addr"] != start_fields["addr"]:
                raise CheckError(
                    "trace line {} DAT address {:#x} does not match START {:#x}".format(
                        record.line_no, fields["addr"], start_fields["addr"]
                    )
                )
            if fields["data_hash"] != start_fields["data_hash"]:
                raise CheckError(
                    "trace line {} DAT hash {} does not match START {}".format(
                        record.line_no,
                        fields["data_hash"],
                        start_fields["data_hash"],
                    )
                )
            chain.dat = record
        elif record.kind == "SN_DV_COMP":
            if chain.dat is None:
                raise CheckError(
                    "trace line {} completes downstream txn {} before DAT".format(
                        record.line_no, txn
                    )
                )
            if chain.comp is not None:
                raise CheckError(
                    "trace line {} duplicates Comp for downstream txn {}".format(
                        record.line_no, txn
                    )
                )
            if fields["dbid"] != chain.dat.fields["dbid"]:
                raise CheckError(
                    "trace line {} Comp DBID {} does not match DAT DBID {}".format(
                        record.line_no, fields["dbid"], chain.dat.fields["dbid"]
                    )
                )
            if fields["error"] != 0:
                raise CheckError(
                    "trace line {} downstream txn {} completed with error {}".format(
                        record.line_no, txn, fields["error"]
                    )
                )
            chain.comp = record
        elif record.kind == "HNF_DV_RELEASE":
            if chain.comp is None:
                raise CheckError(
                    "trace line {} releases downstream txn {} before Comp".format(
                        record.line_no, txn
                    )
                )
            if chain.release is not None:
                raise CheckError(
                    "trace line {} duplicates release for downstream txn {}".format(
                        record.line_no, txn
                    )
                )
            if fields["victim"] != start_fields["victim"]:
                raise CheckError(
                    "trace line {} release victim {} does not match START {}".format(
                        record.line_no,
                        fields["victim"],
                        start_fields["victim"],
                    )
                )
            if fields["release_req"] == 0:
                raise CheckError(
                    "trace line {} release_req must be nonzero".format(
                        record.line_no
                    )
                )
            chain.release = record
            del active_txns[txn]
            del active_victims[start_fields["victim"]]
            finish_if_ready(chain)
        else:  # Defensive: parse_trace_lines only creates known records.
            raise CheckError("unsupported trace marker {}".format(record.kind))

    if not records:
        raise CheckError("trace contains no dirty-victim machine markers")
    if active_txns:
        raise CheckError(
            "trace ends with active downstream txn(s): {}".format(
                sorted(active_txns)
            )
        )
    if pending_requesters:
        requesters = [
            "{}:{}".format(src, txn)
            for src, txn in sorted(pending_requesters)
        ]
        raise CheckError(
            "trace ends without requester completion(s): {}".format(requesters)
        )
    if not completed:
        raise CheckError("trace contains no complete dirty-victim transaction")
    return completed


def _walk_dicts(value: object, path: str = "$") -> Iterator[Tuple[str, dict]]:
    if isinstance(value, dict):
        yield path, value
        for key, child in value.items():
            yield from _walk_dicts(child, "{}.{}".format(path, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_dicts(child, "{}[{}]".format(path, index))


def _config_int(node: Mapping[str, object], key: str, path: str) -> int:
    value = node.get(key)
    if isinstance(value, bool):
        raise CheckError("{}.{!s} must be an integer".format(path, key))
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise CheckError("{}.{!s} must be an integer".format(path, key))
    return result


def validate_config(config: object) -> ConfigEvidence:
    """Confirm the real 2x2 HNF/SN objects and effective 64x1 SLC."""

    objects = list(_walk_dicts(config))
    all_hnfs = [item for item in objects if item[1].get("type") == "HomeNodeFull"]
    if not all_hnfs:
        raise CheckError("config has no HomeNodeFull object")

    active_hnfs = [item for item in all_hnfs if item[1].get("node_type") == "hnf"]
    # Small synthetic config fixtures and older single-HNF dumps may omit
    # node_type.  Multiple objects require an explicit active-HNF identity so
    # stale placeholders cannot accidentally satisfy the geometry check.
    if not active_hnfs and len(all_hnfs) == 1:
        active_hnfs = all_hnfs
    if len(active_hnfs) != 1:
        raise CheckError(
            "config must identify exactly one active HomeNodeFull; found {}".format(
                len(active_hnfs)
            )
        )
    hnf_path, hnf = active_hnfs[0]
    if hnf.get("direct_sn_fake_data") is not False:
        raise CheckError(
            "{} must set direct_sn_fake_data=false to use the real SN bridge".format(
                hnf_path
            )
        )

    bridges = [
        item for item in objects if item[1].get("type") == "Chi2ClassicMemBridge"
    ]
    if len(bridges) != 1:
        raise CheckError(
            "config must contain exactly one Chi2ClassicMemBridge; found {}".format(
                len(bridges)
            )
        )
    bridge_path, _bridge = bridges[0]

    child = hnf.get("slcsf")
    if isinstance(child, dict):
        slc_node = child
        slc_path = "{}.slcsf".format(hnf_path)
    else:
        # Compatibility with the stage-A embedded configuration.  Once US-029
        # emits the child in config.json, the child is deliberately preferred
        # as the single source of the final runtime geometry.
        slc_node = hnf
        slc_path = hnf_path
    sets = _config_int(slc_node, "slc_num_sets", slc_path)
    ways = _config_int(slc_node, "slc_num_ways", slc_path)
    if (sets, ways) != (64, 1):
        raise CheckError(
            "{} final SLC geometry is {}x{}, expected 64x1".format(
                slc_path, sets, ways
            )
        )
    return ConfigEvidence(hnf_path, bridge_path, slc_path)


_BEGIN_RE = re.compile(
    r"\bCHI_LITMUS_BEGIN\s+harts=([0-9]+)\s+iterations=([0-9]+)\s*\Z"
)
_TEST_RE = re.compile(
    r"\bCHI_LITMUS_TEST\s+id=([0-9]+)\s+errors=([0-9]+)\s*\Z"
)
_SUM_RE = re.compile(
    r"\bCHI_LITMUS_SUM\s+hart=([0-9]+)\s+alias=([0-9a-fA-F]+)"
    r"\s+pressure=([0-9a-fA-F]+)\s*\Z"
)
_BAD_RE = re.compile(
    r"\bCHI_LITMUS_BAD\s+hart=([0-9]+)\s+count=([0-9]+)"
    r"\s+owner=([0-9]+)\s+index=([0-9]+)"
    r"\s+expected=([0-9a-fA-F]+)\s+actual=([0-9a-fA-F]+)\s*\Z"
)
_OUTCOME_RE = re.compile(
    r"\bCHI_LITMUS_(PASS|FAIL)\s+total_errors=([0-9]+)\s*\Z"
)


def _one_by_key(entries: Sequence[tuple], expected_keys: set, label: str) -> dict:
    result = {}
    for entry in entries:
        key = entry[0]
        if key in result:
            raise CheckError("simout repeats {} {}".format(label, key))
        result[key] = entry[1:]
    if set(result) != expected_keys:
        raise CheckError(
            "simout {} keys are {}, expected {}".format(
                label, sorted(result), sorted(expected_keys)
            )
        )
    return result


def validate_simout_text(text: str) -> None:
    """Validate all seven litmus tests and the absolute data oracles."""

    begins = []
    tests = []
    sums = []
    bads = []
    outcomes = []
    known_prefixes = {
        "CHI_LITMUS_BEGIN": _BEGIN_RE,
        "CHI_LITMUS_TEST": _TEST_RE,
        "CHI_LITMUS_SUM": _SUM_RE,
        "CHI_LITMUS_BAD": _BAD_RE,
    }

    for line_no, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.rstrip("\r\n")
        matched_prefix = False
        for prefix, pattern in known_prefixes.items():
            if prefix not in line:
                continue
            matched_prefix = True
            match = pattern.search(line)
            if not match:
                raise CheckError(
                    "simout line {} has malformed {} marker".format(
                        line_no, prefix
                    )
                )
            if prefix == "CHI_LITMUS_BEGIN":
                values = tuple(int(value, 10) for value in match.groups())
                begins.append(values)
            elif prefix == "CHI_LITMUS_TEST":
                values = tuple(int(value, 10) for value in match.groups())
                tests.append(values)
            elif prefix == "CHI_LITMUS_SUM":
                groups = match.groups()
                values = (
                    int(groups[0], 10),
                    int(groups[1], 16),
                    int(groups[2], 16),
                )
                sums.append(values)
            else:
                groups = match.groups()
                values = (
                    int(groups[0], 10),
                    int(groups[1], 10),
                    int(groups[2], 10),
                    int(groups[3], 10),
                    int(groups[4], 16),
                    int(groups[5], 16),
                )
                bads.append(values)
            break
        if matched_prefix:
            continue
        if "CHI_LITMUS_PASS" in line or "CHI_LITMUS_FAIL" in line:
            match = _OUTCOME_RE.search(line)
            if not match:
                raise CheckError(
                    "simout line {} has malformed CHI_LITMUS outcome".format(
                        line_no
                    )
                )
            outcomes.append((match.group(1), int(match.group(2))))

    if begins != [(4, 16)]:
        raise CheckError(
            "simout must contain exactly one CHI_LITMUS_BEGIN harts=4 iterations=16"
        )

    test_map = _one_by_key(tests, set(range(7)), "test id")
    for test_id, (errors,) in test_map.items():
        if errors != 0:
            raise CheckError(
                "CHI litmus test {} reports {} error(s)".format(test_id, errors)
            )

    sum_map = _one_by_key(sums, set(range(4)), "checksum hart")
    for hart, (alias, pressure) in sum_map.items():
        if alias != EXPECTED_ALIAS_CHECKSUM:
            raise CheckError(
                "hart {} alias checksum is {:x}, expected {:x}".format(
                    hart, alias, EXPECTED_ALIAS_CHECKSUM
                )
            )
        if pressure != EXPECTED_PRESSURE_CHECKSUM:
            raise CheckError(
                "hart {} pressure checksum is {:x}, expected {:x}".format(
                    hart, pressure, EXPECTED_PRESSURE_CHECKSUM
                )
            )

    bad_map = _one_by_key(bads, set(range(4)), "BAD hart")
    for hart, diagnostic in bad_map.items():
        count, owner, index, expected, actual = diagnostic
        if (count, owner, index, expected, actual) != (0, 0, 0, 0, 0):
            raise CheckError(
                "hart {} BAD diagnostic is not all zero: {}".format(
                    hart, diagnostic
                )
            )

    if outcomes != [("PASS", 0)]:
        raise CheckError(
            "simout must contain exactly one CHI_LITMUS_PASS total_errors=0"
        )


def _read_text_auto(path: Path) -> str:
    """Read UTF-8 text, detecting gzip by its magic bytes rather than suffix."""

    with path.open("rb") as stream:
        magic = stream.read(2)
    opener = gzip.open if magic == b"\x1f\x8b" else open
    with opener(path, "rt", encoding="utf-8") as stream:
        return stream.read()


def verify_files(
    config_json: Path, simout: Path, trace: Path
) -> List[DirtyVictimChain]:
    with config_json.open("r", encoding="utf-8") as stream:
        config = json.load(stream)
    validate_config(config)
    validate_simout_text(_read_text_auto(simout))
    records = parse_trace_text(_read_text_auto(trace))
    return validate_trace(records)


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-json", required=True, type=Path)
    parser.add_argument("--simout", required=True, type=Path)
    parser.add_argument("--trace", required=True, type=Path)
    return parser


def main(argv: Sequence[str] = None) -> int:
    args = _argument_parser().parse_args(argv)
    try:
        chains = verify_files(args.config_json, args.simout, args.trace)
    except (CheckError, OSError, UnicodeError, json.JSONDecodeError) as error:
        print("{}: {}".format(FAIL_MARKER, error), file=sys.stderr)
        return 1

    first = chains[0].start.fields
    print(
        "{} chains={} victim={} txn={} addr={:#x} data_hash={}".format(
            PASS_MARKER,
            len(chains),
            first["victim"],
            first["txn"],
            first["addr"],
            first["data_hash"],
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
