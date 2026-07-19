import {Link} from "react-router-dom";
import DeltaChip from "./DeltaChip.jsx";

const currency = (n) =>
    n == null
        ? "-"
        : new Intl.NumberFormat("en-US", {
            style: "currency",
            currency: "USD",
            maximumFractionDigits: 0,
        }).format(n);

export default function PropertyCard({property, active, onHover}) {
    const {
        id,
        address,
        zip_code: zip,
        property_type: type,
        predicted_value: predictedValue,
        value_delta_pct: delta,
    } = property;

    return (
        <Link
            id={`property-card-${id}`}
            to={`/properties/${id}`}
            onMouseEnter={() => onHover?.(id)}
            className={`block rounded-xl border p-4 transition-colors ${
                active
                    ? "border-terracotta bg-terracotta/5"
                    : "border-line bg-white/60 hover:border-terracotta/40"
            }`}
        >
            <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                    <p className="truncate font-medium text-ink">{address}</p>
                    <p className="text-xs text-ink/50">
                        {zip} · {type?.replace("_", " ")}
                    </p>
                </div>
                {delta != null && <DeltaChip value={delta} />}
            </div>

            <div className="mt-3 flex items-baseline gap-1.5">
                <span className="font-mono text-lg font-medium text-ink">
                    {currency(predictedValue)}
                </span>
                <span className="text-xs text-ink/40">predicted value</span>
            </div>
        </Link>
    );
}