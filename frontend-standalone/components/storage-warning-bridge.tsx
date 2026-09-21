"use client";

import { useEffect, useState } from "react";
import { getLatestStorageWarning, STORAGE_WARNING_EVENT, type StorageWarning } from "@/store/storage-persistence";

export function StorageWarningBridge() {
  const [message, setMessage] = useState("");
  useEffect(() => {
    const handle = (event: Event) => {
      const detail = (event as CustomEvent<StorageWarning>).detail;
      if (detail?.operation === "write") {
        setMessage("浏览器轻量缓存暂时不可用，行程仍会从后端正常读取。");
      }
    };
    if (getLatestStorageWarning()?.operation === "write") handle(new CustomEvent(STORAGE_WARNING_EVENT, { detail: getLatestStorageWarning() }));
    window.addEventListener(STORAGE_WARNING_EVENT, handle);
    return () => window.removeEventListener(STORAGE_WARNING_EVENT, handle);
  }, []);
  if (!message) return null;
  return <div className="storage-warning" role="status">{message}</div>;
}
