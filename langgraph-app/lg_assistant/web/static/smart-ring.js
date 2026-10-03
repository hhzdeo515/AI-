/* Local WebGL product model and pointer input. No network or hardware APIs. */
(function(root) {
  'use strict';
  function classifyGesture({dx=0,dy=0,duration=0,cancelled=false,inspect=false,vertical=false}={}) {
    if (cancelled || inspect) return null;
    if (Math.abs(dx)>=32 && Math.abs(dx)>Math.abs(dy)*1.4) return dx<0?'previous':'next';
    if (vertical && Math.abs(dy)>=32 && Math.abs(dy)>Math.abs(dx)*1.4) return dy<0?'up':'down';
    if (Math.hypot(dx,dy)>12) return null;
    return duration>=700?'back':'press';
  }
  function bindInput(button,{action,inspect=()=>false,rotate=()=>{},feedback=()=>{},vertical=()=>false,doubleTap=false,flow=false,onFlow=()=>{}}) {
    let down=null, suppressClick=false, tapTimer=null;
    const cancelTap=()=>{if(tapTimer!==null){root.clearTimeout(tapTimer);tapTimer=null;}};
    const clear=()=>{down=null;button.classList.remove('is-touching');};
    const cancel=()=>{cancelTap();if(down?.flowing)onFlow({phase:'cancel'});suppressClick=true;clear();};
    button.addEventListener('pointerdown',e=>{
      if(button.disabled || e.button!==0 || !e.isPrimary) return;
      suppressClick=false; down={id:e.pointerId,x:e.clientX,y:e.clientY,px:e.clientX,py:e.clientY,time:e.timeStamp,max:0,flowing:false,axis:null};
      button.setPointerCapture(e.pointerId);button.classList.add('is-touching');
    });
    button.addEventListener('pointermove',e=>{
      if(!down || e.pointerId!==down.id) return;
      const dx=e.clientX-down.x,dy=e.clientY-down.y;
      down.max=Math.max(down.max,Math.hypot(dx,dy));
      if(inspect()) rotate(e.clientX-down.px,e.clientY-down.py);
      if(flow && !inspect() && down.max>12) {
        cancelTap();
        if(!down.flowing){down.flowing=true;down.axis=Math.abs(dy)>=Math.abs(dx)?'y':'x';onFlow({phase:'start',axis:down.axis});}
        onFlow({phase:'move',axis:down.axis,total:down.axis==='y'?dy:dx,dx,dy});
      }
      else if(inspect()) {}
      else if(Math.abs(dx)>=32 && Math.abs(dx)>Math.abs(dy)*1.4) {cancelTap();feedback(dx<0?'松开向前移动':'松开向后移动');}
      else if(vertical() && Math.abs(dy)>=32) {cancelTap();feedback(dy<0?'松开向上阅读':'松开向下阅读');}
      down.px=e.clientX;down.py=e.clientY;
    });
    button.addEventListener('pointerup',e=>{
      if(!down || e.pointerId!==down.id) return;
      if(down.flowing) {cancelTap();suppressClick=true;clear();e.preventDefault();onFlow({phase:'end'});return;}
      const dx=e.clientX-down.x,dy=e.clientY-down.y;
      const kind=classifyGesture({dx,dy,duration:e.timeStamp-down.time,inspect:inspect(),vertical:vertical(),cancelled:down.max>16 && Math.hypot(dx,dy)<12});
      suppressClick=kind!=='press';clear();
      if(kind && kind!=='press') {cancelTap();e.preventDefault();action(kind);}
      else if(!kind) feedback(inspect()?'拖动旋转 · 方向键也可调整':'操作已取消');
    });
    button.addEventListener('pointercancel',()=>{cancel();feedback('操作已取消');});
    button.addEventListener('lostpointercapture',()=>{if(down)cancel();});
    button.addEventListener('click',e=>{
      if(button.disabled || inspect()) return;
      if(suppressClick && e.detail!==0){suppressClick=false;return;}
      if(!doubleTap || e.detail===0) {cancelTap();action('press');return;}
      if(tapTimer!==null) {cancelTap();action('double');}
      else {feedback('轻触已收到');tapTimer=root.setTimeout(()=>{tapTimer=null;if(!button.disabled&&!inspect())action('press');},280);}
    });
    button.addEventListener('keydown',e=>{
      if(!inspect() || !['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)) return;
      e.preventDefault();e.stopPropagation();rotate(e.key==='ArrowLeft'?-15:e.key==='ArrowRight'?15:0,e.key==='ArrowUp'?-15:e.key==='ArrowDown'?15:0);
    });
    root.addEventListener?.('blur',cancel);
    return {cancel};
  }
  function mount(canvas, {onUnavailable=()=>{}}={}) {
    const gl=canvas.getContext('webgl',{alpha:true,antialias:true,premultipliedAlpha:false});
    if(!gl) return null;
    let destroyed=false, visible=true, frame=0, angleX=.34,angleY=-.68,angleZ=-.40;
    let targetX=angleX,targetY=angleY;
    let status={connected:true,busy:false,error:false};
    const reduced=root.matchMedia('(prefers-reduced-motion: reduce)');
    const vertex=`attribute vec3 p;attribute vec3 n;attribute float m;uniform mat4 model;uniform mat4 projection;varying vec3 N;varying vec3 P;varying float M;void main(){vec4 q=model*vec4(p,1.);P=q.xyz;N=mat3(model)*n;M=m;gl_Position=projection*q;}`;
    const fragment=`precision mediump float;varying vec3 N;varying vec3 P;varying float M;uniform vec3 signal;uniform float pulse;void main(){vec3 n=normalize(N);vec3 v=normalize(-P);float top=max(dot(n,normalize(vec3(-2.,3.,4.))),0.);float edge=max(dot(n,normalize(vec3(3.,.4,-1.))),0.);float spec=pow(max(dot(reflect(-normalize(vec3(-2.,3.,4.)),n),v),0.),46.);float rim=pow(1.-abs(dot(n,v)),2.);float band=pow(max(dot(n,normalize(vec3(-1.,-.6,2.))),0.),18.);vec3 base=vec3(.36,.39,.37);if(M>0.5)base=vec3(.018,.027,.022);if(M>1.5)base=vec3(.68,.62,.39);vec3 c=base*(.3+top*.82+edge*.52)+vec3(.88,.94,.89)*spec*.78+vec3(.45,.55,.47)*rim*.35+vec3(.62,.72,.66)*band*.27;vec3 ray=reflect(-v,n);float softbox=exp(-pow((ray.x+ray.y*.32+.16)*7.,2.));float darkbox=smoothstep(.1,.3,ray.y)*(1.-smoothstep(.35,.6,ray.y));float brushed=0.;if(M<.5)c=c*.62+vec3(.68,.75,.70)*softbox*.72-vec3(.07)*darkbox+brushed;if(M>2.5)c=signal*(.65+.35*pulse);gl_FragColor=vec4(c,1.);}`;
    function shader(type,src){const sh=gl.createShader(type);gl.shaderSource(sh,src);gl.compileShader(sh);if(!gl.getShaderParameter(sh,gl.COMPILE_STATUS))throw new Error('Ring shader unavailable');return sh;}
    let program;
    try{program=gl.createProgram();const vs=shader(gl.VERTEX_SHADER,vertex),fs=shader(gl.FRAGMENT_SHADER,fragment);gl.attachShader(program,vs);gl.attachShader(program,fs);gl.linkProgram(program);gl.deleteShader(vs);gl.deleteShader(fs);if(!gl.getProgramParameter(program,gl.LINK_STATUS))return null;}catch(_){return null;}
    const vertices=[];
    function point(theta,r,z,nr,nz,m){return [Math.cos(theta)*r,Math.sin(theta)*r,z,Math.cos(theta)*nr,Math.sin(theta)*nr,nz,m];}
    function quad(a,b,c,d){vertices.push(...a,...b,...c,...a,...c,...d);}
    // Thin comfort-fit band with continuous rounded edges and analytic normals.
    // This replaces the earlier thick, flat-chamfer model entirely.
    const profile=[], bevel=.038;
    const corners=[[.962,.142,0],[.858,.142,Math.PI/2],[.858,-.142,Math.PI],[.962,-.142,Math.PI*1.5]];
    for(const [r,z,start] of corners)for(let j=0;j<=10;j++){
      const a=start+j/10*Math.PI/2;
      profile.push([r+bevel*Math.cos(a),z+bevel*Math.sin(a),Math.cos(a),Math.sin(a)]);
    }
    for(let j=0;j<profile.length;j++){
      const a=profile[j],b=profile[(j+1)%profile.length];
      for(let i=0;i<192;i++){const t=i/192*Math.PI*2,u=(i+1)/192*Math.PI*2;quad(point(t,a[0],a[1],a[2],a[3],0),point(u,a[0],a[1],a[2],a[3],0),point(u,b[0],b[1],b[2],b[3],0),point(t,b[0],b[1],b[2],b[3],0));}
    }
    function patch(start,end,r,z0,z1,m,inner=false){for(let i=0;i<36;i++){const t=start+(end-start)*i/36,u=start+(end-start)*(i+1)/36,nr=inner?-1:1;quad(point(t,r,z0,nr,0,m),point(u,r,z0,nr,0,m),point(u,r,z1,nr,0,m),point(t,r,z1,nr,0,m));}}
    patch(-.36,.36,1.003,-.11,.11,1); // Flush ceramic touch surface.
    patch(-.21,.21,1.006,.015,.034,3); // Fine status light strip.
    patch(1.7,2.85,.818,-.10,.10,1,true);
    for(let i=0;i<3;i++)patch(1.95+i*.23,2.012+i*.23,.813,-.045,.035,2,true);
    const buffer=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,buffer);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array(vertices),gl.STATIC_DRAW);
    gl.useProgram(program);for(const [name,size,offset] of [['p',3,0],['n',3,12],['m',1,24]]){const loc=gl.getAttribLocation(program,name);gl.enableVertexAttribArray(loc);gl.vertexAttribPointer(loc,size,gl.FLOAT,false,28,offset);}
    const loc={model:gl.getUniformLocation(program,'model'),projection:gl.getUniformLocation(program,'projection'),signal:gl.getUniformLocation(program,'signal'),pulse:gl.getUniformLocation(program,'pulse')};
    gl.enable(gl.DEPTH_TEST);gl.clearColor(0,0,0,0);
    function multiply(a,b){const out=new Float32Array(16);for(let c=0;c<4;c++)for(let r=0;r<4;r++)for(let k=0;k<4;k++)out[c*4+r]+=a[k*4+r]*b[c*4+k];return out;}
    function draw(time=0){
      frame=0;if(destroyed||!visible||gl.isContextLost())return;
      const width=canvas.clientWidth,height=canvas.clientHeight;if(!width||!height)return;
      const ease=reduced.matches?1:.22;
      angleX+=(targetX-angleX)*ease;angleY+=(targetY-angleY)*ease;
      const dpr=Math.min(root.devicePixelRatio||1,2);if(canvas.width!==Math.round(width*dpr)||canvas.height!==Math.round(height*dpr)){canvas.width=Math.round(width*dpr);canvas.height=Math.round(height*dpr);}
      gl.viewport(0,0,canvas.width,canvas.height);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);
      const cx=Math.cos(angleX),sx=Math.sin(angleX),cy=Math.cos(angleY),sy=Math.sin(angleY),cz=Math.cos(angleZ),sz=Math.sin(angleZ);
      const x=[1,0,0,0,0,cx,sx,0,0,-sx,cx,0,0,0,0,1],y=[cy,0,-sy,0,0,1,0,0,sy,0,cy,0,0,0,0,1],z=[cz,sz,0,0,-sz,cz,0,0,0,0,1,0,0,0,0,1];
      const model=multiply(z,multiply(y,x));model[14]=-4.65;
      const aspect=width/height,f=1/Math.tan(.60/2),near=.1,far=20;
      const proj=[f/aspect,0,0,0,0,f,0,0,0,0,(far+near)/(near-far),-1,0,0,2*far*near/(near-far),0];
      gl.uniformMatrix4fv(loc.model,false,model);gl.uniformMatrix4fv(loc.projection,false,new Float32Array(proj));
      const light=status.error?[1,.42,.24]:!status.connected?[.20,.24,.22]:status.busy?[.55,.8,1]:[.60,1,.73];
      gl.uniform3fv(loc.signal,light);gl.uniform1f(loc.pulse,status.busy && !reduced.matches ? 0.5+0.5*Math.sin(time/210) : 1);
      gl.drawArrays(gl.TRIANGLES,0,vertices.length/7);
      if((status.busy&&!reduced.matches)||Math.abs(targetX-angleX)+Math.abs(targetY-angleY)>.001)frame=root.requestAnimationFrame(draw);
    }
    const schedule=()=>{if(!frame&&!destroyed&&visible)frame=root.requestAnimationFrame(draw);};
    const resize=new ResizeObserver(schedule);resize.observe(canvas);
    canvas.addEventListener('webglcontextlost',e=>{e.preventDefault();destroyed=true;resize.disconnect();canvas.parentElement.classList.remove('has-model');root.cancelAnimationFrame(frame);frame=0;onUnavailable();});
    // Reload restores the optional model; the native fallback controls remain usable.
    canvas.addEventListener('webglcontextrestored',()=>{canvas.parentElement.classList.remove('has-model');});
    canvas.parentElement.classList.add('has-model');schedule();
    return {
      setState(s){status=s;schedule();},
      rotate(dx,dy){targetY+=dx*.010;targetX+=dy*.010;schedule();},
      reset(){targetX=.34;targetY=-.68;angleZ=-.40;schedule();},
      setVisible(value){visible=value;if(!value){root.cancelAnimationFrame(frame);frame=0;}else schedule();},
      destroy(){destroyed=true;resize.disconnect();root.cancelAnimationFrame(frame);gl.deleteBuffer(buffer);gl.deleteProgram(program);}
    };
  }
  const api={classifyGesture,bindInput,mount};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.SmartRing=api;
})(typeof window==='undefined'?globalThis:window);
