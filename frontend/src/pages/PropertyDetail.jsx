import {useEffect, useState} from "react";
import {useParams, Link} from "react-router-dom";
import {propertiesApi} from "../api/client.js";
import DeltaChip from "../components/DeltaChip.jsx";
import ValueTrendChart from "../components/ValueTrendChart.jsx";
import RecommendationCard from "../components/RecommendationCard.jsx";

const currency = (n) =>
    n == null
        ? "-"
        : new Intl.NumberFormat("en-US", {
            style: "currency",
            currency: "USD",
            maximumFractionDigits: 0,
        }).format(n);

export default function PropertyDetail() {
    const {id} = useParams();
    const [property, setProperty] = useState(null);
    const [valuation, setValuation] = useState(null);
    const [analysis, setAnalysis] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");

    useEffect(() => {
        let cancelled = false;
        setLoading(true);
        setError("");

        Promise.all([
            propertiesApi.get(id),
            propertiesApi.valuation(id),
            propertiesApi.analysis(id),
        ])
            .then(([propRes, valRes, anaRes]) => {
                if(cancelled) return;
                setProperty(propRes.data);
                setValuation(valRes.data);
                setAnalysis(anaRes.data);
            })
            .catch(() => !cancelled && setError("Couldn't load this property."))
            .finally(() => !cancelled && setLoading(false));

        return () => {
            cancelled = true;
        };
    }, [id]);

    if(loading) return <div className="p-8 text-sm text-ink/45">Loading...</div>;
    if(error) return <div className="p-8 text-sm text-clay">{error}</div>;
    if(!property) return null;

    return (
        <div className="mx-auto max-w-5xl px-8 py-8">
            <Link to="/" className="text-sm font-medium text-ink/50 hover:text-ink">
                ← All properties
            </Link>

            <div className="mt-3 flex items-start justify-between gap-4">
                <div>
                    <h1 className="font-display text-3xl font-semibold">
                        {property.address}
                    </h1>
                    <p className="mt-1 text-sm text-ink/55">
                        {property.zip_code} · {property.property_type?.replace("_", " ")} · {" "}
                        {property.lot_sqft?.toLocaleString()} sqft lot
                    </p>
                </div>
                {valuation && (
                    <DeltaChip
                        value={
                            valuation.value_vs_list && valuation.list_price
                                ? (valuation.value_vs_list / valuation.list_price) * 100
                                : 0
                        }
                    />
                )}
            </div>
            <div className="mt-6 grid grid-cols-3 gap-4">
                <div className="panel p-5">
                    <p className="text-xs font-semibold uppercase tracking-wide text-ink/45">
                        Current estimate
                    </p>
                    <p className="mt-1 font-mono text-2xl font-medium">
                        {currency(valuation?.estimated_value)}
                    </p>
                </div>
                <div className="panel p-5">
                    <p className="text-xs font-semibold uppercase tracking-wide text-ink/45">
                        Model confidence
                    </p>
                    <p className="mt-1 font-mono text-2xl font-medium">
                        {valuation?.confidence
                            ? `${(valuation.confidence * 100).toFixed(0)}%`
                            : "-"}
                    </p>
                </div>
            </div>

            <section className="mt-8">
                <h2 className="mb-3 font-display text-xl font-semibold">
                    Value forecast
                </h2>
                <div className="panel p-5">
                    <ValueTrendChart
                        history={valuation?.history ?? []}
                        forecast={valuation?.forecast ?? []}
                    />
                    <div className="mt-2 flex gap-5 font-mono text-xs text-ink/50">
                        <span className="flex items-center gap-1.5">
                            <span className="h-0.5 w-4 bg-terracotta"/> Historical
                        </span>
                        <span className="flex items-center gap-1.5">
                            <span className="h-0.5 w-4 border-t-2 border-dashed border-marine"/> {" "}
                            LSTM forecast
                        </span>
                    </div>
                </div>
            </section>

            {analysis?.summary && (
                <section className="mt-8">
                    <h2 className="mb-3 font-display text-xl font-semibold">
                        Deal analysis
                    </h2>
                    <div className="panel p-5 text-sm leading-relaxed text-ink/70">
                        {analysis.summary}
                    </div>
                </section>
            )}

            <section className="mt-8 mb-4">
                <h2 className="mb-3 font-display text-xl font-semibold">
                    Recommended improvements
                </h2>
                <div className="grid grid-cols-2 gap-4">
                    {(analysis?.recommendations ?? []).map((rec, i) => (
                        <RecommendationCard key={i} rec={rec}/>
                    ))}
                    {(!analysis?.recommendations || analysis.recommendations.length === 0) && (
                        <p className="text-sm text-ink/45">
                            No recommendations available for this property yet.
                        </p>
                    )}
                </div>
            </section>
        </div>
    );
}