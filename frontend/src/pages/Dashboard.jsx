import {useEffect, useMemo, useState} from "react";
import {propertiesApi} from "../api/client.js";
import PropertyCard from "../components/PropertyCard.jsx"
import PropertyMap from "../components/PropertyMap.jsx";
import StatCard from "../components/StatCard.jsx";

export default function Dashboard() {
    const [zip, setZip] = useState("90210");
    const [properties, setProperties] = useState([]);
    const [activeId, setActiveId] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");

    useEffect(() => {
        let cancelled = false;
        setLoading(true);
        setError("");

        propertiesApi
            .search({zip_code: zip, page_size: 40, include_analysis: true})
            .then((res) => {
                if(cancelled) return;
                setProperties(res.data.items ?? []);
            })
            .catch(() => {
                if(!cancelled) setError("Couldn't load properties for that zip code.");
            })
            .finally(() => !cancelled && setLoading(false));

        return () => {
            cancelled = true;
        };
    }, [zip]);

    useEffect(() => {
        if (activeId == null) return;
        document
            .getElementById(`property-card-${activeId}`)
            ?.scrollIntoView({behavior: "smooth", block: "nearest"});
    }, [activeId]);
    const stats = useMemo(() => {
        if(!properties.length) return null;
        const avgLift =
            properties.reduce((sum, p) => sum + (p.value_delta_pct ?? 0), 0) /
            properties.length;
        const totalValue = properties.reduce(
            (sum, p) => sum + (p.predicted_value ?? 0),
            0
        );
        return {count: properties.length, avgLift, totalValue};
    }, [properties]);

    return (
        <div className="flex h-screen flex-col">
            <header className="border-b border-line bg-parchment/80 px-8 py-5">
                <div className="flex items-center justify-between gap-4">
                    <div>
                        <h1 className="font-display text-2xl font-semibold">Properties</h1>
                        <p className="text-sm text-ink/55">
                            Predicted values and improvement opportunities across SoCal.
                        </p>
                    </div>
                    <form
                        onSubmit={(e) => e.preventDefault()}
                        className="flex items-center gap-2"
                    >
                        <input
                            className="field-input w-36"
                            value={zip}
                            onChange={(e) => setZip(e.target.value)}
                            placeholder="Zip code"
                        />
                    </form>
                </div>

                {stats && (
                    <div className="mt-5 grid grid-cols-3 gap-3">
                        <StatCard label="Properties tracked" value={stats.count}/>
                        <StatCard
                            label="Avg. predicted lift"
                            value={`${stats.avgLift >= 0 ? "+" : ""}${stats.avgLift.toFixed(1)}%`}
                        />
                        <StatCard
                            label="Portfolio value (predicted)"
                            value={new Intl.NumberFormat("en-US", {
                                style: "currency",
                                currency: "USD",
                                notation: "compact",
                            }).format(stats.totalValue)}
                        />
                    </div>
                )}
            </header>

            <div className="flex flex-1 overflow-hidden">
                <div className="w-[420px] shrink-0 overflow-y-auto border-y border-line px-6 py-5">
                    {loading && (
                        <p className="py-8 text-center text-sm text-ink/45">Loading...</p>
                    )}
                    {error && <p className="py-8 text-center text-sm text-clay">{error}</p>}
                    {!loading && !error && properties.length === 0 && (
                        <p className="py-8 text-center text-sm text-ink/45">
                            No properties found for {zip}.
                        </p>
                    )}

                    <div className="space-y-3">
                        {properties.map((p) => (
                            <PropertyCard
                                key={p.id}
                                property={p}
                                active={p.id === activeId}
                                onHover={setActiveId}
                            />
                        ))}
                    </div>
                </div>

                <div className="flex-1 p-4">
                    <PropertyMap
                        properties={properties}
                        activeId={activeId}
                        onSelect={setActiveId}
                    />
                </div>
            </div>
        </div>
    );
}