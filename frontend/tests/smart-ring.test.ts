import { afterEach, describe, expect, it, vi } from "vitest";
import { mount } from "../lib/smart-ring";

afterEach(() => { vi.restoreAllMocks(); document.body.replaceChildren(); });

function setup(compiles = true) {
  const ring = document.createElement("button"), canvas = document.createElement("canvas");
  ring.append(canvas); document.body.append(ring);
  Object.defineProperties(canvas, { clientWidth: { value: 190 }, clientHeight: { value: 205 } });
  const program = {}, buffer = {};
  const gl = {
    VERTEX_SHADER: 1, FRAGMENT_SHADER: 2, COMPILE_STATUS: 3, LINK_STATUS: 4, ARRAY_BUFFER: 5,
    STATIC_DRAW: 6, FLOAT: 7, DEPTH_TEST: 8, COLOR_BUFFER_BIT: 16, DEPTH_BUFFER_BIT: 32, TRIANGLES: 9,
    createProgram: vi.fn(() => program), createShader: vi.fn(() => ({})), shaderSource: vi.fn(), compileShader: vi.fn(),
    getShaderParameter: vi.fn(() => compiles), attachShader: vi.fn(), linkProgram: vi.fn(), deleteShader: vi.fn(),
    getProgramParameter: vi.fn(() => true), createBuffer: vi.fn(() => buffer), bindBuffer: vi.fn(), bufferData: vi.fn(),
    useProgram: vi.fn(), getAttribLocation: vi.fn(() => 0), enableVertexAttribArray: vi.fn(), vertexAttribPointer: vi.fn(),
    getUniformLocation: vi.fn(() => ({})), enable: vi.fn(), clearColor: vi.fn(), isContextLost: vi.fn(() => false),
    viewport: vi.fn(), clear: vi.fn(), uniformMatrix4fv: vi.fn(), uniform3fv: vi.fn(), uniform1f: vi.fn(), drawArrays: vi.fn(),
    deleteBuffer: vi.fn(), deleteProgram: vi.fn(),
  };
  vi.spyOn(canvas, "getContext").mockReturnValue(gl as unknown as WebGLRenderingContext);
  const frame = vi.spyOn(window, "requestAnimationFrame").mockReturnValue(42);
  const cancel = vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => {});
  return { ring, canvas, gl, frame, cancel, program, buffer };
}

describe("戒指模型生命周期", () => {
  it("按原几何绘制，隐藏停止帧，恢复后重绘；上下文丢失使用CSS造型，卸载释放资源", () => {
    const { ring, canvas, gl, frame, cancel, program, buffer } = setup();
    const unavailable = vi.fn();
    const model = mount(canvas, { onUnavailable: unavailable })!;
    expect(model).toBeTruthy(); expect(ring.classList.contains("has-model")).toBe(true);
    const vertices = gl.bufferData.mock.calls[0][1] as Float32Array;
    expect(vertices.length).toBeGreaterThan(300000);
    expect(vertices.every(Number.isFinite)).toBe(true);
    frame.mock.calls[0][0](0);
    expect(gl.drawArrays).toHaveBeenCalledWith(gl.TRIANGLES, 0, vertices.length / 7);
    model.rotate(20, 10);
    model.setVisible(false);
    expect(cancel).toHaveBeenCalledWith(42);
    const scheduled = frame.mock.calls.length;
    model.setVisible(true);
    expect(frame.mock.calls.length).toBe(scheduled + 1);
    const lost = new Event("webglcontextlost", { cancelable: true });
    canvas.dispatchEvent(lost);
    expect(lost.defaultPrevented).toBe(true); expect(unavailable).toHaveBeenCalledTimes(1);
    expect(ring.classList.contains("has-model")).toBe(false);
    model.destroy();
    expect(gl.deleteBuffer).toHaveBeenCalledWith(buffer); expect(gl.deleteProgram).toHaveBeenCalledWith(program);
    canvas.dispatchEvent(new Event("webglcontextlost", { cancelable: true }));
    expect(unavailable).toHaveBeenCalledTimes(1);
  });

  it("模型编译失败保留原生触控造型并清理已分配的GPU资源", () => {
    const { ring, canvas, gl, program } = setup(false);
    expect(mount(canvas)).toBeNull();
    expect(ring.classList.contains("has-model")).toBe(false);
    expect(gl.deleteProgram).toHaveBeenCalledWith(program);
    expect(gl.deleteShader).toHaveBeenCalledTimes(1);
  });
});
