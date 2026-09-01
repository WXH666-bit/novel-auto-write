import { useEffect, useState } from "react";
import { LayoutGrid, MessageCircle, Network } from "lucide-react";

export type MobileWorkshopPanel = "agent" | "dossier" | "graph";

interface MobileWorkshopTabsProps {
  initialPanel?: MobileWorkshopPanel;
}

/**
 * A small mobile-only workspace switcher.  The workshop keeps its desktop
 * three-column layout, while narrow screens expose one readable surface at a
 * time.  The custom event lets StoryStudio change its internal mode without
 * coupling the navigation shell to its editor state.
 */
export default function MobileWorkshopTabs({
  initialPanel = "dossier",
}: MobileWorkshopTabsProps) {
  const [panel, setPanel] = useState<MobileWorkshopPanel>(initialPanel);

  const activate = (next: MobileWorkshopPanel) => {
    if (next !== panel) {
      setPanel(next);
      return;
    }
    // Re-dispatch the selected tab as an explicit navigation command.  This
    // keeps the internal studio mode in sync after a desktop-to-mobile resize.
    document.documentElement.dataset.mobileWorkshopPanel = next;
    window.dispatchEvent(new CustomEvent("story-studio-mobile-panel", { detail: next }));
  };

  useEffect(() => {
    document.documentElement.dataset.mobileWorkshopPanel = panel;
    window.dispatchEvent(new CustomEvent("story-studio-mobile-panel", { detail: panel }));
    return () => {
      if (document.documentElement.dataset.mobileWorkshopPanel === panel) {
        delete document.documentElement.dataset.mobileWorkshopPanel;
      }
    };
  }, [panel]);

  return (
    <nav className="studio-mobile-tabs" aria-label="移动端工作区" role="tablist">
      <button
        type="button"
        role="tab"
        aria-selected={panel === "agent"}
        className={panel === "agent" ? "is-active" : ""}
        onClick={() => activate("agent")}
      >
        <MessageCircle size={14} aria-hidden="true" />
        对话
      </button>
      <button
        type="button"
        role="tab"
        aria-selected={panel === "dossier"}
        className={panel === "dossier" ? "is-active" : ""}
        onClick={() => activate("dossier")}
      >
        <LayoutGrid size={14} aria-hidden="true" />
        资料
      </button>
      <button
        type="button"
        role="tab"
        aria-selected={panel === "graph"}
        className={panel === "graph" ? "is-active" : ""}
        onClick={() => activate("graph")}
      >
        <Network size={14} aria-hidden="true" />
        图谱
      </button>
    </nav>
  );
}
