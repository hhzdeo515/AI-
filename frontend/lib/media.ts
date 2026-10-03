/** A late permission grant must never reopen a camera after its view was closed. */
export class MediaLease {
  private generation = 0;
  private stream: MediaStream | null = null;
  async open(request: () => Promise<MediaStream>): Promise<MediaStream | null> {
    this.stop();
    const current = this.generation;
    const stream = await request();
    if (current !== this.generation) { stream.getTracks().forEach(track => track.stop()); return null; }
    this.stream = stream;
    return stream;
  }
  stop() {
    this.generation++;
    this.stream?.getTracks().forEach(track => track.stop());
    this.stream = null;
  }
}
export function recordingMime(): string {
  return ["audio/webm;codecs=opus", "audio/mp4", "audio/ogg;codecs=opus", "audio/webm"]
    .find(mime => MediaRecorder.isTypeSupported(mime)) || "";
}
export function validateAttachments(files: File[]): string {
  if (files.some(file => !/\.(png|jpe?g|webp|bmp|gif|tiff?|wav|mp3|m4a|aac|flac|ogg|amr|wma|webm)$/i.test(file.name))) return "请选择图片或音频文件。";
  if (files.some(file => file.size === 0)) return "附件为空，请重新选择。";
  if (files.reduce((total, file) => total + file.size, 0) > 31 * 1024 * 1024) return "附件总大小请控制在 31 MB 内，为文字和上传信息预留空间。";
  return "";
}
export const isImage = (file: File) => /^image\//.test(file.type) || /\.(png|jpe?g|webp|bmp|gif|tiff?)$/i.test(file.name);
