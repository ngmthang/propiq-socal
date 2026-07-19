import {useEffect, useRef} from "react";
import mapboxgl from "mapbox-gl";
import "mapbox-gl/dist/mapbox-gl.css";

// Set VITE_MAPBOX_TOKEN in .env - see .env.example
mapboxgl.accessToken = import.meta.env.VITE_MAPBOX_TOKEN || "";

export default function PropertyMap({properties, activeId, onSelect}) {
    const containerRef = useRef(null);
    const mapRef = useRef(null);
    const markersRef = useRef({});

    useEffect(() => {
        if(mapRef.current || !containerRef.current) return;

        mapRef.current = new mapboxgl.Map({
            container: containerRef.current,
            style: "mapbox://styles/mapbox/light-v11",
            center: [-118.35, 34.05], // LA basin default
            zoom: 9.5,
        });
        mapRef.current.addControl(
            new mapboxgl.NavigationControl({showCompass: false}),
            "top-right"
        );

        return () => {
            mapRef.current?.remove();
            mapRef.current = null;
        };
    }, []);

    useEffect(() => {
        const map = mapRef.current;
        if (!map) return;

        Object.values(markersRef.current).forEach((m) => m.remove());
        markersRef.current = {};

        properties.forEach((p) => {
            if(p.latitude == null || p.longitude == null) return;

            const el = document.createElement("button");
            el.setAttribute("aria-label", p.address);
            const isUp = (p.value_delta_pct ?? 0) >= 0;
            el.style.width = "14px";
            el.style.height = "14px";
            el.style.borderRadius = "50%";
            el.style.border = "2px solid #F6F1E4";
            el.style.cursor = "pointer";
            el.style.background = isUp ? "#6B7A56" : "#B23B2E";
            el.style.boxShadow =
                p.id === activeId ? "0 0 0 4px rgba(166, 70, 31, 0.35)" : "none";
            el.onclick = () => onSelect?.(p.id);

            const marker = new mapboxgl.Marker({element: el})
                .setLngLat([p.longitude, p.latitude])
                .addTo(map);
            markersRef.current[p.id] = marker;
        });
    }, [properties, activeId, onSelect]);

    if(!mapboxgl.accessToken) {
        return (
            <div className="flex h-full items-center justify-center rounded-xl border border-dashed border-line bg-white/40 p-6 text-center text-sm text-ink/50">
                Set VITE_MAPBOX_TOKEN in your .env to render the parcel map.
            </div>
        );
    }

    return <div ref={containerRef} className="h-full w-full rounded-xl" />;
}