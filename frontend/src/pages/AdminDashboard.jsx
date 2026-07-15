import {useEffect, useState} from "react";
import StatCard from "../components/StatCard.jsx";

export default function AdminDashboard() {
    const [health, setHealth] = useState(null);

    useEffect(() => {
        // Layer 3's /health lives at the API root, not under /api - call it
        // directly rather than through the /api-prefixed client instance.
        fetch("/health")
            .then((res) => (res.ok ? res.json() : Promise.reject()))
            .then(setHealth)
            .catch(() => setHealth(null));
    }, []);

    return (
        <div className="px-8 py-8">
            <h1 className="font-display text-2xl font-semibold">Admin</h1>
            <p className="mt-1 text-sm text-ink/55">
                System health, model status, and data pipeline visibility.
            </p>

            <div className="mt-6 grid grid-cols-4 gap-4">
                <StatCard
                    label="API status"
                    value={health?.status === "ok" ? "Healthy" : "Unknown"}
                />
                <StatCard
                    label="Inference engine"
                    value={health?.engine_loaded ? "Loaded" : "-"}
                />
                <StatCard
                    label="Scheduler"
                    value={health?.scheduler_running ? "Running" : "Stopped"}
                />
                <StatCard label="API version" value={health?.version ?? "-"}/>
            </div>

            <div className="mt-8 grid grid-cols-2 gap-4">
                <div className="panel p-5">
                    <h2 className="font-display text-lg font-semibold">Data pipeline</h2>
                    <p className="mt-1 text-sm text-ink/60">
                        Full sync runs Sundays 2 AM PT. Incremental sync daily 6 AM PT.
                        Feature recompute every 6 hours.
                    </p>
                    <p className="mt-3 text-xs text-ink/40">
                        Wire this panel to a real{" "}
                        <code className="font-mono">/admin/pipeline/status</code> endpoint
                        once Layer 3 exposes scheduler job history.
                    </p>
                </div>

                <div className="panel p-5">
                    <h2 className="font-display text-lg font-semibold">Model registry</h2>
                    <p className="mt-1 text-sm text-ink/60">
                        XGBoost AVM + LSTM forecaster tracked via MLflow. Retraining
                        triggered on schedule from Layer 2.
                    </p>
                    <p className="mt-3 text-xs text-ink/40">
                        Wire this panel to a real{" "}
                        <code className="font-mono">/admin/models</code> endpoint to
                        surface live metrics, MAE, and last-trained timestamps.
                    </p>
                </div>
            </div>
        </div>
    );
}