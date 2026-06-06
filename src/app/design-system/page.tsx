import { notFound } from "next/navigation";
import { DesignSystemPreview } from "./preview";

export default function DesignSystemPage() {
  if (process.env.NODE_ENV !== "development") {
    notFound();
  }

  return <DesignSystemPreview />;
}
