import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import NewProjectWizard, {
  type NewProjectForm,
} from "../src/NewProjectWizard";
import type { StartMode } from "../src/types";

const initialForm: NewProjectForm = {
  title: "",
  logline: "",
  genre: "悬疑 / 奇幻",
  tone: "克制、具体、留白",
};

function WizardHarness({
  defaultMode = "blank",
  onSubmit,
  onClose = vi.fn(),
}: {
  defaultMode?: StartMode;
  onSubmit: (mode: StartMode) => void;
  onClose?: () => void;
}) {
  const [form, setForm] = useState(initialForm);
  return (
    <NewProjectWizard
      form={form}
      setForm={setForm}
      onClose={onClose}
      onSubmit={onSubmit}
      busy={false}
      defaultMode={defaultMode}
    />
  );
}

describe("NewProjectWizard", () => {
  it.each([
    ["blank", "空白稿纸", "创建第一张稿纸"],
    ["import", "导入旧稿", "创建并导入"],
    ["setup", "和 Agent 一起搭建", "创建并打开工坊"],
  ] as const)(
    "submits the %s start path without losing the selected mode",
    async (mode, modeLabel, submitLabel) => {
      const user = userEvent.setup();
      const onSubmit = vi.fn();
      render(<WizardHarness onSubmit={onSubmit} />);

      const modeCard = screen.getByRole("radio", { name: new RegExp(modeLabel) });
      await user.click(modeCard);
      expect(modeCard).toHaveAttribute("aria-checked", "true");
      await user.click(screen.getByRole("button", { name: /继续/ }));

      const title = screen.getByRole("textbox", { name: "小说名称" });
      await user.type(title, "雾中灯塔");
      await user.click(screen.getByRole("button", { name: new RegExp(submitLabel) }));

      expect(onSubmit).toHaveBeenCalledTimes(1);
      expect(onSubmit).toHaveBeenCalledWith(mode);
    },
  );

  it("keeps the dialog keyboard accessible and closes on Escape", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<WizardHarness onSubmit={vi.fn()} onClose={onClose} />);

    const dialog = screen.getByRole("dialog", { name: "新建小说" });
    const close = screen.getByRole("button", { name: "关闭" });
    const continueButton = screen.getByRole("button", { name: /继续/ });
    await waitFor(() => expect(document.activeElement).toBe(close));

    close.focus();
    fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(continueButton);
    continueButton.focus();
    fireEvent.keyDown(dialog, { key: "Tab" });
    expect(document.activeElement).toBe(close);

    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("keeps the same immediate interaction contract under reduced motion", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: query === "(prefers-reduced-motion: reduce)",
      media: query,
      onchange: null,
      addListener: () => undefined,
      removeListener: () => undefined,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      dispatchEvent: () => false,
    }));
    expect(window.matchMedia("(prefers-reduced-motion: reduce)").matches).toBe(true);
    render(<WizardHarness onSubmit={onSubmit} defaultMode="setup" />);

    await user.click(screen.getByRole("button", { name: /继续/ }));
    expect(screen.getByRole("heading", { name: "先给这本小说一个坐标" })).toBeVisible();
    expect(screen.getByRole("dialog")).toHaveAttribute("aria-modal", "true");
  });
});
