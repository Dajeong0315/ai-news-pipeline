"""Read-only preflight. Never generates, publishes, notifies or writes to DB."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import sys

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--channel', choices=['economy', 'youth'], default='economy')
    p.add_argument('--online', action='store_true')
    p.add_argument('--output')
    a=p.parse_args()
    root=Path(__file__).resolve().parent
    os.chdir(root)
    os.environ['CHANNEL']=a.channel
    checks={}
    for name in ['httpx','supabase','dotenv','PIL','rapidfuzz','feedparser','trafilatura']:
        checks['module:'+name]=bool(importlib.util.find_spec(name))
    for name in ['node','ffmpeg']:
        checks['tool:'+name]=bool(shutil.which(name))
    try:
        from media_tools import ffmpeg_path
        checks['tool:ffmpeg']=Path(ffmpeg_path()).is_file()
    except (ImportError, RuntimeError):
        pass
    if not checks['module:dotenv']:
        print(json.dumps(checks)); return 1
    import config
    for key in ['SUPABASE_URL','SUPABASE_SERVICE_ROLE_KEY','CLOUDFLARE_ACCOUNT_ID','CLOUDFLARE_API_TOKEN',
                'INSTAGRAM_ACCESS_TOKEN','INSTAGRAM_BUSINESS_ACCOUNT_ID','TELEGRAM_BOT_TOKEN','TELEGRAM_CHAT_ID']:
        checks['configured:'+key]=bool(getattr(config,key,''))
    if config.CHANNEL_CONFIG.get('source') == 'listings':
        for key in ['DATA_GO_KR_SERVICE_KEY','YOUTHCENTER_API_KEY']:
            checks['configured:'+key]=bool(getattr(config,key,''))
    for spec in config.CARD_TEMPLATES:
        checks['template:'+spec]=(root/'card_templates'/spec.split(':')[0]/'template.html').is_file()
    if a.online and checks['module:httpx'] and config.SUPABASE_URL and config.SUPABASE_SERVICE_ROLE_KEY:
        import httpx
        host=__import__('urllib.parse',fromlist=['urlparse']).urlparse(config.SUPABASE_URL).hostname
        try:
            socket.getaddrinfo(host,443); checks['dns:supabase']=True
        except OSError:
            checks['dns:supabase']=False
        headers={'apikey':config.SUPABASE_SERVICE_ROLE_KEY,'Authorization':'Bearer '+config.SUPABASE_SERVICE_ROLE_KEY}
        with httpx.Client(headers=headers,timeout=30,transport=httpx.HTTPTransport(retries=2)) as client:
            for table,cols in [('cards','id,approval,publish_status,media_id,revision_request,approval_chat_id'),
                               ('news_items','id,category,source_type'),('pipeline_events','id,run_id,detail,channel')]:
                try:
                    r=client.get(config.SUPABASE_URL+'/rest/v1/'+table,params={'select':cols,'limit':'1'})
                    checks['db:'+table] = r.status_code==200
                    checks['db_http:'+table] = r.status_code
                except httpx.TransportError:
                    checks['db:'+table]=False
            # GET only; credentials and response bodies are never emitted.
            if config.INSTAGRAM_ACCESS_TOKEN and config.INSTAGRAM_BUSINESS_ACCOUNT_ID:
                try:
                    r=client.get('https://graph.facebook.com/'+config.INSTAGRAM_API_VERSION+'/'+config.INSTAGRAM_BUSINESS_ACCOUNT_ID,
                                 params={'fields':'id,username'},headers={'Authorization':'Bearer '+config.INSTAGRAM_ACCESS_TOKEN, 'apikey':''})
                    checks['instagram:identity']=r.status_code==200
                except httpx.TransportError:
                    checks['instagram:identity']=False
    result={'channel':a.channel,'python':sys.version.split()[0], 'checks':checks,
            'ready':all(v for k,v in checks.items() if not k.startswith('db_http:'))}
    rendered=json.dumps(result,ensure_ascii=False,indent=2)
    print(rendered)
    if a.output:
        Path(a.output).write_text(rendered,encoding='utf-8')
    return 0 if result['ready'] else 1

if __name__=='__main__':
    sys.exit(main())
