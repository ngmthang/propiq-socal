export default function DeltaChip({value, suffix = "%"}) {
    const isUp = value >= 0;
    return (
      <span className={`delta-chip ${isUp ? "up" : "down"}`}>
          <span aria-hidden="true">{isUp ? "▲" : "▼"}</span>
          {Math.abs(value).toFixed(1)}
          {suffix}
      </span>
    );
}