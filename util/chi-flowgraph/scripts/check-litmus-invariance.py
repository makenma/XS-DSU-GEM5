"""Architectural and simulator-counter equivalence with the observer disabled."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def stats(folder):
    result={}
    for line in (folder/'stats.txt').read_text().splitlines():
        fields=line.split()
        if len(fields)>=2 and (fields[0].startswith('system.') or fields[0] in ('simTicks','simInsts','simOps','finalTick')):
            result[fields[0]]=fields[1]
    return result

report={}
for variant in ('iriw','protocol','stress'):
    on=ROOT/f'build/litmus-{variant}';off=ROOT/f'build/litmus-{variant}-off'
    actual=json.loads((on/'flowgraph-result.json').read_text())
    assert actual==json.loads((off/'flowgraph-result.json').read_text()), (variant,'architectural results changed')
    assert stats(on)==stats(off), (variant,'system counters changed')
    report[variant]={'observerInvariant':True,'tick':actual['tick'],'done':actual['done'],'countersCompared':len(stats(on))}
(ROOT/'build/qa/litmus-invariance.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
