import {useState} from "react";
import {useNavigate} from "react-router-dom";
import {useAuth} from "../context/AuthContext.jsx";
import client from "../api/client.js";

export default function Login() {
    const [key, setKey] = useState("");
    const [error, setError] = useState("");
    const [loading, setLoading] = useState(false);
    const {login} = useAuth();
    const navigate = useNavigate();

    async function handleSubmit(e) {
        e.preventDefault();
        setError("");
        setLoading(true);
        try {
            // Layer 3's health endpoint is unauthenticated; verify the key against
            // a real gated route instead so a bad key surfaces before landing.
            await client.get("/search", {
                params: {zip: "90210", limit: 1},
                headers: {"X-API-Key": key},
            });
            login(key);
            navigate("/", {replace: true});
        } catch(err) {
            setError(
                err.response?.status === 401
                    ? "That API key was rejected."
                    : "Couldn't reach PropIQ - is the API running?"
            );
        } finally {
            setLoading(false);
        }
    }

    return (
        <div className="flex min-h-screen items-center justify-center px-4">
            <div className="w-full max-w-sm">
                <div className="mb-8 flex items-center gap-2">
                    <div className="flex h-9 w-9 items-center justify-center
                                    rounded-lg bg-terracotta font-display text-lg font-semibold text-parchment"
                    >
                        P
                    </div>
                    <span className="font-display text-xl font-semibold">PropIQ</span>
                </div>

                <h1 className="mb-1 font-display text-2xl font-semibold">Sign in</h1>
                <p className="mb-6 text-sm text-ink/60">
                    Southern California property intelligence.
                </p>

                <form onSubmit={handleSubmit} className="panel space-y-4 p-6">
                    <div>
                        <label className="field-label" htmlFor="key">
                            API key
                        </label>
                        <input
                            id="key"
                            type="password"
                            className="field-input"
                            value={key}
                            onChange={(e) => setKey(e.target.value)}
                            placeholder="sk-propiq-..."
                            autoFocus
                            required
                        />
                    </div>

                    {error && <p className="text-sm text-clay">{error}</p>}

                    <button type="submit" disabled={loading} className="btn-primary w-full">
                        {loading ? "Checking..." : "Sign in"}
                    </button>
                </form>
            </div>
        </div>
    );
}