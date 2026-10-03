export type RingAction = "previous" | "next" | "up" | "down" | "back" | "press" | "double";
export interface RingFlow { phase: "start" | "move" | "end" | "cancel"; axis?: "x" | "y"; total?: number; dx?: number; dy?: number }
export interface RingModel { setState(state: { connected: boolean; busy: boolean; error: boolean }): void; rotate(dx: number, dy: number): void; reset(): void; setVisible(visible: boolean): void; destroy(): void }
export interface RingInput { cancel(): void; destroy(): void }
export function classifyGesture(gesture?: { dx?: number; dy?: number; duration?: number; cancelled?: boolean; inspect?: boolean; vertical?: boolean }): Exclude<RingAction, "double"> | null;
export function bindInput(button: HTMLButtonElement, options: { action: (action: RingAction) => void; inspect?: () => boolean; rotate?: (dx: number, dy: number) => void; feedback?: (message: string) => void; vertical?: () => boolean; doubleTap?: boolean; flow?: boolean; onFlow?: (flow: RingFlow) => void }): RingInput;
export function mount(canvas: HTMLCanvasElement, options?: { onUnavailable?: () => void }): RingModel | null;
