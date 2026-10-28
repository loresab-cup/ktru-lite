"""One command: python scripts/acceptance.py --live (paid model requests)."""
import json
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
command = [
    'docker', 'compose', '--profile', 'acceptance', 'run', '--rm', '-T', 'acceptance',
    *sys.argv[1:],
]
result = subprocess.run(
    command, cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=1200
)
if result.stderr:
    print(result.stderr, file=sys.stderr)
try:
    report = json.loads(result.stdout)
except json.JSONDecodeError:
    print(result.stdout)
    raise SystemExit(result.returncode or 1)
out = root / 'outputs/acceptance'
out.mkdir(parents=True, exist_ok=True)
(out / 'report.json').write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8'
)
for name, check in report['checks'].items():
    print(name, 'PASS' if check['passed'] else 'FAIL')
if report.get('error'):
    print(report['error'], file=sys.stderr)
print('Report:', out / 'report.json')
raise SystemExit(result.returncode)
