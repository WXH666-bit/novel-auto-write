import { useEffect, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, Bot, FileText, Loader2, PenLine, Plus, Sparkles, X } from "lucide-react";
import type { StartMode } from "./types";

export interface NewProjectForm {
  title: string;
  logline: string;
  genre: string;
  tone: string;
}

interface NewProjectWizardProps {
  form: NewProjectForm;
  setForm: (form: NewProjectForm) => void;
  onClose: () => void;
  onSubmit: (mode: StartMode) => void;
  busy: boolean;
  defaultMode?: StartMode;
}

const startModes: Array<{
  mode: StartMode;
  icon: typeof PenLine;
  label: string;
  title: string;
  detail: string;
}> = [
  {
    mode: "blank",
    icon: PenLine,
    label: "空白稿纸",
    title: "从第一笔开始",
    detail: "马上得到一张可编辑的第一章稿纸，人物和设定之后再慢慢补齐。",
  },
  {
    mode: "setup",
    icon: Bot,
    label: "和 Agent 一起搭建",
    title: "先把故事骨架说出来",
    detail: "打开人物页，让右侧 Agent 通过对话帮你填写人物、世界和主线。",
  },
  {
    mode: "import",
    icon: FileText,
    label: "导入旧稿",
    title: "把已经发生的事带进来",
    detail: "导入 TXT 或 Markdown，拆章预览后再由模型整理故事记忆。",
  },
];

function useWizardFocus() {
  const ref = useRef<HTMLElement>(null);
  const onCloseRef = useRef<(() => void) | null>(null);
  useEffect(() => {
    const root = ref.current;
    if (!root) return undefined;
    const previous = document.activeElement as HTMLElement | null;
    const focusables = () =>
      Array.from(
        root.querySelectorAll<HTMLElement>(
          "button:not([disabled]),input:not([disabled]),textarea:not([disabled]),select:not([disabled])",
        ),
      ).filter((element) => element.offsetParent !== null);
    const frame = window.requestAnimationFrame(() => {
      if (!root.contains(document.activeElement)) focusables()[0]?.focus();
    });
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current?.();
        return;
      }
      if (event.key !== "Tab") return;
      const items = focusables();
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    root.addEventListener("keydown", onKeyDown);
    return () => {
      window.cancelAnimationFrame(frame);
      root.removeEventListener("keydown", onKeyDown);
      if (previous?.isConnected) window.requestAnimationFrame(() => previous.focus());
    };
  }, []);
  return { ref, onCloseRef };
}

export default function NewProjectWizard({
  form,
  setForm,
  onClose,
  onSubmit,
  busy,
  defaultMode = "blank",
}: NewProjectWizardProps) {
  const { ref, onCloseRef } = useWizardFocus();
  onCloseRef.current = onClose;
  const [mode, setMode] = useState<StartMode>(defaultMode);
  const [step, setStep] = useState<"choose" | "details">("choose");

  useEffect(() => {
    setMode(defaultMode);
    setStep("choose");
  }, [defaultMode]);

  const selected = startModes.find((item) => item.mode === mode) || startModes[0];
  const SelectedIcon = selected.icon;

  return (
    <div className="modal-layer">
      <button className="modal-scrim" onClick={onClose} aria-label="关闭新建小说窗口" />
      <section
        className="modal modal-large new-project-wizard"
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby="new-project-wizard-title"
        tabIndex={-1}
      >
        <div className="modal-head">
          <div>
            <p className="eyebrow">START A NEW MANUSCRIPT</p>
            <h2 id="new-project-wizard-title">新建小说</h2>
          </div>
          <button className="quiet-icon" onClick={onClose} aria-label="关闭">
            <X size={17} />
          </button>
        </div>

        {step === "choose" ? (
          <div className="wizard-body">
            <div className="wizard-intro">
              <div>
                <span className="eyebrow">第一步 · 选择起点</span>
                <h3>你想从哪里落笔？</h3>
                <p>三种入口都会进入同一个写作台；你可以随时在表格和关系图之间切换。</p>
              </div>
              <span className="wizard-seal" aria-hidden="true"><Sparkles size={18} /></span>
            </div>
            <div className="wizard-mode-grid" role="radiogroup" aria-label="新建方式">
              {startModes.map(({ mode: itemMode, icon: Icon, label, title, detail }) => (
                <button
                  type="button"
                  role="radio"
                  aria-checked={mode === itemMode}
                  className={`wizard-mode-card ${mode === itemMode ? "is-selected" : ""}`}
                  key={itemMode}
                  onClick={() => setMode(itemMode)}
                >
                  <span className="wizard-mode-icon"><Icon size={19} /></span>
                  <span className="wizard-mode-label">{label}</span>
                  <strong>{title}</strong>
                  <small>{detail}</small>
                  <span className="wizard-mode-arrow"><ArrowRight size={15} /></span>
                </button>
              ))}
            </div>
            <div className="wizard-actions">
              <button type="button" className="button button-secondary" onClick={onClose}>取消</button>
              <button type="button" className="button button-primary" onClick={() => setStep("details")}>
                继续 <ArrowRight size={14} />
              </button>
            </div>
          </div>
        ) : (
          <form
            className="wizard-body"
            onSubmit={(event) => {
              event.preventDefault();
              onSubmit(mode);
            }}
          >
            <button type="button" className="wizard-back" onClick={() => setStep("choose")}>
              <ArrowLeft size={14} /> 返回选择
            </button>
            <div className="wizard-selected-mode">
              <span className="wizard-mode-icon"><SelectedIcon size={17} /></span>
              <div><span>{selected.label}</span><strong>{selected.title}</strong></div>
            </div>
            <div className="wizard-form-heading">
              <span className="eyebrow">第二步 · 填写基本信息</span>
              <h3>先给这本小说一个坐标</h3>
              <p>{mode === "import" ? "稍后选择原稿；这里的信息会成为导入项目的故事首页。" : mode === "setup" ? "创建后打开人物页，右侧 Agent 会把这些信息作为第一轮对话的起点。" : "创建后马上出现第一章空白稿纸，随时可以补充人物和规则。"}</p>
            </div>
            <label className="field">
              <span>小说名称</span>
              <input autoFocus value={form.title} onChange={(event) => setForm({ ...form, title: event.target.value })} placeholder="例如：雾中灯塔" required />
            </label>
            <label className="field">
              <span>一句话梗概 <small>可稍后让 Agent 改写</small></span>
              <textarea rows={3} value={form.logline} onChange={(event) => setForm({ ...form, logline: event.target.value })} placeholder="谁在什么地方，为了什么不得不行动？" />
            </label>
            <div className="form-grid form-grid-two">
              <label className="field"><span>类型</span><input value={form.genre} onChange={(event) => setForm({ ...form, genre: event.target.value })} /></label>
              <label className="field"><span>文风</span><input value={form.tone} onChange={(event) => setForm({ ...form, tone: event.target.value })} /></label>
            </div>
            <div className="wizard-actions">
              <button type="button" className="button button-secondary" onClick={onClose}>取消</button>
              <button type="submit" className="button button-primary" disabled={busy}>
                {busy ? <Loader2 size={14} className="spin" /> : <Plus size={14} />}
                {busy ? "准备中…" : mode === "import" ? "创建并导入" : mode === "setup" ? "创建并打开工坊" : "创建第一张稿纸"}
              </button>
            </div>
          </form>
        )}
      </section>
    </div>
  );
}
