import { Badge, type BadgeTone } from "../components/Badge";
import { Card } from "../components/Card";
import { Money } from "../components/Money";
import { DataView } from "../components/DataView";
import { formatDate, formatDateTime, formatPence } from "../lib/format";

/**
 * Component catalogue (issue #122) — every core component in every
 * state, browsable at /catalogue. The lightweight equivalent of
 * Storybook: no extra dependency, always in sync because it imports
 * the real components.
 */
export function Catalogue() {
  const tones: BadgeTone[] = [
    "neutral",
    "info",
    "success",
    "warning",
    "error",
  ];
  return (
    <main style={{ display: "grid", gap: 16, padding: 16 }}>
      <Card title="Badges">
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {tones.map((t) => (
            <Badge key={t} tone={t}>
              {t}
            </Badge>
          ))}
        </div>
      </Card>

      <Card title="Money" actions={<Badge tone="info">tabular-nums</Badge>}>
        <p>
          <Money pence={123456} /> · <Money pence={0} /> ·{" "}
          <Money pence={-4200} /> · <Money pence={99999999} />
        </p>
      </Card>

      <Card title="Formatters">
        <p>formatPence(123456) → {formatPence(123456)}</p>
        <p>formatDate → {formatDate("2026-03-15T10:30:00Z")}</p>
        <p>formatDateTime → {formatDateTime("2026-03-15T10:30:00Z")}</p>
        <p>invalid → {formatDate("not-a-date")}</p>
      </Card>

      <Card title="DataView states">
        <DataView
          state={{ loading: true, error: null, data: null }}
          empty="unused"
        >
          {() => null}
        </DataView>
        <DataView
          state={{ loading: false, error: "Sync failed", data: null }}
          empty="unused"
        >
          {() => null}
        </DataView>
        <DataView
          state={{ loading: false, error: null, data: [] }}
          empty="Nothing here yet"
        >
          {() => null}
        </DataView>
        <DataView
          state={{ loading: false, error: null, data: ["row"] }}
          empty="unused"
        >
          {(d) => <p>data: {d.join(",")}</p>}
        </DataView>
      </Card>

      <Card title="Card without title">
        <p>Body-only card content.</p>
      </Card>
    </main>
  );
}
