"""Read axis-aligned, solid black/white grid cells from pixels, without a key.

Fail closed on rotated grids, holes, symbols, irregular spacing or uncertain
fills. Coordinates are evidence; label/row binding still needs visual review.
Uses only Pillow. No OCR service, training examples or fixture identifiers.
"""
from __future__ import annotations
import re
from pathlib import Path
from PIL import Image, ImageOps


def _rectangles(im):
    w,h=im.size
    pixels=im.tobytes().translate(bytes(1 if i<220 else 0 for i in range(256)))
    parent=[];boxes=[];previous=[]
    def root(i):
        while parent[i]!=i:
            parent[i]=parent[parent[i]];i=parent[i]
        return i
    for y in range(h):
        current=[];j=0
        for m in re.finditer(b'\x01+',pixels[y*w:(y+1)*w]):
            a,b=m.span();idx=len(parent);parent.append(idx);boxes.append([a,y,b-1,y])
            while j<len(previous) and previous[j][1]<a:j+=1
            k=j
            while k<len(previous) and previous[k][0]<b:
                old=root(previous[k][2]);new=root(idx)
                if old!=new:
                    parent[new]=old;box=boxes[old];add=boxes[new]
                    boxes[old]=[min(box[0],add[0]),min(box[1],add[1]),max(box[2],add[2]),max(box[3],add[3])]
                k+=1
            current.append((a,b-1,idx))
        previous=current
    return [b for i,b in enumerate(boxes) if root(i)==i and 40<=b[2]-b[0]<=650 and
            .94<=(b[2]-b[0])/max(1,b[3]-b[1])<=1.06],pixels


def _lines(values):
    groups=[]
    for i in values:
        if groups and i<=groups[-1][-1]+1:groups[-1].append(i)
        else:groups.append([i])
    points=[sum(g)/len(g) for g in groups]
    if not 3<=len(points)<=9:return None
    gaps=[b-a for a,b in zip(points,points[1:])]
    if min(gaps)<6 or max(gaps)/min(gaps)>1.15:return None
    return points


def read_grids(path: str | Path) -> list[dict]:
    try:
        with Image.open(path) as src:
            im=ImageOps.exif_transpose(src).convert('L')
        if max(im.size)>4096:return []
        w,h=im.size;rects,pixels=_rectangles(im);out=[]
        for x0,y0,x1,y1 in rects:
            width=x1-x0+1;height=y1-y0+1
            ys=_lines([y for y in range(y0,y1+1) if sum(pixels[y*w+x0:y*w+x1+1])/width>.97])
            xs=_lines([x for x in range(x0,x1+1) if sum(pixels[y*w+x] for y in range(y0,y1+1))/height>.97])
            if not xs or not ys or len(xs)!=len(ys):continue
            cells=[];uncertain=False
            for top,bottom in zip(ys,ys[1:]):
                for left,right in zip(xs,xs[1:]):
                    dx=(right-left)*.16;dy=(bottom-top)*.16
                    box=(round(left+dx),round(top+dy),round(right-dx),round(bottom-dy))
                    sample=im.crop(box);hist=sample.histogram();area=sample.width*sample.height
                    if not area:uncertain=True;break
                    ink=sum(hist[:150])/area
                    if ink>=.88:cells.append('1')
                    elif ink<=.04:cells.append('0')
                    else:uncertain=True;break
                if uncertain:break
            if not uncertain:
                side=len(xs)-1; bits=''.join(cells)
                pending={i for i,b in enumerate(bits) if b=='1'}; components=0
                while pending:
                    components+=1;stack=[pending.pop()]
                    while stack:
                        pos=stack.pop();r,c=divmod(pos,side)
                        adjacent=[rr*side+cc for rr,cc in ((r-1,c),(r+1,c),(r,c-1),(r,c+1))
                                  if 0<=rr<side and 0<=cc<side]
                        for neighbor in adjacent:
                            if neighbor in pending:pending.remove(neighbor);stack.append(neighbor)
                out.append({'bounds':[x0,y0,x1,y1],'side':side,'bits':bits,
                            'filled_cells':bits.count('1'),'edge_connected_components':components})
        return sorted(out,key=lambda g:(g['bounds'][1],g['bounds'][0]))
    except (OSError,ValueError,TypeError):
        return []


def observe_grids(images: list[str]) -> dict:
    pages=[]
    for index,path in enumerate(images):
        grids=read_grids(path)
        if not grids:continue
        groups=[]
        for g in grids:
            y=g['bounds'][1];height=g['bounds'][3]-y
            if groups and abs(y-groups[-1][0]['bounds'][1])<height*.25:groups[-1].append(g)
            else:groups.append([g])
        page={'page':index+1,'grids':grids,'label_binding':'请按原图确认每幅图的题图行列及选项字母；像素数据不包含规律或答案。'}
        # Conventional matrix layout is a hypothesis, not an answer or forced
        # binding. The missing position is inferred from column gaps.
        if len(groups)==4 and sorted(len(g) for g in groups[:3])==[2,3,3] and 2<=len(groups[3])<=8:
            full=next(g for g in groups[:3] if len(g)==3)
            columns=[g['bounds'][0] for g in sorted(full,key=lambda v:v['bounds'][0])]
            rows=[];valid=True
            for group in groups[:3]:
                row=['?']*3
                for grid in group:
                    x=grid['bounds'][0];col=min(range(3),key=lambda k:abs(x-columns[k]))
                    if abs(x-columns[col])>(grid['bounds'][2]-x)*.3 or row[col]!='?':valid=False;break
                    row[col]=grid['bits']
                rows.append(row)
            if valid and sum(v=='?' for r in rows for v in r)==1:
                page['matrix_layout_candidate']={'rows':rows,'options':{k:g['bits'] for k,g in zip('ABCDEFGH',sorted(groups[3],key=lambda v:v['bounds'][0]))}}
        pages.append(page)
    return {'pages':pages,'scope':'程序仅读取清晰、规则、实心黑白格；不识别圆点、图标、折叠语义或规律。不适用时不补全。'} if pages else {}
