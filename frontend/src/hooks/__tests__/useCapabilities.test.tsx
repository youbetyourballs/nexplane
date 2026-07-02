import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
const get = jest.fn().mockResolvedValue({ edition: "commercial", commercial: true, domains: ["commercial"] });
jest.mock("../../api/endpoints", () => ({ capabilitiesApi: { get: () => get() } }));
import { useCapabilities } from "../useCapabilities";

test("returns capabilities", async () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: React.ReactNode }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  const { result } = renderHook(() => useCapabilities(), { wrapper });
  await waitFor(() => expect(result.current.data?.commercial).toBe(true));
});
