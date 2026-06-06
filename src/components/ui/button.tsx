import Link from "next/link";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { cx } from "@/lib/class-names";

export type ButtonVariant = "primary" | "secondary" | "quiet" | "danger";

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  children: ReactNode;
  className?: string;
  href?: string;
  variant?: ButtonVariant;
};

export function Button({
  children,
  className,
  href,
  type = "button",
  variant = "primary",
  ...props
}: ButtonProps) {
  const classes = cx("button", `button--${variant}`, className);

  if (href) {
    return (
      <Link className={classes} href={href}>
        {children}
      </Link>
    );
  }

  return (
    <button className={classes} type={type} {...props}>
      {children}
    </button>
  );
}
