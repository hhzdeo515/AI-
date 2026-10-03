"use client";
import { useEffect, useState } from "react";
export function useObjectUrls(files: File[]) {
  const [urls, setUrls] = useState<string[]>([]);
  useEffect(() => {
    const next = files.map(file => URL.createObjectURL(file)); setUrls(next);
    return () => next.forEach(url => URL.revokeObjectURL(url));
  }, [files]);
  return urls;
}
