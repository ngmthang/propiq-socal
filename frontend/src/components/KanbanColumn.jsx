import {useDroppable} from "@dnd-kit/core";
import {SortableContext, verticalListSortingStrategy} from "@dnd-kit/sortable";
import KanbanCard from "./KanbanCard.jsx";

export default function KanbanColumn({id, title, tasks}) {
    const {setNodeRef, isOver} = useDroppable({id});

    return (
      <div
        ref={setNodeRef}
        className={'flex w-72 shrink-0 flex-col rounded-xl border p-3 transition-colors ${' +
            'isOver ? "border-terracotta bg-terracotta/5" : "border-line bg-white/40"' +
            '}'}
      >
          <div className="mb-3 flex items-center justify-between px-1">
              <h3 className="text-sm font-semibold text-ink/70">{title}</h3>
              <span className="rounded-full bg-ink/5 px-2 py-0.5 font-mono text-xs text-ink/50">
                  {tasks.length}
              </span>
          </div>

          <SortableContext
            items={tasks.map((t) => t.id)}
            strategy={verticalListSortingStrategy}
          >
              <div className="flex flex-1 flex-col gap-2">
                  {tasks.map((task) => (
                      <KanbanColumn key={task.id} task={task} />
                  ))}
              </div>
          </SortableContext>
      </div>
    );
}