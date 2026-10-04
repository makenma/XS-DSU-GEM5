#!/usr/bin/env python3
"""Check native CHI observed port traffic directly, without chi-refine.

This gate checks the project's finite 64B/256-bit, no DMT/DCT/DWT profile.
It neither synthesizes messages nor repairs opcode, Size, DataID, or identifiers.
Simulator flow IDs are deliberately not used for protocol association.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys


READS = {1, 3, 4, 7, 0x24, 0x25, 0x26}
WRITES = {0x15, 0x17, 0x18, 0x19, 0x1b, 0x1c, 0x1d, 0x42}
COPYBACK = {0x15, 0x17, 0x1b, 0x42}
FULL_WRITES = {0x15, 0x17, 0x19, 0x1b, 0x1d, 0x42}
DATALLESS = {8, 9, 10, 11, 12, 13}
ALLOCATING_READS = {1, 7, 0x26}
CMOS = {8, 9, 10}


def number(value):
    return int(value, 0) if isinstance(value, str) else int(value)


class Audit:
    def __init__(self, path, allow_open=False):
        self.path = str(path)
        self.allow_open = allow_open
        self.rules = Counter()
        self.opcodes = Counter()
        self.errors = []
        self.live = {}
        self.buffers = {}
        self.acks = {}
        self.snoops = {}
        self.credits = Counter()
        self.retries = Counter()
        self.tx = {}
        self.rx = {}
        self.position = "manifest"
        self.nodes = {}
        self.node_bits = 11
        self.address_bits = 48

    def require(self, condition, rule, detail):
        self.rules[rule] += 1
        if not condition:
            raise ValueError(f"{rule}: {detail}")

    def width(self, fields, name, bits, default=None):
        value = fields.get(name, default)
        self.require(value is not None, "raw-field", f"missing {name}")
        value = number(value)
        self.require(0 <= value < 1 << bits, "field-width", f"{name}={value} exceeds {bits} bits")
        return value

    def identity(self, fields):
        source = self.width(fields, "srcid", self.node_bits)
        target = self.width(fields, "tgtid", self.node_bits)
        self.require(source in self.nodes and target in self.nodes,
                     "topology", "wire NodeID absent from capture topology")
        txn = self.width(fields, "txnid", 12)
        self.width(fields, "qos", 4)
        return source, target, txn

    def role(self, node):
        return self.nodes.get(node, "")

    def finish(self, key):
        txn = self.live[key]
        if txn["opcode"] in READS:
            done = txn["data_done"] and (not txn["receipt_needed"] or txn["receipt"])
        elif txn["opcode"] in WRITES:
            done = txn["completion"] and (txn["comp_only"] or txn["data_done"])
        else:
            done = txn["completion"]
        if not done or txn["ack_needed"] and not txn["ack"]:
            return
        if txn["dbid"] is not None:
            self.buffers.pop((txn["target"], txn["source"], txn["dbid"]), None)
        self.live.pop(key)

    def request(self, e, f, source, target, tid):
        op = self.width(f, "opcode", 7)
        self.require(op in READS | WRITES | DATALLESS, "finite-profile", f"unsupported native REQ {op:#x}")
        size = self.width(f, "size", 3)
        self.require(size <= 6, "size-log2", "reserved Size=7")
        addr = number(e["address"])
        self.require(0 <= addr < 1 << self.address_bits, "field-width", "REQ Addr exceeds configured width")
        retry = self.width(f, "allowRetry", 1)
        credit = self.width(f, "pcrdtype", 4)
        order = self.width(f, "order", 2)
        memattr = self.width(f, "memattr", 4)
        snpattr = self.width(f, "snpattr", 1)
        self.width(f, "likelyShared", 1, f.get("LikelyShared", 0))
        if "cah" in f or "CAH" in f:
            self.width(f, "cah", 1, f.get("CAH"))
        ack = self.width(f, "expCompAck", 1)
        return_nid = self.width(f, "returnNid", self.node_bits)
        return_tid = self.width(f, "returnTxnId", 12)
        key = source, tid
        self.require(key not in self.live, "txnid-lifetime", f"reused live requester TxnID {key}")
        if retry:
            self.require(credit == 0, "retry-credit", "AllowRetry=1 requires PCrdType=0")
        else:
            credit_key = source, target, credit
            self.require(self.credits[credit_key] > 0 and self.retries[credit_key] > 0,
                         "retry-credit", "retry needs prior RetryAck and matching node-pair/type credit")
            self.credits[credit_key] -= 1
            self.retries[credit_key] -= 1
        if self.role(source).startswith("RN"):
            self.require(return_nid == 0 and return_tid == 0, "return-fields", "RN request return fields must be zero")
            self.require(order != 1, "order", "RN to HN Order=01 is reserved")
        if self.role(target).startswith("SN"):
            self.require(not ack, "exp-compack", "HN to SN must not request CompAck")
            self.require(order in (0, 1), "order", "HN to SN permits Order=00/01 only")
            if op in WRITES:
                self.require(order == 0, "order", "HN-F to SN-F writes require Order=00")
            if op == 4:
                self.require(return_nid == source and return_tid == tid,
                             "return-fields", "no-DMT ReadNoSnp return identity must be HN request identity")
        if op in ALLOCATING_READS | {11, 12}:
            self.require(ack == 1, "exp-compack", "allocating coherent read requires ExpCompAck")
        if op in CMOS | {13}:
            self.require(ack == 0, "exp-compack", "CMO and Evict must not request CompAck")
        if op in COPYBACK:
            self.require(ack == int(op == 0x42), "exp-compack",
                         "CopyBack ExpCompAck must be one for WriteEvictOrEvict and zero otherwise")
        if op in ALLOCATING_READS | DATALLESS | COPYBACK:
            self.require(size == 6, "size-log2", "allocating read, Dataless and CopyBack require a 64-byte line")
            self.require(order == 0, "order", "coherent line/Dataless/CopyBack request requires Order=00")
        if op in {4, 0x1c, 0x1d}:
            self.require(snpattr == 0, "snoop-attribute", "ReadNoSnp/WriteNoSnp require SnpAttr=0")
        count = 1 << size
        aligned = (addr % 64) & ~(count - 1)
        # Packet count follows Size even for an unaligned Device address.
        # Only the byte access window excludes bytes below that Device address.
        start = addr % 64 if memattr & 2 else aligned
        end = aligned + count
        expected = {0 if offset < 32 else 2 for offset in range(aligned, end)}
        self.live[key] = dict(source=source, target=target, tid=tid, opcode=op, allow_retry=retry,
            credit_type=credit, addr=addr, count=count, start=start, end=end, expected=expected, beats=set(),
            ack_needed=bool(ack) and op not in COPYBACK, ack=False,
            receipt_needed=bool(order and op == 4), receipt=False,
            completion=False, data_done=False, dbid=None, comp_dbid=None, response_seen=False,
            signature=None, comp_only=False, cah=f.get("cah", f.get("CAH")))

    def response(self, e, f, source, target, tid):
        op = self.width(f, "opcode", 5)
        dbid = self.width(f, "dbid", 12)
        resp = self.width(f, "resp", 3)
        resp_err = self.width(f, "respErr", 2)
        credit = self.width(f, "pcrdtype", 4)
        if op == 7:
            # PCrdGrant.TxnID is intentionally never used to find a request.
            self.credits[target, source, credit] += 1
            self.rules["pcredit-independent-txnid"] += 1
            return
        if op == 1:
            key = target, source, tid
            self.require(key in self.snoops, "snoop-identity", f"unmatched SnpResp {key}")
            self.require(resp <= 6, "snoop-response", "SnpResp Resp=7 reserved")
            self.snoops.pop(key)
            return
        if op == 2:
            self.require(resp_err == 0, "compack-response", "CompAck RespErr must be zero")
            key = target, source, tid
            self.require(key in self.acks, "compack-dbid", f"unmatched CompAck {key}; TxnID must be completion DBID")
            request_key = self.acks.pop(key)
            txn = self.live[request_key]
            if txn["opcode"] in COPYBACK:
                self.require(resp in (0, 1, 2) if txn["opcode"] in (0x15, 0x42) else resp in (0, 1, 2, 6, 7),
                             "copyback-response", "CompAck cache state is not permitted for this CopyBack request")
            else:
                self.require(resp == 0, "compack-response", "non-CopyBack CompAck Resp is inapplicable and must be zero")
            self.require(txn["data_done"] or txn["comp_only"] or txn["opcode"] in DATALLESS,
                         "compack-order", "CompAck before required data completed")
            txn["ack"] = True
            self.finish(request_key)
            return
        key = target, tid
        self.require(key in self.live, "response-identity", f"unmatched RSP {key}")
        txn = self.live[key]
        self.require(source == txn["target"], "response-identity", "wrong completer")
        if op == 3:
            self.require(txn["allow_retry"] and not txn["response_seen"], "retry-ack", "RetryAck after response or AllowRetry=0")
            self.retries[target, source, credit] += 1
            self.live.pop(key)
            return
        txn["response_seen"] = True
        if op == 8:
            self.require(txn["receipt_needed"] and not txn["receipt"], "readreceipt", "unexpected or duplicate ReadReceipt")
            txn["receipt"] = True
            # ReadReceipt.DBID is inapplicable and never creates a binding.
            self.finish(key)
            return
        self.require(op in (4, 5, 6), "finite-profile", f"unsupported native RSP {op:#x}")
        if txn["opcode"] in DATALLESS:
            self.require(op == 4, "completion", "Dataless request requires Comp, not a write DBID response")
            if txn["opcode"] in (11, 12):
                self.require(resp == 2, "dataless-response", "CleanUnique/MakeUnique require Comp_UC")
        if txn["opcode"] in COPYBACK:
            self.require(op in (4, 5), "copyback-response", "CopyBack requires Comp or CompDBIDResp; split DBIDResp is not permitted")
            if op == 4 and txn["opcode"] != 0x42 and txn["cah"] is not None:
                self.require(number(txn["cah"]) == 1, "copyback-response", "Comp-only non-WEOE CopyBack requires CAH=1")
        if op in (4, 5):
            self.require(not txn["completion"], "completion", "duplicate completion")
            txn["completion"] = True
            txn["comp_dbid"] = dbid
        if op in (5, 6):
            self.require(txn["opcode"] in WRITES and txn["dbid"] is None,
                         "write-dbid", "unexpected or duplicate DBID response")
            buffer_key = source, target, dbid
            self.require(buffer_key not in self.buffers, "dbid-lifetime", f"reused live DBID {buffer_key}")
            self.buffers[buffer_key] = key
            txn["dbid"] = dbid
        if txn["dbid"] is not None and txn["comp_dbid"] is not None:
            self.require(txn["dbid"] == txn["comp_dbid"], "write-dbid", "Comp/DBIDResp DBID mismatch")
        if op == 4 and txn["opcode"] in COPYBACK and txn["dbid"] is None:
            txn["comp_only"] = True
            txn["ack_needed"] = True
        if (txn["comp_only"] or txn["opcode"] in DATALLESS and txn["ack_needed"]) and op == 4:
            ack_key = source, target, dbid
            self.require(ack_key not in self.acks, "dbid-lifetime", f"reused completion DBID {ack_key}")
            self.acks[ack_key] = key
        self.finish(key)

    def snoop(self, e, f, source, target, tid):
        op = self.width(f, "opcode", 5)
        self.require(op in {1, 2, 3, 4, 7, 8, 9, 10}, "finite-profile", f"unsupported native SNP {op:#x}")
        addr = self.width(f, "addr", self.address_bits - 3)
        self.require(addr << 3 == number(e["address"]), "snoop-address", "SNP wire Addr must equal PA>>3")
        self.require("size" not in f and "sizeBytes" not in f, "snoop-address", "SNP has no Size field")
        ret = self.width(f, "retToSrc", 1)
        no_sd = self.width(f, "doNotGoToSD", 1)
        if op in (7, 8, 9, 10):
            self.require(no_sd == 1, "snoop-return", "Unique/maintenance snoop DoNotGoToSD must be one")
        if op in (8, 9, 10):
            self.require(ret == 0, "snoop-return", "maintenance snoop RetToSrc must be zero")
        key = source, target, tid
        self.require(key not in self.snoops, "snoop-lifetime", f"duplicate live snoop {key}")
        self.snoops[key] = dict(expected={0, 2}, beats=set(), signature=None, opcode=op)

    def data(self, e, f, source, target, tid):
        op = self.width(f, "opcode", 4)
        self.require(op in (1, 2, 3, 4), "finite-profile", f"unsupported native DAT {op:#x}")
        dbid = self.width(f, "dbid", 12)
        home = self.width(f, "HomeNID", self.node_bits)
        resp = self.width(f, "resp", 3)
        dataid = self.width(f, "dataid", 2)
        self.require(dataid in (0, 2), "dataid", "256-bit DAT requires DataID 0/2")
        payload = bytes.fromhex(e["payload"])
        be = bytes.fromhex(e["byteEnable"])
        valid = bytes.fromhex(e["validMask"])
        self.require(len(payload) == len(be) == len(valid) == 32, "natural-lanes", "physical DAT must contain 32 bytes/BE/known mask")
        self.require(all(x in (0, 255) for x in be + valid), "byte-mask", "mask bytes must be 00 or ff")
        if op != 4:
            self.require(home == 0, "home-nid", "HomeNID must be zero on write/snoop data")
            self.require(all(enable or not known or byte == 0 for byte, enable, known in zip(payload, be, valid)),
                         "byte-enable", "known BE=0 write/snoop byte must be zero")
        if op == 3:
            self.require(resp == 0, "data-response", "NonCopyBackWriteData Resp must be zero")
        else:
            self.require(resp in (0, 1, 2, 3, 4, 5, 6) if op == 1 else resp in (0, 1, 2, 6, 7),
                         "data-response", "reserved DAT Resp")
        if op == 1:
            key = target, source, tid
            self.require(key in self.snoops, "snoop-identity", f"unmatched SnpRespData {key}")
            txn = self.snoops[key]
            self.require(dbid == 0 and all(be), "snoop-data", "SnpRespData without DataPull needs DBID=0 and full BE")
            if txn["opcode"] in (8, 9):
                self.require(resp & 4, "snoop-response", "clean maintenance snoop must return RSP, not DAT")
            self.require(txn["opcode"] != 10, "snoop-response", "SnpMakeInvalid must not return DAT")
        elif op in (2, 3):
            binding = target, source, tid
            self.require(binding in self.buffers, "write-dbid", f"unmatched DAT.TxnID={tid}; must use DBIDResp.DBID")
            key = self.buffers[binding]
            txn = self.live[key]
            self.require((op == 2) == (txn["opcode"] in COPYBACK), "write-data-opcode", "CopyBack/NonCopyBack opcode mismatch")
            offset = dataid * 16
            self.require(all(not enable or txn["start"] <= offset + lane < txn["end"]
                             for lane, enable in enumerate(be)), "natural-lanes", "BE outside Addr/Size window")
            if txn["opcode"] in (0x15, 0x42):
                self.require(resp in (0, 1, 2), "copyback-response",
                             "clean eviction CopyBackWriteData must be I, SC or UC")
            if op == 2 and resp == 0:
                self.require(not any(be), "byte-enable", "CopyBackWriteData_I must have all BE=0")
            elif txn["opcode"] in FULL_WRITES:
                self.require(all(be), "byte-enable", "full write must set all BE")
        else:
            key = target, tid
            self.require(key in self.live, "read-identity", f"unmatched CompData {key}")
            txn = self.live[key]
            self.require(txn["opcode"] in READS and source == txn["target"], "read-identity", "wrong read data source/opcode")
            self.require(home == source, "home-nid", "no-DMT CompData HomeNID must equal the completer / REQ.TgtID (B2.5.1.4)")
            txn["response_seen"] = True
            if txn["ack_needed"] and not txn["beats"]:
                ack_key = home, target, dbid
                self.require(ack_key not in self.acks, "dbid-lifetime", f"reused completion DBID {ack_key}")
                self.acks[ack_key] = key
        signature = (source, target, tid, op, home, resp, dbid if op in (1, 4) else None)
        self.require(txn["signature"] is None or txn["signature"] == signature,
                     "beat-consistency", "identity/response fields changed across DAT beats")
        txn["signature"] = signature
        self.require(dataid in txn["expected"] and dataid not in txn["beats"], "beat-coverage", "duplicate or unexpected DAT beat")
        txn["beats"].add(dataid)
        if txn["beats"] == txn["expected"]:
            if op == 1:
                self.snoops.pop(key)
            else:
                txn["data_done"] = True
                if op in (2, 3):
                    self.buffers.pop((target, source, tid))
                self.finish(key)

    @staticmethod
    def wire_snapshot(e):
        f = dict(e["fields"])
        # Exclude simulator annotations, not actual protocol fields.
        for key in ("beatOffset", "last"):
            f.pop(key, None)
        return dict(channel=e["channel"], fields=f, payload=e.get("payload"), byteEnable=e.get("byteEnable"),
                    validMask=e.get("validMask"), address=e.get("address") if e["channel"] == "REQ" else None)

    def run(self):
        with open(self.path, encoding="utf-8-sig") as stream:
            for line_no, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                self.position = str(line_no)
                try:
                    e = json.loads(line)
                    if e.get("type") == "manifest":
                        self.nodes = {number(n["nodeId"]): str(n.get("kind", n.get("type", n.get("role", "")))).upper().replace("-", "")
                                      for n in e.get("topology", [])}
                        self.node_bits = number(e.get("nodeIdWidth", 11))
                        self.address_bits = number(e.get("reqAddrWidth", 48))
                        continue
                    if e.get("kind") != "flit":
                        continue
                    self.require(e.get("wireEncoding") == "native-chi", "native-source", "coarse flit found in native capture")
                    message = e.get("messageId")
                    self.require(bool(message), "transport-evidence", "missing stable message identity")
                    observation = e.get("observation")
                    self.require(observation in ("TX", "RX"), "transport-evidence", "capture must observe actual endpoint port")
                    observed = self.tx if observation == "TX" else self.rx
                    self.require(message not in observed, "transport-evidence", f"duplicate {observation} for message {message}")
                    observed[message] = self.wire_snapshot(e)
                    if observation != "TX":
                        continue
                    f = e["fields"]
                    source, target, tid = self.identity(f)
                    self.opcodes[e.get("opcode", f"{e['channel']}:{f['opcode']}")] += 1
                    handler = {"REQ": self.request, "RSP": self.response, "SNP": self.snoop, "DAT": self.data}.get(e["channel"])
                    self.require(handler is not None, "channel", "unknown CHI channel")
                    handler(e, f, source, target, tid)
                except (ValueError, KeyError, TypeError, OverflowError) as error:
                    self.errors.append({"line": line_no, "error": str(error)})
        for message, sent in self.tx.items():
            if message not in self.rx:
                if not self.allow_open:
                    self.errors.append({"message": message, "error": "transport-evidence: sent flit not observed at destination"})
            elif sent != self.rx[message]:
                self.errors.append({"message": message, "error": "transport-identity: router altered native wire fields"})
            else:
                self.rules["transport-identity"] += 1
        if not self.allow_open:
            for message in self.rx.keys() - self.tx.keys():
                self.errors.append({"message": message, "error": "transport-evidence: received flit has no captured sender"})
        if not self.allow_open:
            for label, values in (("requests", self.live), ("snoops", self.snoops), ("DBIDs", self.buffers), ("CompAcks", self.acks)):
                if values:
                    self.errors.append({"error": f"lifecycle: incomplete {label}: {list(values)}"})
            if any(self.retries.values()):
                self.errors.append({"error": f"lifecycle: RetryAck without reissue: {dict(self.retries)}"})
        if not self.tx:
            self.errors.append({"error": "native-source: no native transmitted flits captured"})
        return dict(input=self.path, passed=not self.errors,
                    capturePolicy="allow-open-prefix" if self.allow_open else "closed-capture",
                    completeCaptureValidated=not self.allow_open,
                    openEvidence=dict(requests=len(self.live), snoops=len(self.snoops),
                        writeBuffers=len(self.buffers), completionAcks=len(self.acks),
                        retries=sum(self.retries.values()),
                        sentWithoutRecordedReceive=len(self.tx.keys()-self.rx.keys()),
                        receivedWithoutRecordedSend=len(self.rx.keys()-self.tx.keys())),
                    transmittedFlits=len(self.tx),
                    receivedFlits=len(self.rx), checks=sum(self.rules.values()), rules=dict(self.rules),
                    opcodes=dict(self.opcodes), errors=self.errors)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, action="append", type=Path)
    parser.add_argument("--output", type=Path, default=Path("build/qa/native-wire-audit.json"))
    parser.add_argument("--allow-open", action="store_true", help="permit explicitly truncated capture lifecycles")
    parser.add_argument("--require-opcode", action="append", default=[])
    args = parser.parse_args()
    sessions = [Audit(path, args.allow_open).run() for path in args.input]
    counts = Counter()
    for session in sessions:
        counts.update(session["opcodes"])
    missing = [opcode for opcode in args.require_opcode if not counts[opcode]]
    report = dict(passed=all(s["passed"] for s in sessions) and not missing,
                  completeCaptureValidated=not args.allow_open,
                  source="actual native CHI endpoint TX/RX records; no refinement or correction",
                  profile="Issue H finite non-forwarding 64B line / 256-bit DAT",
                  missingRequiredOpcodes=missing, sessions=sessions)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(dict(passed=report["passed"], checks=sum(s["checks"] for s in sessions),
                         errors=sum(len(s["errors"]) for s in sessions), missingRequiredOpcodes=missing,
                         report=str(args.output)), ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
