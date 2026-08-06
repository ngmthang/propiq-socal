import {useEffect, useRef, useState} from "react";
import {
    DndContext,
    DragOverlay,
    closestCorners,
    PointerSensor,
    TouchSensor,
    useSensor,
    useSensors,
} from "@dnd-kit/core";
import {projectsApi} from "../api/client.js";
import KanbanColumn from "../components/KanbanColumn.jsx";
import KanbanCard from "../components/KanbanCard.jsx";

const COLUMNS = [
    {id: "backlog", title: "Backlog"},
    {id: "in_progress", title: "In progress"},
    {id: "review", title: "Customer review"},
    {id: "done", title: "Done"},
];

const COLUMN_IDS = new Set(COLUMNS.map((c) => c.id));

// Resolve whatever dnd-kit says we're hovering over - a column (when a
// column is empty or you're near its edge) or another card (when you're
// hovering a card, `over.id` is that card's task id) - down to a status.
function statusForOverId(overId, tasks) {
    if (COLUMN_IDS.has(overId)) return overId;
    return tasks.find((t) => t.id === overId)?.status ?? null;
}

export default function ProjectBoard() {
    const [tasks, setTasks] = useState([]);
    const [loading, setLoading] = useState(true);
    const [activeTask, setActiveTask] = useState(null);
    const dragOriginRef = useRef(null);

    const sensors = useSensors(
        // A small activation distance stops an ordinary click from being
        // read as a drag, which is what made the board feel twitchy.
        useSensor(PointerSensor, {activationConstraint: {distance: 5}}),
        useSensor(TouchSensor, {activationConstraint: {delay: 150, tolerance: 5}})
    );

    useEffect(() => {
        projectsApi
            .list()
            .then((res) => setTasks(res.data.results ?? res.data ?? []))
            .catch(() => setTasks([]))
            .finally(() => setLoading(false));
    }, []);

    function handleDragStart(event) {
        const task = tasks.find((t) => t.id === event.active.id);
        dragOriginRef.current = task?.status ?? null;
        setActiveTask(task ?? null);
    }

    // Move the card between columns live, as the pointer crosses a
    // boundary, instead of only snapping into place on drop.
    function handleDragOver(event) {
        const {active, over} = event;
        if (!over) return;

        const newStatus = statusForOverId(over.id, tasks);
        if (!newStatus) return;

        setTasks((prev) => {
            const current = prev.find((t) => t.id === active.id);
            if (!current || current.status === newStatus) return prev;
            return prev.map((t) => (t.id === active.id ? {...t, status: newStatus} : t));
        });
    }

    function handleDragEnd(event) {
        const {active} = event;
        setActiveTask(null);

        const finalTask = tasks.find((t) => t.id === active.id);
        const originalStatus = dragOriginRef.current;
        if (!finalTask || finalTask.status === originalStatus) return;

        projectsApi.update(active.id, {status: finalTask.status}).catch(() => {
            // Revert on failure - keep the board honest about what actually saved.
            setTasks((prev) =>
                prev.map((t) =>
                    t.id === active.id ? {...t, status: originalStatus} : t
                )
            );
        });
    }

    function handleDragCancel() {
        setActiveTask(null);
        if (dragOriginRef.current == null || !activeTask) return;
        const originalStatus = dragOriginRef.current;
        setTasks((prev) =>
            prev.map((t) =>
                t.id === activeTask.id ? {...t, status: originalStatus} : t
            )
        );
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
                <DndContext
                    sensors={sensors}
                    collisionDetection={closestCorners}
                    onDragStart={handleDragStart}
                    onDragOver={handleDragOver}
                    onDragEnd={handleDragEnd}
                    onDragCancel={handleDragCancel}
                >
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
                    <DragOverlay>
                        {activeTask ? <KanbanCard task={activeTask} overlay /> : null}
                    </DragOverlay>
                </DndContext>
            )}
        </div>
    );
}