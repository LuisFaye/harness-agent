export function isImageFile(file: File): boolean {
  if (file.type.startsWith("image/")) {
    return true;
  }
  const name = file.name.toLowerCase();
  return [".png", ".jpg", ".jpeg", ".webp", ".gif"].some((suffix) => name.endsWith(suffix));
}

export async function createAttachmentPreviewUrl(file: File): Promise<string> {
  if (isImageFile(file)) {
    return readFileAsDataUrl(file);
  }
  return URL.createObjectURL(file);
}

export function releaseAttachmentPreviewUrl(url: string): void {
  if (url.startsWith("blob:")) {
    URL.revokeObjectURL(url);
  }
}

function readFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(reader.error ?? new Error("预览生成失败"));
    reader.readAsDataURL(file);
  });
}
