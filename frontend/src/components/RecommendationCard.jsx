import DeltaChip from "./DeltaChip.jsx";

const currency = (n) =>
    new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
        maximumFractionDigits: 0,
    }).format(n);

export default function RecommendationCard({rec, onAddToProject}) {
    const {title, rationale, est_cost: cost, value_lift_pct: lift, confidence} = rec;

    return (
        <div className="panel flex flex-col gap-3 p-4">
            <div className="flex items-start justify-between gap-3">
                <h4 className="font-display text-base font-semibold text-ink">
                    {title}
                </h4>
                <DeltaChip value={lift} />
            </div>

            <p className="text-sm leading-relaxed text-ink/65">{rationale}</p>

            <div className="mt-1 flex items-center justify-between border-t border-line pt-3">
                <div className="flex gap-4 font-mono text-xs text-ink/50">
                    <span>Est. cost {currency(cost)}</span>
                    <span>Confidence {(confidence * 100).toFixed(0)}%</span>
                </div>
                <button
                    onClick={() => onAddToProject?.(rec)}
                    className="text-xs font-semibold text-terracotta hover:text-terracotta-dark"
                >
                    Add to project →
                </button>
            </div>
        </div>
    );
}