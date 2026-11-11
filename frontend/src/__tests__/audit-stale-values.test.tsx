import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { widgetById } from "../widgets/registry";
const states = vi.hoisted(() => ({
  retry: vi.fn(),
  projection: { account_id:1, safe_to_spend_pence:12345, start_balance_pence:12345, committed_until_payday_pence:0, next_payday:null },
}));
vi.mock("../api/hooks", async (original) => ({...await original<object>(),
  useAccounts:()=>({data:{items:[{id:1,name:"Joint",type:"joint",archived:false}]},loading:false,error:null,retrying:false,refetch:states.retry}),
  useProjection:()=>({data:states.projection,accountName:"Joint",loading:false,error:"Projection refresh failed",retrying:false,refetch:states.retry}),
}));
vi.mock("../api/useApi", async (original) => ({...await original<object>(),
  useApi:()=>({data:{balance_pence:12345},loading:false,error:"Balance refresh failed",retrying:false,refetch:states.retry}),
}));
afterEach(()=>{cleanup();vi.clearAllMocks();});
for (const [id,error] of [["safe-to-spend","Projection refresh failed"],["accounts","Balance refresh failed"]]) {
  it(`${id} labels stale financial values with refresh failure and Retry`,()=>{
    const W=widgetById(id!)!.Component;
    render(<MemoryRouter><W title="Widget" /></MemoryRouter>);
    expect(screen.getByRole("alert").textContent).toContain(error);
    expect(screen.getAllByText(/£123/).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button",{name:"Retry"}));
    expect(states.retry).toHaveBeenCalledOnce();
  });
}
