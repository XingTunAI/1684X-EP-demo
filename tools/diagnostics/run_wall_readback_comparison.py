import argparse
import json
from pathlib import Path
from run_readback_study import run_stage, save
parser = argparse.ArgumentParser(description='Focused local-video wall readback comparison')
parser.add_argument('--streams', type=int, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
if not 1 <= args.streams <= 32:
    parser.error('streams must be 1..32')
root = args.output
root.mkdir(parents=True, exist_ok=False)
base = json.loads(Path('data/results/readback-20260922/wall16/r0_preview10/command.json').read_text())
def setarg(cmd, key, value):
    cmd[cmd.index(key)+1] = str(value)
for rep in range(2):
    cases = [('full', False, 0, True), ('gate64', True, 0, True), ('preview10', True, 10, True), ('model_only', True, 0, False)]
    if rep: cases.reverse()
    for name, gate, preview, readback in cases:
        cmd = base.copy()
        for key, value in {'--streams':args.streams, '--warmup':10, '--duration':30, '--window':30, '--output':'{stage}/worker', '--readback-control':'{stage}/readback.json', '--score-gate':'on' if gate else 'off', '--cpu-post':'selected' if gate else 'dense', '--preview-fps':preview, '--gate-merge-budget-kib':64 if gate else 0}.items():
            setarg(cmd,key,value)
        run_stage(root, f'r{rep}_{name}', cmd, 1, timeout=180, readback=readback)
save(root/'state.json', {'status':'completed'})



