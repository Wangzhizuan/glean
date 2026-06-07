import type {
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from "react";
import { cx } from "@/lib/class-names";

export function Input({
  className,
  ...props
}: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cx("input", className)} {...props} />;
}

export function Select({
  children,
  className,
  ...props
}: SelectHTMLAttributes<HTMLSelectElement> & { children: ReactNode }) {
  return (
    <select className={cx("select", className)} {...props}>
      {children}
    </select>
  );
}

export function Textarea({
  className,
  ...props
}: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cx("input textarea", className)} {...props} />;
}

export function CheckboxLine({
  children,
  defaultChecked = true,
}: {
  children: ReactNode;
  defaultChecked?: boolean;
}) {
  return (
    <label className="checkbox-line">
      <input defaultChecked={defaultChecked} type="checkbox" />
      {children}
    </label>
  );
}
