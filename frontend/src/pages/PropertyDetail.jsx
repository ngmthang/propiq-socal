import {useEffect, useMemo, useState} from "react";
import {useParams, Link} from "react-router-dom";
import {propertiesApi, marketApi} from "../api/client.js";
import {projectsApi} from "../api/client.js";
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

const DATA_SOURCE_LABELS = {
    seed_synthetic: "Synthetic demo data",
    oc_parcel_gis: "Real OC county parcel",
};

function DetailField({label, value}) {
    return (
        <div>
            <p className="text-xs text-ink/45">{label}</p>
            <p className="mt-0.5 font-medium text-ink/80">
                {value === null || value === undefined || value === "" ? "—" : value}
            </p>
        </div>
    );
}

export default function PropertyDetail() {
    const { id } = useParams();
    const [property, setProperty] = useState(null);
    const [valuation, setValuation] = useState(null);
    const [analysis, setAnalysis] = useState(null);
    const [marketTrend, setMarketTrend] = useState(null);
    // Set when the API returns 422: this property lacks the physical data
    // (sqft/bathrooms/...) the AVM needs. Holds the missing-field list.
    const [valuationUnavailable, setValuationUnavailable] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");

    const handleAddToProject = async (rec) => {
        try {
            await projectsApi.createFromRecommendation({
                property_id: property.id,
                rec_type: rec.type,
                title: rec.title,
                rationale: rec.rationale,
                est_cost: rec.est_cost,
                value_lift_pct: rec.value_lift_pct,
                method: rec.method,
            });
            // TODO: real toast/feedback - alert() as a placeholder only
            alert(`Added "${rec.title}" to your project board.`);
        } catch (err) {
            alert("Couldn't add to project - please try again.");
        }
    };

    useEffect(() => {
        let cancelled = false;
        setLoading(true);
        setError("");
        setValuationUnavailable(null);

        // The property fetch is the only hard requirement. Valuation and
        // AI analysis fail soft: county parcel records (442k oc_parcel_gis
        // rows) legitimately can't be valued, and a 422 there must not
        // block the page - before this split, ANY parcel detail page
        // showed "Couldn't load this property."
        const valuationReq = propertiesApi.valuation(id).catch((err) => {
            if (err?.response?.status === 422) {
                const detail = err.response.data?.detail;
                return {unavailable: detail?.missing_fields ?? []};
            }
            return null; // other valuation errors: just omit the panel data
        });
        const analysisReq = propertiesApi.analysis(id).catch(() => null);

        Promise.all([propertiesApi.get(id), valuationReq, analysisReq])
            .then(([propRes, valRes, anaRes]) => {
                if(cancelled) return;
                setProperty(propRes.data);
                if (valRes?.unavailable) {
                    setValuationUnavailable(valRes.unavailable);
                } else {
                    setValuation(valRes?.data ?? null);
                }
                setAnalysis(anaRes?.data ?? null);
            })
            .catch(() => !cancelled && setError("Couldn't load this property."))
            .finally(() => !cancelled && setLoading(false));

        return () => {
            cancelled = true;
        };
    }, [id]);

    // Separate effect: the zip isn't known until the property itself has
    // loaded, and this failing shouldn't block anything else on the page -
    // the chart just renders empty if it's unavailable.
    useEffect(() => {
        if (!property?.zip_code) return;
        let cancelled = false;
        marketApi
            .trend(property.zip_code)
            .then((res) => !cancelled && setMarketTrend(res.data))
            .catch(() => !cancelled && setMarketTrend(null));
        return () => {
            cancelled = true;
        };
    }, [property?.zip_code]);

    // The LSTM only ever outputs three horizon percentages (3/6/12mo), not
    // a full monthly curve - project those onto points anchored to the
    // last known historical month so the chart has something to draw a
    // forecast line/band through. Confidence bands widen with horizon
    // length since further-out forecasts are inherently less certain.
    const chartHistory = useMemo(
        () =>
            (marketTrend?.historical_median_price ?? [])
                .filter((pt) => pt.median_price != null)
                .map((pt) => ({date: `${pt.month}-01`, value: pt.median_price})),
        [marketTrend]
    );

    const forecastHorizons = marketTrend
        ? [marketTrend.forecast_3mo, marketTrend.forecast_6mo, marketTrend.forecast_12mo]
        : [];
    // Distinguish "no model trained yet" from "model ran but returned
    // null" (e.g. a NaN prediction) - both need a message instead of a
    // chart, but they're different problems worth telling apart.
    const forecastUnavailableReason =
        !marketTrend || marketTrend.model_version === "unavailable"
            ? "not-trained"
            : forecastHorizons.some((v) => v == null)
            ? "prediction-failed"
            : null;

    const chartForecast = useMemo(() => {
        if (forecastUnavailableReason || !chartHistory.length) return [];
        const base = chartHistory[chartHistory.length - 1].value;
        const lastDate = new Date(chartHistory[chartHistory.length - 1].date);
        const horizons = [
            {months: 3, pct: marketTrend.forecast_3mo, band: 0.03},
            {months: 6, pct: marketTrend.forecast_6mo, band: 0.05},
            {months: 12, pct: marketTrend.forecast_12mo, band: 0.08},
        ];
        return horizons.map(({months, pct, band}) => {
            const date = new Date(lastDate);
            date.setMonth(date.getMonth() + months);
            const value = base * (1 + pct / 100);
            return {date, value, lower: value * (1 - band), upper: value * (1 + band)};
        });
    }, [marketTrend, chartHistory, forecastUnavailableReason]);

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
                        {property.lot_sqft ? Math.round(property.lot_sqft).toLocaleString() : "—"} sqft lot
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
                    {valuationUnavailable ? (
                        <>
                            <p className="mt-1 text-sm font-medium text-ink/60">
                                Valuation unavailable
                            </p>
                            <p className="mt-1 text-xs text-ink/40">
                                This county parcel record is missing{" "}
                                {valuationUnavailable
                                    .map((f) => f.replace(/_/g, " "))
                                    .join(", ")}{" "}
                                — not enough data for a reliable estimate.
                            </p>
                        </>
                    ) : (
                        <>
                            <p className="mt-1 font-mono text-2xl font-medium">
                                {currency(valuation?.estimated_value)}
                            </p>
                            <p className="mt-1 text-xs text-ink/40">
                                AI-generated estimate, not an appraisal
                            </p>
                        </>
                    )}
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
                    Property details
                </h2>
                <div className="panel grid grid-cols-2 gap-x-8 gap-y-4 p-5 text-sm sm:grid-cols-3 lg:grid-cols-4">
                    <DetailField label="Type" value={property.property_type?.replace(/_/g, " ")} />
                    <DetailField label="Zoning" value={property.zoning} />
                    <DetailField label="County" value={property.county} />
                    <DetailField label="Neighborhood" value={property.neighborhood_name} />

                    <DetailField label="Beds" value={property.beds} />
                    <DetailField label="Baths" value={property.baths} />
                    <DetailField
                        label="Building sqft"
                        value={property.building_sqft ? Math.round(property.building_sqft).toLocaleString() : null}
                    />
                    <DetailField
                        label="Lot sqft"
                        value={property.lot_sqft ? Math.round(property.lot_sqft).toLocaleString() : null}
                    />

                    <DetailField label="Year built" value={property.year_built} />
                    <DetailField label="Stories" value={property.stories} />
                    <DetailField label="Garage spaces" value={property.garage_spaces} />
                    <DetailField label="Pool" value={property.pool == null ? null : property.pool ? "Yes" : "No"} />

                    <DetailField label="List price" value={property.list_price != null ? currency(property.list_price) : null} />
                    <DetailField label="Last sale price" value={property.last_sale_price != null ? currency(property.last_sale_price) : null} />
                    <DetailField
                        label="Last sale date"
                        value={property.last_sold_date ? new Date(property.last_sold_date).toLocaleDateString() : null}
                    />
                    <DetailField label="Assessed value" value={property.assessed_value != null ? currency(property.assessed_value) : null} />

                    <DetailField label="Walk score" value={property.walk_score} />
                    <DetailField label="Transit score" value={property.transit_score} />
                    <DetailField label="School rating" value={property.school_rating} />
                    <DetailField
                        label="Distance to downtown"
                        value={property.distance_to_downtown_mi != null ? `${property.distance_to_downtown_mi.toFixed(1)} mi` : null}
                    />

                    <DetailField label="Flood zone" value={property.flood_zone} />
                    <DetailField label="Fire hazard zone" value={property.fire_hazard_zone} />
                    <DetailField
                        label="Data source"
                        value={DATA_SOURCE_LABELS[property.data_source] ?? property.data_source}
                    />
                    <DetailField
                        label="Last updated"
                        value={property.updated_at ? new Date(property.updated_at).toLocaleDateString() : null}
                    />
                </div>
            </section>

            <section className="mt-8">
                <h2 className="mb-3 font-display text-xl font-semibold">
                    Value forecast
                </h2>
                <div className="panel p-5">
                    {forecastUnavailableReason === "not-trained" ? (
                        <p className="py-8 text-center text-sm text-ink/45">
                            Forecast unavailable for {property.zip_code} yet - the LSTM market
                            model hasn't been trained.
                        </p>
                    ) : forecastUnavailableReason === "prediction-failed" ? (
                        <p className="py-8 text-center text-sm text-ink/45">
                            The forecast model couldn't produce a prediction for{" "}
                            {property.zip_code} right now - showing historical prices only.
                        </p>
                    ) : null}
                    <ValueTrendChart history={chartHistory} forecast={chartForecast} />
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
                        <RecommendationCard key={i} rec={rec} onAddToProject={handleAddToProject}/>
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