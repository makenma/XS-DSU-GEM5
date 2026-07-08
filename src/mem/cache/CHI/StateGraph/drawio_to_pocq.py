#!/usr/bin/env python3

"""Convert CHI StateGraph draw.io XML files into a POCQ graph spec.

The generated JSON is intentionally an intermediate representation: it keeps
the parsed draw.io nodes/edges for debugging and also emits folded POCQ
transitions where non-pausing action nodes become transition actions.
"""

from __future__ import annotations

import argparse
import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set
import xml.etree.ElementTree as ET


ACTION_NAMES = {
    "TXDat send": "QueueCompData",
    "TX ReadNoSnp": "QueueMcRead",
    "Tx CompAck": "QueueCompAck",
    "Tx SnpUnique": "QueueSnpUnique",
    "Tx SnpMakeInvlid": "QueueSnpMakeInvalid",
    "Tx SnpOnceFwd": "QueueSnpOnceFwd",
    "Tx compDBIDResp": "QueueCompDBIDResp",
    "TXCOMP": "QueueComp",
    "Flush SF": "FlushSf",
    "Flush L3": "FlushL3",
    "Flush SF Flush L3": "FlushSfFlushL3",
    "Write L3": "WriteL3",
    "Write L3 Flush SF": "WriteL3FlushSf",
    "WriteL3 Flush SF": "WriteL3FlushSf",
}

EVENT_NAMES = {
    "SLC LookUp": "SlcLookupDone",
    "SLC lookup": "SlcLookupDone",
    "RX CompData": "CompData",
    "Wait CompAck": "CompAck",
    "RXCOMPACK": "CompAck",
    "Wait Rnf CompAck": "CompAck",
    "Wait ReadReceipt": "ReadReceipt",
    "SnpResp_I": "SnpRespI",
    "SnpRespData(Ptl)_I_PD": "SnpRespDataPtlIPD",
}

GUARD_NAMES = {
    "SLC HIT": "slc_hit",
    "SLC MISS": "!slc_hit",
    "SF Miss": "sf_miss",
    "SF MISS": "sf_miss",
    "SF HIT": "sf_hit",
    "SF HIT SLC HIT": "sf_hit && slc_hit",
    "SF HIT SLC MISS": "sf_hit && !slc_hit",
    "SF MISS SLC HIT": "sf_miss && slc_hit",
    "SF MISS SLC MISS": "sf_miss && !slc_hit",
}

SUBGRAPH_ALIASES = {
    # Keep historical misspellings in the draw.io files from leaking into the
    # generated state graph.
    "SnpCleanInvid": "SnpCleanInvalid",
    "SnpMakeInvid": "SnpMakeInvlid",
}


@dataclass
class Node:
    id: str
    label: str
    style: str
    kind: str
    semantic: Dict[str, str] = field(default_factory=dict)


@dataclass
class Edge:
    id: str
    source: str
    target: str
    label: str = ""


@dataclass
class Diagnostic:
    level: str
    graph: str
    message: str
    node: Optional[str] = None

    def as_dict(self) -> Dict[str, str]:
        out = {"level": self.level, "graph": self.graph,
               "message": self.message}
        if self.node is not None:
            out["node"] = self.node
        return out


def clean_label(value: Optional[str]) -> str:
    if value is None:
        return ""
    text = html.unescape(value)
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"</div\s*>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return " ".join(text.split())


def style_has(style: str, token: str) -> bool:
    return token in style.split(";") or token in style


def normalize_symbol(label: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", label)
    if not words:
        return "Unnamed"
    return "".join(word[:1].upper() + word[1:] for word in words)


def classify_node(label: str, style: str) -> str:
    label_upper = label.upper()

    if style_has(style, "shape=parallelogram"):
        return "action_link"
    if style_has(style, "shape=hexagon"):
        return "action_slc_sf"
    if style_has(style, "shape=rhombus") or style_has(style, "rhombus"):
        return "subgraph"
    if style_has(style, "ellipse"):
        if label_upper in {"START", "ENTRY"}:
            return "start"
        if label_upper == "EXIT":
            return "exit"
        return "state"

    is_text = (
        style.startswith("text;") or
        ("strokeColor=none" in style and "fillColor=none" in style)
    )
    if is_text:
        if not label:
            return "note"
        if label in {"Y", "N"}:
            return "branch_guard"
        if "DCT" in label_upper:
            return "config_guard"
        if any(op in label for op in ("!=", "==", "<=", ">=", "<", ">")):
            return "guard"
        if label_upper.startswith("SLC") or label_upper.startswith("SF"):
            return "guard"
        return "note"

    if "rounded=0" in style or "rounded=1" in style:
        return "state"

    return "state"


def semantic_for(node: Node) -> Dict[str, str]:
    if node.kind in {"action_link", "action_slc_sf"}:
        return {"action": ACTION_NAMES.get(node.label,
                                            normalize_symbol(node.label))}
    if node.kind in {"state", "start"}:
        return {"event": EVENT_NAMES.get(node.label,
                                         normalize_symbol(node.label))}
    if node.kind == "subgraph":
        label = node.label.replace(" Graph", "")
        subgraph = normalize_symbol(label)
        return {"subgraph": SUBGRAPH_ALIASES.get(subgraph, subgraph)}
    if node.kind in {"guard", "config_guard", "branch_guard"}:
        return {"guard": GUARD_NAMES.get(node.label, node.label)}
    return {}


def parse_drawio(path: Path) -> tuple[str, Dict[str, Node], List[Edge]]:
    tree = ET.parse(path)
    root = tree.getroot()

    graph_name = path.stem.replace(".drawio", "")
    nodes: Dict[str, Node] = {}
    edges: List[Edge] = []

    for cell in root.iter("mxCell"):
        cell_id = cell.get("id")
        if not cell_id or cell_id in {"0", "1"}:
            continue

        label = clean_label(cell.get("value"))
        if cell.get("edge") == "1":
            source = cell.get("source")
            target = cell.get("target")
            if source and target:
                edges.append(Edge(cell_id, source, target, label))
            continue

        if cell.get("vertex") != "1":
            continue

        style = cell.get("style", "")
        kind = classify_node(label, style)
        node = Node(cell_id, label, style, kind)
        node.semantic = semantic_for(node)
        nodes[cell_id] = node

    title_nodes = [n for n in nodes.values()
                   if n.kind == "note" and n.label == graph_name]
    if title_nodes:
        graph_name = title_nodes[0].label

    return graph_name, nodes, edges


def is_action(node: Node) -> bool:
    return node.kind in {"action_link", "action_slc_sf"}


def is_guard(node: Node) -> bool:
    return node.kind in {"guard", "config_guard", "branch_guard"}


def is_note(node: Node) -> bool:
    return node.kind == "note"


def is_pause_node(node: Node) -> bool:
    return node.kind in {"state", "start", "exit", "subgraph"}


def source_event(node: Optional[Node]) -> str:
    if node is None:
        return "Enter"
    if node.kind == "start":
        return "Enter"
    if node.kind == "subgraph":
        return "SubGraphDone"
    if node.kind == "exit":
        return "Exit"
    return node.semantic.get("event", normalize_symbol(node.label))


def combine_guards(labels: Iterable[str]) -> List[str]:
    labels = list(labels)
    out: List[str] = []
    index = 0
    while index < len(labels):
        label = labels[index]
        next_label = labels[index + 1] if index + 1 < len(labels) else None

        if label in {"If(DCT)", "if (DCT)"} and next_label in {"Y", "N"}:
            out.append("dct" if next_label == "Y" else "!dct")
            index += 2
            continue
        if label == "Order!=00" and next_label in {"Y", "N"}:
            out.append("order_nonzero" if next_label == "Y"
                       else "!order_nonzero")
            index += 2
            continue
        if label in {"Y", "N"}:
            out.append("branch_" + label.lower())
            index += 1
            continue

        out.append(GUARD_NAMES.get(label, label))
        index += 1
    return out


def build_adjacency(edges: Iterable[Edge]) -> Dict[str, List[str]]:
    adjacency: Dict[str, List[str]] = {}
    for edge in edges:
        adjacency.setdefault(edge.source, []).append(edge.target)
    return adjacency


def build_reverse_adjacency(edges: Iterable[Edge]) -> Dict[str, List[str]]:
    reverse: Dict[str, List[str]] = {}
    for edge in edges:
        reverse.setdefault(edge.target, []).append(edge.source)
    return reverse


def fold_transitions(
    graph_name: str,
    nodes: Dict[str, Node],
    edges: List[Edge],
    *,
    enable_dct: bool,
    diagnostics: List[Diagnostic],
) -> List[Dict[str, object]]:
    adjacency = build_adjacency(edges)
    reverse = build_reverse_adjacency(edges)
    transitions: List[Dict[str, object]] = []

    def dct_disabled_node(node: Optional[Node]) -> bool:
        if enable_dct or node is None or node.kind != "subgraph":
            return False
        return node.semantic.get("subgraph") == "SnpUniqueFwd"

    def emit_transition(
        source: Optional[Node],
        target: Node,
        guards: List[str],
        actions: List[str],
        path: List[str],
    ) -> None:
        if dct_disabled_node(source) or dct_disabled_node(target):
            diagnostics.append(Diagnostic(
                "info", graph_name,
                "drop SnpUniqueFwd transition because DCT is disabled",
                target.id))
            return

        canonical_guards = combine_guards(guards)
        if not enable_dct and "dct" in canonical_guards:
            diagnostics.append(Diagnostic(
                "info", graph_name,
                "drop DCT-only transition because --enable-dct is not set",
                target.id))
            return

        transitions.append({
            "from": source.label if source is not None else "Start",
            "to": target.label,
            "event": source_event(source),
            "guard": canonical_guards,
            "guard_labels": guards,
            "actions": actions,
            "path": path,
        })

    def walk(
        source: Optional[Node],
        current_id: str,
        guards: List[str],
        actions: List[str],
        path: List[str],
        visited: Set[str],
    ) -> None:
        if current_id in visited:
            diagnostics.append(Diagnostic(
                "warning", graph_name,
                "cycle detected while folding transition", current_id))
            return
        visited = set(visited)
        visited.add(current_id)

        node = nodes.get(current_id)
        if node is None or is_note(node):
            return

        path = path + [node.label or current_id]

        if is_guard(node):
            next_guards = guards + [node.label]
            for next_id in adjacency.get(current_id, []):
                walk(source, next_id, next_guards, actions, path, visited)
            return

        if is_action(node):
            action = node.semantic.get("action", normalize_symbol(node.label))
            if node.label not in ACTION_NAMES:
                diagnostics.append(Diagnostic(
                    "warning", graph_name,
                    f"action label '{node.label}' has no canonical mapping",
                    node.id))
            next_actions = actions + [action]
            for next_id in adjacency.get(current_id, []):
                walk(source, next_id, guards, next_actions, path, visited)
            return

        if is_pause_node(node):
            emit_transition(source, node, guards, actions, path)
            return

    start_candidates = [
        node for node in nodes.values()
        if not is_note(node) and not reverse.get(node.id)
    ]

    for node in start_candidates:
        if is_action(node) or is_guard(node):
            walk(None, node.id, [], [], [], set())

    for source in nodes.values():
        if not is_pause_node(source) or source.kind == "exit":
            continue
        for target_id in adjacency.get(source.id, []):
            walk(source, target_id, [], [], [], {source.id})

    return transitions


def graph_to_dict(
    graph_name: str,
    nodes: Dict[str, Node],
    edges: List[Edge],
    transitions: List[Dict[str, object]],
) -> Dict[str, object]:
    node_items = sorted(nodes.values(), key=lambda n: n.id)
    edge_items = sorted(edges, key=lambda e: e.id)

    return {
        "nodes": [
            {
                "id": node.id,
                "label": node.label,
                "kind": node.kind,
                "semantic": node.semantic,
            }
            for node in node_items
        ],
        "edges": [
            {
                "id": edge.id,
                "source": edge.source,
                "target": edge.target,
                "label": edge.label,
            }
            for edge in edge_items
        ],
        "transitions": transitions,
    }


def default_inputs() -> List[Path]:
    base = Path(__file__).resolve().parent
    trans_graph = base / "Trans_Graph"
    if trans_graph.is_dir():
        return sorted(trans_graph.glob("*.drawio"))
    return sorted(base.glob("*.drawio.xml"))


def build_spec(paths: List[Path], *, enable_dct: bool) -> Dict[str, object]:
    diagnostics: List[Diagnostic] = []
    graphs: Dict[str, object] = {}
    graph_names: Set[str] = set()

    parsed = []
    for path in paths:
        graph_name, nodes, edges = parse_drawio(path)
        parsed.append((path, graph_name, nodes, edges))
        graph_names.add(normalize_symbol(graph_name))

    for path, graph_name, nodes, edges in parsed:
        transitions = fold_transitions(
            graph_name, nodes, edges,
            enable_dct=enable_dct,
            diagnostics=diagnostics)

        for node in nodes.values():
            if node.kind != "subgraph":
                continue
            subgraph = node.semantic.get("subgraph")
            if subgraph and subgraph not in graph_names:
                if subgraph == "SnpUniqueFwd" and not enable_dct:
                    diagnostics.append(Diagnostic(
                        "info", graph_name,
                        "SnpUniqueFwd subgraph is absent and DCT is disabled",
                        node.id))
                else:
                    diagnostics.append(Diagnostic(
                        "warning", graph_name,
                        f"subgraph '{subgraph}' has no matching drawio file",
                        node.id))

        graphs[graph_name] = {
            "source": str(path),
            **graph_to_dict(graph_name, nodes, edges, transitions),
        }

    return {
        "version": 1,
        "graphs": graphs,
        "diagnostics": [diag.as_dict() for diag in diagnostics],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert CHI StateGraph draw.io files to POCQ GraphSpec")
    parser.add_argument(
        "inputs", nargs="*", type=Path,
        help="draw.io XML files; defaults to all *.drawio.xml nearby")
    parser.add_argument(
        "-o", "--output", type=Path,
        help="write JSON GraphSpec to this path instead of stdout")
    parser.add_argument(
        "--enable-dct", action="store_true",
        help="keep DCT/SnpUniqueFwd transitions")
    parser.add_argument(
        "--strict", action="store_true",
        help="return non-zero when warnings are emitted")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    inputs = args.inputs if args.inputs else default_inputs()
    spec = build_spec(inputs, enable_dct=args.enable_dct)
    data = json.dumps(spec, indent=2, sort_keys=True)

    if args.output:
        args.output.write_text(data + "\n")
    else:
        print(data)

    has_warning = any(
        diag.get("level") == "warning"
        for diag in spec.get("diagnostics", [])
    )
    return 1 if args.strict and has_warning else 0


if __name__ == "__main__":
    raise SystemExit(main())
