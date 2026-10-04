#!/usr/bin/env bash
# Run an already built gem5. Building gem5 is deliberately separate.
set -euo pipefail
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
gem5_dir="${CHI_GEM5_DIR:-$HOME/chi-flowgraph-work/gem5}"
output_dir="${CHI_NATIVE_L2_OUT:-$project_dir/build/native-l2-run}"
test -x "$gem5_dir/build/RISCV/gem5.opt"
python3 "$project_dir/scripts/build-native-l2.py"
mkdir -p "$output_dir"
if [[ "${CHI_NATIVE_L2_TRACE:-1}" == 1 ]]; then
    export CHI_FLOWGRAPH_OUT="$output_dir/raw.jsonl"
else
    unset CHI_FLOWGRAPH_OUT
fi
CHI_GEM5_DIR="$gem5_dir" "$gem5_dir/build/RISCV/gem5.opt" --outdir="$output_dir" \
    "$project_dir/native/gem5/native_l2_run.py" --elf-dir "$project_dir/build/native-l2-program" \
    "$@" > "$output_dir/run.log" 2>&1
python3 - "$output_dir" "${CHI_NATIVE_L2_TRACE:-1}" <<'PY'
import collections
import json
import pathlib
import sys
out = pathlib.Path(sys.argv[1])
result = json.loads((out / 'native-l2-result.json').read_text())
log = (out / 'run.log').read_text()
result['hartPass'] = [f'NATIVE_L2_HART_{hart}_PASS' in log for hart in range(4)]
result['pass'] &= all(result['hartPass']) and 'NATIVE_L2_FAIL' not in log
if sys.argv[2] == '1':
    # All checks below inspect online raw flits; chi-refine is never invoked.
    counts = collections.Counter()
    issues = []
    tx = []
    with (out / 'raw.jsonl').open() as source:
        for line in source:
            event = json.loads(line)
            if event.get('kind') != 'flit' or event.get('observation') != 'TX':
                continue
            tx.append(event)
            if event.get('wireEncoding') != 'native-chi':
                issues.append(f"non-native flit: {event.get('id')}")
            f = event['fields']
            if event['channel'] == 'REQ' and f.get('size') != 6:
                issues.append(f"incorrect line Size: {event.get('id')}")
            if event['channel'] == 'DAT' and f.get('dataid') not in (0, 2):
                issues.append(f"incorrect DataID: {event.get('id')}")
            if event.get('source', '').startswith('RN'):
                counts[event['opcode']] += 1
    result['nativePortAudit'] = {'txFlits': len(tx), 'rnOpcodes': dict(counts), 'issues': issues}
    # A zero-event trace is not evidence, nor is just read-only traffic.
    required = ('ReadNotSharedDirty', 'ReadUnique', 'WriteBackFull', 'CompAck')
    result['nativePortAudit']['requiredObserved'] = {op: counts[op] > 0 for op in required}
    result['pass'] &= bool(tx) and not issues and all(counts[op] for op in required)
(out / 'native-l2-result.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
if not result['pass']:
    raise SystemExit('Native L2 CPU / raw-port validation failed')
PY
