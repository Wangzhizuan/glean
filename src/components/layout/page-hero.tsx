import type { ReactNode } from "react";

export function PageHero({
  action,
  description,
  eyebrow,
  title,
}: {
  action?: ReactNode;
  description: string;
  eyebrow: string;
  title: string;
}) {
  return (
    <div className="page-hero">
      <div>
        <span className="eyebrow">{eyebrow}</span>
        <h1>{title}</h1>
        <p className="lead">{description}</p>
      </div>
      {action}
    </div>
  );
}
