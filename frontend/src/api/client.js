import axios from "axios";

// Layer 3 (FastAPI) exposes /api/properties, /api/search, /api/market/{zip}
// and the per-property /valuation, and /analysis endpoints (InferenceEngine).

const client = axios.create({
    baseURL: "/api",
    headers: {"Content-Type": "application/json"},
});

client.interceptors.request.use((config) => {
    const key = localStorage.getItem("propiq_api_key");
    if (key) config.headers["X-API-Key"] = key;
    return config;
});

client.interceptors.response.use(
    (res) => res,
    (err) => {
        if(err.response?.status === 401) {
            localStorage.removeItem("propiq_api_key");
        }
        return Promise.reject(err);
    }
);

export const propertiesApi = {
    search: (params) => client.get("/search", { params }),
    get: (id) => client.get(`/properties/${id}`),
    valuation: (id) => client.get(`/properties/${id}/valuation`),
    analysis: (id) => client.get(`/properties/${id}/analysis`),
};

export const marketApi = {
    trend: (zip) => client.get(`/market/${zip}`),
};

export const projectsApi = {
    list: () => client.get("/projects"),
    update: (id, payload) => client.patch(`/projects/${id}`, payload),
};

export default client;