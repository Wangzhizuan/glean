"use client";

import type { ReactNode } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Brand } from "./brand";

const navItems = [
  { href: "/submit", label: "新建任务", mobileLabel: "新建" },
  { href: "/progress", label: "处理中", mobileLabel: "处理中" },
  { href: "/history", label: "历史记录", mobileLabel: "历史" },
];

function isActive(pathname: string, href: string) {
  if (href === "/history" && pathname === "/detail") return true;
  return pathname === href;
}

export function AppShell({
  action,
  children,
}: {
  action: ReactNode;
  children: ReactNode;
}) {
  const pathname = usePathname();

  return (
    <>
      <header className="topbar">
        <div className="container site-nav">
          <Brand />
          <nav aria-label="主导航" className="nav-links">
            {navItems.map((item) => (
              <Link
                aria-current={
                  isActive(pathname, item.href) ? "page" : undefined
                }
                href={item.href}
                key={item.href}
              >
                {item.label}
              </Link>
            ))}
          </nav>
          {action}
        </div>
      </header>
      <main className="page">{children}</main>
      <nav aria-label="移动端导航" className="mobile-nav">
        {navItems.map((item) => (
          <Link
            aria-current={
              isActive(pathname, item.href) ? "page" : undefined
            }
            href={item.href}
            key={item.href}
          >
            {item.mobileLabel}
          </Link>
        ))}
      </nav>
    </>
  );
}
