import {useEffect, useMemo, useState} from "react";
import {useNavigate} from "react-router-dom";
import {propertiesApi} from "../api/client.js";
import PropertyCard from "../components/PropertyCard.jsx"
import PropertyMap from "../components/PropertyMap.jsx";
import StatCard from "../components/StatCard.jsx";

const PAGE_SIZE = 40;

export default function Dashboard() {
    const navigate = useNavigate();

    // Filters are optional now - leaving all three blank searches every
    // property PropIQ has (currently all of Orange County). Filling in a
    // zip/city/county scopes both the list and the map to that area.
    const [filterInputs, setFilterInputs] = useState({zip_code: "", city: "", county: ""});
    const [filters, setFilters] = useState({});

    const [properties, setProperties] = useState([]);
    const [page, setPage] = useState(1);
    const [total, setTotal] = useState(0);
    const [hasNext, setHasNext] = useState(false);
    const [activeId, setActiveId] = useState(null);
    const [loading, setLoading] = useState(true);
    const [loadingMore, setLoadingMore] = useState(false);
    const [error, setError] = useState("");

    // Applying a filter resets pagination and starts a fresh list load.
    useEffect(() => {
        let cancelled = false;
        setLoading(true);
        setError("");
        setPage(1);

        propertiesApi
            .search({...filters, page: 1, page_size: PAGE_SIZE, include_analysis: true})
            .then((res) => {
                if (cancelled) return;
                setProperties(res.data.items ?? []);
                setTotal(res.data.total ?? 0);
                setHasNext(res.data.has_next ?? false);
            })
            .catch(() => {
                if (!cancelled) setError("Couldn't load properties for that filter.");
            })
            .finally(() => !cancelled && setLoading(false));

        return () => {
            cancelled = true;
        };
    }, [filters]);

    function loadMore() {
        const nextPage = page + 1;
        setLoadingMore(true);
        propertiesApi
            .search({...filters, page: nextPage, page_size: PAGE_SIZE, include_analysis: true})
            .then((res) => {
                setProperties((prev) => [...prev, ...(res.data.items ?? [])]);
                setHasNext(res.data.has_next ?? false);
                setPage(nextPage);
            })
            .catch(() => setError("Couldn't load more properties."))
            .finally(() => setLoadingMore(false));
    }

    function applyFilters(e) {
        e.preventDefault();
        const cleaned = Object.fromEntries(
            Object.entries(filterInputs).filter(([, v]) => v.trim() !== "")
        );
        setFilters(cleaned);
    }

    useEffect(() => {
        if (activeId == null) return;
        document
            .getElementById(`property-card-${activeId}`)
            ?.scrollIntoView({behavior: "smooth", block: "nearest"});
    }, [activeId]);

    const stats = useMemo(() => {
        if (!properties.length) return null;
        const avgLift =
            properties.reduce((sum, p) => sum + (p.value_delta_pct ?? 0), 0) /
            properties.length;
        const totalValue = properties.reduce(
            (sum, p) => sum + (p.predicted_value ?? 0),
            0
        );
        return {count: properties.length, avgLift, totalValue};
    }, [properties]);

    // A map pin belonging to a property outside the currently loaded list
    // page still opens fine - it just goes straight to the detail page,
    // same as clicking a card does.
    function handleMapSelect(id) {
        setActiveId(id);
        navigate(`/properties/${id}`);
    }

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
                    <form onSubmit={applyFilters} className="flex items-center gap-2">
                        <input
                            className="field-input w-28"
                            value={filterInputs.zip_code}
                            onChange={(e) =>
                                setFilterInputs((f) => ({...f, zip_code: e.target.value}))
                            }
                            placeholder="Zip code"
                        />
                        <input
                            className="field-input w-32"
                            value={filterInputs.city}
                            onChange={(e) => setFilterInputs((f) => ({...f, city: e.target.value}))}
                            placeholder="City"
                        />
                        <input
                            className="field-input w-32"
                            value={filterInputs.county}
                            onChange={(e) => setFilterInputs((f) => ({...f, county: e.target.value}))}
                            placeholder="County"
                        />
                        <button type="submit" className="btn-primary">
                            Filter
                        </button>
                        {Object.keys(filters).length > 0 && (
                            <button
                                type="button"
                                className="text-xs font-medium text-ink/50 hover:text-ink"
                                onClick={() => {
                                    setFilterInputs({zip_code: "", city: "", county: ""});
                                    setFilters({});
                                }}
                            >
                                Clear
                            </button>
                        )}
                    </form>
                </div>

                {stats && (
                    <div className="mt-5 grid grid-cols-3 gap-3">
                        <StatCard
                            label="Properties tracked"
                            value={`${stats.count.toLocaleString()} of ${total.toLocaleString()}`}
                        />
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
                            No properties found for that filter.
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

                    {!loading && hasNext && (
                        <button
                            onClick={loadMore}
                            disabled={loadingMore}
                            className="mt-4 w-full rounded-lg border border-line bg-white/60 py-2.5 text-sm font-medium text-ink/70 hover:border-terracotta/40 disabled:opacity-50"
                        >
                            {loadingMore ? "Loading..." : `Load more (${total - properties.length} remaining)`}
                        </button>
                    )}
                </div>

                <div className="flex-1 p-4">
                    <PropertyMap
                        filters={filters}
                        activeId={activeId}
                        onSelect={handleMapSelect}
                    />
                </div>
            </div>
        </div>
    );
}