/* Blender GLB renderer. DOM lens controls share the exact same CSS perspective. */
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";

const PERSPECTIVE = 1600;
const PIVOT_DEPTH = 350;

export function mount(stage, { onReady, onUnavailable } = {}) {
  let renderer, environment, pmrem, room, observer, frame = 0;
  let scene, camera, assembly, rotating, asset;
  let disposed = false, ready = false, pageVisible = true;
  let state = { pitch: -24, yaw: -18, scale: .5, offset: 0 };
  const canvas = document.createElement("canvas");
  const outline = Array.from({ length: 96 }, (_, i) => {
    const t = i * Math.PI * 2 / 96;
    const z = Math.sign(Math.sin(t)) * Math.abs(Math.sin(t)) ** (2 / 3.4);
    const x = Math.sign(Math.cos(t)) * Math.abs(Math.cos(t)) ** (2 / 3.4) * (.97 + .03 * z);
    return `${(50 + x * 50).toFixed(3)}% ${(50 - z * 50).toFixed(3)}%`;
  });
  stage.style.setProperty("--real-lens-outline", `polygon(${outline.join(",")})`);
  canvas.className = "hw-real-glasses-canvas";
  canvas.setAttribute("aria-hidden", "true");
  stage.prepend(canvas);
  const visible = () => pageVisible && !document.hidden && location.hash !== "#memory";
  const draw = () => {
    frame = 0;
    if (disposed || !ready || !visible()) return;
    const w = stage.clientWidth, h = stage.clientHeight;
    if (!w || !h) return;
    // Match CSS perspective: a point at z=0 is one WebGL unit per CSS pixel.
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.fov = THREE.MathUtils.radToDeg(2 * Math.atan(h / (2 * PERSPECTIVE)));
    camera.updateProjectionMatrix();
    assembly.position.set(state.scale * state.offset, 0, -PIVOT_DEPTH);
    assembly.scale.setScalar(state.scale);
    rotating.rotation.set(-state.pitch * Math.PI / 180, state.yaw * Math.PI / 180, 0, "XYZ");
    renderer.render(scene, camera);
  };
  function requestRender() {
    if (!disposed && !frame && ready && visible()) frame = requestAnimationFrame(draw);
  }
  function releaseAsset(object) {
    object?.traverse(child => {
      child.geometry?.dispose();
      const materials = child.material ? (Array.isArray(child.material) ? child.material : [child.material]) : [];
      for (const material of materials) {
        for (const value of Object.values(material)) if (value?.isTexture) value.dispose();
        material.dispose();
      }
    });
  }
  function dispose() {
    if (disposed) return;
    disposed = true;
    cancelAnimationFrame(frame);
    observer?.disconnect();
    document.removeEventListener("visibilitychange", requestRender);
    window.removeEventListener("hashchange", requestRender);
    window.removeEventListener("pageshow", show);
    window.removeEventListener("pagehide", hide);
    releaseAsset(asset);
    environment?.dispose();
    pmrem?.dispose();
    room?.dispose();
    renderer?.dispose();
    canvas.remove();
  }
  function fail() {
    if (disposed) return;
    dispose();
    onUnavailable?.();
  }
  function hide(event) {
    pageVisible = false;
    cancelAnimationFrame(frame); frame = 0;
    if (!event.persisted) dispose();
  }
  function show() { pageVisible = true; requestRender(); }
  try {
    renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true, powerPreference: "low-power" });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.1;
    renderer.setClearColor(0x000000, 0);
    scene = new THREE.Scene();
    camera = new THREE.PerspectiveCamera(30, 1, 1, 12000);
    camera.position.z = PERSPECTIVE;
    room = new RoomEnvironment();
    pmrem = new THREE.PMREMGenerator(renderer);
    environment = pmrem.fromScene(room, .04);
    scene.environment = environment.texture;
    scene.environmentIntensity = .9;
    const key = new THREE.DirectionalLight(0xf2f6ff, 2.2);
    key.position.set(-600, 900, 1200);
    scene.add(key);
    const rim = new THREE.DirectionalLight(0xc3e3dd, 2.5);
    rim.position.set(900, 500, -700);
    scene.add(rim);
    scene.add(new THREE.HemisphereLight(0xdce8f4, 0x26332e, .7));
    assembly = new THREE.Group();
    rotating = new THREE.Group();
    assembly.add(rotating);
    scene.add(assembly);
    canvas.addEventListener("webglcontextlost", event => { event.preventDefault(); fail(); }, { once: true });
    observer = new ResizeObserver(requestRender);
    observer.observe(stage);
    document.addEventListener("visibilitychange", requestRender);
    window.addEventListener("hashchange", requestRender);
    window.addEventListener("pageshow", show);
    window.addEventListener("pagehide", hide);
    new GLTFLoader().load("/static/models/smart-glasses.glb", gltf => {
      if (disposed) { releaseAsset(gltf.scene); return; }
      asset = gltf.scene;
      asset.scale.setScalar(10000); // metres -> 10 CSS pixels per millimetre
      asset.position.z = PIVOT_DEPTH;
      asset.traverse(child => {
        if (!child.isMesh) return;
        child.frustumCulled = false;
        // DOM camera and menu sit over these clear physical lenses.
        // Retain optical highlights without a costly screen-space transmission pass.
        if (child.name.startsWith("Optical_lens") || child.name.startsWith("Optical lens")) {
          child.material.transmission = 0;
          child.material.transparent = true;
          child.material.opacity = .09;
          child.material.depthWrite = false;
          child.material.color.set(0x9fced0);
          child.material.metalness = .08;
          child.material.roughness = .12;
        }
      });
      rotating.add(asset);
      ready = true;
      onReady?.();
      requestRender();
    }, undefined, fail);
  } catch (_) { fail(); }
  return {
    setView(next) { state = { ...state, ...next }; requestRender(); },
    dispose,
  };
}

window.SmartGlasses = { mount };
