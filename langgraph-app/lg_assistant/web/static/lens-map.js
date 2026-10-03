/* AMap and a heading-up lens view. IMU simulation remains available without keys. */
(function (root) {
  "use strict";
  let sdkCallbackSequence=0;
  const validPoint = (lat, lon) => Number.isFinite(lat) && Number.isFinite(lon) && Math.abs(lat) <= 90 && Math.abs(lon) <= 180;
  function coordinates(text) {
    const match = String(text).trim().match(/^(-?\d+(?:\.\d+)?)\s*[,，]\s*(-?\d+(?:\.\d+)?)$/);
    if (!match) return null;
    const latitude = Number(match[1]), longitude = Number(match[2]);
    return validPoint(latitude, longitude) ? {latitude, longitude, name:"坐标位置",coordinate:"wgs84"} : null;
  }
  function amapUrl(point) {
    if (!point) return "https://www.amap.com/";
    const url = new URL("https://uri.amap.com/marker");
    url.searchParams.set("position", `${point.longitude},${point.latitude}`);
    url.searchParams.set("name", point.name); url.searchParams.set("coordinate", point.coordinate || "gaode");
    url.searchParams.set("src", "智能助手"); url.searchParams.set("callnative", "0");
    return url.href;
  }
  function createMapFeature() {
    let panel, active=false, generation=0, request=null, scriptCancel=null, map=null, marker=null, point=null, selected=null, imu=null;
    let heading={heading:0,label:"北",source:"manual"}, busy=false, locationTicket=0, dragging=false, dragPointer=null;
    const pendingOperations=new Set();
    const el = name => panel.querySelector(`[data-map="${name}"]`);
    const status = text => {el("status").textContent=text;};
    const token = () => {try{return sessionStorage.getItem("assistant.translation.access")||"";}catch(_){return "";}};
    function updateHeading(state) {
      heading=state;
      if (!panel) return;
      const shownHeading=Math.round(((state.heading%360)+360)%360)%360;
      el("heading").textContent=`${state.source==="relative" ? "相对朝向" : "当前面对"} ${state.label||""} · ${shownHeading}°`;
      el("source").textContent=state.source==="sensor"?"设备 IMU":state.source==="relative"?(state.calibrated?"设备 IMU · 相对参考（已校准）":"设备 IMU · 未校准"):"眼镜 IMU 模拟";
      el("angle").value=String(shownHeading);
      el("dial").setAttribute("aria-valuenow",String(shownHeading));
      el("dial").setAttribute("aria-valuetext",`${state.label||""} ${shownHeading}度`);
      el("cardinals").style.transform=`rotate(${-state.heading}deg)`;
      if (state.message) el("imu-status").textContent=state.message;
      if (map) map.setRotation(el("follow").checked?-state.heading:0,true);
      marker?.setAngle?.(state.heading);
    }
    function updateLinks() {
      el("full").href=amapUrl(selected||point);
      const target=selected||point, nav=el("navigate");nav.hidden=!target;
      if(target){
        const url=new URL("https://uri.amap.com/navigation");
        url.searchParams.set("to",`${target.longitude},${target.latitude},${target.name}`);
        url.searchParams.set("mode",el("travel").value);url.searchParams.set("coordinate",target.coordinate||"gaode");
        url.searchParams.set("src","智能助手");url.searchParams.set("callnative","0");nav.href=url.href;
      }else nav.removeAttribute("href");
      const show=selected||point;
      el("place").textContent=show?show.name:"高德地图";
      el("coordinates").textContent=show?`${show.latitude.toFixed(5)}, ${show.longitude.toFixed(5)}`:"尚未定位";
    }
    function cancelPending() {
      generation++;locationTicket++;request?.abort();request=null;scriptCancel?.();scriptCancel=null;busy=false;
      for(const cancel of [...pendingOperations])cancel();
      if(panel){el("search").disabled=false;el("locate").disabled=false;el("reload").disabled=false;}
    }
    function loadSDK(key) {
      if(root.AMap?.Map)return Promise.resolve();
      return new Promise((resolve,reject)=>{
        const script=document.createElement("script"),url=new URL("https://webapi.amap.com/maps");
        const callback=`__lensAMapReady_${Date.now().toString(36)}_${(++sdkCallbackSequence).toString(36)}_${Math.random().toString(36).slice(2)}`;
        url.searchParams.set("v","2.0");url.searchParams.set("key",key);url.searchParams.set("plugin","AMap.PlaceSearch");
        url.searchParams.set("callback",callback);
        script.src=url.href;script.async=true;
        let settled=false;
        const finish=(error)=>{if(settled)return;settled=true;clearTimeout(timer);script.onload=script.onerror=null;if(root[callback]===ready)delete root[callback];if(scriptCancel===cancel)scriptCancel=null;if(error){script.remove();reject(error);}else resolve();};
        const ready=()=>finish(root.AMap?.Map?null:new Error("高德地图 SDK 未就绪，请检查配置后重试。"));
        const cancel=()=>finish(new DOMException("地图已关闭","AbortError"));
        const timer=setTimeout(()=>finish(new Error("高德地图加载超时，请检查网络后重试。")),15000);
        // The entry script can finish loading before its asynchronous SDK
        // initialization. Only the official callback guarantees readiness.
        root[callback]=ready;
        script.onload=()=>{if(root.AMap?.Map)finish();};
        script.onerror=()=>finish(new Error("高德地图加载失败，请检查网络或密钥域名设置。"));
        scriptCancel=cancel;document.head.append(script);
      });
    }
    async function loadMap() {
      if(!active||busy)return;
      cancelPending();const ticket=generation,controller=request=new AbortController();busy=true;el("reload").disabled=true;
      el("search").disabled=true;el("locate").disabled=true;
      status("正在加载高德地图…");
      const timer=setTimeout(()=>controller.abort(),12000);
      try{
        const credential=token();
        const response=await fetch("/api/map-config",{signal:controller.signal,headers:credential?{Authorization:`Bearer ${credential}`}:{}});
        const data=await response.json();if(!active||generation!==ticket)return;
        if(response.status===401){el("access").hidden=false;throw new Error("请输入访问口令后重新加载高德地图。译文与地图使用同一口令。");}
        if(!response.ok)throw new Error(data.error||"高德地图尚未配置，IMU 模拟仍可使用。");
        if(typeof data.key!=="string"||!data.key||data.serviceHost!=="/_AMapService")throw new Error("高德地图配置不完整，请联系管理员。");
        root._AMapSecurityConfig={serviceHost:location.origin+data.serviceHost};
        await loadSDK(data.key);if(!active||generation!==ticket)return;
        map?.destroy();map=new root.AMap.Map(el("canvas"),{viewMode:"3D",zoom:11,center:[116.397428,39.909154],rotation:-heading.heading,pitch:0,rotateEnable:false});
        el("placeholder").hidden=true;el("access").hidden=true;updateHeading(heading);
        if(selected||point)await displayPoint(selected||point,ticket);
        if(!active||generation!==ticket)return;
        status("高德地图已就绪。拖动罗盘模拟转向，地图随面对方向转动。");
      }catch(error){
        if(active&&generation===ticket)status(error.name==="AbortError"?"地图加载超时，请重试。":error.message||"高德地图暂不可用。");
      }finally{clearTimeout(timer);if(generation===ticket){request=null;busy=false;el("reload").disabled=false;el("search").disabled=false;el("locate").disabled=false;}}
    }
    async function toGaode(value,ticket) {
      if(value.coordinate!=="wgs84")return value;
      return new Promise((resolve,reject)=>{
        let settled=false;
        const finish=(result,error)=>{if(settled)return;settled=true;clearTimeout(timer);pendingOperations.delete(cancel);if(error)reject(error);else resolve(result);};
        const cancel=()=>finish(null),timer=setTimeout(()=>finish(null,new Error("高德坐标转换超时，请重试。")),10000);pendingOperations.add(cancel);
        root.AMap.convertFrom([value.longitude,value.latitude],"gps",(state,data)=>{
          if(!active||generation!==ticket){finish(null);return;}
          const converted=data?.locations?.[0];
          if(state!=="complete"||!converted){finish(null,new Error("高德坐标转换失败，请重试。"));return;}
          const longitude=Number(converted.getLng?.()??converted.lng),latitude=Number(converted.getLat?.()??converted.lat);
          if(!validPoint(latitude,longitude)){finish(null,new Error("高德返回无效坐标。"));return;}
          finish({...value,longitude,latitude,coordinate:"gaode"});
        });
      });
    }
    async function displayPoint(value,ticket=generation) {
      if(!map)return;
      const converted=await toGaode(value,ticket);if(!converted||!active||generation!==ticket)return;
      marker?.setMap(null);
      marker=new root.AMap.Marker({position:[converted.longitude,converted.latitude],content:'<span class="lens-map-position" aria-label="选定位置">▲</span>',angle:heading.heading});marker.setMap(map);
      map.setZoomAndCenter(16,[converted.longitude,converted.latitude]);updateHeading(heading);
    }
    async function selectPoint(value) {
      if(!validPoint(value.latitude,value.longitude))return;
      cancelPending();const ticket=generation;selected=value;el("results").replaceChildren();updateLinks();
      try{await displayPoint(value,ticket);if(active&&generation===ticket)status("已选择位置，可模拟转向或打开高德导航。");}catch(error){if(active&&generation===ticket)status(error.message);}
    }
    function search() {
      if(!active||busy)return;
      const query=el("query").value.trim();if(query.length<2||query.length>100){status("请输入 2–100 字的地点名，或纬度,经度。");return;}
      const direct=coordinates(query);if(direct){void selectPoint(direct);return;}
      if(!map){const url=new URL("https://uri.amap.com/search");url.searchParams.set("keyword",query);url.searchParams.set("view","map");url.searchParams.set("src","智能助手");url.searchParams.set("callnative","0");el("full").href=url.href;status("镜片内地图尚未配置，可点击打开高德搜索该地点。");return;}
      cancelPending();const ticket=generation;el("search").disabled=true;status("正在搜索高德地点…");el("results").replaceChildren();
      const finder=new root.AMap.PlaceSearch({pageSize:5});
      let settled=false;
      const cancel=()=>{settled=true;clearTimeout(timer);pendingOperations.delete(cancel);};
      const timer=setTimeout(()=>{cancel();if(active&&generation===ticket){el("search").disabled=false;status("高德地点搜索超时，请重试。");}},10000);pendingOperations.add(cancel);
      finder.search(query,(state,data)=>{
        if(settled||!active||generation!==ticket)return;cancel();el("search").disabled=false;
        const pois=state==="complete"?(data?.poiList?.pois||[]):[];
        if(!pois.length){status("没有找到该地点，请换个名称。 ");return;}
        pois.slice(0,5).forEach(item=>{
          const latitude=Number(item.location?.getLat?.()??item.location?.lat),longitude=Number(item.location?.getLng?.()??item.location?.lng);if(!validPoint(latitude,longitude))return;
          const button=document.createElement("button");button.type="button";button.textContent=[item.name,item.address].filter(Boolean).join(" · ");button.addEventListener("click",()=>void selectPoint({latitude,longitude,name:item.name||"选定地点",coordinate:"gaode"}));el("results").append(button);
        });status("请选择地点，然后查看朝向与导航。");
      });
    }
    function locate() {
      if(!active)return;if(!navigator.geolocation){status("当前浏览器不支持定位，请搜索地点或输入坐标。");return;}
      cancelPending();const ticket=generation,geoTicket=locationTicket;el("locate").disabled=true;status("正在定位，请允许浏览器位置访问…");
      navigator.geolocation.getCurrentPosition(async result=>{
        if(!active||generation!==ticket||locationTicket!==geoTicket)return;el("locate").disabled=false;
        const {latitude,longitude}=result.coords;if(!validPoint(latitude,longitude)){status("定位坐标无效，请重试。");return;}
        point={latitude,longitude,name:"我的位置",coordinate:"wgs84"};selected=null;updateLinks();
        try{await displayPoint(point,ticket);if(active&&generation===ticket)status(map?"已定位，地图朝向与 IMU 同步。":"位置已获取；高德地图配置完成后可在镜片查看，也可打开高德。");}catch(error){if(active&&generation===ticket)status(error.message);}
      },error=>{if(!active||generation!==ticket)return;el("locate").disabled=false;status(error.code===1?"定位权限未开启，可搜索地点或输入坐标。":"定位失败或超时，请重试。");},{enableHighAccuracy:false,timeout:10000,maximumAge:30000});
    }
    return {
      mount(target) {
        panel=target;
        panel.innerHTML=`<header><strong>高德地图 · 朝向</strong><button type="button" data-hw-back>返回</button></header>
        <div class="hw-panel-scroll lens-map-content"><div class="lens-map-imu">
          <div class="lens-map-dial" data-map="dial" role="slider" tabindex="0" aria-label="模拟眼镜朝向" aria-valuemin="0" aria-valuemax="359" aria-valuenow="0"><div data-map="cardinals" class="lens-map-cardinals"><b>北 N</b><span>东 E</span><span>南 S</span><span>西 W</span></div><i class="lens-map-facing"></i></div>
          <div class="lens-map-direction"><strong data-map="heading">当前面对 北 · 0°</strong><span data-map="source">眼镜 IMU 模拟</span><label>模拟转向<input data-map="angle" type="range" min="0" max="359" value="0" aria-label="模拟转向角度"></label><div><button type="button" data-map="sensor">读取设备方向</button><button type="button" data-map="calibrate">以当前方向为北</button></div><label><input type="checkbox" data-map="follow" checked>朝向朝上</label></div></div>
          <p data-map="imu-status" class="lens-map-imu-status" role="status">拖动罗盘或滑杆模拟眼镜转向。电脑有方向传感器时，可点击读取设备方向。</p>
          <form data-map="form" class="lens-map-search"><label><span class="lens-map-sr">搜索高德地点</span><input data-map="query" maxlength="100" placeholder="搜索地点 / 纬度,经度" autocomplete="off"></label><button type="submit" data-map="search">搜索</button><button type="button" data-map="locate">定位到我的位置</button></form>
          <div data-map="access" class="lens-map-access" hidden><label>地图访问口令<input type="password" data-map="token" autocomplete="current-password" aria-label="地图访问口令"></label></div>
          <p data-map="status" role="status" aria-live="polite"></p><div data-map="results" class="lens-map-results" aria-label="高德地点搜索结果"></div>
          <div class="lens-map-heading"><strong data-map="place">高德地图</strong><span data-map="coordinates">尚未定位</span></div>
          <div class="lens-map-canvas-wrap"><div data-map="canvas" class="lens-map-canvas" aria-label="高德交互地图"></div><div data-map="placeholder" class="lens-map-placeholder"><strong>高德地图</strong><span>地图正在等待配置或加载，方向模拟可直接使用。</span></div></div>
          <div class="lens-map-actions"><button type="button" data-map="reload">重新加载地图</button><button type="button" data-map="zoom-in">放大</button><button type="button" data-map="zoom-out">缩小</button><label>出行方式<select data-map="travel"><option value="walk">步行</option><option value="car">驾车</option><option value="bus">公交</option></select></label><a data-map="navigate" target="_blank" rel="noopener noreferrer" hidden>高德导航</a><a data-map="full" href="https://www.amap.com/" target="_blank" rel="noopener noreferrer">打开高德</a></div>
          <p class="lens-map-credit">地图由高德提供。手动方向标记为模拟；没有传感器的电脑无法测量物理朝向。定位仅在允许权限后获取。</p></div>`;
        imu=root.LensIMU?.create({onChange:updateHeading});
        const manual=value=>{if(imu)imu.setHeading(value);else updateHeading({heading:((value%360)+360)%360,label:["北","东北","东","东南","南","西南","西","西北"][Math.round(value/45)%8],source:"manual"});};
        el("angle").addEventListener("input",()=>manual(Number(el("angle").value)));
        const turn=event=>{const box=el("dial").getBoundingClientRect();manual((Math.atan2(event.clientX-box.left-box.width/2,-(event.clientY-box.top-box.height/2))*180/Math.PI+360)%360);};
        el("dial").addEventListener("pointerdown",event=>{if(!active)return;dragging=true;dragPointer=event.pointerId;el("dial").setPointerCapture?.(event.pointerId);turn(event);event.preventDefault();});
        el("dial").addEventListener("pointermove",event=>{if(active&&dragging)turn(event);});
        for(const name of ["pointerup","pointercancel","lostpointercapture"])el("dial").addEventListener(name,()=>{dragging=false;});
        el("dial").addEventListener("keydown",event=>{if(!active||!["ArrowLeft","ArrowRight","ArrowUp","ArrowDown","Home","End"].includes(event.key))return;event.preventDefault();event.stopPropagation();manual(event.key==="Home"?0:event.key==="End"?359:heading.heading+(["ArrowLeft","ArrowDown"].includes(event.key)?-5:5));});
        el("sensor").addEventListener("click",()=>{if(!active)return;if(imu)void imu.start();else el("imu-status").textContent="当前浏览器没有可用方向传感器，可拖动罗盘模拟。";});
        el("calibrate").addEventListener("click",()=>{if(active)imu?imu.calibrate(0):manual(0);});
        el("follow").addEventListener("change",()=>updateHeading(heading));
        el("form").addEventListener("submit",event=>{event.preventDefault();search();});el("locate").addEventListener("click",locate);
        el("token").addEventListener("change",()=>{try{sessionStorage.setItem("assistant.translation.access",el("token").value.trim());}catch(_){}el("token").value="";});
        el("reload").addEventListener("click",()=>void loadMap());
        el("zoom-in").addEventListener("click",()=>map?.zoomIn());el("zoom-out").addEventListener("click",()=>map?.zoomOut());
        el("travel").addEventListener("change",updateLinks);updateLinks();updateHeading(heading);
      },
      open(){if(active)return;active=true;void loadMap();},
      close(){active=false;dragging=false;try{if(dragPointer!==null)el("dial").releasePointerCapture?.(dragPointer);}catch(_){}dragPointer=null;cancelPending();imu?.stop();map?.destroy();map=null;marker=null;if(panel)el("placeholder").hidden=false;},
    };
  }
  root.LensFeatures=root.LensFeatures||{};root.LensFeatures.map=createMapFeature();
  if(typeof module!=="undefined"&&module.exports)module.exports={coordinates,amapUrl,createMapFeature};
})(typeof window!=="undefined"?window:globalThis);
