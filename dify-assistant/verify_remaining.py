import json,re,io,zipfile,sys
from test_live import run,session,BASE,APP,OUT
import requests
with (OUT/'test-equation.png').open('rb') as f:
    r=session().post(BASE+'/apps/'+APP+'/files',files={'file':('test-equation.png',f,'image/png')},timeout=30);r.raise_for_status();fid=r.json()['id']
x=run('请识别图片里的方程并求解',files=[{'type':'image','transfer_method':'local_file','upload_file_id':fid}]);(OUT/'vision.json').write_text(json.dumps(x,ensure_ascii=False,indent=2),'utf-8');print(json.dumps({'test':'vision','answer':x['answer'],'status':x.get('status')},ensure_ascii=False),flush=True);assert '5' in x['answer']
original=run('导出刚才图片的原件',x['conversation_id']);(OUT/'original.json').write_text(json.dumps(original,ensure_ascii=False,indent=2),'utf-8');print(json.dumps({'test':'original','answer':original['answer']},ensure_ascii=False),flush=True)
url=re.search(r'\[下载文件\]\(([^)]+)\)',original['answer']).group(1);r=requests.get(url,timeout=30);r.raise_for_status();assert any(n.endswith('.png') for n in zipfile.ZipFile(io.BytesIO(r.content)).namelist());(OUT/'original.zip').write_bytes(r.content)
m=json.loads((OUT/'meeting.json').read_text('utf-8'))
for fmt in ('Word','PDF'):
    d=run('把刚才的会议纪要导出成 '+fmt,m['conversation_id']);url=re.search(r'\[下载文件\]\(([^)]+)\)',d['answer']).group(1);r=requests.get(url,timeout=30);r.raise_for_status();(OUT/('meeting.'+('docx' if fmt=='Word' else 'pdf'))).write_bytes(r.content);print(json.dumps({'test':'formatted_export_'+fmt,'bytes':len(r.content)},ensure_ascii=False),flush=True)
