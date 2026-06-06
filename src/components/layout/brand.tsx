import Link from "next/link";

export function Brand() {
  return (
    <Link className="brand" href="/">
      <span className="brand__mark">拾</span>
      拾句
    </Link>
  );
}
