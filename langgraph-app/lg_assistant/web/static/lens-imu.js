/* Optical forward heading for the lens; no sensor access until start(). */
(function (root) {
  "use strict";
  const directions = ["北", "东北", "东", "东南", "南", "西南", "西", "西北"];
  const finite = value => typeof value === "number" && Number.isFinite(value);
  const normalize = value => finite(value) ? ((value % 360) + 360) % 360 : null;
  const label = heading => directions[Math.round(heading / 45) % directions.length];

  function facingHeading(alpha, beta, gamma) {
    if (![alpha, beta, gamma].every(finite)) return null;
    const radians = Math.PI / 180, a = alpha * radians, b = beta * radians, g = gamma * radians;
    // W3C Device Orientation, appendix A.1: project Rz(alpha) Rx(beta)
    // Ry(gamma) [0, 0, -1] onto the ground, with north as zero.
    // https://www.w3.org/TR/orientation-event/#worked-example
    const east = -Math.cos(a) * Math.sin(g) - Math.sin(a) * Math.sin(b) * Math.cos(g);
    const north = -Math.sin(a) * Math.sin(g) + Math.cos(a) * Math.sin(b) * Math.cos(g);
    if (Math.hypot(east, north) < 0.001) return null;
    return normalize(Math.atan2(east, north) / radians);
  }

  function create(options = {}) {
    let generation = 0, listening = false, timer = null, pending = null, complete = null;
    let removers = [], lastReading = null, priority = 0, reference = null, offset = 0, calibrated = false;
    const screenAngle = () => normalize(root.screen?.orientation?.angle ?? root.orientation) ?? 0;
    let state = {
      heading: 0, label: "北", source: "manual", available: false,
      absolute: false, calibrated: false, pitch: null, roll: null,
      screenAngle: screenAngle(), status: "manual", message: "手动模拟朝向，未读取设备传感器。",
    };
    const snapshot = () => ({ ...state });
    function publish(next) {
      state = { ...state, ...next, screenAngle: screenAngle() };
      state.label = label(state.heading);
      if (typeof options.onChange === "function") options.onChange(snapshot());
    }
    function settle() {
      const done = complete;
      complete = null; pending = null;
      if (done) done(snapshot());
    }
    function detach() {
      listening = false;
      if (timer !== null) root.clearTimeout(timer);
      timer = null;
      for (const remove of removers) remove();
      removers = []; lastReading = null; priority = 0; reference = null; offset = 0; calibrated = false;
    }
    function manual(status, message, heading = state.heading) {
      generation++;
      detach();
      publish({ heading, source: "manual", available: false, absolute: false, calibrated: false, pitch: null, roll: null, status, message });
      settle();
      return snapshot();
    }
    function stop() {
      return manual("stopped", "方向传感器已停止；当前朝向为手动模拟。");
    }
    function setHeading(degrees) {
      const heading = normalize(degrees);
      if (heading === null) return snapshot();
      return manual("manual", "手动模拟朝向，未读取真实朝向。", heading);
    }
    function readingMessage() {
      if (lastReading.source === "relative") return calibrated
        ? "相对传感器方向（已校准参考方向，非绝对朝向）。"
        : "相对传感器方向（未校准），请面朝北后校准参考方向。";
      return calibrated ? "方向传感器参考朝向（已校准）。" : "设备方向传感器朝向。";
    }
    function publishReading() {
      publish({ heading: normalize(lastReading.rawHeading + offset), source: lastReading.source,
        available: true, absolute: lastReading.absolute, calibrated,
        pitch: lastReading.pitch, roll: lastReading.roll, status: "active", message: readingMessage() });
    }
    function calibrate(degrees = 0) {
      const target = normalize(degrees);
      if (target === null) return snapshot();
      if (!listening || !lastReading || !state.available) return setHeading(target);
      offset = normalize(target - lastReading.rawHeading);
      calibrated = true;
      publishReading();
      return snapshot();
    }
    function listen(object, type, fn) {
      if (!object?.addEventListener) return;
      object.addEventListener(type, fn);
      removers.push(() => object.removeEventListener(type, fn));
    }
    function subscribe(token) {
      if (token !== generation) return;
      if (root.document?.hidden) {
        manual("stopped", "页面已隐藏；请返回地图后重新启用方向传感器，当前可手动模拟。");
        return;
      }
      listening = true;
      const accept = event => {
        if (token !== generation || !listening) return;
        if (![event.beta, event.gamma].every(finite)) return;
        const absolute = event.absolute === true || event.type === "deviceorientationabsolute";
        let alpha, source, rank, kind;
        if (absolute && finite(event.alpha)) {
          alpha = event.alpha; source = "sensor"; rank = 3; kind = "absolute";
        } else if (finite(event.webkitCompassHeading) && event.webkitCompassHeading >= 0
          && event.webkitCompassHeading <= 360 && !(finite(event.webkitCompassAccuracy) && event.webkitCompassAccuracy < 0)) {
          // WebKit supplies clockwise magnetic yaw, opposite the alpha sense.
          alpha = normalize(360 - event.webkitCompassHeading); source = "sensor"; rank = 2; kind = "webkit";
        } else if (finite(event.alpha)) {
          alpha = event.alpha; source = "relative"; rank = 1; kind = "relative";
        } else return;
        if (rank < priority) return;
        const heading = facingHeading(alpha, event.beta, event.gamma);
        if (heading === null) {
          lastReading = null;
          publish({ source: "manual", available: false, absolute: false, calibrated: false,
            pitch: event.beta, roll: event.gamma, status: "tilt",
            message: "设备前方接近竖直地面，无法确定水平朝向；请立起屏幕或使用手动模拟。" });
          return;
        }
        if (reference !== kind) { offset = 0; calibrated = false; }
        reference = kind; priority = rank;
        lastReading = { rawHeading: heading, source, absolute: source === "sensor", pitch: event.beta, roll: event.gamma };
        publishReading();
        if (timer !== null) root.clearTimeout(timer);
        timer = null;
        settle();
      };
      const rotateScreen = () => {
        if (token !== generation || !listening) return;
        // DeviceOrientation uses the device's *standard* screen axes. Rotating
        // the document rotates X/Y about Z; optical forward (-Z) is unchanged.
        // Adding screen.orientation.angle to physical yaw would falsely turn
        // the map when only the browser changes between portrait/landscape.
        publish({});
      };
      const hide = () => { if (token === generation) stop(); };
      listen(root, "deviceorientationabsolute", accept);
      listen(root, "deviceorientation", accept);
      listen(root.screen?.orientation, "change", rotateScreen);
      listen(root, "orientationchange", rotateScreen);
      listen(root, "pagehide", hide);
      listen(root.document, "visibilitychange", () => { if (root.document.hidden) hide(); });
      timer = root.setTimeout(() => {
        if (token !== generation) return;
        manual("unavailable", "未收到可用方向传感器数据；电脑可能不支持方向传感器，请使用手动模拟转向。");
      }, 2500);
      publish({ status: "waiting", message: "等待方向传感器；请让屏幕近似竖直，当前仍为手动模拟。" });
    }
    function start() {
      if (pending) return pending;
      if (listening) return Promise.resolve(snapshot());
      const token = ++generation;
      detach();
      pending = new Promise(resolve => { complete = resolve; });
      const result = pending;
      publish({ source: "manual", available: false, absolute: false, calibrated: false,
        status: "requesting", message: "正在启用方向传感器；当前朝向为手动模拟。" });
      const Orientation = root.DeviceOrientationEvent;
      if (root.isSecureContext === false) {
        manual("unavailable", "方向传感器需要 HTTPS；当前可使用手动模拟转向。");
      } else if (!Orientation) {
        manual("unavailable", "此浏览器不支持方向传感器；请使用手动模拟转向。");
      } else if (typeof Orientation.requestPermission === "function") {
        // Invoke before any await: iOS requires the button's user activation.
        try {
          Promise.resolve(Orientation.requestPermission(true)).then(permission => {
            if (token !== generation) return;
            if (permission === "granted") subscribe(token);
            else manual("denied", "方向传感器权限未获允许；请使用手动模拟转向。");
          }, () => {
            if (token === generation) manual("denied", "无法启用方向传感器权限；请使用手动模拟转向。");
          });
        } catch (_) {
          if (token === generation) manual("denied", "无法启用方向传感器权限；请使用手动模拟转向。");
        }
      } else subscribe(token);
      return result;
    }
    return { start, stop, setHeading, calibrate, get status() { return snapshot(); } };
  }
  root.LensIMU = { create };
})(window);
