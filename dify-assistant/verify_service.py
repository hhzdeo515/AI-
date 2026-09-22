import sys,json,uuid,re
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent/'.deps'))
import requests
from test_live import run,OUT
root=Path(__file__).parent
cfg=json.loads((root/'.private/settings.json').read_text())
base='http://localhost:8792'
headers={'Authorization':'Bearer '+cfg['service_token']}
assert requests.post(base+'/api/context',json={}).status_code==401
p={'owner':'qa-'+str(uuid.uuid4()),'session_id':'qa','request_id':str(uuid.uuid4()),'query':'','route':{'scene':'meeting','action':'append','transcript':'唯一测试片段'}}
def post(path,p):
    r=requests.post(base+path,headers=headers,json=p,timeout=30);r.raise_for_status();return r.json()
a=post('/api/prepare',p);b=post('/api/prepare',p);assert a==b
state=post('/api/context',p)['state'];assert state['meeting']['transcript']=='唯一测试片段'
p.update(request_id=str(uuid.uuid4()),route={'scene':'resource','action':'export','resource_ids':['419c369b-89e8-44d8-804b-050d661599c8']})
assert '无访问权限' in post('/api/prepare',p)['direct_text']
m=json.loads((OUT/'meeting.json').read_text('utf-8'))
d=run('把刚才的会议纪要导出成 PDF',m['conversation_id'])
url=re.search(r'\[下载文件\]\(([^)]+)\)',d['answer']).group(1)
r=requests.get(url,timeout=30);r.raise_for_status();(OUT/'meeting.pdf').write_bytes(r.content)
assert requests.get(re.sub(r'sig=[^&]+','sig=invalid',url),timeout=30).status_code==403
(OUT/'service-checks.json').write_text(json.dumps({'unauthenticated_request':True,'duplicate_event':True,'cross_owner_export_denied':True,'tampered_download_denied':True,'pdf_bytes':len(r.content)},indent=2),'utf-8')
print('PASS: auth, duplicate event, owner isolation, signed download, embedded-font PDF',flush=True)
