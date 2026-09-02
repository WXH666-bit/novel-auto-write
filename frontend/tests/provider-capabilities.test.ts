import { describe, expect, it } from "vitest";

import { normalizeProviderCapabilities } from "../src/api";

describe("normalizeProviderCapabilities", () => {
  it("keeps an explicit canonical false authoritative", () => {
    expect(
      normalizeProviderCapabilities({
        vision: false,
        image_input: true,
        supports_vision: true,
        tools: true,
      }),
    ).toEqual({ vision: false, tools: true });
  });

  it.each([
    ["image_input", true],
    ["supports_vision", "true"],
    ["multimodal", 1],
  ])("migrates the legacy %s alias", (name, value) => {
    expect(normalizeProviderCapabilities({ [name]: value })).toEqual({
      vision: true,
    });
  });

  it("does not treat the string false as enabled", () => {
    expect(normalizeProviderCapabilities({ vision: "false" })).toEqual({
      vision: false,
    });
  });
});
