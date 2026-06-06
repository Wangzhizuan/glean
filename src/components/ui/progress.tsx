export function Progress({ value }: { value: number }) {
  return (
    <div
      aria-label={`任务进度 ${value}%`}
      aria-valuemax={100}
      aria-valuemin={0}
      aria-valuenow={value}
      className="progress"
      role="progressbar"
    >
      <span
        className="progress__value"
        style={{ "--progress-value": `${value}%` } as React.CSSProperties}
      />
    </div>
  );
}
