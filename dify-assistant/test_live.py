import sys,pathlib,json,time,re,io,zipfile
from dify_api import session
import requests
R=pathlib.Path(__file__).parent;OUT=R/'test-results';OUT.mkdir(exist_ok=True)
APP='01a51df1-fa63-4ff2-84d4-321a21c2c5a3';BASE='http://localhost/openapi/v1'
def run(query,conversation_id=None,files=None):
    p={'query':query,'inputs':{},'workspace_id':'5f108ba6-13db-489e-bb02-553a7088f05d'}
    if conversation_id:p['conversation_id']=conversation_id
    if files:p['files']=files
    r=session().post(BASE+'/apps/'+APP+':run',json=p,timeout=180,stream=True);r.raise_for_status()
    result={'query':query,'answer':'','events':[]}
    for raw in r.iter_lines():
        if not raw.startswith(b'data:'):continue
        j=json.loads(raw[5:]);ev=j.get('event','');result['events'].append(ev)
        if j.get('conversation_id'):result['conversation_id']=j['conversation_id']
        if ev in ('message','agent_message','text_chunk'):result['answer']+=j.get('answer',j.get('data',{}).get('text',''))
        if ev=='workflow_finished':result['status']=j.get('data',{}).get('status');result['error']=j.get('data',{}).get('error');result['workflow_run_id']=j.get('workflow_run_id') or j.get('data',{}).get('id')
        if ev=='error':result['error']=j.get('message')
    if result.get('error'):raise RuntimeError(result['error'])
    assert result['answer'],'Empty answer'
    return result

if __name__=='__main__':
    records=[]
    def test(name,q,cid=None,files=None):
        x=run(q,cid,files);x['test']=name;records.append(x);(OUT/(name+'.json')).write_text(json.dumps(x,ensure_ascii=False,indent=2),'utf-8');print(json.dumps({'test':name,'answer':x['answer'],'conversation_id':x.get('conversation_id'),'status':x.get('status')},ensure_ascii=False),flush=True);return x
    h=test('calculation','计算 (18+24)*3，并说明计算步骤');assert '126' in h['answer']
    h2=test('context','为什么要先算括号里面？',h['conversation_id']);assert '括号' in h2['answer']
    m=test('meeting','请生成会议纪要。以下是测试转写：张三：本周完成眼镜按键接入。李四：我负责在周五之前提交接口文档。张三：决定先做本机版，地图服务下周再评估。')
    assert '李四' in m['answer'] and '周五' in m['answer']
    d=test('export_docx','把刚才的会议纪要导出成 Word',m['conversation_id']);url=re.search(r'\[下载文件\]\(([^)]+)\)',d['answer']).group(1);r=requests.get(url,timeout=30);r.raise_for_status();(OUT/'meeting.docx').write_bytes(r.content);assert 'word/document.xml' in zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    p=test('export_pdf','再导出成 PDF',m['conversation_id']);url=re.search(r'\[下载文件\]\(([^)]+)\)',p['answer']).group(1);r=requests.get(url,timeout=30);r.raise_for_status();(OUT/'meeting.pdf').write_bytes(r.content);assert r.content.startswith(b'%PDF')
    b=test('button',json.dumps({'input_type':'button','event_id':'test-button-'+str(time.time_ns()),'semantic_action':'switch_scene','scene':'homework'},ensure_ascii=False));assert '拍题' in b['answer']
    n=test('navigation','导航去北京南站，步行');assert '未接入' in n['answer']
    n2=test('navigation_context','换成骑行',n['conversation_id']);assert '北京南站' in n2['answer'] and '骑行' in n2['answer']
    from PIL import Image,ImageDraw,ImageFont
    image=Image.new('RGB',(1000,400),'white');draw=ImageDraw.Draw(image);font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',64);draw.text((50,100),'Solve: 3x + 7 = 22',font=font,fill='black');img=OUT/'test-equation.png';image.save(img)
    with img.open('rb') as f:
        response=session().post(BASE+'/apps/'+APP+'/files',files={'file':('test-equation.png',f,'image/png')},timeout=30);response.raise_for_status();file_id=response.json()['id']
    im=test('vision','请识别图片中的题目并解答',files=[{'type':'image','transfer_method':'local_file','upload_file_id':file_id}]);assert '5' in im['answer']
    (OUT/'summary.json').write_text(json.dumps({'passed':len(records),'tests':[r['test'] for r in records]},ensure_ascii=False,indent=2),'utf-8')
