from mesh_ir.runtime_errors import require as _require


_BYTE_FIELDS = ("read_bytes", "write_bytes", "p2p_bytes", "fill_bytes")
_BURST_FIELDS = ("read_bursts", "write_bursts", "p2p_bursts")


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
        isinstance(admitted_transactions, dict),
        "admitted P2P transfers must be a mapping",
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
