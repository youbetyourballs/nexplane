import { validateAllowlistEntry, RFC1918_DEFAULT_ALLOWLIST } from "../allowlist";

test("accepts valid entries", () => {
  for (const ok of ["10.0.0.0/8:5432", "192.168.1.10:443", "db.internal:5432", "10.0.0.0/24:1-1024", "10.0.0.0/8:*"]) {
    expect(validateAllowlistEntry(ok)).toBeNull();
  }
});
test("rejects invalid entries", () => {
  for (const bad of ["", "noport", "host:99999", "host:0", "host:", ":443"]) {
    expect(validateAllowlistEntry(bad)).not.toBeNull();
  }
});
test("RFC1918 default has the three private ranges", () => {
  expect(RFC1918_DEFAULT_ALLOWLIST).toEqual(["10.0.0.0/8:*", "172.16.0.0/12:*", "192.168.0.0/16:*"]);
});
