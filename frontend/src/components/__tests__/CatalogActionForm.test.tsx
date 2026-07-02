// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.
import { render, screen, fireEvent } from "@testing-library/react";
import CatalogActionForm from "../CatalogActionForm";
const ACTION = { connector_type: "commercial", action_id: "provision_instance", generic_action: "provision_instance",
  display_name: "Provision", description: "", group: "Customers", domain: "commercial", read_only: false, destructive: false, order: 1,
  param_schema: [
    { name: "client_id", type: "string", required: true },
    { name: "dry_run", type: "boolean", required: false, default: false },
    { name: "mode", type: "string", required: true, enum: ["managed_single_ec2", "self_hosted_compose"] },
  ] };

test("renders fields + submits typed params", () => {
  const onSubmit = jest.fn();
  render(<CatalogActionForm action={ACTION} onSubmit={onSubmit} />);
  fireEvent.change(screen.getByLabelText(/client_id/i), { target: { value: "acme" } });
  fireEvent.change(screen.getByLabelText(/^mode/i), { target: { value: "managed_single_ec2" } });
  fireEvent.click(screen.getByRole("button", { name: /run|submit/i }));
  expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ client_id: "acme", mode: "managed_single_ec2", dry_run: false }));
});

test("blocks submit when required empty", () => {
  const onSubmit = jest.fn();
  render(<CatalogActionForm action={ACTION} onSubmit={onSubmit} />);
  fireEvent.click(screen.getByRole("button", { name: /run|submit/i }));
  expect(onSubmit).not.toHaveBeenCalled();
});
