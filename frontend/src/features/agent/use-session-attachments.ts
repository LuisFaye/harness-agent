import { useCallback, useEffect, useRef, useState, type SetStateAction } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { uploadSessionAttachment } from "@/lib/api/agent";
import type { Attachment } from "@/lib/api/schemas";
import { createAttachmentPreviewUrl, releaseAttachmentPreviewUrl } from "./attachment-preview";

export interface PendingAttachment {
  localId: string;
  file: File;
  previewUrl: string;
  attachment?: Attachment;
  uploading: boolean;
  error?: string | undefined;
}

interface UploadTask {
  sessionId: string;
  item: PendingAttachment;
  controller: AbortController;
  done: () => void;
}

const EMPTY_ATTACHMENTS: PendingAttachment[] = [];

export function useSessionAttachments(userId: string, sessionId: string) {
  const queryClient = useQueryClient();
  const sessionsRef = useRef(new Map<string, PendingAttachment[]>());
  const noticesRef = useRef(new Map<string, string>());
  const previewsRef = useRef(new Set<string>());
  const tasksRef = useRef(new Map<string, UploadTask>());
  const queueRef = useRef<UploadTask[]>([]);
  const runningRef = useRef(0);
  const mountedRef = useRef(true);
  const [, render] = useState(0);

  const notify = useCallback(() => {
    if (mountedRef.current) render((value) => value + 1);
  }, []);

  const updateSession = useCallback((id: string, update: SetStateAction<PendingAttachment[]>) => {
    const current = sessionsRef.current.get(id) ?? EMPTY_ATTACHMENTS;
    sessionsRef.current.set(id, typeof update === "function" ? update(current) : update);
    notify();
  }, [notify]);

  const setPendingAttachments = useCallback((update: SetStateAction<PendingAttachment[]>) => {
    updateSession(sessionId, update);
  }, [sessionId, updateSession]);

  function updateTask(task: UploadTask, patch: Partial<PendingAttachment>) {
    if (!mountedRef.current || task.controller.signal.aborted) return;
    updateSession(task.sessionId, (items) => items.map((item) => (
      item.localId === task.item.localId ? { ...item, ...patch } : item
    )));
  }

  async function runTask(task: UploadTask) {
    try {
      const previewUrl = task.item.previewUrl || await createAttachmentPreviewUrl(task.item.file);
      if (!mountedRef.current || task.controller.signal.aborted) {
        releaseAttachmentPreviewUrl(previewUrl);
        return;
      }
      if (previewUrl.startsWith("blob:")) previewsRef.current.add(previewUrl);
      updateTask(task, { previewUrl });
      const attachment = await uploadSessionAttachment(
        userId, task.sessionId, task.item.file, task.controller.signal,
      );
      updateTask(task, { attachment, uploading: false, error: undefined });
      if (!task.controller.signal.aborted && mountedRef.current) {
        void queryClient.invalidateQueries({ queryKey: ["attachments", userId, task.sessionId] });
      }
    } catch (error) {
      updateTask(task, {
        uploading: false,
        error: error instanceof Error ? error.message : "附件上传失败，请重试。",
      });
    } finally {
      if (tasksRef.current.get(task.item.localId) === task) tasksRef.current.delete(task.item.localId);
      runningRef.current -= 1;
      task.done();
      pump();
    }
  }

  function pump() {
    // 所有会话共用三个上传槽位，切换会话不会让旧上传写入新会话。
    while (mountedRef.current && runningRef.current < 3 && queueRef.current.length) {
      const task = queueRef.current.shift()!;
      if (task.controller.signal.aborted) {
        if (tasksRef.current.get(task.item.localId) === task) tasksRef.current.delete(task.item.localId);
        task.done();
        continue;
      }
      runningRef.current += 1;
      void runTask(task);
    }
  }

  function enqueue(item: PendingAttachment) {
    return new Promise<void>((done) => {
      const task = { sessionId, item, controller: new AbortController(), done };
      tasksRef.current.set(item.localId, task);
      queueRef.current.push(task);
      pump();
    });
  }

  async function uploadFiles(files: File[]) {
    if (!files.length) return;
    const current = sessionsRef.current.get(sessionId) ?? EMPTY_ATTACHMENTS;
    const available = Math.max(0, 10 - current.length);
    const accepted = files.slice(0, available);
    if (accepted.length < files.length) {
      noticesRef.current.set(sessionId, `单条消息最多 10 个附件，本次接收 ${accepted.length} 个，另有 ${files.length - accepted.length} 个未添加。`);
    } else {
      noticesRef.current.delete(sessionId);
    }
    // 先同步占用名额，再启动异步预览和上传，避免连续粘贴突破数量限制。
    const staged = accepted.map((file) => ({
      localId: crypto.randomUUID(), file, previewUrl: "", uploading: true,
    }));
    updateSession(sessionId, [...current, ...staged]);
    await Promise.all(staged.map(enqueue));
  }

  function cancelAttachmentUpload(localId: string) {
    tasksRef.current.get(localId)?.controller.abort();
    updateSession(sessionId, (items) => items.map((item) => item.localId === localId
      ? { ...item, uploading: false, error: "已取消上传，可重试" }
      : item));
  }

  async function retryAttachment(localId: string) {
    const item = sessionsRef.current.get(sessionId)?.find((entry) => entry.localId === localId);
    if (!item || item.uploading || item.attachment) return;
    const next = { ...item, uploading: true, error: undefined };
    updateSession(sessionId, (items) => items.map((entry) => entry.localId === localId ? next : entry));
    await enqueue(next);
  }

  function removePendingAttachment(localId: string) {
    tasksRef.current.get(localId)?.controller.abort();
    updateSession(sessionId, (items) => items.filter((item) => {
      if (item.localId !== localId) return true;
      releaseAttachmentPreviewUrl(item.previewUrl);
      previewsRef.current.delete(item.previewUrl);
      return false;
    }));
  }

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      tasksRef.current.forEach((task) => task.controller.abort());
      queueRef.current.splice(0).forEach((task) => task.done());
      previewsRef.current.forEach(releaseAttachmentPreviewUrl);
      previewsRef.current.clear();
    };
  }, []);

  const pendingAttachments = sessionsRef.current.get(sessionId) ?? EMPTY_ATTACHMENTS;
  return {
    pendingAttachments, setPendingAttachments, uploadFiles, retryAttachment,
    cancelAttachmentUpload, removePendingAttachment,
    isUploading: pendingAttachments.some((item) => item.uploading),
    uploadNotice: noticesRef.current.get(sessionId) ?? null,
  };
}
