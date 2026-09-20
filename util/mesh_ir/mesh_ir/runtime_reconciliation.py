"""Reusable runtime reconciliation entry shared by the config and the tests.

The expected execution plan is built from the admitted descriptor groups, the
dispatched ``(command, generation)`` plan and the instance set; the archived
terminals, command lifecycle observations and descriptor executions only verify
that plan.  Every execution is checked for its own status, completion, commit,
bytes, bursts and payload, the transport row is the sum of those executions, and
the peer transfer observation independently owns the committed peer set.  The
mock run is compared with the oracle published with the program, never with the
actual totals.
"""

from __future__ import annotations

import collections
import hashlib


class ReconciliationError(RuntimeError):
    pass


_TERMINAL_STATES = ("completed", "errored", "cancelled")

_DMA_ERROR_STATUS = {
    1: "AXI_READ_ERROR",
    2: "AXI_WRITE_ERROR",
    3: "AXI_WRITE_ERROR",
    4: "AXI_READ_ERROR",
    5: "AXI_WRITE_ERROR",
}


def payload_digest(data: bytes) -> str:
    h0 = 0xCBF29CE484222325
    h1 = 0x9E3779B97F4A7C15
    for byte in data:
        h0 = ((h0 ^ byte) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
        h1 = ((h1 + ((h0 >> 31) ^ byte)) * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
    return f"{h0:016x}-{h1:016x}"


def _require(condition, message):
    if not condition:
        raise ReconciliationError(message)


def _fill_pattern(attrs_by_index, commands, command_id):
    for command in commands:
        if command["command_id"] == command_id:
            value = attrs_by_index.get(command["attr_index"], {}).get("pattern", 0)
            return int(value, 16) if isinstance(value, str) else value
    return 0


def _admitted_descriptors(schedule):
    return {
        row["descriptor_id"]: row for row in schedule["sections"]["DMA_DESCRIPTORS"]
    }


def _execution_oracle(expected_rows, admitted):
    oracle = {}
    for row in expected_rows:
        descriptor_id = row["descriptor_id"]
        descriptor = admitted[descriptor_id]
        executions = row["execution_count"] or 1
        execution_bytes = row["row_bytes"] * row["rows"]
        _require(
            executions > 0
            and row["bursts"] % executions == 0
            and row["useful_bytes"] == execution_bytes * executions,
            "published oracle for descriptor %d does not divide into executions"
            % descriptor_id,
        )
        oracle[descriptor_id] = {
            "descriptor_id": descriptor_id,
            "kind": descriptor["kind"],
            "command_id": descriptor["command_id"],
            "owner_core": descriptor["owner_core"],
            "transfer_id": descriptor.get("transfer_id", 0),
            "execution_bytes": execution_bytes,
            "execution_bursts": row["bursts"] // executions,
        }
    return oracle


def _dispatched_plan(result):
    plan = {}
    for core in result["cores"]:
        core_id = core["core_id"]
        _require(
            core_id not in plan,
            "dispatched plan duplicates core identity %r" % (core_id,),
        )
        entries = [
            (command_id, generation)
            for command_id, generation in core["dispatch_plan"]
        ]
        _require(
            len(set(entries)) == len(entries),
            "core %r dispatch plan duplicates a command generation: %s"
            % (core_id, entries),
        )
        plan[core_id] = entries
    return plan


def _expected_execution_transfer(kind, size, bursts):
    transfer = {field: 0 for field in _TRAFFIC_FIELDS}
    if kind == 5:
        transfer["fill_bytes"] = size
        return transfer
    byte_field, burst_field = _AXI_DIRECTION_FIELDS[kind]
    transfer[byte_field] = size
    transfer[burst_field] = bursts
    return transfer


# The DMA direction class of each descriptor kind: LOAD and PREFETCH read,
# STORE writes, P2P pushes into a peer aperture.  Both the admitted execution
# oracle and the fault model name a burst through this one table, so a faulted
# execution is never measured on the wrong direction's field.
_AXI_DIRECTION_FIELDS = {
    1: ("read_bytes", "read_bursts"),
    2: ("write_bytes", "write_bursts"),
    3: ("p2p_bytes", "p2p_bursts"),
    4: ("read_bytes", "read_bursts"),
}

_BYTE_FIELDS = ("read_bytes", "write_bytes", "p2p_bytes", "fill_bytes")
_BURST_FIELDS = ("read_bursts", "write_bursts", "p2p_bursts")

_TRAFFIC_FIELDS = (
    "read_bytes",
    "write_bytes",
    "p2p_bytes",
    "fill_bytes",
    "read_bursts",
    "write_bursts",
    "p2p_bursts",
)


def _empty_transfer(transfer):
    return not any(transfer.get(field, 0) for field in _TRAFFIC_FIELDS)


def fill_pattern_bytes(attrs_by_index, commands, command_id, size):
    """The declared LOCAL_FILL payload of a command: the little-endian 64-bit
    pattern repeated across the descriptor payload."""
    pattern = _fill_pattern(attrs_by_index, commands, command_id)
    return bytes((pattern >> (8 * (index % 8))) & 0xFF for index in range(size))


def _payload_oracle(kind, size, pattern):
    if kind == 5:
        return payload_digest(
            bytes((pattern >> (8 * (index % 8))) & 0xFF for index in range(size))
        )
    if kind in (1, 4):
        return payload_digest(bytes(size))
    return None


def _read_payload_oracle(descriptor_id, kind, read_payload_digests):
    """Declared-source payload of a read descriptor.

    The mock HBM backing returns zero bytes, so its read oracle is the zero
    digest.  A real backing is seeded before cycle 0, so the runner passes the
    digest of the declared source bytes per read descriptor instead.
    """
    if kind not in (1, 4) or read_payload_digests is None:
        return None
    digest = read_payload_digests.get(descriptor_id)
    _require(
        digest is not None,
        "read descriptor %d has no declared source payload" % descriptor_id,
    )
    return digest


# Every field a loader control-plane snapshot must carry.  The install boundary
# is only proven when both snapshots state the same complete owner fact set: an
# empty or thinned pair is a missing measurement, not a zero-traffic result.
LOADER_SNAPSHOT_FIELDS = (
    "ar_accepted",
    "aw_accepted",
    "w_accepted",
    "r_beats",
    "b_consumed",
    "b_errors",
    "r_errors",
    "ni_queued_flits",
    "ni_queued_messages",
    "router_buffered_flits",
    "non_idle_input_vcs",
    "non_idle_output_vcs",
    "data_link_pending_flits",
    "credit_link_pending_credits",
    "bridge_pending_items",
    "credit_deficit",
    "packets_injected",
    "packets_received",
    "flits_injected",
    "flits_received",
)

# Cumulative fields can only grow; a gauge may move either way.
LOADER_CUMULATIVE_FIELDS = (
    "ar_accepted",
    "aw_accepted",
    "w_accepted",
    "r_beats",
    "b_consumed",
    "b_errors",
    "r_errors",
    "packets_injected",
    "packets_received",
    "flits_injected",
    "flits_received",
)


def loader_control_plane_delta(window):
    """Fields the loader install moved, as {field: (begin, end)}.

    TORCH-NORM-00A requires an empty result: the control plane installs with
    no packet, flit or AXI beat moved.  A missing window is a failure, not an
    absent measurement, and so is a snapshot pair that dropped a required owner
    field or a cumulative counter that went backwards.
    """
    _require(
        isinstance(window, dict) and window.get("captured"),
        "loader control-plane window is missing or was not captured",
    )
    begin = window.get("begin")
    end = window.get("end")
    _require(
        isinstance(begin, dict) and isinstance(end, dict)
        and begin.keys() == end.keys(),
        "loader control-plane window has no comparable snapshot pair",
    )
    missing = sorted(set(LOADER_SNAPSHOT_FIELDS) - set(begin))
    _require(
        not missing,
        "loader control-plane snapshot dropped the required fields %s" % missing,
    )
    for field in LOADER_SNAPSHOT_FIELDS:
        for value in (begin[field], end[field]):
            _require(
                isinstance(value, int) and not isinstance(value, bool) and value >= 0,
                "loader control-plane field %s is %r" % (field, value),
            )
    for field in LOADER_CUMULATIVE_FIELDS:
        _require(
            end[field] >= begin[field],
            "loader control-plane counter %s went backwards: %s -> %s"
            % (field, begin[field], end[field]),
        )
    _require(
        window.get("end_tick", 0) >= window.get("begin_tick", 0),
        "loader control-plane window ticks are inverted",
    )
    return {
        field: (begin[field], end[field])
        for field in begin
        if end[field] != begin[field]
    }


def expected_garnet_traffic(descriptors, instances):
    """The admitted packet/flit expectation of a run, per vnet.

    G5-R23-05: ``descriptors`` are the admitted oracle rows with their compiled
    per-channel packetizer projection; the totals are the sum over every
    execution the program plans, so they are independent of what the network
    happened to count.
    """
    _require(isinstance(descriptors, dict) and descriptors,
             "no admitted descriptor to derive the packet expectation from")
    _require(isinstance(instances, int) and instances > 0,
             "the instance count must be positive")
    per_vnet = {}
    for row in descriptors.values():
        executions = (row.get("execution_count") or 1) * instances
        for channel in row.get("channels") or ():
            vnet = channel["vnet"]
            entry = per_vnet.setdefault(
                vnet, {"messages": 0, "packets": 0, "flits": 0, "wire_bytes": 0}
            )
            for field in ("messages", "packets", "flits", "wire_bytes"):
                entry[field] += (channel.get(field) or 0) * executions
    return per_vnet


def verified_garnet_traffic(traffic, expected, *, flit_bytes, vnets):
    """The Garnet traffic a run really produced, against the admitted plan.

    G5-R23-05: the archived per-vnet injected/received packets and flits must
    cover every virtual network, be internally consistent (nothing is received
    that was not injected) and equal the independently derived expectation of
    the admitted plan.  The expectation is normalized over the full legal vnet
    set with a zero baseline, so a vnet the plan never uses must carry exactly
    zero traffic: extra packets on an unplanned vnet are as fatal as a missing
    group.  A thinned field set, a negative or non-integer counter and a
    cross-instance substitution are all rejected.
    """
    _require(isinstance(traffic, dict), "the result archived no Garnet traffic")
    for field in ("flit_bytes", "vnets", "injected", "received"):
        _require(field in traffic,
                 "the Garnet traffic report dropped the %s field" % field)
    _require(
        traffic["flit_bytes"] == flit_bytes,
        "the Garnet traffic uses %s-byte flits, the admitted fabric uses %s"
        % (traffic["flit_bytes"], flit_bytes),
    )
    _require(
        traffic["vnets"] == vnets,
        "the Garnet traffic reports %s virtual networks, the admitted fabric "
        "has %s" % (traffic["vnets"], vnets),
    )
    counts = {}
    for direction in ("injected", "received"):
        block = traffic[direction]
        _require(
            isinstance(block, dict) and set(block) == {"packets", "flits"},
            "the %s Garnet traffic is %r" % (direction, block),
        )
        for field in ("packets", "flits"):
            values = block[field]
            _require(
                isinstance(values, list) and len(values) == vnets,
                "the %s %s counters cover %s of %s virtual networks"
                % (direction, field, len(values) if isinstance(values, list)
                   else values, vnets),
            )
            for vnet, value in enumerate(values):
                _require(
                    isinstance(value, int) and not isinstance(value, bool)
                    and value >= 0,
                    "%s %s of vnet %s is %r" % (direction, field, vnet, value),
                )
            counts[(direction, field)] = values
    for vnet in range(vnets):
        for field in ("packets", "flits"):
            injected = counts[("injected", field)][vnet]
            received = counts[("received", field)][vnet]
            _require(
                received == injected,
                "vnet %s injected %s %s but received %s"
                % (vnet, injected, field, received),
            )
    plan = expected or {}
    for vnet in sorted(plan):
        _require(
            0 <= vnet < vnets,
            "the admitted plan uses vnet %s, the fabric has %s" % (vnet, vnets),
        )
    for vnet in range(vnets):
        want = plan.get(vnet) or {"packets": 0, "flits": 0}
        for field in ("packets", "flits"):
            got = counts[("injected", field)][vnet]
            _require(
                got == want.get(field, 0),
                "vnet %s injected %s %s, the admitted plan expects %s"
                % (vnet, got, field, want.get(field, 0)),
            )
    return {"vnets": vnets,
            "packets": sum(plan.get(vnet, {}).get("packets", 0)
                           for vnet in range(vnets))}


def verified_read_window(bursts, window, bridges, *, limit, max_beats, beat_bytes):
    """The admitted read window really slides, and every owner agrees on it.

    G5-02/G5-03: the window fills with exactly the admitted number of ARs before
    the first RLAST returns, refills at that RLAST (not at the local SRAM commit),
    reuses IDs only after their RLAST, and never exceeds the admitted window.
    ``window`` is the engine/bridge read-window observation, so the engine's own
    peak and the bridge's accounting must agree, and the first credit release must
    be the earliest RLAST rather than a local commit.
    """
    _require(isinstance(bursts, list) and bursts, "missing per-burst evidence")
    _require(isinstance(window, dict), "missing the read-window observation")
    verified_burst_geometry(bursts, max_beats=max_beats, beat_bytes=beat_bytes)
    reads = sorted(
        (row for row in bursts if row["channel"] == "AR"),
        key=lambda row: (row["ar_aw_tick"], row["ordinal"]),
    )
    _require(len(reads) > limit, "missing refill burst")
    _require(
        len({row["core_id"] for row in reads}) == 1,
        "read-window stimulus must use one initiator",
    )
    first = reads[0]
    release = min(row["response_tick"] for row in reads)
    _require(release > 0 and first["commit_tick"] > 0,
             "missing RLAST or first burst commit")
    _require(
        sum(row["ar_aw_tick"] < release for row in reads) == limit,
        "first RLAST must follow exactly the configured number of ARs",
    )
    refill = reads[limit]
    _require(
        release <= refill["ar_aw_tick"] < first["commit_tick"],
        "the refill must precede the first burst SRAM commit",
    )
    _require(refill["axi_id"] == first["axi_id"],
             "the refill did not reuse the first ID")
    _require(
        len({row["axi_id"] for row in reads}) == limit,
        "the read-window stimulus did not use a bounded ID pool",
    )
    previous = {}
    for row in reads:
        prior = previous.get(row["axi_id"])
        _require(
            prior is None or row["ar_aw_tick"] >= prior["response_tick"],
            "an ID was reused before its RLAST was consumed",
        )
        previous[row["axi_id"]] = row
    peaks = [bridge.get("peak_read_outstanding", 0) for bridge in bridges]
    _require(peaks and max(peaks) == limit,
             "the adapter read window peak differs from the admitted window")
    _require(
        window.get("segment_peak") == limit,
        "the engine read window peaked at %s over the admitted %s"
        % (window.get("segment_peak"), limit),
    )
    accepts = list(window.get("ar_accept_ticks") or ())
    _require(
        accepts == sorted(row["ar_aw_tick"] for row in reads),
        "the bridge AR accepts differ from the engine's accepted bursts",
    )
    credit_release = window.get("first_credit_release_tick")
    _require(
        credit_release == release,
        "the first credit was released at %s, the earliest RLAST is %s"
        % (credit_release, release),
    )
    return {
        "bursts": len(reads),
        "limit": limit,
        "credit_release_tick": credit_release,
    }


def verified_store_destinations(result, stores):
    """Every STORE destination holds exactly the payload its own execution wrote.

    G5-01/E2E-1: ``stores`` are the admitted STORE oracle rows; the destination is
    the admitted one and contiguous, the verified span is exactly that span, and
    the last instance's own execution wrote it.  The LOAD content and the
    per-descriptor row content belong to the read oracle and the producer-content
    entry, so this entry owns only the destination landing.
    """
    _require(isinstance(stores, list) and stores,
             "no admitted STORE descriptor to check its destination")
    frames = result.get("instances") or []
    _require(frames, "the result archived no instance frame")
    last = frames[-1]
    verify_rows = {
        row["address"]: row
        for row in (result.get("memory_endpoint") or {}).get("verifies", [])
    }
    _require(verify_rows, "the store destination range was not verified")
    landed = 0
    for oracle in stores:
        descriptor_id = oracle["descriptor_id"]
        _require(
            oracle["remote_stride_bytes"] == oracle["row_bytes"],
            "descriptor %s writes a strided destination; its landing is owned by "
            "the row-level content check" % descriptor_id,
        )
        span = oracle["rows"] * oracle["row_bytes"]
        row = verify_rows.get(oracle["dst_address"])
        _require(
            row is not None,
            "the destination %#x of descriptor %s was not verified"
            % (oracle["dst_address"], descriptor_id),
        )
        _require(
            row.get("size") == span,
            "the verified span at %#x is %s bytes, the admitted destination of "
            "descriptor %s is %s"
            % (oracle["dst_address"], row.get("size"), descriptor_id, span),
        )
        executions = [
            execution
            for ledger in last["cores"].values()
            for execution in ledger["observations"]["descriptor_executions"]
            if execution["descriptor_id"] == descriptor_id
        ]
        _require(
            len(executions) == 1,
            "descriptor %s has %s executions in the last frame"
            % (descriptor_id, len(executions)),
        )
        execution = executions[0]
        _require(
            execution["committed"] and execution["status"] == "OK",
            "the last execution of descriptor %s did not commit: %s"
            % (descriptor_id, execution),
        )
        written = execution["transfer"].get("payload_digest")
        _require(
            written and row.get("digest") == written,
            "the destination at %#x holds %s, but its own execution wrote %s"
            % (oracle["dst_address"], row.get("digest"), written),
        )
        landed += 1
    return landed


def verified_post_commit_landing(result, *, admitted_burst_bytes):
    """A post-commit B error landed its bytes and still failed the source.

    G5-14: the errored descriptor must not claim a commit, the destination must
    hold exactly the bytes that execution read from its producer, the target must
    count those bytes as committed, and exactly the admitted burst failed rather
    than the whole transfer.  Returns the landed byte count.
    """
    errored = [
        row
        for frame in result.get("instances") or []
        for ledger in frame["cores"].values()
        for row in ledger["observations"]["descriptor_executions"]
        if row["status"] != "OK"
    ]
    _require(
        len(errored) == 1,
        "the post-commit carrier must error exactly one descriptor, not %s"
        % [(row["descriptor_id"], row["status"]) for row in errored],
    )
    row = errored[0]
    _require(row["status"] == "AXI_WRITE_ERROR",
             "the errored descriptor reports %s" % row["status"])
    _require(
        not row["committed"] and row["commit_tick"] is None,
        "an errored descriptor claimed a source commit: %s" % row,
    )
    _require(row["source_rows"], "the errored descriptor has no source rows")
    produced = b"".join(
        bytes.fromhex(source["bytes_hex"]) for source in row["source_rows"]
    )
    _require(
        len(produced) == sum(source["size"] for source in row["source_rows"]),
        "the source rows of descriptor %s carry %s bytes over %s declared"
        % (row["descriptor_id"], len(produced),
           sum(source["size"] for source in row["source_rows"])),
    )
    endpoint = result.get("memory_endpoint") or {}
    verifies = endpoint.get("verifies") or []
    _require(len(verifies) == 1,
             "the post-commit carrier verified %s destinations" % len(verifies))
    landed = bytes.fromhex(verifies[0].get("bytes_hex") or "")
    _require(
        len(landed) == len(produced),
        "the destination holds %s bytes, the execution produced %s"
        % (len(landed), len(produced)),
    )
    _require(
        landed == produced,
        "the post-commit landing differs from the bytes the DMA read",
    )
    _require(
        endpoint.get("committed_valid_bytes") == len(produced),
        "the target counted %s committed bytes but landed %s"
        % (endpoint.get("committed_valid_bytes"), len(produced)),
    )
    failed = len(produced) - row["transfer"]["write_bytes"]
    _require(
        failed == admitted_burst_bytes,
        "the failing span covers %s bytes, the admitted burst is %s"
        % (failed, admitted_burst_bytes),
    )
    return {"landed_bytes": len(landed), "failed_bytes": failed}


def verified_burst_geometry(rows, *, max_beats, beat_bytes, admitted=None):
    """Every admitted AXI burst stays inside one 4 KiB page and its beat cap.

    ``rows`` is the archived per-burst evidence of one direction.  A burst that
    crossed a page boundary or exceeded the admitted beat cap would mean the
    engine split the payload differently than the admitted plan, which is what
    makes the unaligned head, the row padding and the page split checkable.
    ``admitted`` are the admitted ``(address, beats)`` pairs of that direction:
    with them every archived burst must be one admitted burst, so a duplicated
    burst cannot stand in for a missing one and a relabelled burst cannot leave
    the direction short.
    """
    _require(isinstance(rows, list) and rows, "no admitted burst to check")
    _require(max_beats > 0 and beat_bytes > 0,
             "the admitted burst limits are not positive")
    for row in rows:
        length = row["beats"] * row.get("beat_bytes", beat_bytes)
        _require(
            row["beats"] <= max_beats,
            "burst %s uses %s beats over the admitted cap %s"
            % (row.get("ordinal"), row["beats"], max_beats),
        )
        _require(
            row["address"] // 4096
            == (row["address"] + length - 1) // 4096,
            "burst %s at %#x crosses a 4 KiB page"
            % (row.get("ordinal"), row["address"]),
        )
    if admitted is not None:
        observed = sorted((row["address"], row["beats"]) for row in rows)
        _require(
            observed == sorted(tuple(pair) for pair in admitted),
            "the archived bursts are %s, the admitted split is %s"
            % (observed, sorted(tuple(pair) for pair in admitted)),
        )
    return len(rows)


# Descriptor kinds whose destination really holds the payload the execution
# moved: STORE writes HBM, P2P writes the receiving tile's SRAM.
WRITER_KINDS = (2, 3)
MOVED_BYTES_FIELD = {2: "write_bytes", 3: "p2p_bytes"}


def verified_destination_bytes(frames, descriptors, *, sources=None):
    """Every writer execution's destination holds the bytes it really moved.

    G5-R23-03: this is the single E2E byte oracle.  For every instance frame and
    every admitted writer execution it reads the producer's own raw source bytes,
    the execution's archived payload digest and the destination owner's raw
    post-image, and requires the admitted destination geometry, the byte lengths,
    the addresses and the digests to agree with those bytes -- per instance, not
    only for the last one.  A missing raw image fails closed instead of being
    accepted because a digest happens to match.  ``sources`` states the admitted
    absolute source rows when the caller can resolve them, so a producer whose
    addresses moved together with its transfer is rejected too.
    """
    _require(isinstance(frames, list) and frames, "the result archived no frame")
    _require(isinstance(descriptors, dict) and descriptors,
             "no admitted descriptor to check its destination bytes")
    checked = 0
    for frame in frames:
        images = {}
        for segment in frame.get("destinations", ()):
            key = (segment["command_id"], segment["generation"],
                   segment["descriptor_id"])
            images.setdefault(key, []).append(segment)
        for core_id, ledger in frame.get("cores", {}).items():
            for execution in ledger["observations"]["descriptor_executions"]:
                row = descriptors.get(execution["descriptor_id"])
                _require(row is not None,
                         "execution of descriptor %s has no admitted row"
                         % execution["descriptor_id"])
                if row["kind"] not in WRITER_KINDS:
                    continue
                key = (execution["command_id"], execution["generation"],
                       execution["descriptor_id"])
                moved = execution.get("transfer", {}).get(
                    MOVED_BYTES_FIELD[row["kind"]], 0)
                segments = images.get(key, ())
                if moved == 0:
                    _require(
                        not segments,
                        "execution %s moved no byte but archived %s destination "
                        "segments" % (key, len(segments)),
                    )
                    continue
                _require(
                    row["kind"] != 3 or execution.get("committed", False),
                    "peer execution %s archived a destination without committing"
                    % (key,),
                )
                admitted_sources = (sources or {}).get(
                    execution["descriptor_id"]
                )
                if admitted_sources is not None:
                    _require(
                        [(row["address"], row["size"])
                         for row in (execution.get("source_rows") or ())]
                        == [tuple(row) for row in admitted_sources],
                        "execution %s producer rows are %s, the admitted source "
                        "rows are %s"
                        % (key,
                           [(row["address"], row["size"])
                            for row in (execution.get("source_rows") or ())],
                           [tuple(row) for row in admitted_sources]),
                    )
                source_rows = execution.get("source_rows") or []
                _require(
                    source_rows,
                    "execution %s archived no producer bytes" % (key,),
                )
                raw_sources = []
                for source in source_rows:
                    hexed = source.get("bytes_hex")
                    _require(
                        isinstance(hexed, str) and hexed
                        and len(hexed) == 2 * source["size"],
                        "execution %s lost the raw bytes of its producer row at "
                        "%#x" % (key, source["address"]),
                    )
                    raw = bytes.fromhex(hexed)
                    # The row digest is the SHA the producer-content entry pins
                    # to the admitted compute producer, so the raw bytes and
                    # the digest chain describe one physical content.
                    _require(
                        isinstance(source.get("digest"), str)
                        and len(source["digest"]) == 64
                        and hashlib.sha256(raw).hexdigest() == source["digest"],
                        "execution %s producer row at %#x holds bytes hashing "
                        "to %s, its archived row digest is %s"
                        % (key, source["address"],
                           hashlib.sha256(raw).hexdigest(), source.get("digest")),
                    )
                    raw_sources.append(raw)
                produced = b"".join(raw_sources)
                _require(
                    len(produced) == moved,
                    "execution %s moved %s bytes but its producer rows hold %s"
                    % (key, moved, len(produced)),
                )
                digest = payload_digest(produced)
                _require(
                    execution["transfer"].get("payload_digest") == digest,
                    "execution %s archived digest %s, its producer bytes hash to "
                    "%s" % (key, execution["transfer"].get("payload_digest"),
                            digest),
                )
                expected_rows = [
                    (row["remote_address"]
                     + index * row["remote_stride_bytes"], row["row_bytes"])
                    for index in range(row["rows"])
                ]
                observed_rows = {}
                for segment in segments:
                    _require(
                        segment["row"] not in observed_rows,
                        "execution %s archived destination row %s twice"
                        % (key, segment["row"]),
                    )
                    observed_rows[segment["row"]] = segment
                _require(
                    sorted(observed_rows) == list(range(len(expected_rows))),
                    "execution %s archived destination rows %s, the admitted "
                    "destination has %s rows"
                    % (key, sorted(observed_rows), len(expected_rows)),
                )
                for index, (address, size) in enumerate(expected_rows):
                    segment = observed_rows[index]
                    _require(
                        (segment["address"], segment["size"]) == (address, size),
                        "execution %s destination row %s is %s, the admitted "
                        "destination row is %s"
                        % (key, index,
                           (segment["address"], segment["size"]),
                           (address, size)),
                    )
                    hexed = segment.get("bytes_hex")
                    _require(
                        isinstance(hexed, str) and hexed
                        and len(hexed) == 2 * size,
                        "execution %s lost the raw destination bytes of row %s "
                        "at %#x" % (key, index, address),
                    )
                    landed = bytes.fromhex(hexed)
                    wanted = produced[
                        index * row["row_bytes"]:(index + 1) * row["row_bytes"]
                    ]
                    _require(
                        landed == wanted,
                        "execution %s destination row %s holds %s, its own "
                        "producer bytes are %s"
                        % (key, index, landed[:16].hex(), wanted[:16].hex()),
                    )
                    checked += 1
    return checked


AXI_READ_KINDS = (1, 4)


def _merged_intervals(intervals):
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return [tuple(run) for run in merged]


def admitted_write_identities(program_dir, descriptors, *, arch, src_nodes,
                              instances):
    """Per (descriptor, instance): every accepted write burst with the
    single-use transaction identity the admitted accept order assigns to that
    burst index, over the burst's own useful remote span.

    ``descriptors`` are the admitted oracle rows.  ``fault_plan.execution_keys``
    owns the identity derivation; the shared splitter owns the remote burst
    geometry; both enumerate a descriptor's bursts in the same admitted order,
    so burst index pairs them without a second identity formula.
    """
    from mesh_ir.burst_splitter import plan_descriptor
    from mesh_ir.fault_plan import execution_keys

    facts = {}
    for core_facts in execution_keys(
            program_dir, arch, src_nodes, instances=instances).values():
        for fact in core_facts:
            if fact["direction"] != "write":
                continue
            key = (fact["descriptor_id"], fact["instance"], fact["execution"])
            facts.setdefault(key, {})[fact["burst_index"]] = fact
    identities = {}
    for descriptor_id, row in descriptors.items():
        if row["kind"] not in (2, 3):
            continue
        plan = plan_descriptor(
            row["row_bytes"], row["rows"], row["remote_address"],
            row["remote_stride_bytes"], arch["axi_data_bytes"],
            min(arch["axi_max_burst_beats"], row["max_burst_beats"]),
        ).bursts
        for instance in range(1, instances + 1):
            execution = 0
            while (descriptor_id, instance, execution) in facts:
                by_index = facts[(descriptor_id, instance, execution)]
                identities.setdefault((descriptor_id, instance), []).extend(
                    {
                        "uid": by_index[index]["uid"],
                        "descriptor_id": descriptor_id,
                        "execution": execution,
                        "burst_index": index,
                        # The useful bytes start at the burst's logical
                        # start inside the aligned beat, not at the beat
                        # base.
                        "address": burst.logical_start,
                        "useful_bytes": burst.useful_bytes,
                    }
                    for index, burst in enumerate(plan)
                    if index in by_index
                )
                execution += 1
    return identities


def verified_burst_attribution(frames, descriptors, *, max_beats, beat_bytes,
                               bridges):
    """Per-execution burst identity and retirement facts of a real run.

    G5-R23-02: ``frames`` are the archived instance frames, ``descriptors``
    the admitted oracle rows and ``bridges`` the per-core bridge counters.
    Every archived burst must name an execution that really ran in that frame
    on that core, match the admitted burst plan of that execution (geometry
    and burst order), retire exactly once -- a read burst at RLAST and, when
    it committed, at its local SRAM commit; a write burst at exactly one B --
    and carry the terminal tick of its own execution, not a bound the burst
    reports about itself.  A burst of a committed execution may not claim an
    error, a committed execution's archived bursts and moved bytes equal its
    own transfer facts, and every core's archived totals equal the bursts its
    bridge accepted, so a late, missing, borrowed, relabelled or dropped
    burst cannot hide behind self-reported fields or aggregate counters.
    """
    from mesh_ir.burst_splitter import plan_descriptor

    _require(isinstance(frames, list) and frames, "the result archived no frame")
    _require(isinstance(descriptors, dict) and descriptors,
             "no admitted descriptor to check its bursts")
    _require(isinstance(bridges, list) and bridges,
             "the result archived no bridge counters")
    bridge_of = {}
    for bridge in bridges:
        core_id = bridge.get("core_id")
        _require(core_id is not None and core_id not in bridge_of,
                 "the bridges report core %s twice" % core_id)
        bridge_of[core_id] = bridge
    ordinal_owner = {}
    archived = {}
    total = 0
    previous_boundary = None
    previous_instance = None
    for frame in frames:
        instance = frame.get("instance")
        executions = {}
        for core_id, ledger in frame.get("cores", {}).items():
            for execution in ledger["observations"]["descriptor_executions"]:
                key = (int(core_id), execution["command_id"],
                       execution["generation"], execution["descriptor_id"])
                _require(key not in executions,
                         "frame %s runs execution %s twice" % (instance, key))
                executions[key] = execution
        observed = {}
        for row in frame.get("bursts", ()):
            _require(
                row.get("instance") == instance,
                "frame %s archives a burst of instance %s: %s"
                % (instance, row.get("instance"), row),
            )
            key = (row["core_id"], row["command_id"], row["generation"],
                   row["descriptor_id"])
            _require(key in executions,
                     "frame %s has no execution %s on core %s"
                     % (instance, key[1:], key[0]))
            # One instance only starts after the previous instance fully
            # completed, so no burst of this frame may be submitted inside
            # the previous frame's execution window: this anchor is what a
            # ledger-wide consistent time shift cannot move past.
            if previous_boundary is not None:
                _require(
                    row["issue_tick"] > previous_boundary,
                    "burst %s of execution %s was submitted at %s, before "
                    "frame %s completed at %s"
                    % (row["burst_index"], key, row["issue_tick"],
                       previous_instance, previous_boundary),
                )
            execution = executions[key]
            # The bridge owns burst ordinals per core, so the identity of one
            # archived burst is (core, ordinal).
            burst_identity = (row["core_id"], row["ordinal"])
            _require(
                burst_identity not in ordinal_owner,
                "burst (core %s, ordinal %s) is archived for %s and for %s"
                % (row["core_id"], row["ordinal"],
                   ordinal_owner.get(burst_identity), key),
            )
            ordinal_owner[burst_identity] = key
            observed.setdefault(key, []).append(row)
            entry = archived.setdefault(row["core_id"], {"AR": 0, "AW": 0})
            _require(row["channel"] in entry,
                     "burst %s of execution %s is on an unknown channel %s"
                     % (row["burst_index"], key, row["channel"]))
            entry[row["channel"]] += 1
            total += 1
        for key, execution in executions.items():
            row = descriptors.get(key[3])
            _require(row is not None,
                     "execution %s has no admitted descriptor" % (key,))
            if row["kind"] not in (1, 2, 3, 4):
                continue
            plan = plan_descriptor(
                row["row_bytes"], row["rows"], row["remote_address"],
                row["remote_stride_bytes"], beat_bytes,
                min(max_beats, row["max_burst_beats"]),
            ).bursts
            read = row["kind"] in AXI_READ_KINDS
            rows = sorted(observed.get(key, ()), key=lambda item: item["burst_index"])
            _require(
                [item["burst_index"] for item in rows]
                == list(range(len(rows))),
                "execution %s archives burst indices %s"
                % (key, [item["burst_index"] for item in rows]),
            )
            committed = bool(execution.get("committed")) and \
                execution.get("status") == "OK"
            if committed:
                _require(
                    len(rows) == len(plan),
                    "committed execution %s archives %s of its %s admitted bursts"
                    % (key, len(rows), len(plan)),
                )
                moved = execution.get("transfer", {})
                if read:
                    burst_field, byte_field = "read_bursts", "read_bytes"
                elif row["kind"] == 3:
                    burst_field, byte_field = "p2p_bursts", "p2p_bytes"
                else:
                    burst_field, byte_field = "write_bursts", "write_bytes"
                _require(
                    len(rows) == moved.get(burst_field)
                    and sum(item["useful_bytes"] for item in rows)
                    == moved.get(byte_field),
                    "committed execution %s archives %s bursts moving %s "
                    "bytes, its own transfer facts say %s bursts and %s bytes"
                    % (key, len(rows),
                       sum(item["useful_bytes"] for item in rows),
                       moved.get(burst_field), moved.get(byte_field)),
                )
            else:
                _require(
                    len(rows) <= len(plan),
                    "execution %s archives %s bursts over its admitted %s"
                    % (key, len(rows), len(plan)),
                )
                errored_rows = [item for item in rows if item["errored"]]
                moved = execution.get("transfer", {})
                successful = sum(
                    moved.get(field, 0)
                    for field in ("read_bursts", "write_bursts", "p2p_bursts")
                )
                _require(
                    successful == len(rows) - len(errored_rows),
                    "execution %s declares %s successful bursts but archives "
                    "%s non-errored ones"
                    % (key, successful, len(rows) - len(errored_rows)),
                )
                if execution.get("status") not in (None, "OK"):
                    # An execution that reported an AXI failure accepted the
                    # burst that failed: the burst must be archived, in the
                    # execution's direction, and a plan cut short by the
                    # fault ends on the burst that faulted.
                    _require(
                        errored_rows,
                        "execution %s reports status %s but archives no "
                        "errored burst" % (key, execution.get("status")),
                    )
                    _require(
                        all(item["channel"] == ("AR" if read else "AW")
                            for item in errored_rows),
                        "execution %s reports %s but its errored bursts are "
                        "on the wrong direction" % (key,
                                                    execution.get("status")),
                    )
                    if len(rows) < len(plan):
                        _require(
                            rows[-1]["errored"],
                            "execution %s archived %s of its %s admitted bursts "
                            "but its boundary burst did not fault"
                            % (key, len(rows), len(plan)),
                        )
                else:
                    _require(
                        not errored_rows,
                        "unerrored execution %s archives errored bursts"
                        % (key,),
                    )
            for index, item in enumerate(rows):
                admitted = plan[index]
                _require(
                    item["channel"] == ("AR" if read else "AW"),
                    "burst %s of execution %s is a %s burst, the admitted "
                    "direction is %s"
                    % (item["burst_index"], key, item["channel"],
                       "read" if read else "write"),
                )
                _require(
                    (item["address"], item["beats"], item["useful_bytes"],
                     item["logical_start"])
                    == (admitted.beat_base, admitted.beats,
                        admitted.useful_bytes, admitted.logical_start),
                    "burst %s of execution %s is %s, the admitted burst is %s"
                    % (item["burst_index"], key,
                       (item["address"], item["beats"], item["useful_bytes"],
                        item["logical_start"]),
                       (admitted.beat_base, admitted.beats,
                        admitted.useful_bytes, admitted.logical_start)),
                )
                _require(
                    item["done_tick"] == execution.get("completion_tick"),
                    "burst %s of execution %s reports the terminal tick %s, "
                    "the execution completed at %s"
                    % (item["burst_index"], key, item["done_tick"],
                       execution.get("completion_tick")),
                )
                _require(
                    execution.get("submit_tick", 0) <= item["issue_tick"]
                    <= execution.get("completion_tick", 0),
                    "burst %s of execution %s was submitted at %s, outside "
                    "its execution's submit %s to completion %s"
                    % (item["burst_index"], key, item["issue_tick"],
                       execution.get("submit_tick"),
                       execution.get("completion_tick")),
                )
                _require(
                    isinstance(item["ar_aw_tick"], int)
                    and item["ar_aw_tick"] > 0
                    and item["issue_tick"] <= item["ar_aw_tick"]
                    <= execution.get("completion_tick", 0),
                    "burst %s of execution %s has no valid request handshake: "
                    "%s outside its submission %s and completion %s"
                    % (item["burst_index"], key, item["ar_aw_tick"],
                       item["issue_tick"], execution.get("completion_tick")),
                )
                _require(
                    item["retire_tick"] >= item["ar_aw_tick"],
                    "burst %s of execution %s retired at %s, before its "
                    "request handshake at %s"
                    % (item["burst_index"], key, item["retire_tick"],
                       item["ar_aw_tick"]),
                )
                if committed:
                    _require(
                        not item["errored"],
                        "burst %s of execution %s reports an error while its "
                        "execution committed" % (item["burst_index"], key),
                    )
                _require(
                    item["retire_tick"] > 0 and item["response_tick"] > 0
                    and item["retire_tick"] == item["response_tick"],
                    "burst %s of execution %s did not retire exactly once: %s"
                    % (item["burst_index"], key, item),
                )
                _require(
                    item["done_tick"] >= item["retire_tick"],
                    "burst %s of execution %s retired at %s, after its "
                    "descriptor terminal tick %s"
                    % (item["burst_index"], key, item["retire_tick"],
                       item["done_tick"]),
                )
                if read:
                    _require(
                        item["local_commit_tick"] == 0
                        or item["retire_tick"] <= item["local_commit_tick"]
                        <= item["done_tick"],
                        "read burst %s of execution %s retired at %s with local "
                        "commit %s and terminal tick %s"
                        % (item["burst_index"], key, item["retire_tick"],
                           item["local_commit_tick"], item["done_tick"]),
                    )
                    _require(
                        item["errored"] or item["local_commit_tick"] > 0,
                        "committed read burst %s of execution %s never reached "
                        "its local SRAM commit" % (item["burst_index"], key),
                    )
                else:
                    _require(
                        item["local_commit_tick"] == 0,
                        "write burst %s of execution %s reports a read local "
                        "commit" % (item["burst_index"], key),
                    )
        previous_boundary = max(
            (execution.get("completion_tick", 0)
             for execution in executions.values()),
            default=0,
        )
        previous_instance = instance
    errored_by_core = {}
    for frame in frames:
        for row in frame.get("bursts", ()):
            if row["errored"]:
                errored_by_core.setdefault(
                    row["core_id"], {"AR": 0, "AW": 0})[row["channel"]] += 1
    for core_id in sorted(set(errored_by_core) | set(bridge_of)):
        errored = errored_by_core.get(core_id, {"AR": 0, "AW": 0})
        bridge = bridge_of[core_id]
        _require(
            errored["AW"] == bridge.get("b_error_count"),
            "the bridge of core %s classified %s errored B responses, the "
            "archive holds %s"
            % (core_id, bridge.get("b_error_count"), errored["AW"]),
        )
        _require(
            (errored["AR"] > 0) == (bridge.get("r_error_beats", 0) > 0),
            "the bridge of core %s reports %s error beats but the archive "
            "holds %s errored read bursts"
            % (core_id, bridge.get("r_error_beats"), errored["AR"]),
        )
    for core_id, counts in sorted(archived.items()):
        _require(
            core_id in bridge_of,
            "core %s archived bursts but has no bridge" % core_id,
        )
        bridge = bridge_of[core_id]
        for channel, field in (("AR", "ar_accepted"), ("AW", "aw_accepted")):
            _require(
                counts[channel] == bridge.get(field),
                "the bridge of core %s accepted %s %s bursts, the archive "
                "holds %s"
                % (core_id, bridge.get(field), channel, counts[channel]),
            )
    return {"frames": len(frames), "bursts": total}


def admitted_transfer_destination(sections, descriptor_ids):
    """(receiver core, sorted destination allocation ids) of admitted descriptors.

    ``sections`` is the schedule's section mapping.  G5-10: a report about a
    resident destination must name the allocation the admitted plan resolved
    through its own shards, not one read back from the run.
    """
    allocation_of = {
        row["shard_id"]: row["allocation_id"] for row in sections["SHARDS"]
    }
    geometry = {row["descriptor_id"]: row for row in sections["DMA_DESCRIPTORS"]}
    rows = [geometry[descriptor_id] for descriptor_id in descriptor_ids]
    _require(rows, "no admitted descriptor to resolve a destination for")
    cores = {row["dst"]["owner_core"] for row in rows}
    _require(
        len(cores) == 1,
        "the admitted descriptors span several receiver cores: %s" % sorted(cores),
    )
    allocations = sorted({allocation_of[row["dst"]["shard_id"]] for row in rows})
    _require(
        allocations and 0 not in allocations,
        "the admitted destination allocations %s are not admitted" % allocations,
    )
    return cores.pop(), allocations


def verified_landing_events(transfer_row, *, admitted_ids, instance,
                            identities, executions, archived_bursts):
    """Validate the complete raw landing-event set of one transfer.

    Every event of the transfer -- landed by completed and by pending
    descriptors alike -- is associated with the one accepted write burst of
    this instance whose own useful span carries all of the event's ranges:
    the identity the event claims must be the identity the admitted accept
    order gave that burst, the event's tick may not precede that burst's
    request handshake, and across the whole transfer no identity may explain
    more landings than the transfer recorded.  The event set must also be
    complete: the union of the events' ranges is exactly the coverage the
    receiver recorded (``covered_ranges``/``covered_bytes``), each commit
    that extended coverage produced exactly one event and one stage at the
    same tick with the same cumulative bytes, and every range is a positive,
    addressable run -- so a deleted event, an emptied range list or a
    shortened range cannot leave the surviving set looking complete.
    Returns the validated events grouped by owning descriptor as
    ``(tick, uid, ((address, size), ...), request_tick)``; raises on an
    unassociated, misattributed, mistimed, duplicated or incomplete landing.
    """
    bursts = {}
    for row in archived_bursts:
        if row["channel"] != "AW" or row["instance"] != instance:
            continue
        key = (row["command_id"], row["generation"], row["descriptor_id"],
               row["burst_index"])
        _require(key not in bursts,
                 "instance %s archives write burst %s twice" % (instance, key))
        bursts[key] = row
    raw_events = list(transfer_row.get("landing_events") or ())
    for event in raw_events:
        _require(
            isinstance(event.get("ranges"), list) and event["ranges"],
            "transfer %s archives a landing of transaction %#x at tick %s "
            "with no ranges"
            % (transfer_row["transfer_id"], event.get("txn_uid"),
               event.get("tick")),
        )
        for run in event["ranges"]:
            _require(
                isinstance(run, dict)
                and isinstance(run.get("address"), int)
                and run["address"] >= 0
                and isinstance(run.get("size"), int)
                and run["size"] > 0,
                "transfer %s archives the landing range %r of transaction "
                "%#x, which is not a positive addressed run"
                % (transfer_row["transfer_id"], run,
                   event.get("txn_uid")),
            )
    stages = list(transfer_row.get("stages") or ())
    _require(
        len(raw_events) == len(stages),
        "transfer %s archives %s landing events for %s coverage stages"
        % (transfer_row["transfer_id"], len(raw_events), len(stages)),
    )
    cumulative = 0
    for event, stage in zip(raw_events, stages):
        _require(
            event["tick"] == stage["tick"],
            "transfer %s archives a landing of transaction %#x at tick %s "
            "against a coverage stage at tick %s"
            % (transfer_row["transfer_id"], event["txn_uid"],
               event["tick"], stage["tick"]),
        )
        cumulative += sum(run["size"] for run in event["ranges"])
        _require(
            cumulative == stage["covered_bytes"],
            "transfer %s covered %s stage bytes by tick %s, its landing "
            "events account for %s"
            % (transfer_row["transfer_id"], stage["covered_bytes"],
               stage["tick"], cumulative),
        )
    event_union = _merged_intervals(
        (run["address"], run["address"] + run["size"])
        for event in raw_events for run in event["ranges"])
    covered_union = _merged_intervals(
        (run["address"], run["address"] + run["size"])
        for run in transfer_row.get("covered_ranges") or ())
    _require(
        event_union == covered_union,
        "transfer %s records coverage %s but its landing events cover %s"
        % (transfer_row["transfer_id"], covered_union, event_union),
    )
    _require(
        sum(end - start for start, end in event_union)
        == transfer_row.get("covered_bytes"),
        "transfer %s records %s covered bytes, its landing events cover %s"
        % (transfer_row["transfer_id"], transfer_row.get("covered_bytes"),
           sum(end - start for start, end in event_union)),
    )
    allowance = collections.Counter(transfer_row.get("transaction_uids") or ())
    demand = collections.Counter()
    validated = {}
    for event in raw_events:
        owner = None
        for descriptor_id in admitted_ids:
            for candidate in identities.get((descriptor_id, instance), ()):
                span = (candidate["address"],
                        candidate["address"] + candidate["useful_bytes"])
                if all(span[0] <= run["address"]
                       and run["address"] + run["size"] <= span[1]
                       for run in event["ranges"]):
                    owner = candidate
                    break
            if owner is not None:
                break
        _require(
            owner is not None,
            "transfer %s archives a landing of transaction %#x at tick %s "
            "that no accepted burst of this instance carries"
            % (transfer_row["transfer_id"], event["txn_uid"], event["tick"]),
        )
        execution_rows = executions.get(owner["descriptor_id"]) or []
        execution = (
            execution_rows[owner["execution"]]
            if isinstance(execution_rows, list)
            and 0 <= owner["execution"] < len(execution_rows) else None)
        _require(
            execution is not None,
            "transfer %s archives a landing owned by execution %s of "
            "descriptor %s, which the frame did not run"
            % (transfer_row["transfer_id"], owner["execution"],
               owner["descriptor_id"]),
        )
        burst = bursts.get(
            (execution["command_id"], execution["generation"],
             owner["descriptor_id"], owner["burst_index"]))
        _require(
            burst is not None,
            "transfer %s archives a landing owned by burst %s of descriptor "
            "%s, which the frame did not accept"
            % (transfer_row["transfer_id"], owner["burst_index"],
               owner["descriptor_id"]),
        )
        _require(
            event["txn_uid"] == owner["uid"],
            "transfer %s archives a landing claiming transaction %#x, but "
            "its ranges are the landing of transaction %#x"
            % (transfer_row["transfer_id"], event["txn_uid"], owner["uid"]),
        )
        _require(
            event["tick"] >= burst["ar_aw_tick"],
            "transfer %s archives transaction %#x landing at %s, before its "
            "request handshake at %s"
            % (transfer_row["transfer_id"], event["txn_uid"], event["tick"],
               burst["ar_aw_tick"]),
        )
        # A fresh target commit of this transaction cannot happen after its
        # own write response returned and retired: the target adapter calls
        # the commit observer before it builds and queues the B, so a burst
        # that already received its B bounds its landing from above.  A burst
        # still waiting for its response carries response_tick == 0 and gets
        # no upper bound here -- its landing may legitimately be pending.
        if burst["response_tick"] > 0:
            _require(
                event["tick"] <= burst["response_tick"],
                "transfer %s archives transaction %#x landing at %s, after "
                "its own write response retired at %s"
                % (transfer_row["transfer_id"], event["txn_uid"],
                   event["tick"], burst["response_tick"]),
            )
        demand[event["txn_uid"]] += 1
        validated.setdefault(owner["descriptor_id"], []).append(
            (event["tick"], event["txn_uid"],
             tuple((run["address"], run["size"]) for run in event["ranges"]),
             burst["ar_aw_tick"]))
    for txn_uid, claimed in sorted(demand.items()):
        _require(
            claimed <= allowance[txn_uid],
            "transfer %s archives more landings of transaction %#x than it "
            "recorded" % (transfer_row["transfer_id"], txn_uid),
        )
    return validated


def verified_transfer_snapshots(
    rows,
    *,
    admitted,
    geometry,
    executions,
    target_core,
    admitted_allocations,
    completed,
    initial_bytes=None,
    resident=False,
    landing=None,
):
    """Per-commit destination evidence of one P2P transfer.

    TORCH-CPP-09 / G5-09 / G5-10: the source archives one observation per
    descriptor it really committed, in commit order.  ``admitted`` lists the
    transfer's admitted descriptor ids in plan order, ``geometry`` their admitted
    descriptors, ``executions`` their execution observations keyed by descriptor,
    ``completed`` whether the transfer's command reached its completed terminal,
    and ``resident`` whether the destination allocation was already valid before
    this transfer started (an earlier producer's prefill).  ``initial_bytes``
    names the admitted initial content of a still-pending descriptor's
    destination span so the archived pending digest is checked against it, or is
    ``None`` when that content is not known.  ``landing`` prepares the
    transfer's own raw landing facts: the aperture transfer row, the instance,
    the admitted write identities (``admitted_write_identities``), the frame's
    archived bursts and, per pending descriptor, the span layout, destination
    read-back and producer bytes.  The complete raw event set -- completed and
    pending descriptors alike -- is associated, timed and quota-checked by
    ``verified_landing_events`` before any pending timeline is derived, so the
    caller cannot decide which events get validated.

    A row may only claim progress its own accepted transaction made: a resident
    destination is never this transfer's completion, the pending set is exactly
    the admitted descriptors that have not committed, and nothing is released
    before every admitted descriptor committed and the command completed.  A
    pending span's digest must equal the landing state its events had really
    reached at that row's commit tick -- a snapshot may never borrow a state a
    later burst reaches.  The receiver's own notification arrives at the
    target's commit of the final byte, so it can never come after the source's
    commit of the final descriptor.
    """
    _require(rows, "the transfer archived no commit observation")
    _require(admitted, "the transfer has no admitted descriptor")
    validated_events = {}
    if landing is not None:
        validated_events = verified_landing_events(
            landing["transfer"],
            admitted_ids=admitted,
            instance=landing["instance"],
            identities=landing["identities"],
            executions=executions,
            archived_bursts=landing["bursts"],
        )
    _require(
        len({(row["command_id"], row["generation"]) for row in rows}) == 1,
        "one transfer archived commit observations of several commands: %s"
        % sorted({(row["command_id"], row["generation"]) for row in rows}),
    )
    admitted_set = set(admitted)
    committed = set()
    for row in rows:
        descriptor_id = row["descriptor_id"]
        _require(
            descriptor_id in admitted_set,
            "transfer commit %s is not an admitted descriptor" % descriptor_id,
        )
        _require(
            descriptor_id not in committed,
            "descriptor %s committed twice in one transfer" % descriptor_id,
        )
        execution_rows = executions.get(descriptor_id) or []
        _require(
            any(row.get("committed") is True for row in execution_rows),
            "descriptor %s archived a commit without its own committed execution"
            % descriptor_id,
        )
        # The row observes the destination at descriptor completion, so it is
        # recorded at or after the committing execution's own commit and no
        # later than its completion.
        _require(
            any(candidate.get("commit_tick") is not None
                and candidate["commit_tick"] <= row["commit_tick"]
                <= candidate.get("completion_tick", row["commit_tick"])
                for candidate in execution_rows),
            "descriptor %s archived its commit at tick %s, outside every "
            "execution's commit-to-completion window %s"
            % (descriptor_id, row["commit_tick"],
               [(candidate.get("commit_tick"),
                 candidate.get("completion_tick"))
                for candidate in execution_rows]),
        )
        committed.add(descriptor_id)
        publishable = completed and committed == admitted_set
        _require(
            bool(row.get("sender_published")) == publishable
            and row.get("sender_notifications") == (1 if publishable else 0),
            "descriptor %s reports sender_published=%s with %s notifications "
            "while %s of %s admitted descriptors committed"
            % (descriptor_id, row.get("sender_published"),
               row.get("sender_notifications"), len(committed), len(admitted_set)),
        )
        _require(
            row.get("receiver_present") is True,
            "descriptor %s has no receiver record" % descriptor_id,
        )
        _require(
            row.get("receiver_core") == target_core
            and sorted(row.get("receiver_admitted_allocations") or ())
            == sorted(admitted_allocations),
            "descriptor %s names receiver %s with allocations %s instead of the "
            "admitted core %s allocations %s"
            % (descriptor_id, row.get("receiver_core"),
               row.get("receiver_admitted_allocations"), target_core,
               admitted_allocations),
        )
        _require(
            row.get("receiver_allocation_id") in admitted_allocations,
            "descriptor %s names destination allocation %s outside the admitted "
            "%s" % (descriptor_id, row.get("receiver_allocation_id"),
                    admitted_allocations),
        )
        _require(
            bool(row.get("receiver_transfer_committed")) == publishable,
            "descriptor %s reports the receiver transfer %s while %s of %s "
            "admitted descriptors committed"
            % (descriptor_id,
               "committed" if row.get("receiver_transfer_committed")
               else "pending", len(committed), len(admitted_set)),
        )
        if publishable:
            _require(
                row.get("receiver_notifications") == 1
                and 0 < (row.get("receiver_notification_tick") or 0)
                <= row["commit_tick"]
                and row.get("receiver_notified_allocation_id")
                == row.get("receiver_allocation_id"),
                "descriptor %s released the receiver with %s notifications at "
                "tick %s and committed at %s"
                % (descriptor_id, row.get("receiver_notifications"),
                   row.get("receiver_notification_tick"), row["commit_tick"]),
            )
        else:
            _require(
                row.get("receiver_notifications") == 0
                and row.get("receiver_notification_tick") == 0
                and row.get("receiver_notified_allocation_id") == 0,
                "descriptor %s notified the receiver before the transfer was "
                "complete" % descriptor_id,
            )
        _require(
            bool(row.get("receiver_allocation_valid")) == (resident or publishable),
            "descriptor %s reports destination valid=%s while the admitted "
            "transfer is %s and the destination %s prefilled"
            % (descriptor_id, row.get("receiver_allocation_valid"),
               "complete" if publishable else "incomplete",
               "was" if resident else "was not"),
        )
        pending = [entry for entry in admitted if entry not in committed]
        pending_size = sum(
            geometry[entry]["row_bytes"] * geometry[entry]["rows"]
            for entry in pending
        )
        _require(
            row.get("pending_bytes") == pending_size,
            "descriptor %s left %s pending bytes but its %s uncommitted admitted "
            "descriptors hold %s"
            % (descriptor_id, row.get("pending_bytes"), len(pending),
               pending_size),
        )
        spans = list(row.get("pending_spans") or ())
        _require(
            [span["descriptor_id"] for span in spans] == pending,
            "descriptor %s reports pending spans %s for its uncommitted "
            "descriptors %s"
            % (descriptor_id, [span.get("descriptor_id") for span in spans],
               pending),
        )
        if initial_bytes is None:
            _require(
                (row.get("pending_digest") is None) == (pending_size == 0)
                and (pending_size == 0
                     or len(row.get("pending_digest") or "") == 64),
                "descriptor %s pending digest %r does not match %s pending bytes"
                % (descriptor_id, row.get("pending_digest"), pending_size),
            )
            continue
        content = b""
        for span in spans:
            entry = span["descriptor_id"]
            expected = initial_bytes(entry)
            _require(
                span.get("size") == len(expected)
                and len(span.get("digest") or "") == 64,
                "descriptor %s reports a %s-byte pending span for descriptor %s "
                "whose admitted span is %s bytes"
                % (descriptor_id, span.get("size"), entry, len(expected)),
            )
            span_facts = (landing or {}).get("spans", {}).get(entry)
            if span_facts is None:
                _require(
                    span["digest"] == hashlib.sha256(expected).hexdigest(),
                    "pending descriptor %s does not hold its admitted initial "
                    "content" % entry,
                )
                content += expected
                continue
            # A descriptor whose plan was cut short is split by the bytes its
            # own accepted transactions really landed: those must equal the
            # producer's bytes and every other byte must still be the admitted
            # initial content.  The landing facts are the transfer's own
            # validated events, time-stamped by the transaction that carried
            # them, so this snapshot row is only explained by the events that
            # had really landed at the row's own commit tick -- never by the
            # transfer's final coverage or a state a later burst reaches.
            execution_rows = executions.get(entry) or []
            _require(
                execution_rows
                and all(not row.get("committed") for row in execution_rows),
                "descriptor %s is reported as a partial failure but its "
                "execution committed" % entry,
            )
            destination = span_facts["destination"]
            _require(
                isinstance(destination, bytes) and len(destination) == len(expected),
                "pending descriptor %s has no %s-byte destination read-back"
                % (entry, len(expected)),
            )
            producer = span_facts.get("producer")
            layout = span_facts["layout"]
            events = []
            for tick, uid, absolute, request in validated_events.get(entry, ()):
                offsets = []
                for address, size in absolute:
                    for span_offset, base, span_size in layout:
                        begin = max(address, base)
                        finish = min(address + size, base + span_size)
                        if begin < finish:
                            offsets.append(
                                (span_offset + begin - base, finish - begin))
                if offsets:
                    events.append((tick, uid, tuple(offsets), request))
            events.sort(key=lambda event: (event[0], event[1]))
            landed = bytearray(len(destination))
            for _tick, _uid, ranges, _request in events:
                for offset, size in ranges:
                    _require(
                        0 <= offset and offset + size <= len(destination),
                        "pending descriptor %s landed outside its %s-byte "
                        "span" % (entry, len(destination)),
                    )
                    for index in range(offset, offset + size):
                        landed[index] = 1
            if any(landed):
                _require(
                    isinstance(producer, bytes) and len(producer) == len(expected),
                    "partially landed descriptor %s has no producer bytes"
                    % entry,
                )
            for index, byte in enumerate(destination):
                want = producer[index] if landed[index] else expected[index]
                _require(
                    byte == want,
                    "pending descriptor %s byte %s is %#x, its %s byte is %#x"
                    % (entry, index, byte,
                       "landed producer" if landed[index] else "admitted initial",
                       want),
                )

            def state_at(snapshot_tick):
                state = bytearray(expected)
                for event in events:
                    if event[0] > snapshot_tick:
                        continue
                    for offset, size in event[2]:
                        state[offset:offset + size] = \
                            producer[offset:offset + size]
                return bytes(state)

            landed_state = state_at(row["commit_tick"])
            _require(
                span["digest"] == hashlib.sha256(landed_state).hexdigest(),
                "pending descriptor %s archives the span digest %s at tick %s, "
                "which is not the landing state its events had reached by then"
                % (entry, span["digest"], row["commit_tick"]),
            )
            content += landed_state
        _require(
            row.get("pending_digest")
            == (hashlib.sha256(content).hexdigest() if content else None),
            "descriptor %s pending digest %r is not the combination of its "
            "pending descriptors' own landing states"
            % (descriptor_id, row.get("pending_digest")),
        )


def verified_transfer_publishes(apertures, admitted_transactions, abandoned=None,
                                sender_rows=(), refused_replays=None):
    """Per-transfer publish facts of the real peer apertures.

    TORCH-CPP-09: a transfer is released exactly once and only when its own
    distinct accepted transactions have covered every admitted byte.  Returns
    ``{transfer_id: commit_tick}`` for the publishes of the last instance and
    raises when a transfer is missing, uncovered, over-covered or published
    twice.

    Every published transfer also carries the real transaction identities whose
    commits covered it: a transaction identity is single-use, so an identity that
    appears twice, or on a transfer other than the one it first covered, is a
    replayed or foreign commit rather than coverage, and ``refused_replays``
    states which transfers really refused injected stale deliveries and how many
    lanes they refused.

    ``abandoned`` names the transfers whose admitted plan lost a burst, mapping
    each to the source-side execution contributions of that plan.  Those
    contributions are the only record of what really moved, so the receiver must
    have landed exactly their bytes over exactly their bursts and must never
    release the transfer, and ``sender_rows`` (the source-side transfer
    observations) must show no release either: a plan that lost part of its
    payload may not be reported as completed by either side.
    """
    _require(
        isinstance(admitted_transactions, dict) and admitted_transactions,
        "no admitted P2P transfer to check",
    )
    abandoned = dict(abandoned or {})
    refused = {key: value for key, value in (refused_replays or {}).items() if value}
    identity_owner = {}
    refused_seen = {}
    by_sender = {}
    for row in sender_rows:
        by_sender.setdefault(row["transfer_id"], []).append(row)
    published = {}
    abandoned_seen = set()
    for index, aperture in enumerate(apertures):
        for row in aperture.get("transfers", ()):
            transfer_id = row["transfer_id"]
            _require(
                transfer_id in admitted_transactions,
                "aperture on core %s reports transfer %s without an admitted "
                "descriptor" % (aperture.get("core_id"), transfer_id),
            )
            uids = row.get("transaction_uids")
            _require(
                isinstance(uids, list) and len(uids) == len(set(uids))
                and len(uids) == row.get("transactions"),
                "transfer %s reports %s distinct transaction identities for %s "
                "transactions"
                % (transfer_id, len(uids) if isinstance(uids, list) else uids,
                   row.get("transactions")),
            )
            for uid in uids:
                _require(
                    uid not in identity_owner,
                    "transaction %#x is attributed to transfer %s and transfer %s"
                    % (uid, identity_owner.get(uid), transfer_id),
                )
                identity_owner[uid] = transfer_id
            refused_seen[transfer_id] = row.get("replayed_lanes", 0)
            _require(
                refused_seen[transfer_id] == refused.get(transfer_id, 0),
                "transfer %s refused %s replayed lanes, the injected stale "
                "delivery carries %s"
                % (transfer_id, refused_seen[transfer_id],
                   refused.get(transfer_id, 0)),
            )
            if row.get("abandoned"):
                _require(
                    transfer_id in abandoned,
                    "aperture on core %s abandoned transfer %s without an "
                    "admitted lost burst"
                    % (aperture.get("core_id"), transfer_id),
                )
                _require(
                    not row.get("notified"),
                    "abandoned transfer %s was released anyway" % transfer_id,
                )
                contributions = abandoned[transfer_id]
                _require(
                    contributions,
                    "abandoned transfer %s has no source contribution"
                    % transfer_id,
                )
                moved = sum(
                    entry["transfer"].get(field, 0)
                    for entry in contributions
                    for field in _BYTE_FIELDS
                )
                bursts = sum(
                    entry["transfer"].get(field, 0)
                    for entry in contributions
                    for field in _BURST_FIELDS
                )
                _require(
                    0 < moved < row.get("expected_bytes", 0),
                    "abandoned transfer %s moved %s of %s admitted bytes"
                    % (transfer_id, moved, row.get("expected_bytes")),
                )
                _require(
                    row.get("covered_bytes") == moved,
                    "abandoned transfer %s landed %s bytes but the source moved "
                    "%s" % (transfer_id, row.get("covered_bytes"), moved),
                )
                _require(
                    row.get("uncovered_bytes") == row.get("expected_bytes") - moved,
                    "abandoned transfer %s left %s bytes uncovered but the "
                    "source moved %s of %s"
                    % (transfer_id, row.get("uncovered_bytes"), moved,
                       row.get("expected_bytes")),
                )
                _require(
                    row.get("transactions") == bursts,
                    "abandoned transfer %s was covered by %s transactions but "
                    "the source moved %s bursts"
                    % (transfer_id, row.get("transactions"), bursts),
                )
                _require(
                    row.get("duplicate_notifications") == 0,
                    "abandoned transfer %s replayed %s commit notifications"
                    % (transfer_id, row.get("duplicate_notifications")),
                )
                _require(
                    row.get("stages")
                    and not any(stage.get("notified") for stage in row["stages"]),
                    "abandoned transfer %s reports a publish stage: %s"
                    % (transfer_id, row.get("stages")),
                )
                senders = by_sender.get(transfer_id, ())
                _require(
                    len(senders) == 1,
                    "abandoned transfer %s has %s sender observations"
                    % (transfer_id, len(senders)),
                )
                sender = senders[0]
                _require(
                    not sender.get("sender_published")
                    and sender.get("sender_notifications") == 0,
                    "the sender released abandoned transfer %s: %s"
                    % (transfer_id, sender),
                )
                abandoned_seen.add(transfer_id)
                continue
            if not row.get("notified"):
                _require(
                    index == len(apertures) - 1,
                    "aperture on core %s kept an unretired expectation %s"
                    % (aperture.get("core_id"), transfer_id),
                )
                _require(
                    transfer_id in abandoned,
                    "aperture on core %s kept an unretired expectation %s "
                    "without an admitted lost burst"
                    % (aperture.get("core_id"), transfer_id),
                )
                continue
            _require(
                row.get("covered_bytes") == row.get("expected_bytes")
                and row.get("uncovered_bytes") == 0,
                "transfer %s was released with %s of %s admitted bytes covered"
                % (transfer_id, row.get("covered_bytes"), row.get("expected_bytes")),
            )
            _require(
                row.get("transactions") == admitted_transactions[transfer_id],
                "transfer %s was covered by %s transactions but its admitted "
                "plan has %s bursts"
                % (transfer_id, row.get("transactions"),
                   admitted_transactions[transfer_id]),
            )
            _require(
                transfer_id not in published,
                "transfer %s was published twice in one instance" % transfer_id,
            )
            published[transfer_id] = row.get("commit_tick")
    _require(
        sorted(abandoned_seen) == sorted(abandoned),
        "abandoned transfers %s differ from the ones the admitted plan lost %s"
        % (sorted(abandoned_seen), sorted(abandoned)),
    )
    _require(
        sorted(published) == sorted(set(admitted_transactions) - set(abandoned)),
        "published transfers %s differ from the admitted %s"
        % (sorted(published),
           sorted(set(admitted_transactions) - set(abandoned))),
    )
    _require(
        sorted(key for key, lanes in refused_seen.items() if lanes)
        == sorted(refused),
        "transfers refusing replayed lanes %s differ from the injected stale "
        "deliveries %s"
        % (sorted(key for key, lanes in refused_seen.items() if lanes),
           sorted(refused)),
    )
    return published


def verified_sentinels(declared, observed, computed_spans=()):
    """Sentinel spans that no admitted write may touch.

    ``declared`` is the oracle's list of ``{address, size}`` spans and
    ``observed`` the target owner's before/after digests.  TORCH-CPP-08: every
    declared span must have been sampled and its bytes must be identical before
    the run and after quiescence, which is how WSTRB-disabled lanes, row padding
    and the unaligned head and tail around a payload are proven untouched.

    ``computed_spans`` are the ``{address, size}`` runs the admitted compute
    engines really published.  A span a compute engine wrote is not claimed to be
    untouched -- no plan can know those runs before the run -- and is reported as
    explained instead of unchanged; every other declared span must be identical.
    """
    _require(
        isinstance(declared, list) and declared,
        "no admitted sentinel span was declared",
    )
    _require(
        isinstance(observed, list) and len(observed) == len(declared),
        "the target reported %s sentinel spans but %s were declared"
        % (len(observed) if isinstance(observed, list) else observed,
           len(declared)),
    )
    by_address = {}
    for row in observed:
        by_address.setdefault(row.get("address"), []).append(row)
    unchanged = 0
    explained = 0
    for want in declared:
        rows = by_address.get(want["address"])
        _require(
            rows and len(rows) == 1,
            "sentinel span at %#x was not sampled exactly once"
            % want["address"],
        )
        row = rows[0]
        _require(
            row.get("size") == want["size"],
            "sentinel span at %#x reported size %s instead of %s"
            % (want["address"], row.get("size"), want["size"]),
        )
        _require(
            row.get("available") and row.get("before_digest")
            and row.get("after_digest"),
            "sentinel span at %#x has no before/after sample" % want["address"],
        )
        if row["before_digest"] == row["after_digest"]:
            unchanged += 1
            continue
        _require(
            any(
                span.get("address", -1) <= want["address"]
                and want["address"] + want["size"]
                <= span.get("address", -1) + span.get("size", 0)
                for span in computed_spans
            ),
            "sentinel span at %#x changed: %s -> %s"
            % (want["address"], row["before_digest"], row["after_digest"]),
        )
        explained += 1
    return unchanged + explained


def open_interval_peak(intervals):
    """The most intervals that were open at the same time."""
    peak = 0
    for tick in sorted([start for start, _ in intervals]
                       + [end for _, end in intervals]):
        peak = max(peak, sum(1 for start, end in intervals if start <= tick <= end))
    return peak


def verified_queue_bounds(
    frames,
    bridges,
    network_stalls,
    *,
    descriptor_queue_depth,
    read_window,
):
    """What the admitted queue depths really bounded in one finished run.

    G5-01/G5-07/G5-08: ``frames`` is every instance frame's per-core ledger, so
    the descriptors in flight are counted per frame rather than cumulatively.  A
    shallow configuration must really reach its bound (otherwise the carrier
    proves nothing), no owner may exceed it, every accepted burst must retire
    exactly once, and the source must show measurable blocking.
    """
    _require(frames, "the result archived no instance frame")
    _require(
        isinstance(descriptor_queue_depth, int) and descriptor_queue_depth > 0
        and isinstance(read_window, int) and read_window > 0,
        "the admitted queue depths are not positive",
    )
    peaks = []
    for instance_id, cores in frames:
        for core_id, ledger in sorted(cores.items()):
            intervals = [
                (row["submit_tick"], row["completion_tick"] or row["submit_tick"])
                for row in ledger["observations"]["descriptor_executions"]
            ]
            if not intervals:
                continue
            peak = open_interval_peak(intervals)
            _require(
                peak <= descriptor_queue_depth,
                "instance %s core %s held %d descriptors in flight over the "
                "admitted depth %d"
                % (instance_id, core_id, peak, descriptor_queue_depth),
            )
            peaks.append(peak)
    _require(peaks, "no descriptor was ever in flight")
    _require(
        max(peaks) == descriptor_queue_depth,
        "the shallow configuration never reached its descriptor depth: %s of %d"
        % (sorted(peaks), descriptor_queue_depth),
    )
    _require(isinstance(bridges, list) and bridges, "result has no bridges")
    read_peaks = [bridge.get("peak_read_outstanding", 0) for bridge in bridges]
    _require(
        max(read_peaks) <= read_window,
        "the adapter read window peaked at %d over the admitted %d"
        % (max(read_peaks), read_window),
    )
    _require(
        max(read_peaks) == read_window,
        "the shallow configuration never filled its read window: %s of %d"
        % (sorted(read_peaks), read_window),
    )
    for bridge in bridges:
        _require(
            bridge.get("read_bursts_submitted")
            == bridge.get("read_bursts_completed")
            and bridge.get("write_bursts_submitted")
            == bridge.get("write_bursts_completed"),
            "bridge on core %s did not retire every burst it accepted: %s"
            % (bridge.get("core_id"), bridge),
        )
    busy = sum(
        channel.get("ni_vc_busy_cycles", 0)
        for channel in (network_stalls or {}).values()
    )
    _require(
        busy > 0,
        "the shallow configuration produced no measurable source blocking",
    )
    return {
        "descriptor_queue_depth": descriptor_queue_depth,
        "read_window": read_window,
        "descriptor_peaks": sorted(peaks),
        "busy_cycles": busy,
    }


def verified_drain(garnet, credit_ledger, drain, bridges, aperture_frames, endpoint,
                   instances=1):
    """The unified drain boundary of one finished or draining run.

    G5-18: HALT only starts the drain.  The instance may be archived and the
    program may exit only once every real owner is empty: the Garnet network
    snapshot, every credit ledger entry restored, every bridge idle, no live
    peer expectation in any archived instance frame and the target endpoint
    idle.  ``aperture_frames`` is every frame's per-core peer coverage record.
    Returns the observed drain window
    ``{begin_tick, begin_pending, end_tick, end_pending}``.
    """
    _require(isinstance(garnet, dict), "result has no Garnet snapshot")
    _require(
        garnet.get("quiescent") in (1, True),
        "the network is not quiescent at the exit point: %s" % garnet,
    )
    _require(
        isinstance(credit_ledger, list) and credit_ledger,
        "the credit ledger was not published",
    )
    for entry in credit_ledger:
        _require(
            entry.get("conserved"),
            "credit ledger entry %s is not conserved" % entry,
        )
        _require(
            entry.get("restored"),
            "credit ledger entry %s never returned to its initial depth: %s"
            % (entry.get("link_id"), entry),
        )
    for bridge in bridges:
        _require(
            not bridge.get("pending_aw") and not bridge.get("pending_ar")
            and not bridge.get("outstanding_writes")
            and not bridge.get("outstanding_reads") and bridge.get("idle"),
            "bridge on core %s still holds in-flight work: %s"
            % (bridge.get("core_id"), bridge),
        )
    for aperture in aperture_frames:
        for row in aperture.get("transfers", ()):
            _require(
                row.get("notified") or row.get("abandoned")
                or row.get("uncovered_bytes") == 0,
                "aperture on core %s kept a live expectation %s"
                % (aperture.get("core_id"), row),
            )
    if endpoint is not None:
        _require(
            endpoint.get("idle") in (1, True),
            "the target endpoint is not idle at the exit point: %s" % endpoint,
        )
    _require(isinstance(drain, dict), "result has no drain window")
    _require(
        drain.get("instances_drained") == instances,
        "the drain gate closed %s instances but %s ran"
        % (drain.get("instances_drained"), instances),
    )
    if drain.get("active"):
        _require(
            drain.get("end_tick", 0) >= drain.get("begin_tick", 0),
            "the drain window ticks are inverted: %s" % drain,
        )
        _require(
            drain.get("end_pending") == 0,
            "the exit point still has %s in-flight flits or credits"
            % drain.get("end_pending"),
        )
    return {
        "begin_tick": drain.get("begin_tick", 0),
        "begin_pending": drain.get("begin_pending", 0),
        "end_tick": drain.get("end_tick", 0),
        "end_pending": drain.get("end_pending", 0),
    }


def reconcile(
    *,
    cause,
    result,
    schedule,
    expected_rows,
    error_descriptors,
    fault_occurrence,
    instances,
    traffic_multiplier,
    read_payload_digests=None,
    fault_models=None,
):
    """Verify a DONE or ERROR_DRAINED run against the published oracle.

    ``fault_models`` maps a faulted descriptor to
    ``{"burst_useful_bytes": [...], "failed_burst": k}``.  Every burst of the
    execution except the failed one still commits, so the source reports
    ``total - burst_useful_bytes[k]`` over ``len(bursts) - 1`` bursts; a
    descriptor without a model must commit nothing.
    """

    if cause.startswith("MESH_PROGRAM_DONE"):
        outcome = "done"
    elif cause.startswith("MESH_PROGRAM_ERROR_DRAINED"):
        outcome = "error_drained"
    else:
        raise ReconciliationError(f"cause is not a reconcilable completion: {cause}")

    _require(instances >= 1, "instances must be positive")
    _require(traffic_multiplier >= 1, "traffic multiplier must be positive")
    _require(
        len(result["instances"]) == instances,
        "result carries %d instances but %d were requested"
        % (len(result["instances"]), instances),
    )
    instance_ids = [instance["instance"] for instance in result["instances"]]
    _require(
        len(set(instance_ids)) == len(instance_ids),
        "result duplicates an instance identity: %s" % instance_ids,
    )
    _require(
        sorted(instance_ids) == list(range(1, instances + 1)),
        "instance identities %s do not match the %d requested instances"
        % (instance_ids, instances),
    )

    admitted = _admitted_descriptors(schedule)
    oracle = _execution_oracle(expected_rows, admitted)
    plan = _dispatched_plan(result)
    plan_cores = set(plan)
    for instance in result["instances"]:
        core_ids = {int(core_id) for core_id in instance["cores"]}
        _require(
            core_ids == plan_cores,
            "instance %d core set %s does not match the dispatched plan %s"
            % (instance["instance"], sorted(core_ids), sorted(plan_cores)),
        )
    descriptors_of = {}
    transferred = {}
    for descriptor_id, row in oracle.items():
        descriptors_of.setdefault(row["command_id"], []).append(descriptor_id)
        if row["transfer_id"]:
            transferred.setdefault(row["transfer_id"], []).append(descriptor_id)
    for descriptor_ids in descriptors_of.values():
        descriptor_ids.sort()
    for descriptor_ids in transferred.values():
        descriptor_ids.sort()
    injected = sorted({int(value) for value in error_descriptors if str(value) != ""})
    for descriptor_id in injected:
        _require(
            descriptor_id in oracle,
            "injected descriptor %d is not an admitted descriptor" % descriptor_id,
        )
    commands = schedule["sections"]["COMMANDS"]
    attrs_by_index = {
        index + 1: attr for index, attr in enumerate(schedule["sections"]["OP_ATTRS"])
    }

    actual = {}
    for row in result["transport"]:
        descriptor_id = row["descriptor_id"]
        _require(
            descriptor_id not in actual,
            "transport duplicates descriptor %d" % descriptor_id,
        )
        actual[descriptor_id] = row
    _require(
        set(actual) <= set(oracle),
        "traffic reports descriptors without an admitted group: %s"
        % sorted(set(actual) - set(oracle)),
    )

    executions = {}
    contexts = {}
    terminals = {}
    issued = {}
    lifecycles = {}
    peer_transfers = {}
    completed_commands = set()
    for instance in result["instances"]:
        instance_id = instance["instance"]
        for core_id, core in instance["cores"].items():
            core_id = int(core_id)
            _require(
                core_id in plan,
                "instance %d has core %d outside the dispatched plan"
                % (instance_id, core_id),
            )
            expected = {
                (command, generation): descriptors_of.get(command, [])
                for command, generation in plan[core_id]
            }
            for row in core["terminals"]:
                key = (row["command_id"], row["generation"])
                scope = (instance_id, core_id, key[0], key[1])
                _require(
                    scope not in terminals,
                    "instance %d core %d duplicates terminal %s"
                    % (instance_id, core_id, key),
                )
                _require(
                    key in expected,
                    "instance %d core %d terminates command %d generation %d "
                    "outside the plan" % (instance_id, core_id, key[0], key[1]),
                )
                _require(
                    row["state"] in _TERMINAL_STATES,
                    "instance %d core %d records terminal state %r that is not "
                    "enumerated" % (instance_id, core_id, row["state"]),
                )
                if outcome == "done":
                    _require(
                        row["state"] == "completed",
                        "a healthy completion records a %s terminal for command "
                        "%d generation %d" % (row["state"], key[0], key[1]),
                    )
                terminals[scope] = row["state"]
                if row["state"] == "completed":
                    completed_commands.add(key[0])
            _require(
                {(scope[2], scope[3]) for scope in terminals if scope[:2] == (instance_id, core_id)}
                == set(expected),
                "instance %d core %d terminal set does not partition the plan"
                % (instance_id, core_id),
            )

            for row in core["observations"]["commands"]:
                key = (row["command_id"], row["generation"])
                scope = (instance_id, core_id, key[0], key[1])
                _require(
                    scope not in issued,
                    "instance %d core %d duplicates command observation %s"
                    % (instance_id, core_id, key),
                )
                _require(
                    key in expected,
                    "instance %d core %d observes command %d generation %d "
                    "outside the plan" % (instance_id, core_id, key[0], key[1]),
                )
                _require(
                    bool(row["issued"]) == (row["issue_tick"] is not None),
                    "instance %d core %d command %d generation %d issued flag "
                    "does not match its issue tick"
                    % (instance_id, core_id, key[0], key[1]),
                )
                _require(
                    bool(row["terminal"]) and row["terminal_tick"] is not None,
                    "instance %d core %d command %d generation %d has no terminal "
                    "record" % (instance_id, core_id, key[0], key[1]),
                )
                if row["issued"]:
                    _require(
                        row["terminal_tick"] >= row["issue_tick"],
                        "instance %d core %d command %d generation %d terminates "
                        "at tick %s before its issue at tick %s"
                        % (
                            instance_id,
                            core_id,
                            key[0],
                            key[1],
                            row["terminal_tick"],
                            row["issue_tick"],
                        ),
                    )
                issued[scope] = bool(row["issued"])
                lifecycles[scope] = (row["issue_tick"], row["terminal_tick"])
            _require(
                {scope for scope in issued if scope[:2] == (instance_id, core_id)}
                == {
                    (instance_id, core_id, key[0], key[1]) for key in expected
                },
                "instance %d core %d command observations do not cover the plan"
                % (instance_id, core_id),
            )

            for row in core["observations"]["descriptor_executions"]:
                key = (
                    instance_id,
                    core_id,
                    row["command_id"],
                    row["generation"],
                    row["descriptor_id"],
                )
                _require(
                    key not in executions,
                    "execution %s is recorded more than once" % (key,),
                )
                _require(
                    row["descriptor_id"] in admitted,
                    "descriptor %d executed without an admitted descriptor"
                    % row["descriptor_id"],
                )
                _require(
                    admitted[row["descriptor_id"]]["command_id"] == row["command_id"],
                    "descriptor %d executed under command %d but belongs to command %d"
                    % (
                        row["descriptor_id"],
                        row["command_id"],
                        admitted[row["descriptor_id"]]["command_id"],
                    ),
                )
                _require(
                    oracle[row["descriptor_id"]]["owner_core"] == core_id,
                    "descriptor %d executed on core %d but is owned by core %d"
                    % (
                        row["descriptor_id"],
                        core_id,
                        oracle[row["descriptor_id"]]["owner_core"],
                    ),
                )
                _require(
                    (row["command_id"], row["generation"]) in expected,
                    "descriptor %d executed under command %d generation %d "
                    "outside the plan"
                    % (row["descriptor_id"], row["command_id"], row["generation"]),
                )
                _require(
                    row["submit_tick"] is not None,
                    "descriptor %d execution has no submission" % row["descriptor_id"],
                )
                _require(
                    bool(row["completed"])
                    == (row["completion_tick"] is not None),
                    "descriptor %d execution completion flag does not match its "
                    "completion tick" % row["descriptor_id"],
                )
                issue_tick, terminal_tick = lifecycles[
                    (instance_id, core_id, row["command_id"], row["generation"])
                ]
                state = terminals[
                    (instance_id, core_id, row["command_id"], row["generation"])
                ]
                _require(
                    issue_tick is not None,
                    "descriptor %d execution %s belongs to a command that was "
                    "never issued" % (row["descriptor_id"], key),
                )
                _require(
                    issue_tick <= row["submit_tick"] <= terminal_tick,
                    "descriptor %d execution %s submits at tick %s outside the "
                    "lifecycle [%s, %s] of command %d generation %d"
                    % (
                        row["descriptor_id"],
                        key,
                        row["submit_tick"],
                        issue_tick,
                        terminal_tick,
                        row["command_id"],
                        row["generation"],
                    ),
                )
                for event in ("completion_tick", "commit_tick"):
                    if row[event] is None:
                        continue
                    _require(
                        row[event] >= row["submit_tick"],
                        "descriptor %d execution %s records %s at tick %s before "
                        "its submission at tick %s"
                        % (
                            row["descriptor_id"],
                            key,
                            event,
                            row[event],
                            row["submit_tick"],
                        ),
                    )
                    if state == "completed":
                        _require(
                            row[event] <= terminal_tick,
                            "descriptor %d execution %s records %s at tick %s "
                            "after its completed command %d generation %d "
                            "terminated at tick %s"
                            % (
                                row["descriptor_id"],
                                key,
                                event,
                                row[event],
                                row["command_id"],
                                row["generation"],
                                terminal_tick,
                            ),
                        )
                executions[key] = row

            for row in core["observations"].get("transfers", []):
                transfer_key = (instance_id, core_id, row["transfer_id"])
                _require(
                    transfer_key not in peer_transfers,
                    "instance %d core %d duplicates transfer observation %d"
                    % (instance_id, core_id, row["transfer_id"]),
                )
                peer_transfers[transfer_key] = row

    ordered = {}
    for key in executions:
        ordered.setdefault(key[4], []).append(key)
    for keys in ordered.values():
        keys.sort(key=lambda key: (key[0], executions[key]["submit_tick"]))

    faulted_keys = set()
    for descriptor_id in injected:
        keys = ordered.get(descriptor_id, [])
        if fault_occurrence == 0:
            faulted_keys.update(keys)
        elif fault_occurrence <= len(keys):
            faulted_keys.add(keys[fault_occurrence - 1])
    if outcome == "done":
        _require(
            not faulted_keys,
            "a healthy completion was configured to fault %s"
            % sorted({key[4] for key in faulted_keys}),
        )
    else:
        _require(
            faulted_keys,
            "an error drain has no execution for fault occurrence %d of %s"
            % (fault_occurrence, injected),
        )

    for key, row in executions.items():
        oracle_row = oracle[key[4]]
        kind = oracle_row["kind"]
        size = oracle_row["execution_bytes"]
        bursts = oracle_row["execution_bursts"]
        _require(
            row["completed"],
            "descriptor %d execution %s did not retire" % (key[4], key),
        )
        if key in faulted_keys:
            _require(
                row["status"] == _DMA_ERROR_STATUS.get(kind),
                "descriptor %d execution %s status %s does not match the "
                "admitted DMA direction fault model"
                % (key[4], key, row["status"]),
            )
            _require(
                not row["committed"] and row["commit_tick"] is None,
                "faulted descriptor %d execution %s recorded a commit" % (key[4], key),
            )
            model = (fault_models or {}).get(key[4])
            if model is None:
                _require(
                    _empty_transfer(row["transfer"]),
                    "faulted descriptor %d execution %s produced traffic: %s"
                    % (key[4], key, row["transfer"]),
                )
                _require(
                    not row["transfer"].get("payload_digest")
                    and row["landing_digest"] is None
                    and not row["sentinel_ranges"],
                    "faulted descriptor %d execution %s kept landing evidence"
                    % (key[4], key),
                )
                continue
            _require(
                not row["sentinel_ranges"],
                "faulted descriptor %d execution %s kept sentinel evidence"
                % (key[4], key),
            )
            burst_bytes = list(model.get("burst_useful_bytes", ()))
            failed_burst = model.get("failed_burst")
            _require(
                burst_bytes and isinstance(failed_burst, int)
                and 0 <= failed_burst < len(burst_bytes),
                "faulted descriptor %d has no admitted burst model" % key[4],
            )
            _require(
                kind in _AXI_DIRECTION_FIELDS,
                "faulted descriptor %d execution %s has kind %d that issues no "
                "AXI burst" % (key[4], key, kind),
            )
            byte_field, burst_field = _AXI_DIRECTION_FIELDS[kind]
            committed = row["transfer"].get(byte_field, 0)
            committed_bursts = row["transfer"].get(burst_field, 0)
            expected_committed = sum(burst_bytes) - burst_bytes[failed_burst]
            _require(
                committed == expected_committed
                and committed_bursts == len(burst_bytes) - 1,
                "faulted descriptor %d execution %s committed %d bytes over %d "
                "bursts, but only burst %d of %s failed"
                % (key[4], key, committed, committed_bursts, failed_burst,
                   burst_bytes),
            )
            _require(
                committed < size,
                "faulted descriptor %d execution %s committed its whole payload"
                % (key[4], key),
            )
            _require(
                not any(
                    row["transfer"].get(field, 0)
                    for field in _TRAFFIC_FIELDS
                    if field not in (byte_field, burst_field)
                ),
                "faulted descriptor %d execution %s moved traffic of the wrong "
                "direction: %s" % (key[4], key, row["transfer"]),
            )
            continue
        _require(
            row["status"] == "OK",
            "descriptor %d execution %s errored without being configured"
            % (key[4], key),
        )
        _require(
            row["committed"] and row["commit_tick"] is not None,
            "descriptor %d execution %s reported OK without a commit"
            % (key[4], key),
        )
        if size == 0:
            _require(
                _empty_transfer(row["transfer"]),
                "zero-byte descriptor %d execution %s produced traffic"
                % (key[4], key),
            )
            expected_payload = _read_payload_oracle(
                key[4], kind, read_payload_digests
            )
            if expected_payload is None:
                expected_payload = _payload_oracle(
                    kind,
                    0,
                    _fill_pattern(attrs_by_index, commands, key[2]),
                )
            if expected_payload is not None:
                _require(
                    row["transfer"].get("payload_digest") == expected_payload,
                    "zero-byte descriptor %d execution %s payload does not match "
                    "the oracle" % (key[4], key),
                )
            continue
        expected_transfer = _expected_execution_transfer(kind, size, bursts)
        for field, value in expected_transfer.items():
            _require(
                row["transfer"].get(field) == value,
                "descriptor %d execution %s %s mismatch: is %s but the "
                "oracle expects %s"
                % (key[4], key, field, row["transfer"].get(field), value),
            )
        expected_payload = _read_payload_oracle(
            key[4], kind, read_payload_digests
        )
        if expected_payload is None:
            expected_payload = _payload_oracle(
                kind,
                size,
                _fill_pattern(attrs_by_index, commands, key[2]),
            )
        if expected_payload is not None:
            _require(
                row["transfer"].get("payload_digest") == expected_payload,
                "descriptor %d execution %s payload does not match the oracle"
                % (key[4], key),
            )
        else:
            _require(
                row["transfer"].get("payload_digest")
                and row["transfer"]["payload_digest"] != payload_digest(bytes(size)),
                "descriptor %d execution %s payload shows no content flow"
                % (key[4], key),
            )

    summary_descriptors = []
    for descriptor_id in sorted(oracle):
        oracle_row = oracle[descriptor_id]
        command_id = oracle_row["command_id"]
        keys = ordered.get(descriptor_id, [])
        rows = [executions[key] for key in keys]
        planned = (
            len(
                [
                    key
                    for key in plan[oracle_row["owner_core"]]
                    if key[0] == command_id
                ]
            )
            * instances
            * traffic_multiplier
        )
        got = actual.get(descriptor_id)
        if got is None:
            _require(
                not rows,
                "descriptor %d has executions but no traffic row" % descriptor_id,
            )
            _require(
                command_id not in completed_commands,
                "descriptor %d has no traffic while command %d completed"
                % (descriptor_id, command_id),
            )
            summary_descriptors.append(
                {
                    "descriptor_id": descriptor_id,
                    "kind": oracle_row["kind"],
                    "command_id": command_id,
                    "planned_executions": planned,
                    "observed_executions": 0,
                    "faulted_executions": 0,
                    "successful_executions": 0,
                    "attempted": False,
                    "expected_success_bytes": 0,
                    "actual_bytes": {},
                }
            )
            continue
        _require(
            rows,
            "descriptor %d has a traffic row without any execution" % descriptor_id,
        )
        _require(
            len(rows) <= planned,
            "descriptor %d executed %d times beyond its %d planned executions"
            % (descriptor_id, len(rows), planned),
        )
        for field in _TRAFFIC_FIELDS:
            total = sum(row["transfer"].get(field, 0) for row in rows)
            _require(
                got.get(field) == total,
                "descriptor %d %s is %s but its executions sum to %s"
                % (descriptor_id, field, got.get(field), total),
            )
        successful_rows = [row for row in rows if row["status"] == "OK"]
        digest_rows = successful_rows or [
            row for row in rows if row["transfer"].get("payload_digest")
        ]
        if digest_rows:
            _require(
                got.get("payload_digest")
                == digest_rows[-1]["transfer"].get("payload_digest"),
                "descriptor %d payload digest does not match its last committed "
                "execution" % descriptor_id,
            )
        else:
            _require(
                not got.get("payload_digest"),
                "descriptor %d has no successful execution but recorded a payload"
                % descriptor_id,
            )
        faulted = len(rows) - len(successful_rows)
        summary_descriptors.append(
            {
                "descriptor_id": descriptor_id,
                "kind": oracle_row["kind"],
                "command_id": command_id,
                "attempted": True,
                "planned_executions": planned,
                "observed_executions": len(rows),
                "faulted_executions": faulted,
                "successful_executions": len(successful_rows),
                "expected_success_bytes": oracle_row["execution_bytes"]
                * len(successful_rows),
                "actual_bytes": {
                    "read_bytes": got.get("read_bytes", 0),
                    "write_bytes": got.get("write_bytes", 0),
                    "fill_bytes": got.get("fill_bytes", 0),
                    "p2p_bytes": got.get("p2p_bytes", 0),
                },
            }
        )

    for (instance_id, core_id, command_id, generation), state in terminals.items():
        key = (command_id, generation)
        descriptor_ids = descriptors_of.get(command_id, [])
        command_issued = issued.get((instance_id, core_id, command_id, generation)) or False
        present = [
            executions[(instance_id, core_id, command_id, generation, descriptor_id)]
            for descriptor_id in descriptor_ids
            if (instance_id, core_id, command_id, generation, descriptor_id)
            in executions
        ]
        if state == "completed":
            _require(
                command_issued,
                "instance %d core %d completed command %d generation %d was never "
                "issued" % (instance_id, core_id, key[0], key[1]),
            )
            _require(
                len(present) == len(descriptor_ids) * traffic_multiplier,
                "instance %d core %d completed command %d generation %d is missing "
                "descriptor executions"
                % (instance_id, core_id, key[0], key[1]),
            )
            _require(
                all(row["status"] == "OK" for row in present),
                "instance %d core %d completed command %d generation %d has a "
                "non-OK execution" % (instance_id, core_id, key[0], key[1]),
            )
        elif state == "errored":
            _require(
                command_issued,
                "instance %d core %d errored command %d generation %d was never "
                "issued" % (instance_id, core_id, key[0], key[1]),
            )
        else:
            _require(
                command_issued or not present,
                "instance %d core %d cancelled command %d generation %d was never "
                "issued but executed" % (instance_id, core_id, key[0], key[1]),
            )

    for (instance_id, core_id, transfer_id), observation in peer_transfers.items():
        descriptor_ids = transferred.get(transfer_id)
        _require(
            descriptor_ids,
            "instance %d core %d reports transfer %d without admitted descriptors"
            % (instance_id, core_id, transfer_id),
        )
        keys = [
            (instance_id, core_id, command, generation, descriptor_id)
            for command, generation in plan[core_id]
            for descriptor_id in descriptors_of.get(command, [])
            if descriptor_id in descriptor_ids
        ]
        present = [executions[key] for key in keys if key in executions]
        committed = [row for row in present if row["committed"]]
        expected_bytes = sum(
            oracle[descriptor_id]["execution_bytes"] for descriptor_id in descriptor_ids
        )
        _require(
            observation["expected_descriptors"] == len(descriptor_ids)
            and observation["expected_bytes"] == expected_bytes,
            "instance %d core %d transfer %d disagrees with the admitted descriptor "
            "set" % (instance_id, core_id, transfer_id),
        )
        _require(
            observation["committed_descriptors"] == len(committed)
            and observation["committed_bytes"]
            == sum(oracle[key[4]]["execution_bytes"] for key in keys
                   if key in executions and executions[key]["committed"]),
            "instance %d core %d transfer %d committed set disagrees with the "
            "descriptor executions" % (instance_id, core_id, transfer_id),
        )

    return {
        "status": "ok",
        "outcome": outcome,
        "cause": cause,
        "fault_occurrence": fault_occurrence,
        "injected_descriptors": injected,
        "descriptors": summary_descriptors,
    }
