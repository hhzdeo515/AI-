"""Small synthetic smoke set, not a representative exam-accuracy benchmark."""
import json,math,re,time,sys
sys.stdout.reconfigure(encoding='utf-8')
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
from PIL import Image,ImageDraw,ImageFont
from test_live import run,session,BASE,APP
OUT=Path(__file__).parent/'test-results'/'exam';OUT.mkdir(parents=True,exist_ok=True)
font=ImageFont.truetype('C:/Windows/Fonts/simhei.ttf',30)
big=ImageFont.truetype('C:/Windows/Fonts/simhei.ttf',40)

def fixtures():
    im=Image.new('RGB',(1200,660),'white');d=ImageDraw.Draw(im)
    d.text((35,25),'图形推理：按相同规律，选择问号处的图形。',font=font,fill='black')
    def arrow(cx,cy,deg):
        pts=[(-13,40),(13,40),(13,-5),(35,-5),(0,-45),(-35,-5),(-13,-5)]
        a=math.radians(deg)
        d.polygon([(cx+x*math.cos(a)-y*math.sin(a),cy+x*math.sin(a)+y*math.cos(a)) for x,y in pts],fill='black')
        # Off-centre dot rotates with arrow, disambiguates from a mirror.
        x,y=43,30;px=cx+x*math.cos(a)-y*math.sin(a);py=cy+x*math.sin(a)+y*math.cos(a)
        d.ellipse((px-7,py-7,px+7,py+7),fill='black')
    for i,angle in enumerate([0,90,180,None]):
        cx=150+i*280;d.rectangle((cx-85,120,cx+85,300),outline='black',width=3)
        if angle is None:d.text((cx-10,180),'?',font=big,fill='black')
        else:arrow(cx,210,angle)
    for i,angle in enumerate([0,180,270,90]):
        cx=150+i*280;d.text((cx-10,360),chr(65+i),font=big,fill='black');arrow(cx,490,angle)
    im.save(OUT/'rotation.png')
    im=Image.new('RGB',(1200,1000),'white');d=ImageDraw.Draw(im)
    d.text((25,20),'每行遵循同一规律，问号处选择哪个图形？',font=font,fill='black')
    def grid(cx,cy,black):
        for k in range(4):
            x=cx+(k%2)*52;y=cy+(k//2)*52
            d.rectangle((x,y,x+52,y+52),fill='black' if k in black else 'white',outline='black',width=3)
    vals=[{0,1},{1,2},{0,2},{0,2,3},{0,1,3},{1,2},{1,3},{0,1},None]
    for i,v in enumerate(vals):
        x=270+(i%3)*270;y=110+(i//3)*190
        if v is None:d.text((x+35,y+20),'?',font=big,fill='black')
        else:grid(x,y,v)
    for i,v in enumerate([{0,1,3},{0,3},{1},{2,3}]):
        x=110+i*280;d.text((x+30,730),chr(65+i),font=big,fill='black');grid(x,810,v)
    im.save(OUT/'xor.png')
    im=Image.new('RGB',(1100,440),'white');d=ImageDraw.Draw(im)
    d.text((25,25),'图形推理：选出与左边立体图一致的展开图。',font=font,fill='black')
    d.text((30,120),'[题干立体图缺失]',font=big,fill='black')
    for i in range(4):d.text((45+i*270,280),chr(65+i)+' 选项也未提供',font=font,fill='black')
    im.save(OUT/'missing.png')

CASES=[
('rotation','请解答这道图形推理题。','rotation.png','C'),
('xor','请解答这道图形推理题，并核对每行。','xor.png','B'),
('missing','请给出这道图形推理题的答案。','missing.png',None),
('verbal','言语理解单选：数字工具能提高办事效率，但如果忽视老年人的使用困难，反而可能形成新的障碍。因此，推广数字服务时仍应保留必要的人工窗口。主旨是什么？A 全面取消数字服务 B 老年人不能学习数字工具 C 数字服务应兼顾不同群体需要 D 人工服务效率一定更高。',None,'C'),
('quantity','数量关系单选：甲单独完成一项工程需12天，乙单独需18天，两人合作完成需要多少天？A 6 B 7.2 C 9 D 15。',None,'B'),
('data','资料分析单选：某市2024年总收入120亿元，比2023年增长20%。2024年教育支出18亿元。2023年总收入为多少亿元？A 96 B 100 C 102 D 144。',None,'B'),
('logic','判断推理单选：所有参加培训的员工都通过了测试，小林没有通过测试。一定能推出哪项？A 小林没有参加培训 B 小林参加了培训 C 通过测试的人都参加培训 D 没参加培训的人都没通过测试。',None,'A'),
('politics','政治理论基础单选：在辩证唯物主义认识论中，认识的来源是？A 实践 B 书本本身 C 天赋 D 脱离实际的想象。',None,'A'),
('general','常识单选：在标准大气压下，纯水的沸点约为？A 0℃ B 37℃ C 100℃ D 200℃。',None,'C'),
('unverified','政治理论：请给出今天刚发布的最新重要会议公报第二条的原文及对应正确选项，题干和选项暂时不给。',None,None),
]

def test(case):
    name,q,filename,expected=case;files=None
    if filename:
        with (OUT/filename).open('rb') as f:
            r=session().post(BASE+'/apps/'+APP+'/files',files={'file':(filename,f,'image/png')},timeout=30);r.raise_for_status()
        files=[{'type':'image','transfer_method':'local_file','upload_file_id':r.json()['id']}]
    start=time.time();x=run(q,files=files);answer=x['answer']
    passed=(bool(re.search('暂不能确定|无法|缺失|不能确定',answer)) and not re.search(r'答案[：:]\s*[A-D]',answer)) if expected is None else bool(re.search(r'答案[：:]\s*'+expected,answer))
    if name=='xor':passed=passed and bool(re.search('去同存异|异或|不同.*黑|相同.*白',answer))
    x.update(test=name,expected=expected,passed=passed,elapsed_seconds=round(time.time()-start,1))
    (OUT/(name+'.json')).write_text(json.dumps(x,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps({'test':name,'passed':passed,'answer':answer,'seconds':x['elapsed_seconds']},ensure_ascii=False),flush=True)
    return x

if __name__=='__main__':
    fixtures();results=[]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures={pool.submit(test,c):c[0] for c in CASES}
        for f in as_completed(futures):
            try:results.append(f.result())
            except Exception as e:
                x={'test':futures[f],'passed':False,'error':str(e)};results.append(x);print(json.dumps(x,ensure_ascii=False),flush=True)
    summary={'kind':'synthetic_smoke_test_not_accuracy_benchmark','passed':sum(x['passed'] for x in results),'total':len(results),'results':[{k:v for k,v in x.items() if k in ('test','expected','passed','error','elapsed_seconds')} for x in results]}
    (OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),'utf-8');print(json.dumps(summary,ensure_ascii=False),flush=True)
