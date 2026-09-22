import json,sys
from pathlib import Path
from exam_tools import check_grids
ROOT=Path(__file__).parent;sys.path.insert(0,str(ROOT/'.deps'))
import requests

grid={'rows':[['1100','0110','1010'],['1011','1101','0110'],['0101','1100','?']],'options':{'A':'1101','B':'1001','C':'0100','D':'0011'}}
x=check_grids(grid)
assert x['status']=='checked'
xor=[r for r in x['candidates'] if r['direction']=='row' and r['rule'].startswith('xor')]
assert len(xor)==1 and xor[0]['prediction']=='1001' and xor[0]['matching_options']==['B']
assert check_grids({'rows':[],'options':{}})['status']=='invalid'
assert check_grids(None)['status']=='not_applicable'
transpose={'rows':[list(r) for r in zip(*grid['rows'])],'options':grid['options']}
assert any(r['direction']=='column' and r['rule'].startswith('xor') and r['matching_options']==['B'] for r in check_grids(transpose)['candidates'])
ambiguous={'rows':grid['rows'],'options':{'A':'1001','B':'1001'}}
assert any(r['matching_options']==['A','B'] for r in check_grids(ambiguous)['candidates'])
cfg=json.loads((ROOT/'.private/settings.json').read_text('utf-8'))
payload={'owner':'synthetic-tool-test','session_id':'qa','draft':{'binary_grid':grid,'calculations':[{'label':'base','expression':'120/(1+0.2)'},{'label':'cooperation','expression':'1/(1/12+1/18)'},{'label':'invalid','expression':'__import__("os")'},{'label':'complex','expression':'(-1)**0.5'},{'label':'zero','expression':'1/0'}]}}
r=requests.post('http://localhost:8792/api/exam-check',headers={'Authorization':'Bearer '+cfg['service_token']},json=payload,timeout=20);r.raise_for_status();d=r.json()
assert d['checks'][0]['result']==100
assert abs(d['checks'][1]['result']-7.2)<1e-10
assert all(v['status']=='error' for v in d['checks'][2:])
assert d['grid_checks']==x
(ROOT/'test-results/exam/tool-checks.json').write_text(json.dumps(d,ensure_ascii=False,indent=2),'utf-8')
print('PASS: XOR rows/columns, malformed grid, duplicate options, numeric checks, invalid expressions rejected')
