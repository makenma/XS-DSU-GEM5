from mesh_ir.runtime_errors import require


def verified_drain(garnet, credit_ledger, drain, bridges, aperture_frames, endpoints,
                   instances=1):
    require(isinstance(garnet, dict), "result has no Garnet snapshot")
    require(
        garnet.get("quiescent") in (1, True),
        "the network is not quiescent at the exit point: %s" % garnet,
    )
    require(all(type(garnet.get(field)) is int and garnet[field] == 0 for field in (
        'ni_queued_flits', 'ni_queued_messages', 'router_buffered_flits', 'non_idle_input_vcs',
        'non_idle_output_vcs', 'data_link_pending_flits', 'credit_link_pending_credits',
        'bridge_pending_items', 'credit_deficit')), 'the network has residual work despite its quiescent flag')
    require(
        isinstance(credit_ledger, list) and credit_ledger,
        "the credit ledger was not published",
    )
    for entry in credit_ledger:
        require(all(type(entry.get(field)) is int and entry[field] >= 0
                     for field in ("initial", "depth", "sent", "returned", "current")),
                 "credit ledger entry has invalid counters: %s" % entry)
        require(entry["initial"] - entry["sent"] + entry["returned"] == entry["current"]
                 and 0 <= entry["current"] <= entry["depth"],
                 "credit ledger entry %s is not conserved" % entry)
        require(entry["current"] == entry["initial"] and entry["sent"] == entry["returned"],
                 "credit ledger entry %s never returned to its initial depth" % entry)
        require(
            entry.get("conserved"),
            "credit ledger entry %s is not conserved" % entry,
        )
        require(
            entry.get("restored"),
            "credit ledger entry %s never returned to its initial depth: %s"
            % (entry.get("link_id"), entry),
        )
    for bridge in bridges:
        require(
            not bridge.get("pending_aw") and not bridge.get("pending_ar")
            and not bridge.get("outstanding_writes")
            and not bridge.get("outstanding_reads") and bridge.get("idle"),
            "bridge on core %s still holds in-flight work: %s"
            % (bridge.get("core_id"), bridge),
        )
    for aperture in aperture_frames:
        for row in aperture.get("transfers", ()):
            require(
                row.get("notified") or row.get("abandoned")
                or row.get("uncovered_bytes") == 0,
                "aperture on core %s kept a live expectation %s"
                % (aperture.get("core_id"), row),
            )
    require(isinstance(endpoints, list), "result has no physical memory owner list")
    require(len({row["node"] for row in endpoints}) == len(endpoints),
             "physical memory owner identity is repeated")
    for endpoint in endpoints:
        require(
            endpoint.get("idle") in (1, True),
            "the target endpoint is not idle at the exit point: %s" % endpoint,
        )
    require(isinstance(drain, dict), "result has no drain window")
    require(
        drain.get("instances_drained") == instances,
        "the drain gate closed %s instances but %s ran"
        % (drain.get("instances_drained"), instances),
    )
    if drain.get("active"):
        require(
            drain.get("end_tick", 0) >= drain.get("begin_tick", 0),
            "the drain window ticks are inverted: %s" % drain,
        )
        require(
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


def verified_core_drain(cores: list[dict], frames: list[dict], core_ids: tuple[int, ...]) -> None:
    require(len(cores) == len(core_ids) and {row['core_id'] for row in cores} == set(core_ids),
        'physical core drain coverage differs from the architecture')
    resources = list(cores)
    for frame in frames:
        require(frame['finalized'] and {int(core) for core in frame['cores']} == set(core_ids),
            'phase drain coverage differs from the architecture')
        resources.extend(row['resources'] for row in frame['cores'].values())
    for row in resources:
        require(all(type(row.get(field)) is int and row[field] == 0 for field in
            ('live_commands', 'live_dma_commands', 'live_dma_tags', 'allocation_pins')),
            'core retains commands, DMA tags or allocation pins')
        require(set(row['engine_occupancy']) == {'tensor', 'vector', 'reduce'} and
            all(type(value) is int and value == 0 for value in row['engine_occupancy'].values()),
            'core compute engines retain work')
    require(all(row['dma_idle'] == 1 for row in cores), 'core DMA engines are not idle')
