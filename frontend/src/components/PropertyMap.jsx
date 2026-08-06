import {useCallback, useEffect, useRef, useState} from "react";
import mapboxgl from "mapbox-gl";
import "mapbox-gl/dist/mapbox-gl.css";
import {propertiesApi} from "../api/client.js";

// Set VITE_MAPBOX_TOKEN in .env - see .env.example
mapboxgl.accessToken = import.meta.env.VITE_MAPBOX_TOKEN || "";

const SOURCE_ID = "properties";
const CLUSTER_MAX_ZOOM = 14;
const CLUSTER_RADIUS = 50;
const MOVE_DEBOUNCE_MS = 300;

// At Orange-County scale (470k+ parcels), rendering one mapboxgl.Marker DOM
// element per property is a non-starter. Points are fed into a clustered
// GeoJSON source instead, so Mapbox GL's own WebGL layer draws them - and
// only the current viewport is ever fetched, via the /search/map endpoint.
function pinsToGeoJSON(pins) {
    return {
        type: "FeatureCollection",
        features: pins.map((p) => ({
            type: "Feature",
            id: p.id,
            geometry: {type: "Point", coordinates: [p.longitude, p.latitude]},
            properties: {
                id: p.id,
                display_value: p.display_value,
                value_type: p.value_type,
                zip_code: p.zip_code,
            },
        })),
    };
}

export default function PropertyMap({filters, activeId, onSelect}) {
    const containerRef = useRef(null);
    const mapRef = useRef(null);
    const activeFeatureIdRef = useRef(null);
    const filtersRef = useRef(filters);
    const abortControllerRef = useRef(null);
    const [status, setStatus] = useState({loading: true, shown: 0, total: 0});

    filtersRef.current = filters;

    const fetchPinsForViewport = useCallback(() => {
        const map = mapRef.current;
        if (!map || !map.getSource(SOURCE_ID)) return;

        // A slow response for a viewport you've since panned/zoomed away
        // from must never be allowed to land after a faster response for
        // where you're actually looking now - cancel it outright rather
        // than relying on response order.
        abortControllerRef.current?.abort();
        const controller = new AbortController();
        abortControllerRef.current = controller;

        const bounds = map.getBounds();
        setStatus((s) => ({...s, loading: true}));

        propertiesApi
            .mapPins(
                {
                    min_lat: bounds.getSouth(),
                    max_lat: bounds.getNorth(),
                    min_lng: bounds.getWest(),
                    max_lng: bounds.getEast(),
                    ...filtersRef.current,
                },
                controller.signal
            )
            .then((res) => {
                const source = map.getSource(SOURCE_ID);
                if (!source) return;
                source.setData(pinsToGeoJSON(res.data.items));
                setStatus({
                    loading: false,
                    shown: res.data.items.length,
                    total: res.data.total_in_bounds,
                });
            })
            .catch((err) => {
                if (err.code === "ERR_CANCELED") return; // superseded - the newer request owns the UI now
                setStatus((s) => ({...s, loading: false}));
            });
    }, []);

    // Map + layers: created once.
    useEffect(() => {
        if (mapRef.current || !containerRef.current || !mapboxgl.accessToken) return;

        const map = new mapboxgl.Map({
            container: containerRef.current,
            style: "mapbox://styles/mapbox/light-v11",
            center: [-117.86, 33.7], // Orange County default
            zoom: 10,
        });
        mapRef.current = map;
        map.addControl(new mapboxgl.NavigationControl({showCompass: false}), "top-right");

        map.on("load", () => {
            map.addSource(SOURCE_ID, {
                type: "geojson",
                data: {type: "FeatureCollection", features: []},
                cluster: true,
                clusterMaxZoom: CLUSTER_MAX_ZOOM,
                clusterRadius: CLUSTER_RADIUS,
            });

            map.addLayer({
                id: "clusters",
                type: "circle",
                source: SOURCE_ID,
                filter: ["has", "point_count"],
                paint: {
                    "circle-color": [
                        "step", ["get", "point_count"],
                        "#2B5C63", 25,
                        "#A6461F", 100,
                        "#B23B2E",
                    ],
                    "circle-radius": ["step", ["get", "point_count"], 16, 25, 22, 100, 28],
                    "circle-stroke-width": 2,
                    "circle-stroke-color": "#F6F1E4",
                },
            });

            map.addLayer({
                id: "cluster-count",
                type: "symbol",
                source: SOURCE_ID,
                filter: ["has", "point_count"],
                layout: {
                    "text-field": ["get", "point_count_abbreviated"],
                    "text-font": ["DIN Pro Medium", "Arial Unicode MS Bold"],
                    "text-size": 12,
                },
                paint: {"text-color": "#F6F1E4"},
            });

            map.addLayer({
                id: "unclustered-point",
                type: "circle",
                source: SOURCE_ID,
                filter: ["!", ["has", "point_count"]],
                paint: {
                    "circle-radius": [
                        "case", ["boolean", ["feature-state", "active"], false], 9, 6,
                    ],
                    "circle-color": [
                        "match", ["get", "value_type"],
                        "listed", "#A6461F",
                        "sold", "#6B7A56",
                        "estimated", "#2B5C63",
                        /* unpriced, real parcels without a value yet */ "#9C9282",
                    ],
                    "circle-stroke-width": 2,
                    "circle-stroke-color": "#F6F1E4",
                },
            });

            map.on("click", "clusters", (e) => {
                const [feature] = map.queryRenderedFeatures(e.point, {layers: ["clusters"]});
                if (!feature) return;
                map
                    .getSource(SOURCE_ID)
                    .getClusterExpansionZoom(feature.properties.cluster_id, (err, zoom) => {
                        if (err) return;
                        map.easeTo({center: feature.geometry.coordinates, zoom});
                    });
            });

            map.on("click", "unclustered-point", (e) => {
                const feature = e.features?.[0];
                if (feature) onSelect?.(feature.properties.id);
            });

            map.on("mouseenter", "clusters", () => (map.getCanvas().style.cursor = "pointer"));
            map.on("mouseleave", "clusters", () => (map.getCanvas().style.cursor = ""));
            map.on("mouseenter", "unclustered-point", () => (map.getCanvas().style.cursor = "pointer"));
            map.on("mouseleave", "unclustered-point", () => (map.getCanvas().style.cursor = ""));

            fetchPinsForViewport();
        });

        let debounceTimer = null;
        const onMoveEnd = () => {
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(fetchPinsForViewport, MOVE_DEBOUNCE_MS);
        };
        map.on("moveend", onMoveEnd);

        return () => {
            clearTimeout(debounceTimer);
            abortControllerRef.current?.abort();
            map.remove();
            mapRef.current = null;
        };
    }, [fetchPinsForViewport, onSelect]);

    // Filters (zip/city/county) changed - re-fetch the current viewport.
    useEffect(() => {
        if (mapRef.current?.isStyleLoaded()) fetchPinsForViewport();
    }, [filters, fetchPinsForViewport]);

    // Highlight the active pin (from hovering the list) via feature-state
    // instead of re-rendering the whole layer.
    useEffect(() => {
        const map = mapRef.current;
        if (!map || !map.getSource(SOURCE_ID)) return;

        if (activeFeatureIdRef.current != null) {
            map.setFeatureState({source: SOURCE_ID, id: activeFeatureIdRef.current}, {active: false});
        }
        if (activeId != null) {
            map.setFeatureState({source: SOURCE_ID, id: activeId}, {active: true});
            activeFeatureIdRef.current = activeId;
        } else {
            activeFeatureIdRef.current = null;
        }
    }, [activeId]);

    if (!mapboxgl.accessToken) {
        return (
            <div className="flex h-full items-center justify-center rounded-xl border border-dashed border-line bg-white/40 p-6 text-center text-sm text-ink/50">
                Set VITE_MAPBOX_TOKEN in your .env to render the parcel map.
            </div>
        );
    }

    return (
        <div className="relative h-full w-full">
            <div ref={containerRef} className="h-full w-full rounded-xl" />
            <div className="pointer-events-none absolute bottom-3 left-3 rounded-md bg-white/85 px-2.5 py-1 font-mono text-[11px] text-ink/60 shadow-sm">
                {status.loading
                    ? "Loading..."
                    : status.total > status.shown
                    ? `Showing ${status.shown.toLocaleString()} of ${status.total.toLocaleString()} in view — zoom in for more`
                    : `${status.shown.toLocaleString()} in view`}
            </div>
        </div>
    );
}