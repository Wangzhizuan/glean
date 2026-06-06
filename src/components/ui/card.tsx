import type { HTMLAttributes, ReactNode } from "react";
import { cx } from "@/lib/class-names";

type CardProps = HTMLAttributes<HTMLElement> & {
  as?: "article" | "aside" | "div" | "section";
  children: ReactNode;
  panel?: boolean;
};

export function Card({
  as: Component = "div",
  children,
  className,
  panel = false,
  ...props
}: CardProps) {
  return (
    <Component
      className={cx("card", panel && "card--panel", className)}
      {...props}
    >
      {children}
    </Component>
  );
}
