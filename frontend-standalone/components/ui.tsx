"use client";

import { X } from "@phosphor-icons/react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useEffect } from "react";
import clsx from "clsx";

export const Button = ({ className, variant = "primary", ...props }: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "ghost" | "danger" }) => (
  <button className={clsx("button", `button-${variant}`, className)} {...props} />
);

export const Ticket = ({ className, children }: { className?: string; children: React.ReactNode }) => (
  <div className={clsx("ticket", className)}>{children}</div>
);

export function Modal({ open, title, onClose, children, wide = false }: { open: boolean; title: string; onClose: () => void; children: React.ReactNode; wide?: boolean }) {
  const reduce = useReducedMotion();
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  return (
    <AnimatePresence>
      {open && (
        <motion.div className="modal-backdrop" initial={reduce ? false : { opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onMouseDown={onClose}>
          <motion.section role="dialog" aria-modal="true" aria-labelledby="modal-title" className={clsx("modal-sheet", wide && "modal-wide")} initial={reduce ? false : { opacity: 0, y: 28, scale: .98 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: 18, scale: .98 }} transition={{ type: "spring", stiffness: 260, damping: 26 }} onMouseDown={(event) => event.stopPropagation()}>
            <header className="modal-header"><h2 id="modal-title">{title}</h2><button className="icon-button" onClick={onClose} aria-label="关闭"><X size={20} /></button></header>
            <div className="modal-body">{children}</div>
          </motion.section>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

export function Segmented<T extends string>({ value, options, onChange, ariaLabel }: { value: T; options: { value: T; label: string }[]; onChange: (value: T) => void; ariaLabel: string }) {
  return <div className="segmented" role="group" aria-label={ariaLabel}>{options.map((option) => <button key={option.value} type="button" className={value === option.value ? "active" : ""} onClick={() => onChange(option.value)}>{option.label}</button>)}</div>;
}

export function Field({ label, helper, error, children }: { label: string; helper?: string; error?: string; children: React.ReactNode }) {
  return <label className="field"><span className="field-label">{label}</span>{children}{helper && <span className="field-helper">{helper}</span>}{error && <span className="field-error">{error}</span>}</label>;
}
