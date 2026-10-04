"""Check the RTL CPU workload's observed native CHI behavior without refinement.

Addresses select known workload objects only. Flow root/parent identities join
the local release, CMO and memory child; wire identifiers check each handshake.
This complements, and does not replace, the closed check-native-wire.py gate.
"""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path


def number(value):
    return int(value, 0) if isinstance(value, str) else int(value)


def opcode(event):
    return number(event.get("fields", {}).get("opcode", -1))


class BehaviorAudit:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.legs = defaultdict(list)
        self.receives = defaultdict(list)
        self.issues = []
        self.checks = Counter()

    def require(self, condition, rule, detail):
        self.checks[rule] += 1
        if not condition:
            raise ValueError(f"{rule}: {detail}")

    def before(self, first, second, detail):
        self.require(first["_index"] < second["_index"] and
                     number(first["tick"]) <= number(second["tick"]), "causal-order", detail)

    def select(self, key, channel, op=None, source=None, target=None):
        return [e for e in self.legs.get(key, []) if e.get("kind") == "flit"
                and e.get("observation") == "TX" and e.get("channel") == channel
                and (op is None or opcode(e) == op)
                and (source is None or e.get("source") == source)
                and (target is None or e.get("target") == target)]

    def one(self, events, detail):
        self.require(len(events) == 1, "event-count", f"{detail}: found {len(events)}")
        return events[0]

    def received(self, event):
        self.require(bool(event.get("messageId")), "received-message", "missing stable message identity")
        rx = self.one(self.receives[event["messageId"]], f"RX for {event['id']}")
        for name in ("rootId", "legId", "parentLegId", "channel", "source", "target",
                     "wireEncoding", "fields", "payload", "byteEnable", "validMask"):
            self.require(rx.get(name) == event.get(name), "received-message", f"TX/RX {name} mismatch")
        self.before(event, rx, f"TX must precede RX: {event['id']}")
        return rx

    def request(self, key, source=None):
        requests = self.select(key, "REQ", source=source)
        self.require(bool(requests), "request", f"no request in explicit leg {key}")
        # Retry attempts retain the stable leg. The closed raw gate verifies the
        # RetryAck/PCrdGrant history; behavior checks use the final wire request.
        request = requests[-1]
        self.received(request)
        self.require(request.get("wireEncoding") == "native-chi", "native-wire", "non-native request")
        self.require(bool(key[0]) and bool(key[1]), "flow-identity", "missing root or leg")
        parents = {e.get("parentLegId", "") for e in self.legs[key]}
        self.require(len(parents) == 1, "flow-identity", "leg has inconsistent explicit parent")
        return request

    def response(self, key, request, op):
        response = self.one(self.select(key, "RSP", op, request["target"], request["source"]),
                            f"RSP {op:#x} in {key}")
        req_fields, rsp_fields = request["fields"], response["fields"]
        self.require(rsp_fields.get("srcid") == req_fields.get("tgtid") and
                     rsp_fields.get("tgtid") == req_fields.get("srcid") and
                     rsp_fields.get("txnid") == req_fields.get("txnid"),
                     "response-identity", "response must address the original requester TxnID")
        self.before(self.received(request), response, "request reception before response")
        self.received(response)
        return response

    def ack(self, key, request, completion):
        ack = self.one(self.select(key, "RSP", 2, request["source"], request["target"]),
                       f"CompAck in {key}")
        self.require(ack["fields"].get("txnid") == completion["fields"].get("dbid"),
                     "compack-dbid", "CompAck must use completion DBID")
        self.require(ack["fields"].get("srcid") == request["fields"].get("srcid") and
                     ack["fields"].get("tgtid") == request["fields"].get("tgtid"),
                     "compack-dbid", "CompAck endpoint identity changed")
        self.before(self.received(completion), ack, "CompAck after requester receives completion")
        self.received(ack)
        return ack

    def data(self, key, request, dbid_response, op, clean_only=False):
        beats = self.select(key, "DAT", op, request["source"], request["target"])
        self.require(len(beats) == 2, "data-coverage", "64-byte transfer requires exactly two DAT beats")
        values, ids, rx = bytearray(64), set(), []
        for beat in beats:
            fields = beat["fields"]
            data_id = number(fields.get("dataid", -1))
            self.require(data_id in (0, 2) and data_id not in ids, "data-coverage", "duplicate/wrong DataID")
            ids.add(data_id)
            self.require(fields.get("txnid") == dbid_response["fields"].get("dbid") and
                         fields.get("srcid") == request["fields"].get("srcid") and
                         fields.get("tgtid") == request["fields"].get("tgtid"),
                         "write-dbid", "DAT must address DBID and original endpoints")
            payload = bytes.fromhex(beat.get("payload", ""))
            valid = bytes.fromhex(beat.get("validMask", ""))
            enabled = bytes.fromhex(beat.get("byteEnable", ""))
            self.require(len(payload) == len(valid) == len(enabled) == 32,
                         "data-coverage", "DAT must contain 32 natural lanes")
            is_invalid_copy = op == 2 and fields.get("resp") == 0
            self.require(valid == bytes([255]) * 32, "known-data", "unknown bytes cannot prove data preservation")
            self.require(enabled == bytes([0 if is_invalid_copy else 255]) * 32,
                         "byte-enable", "expected full copy or explicit invalid-copy byte enables")
            if clean_only:
                self.require(fields.get("resp") in (0, 1, 2), "clean-eviction", "WEOE must not return dirty data")
            values[data_id * 16:data_id * 16 + 32] = payload
            self.before(self.received(dbid_response), beat, "DAT must follow received DBID allocation")
            rx.append(self.received(beat))
        return bytes(values), max(beats, key=lambda e: e["_index"]), max(rx, key=lambda e: e["_index"])

    def make_unique(self, key):
        request = self.request(key)
        self.require(not self.select(key, "DAT"), "makeunique-no-data", "MakeUnique must not send/receive DAT")
        completion = self.response(key, request, 4)
        self.require(completion["fields"].get("resp") == 2, "makeunique-uc", "MakeUnique requires Comp_UC")
        self.ack(key, request, completion)
        allocations = [e for e in self.legs[key] if e.get("kind") == "allocate"
                       and e.get("node") == request["source"]]
        allocation = self.one(allocations, f"RN online MakeUnique selection in {key}")
        self.require(allocation.get("fields", {}).get("fullOverwritePromise") is True,
                     "makeunique-full-overwrite", "missing observed L1 whole-line overwrite promise")
        return {"rootId": key[0], "legId": key[1], "request": request["id"], "completion": completion["id"]}

    def weoe(self, key):
        request = self.request(key)
        responses = self.select(key, "RSP", 4, request["target"], request["source"]) + \
                    self.select(key, "RSP", 5, request["target"], request["source"])
        completion = self.one(responses, f"one WEOE completion branch in {key}")
        self.response(key, request, opcode(completion))
        if opcode(completion) == 4:
            self.require(not self.select(key, "DAT"), "weoe-comp-only", "Comp-only WEOE unexpectedly has DAT")
            self.ack(key, request, completion)
            branch = "Comp_CompAck"
        else:
            self.require(not self.select(key, "RSP", 2), "weoe-data", "DAT is implicit acknowledgment; extra CompAck")
            self.data(key, request, completion, 2, clean_only=True)
            branch = "CompDBIDResp_CopyBackWriteData"
        return branch

    def cmo(self, address, expected_op, release_op, expected_value):
        candidates = [key for key in self.legs if any(
            e.get("source") == "RN0" and opcode(e) == expected_op and number(e.get("address", -1)) == address
            for e in self.select(key, "REQ"))]
        key = self.one(candidates, f"known CPU CMO at {address:#x}")
        request = self.request(key, "RN0")
        parent = (key[0], request.get("parentLegId", ""))
        self.require(bool(parent[1]) and parent != key and parent in self.legs,
                     "explicit-parent", "CMO must name its actual release parent")
        release = self.request(parent, "RN0")
        self.require(opcode(release) == release_op and number(release["address"]) == address,
                     "release-parent", "CMO parent is not the expected same-line WriteClean/WriteBack")
        release_response = self.response(parent, release, 5)
        release_data, release_tx, release_rx = self.data(parent, release, release_response, 2)
        self.require(release_data[:8] == expected_value.to_bytes(8, "little"),
                     "cpu-value", "release does not contain the known CPU-written value")
        self.require(all(e["fields"].get("resp") in (6, 7) for e in self.select(parent, "DAT", 2)),
                     "dirty-release", "target CPU rewrite must transfer dirty responsibility")
        self.before(release_tx, request, "L2 local release DAT sent before its CMO child")
        children = [child for child in self.legs if child[0] == key[0] and any(
            e.get("parentLegId") == key[1] and opcode(e) == 0x1d and e.get("source", "").startswith("HN")
            for e in self.select(child, "REQ"))]
        memory_key = self.one(children, f"explicit HN memory-write child of {key}")
        memory = self.request(memory_key)
        self.require(memory["source"] == request["target"] and memory["target"].startswith("SN") and
                     number(memory["address"]) == address, "memory-child", "wrong child endpoint/address")
        self.before(self.received(request), memory, "CMO received before its memory child")
        self.before(release_rx, memory, "HN must receive the new release data before writing SN")
        dbid = self.response(memory_key, memory, 6)
        memory_data, _, memory_rx = self.data(memory_key, memory, dbid, 3)
        self.require(memory_data == release_data, "memory-value", "HN to SN data differs from actual RN release bytes")
        memory_requests = [e for e in self.legs[memory_key] if e.get("kind") == "memory_request"
                           and e.get("node") == memory["target"]]
        memory_responses = [e for e in self.legs[memory_key] if e.get("kind") == "memory_response"
                            and e.get("node") == memory["target"]]
        memory_request = self.one(memory_requests, "actual SN memory-port write acceptance")
        memory_response = self.one(memory_responses, "actual SN memory-port write response")
        for event in (memory_request, memory_response):
            self.require(event.get("fields", {}).get("isWrite") is True and
                         event.get("fields", {}).get("sizeBytes") == 64 and
                         number(event.get("address", -1)) == address,
                         "memory-port", "memory-port write identity/size differs from explicit child")
        self.require(bytes.fromhex(memory_request.get("payload", "")) == release_data,
                     "memory-port-value", "actual accepted memory write differs from RN release bytes")
        self.require(bytes.fromhex(memory_request.get("byteEnable", "")) == bytes([255]) * 64 and
                     bytes.fromhex(memory_request.get("validMask", "")) == bytes([255]) * 64,
                     "memory-port-value", "actual memory write contains disabled/unknown bytes")
        self.before(memory_rx, memory_request, "SN DAT reception before actual memory-port write acceptance")
        self.before(memory_request, memory_response, "actual memory write response follows accepted write")
        sn_comp = self.response(memory_key, memory, 4)
        self.require(sn_comp["fields"].get("dbid") == dbid["fields"].get("dbid"),
                     "memory-dbid", "SN completion DBID differs from its write allocation")
        self.before(memory_response, sn_comp, "actual memory write response precedes SN Comp")
        cmo_comp = self.response(key, request, 4)
        self.before(self.received(sn_comp), cmo_comp, "HN receives SN completion before completing CMO")
        return {"address": hex(address), "rootId": key[0], "releaseLegId": parent[1],
                "cmoLegId": key[1], "memoryLegId": memory_key[1], "releaseRequest": release["id"],
                "cmoRequest": request["id"], "memoryRequest": memory["id"],
                "memoryPortRequest": memory_request["id"], "memoryPortResponse": memory_response["id"],
                "snCompletion": sn_comp["id"], "cmoCompletion": cmo_comp["id"],
                "expectedFirstWord": hex(expected_value), "comparedBytes": 64}

    def run(self):
        result = json.loads((self.folder / "native-l2-result.json").read_text(encoding="utf-8"))
        with (self.folder / "raw.jsonl").open(encoding="utf-8") as source:
            manifest = json.loads(next(source))
            for index, line in enumerate(source):
                event = json.loads(line)
                if event.get("type") != "observed" or event.get("kind") not in (
                        "flit", "allocate", "memory_request", "memory_response"):
                    continue
                event["_index"] = index
                self.legs[event.get("rootId", ""), event.get("legId", "")].append(event)
                if event.get("kind") == "flit" and event.get("observation") == "RX":
                    self.receives[event.get("messageId", "")].append(event)
        if manifest.get("profile") != "h-native-256":
            self.issues.append("native manifest profile required")
        branches, mu, cmos = Counter(), [], []
        for key in self.legs:
            requests = self.select(key, "REQ")
            if not requests or not requests[-1].get("source", "").startswith("RN"):
                continue
            op = opcode(requests[-1])
            try:
                if op == 12:
                    mu.append(self.make_unique(key))
                elif op == 0x42:
                    branches[self.weoe(key)] += 1
            except (ValueError, KeyError, TypeError) as error:
                self.issues.append(f"{key}: {error}")
        if not mu:
            self.issues.append("no completed MakeUnique with observed whole-line overwrite promise")
        for branch in ("Comp_CompAck", "CompDBIDResp_CopyBackWriteData"):
            if not branches[branch]:
                self.issues.append(f"missing completed WEOE branch: {branch}")
        for offset, op, release, value in ((0x1000, 8, 0x17, 0xaabbccdd01020304),
                                           (0x1100, 9, 0x1b, 0xddccbbaa04030201)):
            try:
                cmos.append(self.cmo(number(result["sharedPhysical"]) + offset, op, release, value))
            except (ValueError, KeyError, TypeError) as error:
                self.issues.append(f"CMO offset {offset:#x}: {error}")
        report = {"pass": not self.issues, "checkCount": sum(self.checks.values()),
                  "checksByRule": dict(self.checks), "writeEvictOrEvictBranches": dict(branches),
                  "makeUniqueCount": len(mu), "makeUnique": mu, "cmoMemoryChecks": cmos,
                  "issues": self.issues,
                  "scope": "Observed native TX/RX causal behavior using explicit root/parent legs. "
                           "Compares RN release, HN-to-SN DAT, and the actual accepted memory-port "
                           "write bytes, then orders the actual memory response before SN/CMO Comp. "
                           "No RTL lockstep or full CHI transaction checker proof."}
        (self.folder / "native-rtl-behavior.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    report = BehaviorAudit(args.directory).run()
    print(json.dumps({key: value for key, value in report.items() if key != "makeUnique"}, indent=2))
    raise SystemExit(0 if report["pass"] else 1)
