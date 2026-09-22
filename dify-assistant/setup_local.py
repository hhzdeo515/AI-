import pathlib,sys,json,secrets,subprocess
ROOT=pathlib.Path(__file__).parent
sys.path.insert(0,str(ROOT/'.deps'))
import requests
private=ROOT/'.private'; private.mkdir(exist_ok=True)
settings_path=private/'settings.json'
settings=json.loads(settings_path.read_text('utf-8')) if settings_path.exists() else {'service_token':secrets.token_urlsafe(32),'download_secret':secrets.token_urlsafe(32)}
key=subprocess.check_output(['docker','exec','docker-db_postgres-1','psql','-U','postgres','-d','dify','-At','-c',"select token from api_tokens where tenant_id='5f108ba6-13db-489e-bb02-553a7088f05d' and type='dataset' order by created_at desc limit 1;"],text=True).strip()
if not key: raise RuntimeError('No knowledge API credential')
settings['dataset_token']=key
s=requests.Session();s.headers['Authorization']='Bearer '+key
if not settings.get('dataset_id'):
    r=s.post('http://localhost/v1/datasets',json={'name':'眼镜助手·个人资料知识库','description':'本机眼镜助手生成的题解、会议纪要与图片识别文字；原件由本机资料服务管理。','permission':'only_me','indexing_technique':'economy'},timeout=30)
    r.raise_for_status();settings['dataset_id']=r.json()['id']
settings_path.write_text(json.dumps(settings,ensure_ascii=False,indent=2),'utf-8')
print(json.dumps({'dataset_id':settings['dataset_id'],'credential_saved':True}))
