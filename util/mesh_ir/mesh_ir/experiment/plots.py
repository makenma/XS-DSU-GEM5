import html
import csv
import json
from collections import defaultdict
from pathlib import Path

from ..acceptance import canonical_digest
from .metrics import GROUP_FIELDS
from .config import Topology


PALETTE = ("#1766a4", "#c85b17", "#357b42", "#92358b", "#956f10", "#487c88")


def scatter_svg(path, series, x_label, y_label, title):
    values = [(x, y) for points in series.values() for x, y in points]
    if not values:
        return False
    xmin, xmax = min(x for x, y in values), max(x for x, y in values)
    ymin, ymax = 0, max(y for x, y in values)
    xmax = max(xmax, xmin + 1)
    ymax = max(ymax, 1)
    width, height = 920, 560
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
             '<rect width="100%" height="100%" fill="white"/>',
             f'<text x="70" y="30" font-size="18">{html.escape(title)}</text>',
             '<path d="M 70 60 V 450 H 660" fill="none" stroke="#555"/>',
             f'<text x="320" y="505">{html.escape(x_label)}</text>',
             f'<text transform="translate(20,340) rotate(-90)">{html.escape(y_label)}</text>']
    for index in range(6):
        x = xmin + (xmax - xmin) * index / 5
        y = ymax * index / 5
        xp, yp = 70 + 590 * index / 5, 450 - 390 * index / 5
        parts.extend([f'<text x="{xp:.1f}" y="474" text-anchor="middle" font-size="12">{x:.3g}</text>',
                      f'<text x="60" y="{yp + 4:.1f}" text-anchor="end" font-size="12">{y:.3g}</text>'])
    for index, (label, points) in enumerate(sorted(series.items())):
        color = PALETTE[index % len(PALETTE)]
        points = sorted(points)
        positions = [(70 + (x - xmin) * 590 / (xmax - xmin), 450 - y * 390 / ymax) for x, y in points]
        polyline = " ".join(f"{x:.2f},{y:.2f}" for x, y in positions)
        parts.append(f'<polyline points="{polyline}" fill="none" stroke="{color}" stroke-width="1.5"/>')
        for x, y in positions:
            parts.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="4" fill="{color}"/>')
        parts.append(f'<text x="675" y="{70 + 20 * index}" fill="{color}" font-size="11">{html.escape(label)}</text>')
    parts.append('</svg>')
    Path(path).write_text("\n".join(parts), encoding="utf-8")
    return True


def depth_maps_svg(directory, row):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    entries = row["router_buffer_map"]["entries"]
    for vnet, channel in enumerate(("AW", "W", "B", "AR", "R")):
        by_router = defaultdict(list)
        for router, port, network, depth in entries:
            if network == vnet:
                by_router[router].append((port, depth))
        parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="920" height="700">',
                 '<rect width="100%" height="100%" fill="white"/>',
                 f'<text x="20" y="24">{html.escape(row["case_id"])} {channel}: flits/VC; each input shown separately</text>']
        for router in range(25):
            x, y = 20 + (router % 5) * 178, 50 + (router // 5) * 128
            parts.append(f'<rect x="{x}" y="{y}" width="165" height="118" fill="#eef4f8" stroke="#aaa"/>')
            parts.append(f'<text x="{x + 7}" y="{y + 18}" font-weight="bold">router {router}</text>')
            for index, (port, depth) in enumerate(sorted(by_router[router])):
                parts.append(f'<text x="{x + 7 + 78 * (index // 5)}" y="{y + 36 + 16 * (index % 5)}" font-size="12">p{port}: {depth}</text>')
        parts.append('</svg>')
        (directory / f"depth_{channel}.svg").write_text("\n".join(parts), encoding="utf-8")


def resource_plots(directory, row, hotspot_path):
    directory = Path(directory)
    regions = {}
    if Path(hotspot_path).exists():
        frozen = json.loads(Path(hotspot_path).read_text())
        regions = {router: region for region, routers in frozen["regions"].items() for router in routers}
    links = row["per_link"]
    with (directory / "link_utilization.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("link_id", "route", "utilization", "flits", "AW", "W", "B", "AR", "R"), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(links)
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="800" height="790">',
             '<rect width="100%" height="100%" fill="white"/>',
             '<defs><marker id="arrow" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" fill="#285979"/></marker></defs>',
             f'<text x="25" y="25">{html.escape(row["case_id"])} directed internal link utilization; all endpoint links in CSV</text>']
    for link in links:
        source, destination = link["route"].split("->")
        if not source.startswith("router:") or not destination.startswith("router:"):
            continue
        left, right = int(source.split(":")[1]), int(destination.split(":")[1])
        x1, y1 = 80 + left % 5 * 145, 95 + left // 5 * 145
        x2, y2 = 80 + right % 5 * 145, 95 + right // 5 * 145
        dx, dy = (x2 - x1) / 145, (y2 - y1) / 145
        offset = 8
        sx, sy = x1 + 23 * dx - offset * dy, y1 + 23 * dy + offset * dx
        ex, ey = x2 - 23 * dx - offset * dy, y2 - 23 * dy + offset * dx
        opacity = .2 + .8 * min(1, max(0, link["utilization"]))
        parts.append(f'<path d="M{sx},{sy} L{ex},{ey}" stroke="#285979" stroke-width="3" opacity="{opacity}" marker-end="url(#arrow)"><title>{html.escape(link["route"])} {link["utilization"]:.4f}</title></path>')
        parts.append(f'<text x="{(sx + ex) / 2 - dy * 9}" y="{(sy + ey) / 2 + dx * 9}" font-size="9" text-anchor="middle">{100 * link["utilization"]:.1f}%</text>')
    for router in range(25):
        x, y = 80 + router % 5 * 145, 95 + router // 5 * 145
        fill = "#efc98b" if router in Topology(row["topology"]).hbm_routers else "#e4edf3"
        parts.append(f'<circle cx="{x}" cy="{y}" r="19" fill="{fill}" stroke="#285979"/><text x="{x}" y="{y + 5}" text-anchor="middle">{router}</text>')
    parts.append('</svg>')
    (directory / "link_utilization.svg").write_text("\n".join(parts), encoding="utf-8")
    ports = [{**port, "region": regions.get(port["router_id"], "unclassified")}
             for port in row["per_router_inport_vnet"]]
    with (directory / "port_pressure.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("region", "router_id", "inport_id", "direction", "channel", "depth",
                                                    "average", "full_fraction", "credit_stalls", "no_vc_stalls", "sa_lost"), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(ports)
    ports.sort(key=lambda port: (port["region"], port["router_id"], port["inport_id"], port["channel"]))
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="{70 + 18 * len(ports)}">',
             '<rect width="100%" height="100%" fill="white"/>',
             '<text x="15" y="24">Actual input ports: mean occupancy | full fraction | credit/no-VC probe failures | SA-II lost requests</text>']
    for index, port in enumerate(ports):
        label = f'{port["region"]} r{port["router_id"]}:p{port["inport_id"]} {port["direction"]} {port["channel"]} D={port["depth"]}'
        values = f'{port["average"]:.4f} flits/VC | {port["full_fraction"]:.4f} | {port["credit_stalls"]}/{port["no_vc_stalls"]} | {port["sa_lost"]}'
        y = 50 + index * 18
        parts.extend([f'<text x="15" y="{y}" font-size="12">{html.escape(label)}</text>',
                      f'<rect x="500" y="{y - 10}" width="{180 * port["full_fraction"]}" height="12" fill="#ba5c35"/>',
                      f'<text x="705" y="{y}" font-size="12">{html.escape(values)}</text>'])
    parts.append('</svg>')
    (directory / "port_pressure.svg").write_text("\n".join(parts), encoding="utf-8")


def plot_results(directory, rows, recommendations):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    grouped = defaultdict(list)
    for row in rows:
        if row.get("correctness") == "pass" and row.get("bandwidth_Bps") is not None:
            grouped[tuple(row[field] for field in GROUP_FIELDS)].append(row)
    for group, points in grouped.items():
        label = "_".join(str(points[0][field]) for field in ("topology", "workload", "distribution"))
        prefix = label + "_" + canonical_digest(group)[:12]
        for label, x_key, y_key, scale, x_title, y_title in (
                ("bandwidth_vs_outstanding", "n_read", "bandwidth_Bps", 1e-9, "N / core / direction", "useful GB/s"),
                ("p99_vs_outstanding", "n_read", "p99_ticks", 1, "N / core / direction", "AXI P99 / ticks"),
                ("bandwidth_vs_buffer", "router_buffer_bytes", "bandwidth_Bps", 1e-9, "router flit storage / bytes", "useful GB/s")):
            series = defaultdict(list)
            for row in points:
                if row.get(y_key) is None:
                    continue
                identity = row["map_digest"][:8] + (" stable" if row.get("stable") else " unconverged")
                actual_x = "n_write" if x_key == "n_read" and row["workload"] == "STORE_ONLY" else x_key
                series[identity].append((row[actual_x], row[y_key] * scale))
            scatter_svg(directory / f"{prefix}_{label}.svg", series, x_title, y_title,
                        " ".join(str(points[0][field]) for field in ("topology", "workload", "distribution")))
    chosen = {group["resource_recommendation"] for group in recommendations.values() if group["resource_recommendation"] is not None}
    for row in rows:
        if row["case_id"] in chosen:
            depth_maps_svg(directory / row["case_id"], row)
            resource_plots(directory / row["case_id"], row,
                           directory.parent / f"hotspots_{row['topology']}_{row['workload']}.json")
