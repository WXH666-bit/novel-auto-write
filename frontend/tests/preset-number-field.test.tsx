import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import {
  CONTEXT_LENGTH_PRESETS,
  MAX_OUTPUT_TOKENS_PRESETS,
  PresetNumberField,
} from "../src/PresetNumberField";

function FieldHarness({ initialValue = 32_768 }: { initialValue?: number }) {
  const [value, setValue] = useState(initialValue);
  return (
    <>
      <PresetNumberField
        label="上下文长度"
        value={value}
        options={CONTEXT_LENGTH_PRESETS}
        min={1024}
        onChange={setValue}
      />
      <output data-testid="current-value">{value}</output>
    </>
  );
}

describe("PresetNumberField", () => {
  it("keeps a saved non-preset value in custom mode", () => {
    render(<FieldHarness initialValue={50_000} />);

    expect(screen.getByRole("combobox", { name: "上下文长度档位" })).toHaveValue(
      "custom",
    );
    expect(screen.getByRole("spinbutton", { name: "上下文长度自定义值" })).toHaveValue(
      50_000,
    );
    expect(screen.getByTestId("current-value")).toHaveTextContent("50000");
  });

  it("does not overwrite the value when entering custom mode, then applies a preset", async () => {
    const user = userEvent.setup();
    render(<FieldHarness />);

    const select = screen.getByRole("combobox", { name: "上下文长度档位" });
    await user.selectOptions(select, "custom");
    expect(select).toHaveValue("custom");
    expect(screen.getByRole("spinbutton", { name: "上下文长度自定义值" })).toHaveValue(
      32_768,
    );

    fireEvent.change(
      screen.getByRole("spinbutton", { name: "上下文长度自定义值" }),
      { target: { value: "50000" } },
    );
    expect(screen.getByTestId("current-value")).toHaveTextContent("50000");

    await user.selectOptions(select, "131072");
    expect(select).toHaveValue("131072");
    expect(screen.queryByRole("spinbutton", { name: "上下文长度自定义值" })).toBeNull();
    expect(screen.getByTestId("current-value")).toHaveTextContent("131072");
  });

  it("exposes the requested output-token presets", () => {
    expect(MAX_OUTPUT_TOKENS_PRESETS.map((option) => option.value)).toEqual([
      4_096,
      8_192,
      16_384,
      32_768,
      65_536,
    ]);
  });
});

