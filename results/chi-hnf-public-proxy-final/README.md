# 6×4 CHI HN-F public proxy results

This directory is the completed 2026-07-30 paired-policy experiment. All four
runs passed. The workloads are public-source proxies and the values below are
not SPEC CPU2006 scores.

## Experiment controls

- Topology: 24 Router 6×4 mesh, 16 HN-F, 2 MiB/16-way SLC and
  2 MiB/16-way SF per HN-F
- Clocks: CPU 2.3 GHz; Router/HN-F 1.8 GHz
- Memory: 2 GiB, two DDR4_2400_8x8 channels
- Policies: pseudo-random seed 1 and LRU (`lsu` command-line alias)
- Per run: 100k committed-instruction warm-up/reset followed by about 5M
  committed instructions
- Inputs: libquantum 0.2.4 Shor `1397 8`; OMNeT++ 3.3.2 public Token Ring
  Run 3 with 100 stations

## Results

| Workload | Policy | IPC | Committed insts | SLC victims | HNF lookup CV | Router flits | Service stalls | Accepted→visible |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| libquantum | pseudo-random | 2.287361 | 4,999,997 | 0 | 0.000717 | 1,743,688 | 7,219 | 6.0399 |
| libquantum | LRU | 2.287361 | 4,999,997 | 0 | 0.000717 | 1,743,688 | 7,219 | 6.0399 |
| omnetpp proxy | pseudo-random | 2.233796 | 4,999,991 | 0 | 0.019899 | 226,928 | 175 | 6.0084 |
| omnetpp proxy | LRU | 2.233796 | 4,999,991 | 0 | 0.019899 | 226,928 | 175 | 6.0084 |

The observed LRU-to-pseudo-random IPC delta is 0.00% for both workloads.
However, neither measurement interval produced an SLC victim under either
policy. The replacement selector was therefore not activated, so this result
does not establish that the two algorithms are equivalent. It only establishes
that their behavior and performance are identical before the configured
32 MiB aggregate SLC reaches a replacement point.

The HNF hash was highly balanced: libquantum HNF lookup counts ranged from
5,553 to 5,568 (CV 0.000717), while the OMNeT++ proxy ranged from 573 to 612
(CV 0.019899). The hottest Router was R(1,0) in all four runs, with 241,798
sent flits for libquantum and 26,461 for the OMNeT++ proxy. Modeled Router
output-stall cycles were zero in these intervals.

## Traceability

- libquantum ELF SHA-256:
  `e4e313b2a2c3dcb46a0be4e4295f7ab53820fcff2cb4ea0a10659187ae5a1b34`
- OMNeT++ Token Ring ELF SHA-256:
  `2be6af6d4b687ae7f7a05375ed2a32132c1ff5b41a8506a6ce1e2927d2adb0b7`
- OMNeT++ INI SHA-256:
  `70abfd7866f6c00aae898700ea7953265053a0722087d820c3977658ee0bc37c`
- gem5 ELF SHA-256:
  `eb5c90ceac2a3953b8704dc505d423667b45e3a9bd553fc9d3ecb1bd5f2f6427`

The full path/hash set is in `manifest.json`; analysis revalidated it before
reading statistics. The run also exercises the monotonic, namespace-separated
RN TxnID fix that prevents an old CompAck from aliasing a newer REQ across
independent RSP/REQ channels.

Key artifacts:

- `analysis/summary.json`: structured report input
- `analysis/workload_summary.csv`: workload/policy metrics
- `analysis/policy_comparison.csv`: paired IPC comparison and victim validity
- `analysis/hnf_balance.csv`: all 16 HNF lookup counts
- `analysis/router_hotspots.csv`: all 24 Router channel/direction metrics
- `HNF_6x4_公开源码代理负载_SLC替换策略性能分析.pptx`: 12-slide report

PPTX SHA-256:
`b86d52e23bf6207c15ccc16bbf24f0d471153ea5aab83c14b99bb35b0ae69e13`.
