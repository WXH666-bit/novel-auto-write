import { useEffect, useState } from "react";
import { CircleAlert, FileText, MessageCircle } from "lucide-react";

/**
 * The narrow workspace only needs two readable surfaces.  Keep the former
 * dossier/graph names accepted so deep links and older callers do not break;
 * both now resolve to the content surface.
 */
export type MobileWorkshopPanel = "agent" | "content" | "dossier" | "graph";

type ActiveMobileWorkshopPanel = "agent" | "content";

interface MobileWorkshopTabsProps {
  initialPanel?: MobileWorkshopPanel;
  attentionCount?: number;
  onAttention?: () => void;
}

function normalizePanel(panel: MobileWorkshopPanel): ActiveMobileWorkshopPanel {
  return panel === "agent" ? "agent" : "content";
}

/**
 * A small mobile-only workspace switcher.  The workshop keeps its desktop
 * three-column layout, while narrow screens expose one readable surface at a
 * time.  The custom event lets StoryStudio change its internal mode without
 * coupling the navigation shell to its editor state.
 */
export default function MobileWorkshopTabs({
  initialPanel = "dossier",
  attentionCount = 0,
  onAttention,
}: MobileWorkshopTabsProps) {
  const [panel, setPanel] = useState<ActiveMobileWorkshopPanel>(() =>
    normalizePanel(initialPanel),
  );

  const activate = (next: ActiveMobileWorkshopPanel) => {
    if (next !== panel) {
      setPanel(next);
      return;
    }
    // Re-dispatch the selected tab as an explicit navigation command.  This
    // keeps the internal studio mode in sync after a desktop-to-mobile resize.
    document.documentElement.dataset.mobileWorkshopPanel = next;
    window.dispatchEvent(
      new CustomEvent("story-studio-mobile-panel", { detail: next }),
    );
  };

  useEffect(() => {
    document.documentElement.dataset.mobileWorkshopPanel = panel;
    window.dispatchEvent(
      new CustomEvent("story-studio-mobile-panel", { detail: panel }),
    );
    return () => {
      if (document.documentElement.dataset.mobileWorkshopPanel === panel) {
        delete document.documentElement.dataset.mobileWorkshopPanel;
      }
    };
  }, [panel]);

  useEffect(() => {
    const handlePanelRequest = (event: Event) => {
      const requested = (event as CustomEvent<"agent" | "content">).detail;
      if (requested === "agent" || requested === "content") {
        setPanel(requested);
      }
    };
    window.addEventListener("story-studio-mobile-panel", handlePanelRequest);
    return () =>
      window.removeEventListener("story-studio-mobile-panel", handlePanelRequest);
  }, []);

  return (
    <nav className="studio-mobile-tabs" aria-label="移动端工作区">
      <button
        type="button"
        role="tab"
        aria-selected={panel === "content"}
        className={panel === "content" ? "is-active" : ""}
        onClick={() => activate("content")}
      >
        <FileText size={14} aria-hidden="true" />
        内容
      </button>
      <button
        type="button"
        role="tab"
        aria-selected={panel === "agent"}
        className={panel === "agent" ? "is-active" : ""}
        onClick={() => activate("agent")}
      >
        <MessageCircle size={14} aria-hidden="true" />
        Agent
      </button>
      <button
        type="button"
        className={`mobile-attention-tab ${attentionCount > 0 ? "has-items" : ""}`}
        onClick={onAttention}
        aria-haspopup="dialog"
        aria-label={attentionCount > 0 ? `打开待处理事项，${attentionCount} 项` : "打开待处理事项"}
      >
        <CircleAlert size={14} aria-hidden="true" />
        <span>待处理</span>
        {attentionCount > 0 && <b>{attentionCount}</b>}
      </button>
    </nav>
  );
}
