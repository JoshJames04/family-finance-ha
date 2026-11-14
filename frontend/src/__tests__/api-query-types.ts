/** Compile-time request contracts; this function is never invoked. */
import { api } from "../api/client";

function transactionFilterContracts() {
  void api.get("/api/v1/transactions", { query: { direction: "income", internal: "exclude" } });
  void api.get("/api/v1/transactions", { query: { direction: "expense", internal: "only" } });
  // @ts-expect-error The API rejects the legacy Record payment filter.
  void api.get("/api/v1/transactions", { query: { direction: "in" } });
  // @ts-expect-error Internal filters have a finite public contract too.
  void api.get("/api/v1/transactions", { query: { internal: "unknown" } });
}
void transactionFilterContracts;
