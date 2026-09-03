import { describe, expect, it, vi } from "vitest";

import { createClientId } from "../src/api";

describe("createClientId", () => {
  it("uses the native UUID implementation when available", () => {
    const randomUUID = vi.fn(() => "native-uuid") as () => `${string}-${string}-${string}-${string}-${string}`;
    const source = {
      randomUUID,
      getRandomValues: vi.fn(),
    } as unknown as Crypto;

    expect(createClientId(source)).toBe("native-uuid");
    expect(randomUUID).toHaveBeenCalledOnce();
  });

  it("creates an RFC 4122 version 4 UUID when randomUUID is unavailable", () => {
    const source = {
      getRandomValues: (bytes: Uint8Array) => {
        bytes.forEach((_, index) => {
          bytes[index] = index;
        });
        return bytes;
      },
    } as ClientCryptoForTest;

    const id = createClientId(source);

    expect(id).toBe("00010203-0405-4607-8809-0a0b0c0d0e0f");
  });

  it("still creates a local id when Web Crypto is unavailable", () => {
    expect(createClientId(null)).toMatch(/^local-[a-z0-9]+-[a-z0-9]+-[a-z0-9]+$/);
  });
});

type ClientCryptoForTest = Pick<Crypto, "getRandomValues"> &
  Partial<Pick<Crypto, "randomUUID">>;
