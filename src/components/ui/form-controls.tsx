import type {
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
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
