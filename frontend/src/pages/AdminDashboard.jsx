import {useEffect, useState} from "react";
import StatCard from "../components/StatCard.jsx";
import {adminApi} from "../api/client.js";

const num = (n) => (n == null ? "-" : n.toLocaleString());
const when = (iso) =>
    iso
        ? new Date(iso).toLocaleString("en-US", {
            month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
        })
        : "-";

const statusTone = {
    success: "text-sage",
    running: "text-marine",
    failed: "text-clay",
    partial: "text-clay",
};

export default function AdminDashboard() {
    const [health, setHealth] = useState(null);
    const [pipeline, setPipeline] = useState(null);
    const [models, setModels] = useState(null);
    const [adminError, setAdminError] = useState("");

    useEffect(() => {
        // Layer 3's /health lives at the API root, not under /api - call it
        // directly rather than through the /api-prefixed client instance.
        fetch("/health")
            .then((res) => (res.ok ? res.json() : Promise.reject()))
            .then(setHealth)
            .catch(() => setHealth(null));

        Promise.all([adminApi.pipelineStatus(), adminApi.models()])
            .then(([pipeRes, modelRes]) => {
                setPipeline(pipeRes.data);
                setModels(modelRes.data);
            })
            .catch((err) => {
                setAdminError(
                    err?.response?.status === 403
                        ? "Admin access required to view pipeline and model status."
                        : "Couldn't load admin data."
                );
            });
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
                <StatCard
                    label="Properties tracked"
                    value={num(pipeline?.total_properties)}
                />
            </div>

            {adminError && (
                <p className="mt-6 text-sm text-clay">{adminError}</p>
            )}

            <div className="mt-8 grid grid-cols-2 gap-4">
                <div className="panel p-5">
                    <h2 className="font-display text-lg font-semibold">Data pipeline</h2>
                    <p className="mt-1 text-sm text-ink/60">
                        Full sync runs Sundays 2 AM PT. Incremental sync daily 6 AM PT.
                    </p>

                    {pipeline?.sources && (
                        <div className="mt-4 space-y-2">
                            {Object.entries(pipeline.sources).map(([source, info]) => (
                                <div
                                    key={source}
                                    className="flex items-baseline justify-between border-t border-ink/10 pt-2 text-sm"
                                >
                                    <div>
                                        <span className="font-mono">{source}</span>
                                        <span className="ml-2 text-xs text-ink/45">
                                            {num(info.property_count)} properties
                                        </span>
                                    </div>
                                    <div className="text-right text-xs">
                                        <span className={statusTone[info.last_run.status] ?? "text-ink/60"}>
                                            {info.last_run.status}
                                        </span>
                                        <span className="ml-2 text-ink/45">
                                            {when(info.last_run.completed_at)}
                                        </span>
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}

                    {pipeline?.recent_jobs?.length > 0 && (
                        <div className="mt-4">
                            <p className="text-xs font-semibold uppercase tracking-wide text-ink/45">
                                Recent runs
                            </p>
                            <div className="mt-2 space-y-1 font-mono text-xs text-ink/60">
                                {pipeline.recent_jobs.slice(0, 6).map((j) => (
                                    <div key={j.id} className="flex justify-between">
                                        <span>
                                            #{j.id} {j.source}
                                            <span className={`ml-2 ${statusTone[j.status] ?? ""}`}>
                                                {j.status}
                                            </span>
                                        </span>
                                        <span>
                                            +{num(j.records_saved)} / ~{num(j.records_updated)} upd
                                            {j.duration_secs != null &&
                                                ` · ${Math.round(j.duration_secs)}s`}
                                        </span>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                </div>

                <div className="panel p-5">
                    <h2 className="font-display text-lg font-semibold">Model registry</h2>
                    <p className="mt-1 text-sm text-ink/60">
                        XGBoost AVM + LSTM forecaster, retrained on schedule from Layer 2.
                    </p>

                    {models?.models && (
                        <div className="mt-4 space-y-3">
                            {models.models.map((m) => (
                                <div key={m.name} className="border-t border-ink/10 pt-2 text-sm">
                                    <div className="flex items-baseline justify-between">
                                        <span className="font-mono">{m.name}</span>
                                        <span
                                            className={
                                                m.artifacts_present ? "text-sage text-xs" : "text-clay text-xs"
                                            }
                                        >
                                            {m.artifacts_present ? "artifacts present" : "no artifacts"}
                                        </span>
                                    </div>
                                    <div className="mt-1 flex gap-4 font-mono text-xs text-ink/50">
                                        {m.metrics?.r2 != null && <span>R² {m.metrics.r2.toFixed(3)}</span>}
                                        {m.metrics?.mae != null && (
                                            <span>MAE ${num(Math.round(m.metrics.mae))}</span>
                                        )}
                                        {m.metrics?.mape != null && (
                                            <span>MAPE {(m.metrics.mape * 100).toFixed(1)}%</span>
                                        )}
                                        <span>trained {when(m.last_trained_at)}</span>
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}