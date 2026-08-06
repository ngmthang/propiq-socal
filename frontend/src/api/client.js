import axios from "axios";

// Layer 3 (FastAPI) exposes /api/auth (register/login/me), /api/properties,
// /api/search, /api/market/{zip}, and /api/projects. Auth is a JWT bearer
// token issued by /api/auth/login or /api/auth/register.

const client = axios.create({
    baseURL: "/api",
    headers: {"Content-Type": "application/json"},
});

client.interceptors.request.use((config) => {
    const token = localStorage.getItem("propiq_token");
    if (token) config.headers["Authorization"] = `Bearer ${token}`;
    return config;
});

client.interceptors.response.use(
    (res) => res,
    (err) => {
        if(err.response?.status === 401) {
            localStorage.removeItem("propiq_token");
            localStorage.removeItem("propiq_user");
        }
        return Promise.reject(err);
    }
);

export const authApi = {
    register: (payload) => client.post("/auth/register", payload),
    login: (payload) => client.post("/auth/login", payload),
    me: () => client.get("/auth/me"),
};

export const propertiesApi = {
    search: (params) => client.get("/search", { params }),
    mapPins: (params, signal) => client.get("/search/map", { params, signal }),
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
    createFromRecommendation: (payload) => client.post("/projects", payload),
};

export default client;

export const adminApi = {
    pipelineStatus: () => client.get("/admin/pipeline/status"),
    models: () => client.get("/admin/models"),
};