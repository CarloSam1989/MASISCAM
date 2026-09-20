"""Render host examples without installing/reloading Nginx."""
import argparse
from pathlib import Path
import re

p=argparse.ArgumentParser();p.add_argument('--domain',required=True);p.add_argument('--www',default='');p.add_argument('--cert-name');p.add_argument('--bootstrap',action='store_true');args=p.parse_args()
for value in [args.domain,args.www,args.cert_name or args.domain]:
    if value and ('__' in value or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]*',value)):
        p.error('Use hostnames/cert-name reales, sin esquema ni slash.')
root=Path(__file__).resolve().parent.parent
name='bootstrap.conf.example' if args.bootstrap else 'masiscam.conf.example'
s=(root/'deploy/nginx-host'/name).read_text(encoding='utf-8')
if not args.www and not args.bootstrap:
    start=s.index('server {\n    listen 443 ssl http2;')
    end=s.index('server {\n    listen 443 ssl http2;',start+1)
    s=s[:start]+s[end:]
s=s.replace('__DOMINIO__',args.domain).replace('__WWW__',args.www).replace('__CERT_NAME__',args.cert_name or args.domain)
print(s)
