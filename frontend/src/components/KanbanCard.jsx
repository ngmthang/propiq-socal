import {useSortable} from "@dnd-kit/sortable";
import {CSS} from "@dnd-kit/utilities";

export default function KanbanCard({task}) {
    const {attributes, listeners, setNodeRef, transform, transition, isDragging} =
        useSortable({id: task.id});

    const style = {
        transform: CSS.Transform.toString(transform),
        transition,
        opacity: isDragging ? 0.5 : 1,
    };

    return (
        <div
            ref={setNodeRef}
            style={style}
            {...attributes}
            {...listeners}
            className="cursor-grab rounded-lg border border-line bg-white p-3 shadow-sm active:cursor-grabbing"
        >
            <p className="text-sm font-medium text-ink">{task.title}</p>
            <p className="mt-0.5 truncate text-xs text-ink/50">{task.property_address}</p>
            {task.assignee && (
                <div className="mt-2 flex items-center gap-1.5">
                    <span className="flex h-5 w-5 items-center justify-center rounded-full bg-marine/15 text-[10px] font-semibold text-marine">
                        {task.assignee
                            .split(" ")
                            .map((n) => n[0])
                            .join("")
                            .slice(0, 2)}
                    </span>
                    <span className="text-xs text-ink/50">{task.assignee}</span>
                </div>
            )}
        </div>
    );
}