"""Local validation only: temporary test DB, no production/Drive connection."""
import json
from pathlib import Path
import subprocess
import sys

root=Path(__file__).resolve().parent.parent
out=root/'artifacts';out.mkdir(exist_ok=True)
checks=[('tests',[sys.executable,'manage.py','test','--settings=config.test_settings','--verbosity','1']),
        ('django-check',[sys.executable,'manage.py','check','--settings=config.test_settings']),
        ('migration-check',[sys.executable,'manage.py','makemigrations','--check','--dry-run','--settings=config.test_settings']),
        ('deploy-simulated',[sys.executable,'tools/check_production.py'])]
results=[]
for name,cmd in checks:
    process=subprocess.run(cmd,cwd=root,capture_output=True,text=True)
    out.joinpath(name+'.log').write_text(process.stdout+process.stderr,encoding='utf-8')
    results.append({'name':name,'exit_code':process.returncode,'log':name+'.log'})
    print(name+': '+('PASS' if process.returncode==0 else 'FAIL'))
out.joinpath('validation-results.json').write_text(json.dumps({'python':sys.version.split()[0],'checks':results},indent=2),encoding='utf-8')
raise SystemExit(1 if any(x['exit_code'] for x in results) else 0)
