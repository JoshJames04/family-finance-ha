import { useState } from "react";
import { api } from "../api/client";
import { useApi } from "../api/useApi";
import { DataView } from "./DataView";
import { Panel } from "./Panel";
import { Field, Input } from "./Field";

export function HistoricalNotes({ ready }: { ready: boolean }) {
  const state = useApi(() => api.get("/api/v1/historical-notes"), ready);
  const [search, setSearch] = useState("");
  return <Panel title="Historical notes" description="Your private archive in its original year and workbook context.">
    <p className="mb-3 text-sm text-fg-subtle">Preserved from a retained extraction. The source workbook has not been reverified. These notes are not linked to current records.</p>
    <Field label="Search historical notes">{(id) => <Input id={id} type="search" value={search}
      onChange={(event) => setSearch(event.target.value)} />}</Field>
    <DataView state={state} empty="No historical archive imported">
      {(archive) => {
        const allNotes = archive.notes ?? [];
        const notes = allNotes.filter((note) =>
          [note.text, note.sheet_name, note.cell_reference, String(note.year),
            note.timestamp ?? "", note.thread_id ?? "", note.source_key, note.worksheet_part]
            .join(" ").toLocaleLowerCase().includes(search.toLocaleLowerCase()));
        return <div className="mt-3 space-y-4">
          <p className="text-sm text-fg-subtle">{notes.length} of {allNotes.length} notes</p>
          {!notes.length && <p>{allNotes.length ? "No matching notes." : "No historical archive imported."}</p>}
          {notes.map((note) => <article key={note.source_key} className="rounded-lg border border-border p-3">
            <h3 className="font-medium">{note.year} · {note.sheet_name} · {note.cell_reference}</h3>
            {note.timestamp && <p className="text-sm text-fg-subtle">Original timestamp: {note.timestamp}</p>}
            <p className="mt-2 whitespace-pre-wrap break-words text-sm">{note.text}</p>
            <details className="mt-2 break-words text-sm text-fg-subtle">
              <summary>Original source details</summary>
              <p>Source key: {note.source_key}</p>
              <p>Worksheet: {note.worksheet_part} (index {note.sheet_index})</p>
              {note.thread_id && <p>Thread: {note.thread_id}</p>}
            </details>
          </article>)}
        </div>;
      }}
    </DataView>
  </Panel>;
}
