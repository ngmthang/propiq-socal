import {useEffect, useState} from "react";
import {DndContext, closestCorners} from "@dnd-kit/core";
import {projectsApi} from "../api/client.js";
import KanbanColumn from "../components/KanbanColumn.jsx";

const COLUMNS = [
    {id: "backlog", title: "Backlog"},
    {id: "in_progress", title: "In progress"},
    {id: "review", title: "Customer review"},
    {id: "done", title: "Done"},
];

export default function ProjectBoard() {
    const [tasks, setTasks] = useState([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        projectsApi
            .list()
            .then((res) => setTasks(res.data.results ?? res.data ?? []))
            .catch(() => setTasks([]))
            .finally(() => setLoading(false));
    }, []);

    function handleDragEnd(event) {
        const {active, over} = event;
        if(!over) return;
        const newStatus = over.id;
        if(!COLUMNS.some((c) => c.id === newStatus)) return;

        const originalStatus = tasks.find((t) => t.id === active.id)?.status;

        setTasks((prev) =>
            prev.map((t) => (t.id === active.id ? {...t, status: newStatus} : t))
        );
        projectsApi.update(active.id, {status: newStatus}).catch(() => {
            // Revert on failure - keep the board honest about what actually saved.
            setTasks((prev) =>
                prev.map((t) =>
                    t.id === active.id ? {...t, status: originalStatus} : t
                )
            );
        });
    }

    return (
        <div className="px-8 py-8">
            <h1 className="font-display text-2xl font-semibold">Projects</h1>
            <p className="mt-1 text-sm text-ink/55">
                Track renovation work from recommendation to completion.
            </p>

            {loading ? (
                <p className="mt-8 text-sm text-ink/45">Loading...</p>
            ) : (
                <DndContext collisionDetection={closestCorners} onDragEnd={handleDragEnd}>
                    <div className="mt-6 flex gap-4 overflow-x-auto pb-4">
                        {COLUMNS.map((col) => (
                            <KanbanColumn
                                key={col.id}
                                id={col.id}
                                title={col.title}
                                tasks={tasks.filter((t) => t.status === col.id)}
                            />
                        ))}
                    </div>
                </DndContext>
            )}
        </div>
    );
}