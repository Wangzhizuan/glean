"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { cx } from "@/lib/class-names";

export function useToast() {
  const [message, setMessage] = useState("");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, []);

  const showToast = useCallback((nextMessage: string) => {
    setMessage(nextMessage);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setMessage(""), 2200);
  }, []);

  return { message, showToast };
}

export function Toast({ message }: { message: string }) {
  return (
    <div
      aria-live="polite"
      className={cx("toast", Boolean(message) && "toast--visible")}
      role="status"
    >
      {message}
    </div>
  );
}
