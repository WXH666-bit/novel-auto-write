import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Archive,
  ArrowLeft,
  ArrowRight,
  Bell,
  BookOpen,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  CircleCheck,
  CircleHelp,
  Clock3,
  Command,
  Copy,
  Download,
  FileArchive,
  FileText,
  FolderOpen,
  Gauge,
  GitCompare,
  History,
  Import,
  Keyboard,
  Layers3,
  Lightbulb,
  Loader2,
  LockKeyhole,
  Menu,
  MoreHorizontal,
  Network,
  PanelRight,
  PencilLine,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  Search,
  Send,
  Settings,
  ShieldCheck,
  Sparkles,
  SplitSquareHorizontal,
  Table2,
  Tag,
  TimerReset,
  Trash2,
  Upload,
  WandSparkles,
  X,
  UserRound,
  LogOut,
  ServerCog,
  PlusCircle,
  Trash,
} from "lucide-react";
import {
  commitImport,
  createGeneration,
  createProject,
  downloadExport,
  editReviewDraft,
  getCanon,
  getChapters,
  getProviders,
  getCurrentUser,
  getLatestGeneration,
  getReview,
  listenGenerationEvents,
  getProjects,
  getStoryMap,
  normalizeJob,
  previewImport,
  createProvider,
  updateProvider,
  deleteProvider,
  setDefaultProvider,
  deleteProviderKey,
  logoutAccount,
  logoutAllSessions,
  onAuthEvent,
  rebuildProjectMemory,
  reviewAction,
  retryGeneration,
  testProvider,
  updateChapter,
  updateProject,
} from "./api";
import AuthScreen, {
  AccountSecurityView,
  getAuthViewFromPath,
} from "./AuthScreen";
import InkLandscape, { InkInteractionLayer } from "./InkLandscape";
import type {
  AuditIssue,
  CanonChange,
  CanonItem,
  Chapter,
  GenerationJob,
  ImportChapterPreview,
  ImportPreview,
  JobStatus,
  LedgerTab,
  PlotThread,
  Project,
  ProviderProfile,
  ReviewBundle,
  SourceRef,
  TimelineEvent,
  View,
  AuthSession,
  AuthView,
} from "./types";

const jobPhases: Array<{ key: JobStatus; label: string }> = [
  { key: "preparing_context", label: "准备上下文" },
  { key: "planning", label: "剧情规划" },
  { key: "drafting", label: "正文写作" },
  { key: "extracting", label: "事实提取" },
  { key: "auditing", label: "连续性审查" },
  { key: "revising", label: "定向修订" },
  { key: "awaiting_review", label: "等待审核" },
];

type GenerationFormState = {
  mode: string;
  chapter_count: number;
  word_target: number;
  must: string;
  must_not: string;
  revision_rounds: number;
};

const DEFAULT_GENERATION_WORD_TARGET = 3500;

function clampGenerationWordTarget(value: unknown) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return DEFAULT_GENERATION_WORD_TARGET;
  }
  return Math.min(8000, Math.max(800, Math.round(parsed / 100) * 100));
}

function linesToText(value?: string[]) {
  return (value || []).filter(Boolean).join("\n");
}

function textToLines(value: string) {
  return value
    .split(/\r?\n/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function generationFormForProject(project: Project | null): GenerationFormState {
  const target = project?.target_word_count ?? project?.word_target;
  return {
    mode: "next_chapter",
    chapter_count: 1,
    word_target: clampGenerationWordTarget(target),
    must: linesToText(project?.must_happen),
    must_not: linesToText(project?.must_not_happen),
    revision_rounds: 2,
  };
}

function projectPatchFromGenerationForm(
  form: GenerationFormState,
): Partial<Project> {
  return {
    target_word_count: form.word_target,
    must_happen: textToLines(form.must),
    must_not_happen: textToLines(form.must_not),
  };
}

const statusLabels: Record<string, string> = {
  planned: "规划中",
  draft: "草稿",
  review: "待审核",
  accepted: "已入典",
  archived: "已归档",
  confirmed: "已确认",
  pending: "待确认",
  needs_review: "待复核",
  superseded: "已取代",
  active: "进行中",
  resolved: "已回收",
  dormant: "休眠",
  blocked: "受阻",
};

const severityLabels: Record<string, string> = {
  critical: "严重",
  major: "主要",
  minor: "轻微",
  note: "提示",
};

const iconForCategory: Record<string, typeof BookOpen> = {
  character: BookOpen,
  world: Network,
  item: Archive,
  relationship: CircleCheck,
  constraint: LockKeyhole,
  setting: Layers3,
};

function uid(prefix: string) {
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function formatWords(value: number | undefined) {
  return new Intl.NumberFormat("zh-CN").format(value || 0);
}

function shortTime(value?: string) {
  if (!value) return "刚刚";
  if (value === "刚刚" || value === "昨天") return value;
  return value.replace("T", " ").slice(0, 16);
}

function useDialogFocus<T extends HTMLElement>() {
  const dialogRef = useRef<T>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return undefined;
    const previous = document.activeElement as HTMLElement | null;
    const focusableSelector = [
      "button:not([disabled])",
      "input:not([disabled])",
      "textarea:not([disabled])",
      "select:not([disabled])",
      "[href]",
      '[tabindex]:not([tabindex="-1"])',
    ].join(",");
    const focusable = () =>
      Array.from(dialog.querySelectorAll<HTMLElement>(focusableSelector)).filter(
        (element) => element.offsetParent !== null,
      );
    const frame = window.requestAnimationFrame(() => {
      if (!dialog.contains(document.activeElement)) {
        (focusable()[0] || dialog).focus();
      }
    });
    const trapFocus = (event: KeyboardEvent) => {
      if (event.key !== "Tab") return;
      const items = focusable();
      if (!items.length) {
        event.preventDefault();
        dialog.focus();
        return;
      }
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
    dialog.addEventListener("keydown", trapFocus);
    return () => {
      window.cancelAnimationFrame(frame);
      dialog.removeEventListener("keydown", trapFocus);
      if (previous && previous !== document.body) {
        window.requestAnimationFrame(() => previous.focus());
      }
    };
  }, []);

  return dialogRef;
}

function Workspace({
  session,
  onLogout,
  onLogoutAll,
  onAccount,
}: {
  session: AuthSession;
  onLogout: () => void;
  onLogoutAll: () => void;
  onAccount: () => void;
}) {
  const queryClient = useQueryClient();
  const [view, setView] = useState<View>("library");
  const [activeProjectId, setActiveProjectId] = useState("");
  const [activeChapterId, setActiveChapterId] = useState("");
  const [ledgerTab, setLedgerTab] = useState<LedgerTab>("canon");
  const [mobileSidebar, setMobileSidebar] = useState(false);
  const [mobileLedger, setMobileLedger] = useState(false);
  const [showNewProject, setShowNewProject] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const [showGeneration, setShowGeneration] = useState(false);
  const [showReview, setShowReview] = useState(false);
  const [showCommand, setShowCommand] = useState(false);
  const [toast, setToast] = useState<{
    tone: "success" | "warning" | "error" | "info";
    message: string;
  } | null>(null);
  const [job, setJob] = useState<GenerationJob | null>(null);
  const [draftById, setDraftById] = useState<Record<string, string>>({});
  const [saveState, setSaveState] = useState<
    "saved" | "saving" | "dirty" | "error"
  >("saved");
  const [review, setReview] = useState<ReviewBundle | null>(null);
  const [importPreviewData, setImportPreviewData] =
    useState<ImportPreview | null>(null);
  const [provider, setProvider] = useState<ProviderProfile | null>(null);
  const [selectedProviderId, setSelectedProviderId] = useState("");
  const [defaultProviderId, setDefaultProviderId] = useState(
    session.user.default_provider_id || "",
  );
  const [accountMenuOpen, setAccountMenuOpen] = useState(false);
  const [forceAcceptOpen, setForceAcceptOpen] = useState(false);
  const [forceReason, setForceReason] = useState("");
  const [focusedReviewSection, setFocusedReviewSection] = useState<
    "issues" | "canon" | "sources"
  >("issues");
  const [showDiff, setShowDiff] = useState(false);
  const [newProjectForm, setNewProjectForm] = useState({
    title: "",
    logline: "",
    genre: "悬疑 / 奇幻",
    tone: "克制、具体、留白",
  });
  const [generationForm, setGenerationForm] = useState<GenerationFormState>(
    generationFormForProject(null),
  );
  const generationFormRef = useRef(generationForm);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const draftTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const generationSettingsTimer = useRef<ReturnType<typeof setTimeout> | null>(
    null,
  );
  const pendingGenerationSettings = useRef<{
    projectId: string;
    payload: Partial<Project>;
  } | null>(null);
  const generationSettingsWriteChain = useRef<Promise<unknown>>(
    Promise.resolve(),
  );
  const workspaceEpochRef = useRef(0);
  const activeProjectIdRef = useRef(activeProjectId);
  const activeJobIdRef = useRef(job?.id || "");
  const workspaceStageRef = useRef<HTMLDivElement>(null);
  const lastWorkspaceFocusRef = useRef<HTMLElement | null>(null);
  generationFormRef.current = generationForm;
  activeProjectIdRef.current = activeProjectId;
  activeJobIdRef.current = job?.id || "";

  useEffect(() => {
    window.scrollTo({ top: 0, left: 0, behavior: "auto" });
  }, [view, activeProjectId]);

  useEffect(() => {
    const clearWorkspaceState = () => {
      if (draftTimer.current) {
        clearTimeout(draftTimer.current);
        draftTimer.current = null;
      }
      if (generationSettingsTimer.current) {
        clearTimeout(generationSettingsTimer.current);
        generationSettingsTimer.current = null;
      }
      pendingGenerationSettings.current = null;
      workspaceEpochRef.current += 1;
      activeProjectIdRef.current = "";
      activeJobIdRef.current = "";
      setView("library");
      setActiveProjectId("");
      setActiveChapterId("");
      setJob(null);
      setDraftById({});
      setReview(null);
      setImportPreviewData(null);
      setProvider(null);
      setSelectedProviderId("");
      setDefaultProviderId("");
      setShowNewProject(false);
      setShowImport(false);
      setShowGeneration(false);
      setShowReview(false);
      setShowCommand(false);
      setAccountMenuOpen(false);
    };
    window.addEventListener("novel-auth-cleared", clearWorkspaceState);
    return () =>
      window.removeEventListener("novel-auth-cleared", clearWorkspaceState);
  }, []);

  useEffect(
    () => () => {
      if (generationSettingsTimer.current) {
        clearTimeout(generationSettingsTimer.current);
      }
      pendingGenerationSettings.current = null;
    },
    [],
  );

  const projectsQuery = useQuery({
    queryKey: ["projects"],
    queryFn: getProjects,
  });
  const projects = projectsQuery.data ?? [];
  const activeProject =
    projects.find((project) => project.id === activeProjectId) ?? null;
  const chaptersQuery = useQuery({
    queryKey: ["chapters", activeProjectId],
    queryFn: () => getChapters(activeProjectId),
    enabled: Boolean(activeProjectId),
  });
  const chapters = chaptersQuery.data ?? [];
  const canonQuery = useQuery({
    queryKey: ["canon", activeProjectId],
    queryFn: () => getCanon(activeProjectId),
    enabled: Boolean(activeProjectId),
  });
  const canon = canonQuery.data ?? [];
  const storyMapQuery = useQuery({
    queryKey: ["story-map", activeProjectId],
    queryFn: () => getStoryMap(activeProjectId),
    enabled: Boolean(activeProjectId),
  });
  const storyMap = storyMapQuery.data ?? {
    threads: [],
    timeline: [],
    characters: [],
    foreshadowing: [],
  };
  const providerQuery = useQuery({
    queryKey: ["providers"],
    queryFn: getProviders,
  });
  const providers = providerQuery.data ?? [];
  const activeChapter =
    chapters.find((chapter) => chapter.id === activeChapterId) ??
    chapters[0] ??
    null;
  const activeContent = activeChapter
    ? (draftById[activeChapter.id] ?? activeChapter.content ?? "")
    : "";
  const reviewChapter =
    (review
      ? chapters.find((chapter) => chapter.id === review.chapter_id)
      : activeChapter) ?? null;

  const saveGenerationSettings = useCallback(
    (projectId: string, payload: Partial<Project>) => {
      const workspaceEpoch = workspaceEpochRef.current;
      const write = generationSettingsWriteChain.current
        .catch(() => undefined)
        .then(async () => {
          if (workspaceEpoch !== workspaceEpochRef.current) return null;
          const saved = await updateProject(projectId, payload);
          if (workspaceEpoch !== workspaceEpochRef.current) return saved;
          queryClient.setQueryData<Project[]>(["projects"], (current) =>
            (current || []).map((item) =>
              item.id === saved.id ? { ...item, ...saved } : item,
            ),
          );
          return saved;
        });
      generationSettingsWriteChain.current = write;
      return write;
    },
    [queryClient],
  );

  const queueGenerationSettingsSave = useCallback(
    (form: GenerationFormState, projectId = activeProjectId) => {
      if (!projectId) return;
      if (generationSettingsTimer.current) {
        clearTimeout(generationSettingsTimer.current);
      }
      pendingGenerationSettings.current = {
        projectId,
        payload: projectPatchFromGenerationForm(form),
      };
      generationSettingsTimer.current = setTimeout(() => {
        const pending = pendingGenerationSettings.current;
        pendingGenerationSettings.current = null;
        generationSettingsTimer.current = null;
        if (!pending) return;
        void saveGenerationSettings(pending.projectId, pending.payload).catch(
          (error) =>
            setToast({
              tone: "warning",
              message:
                error instanceof Error
                  ? error.message
                  : "生成偏好自动保存失败，开始生成时仍会携带本次填写内容。",
            }),
        );
      }, 700);
    },
    [activeProjectId, saveGenerationSettings],
  );

  const flushGenerationSettings = useCallback(
    async (projectId: string, form: GenerationFormState) => {
      const pending = pendingGenerationSettings.current;
      if (pending?.projectId === projectId) {
        if (generationSettingsTimer.current) {
          clearTimeout(generationSettingsTimer.current);
          generationSettingsTimer.current = null;
        }
        pendingGenerationSettings.current = null;
      }
      return saveGenerationSettings(projectId, projectPatchFromGenerationForm(form));
    },
    [saveGenerationSettings],
  );

  const handleGenerationFormChange = useCallback(
    (next: GenerationFormState) => {
      setGenerationForm(next);
      queueGenerationSettingsSave(next);
    },
    [queueGenerationSettingsSave],
  );

  useEffect(() => {
    if (!activeProjectId) return undefined;
    const projectId = activeProjectId;
    const persistBeforeLeaving = () => {
      if (activeProjectIdRef.current !== projectId) return;
      if (generationSettingsTimer.current) {
        clearTimeout(generationSettingsTimer.current);
        generationSettingsTimer.current = null;
      }
      pendingGenerationSettings.current = null;
      void updateProject(
        projectId,
        projectPatchFromGenerationForm(generationFormRef.current),
        { keepalive: true },
      ).catch(() => undefined);
    };
    window.addEventListener("pagehide", persistBeforeLeaving);
    return () => window.removeEventListener("pagehide", persistBeforeLeaving);
  }, [activeProjectId]);

  useEffect(() => {
    if (!activeProject) return;
    const pending = pendingGenerationSettings.current;
    if (pending && pending.projectId !== activeProject.id) {
      if (generationSettingsTimer.current) {
        clearTimeout(generationSettingsTimer.current);
        generationSettingsTimer.current = null;
      }
      pendingGenerationSettings.current = null;
      void saveGenerationSettings(pending.projectId, pending.payload).catch(
        (error) =>
          setToast({
            tone: "warning",
            message:
              error instanceof Error
                ? error.message
                : "上一项目的生成偏好自动保存失败。",
          }),
      );
    }
    setGenerationForm(generationFormForProject(activeProject));
  }, [activeProject?.id, saveGenerationSettings]);

  useEffect(() => {
    if (
      projects.length &&
      !projects.some((project) => project.id === activeProjectId)
    )
      setActiveProjectId(projects[0].id);
  }, [activeProjectId, projects]);

  useEffect(() => {
    if (
      chapters.length &&
      !chapters.some((chapter) => chapter.id === activeChapterId)
    )
      setActiveChapterId(chapters[0].id);
  }, [activeChapterId, chapters]);

  useEffect(() => {
    const defaultId = defaultProviderId;
    const preferred =
      providers.find((item) => item.id === defaultId) || providers[0] || null;
    setProvider(preferred);
    setSelectedProviderId(preferred?.id || "");
  }, [defaultProviderId, providers]);

  useEffect(() => {
    setDefaultProviderId(session.user.default_provider_id || "");
  }, [session.user.default_provider_id]);

  useEffect(() => {
    if (!toast) return undefined;
    const timer = setTimeout(() => setToast(null), 4200);
    return () => clearTimeout(timer);
  }, [toast]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        if (activeChapter) saveDraft(activeChapter, activeContent);
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setShowCommand(true);
      }
      if (event.key === "Escape") {
        setShowCommand(false);
        setShowNewProject(false);
        setShowImport(false);
        setShowGeneration(false);
        setShowReview(false);
        setForceAcceptOpen(false);
        setMobileSidebar(false);
        setMobileLedger(false);
        setAccountMenuOpen(false);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  });

  useEffect(() => {
    if (
      !job ||
      ["completed", "failed", "cancelled", "needs_retry"].includes(job.status)
    )
      return undefined;
    const subscribedProjectId = activeProjectId;
    const subscribedJobId = job.id;
    return listenGenerationEvents(
      job.id,
      (nextJob) => {
        if (
          activeProjectIdRef.current !== subscribedProjectId ||
          activeJobIdRef.current !== subscribedJobId
        )
          return;
        setJob((current) => ({
          ...(current || {}),
          ...nextJob,
          chapter_count: nextJob.chapter_count ?? current?.chapter_count,
          batch_index:
            nextJob.batch_index ?? nextJob.chapter_index ?? current?.batch_index,
          batch_total: nextJob.batch_total ?? current?.batch_total,
          batch_remaining:
            nextJob.batch_remaining ?? current?.batch_remaining,
        }));
        if (nextJob.status === "awaiting_review") {
          if (nextJob.review_bundle_id) {
            void getReview(nextJob.review_bundle_id)
              .then((bundle) => {
                if (
                  activeProjectIdRef.current !== subscribedProjectId ||
                  activeJobIdRef.current !== subscribedJobId
                )
                  return;
                setReview(bundle);
                setActiveChapterId(bundle.chapter_id);
                void queryClient.invalidateQueries({
                  queryKey: ["chapters", activeProjectId],
                });
                setShowReview(true);
                setToast({
                  tone: "success",
                  message: "审查完成，审核包等待你的决定；正典尚未改变。",
                });
              })
              .catch((error) =>
                setToast({
                  tone: "error",
                  message:
                    error instanceof Error ? error.message : "审核包读取失败。",
                }),
              );
          } else {
            setToast({
              tone: "error",
              message: "生成任务缺少审核包引用，请重试任务。",
            });
          }
        }
      },
      () => {
        if (
          activeProjectIdRef.current !== subscribedProjectId ||
          activeJobIdRef.current !== subscribedJobId
        )
          return;
        setToast({
          tone: "warning",
          message: "生成进度连接中断；请打开任务条重试，不会自动提交。",
        });
      },
    );
  }, [activeChapterId, activeProjectId, job?.id]);

  useEffect(() => {
    if (!activeProjectId) return undefined;
    let cancelled = false;
    void getLatestGeneration(activeProjectId)
      .then(async (latest) => {
        if (cancelled || !latest) return;
        const visibleStates: JobStatus[] = [
          "queued",
          "running",
          "preparing_context",
          "planning",
          "drafting",
          "extracting",
          "auditing",
          "revising",
          "awaiting_review",
          "needs_retry",
          "failed",
        ];
        if (!visibleStates.includes(latest.status)) return;
        setJob(latest);
        if (latest.status === "awaiting_review" && latest.review_bundle_id) {
          const bundle = await getReview(latest.review_bundle_id);
          if (!cancelled) {
            setReview(bundle);
            setActiveChapterId(bundle.chapter_id);
          }
        }
      })
      .catch((error) => {
        if (!cancelled)
          setToast({
            tone: "error",
            message: error instanceof Error ? error.message : "任务恢复失败。",
          });
      });
    return () => {
      cancelled = true;
    };
  }, [activeProjectId]);

  const saveMutation = useMutation({
    mutationFn: ({ id, content }: { id: string; content: string }) =>
      updateChapter(id, {
        content,
        word_count: content.length,
        status: "review",
      }),
    onSuccess: (saved) => {
      queryClient.setQueryData<Chapter[]>(
        ["chapters", activeProjectId],
        (current) =>
          (current || []).map((chapter) =>
            chapter.id === saved.id
              ? {
                  ...chapter,
                  ...saved,
                  content: saved.content ?? draftById[saved.id],
                }
              : chapter,
          ),
      );
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
      setSaveState("saved");
    },
    onError: () => {
      setSaveState("error");
      setToast({
        tone: "warning",
        message: "服务器暂不可用，草稿仍保留在本机内存；稍后可重试保存。",
      });
    },
  });

  const reviewDraftMutation = useMutation({
    mutationFn: ({
      reviewId,
      content,
    }: {
      reviewId: string;
      content: string;
    }) => editReviewDraft(reviewId, content),
    onSuccess: (bundle) => {
      setReview(bundle);
      void queryClient.invalidateQueries({
        queryKey: ["chapters", activeProjectId],
      });
      setSaveState("saved");
      setToast({
        tone: "info",
        message: "审核稿已保存；旧审查已失效，请重新审查。",
      });
    },
    onError: (error) => {
      setSaveState("error");
      setToast({
        tone: "error",
        message: error instanceof Error ? error.message : "审核稿保存失败。",
      });
    },
  });

  const saveDraft = useCallback(
    (chapter: Chapter, content: string) => {
      setSaveState("saving");
      setDraftById((current) => ({ ...current, [chapter.id]: content }));
      if (
        review &&
        review.chapter_id === chapter.id &&
        ["awaiting_review", "stale"].includes(review.status)
      ) {
        reviewDraftMutation.mutate({ reviewId: review.id, content });
      } else {
        saveMutation.mutate({ id: chapter.id, content });
      }
    },
    [review, reviewDraftMutation, saveMutation],
  );

  const handleContentChange = (content: string) => {
    if (!activeChapter) return;
    setDraftById((current) => ({ ...current, [activeChapter.id]: content }));
    setSaveState("dirty");
    if (draftTimer.current) clearTimeout(draftTimer.current);
    draftTimer.current = setTimeout(
      () => saveDraft(activeChapter, content),
      1200,
    );
  };

  const selectProject = (project: Project) => {
    setActiveProjectId(project.id);
    setActiveChapterId(project.current_chapter_id || "");
    setJob(null);
    setReview(null);
    setView("desk");
  };

  const createProjectMutation = useMutation({
    mutationFn: () =>
      createProject({
        ...newProjectForm,
        title: newProjectForm.title.trim() || "未命名小说",
        source: "local",
      }),
    onSuccess: (project) => {
      queryClient.setQueryData<Project[]>(["projects"], (current) => [
        ...(current || []),
        project,
      ]);
      setActiveProjectId(project.id);
      setActiveChapterId("");
      setShowNewProject(false);
      setView("desk");
      setNewProjectForm({
        title: "",
        logline: "",
        genre: "悬疑 / 奇幻",
        tone: "克制、具体、留白",
      });
      setToast({
        tone: "success",
        message: "项目已创建。先确认故事正典，再开始下一章。",
      });
    },
    onError: (error) => {
      setToast({
        tone: "error",
        message:
          error instanceof Error
            ? error.message
            : "项目创建失败，请检查本地服务。",
      });
    },
  });

  const startGeneration = async () => {
    if (!activeProject) return;
    const selectedProvider =
      providers.find((item) => item.id === selectedProviderId) || provider;
    if (!selectedProvider?.id) {
      setShowGeneration(false);
      setView("settings");
      setToast({
        tone: "warning",
        message: "尚未添加模型。请先添加 Provider 并设置默认项。",
      });
      return;
    }
    const chapterCount =
      generationForm.mode === "next_chapter"
        ? Math.min(10, Math.max(1, Number(generationForm.chapter_count) || 1))
        : 1;
    const formForRun = { ...generationForm, chapter_count: chapterCount };
    try {
      await flushGenerationSettings(activeProject.id, formForRun);
    } catch (error) {
      setToast({
        tone: "warning",
        message:
          error instanceof Error
            ? `${error.message} 本次生成仍会携带当前填写的必须/禁止条件。`
            : "生成偏好暂未保存，本次生成仍会携带当前填写的必须/禁止条件。",
      });
    }
    setShowGeneration(false);
    const useCurrentChapter =
      formForRun.mode === "scene" || formForRun.mode === "rewrite";
    const targetChapterId = useCurrentChapter ? activeChapter?.id : undefined;
    const placeholder: GenerationJob = {
      id: uid("job"),
      project_id: activeProject.id,
      chapter_id: targetChapterId,
      chapter_count: chapterCount,
      batch_index: 1,
      batch_total: chapterCount,
      batch_remaining: Math.max(0, chapterCount - 1),
      status: "preparing_context",
      progress: 4,
      phase_label: "准备上下文",
      provider_name: selectedProvider.name,
    };
    setJob(placeholder);
    try {
      const remote = await createGeneration(activeProject.id, {
        ...formForRun,
        target_word_count: formForRun.word_target,
        chapter_id: targetChapterId,
        canon_version: activeProject.canon_version,
        provider_id: selectedProvider.id,
      });
      const normalized = normalizeJob(remote);
      setJob({
        ...normalized,
        chapter_count: normalized.chapter_count ?? chapterCount,
        batch_index: normalized.batch_index ?? normalized.chapter_index ?? 1,
        batch_total: normalized.batch_total ?? chapterCount,
        batch_remaining:
          normalized.batch_remaining ?? Math.max(0, chapterCount - 1),
      });
    } catch (error) {
      setJob(null);
      setToast({
        tone: "error",
        message: error instanceof Error ? error.message : "生成任务启动失败。",
      });
    }
  };

  const openReview = () => {
    if (!review) {
      setToast({ tone: "info", message: "当前没有待处理的审核包。" });
      return;
    }
    setShowReview(true);
  };

  useEffect(() => {
    const handlers: Record<string, () => void> = {
      "command-generate": () => setShowGeneration(true),
      "command-review": openReview,
      "command-save": () =>
        activeChapter && saveDraft(activeChapter, activeContent),
      "command-import": () => setShowImport(true),
      "command-settings": () => setView("settings"),
    };
    const listeners = Object.entries(handlers).map(([name, handler]) => {
      const listener = () => handler();
      window.addEventListener(name, listener);
      return [name, listener] as const;
    });
    return () =>
      listeners.forEach(([name, listener]) =>
        window.removeEventListener(name, listener),
      );
  }, [activeChapter, activeContent, openReview, saveDraft]);

  const applyReviewAction = async (
    action: "accept" | "reject" | "reaudit",
    extra: Record<string, unknown> = {},
  ) => {
    if (!review) return;
    try {
      const result = await reviewAction(review.id, action, extra);
      setReview(result);
    } catch (error) {
      setToast({
        tone: "error",
        message:
          error instanceof Error
            ? error.message
            : "审核操作失败，数据库没有发生变化。",
      });
      return;
    }
    if (action === "accept") {
      const acceptedRunId = job?.id;
      const acceptedProjectId = activeProjectId;
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      queryClient.invalidateQueries({ queryKey: ["canon", activeProjectId] });
      queryClient.invalidateQueries({
        queryKey: ["chapters", activeProjectId],
      });
      setToast({
        tone: "success",
        message: extra.force
          ? "已强制接受，理由和冲突已写入审计日志。"
          : "章节修订与正典变更已原子提交。",
      });
      setForceAcceptOpen(false);
      setShowReview(false);
      activeJobIdRef.current = "";
      setJob(null);
      void getLatestGeneration(acceptedProjectId)
        .then(async (next) => {
          if (
            !next ||
            next.id === acceptedRunId ||
            activeProjectIdRef.current !== acceptedProjectId
          ) {
            return;
          }
          const activeStatuses: string[] = [
            "queued",
            "running",
            "preparing_context",
            "planning",
            "drafting",
            "extracting",
            "auditing",
            "revising",
            "awaiting_review",
            "needs_retry",
            "failed",
          ];
          if (!activeStatuses.includes(next.status)) return;
          setJob(next);
          if (next.status === "awaiting_review" && next.review_bundle_id) {
            const nextReview = await getReview(next.review_bundle_id);
            if (activeProjectIdRef.current !== acceptedProjectId) return;
            setReview(nextReview);
            setActiveChapterId(nextReview.chapter_id);
            setShowReview(true);
          }
        })
        .catch((error) =>
          setToast({
            tone: "warning",
            message:
              error instanceof Error
                ? error.message
                : "下一章任务状态读取失败，请刷新任务条。",
          }),
        );
    } else if (action === "reject") {
      setToast({ tone: "info", message: "已拒绝审核包，正典版本保持不变。" });
      setShowReview(false);
      setJob(null);
    } else
      setToast({ tone: "info", message: "已重新审查，旧结果标记为失效。" });
  };

  const handleFile = async (file: File) => {
    if (!activeProject) return;
    try {
      const remotePreview = await previewImport(activeProject.id, file);
      setImportPreviewData(normalizePreview(remotePreview, file.name));
    } catch (error) {
      setToast({
        tone: "error",
        message: error instanceof Error ? error.message : "文件预览失败。",
      });
    }
  };

  const commitPreview = async () => {
    if (!activeProject || !importPreviewData) return;
    try {
      const imported = await commitImport(activeProject.id, importPreviewData);
      queryClient.setQueryData<Chapter[]>(
        ["chapters", activeProject.id],
        (current) =>
          [...(current || []), ...imported].sort((a, b) => a.number - b.number),
      );
      queryClient.invalidateQueries({ queryKey: ["projects"] });
    } catch (error) {
      setToast({
        tone: "error",
        message: error instanceof Error ? error.message : "导入提交失败。",
      });
      return;
    }
    setShowImport(false);
    setImportPreviewData(null);
    setToast({
      tone: "success",
      message: "导入稿已保存为确认基稿；模型提取的新设定仍需审核后入典。",
    });
  };

  const exportProject = async () => {
    if (!activeProject) return;
    try {
      const blob = await downloadExport(activeProject.id);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${activeProject.title || "项目"}-backup.zip`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      setToast({
        tone: "error",
        message:
          error instanceof Error
            ? error.message
            : "导出失败，请检查本地服务后重试。",
      });
    }
  };

  const retryCurrentJob = async () => {
    if (!job) return;
    try {
      setJob(await retryGeneration(job.id));
      setToast({ tone: "info", message: "已从最后一个持久化阶段继续任务。" });
    } catch (error) {
      setToast({
        tone: "error",
        message: error instanceof Error ? error.message : "任务重试失败。",
      });
    }
  };

  const rebuildMemory = async () => {
    if (!activeProject) return;
    try {
      const rebuilt = await rebuildProjectMemory(activeProject.id);
      queryClient.setQueryData<Project[]>(["projects"], (current) =>
        (current || []).map((item) =>
          item.id === rebuilt.id ? rebuilt : item,
        ),
      );
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: ["chapters", activeProject.id],
        }),
        queryClient.invalidateQueries({
          queryKey: ["canon", activeProject.id],
        }),
        queryClient.invalidateQueries({
          queryKey: ["story-map", activeProject.id],
        }),
      ]);
      setToast({
        tone: "success",
        message: "记忆索引已安全重建；受影响正典仍保持待复核状态。",
      });
    } catch (error) {
      setToast({
        tone: "error",
        message: error instanceof Error ? error.message : "记忆重建失败。",
      });
    }
  };

  const headerTitle =
    view === "library"
      ? "项目库"
      : view === "settings"
        ? "工作室设置"
        : activeProject?.title || "未选择项目";
  const headerKicker =
    view === "library"
      ? "藏书阁 / 私人卷宗"
      : view === "settings"
        ? "砚台 / 模型与生成"
        : "案头 / 故事正典";
  const overlayOpen =
    showNewProject ||
    showImport ||
    showGeneration ||
    showReview ||
    showCommand;

  useEffect(() => {
    const stage = workspaceStageRef.current;
    if (!stage) return;
    if (overlayOpen) stage.setAttribute("inert", "");
    else {
      stage.removeAttribute("inert");
      const returnTarget = lastWorkspaceFocusRef.current;
      if (returnTarget?.isConnected) {
        window.requestAnimationFrame(() => returnTarget.focus());
      }
    }
  }, [overlayOpen]);

  return (
    <div className="app-shell">
      <InkLandscape className="workspace-ink" />
      <InkInteractionLayer />
      <div
        className="workspace-stage"
        ref={workspaceStageRef}
        onFocusCapture={(event) => {
          lastWorkspaceFocusRef.current = event.target as HTMLElement;
        }}
      >
      <header className="topbar">
        <button
          className="brand-lockup"
          onClick={() => setView("library")}
          type="button"
          aria-label="返回小说项目库"
        >
          <span className="brand-mark">
            <BookOpen size={17} strokeWidth={1.8} />
          </span>
          <span className="brand-name">章回</span>
          <span className="brand-divider" />
          <span className="brand-caption">长篇叙事案头</span>
        </button>
        <div className="breadcrumb">
          <span>{headerKicker}</span>
          <ChevronRight size={13} />
          <strong>{headerTitle}</strong>
        </div>
        <div className="top-actions">
          {provider ? (
            <span className="provider-badge">
              <span className="status-dot green" /> {provider.name}
            </span>
          ) : (
            <span className="provider-badge provider-badge-empty">
              <span className="status-dot" /> 尚未添加模型
            </span>
          )}
          <button
            className="icon-button only-mobile"
            onClick={() => setMobileSidebar(true)}
            aria-label="打开章节树"
          >
            <Menu size={17} />
          </button>
          <button
            className="icon-button"
            onClick={() => setShowCommand(true)}
            aria-label="打开命令栏"
          >
            <Command size={17} />
          </button>
          <button className="icon-button" aria-label="通知">
            <Bell size={17} />
          </button>
          <button
            className="avatar"
            onClick={() => setAccountMenuOpen((open) => !open)}
            aria-label="打开账号菜单"
            aria-haspopup="menu"
            aria-expanded={accountMenuOpen}
          >
            {(session.user.display_name || session.user.username || session.user.email || "章").slice(0, 1).toUpperCase()}
          </button>
          {accountMenuOpen && (
            <div className="account-menu" role="menu">
              <div className="account-menu-head"><span className="account-menu-avatar">{(session.user.display_name || session.user.username || session.user.email || "章").slice(0, 1).toUpperCase()}</span><div><strong>{session.user.display_name || "未设置称呼"}</strong><small>{session.user.username ? `用户名：${session.user.username}` : session.user.email || "账号身份不可用"}</small></div></div>
              <button role="menuitem" onClick={() => { setAccountMenuOpen(false); onAccount(); }}><UserRound size={14} /> 账号与安全</button>
              <button role="menuitem" onClick={() => { setAccountMenuOpen(false); setView("settings"); }}><Settings size={14} /> 模型与生成</button>
              <div className="account-menu-rule" />
              <button role="menuitem" onClick={() => { setAccountMenuOpen(false); onLogout(); }}><LogOut size={14} /> 退出当前设备</button>
              <button role="menuitem" onClick={() => { setAccountMenuOpen(false); onLogoutAll(); }}><ShieldCheck size={14} /> 退出全部会话</button>
            </div>
          )}
        </div>
      </header>

      {job && (
        <JobStrip
          job={job}
          onOpen={() => setShowGeneration(true)}
          onReview={openReview}
          onRetry={retryCurrentJob}
        />
      )}

      <main className={`main-content view-${view}`}>
        {view === "library" && (
          <LibraryView
            projects={projects}
            activeProjectId={activeProjectId}
            onSelect={selectProject}
            onCreate={() => setShowNewProject(true)}
            onImport={() => {
              setView("desk");
              setShowImport(true);
            }}
            onSettings={() => setView("settings")}
          />
        )}
        {view === "settings" && providerQuery.isLoading && (
          <div className="auth-loading">
            <div className="auth-loading-mark"><ServerCog size={19} /></div>
            <span>正在读取你的私有 Provider…</span>
          </div>
        )}
        {view === "settings" && providerQuery.isError && (
          <div className="auth-loading" role="alert">
            <div className="auth-loading-mark"><CircleAlert size={19} /></div>
            <span>读取 Provider 失败，请检查网络后重试。</span>
            <button
              className="button button-secondary button-small"
              onClick={() => void providerQuery.refetch()}
            >
              <RefreshCw size={14} /> 重新读取
            </button>
          </div>
        )}
        {view === "settings" && !providerQuery.isLoading && !providerQuery.isError && (
          <SettingsView
            providers={providers}
            defaultProviderId={defaultProviderId}
            onRefresh={() => providerQuery.refetch()}
            onChangeDefault={(next) => {
              setProvider(next);
              setSelectedProviderId(next?.id || "");
              setDefaultProviderId(next?.id || "");
            }}
            onCreate={async (input) => {
              const created = await createProvider(input);
              await providerQuery.refetch();
              return created;
            }}
            onUpdate={async (id, input) => {
              const updated = await updateProvider(id, input);
              await providerQuery.refetch();
              if (updated.id === provider?.id) setProvider(updated);
              return updated;
            }}
            onDelete={async (id) => {
              await deleteProvider(id);
              await providerQuery.refetch();
              if (id === provider?.id) setProvider(null);
            }}
            onSetDefault={async (id) => {
              const saved = await setDefaultProvider(id);
              setProvider(saved);
              setSelectedProviderId(id);
              setDefaultProviderId(id);
              await providerQuery.refetch();
            }}
            onDeleteKey={async (id) => {
              const result = await deleteProviderKey(id);
              await providerQuery.refetch();
              if (id === provider?.id) {
                setProvider((current) =>
                  current ? { ...current, api_key_set: false } : current,
                );
              }
              return result;
            }}
            onExport={exportProject}
            onBack={() => setView(activeProject ? "desk" : "library")}
          />
        )}
        {view === "desk" && activeProject && (
          <WritingDesk
            project={activeProject}
            chapters={chapters}
            activeChapter={activeChapter}
            activeContent={activeContent}
            ledgerTab={ledgerTab}
            canon={canon}
            storyMap={storyMap}
            saveState={saveState}
            onChapter={(chapter) => {
              setActiveChapterId(chapter.id);
              setMobileSidebar(false);
            }}
            onContentChange={handleContentChange}
            onTab={setLedgerTab}
            onGenerate={() => {
              if (!providers.length) {
                setView("settings");
                setToast({
                  tone: "warning",
                  message: "尚未添加模型。请先配置你自己的 Provider。",
                });
                return;
              }
              setShowGeneration(true);
            }}
            providerGuide={
              !providers.length
                ? "configure"
                : !defaultProviderId
                  ? "choose"
                  : undefined
            }
            onReview={openReview}
            onImport={() => setShowImport(true)}
            onLibrary={() => setView("library")}
            onSettings={() => setView("settings")}
            onRebuild={rebuildMemory}
            onMobileLedger={() => setMobileLedger(true)}
            mobileSidebar={mobileSidebar}
            mobileLedger={mobileLedger}
            onCloseMobileSidebar={() => setMobileSidebar(false)}
            onCloseMobileLedger={() => setMobileLedger(false)}
          />
        )}
        {view === "desk" && !activeProject && (
          <EmptyDesk
            onCreate={() => setShowNewProject(true)}
            onImport={() => setShowImport(true)}
          />
        )}
      </main>
      </div>

      {showNewProject && (
        <NewProjectModal
          form={newProjectForm}
          setForm={setNewProjectForm}
          onClose={() => setShowNewProject(false)}
          onSubmit={() => createProjectMutation.mutate()}
          busy={createProjectMutation.isPending}
        />
      )}
      {showImport && (
        <ImportModal
          preview={importPreviewData}
          fileInputRef={fileInputRef}
          onFile={handleFile}
          onClose={() => {
            setShowImport(false);
            setImportPreviewData(null);
          }}
          onCommit={commitPreview}
          onChange={setImportPreviewData}
        />
      )}
      {showGeneration && (
        <GenerationDrawer
          form={generationForm}
          setForm={handleGenerationFormChange}
          provider={provider}
          providers={providers}
          selectedProviderId={selectedProviderId || provider?.id || ""}
          onProviderChange={(id) => {
            setSelectedProviderId(id);
            setProvider(providers.find((item) => item.id === id) || null);
          }}
          chapter={activeChapter}
          canonVersion={activeProject?.canon_version || 0}
          onClose={() => setShowGeneration(false)}
          onStart={startGeneration}
        />
      )}
      {showReview && review && (
        <ReviewModal
          review={review}
          chapter={reviewChapter}
          canonVersion={activeProject?.canon_version || 0}
          focusedSection={focusedReviewSection}
          setFocusedSection={setFocusedReviewSection}
          showDiff={showDiff}
          setShowDiff={setShowDiff}
          forceAcceptOpen={forceAcceptOpen}
          setForceAcceptOpen={setForceAcceptOpen}
          forceReason={forceReason}
          setForceReason={setForceReason}
          onClose={() => setShowReview(false)}
          onAction={applyReviewAction}
        />
      )}
      {showCommand && (
        <CommandPalette
          onClose={() => setShowCommand(false)}
          onAction={(action) => {
            setShowCommand(false);
            action();
          }}
        />
      )}
      {toast && (
        <Toast
          tone={toast.tone}
          message={toast.message}
          onClose={() => setToast(null)}
        />
      )}
    </div>
  );
}

export default function App() {
  const queryClient = useQueryClient();
  const [authView, setAuthView] = useState<AuthView>("login");
  const [accountView, setAccountView] = useState(false);
  const [authCleared, setAuthCleared] = useState(false);
  const authQuery = useQuery({
    queryKey: ["auth", "me"],
    queryFn: getCurrentUser,
    enabled: !authCleared,
    retry: false,
    staleTime: 5 * 60_000,
  });
  const session = authCleared ? undefined : authQuery.data;
  const deepLinkView = getAuthViewFromPath();

  const clearClientState = useCallback(() => {
    void queryClient.cancelQueries();
    queryClient.clear();
    setAuthCleared(true);
    setAccountView(false);
    setAuthView("login");
    window.dispatchEvent(new Event("novel-auth-cleared"));
  }, [queryClient]);

  useEffect(
    () => onAuthEvent(() => clearClientState()),
    [clearClientState],
  );

  const doLogout = async (all = false) => {
    try {
      if (all) await logoutAllSessions();
      else await logoutAccount();
    } finally {
      clearClientState();
    }
  };

  const renderAuthScreen = (initialView: AuthView) => (
    <AuthScreen
      initialView={initialView}
      onNavigate={setAuthView}
      onSessionCleared={clearClientState}
      onAuthenticated={(next) => {
        setAuthCleared(false);
        queryClient.setQueryData(["auth", "me"], next);
        setAccountView(false);
      }}
    />
  );

  // Verification/reset links must take precedence even when another account
  // already has a cached session in this browser.
  if (deepLinkView) {
    return renderAuthScreen(deepLinkView);
  }
  if (authQuery.isLoading && !authCleared) {
    return <div className="auth-loading"><div className="auth-loading-mark"><BookOpen size={19} /></div><span>正在打开你的故事正典…</span></div>;
  }
  if (!session?.user?.id) {
    return renderAuthScreen(authView);
  }
  if (accountView) {
    return <AccountSecurityView session={session} onBack={() => setAccountView(false)} onLogout={() => void doLogout(false)} onLogoutAll={() => void doLogout(true)} onSession={(next) => queryClient.setQueryData(["auth", "me"], next)} />;
  }
  return <Workspace session={session} onLogout={() => void doLogout(false)} onLogoutAll={() => void doLogout(true)} onAccount={() => setAccountView(true)} />;
}

function LibraryView({
  projects,
  activeProjectId,
  onSelect,
  onCreate,
  onImport,
  onSettings,
}: {
  projects: Project[];
  activeProjectId: string;
  onSelect: (project: Project) => void;
  onCreate: () => void;
  onImport: () => void;
  onSettings: () => void;
}) {
  return (
    <div className="library-page">
      <section className="library-intro">
        <div>
          <p className="eyebrow">私人藏书阁 / 故事正典</p>
          <h1>
            把下一章写在
            <br />
            <em>已经发生过的事上。</em>
          </h1>
          <p className="intro-copy">
            章回将正文、人物状态、剧情线和矛盾账本放在同一个可追溯的工作面上。拒绝的草稿不会悄悄改写故事。
          </p>
        </div>
        <div className="intro-side-note">
          <span className="note-line" />
          <span>当前工作原则</span>
          <strong>未审核，不入典</strong>
          <small>每次接受都会留下版本、来源和理由。</small>
        </div>
      </section>

      <div className="library-toolbar">
        <div className="toolbar-heading">
          <h2>我的小说</h2>
          <span>{projects.length} 个项目</span>
        </div>
        <div className="toolbar-actions">
          <button className="text-button" onClick={onSettings}>
            <Settings size={15} /> 工作室设置
          </button>
          <button className="button button-primary" onClick={onCreate}>
            <Plus size={16} /> 新建小说
          </button>
        </div>
      </div>

      <section className="project-grid" aria-label="小说项目">
        <button className="new-project-tile" onClick={onCreate}>
          <span className="tile-plus">
            <Plus size={24} strokeWidth={1.4} />
          </span>
          <strong>新建一个故事</strong>
          <span>从创意、设定或空白开始</span>
          <small>Ctrl / ⌘ + N</small>
        </button>
        {projects.map((project) => (
          <ProjectCard
            key={project.id}
            project={project}
            active={project.id === activeProjectId}
            onOpen={() => onSelect(project)}
            onImport={onImport}
          />
        ))}
      </section>

      <section className="library-footnote">
        <div className="footnote-icon">
          <ShieldCheck size={17} />
        </div>
        <div>
          <strong>本地优先，版本可回滚</strong>
          <span>
            项目和 API Key 分开保存；导出备份包含正典与修订，不包含密钥。
          </span>
        </div>
        <button className="text-button" onClick={onSettings}>
          查看安全设置 <ArrowRight size={14} />
        </button>
      </section>
    </div>
  );
}

function ProjectCard({
  project,
  active,
  onOpen,
  onImport,
}: {
  project: Project;
  active: boolean;
  onOpen: () => void;
  onImport: () => void;
}) {
  const progress = project.chapter_target
    ? Math.min(
        100,
        Math.round(
          ((project.current_chapter_id ? 3 : 0) / project.chapter_target) * 100,
        ),
      )
    : 0;
  return (
    <article className={`project-card ${active ? "is-active" : ""}`}>
      <button className="project-card-main" onClick={onOpen}>
        <div
          className={`project-mark mark-${project.source === "imported" ? "sage" : "blue"}`}
        >
          <span>{project.cover_mark || project.title.slice(0, 1)}</span>
          <small>{project.source === "imported" ? "导入稿" : "工作中"}</small>
        </div>
        <div className="project-card-copy">
          <div className="project-card-title">
            <h3>{project.title}</h3>
            {active && <span className="active-label">当前</span>}
          </div>
          <p>{project.logline || "还没有故事梗概。打开项目，写下第一句。"}</p>
          <div className="project-card-meta">
            <span>{project.genre || "未分类"}</span>
            <i />{" "}
            <span>
              {project.chapter_target
                ? `${project.chapter_target} 章计划`
                : "章节未规划"}
            </span>
          </div>
        </div>
      </button>
      <div className="project-card-bottom">
        <span>
          <span className="mini-dot" /> {shortTime(project.updated_at)}
        </span>
        <span>正典 v{project.canon_version || 0}</span>
        <button
          className="card-arrow"
          onClick={onOpen}
          aria-label={`打开${project.title}`}
        >
          <ArrowRight size={16} />
        </button>
      </div>
      <div className="project-progress">
        <span style={{ width: `${progress}%` }} />
      </div>
      <div className="project-card-menu">
        <button className="quiet-icon" onClick={onImport} aria-label="导入旧稿">
          <Upload size={14} />
        </button>
        <button className="quiet-icon" onClick={onOpen} aria-label="更多操作">
          <MoreHorizontal size={15} />
        </button>
      </div>
    </article>
  );
}

function EmptyDesk({
  onCreate,
  onImport,
}: {
  onCreate: () => void;
  onImport: () => void;
}) {
  return (
    <div className="empty-desk">
      <div className="empty-illustration">
        <FileText size={34} strokeWidth={1.1} />
      </div>
      <p className="eyebrow">还没有打开项目</p>
      <h1>让故事从一页空白开始。</h1>
      <p>
        新建项目，或把现有的 TXT / Markdown 旧稿带进来。导入内容会先停在审核区。
      </p>
      <div>
        <button className="button button-primary" onClick={onCreate}>
          <Plus size={15} /> 新建小说
        </button>
        <button className="button button-secondary" onClick={onImport}>
          <Upload size={15} /> 导入旧稿
        </button>
      </div>
    </div>
  );
}

interface WritingDeskProps {
  project: Project;
  chapters: Chapter[];
  activeChapter: Chapter | null;
  activeContent: string;
  ledgerTab: LedgerTab;
  canon: CanonItem[];
  storyMap: {
    threads?: PlotThread[];
    timeline?: TimelineEvent[];
    characters?: CanonItem[];
    foreshadowing?: CanonItem[];
  };
  saveState: "saved" | "saving" | "dirty" | "error";
  onChapter: (chapter: Chapter) => void;
  onContentChange: (content: string) => void;
  onTab: (tab: LedgerTab) => void;
  onGenerate: () => void;
  onReview: () => void;
  onImport: () => void;
  onLibrary: () => void;
  onSettings: () => void;
  onRebuild: () => void;
  onMobileLedger: () => void;
  mobileSidebar: boolean;
  mobileLedger: boolean;
  onCloseMobileSidebar: () => void;
  onCloseMobileLedger: () => void;
  providerGuide?: "configure" | "choose";
}

function WritingDesk({
  project,
  chapters,
  activeChapter,
  activeContent,
  ledgerTab,
  canon,
  storyMap,
  saveState,
  onChapter,
  onContentChange,
  onTab,
  onGenerate,
  onReview,
  onImport,
  onLibrary,
  onSettings,
  onRebuild,
  onMobileLedger,
  mobileSidebar,
  mobileLedger,
  onCloseMobileSidebar,
  onCloseMobileLedger,
  providerGuide,
}: WritingDeskProps) {
  return (
    <div className="desk-shell">
      <aside
        className={`chapter-sidebar ${mobileSidebar ? "mobile-open" : ""}`}
      >
        <div className="sidebar-top">
          <button className="back-to-library" onClick={onLibrary}>
            <ChevronLeft size={15} /> 项目库
          </button>
          <button
            className="quiet-icon only-mobile"
            onClick={onCloseMobileSidebar}
            aria-label="关闭章节树"
          >
            <X size={16} />
          </button>
        </div>
        <div className="project-mini-head">
          <div className="mini-project-mark">
            {project.cover_mark || project.title.slice(0, 1)}
          </div>
          <div>
            <span className="eyebrow">正在创作</span>
            <h2>{project.title}</h2>
          </div>
          <button className="quiet-icon" aria-label="项目选项">
            <MoreHorizontal size={16} />
          </button>
        </div>
        <div className="chapter-actions">
          <button
            className="button button-small button-secondary"
            onClick={onImport}
          >
            <Upload size={13} /> 导入
          </button>
          <button
            className="button button-small button-secondary"
            onClick={onSettings}
          >
            <Settings size={13} /> 设置
          </button>
          <button className="quiet-icon" aria-label="新建章节">
            <Plus size={15} />
          </button>
        </div>
        <div className="chapter-tree-label">
          <span>章节树</span>
          <span>{chapters.length} 章</span>
        </div>
        <ChapterTree
          chapters={chapters}
          activeId={activeChapter?.id}
          onChapter={onChapter}
        />
        <div className="sidebar-bottom">
          <div className="sidebar-rule" />
          <div className="sidebar-stat">
            <span>正典版本</span>
            <strong>v{project.canon_version || 0}</strong>
          </div>
          <div className="sidebar-stat">
            <span>当前进度</span>
            <strong>
              {formatWords(
                chapters.reduce(
                  (sum, chapter) =>
                    sum + (chapter.word_count || chapter.content?.length || 0),
                  0,
                ),
              )}{" "}
              字
            </strong>
          </div>
          <button className="text-button" onClick={onSettings}>
            <Keyboard size={14} /> 快捷键与偏好
          </button>
        </div>
      </aside>
      {mobileSidebar && (
        <button
          className="mobile-scrim"
          onClick={onCloseMobileSidebar}
          aria-label="关闭章节树"
        />
      )}

      <section className="editor-column">
        {project.needs_rebuild && (
          <div className="rebuild-banner">
            <CircleAlert size={15} />
            <span>
              <strong>旧章修改待重建</strong>{" "}
              从第一个受影响章节开始的摘要与正典已暂停继续生成。
            </span>
            <button className="text-button" onClick={onRebuild}>
              <RefreshCw size={13} /> 开始重建
            </button>
          </div>
        )}
        <EditorHeader
          project={project}
          chapter={activeChapter}
          saveState={saveState}
          onGenerate={onGenerate}
          onReview={onReview}
          onMobileLedger={onMobileLedger}
          generateDisabled={project.needs_rebuild}
          providerGuide={providerGuide}
        />
        {activeChapter ? (
          <EditorPaper
            key={activeChapter.id}
            chapter={activeChapter}
            content={activeContent}
            onChange={onContentChange}
          />
        ) : (
          <EmptyChapter />
        )}
        <PlotRail
          chapters={chapters}
          threads={storyMap.threads || []}
          activeChapter={activeChapter}
          onChapter={onChapter}
        />
      </section>

      <aside className={`ledger-sidebar ${mobileLedger ? "mobile-open" : ""}`}>
        <div className="ledger-mobile-head">
          <span>连续性账本</span>
          <button
            className="quiet-icon only-mobile"
            onClick={onCloseMobileLedger}
            aria-label="关闭账本"
          >
            <X size={16} />
          </button>
        </div>
        <LedgerSidebar
          project={project}
          activeChapter={activeChapter}
          ledgerTab={ledgerTab}
          onTab={onTab}
          canon={canon}
          storyMap={storyMap}
          onReview={onReview}
        />
      </aside>
      {mobileLedger && (
        <button
          className="mobile-scrim"
          onClick={onCloseMobileLedger}
          aria-label="关闭连续性账本"
        />
      )}
    </div>
  );
}

function ChapterTree({
  chapters,
  activeId,
  onChapter,
}: {
  chapters: Chapter[];
  activeId?: string;
  onChapter: (chapter: Chapter) => void;
}) {
  const volumes = chapters.reduce<Record<string, Chapter[]>>((map, chapter) => {
    const key = `${chapter.volume || 1} · ${chapter.volume_title || "未命名卷"}`;
    (map[key] ||= []).push(chapter);
    return map;
  }, {});
  if (!chapters.length)
    return (
      <div className="tree-empty">
        <FileText size={17} />
        <span>
          还没有章节
          <br />
          <small>先导入旧稿或生成大纲。</small>
        </span>
      </div>
    );
  return (
    <div className="chapter-tree">
      {Object.entries(volumes).map(([volume, volumeChapters]) => (
        <div className="volume-group" key={volume}>
          <div className="volume-heading">
            <ChevronDown size={13} />
            <span>{volume}</span>
            <small>{volumeChapters.length}</small>
          </div>
          {volumeChapters.map((chapter) => (
            <button
              key={chapter.id}
              className={`chapter-item ${chapter.id === activeId ? "is-selected" : ""}`}
              onClick={() => onChapter(chapter)}
            >
              <span className="chapter-number">
                {String(chapter.number).padStart(2, "0")}
              </span>
              <span className="chapter-title">
                {chapter.title || "未命名章节"}
              </span>
              <span
                className={`chapter-status status-${chapter.status || "draft"}`}
                title={statusLabels[chapter.status || "draft"]}
              >
                {chapter.status === "accepted" || chapter.status === "confirmed" ? (
                  <Check size={11} />
                ) : chapter.status === "review" ? (
                  <CircleAlert size={11} />
                ) : chapter.status === "planned" ? (
                  <Clock3 size={11} />
                ) : (
                  <PencilLine size={11} />
                )}
              </span>
            </button>
          ))}
        </div>
      ))}
    </div>
  );
}

function EditorHeader({
  project,
  chapter,
  saveState,
  onGenerate,
  onReview,
  onMobileLedger,
  generateDisabled,
  providerGuide,
}: {
  project: Project;
  chapter: Chapter | null;
  saveState: "saved" | "saving" | "dirty" | "error";
  onGenerate: () => void;
  onReview: () => void;
  onMobileLedger: () => void;
  generateDisabled?: boolean;
  providerGuide?: "configure" | "choose";
}) {
  return (
    <div className="editor-header">
      <div className="editor-title-block">
        <div className="editor-breadcrumb">
          <span>{project.title}</span>
          <ChevronRight size={12} />
          <span>{chapter?.volume_title || "第一卷"}</span>
          <ChevronRight size={12} />
          <span>第 {chapter?.number || "—"} 章</span>
        </div>
        <div className="editor-title-row">
          <h1>{chapter?.title || "选择一个章节"}</h1>
          {chapter && (
            <span className={`chapter-pill pill-${chapter.status || "draft"}`}>
              {statusLabels[chapter.status || "draft"]}
            </span>
          )}
        </div>
      </div>
      <div className="editor-actions">
        <span className={`save-indicator save-${saveState}`}>
          {saveState === "saving" ? (
            <Loader2 size={13} className="spin" />
          ) : saveState === "dirty" ? (
            <CircleAlert size={13} />
          ) : saveState === "error" ? (
            <CircleAlert size={13} />
          ) : (
            <CheckCircle2 size={13} />
          )}
          {saveState === "saving"
            ? "保存中"
            : saveState === "dirty"
              ? "有未保存改动"
              : saveState === "error"
                ? "待重试"
                : "已自动保存"}
        </span>
        <button
          className="button button-secondary button-compact"
          onClick={onReview}
        >
          <ShieldCheck size={14} /> 审核包
        </button>
        <button
          className="button button-primary button-compact"
          onClick={onGenerate}
          disabled={generateDisabled}
          title={
            generateDisabled
              ? "请先完成旧章重建"
              : providerGuide === "configure"
                ? "先添加你的私有 Provider"
                : providerGuide === "choose"
                  ? "尚未设置默认项，请为本次生成选择 Provider"
                  : "开始可恢复生成任务"
          }
        >
          {providerGuide === "configure" ? <ServerCog size={14} /> : <WandSparkles size={14} />}
          {providerGuide === "configure"
            ? "配置模型"
            : providerGuide === "choose"
              ? "选择本次模型"
              : "生成下一章"}
        </button>
        <button
          className="icon-button only-mobile"
          onClick={onMobileLedger}
          aria-label="打开连续性账本"
        >
          <PanelRight size={16} />
        </button>
      </div>
    </div>
  );
}

function EmptyChapter() {
  return (
    <div className="empty-chapter">
      <div className="empty-chapter-mark">
        <PencilLine size={23} />
      </div>
      <p className="eyebrow">等待章节</p>
      <h2>从章节树选择一页稿纸</h2>
      <p>或者打开生成抽屉，让规划、正文、审查在同一条可恢复的任务链上开始。</p>
    </div>
  );
}

function EditorPaper({
  chapter,
  content,
  onChange,
}: {
  chapter: Chapter;
  content: string;
  onChange: (content: string) => void;
}) {
  const lines = Math.max(1, content.split("\n").length);
  return (
    <div className="paper-wrap">
      <div className="paper-toolbar">
        <div className="paper-toolbar-left">
          <button className="paper-tool active">
            <PencilLine size={14} /> 正文
          </button>
          <button className="paper-tool">
            <Table2 size={14} /> 场景卡
          </button>
          <span className="toolbar-separator" />
          <span className="paper-meta">
            修订{" "}
            {chapter.revision_id ? chapter.revision_id.slice(-6) : "未提交"} ·{" "}
            {formatWords(content.length)} 字
          </span>
        </div>
        <div className="paper-toolbar-right">
          <button className="quiet-icon" aria-label="比较修订">
            <GitCompare size={15} />
          </button>
          <button className="quiet-icon" aria-label="更多编辑操作">
            <MoreHorizontal size={16} />
          </button>
        </div>
      </div>
      <div className="paper-editor">
        <div className="line-numbers" aria-hidden="true">
          {Array.from({ length: lines }, (_, index) => (
            <span key={index}>{String(index + 1).padStart(2, "0")}</span>
          ))}
        </div>
        <textarea
          value={content}
          onChange={(event) => onChange(event.target.value)}
          spellCheck={false}
          aria-label={`编辑第${chapter.number}章正文`}
          placeholder="从一个具体动作开始。正文会在审核通过后进入故事正典。"
        />
      </div>
      <div className="paper-footer">
        <span>
          {chapter.status === "accepted" || chapter.status === "confirmed" ? (
            <>
              <CheckCircle2 size={12} /> 当前修订已确认并进入正典
            </>
          ) : (
            <>
              <LockKeyhole size={12} /> 当前编辑内容是草稿，尚未进入正典
            </>
          )}
        </span>
        <span>
          段落 {content.split(/\n\s*\n/).filter(Boolean).length} ·{" "}
          {formatWords(content.length)} 字
        </span>
      </div>
    </div>
  );
}

function PlotRail({
  chapters,
  threads,
  activeChapter,
  onChapter,
}: {
  chapters: Chapter[];
  threads: PlotThread[];
  activeChapter: Chapter | null;
  onChapter: (chapter: Chapter) => void;
}) {
  const columns = chapters;
  return (
    <section className="plot-rail">
      <div className="plot-rail-header">
        <div>
          <span className="eyebrow">跨章节剧情线</span>
          <h2>线索轨道</h2>
        </div>
        <span className="plot-rail-caption">
          <span className="legend-dot seed" />
          埋设 <span className="legend-dot advance" />
          推进 <span className="legend-dot payoff" />
          回收
        </span>
        <button className="quiet-icon" aria-label="查看完整剧情图">
          <Network size={15} />
        </button>
      </div>
      <div className="rail-scroll">
        <div
          className="rail-grid"
          style={{
            gridTemplateColumns: `minmax(132px, 1.15fr) repeat(${columns.length}, minmax(68px, 1fr))`,
          }}
        >
          <div className="rail-corner">
            <span>剧情线</span>
            <small>章节位置</small>
          </div>
          {columns.map((chapter) => (
            <button
              className={`rail-chapter ${chapter.id === activeChapter?.id ? "is-active" : ""}`}
              key={chapter.id}
              onClick={() => onChapter(chapter)}
            >
              <small>CH.{String(chapter.number).padStart(2, "0")}</small>
              <span>{chapter.title || "未命名"}</span>
            </button>
          ))}
          {threads.slice(0, 4).map((thread) => (
            <div className="rail-row" key={thread.id}>
              <div className="rail-thread-name">
                <span
                  className="thread-color"
                  style={{ background: thread.color || "#2E7D8C" }}
                />
                <span>{thread.title}</span>
              </div>
              {columns.map((chapter) => {
                const point = thread.points?.find(
                  (item) => item.chapter_number === chapter.number,
                );
                return (
                  <div className="rail-cell" key={`${thread.id}-${chapter.id}`}>
                    {point && (
                      <span
                        className={`rail-point ${point.state}`}
                        title={point.label}
                      >
                        <span>
                          {point.state === "seed"
                            ? "埋"
                            : point.state === "advance"
                              ? "进"
                              : "收"}
                        </span>
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      </div>
      {threads.length === 0 && (
        <div className="rail-empty">
          <Lightbulb size={15} /> 生成第一条剧情线后，它会在这里贯穿整部小说。
        </div>
      )}
    </section>
  );
}

function LedgerSidebar({
  project,
  activeChapter,
  ledgerTab,
  onTab,
  canon,
  storyMap,
  onReview,
}: {
  project: Project;
  activeChapter: Chapter | null;
  ledgerTab: LedgerTab;
  onTab: (tab: LedgerTab) => void;
  canon: CanonItem[];
  storyMap: {
    threads?: PlotThread[];
    timeline?: TimelineEvent[];
    characters?: CanonItem[];
    foreshadowing?: CanonItem[];
  };
  onReview: () => void;
}) {
  const tabItems: Array<{
    key: LedgerTab;
    label: string;
    icon: typeof BookOpen;
    count?: number;
  }> = [
    { key: "canon", label: "正典", icon: LockKeyhole, count: canon.length },
    {
      key: "timeline",
      label: "时间线",
      icon: Clock3,
      count: storyMap.timeline?.length,
    },
    {
      key: "threads",
      label: "剧情线",
      icon: Network,
      count: storyMap.threads?.length,
    },
    {
      key: "foreshadowing",
      label: "伏笔",
      icon: Lightbulb,
      count: storyMap.foreshadowing?.length,
    },
  ];
  return (
    <div className="ledger-inner">
      <div className="ledger-heading">
        <div>
          <span className="eyebrow">CONTINUITY LEDGER</span>
          <h2>连续性账本</h2>
        </div>
        <button className="quiet-icon" aria-label="账本选项">
          <MoreHorizontal size={16} />
        </button>
      </div>
      <div className="ledger-summary">
        <div className="ledger-summary-top">
          <span>正典健康度</span>
          <strong>有 1 项待复核</strong>
        </div>
        <div className="health-track">
          <span style={{ width: "82%" }} />
        </div>
        <div className="ledger-summary-bottom">
          <span>
            <span className="status-dot green" />{" "}
            {project.canon_version
              ? `v${project.canon_version} 已确认`
              : "等待确认"}
          </span>
          <button className="summary-link" onClick={onReview}>
            查看审核包 <ArrowRight size={12} />
          </button>
        </div>
      </div>
      <nav className="ledger-tabs" aria-label="连续性账本分类">
        {tabItems.map(({ key, label, icon: Icon, count }) => (
          <button
            key={key}
            className={ledgerTab === key ? "is-active" : ""}
            onClick={() => onTab(key)}
          >
            <Icon size={14} />
            <span>{label}</span>
            {typeof count === "number" && <small>{count}</small>}
          </button>
        ))}
      </nav>
      <div className="ledger-content">
        {ledgerTab === "canon" && (
          <CanonPanel items={canon} activeChapter={activeChapter} />
        )}
        {ledgerTab === "timeline" && (
          <TimelinePanel
            events={storyMap.timeline || []}
            activeChapter={activeChapter}
          />
        )}
        {ledgerTab === "threads" && (
          <ThreadsPanel threads={storyMap.threads || []} />
        )}
        {ledgerTab === "foreshadowing" && (
          <ForeshadowingPanel items={storyMap.foreshadowing || []} />
        )}
      </div>
      <div className="ledger-footer">
        <span>
          <Search size={13} /> 所有来源可定位到原文
        </span>
        <button className="quiet-icon" aria-label="搜索账本">
          <Search size={15} />
        </button>
      </div>
    </div>
  );
}

function CanonPanel({
  items,
  activeChapter,
}: {
  items: CanonItem[];
  activeChapter: Chapter | null;
}) {
  const [filter, setFilter] = useState("all");
  const visible =
    filter === "all" ? items : items.filter((item) => item.category === filter);
  return (
    <div className="canon-panel">
      <div className="panel-filter-row">
        <div className="filter-pills">
          {[
            ["all", "全部"],
            ["character", "人物"],
            ["constraint", "硬约束"],
          ].map(([key, label]) => (
            <button
              key={key}
              className={filter === key ? "is-active" : ""}
              onClick={() => setFilter(key)}
            >
              {label}
            </button>
          ))}
        </div>
        <button className="quiet-icon" aria-label="添加正典">
          <Plus size={15} />
        </button>
      </div>
      {visible.length === 0 ? (
        <LedgerEmpty
          icon={<LockKeyhole size={16} />}
          title="还没有正典条目"
          detail="确认第一份审核包后，人物和规则会在这里出现。"
        />
      ) : (
        <div className="canon-list">
          {visible.map((item) => (
            <CanonRow item={item} key={item.id} activeChapter={activeChapter} />
          ))}
        </div>
      )}
      <button className="add-ledger-button">
        <Plus size={14} /> 手动添加一条设定
      </button>
    </div>
  );
}

function CanonRow({
  item,
  activeChapter,
}: {
  item: CanonItem;
  activeChapter: Chapter | null;
}) {
  const Icon = iconForCategory[item.category] || Tag;
  const source = item.source_ref || item.source;
  return (
    <article
      className={`canon-row ${item.status === "needs_review" ? "needs-review" : ""}`}
    >
      <div className="canon-row-top">
        <span className="canon-icon">
          <Icon size={14} />
        </span>
        <div className="canon-subject">
          <strong>{item.subject}</strong>
          {item.predicate && <span>{item.predicate}</span>}
        </div>
        {item.hard && (
          <span className="hard-mark" title="硬约束">
            <LockKeyhole size={11} />
          </span>
        )}
        <span className={`mini-status status-${item.status}`}>
          {statusLabels[item.status]}
        </span>
      </div>
      <p>{item.value}</p>
      {item.aliases?.length ? (
        <div className="alias-row">
          <Tag size={11} /> {item.aliases.join(" / ")}
        </div>
      ) : null}
      {source && <SourceChip source={source} activeChapter={activeChapter} />}
    </article>
  );
}

function TimelinePanel({
  events,
  activeChapter,
}: {
  events: TimelineEvent[];
  activeChapter: Chapter | null;
}) {
  return (
    <div className="timeline-panel">
      {events.length === 0 ? (
        <LedgerEmpty
          icon={<Clock3 size={16} />}
          title="时间线还是空的"
          detail="抽取事实后，事件会按故事内时间排序。"
        />
      ) : (
        <div className="timeline-list">
          {events.map((event) => (
            <article
              className={`timeline-row timeline-${event.status || "unknown"} ${event.chapter_id === activeChapter?.id ? "is-current" : ""}`}
              key={event.id}
            >
              <div className="timeline-marker" />
              <div className="timeline-copy">
                <div className="timeline-meta">
                  <span>{event.date_label || "时间未定"}</span>
                  {event.chapter_number && (
                    <span>
                      CH.{String(event.chapter_number).padStart(2, "0")}
                    </span>
                  )}
                </div>
                <strong>{event.title}</strong>
                <p>{event.description}</p>
              </div>
            </article>
          ))}
        </div>
      )}
      <button className="add-ledger-button">
        <Plus size={14} /> 标记一个时间节点
      </button>
    </div>
  );
}

function ThreadsPanel({ threads }: { threads: PlotThread[] }) {
  return (
    <div className="threads-panel">
      {threads.length === 0 ? (
        <LedgerEmpty
          icon={<Network size={16} />}
          title="还没有剧情线"
          detail="在生成抽屉里开启剧情规划，系统会自动建立轨道。"
        />
      ) : (
        threads.map((thread) => (
          <article className="thread-row" key={thread.id}>
            <div className="thread-row-head">
              <span
                className="thread-color"
                style={{ background: thread.color || "#2E7D8C" }}
              />
              <strong>{thread.title}</strong>
              <span className={`thread-status thread-${thread.status}`}>
                {statusLabels[thread.status || "active"]}
              </span>
            </div>
            <div className="thread-mini-track">
              {thread.points?.map((point) => (
                <span
                  className={`thread-mini-point ${point.state}`}
                  title={`第${point.chapter_number}章 · ${point.label}`}
                  key={`${point.chapter_number}-${point.state}`}
                />
              ))}
            </div>
            <p>
              <span>下一拍</span>
              {thread.next_beat || "尚未规划"}
            </p>
          </article>
        ))
      )}
    </div>
  );
}

function ForeshadowingPanel({ items }: { items: CanonItem[] }) {
  return (
    <div className="foreshadowing-panel">
      {items.length === 0 ? (
        <LedgerEmpty
          icon={<Lightbulb size={16} />}
          title="还没有伏笔"
          detail="每条未回收的线索都会在这里保留来源和状态。"
        />
      ) : (
        items.map((item) => (
          <article className="foreshadow-row" key={item.id}>
            <div className="foreshadow-icon">
              <Lightbulb size={14} />
            </div>
            <div>
              <div className="foreshadow-head">
                <strong>{item.subject}</strong>
                <span>{item.status === "confirmed" ? "已埋设" : "待复核"}</span>
              </div>
              <p>{item.value}</p>
              <span className="source-label">
                {item.source_ref?.chapter_title ||
                  item.source?.chapter_title ||
                  "来源待补"}
              </span>
            </div>
          </article>
        ))
      )}
    </div>
  );
}

function LedgerEmpty({
  icon,
  title,
  detail,
}: {
  icon: ReactNode;
  title: string;
  detail: string;
}) {
  return (
    <div className="ledger-empty">
      <span>{icon}</span>
      <strong>{title}</strong>
      <p>{detail}</p>
    </div>
  );
}

function SourceChip({
  source,
  activeChapter,
}: {
  source: SourceRef;
  activeChapter: Chapter | null;
}) {
  const isCurrent =
    source.chapter_id && source.chapter_id === activeChapter?.id;
  return (
    <button
      className={`source-chip ${isCurrent ? "is-current" : ""}`}
      title={source.quote || source.label || "查看原文位置"}
    >
      <FileText size={11} />
      <span>{source.chapter_title || source.label || "来源章节"}</span>
      {source.start !== undefined && <small>#{source.start}</small>}
      <ChevronRight size={11} />
    </button>
  );
}

function SettingsView({
  providers,
  defaultProviderId,
  onRefresh,
  onChangeDefault,
  onCreate,
  onUpdate,
  onDelete,
  onSetDefault,
  onDeleteKey,
  onExport,
  onBack,
}: {
  providers: ProviderProfile[];
  defaultProviderId: string;
  onRefresh: () => void;
  onChangeDefault: (provider: ProviderProfile | null) => void;
  onCreate: (provider: Partial<ProviderProfile>) => Promise<ProviderProfile>;
  onUpdate: (id: string, provider: Partial<ProviderProfile>) => Promise<ProviderProfile>;
  onDelete: (id: string) => Promise<void>;
  onSetDefault: (id: string) => Promise<void>;
  onDeleteKey: (id: string) => Promise<unknown>;
  onExport: () => void;
  onBack: () => void;
}) {
  const [selectedId, setSelectedId] = useState(defaultProviderId || providers[0]?.id || "");
  const [draft, setDraft] = useState<ProviderProfile | null>(null);
  const [isNew, setIsNew] = useState(false);
  const [busy, setBusy] = useState(false);
  const [testState, setTestState] = useState<"idle" | "testing" | "ok" | "error">("idle");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    if (!isNew && selectedId && !providers.some((item) => item.id === selectedId)) {
      setSelectedId(defaultProviderId || providers[0]?.id || "");
    }
  }, [defaultProviderId, isNew, providers, selectedId]);

  useEffect(() => {
    if (isNew) return;
    const selected = providers.find((item) => item.id === selectedId) || null;
    setDraft(selected ? { ...selected, api_key: "" } : null);
  }, [isNew, providers, selectedId]);

  const update = (key: keyof ProviderProfile, value: unknown) => {
    setDraft((current) => (current ? { ...current, [key]: value } : current));
  };
  const switchProtocol = (protocol: ProviderProfile["protocol"]) => {
    setDraft((current) => {
      if (!current) return current;
      const currentBase = current.base_url?.trim().replace(/\/$/, "") || "";
      const officialOpenAI = "https://api.openai.com/v1";
      const officialAnthropic = "https://api.anthropic.com/v1";
      return {
        ...current,
        protocol,
        ...(protocol === "anthropic_messages" &&
        (!currentBase || currentBase === officialOpenAI)
          ? { base_url: officialAnthropic, api_version: "2023-06-01" }
          : protocol !== "anthropic_messages" &&
              (!currentBase || currentBase === officialAnthropic)
            ? { base_url: officialOpenAI, api_version: undefined }
            : {}),
      };
    });
  };
  const beginNew = () => {
    setIsNew(true);
    setSelectedId("");
    setTestState("idle");
    setNotice("");
    setDraft({
      name: "",
      base_url: "https://api.openai.com/v1",
      protocol: "chat_completions",
      default_model: "",
      model_roles: {},
      context_length: 32768,
      timeout_ms: 60000,
      max_output_tokens: 4096,
      api_key: "",
    });
  };
  const save = async () => {
    if (!draft) return;
    if (!draft.name?.trim() || !draft.base_url?.trim() || !draft.default_model?.trim()) {
      setNotice("请填写名称、Base URL 和默认模型。" );
      return;
    }
    setBusy(true);
    setNotice("");
    try {
      const saved = isNew ? await onCreate(draft) : await onUpdate(draft.id || "", draft);
      setIsNew(false);
      setSelectedId(saved.id || "");
      setDraft({ ...saved, api_key: "" });
      onChangeDefault(saved.id === defaultProviderId ? saved : providers.find((item) => item.id === defaultProviderId) || null);
      setNotice("Provider 已保存。是否设为默认项由你决定。" );
      onRefresh();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Provider 保存失败。" );
    } finally {
      setBusy(false);
    }
  };
  const test = async () => {
    if (!draft) return;
    setTestState("testing");
    setNotice("");
    try {
      const result = await testProvider(draft);
      setTestState(result.ok ? "ok" : "error");
      setNotice(result.ok ? `连接成功 · ${result.model || draft.default_model}` : result.message || "连接未通过" );
    } catch (error) {
      setTestState("error");
      setNotice(error instanceof Error ? error.message : "连接失败，请检查 Base URL。" );
    }
  };
  const remove = async () => {
    if (!draft?.id || !window.confirm(`确定删除「${draft.name}」吗？`)) return;
    const removingDefault = draft.id === defaultProviderId;
    setBusy(true);
    try {
      await onDelete(draft.id);
      const remaining = providers.filter((item) => item.id !== draft.id);
      const next = remaining[0];
      setSelectedId(next?.id || "");
      setDraft(next ? { ...next, api_key: "" } : null);
      setNotice("Provider 已删除。" );
      onChangeDefault(
        removingDefault
          ? null
          : providers.find((item) => item.id === defaultProviderId) || null,
      );
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Provider 删除失败。" );
    } finally {
      setBusy(false);
    }
  };
  const setAccountDefault = async (providerId: string) => {
    if (!providerId) return;
    setBusy(true);
    try {
      await onSetDefault(providerId);
      setSelectedId(providerId);
      setNotice("已设为账户默认 Provider；生成抽屉仍可临时切换。" );
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "默认 Provider 设置失败。" );
    } finally {
      setBusy(false);
    }
  };
  const clearKey = async () => {
    if (!draft?.id || !window.confirm("删除后，调用此 Provider 需要重新输入密钥。继续吗？")) return;
    setBusy(true);
    try {
      await onDeleteKey(draft.id);
      setDraft({ ...draft, api_key_set: false, api_key: "" });
      setNotice("API Key 已从系统凭据库删除。" );
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "API Key 删除失败。" );
    } finally {
      setBusy(false);
    }
  };

  const roles = [
    ["planner", "剧情规划"],
    ["drafter", "正文写作"],
    ["extractor", "事实提取"],
    ["auditor", "连续性审查"],
    ["style_auditor", "风格审查"],
    ["reviser", "定向修订"],
  ] as const;
  return (
    <div className="settings-page">
      <div className="settings-top"><button className="back-to-library" onClick={onBack}><ChevronLeft size={15} /> 返回工作台</button><span className="settings-version">章回 0.2 · PRIVATE MODEL DESK</span></div>
      <div className="settings-layout">
        <aside className="settings-nav"><p className="eyebrow">工作室设置</p><button className="settings-nav-item is-active"><ServerCog size={15} /> 模型与生成</button><button className="settings-nav-item"><Keyboard size={15} /> 快捷键</button><button className="settings-nav-item"><ShieldCheck size={15} /> 安全与备份</button><button className="settings-nav-item"><Gauge size={15} /> 质量闸门</button></aside>
        <section className="settings-content">
           <div className="settings-heading"><div><p className="eyebrow">PRIVATE PROVIDERS / BYOK</p><h1>模型与生成</h1><p>Provider 只属于当前账号。密钥通过系统凭据库隔离保存，永远不会写入小说、任务快照或项目导出。</p><p className="settings-default-guide"><strong>账户默认 Provider</strong> 决定未特别指定时使用哪套连接；每个 Provider 的“内置默认模型”和六角色映射属于它自己的配置，两者互不替代。</p></div><span className="settings-provider-status"><span className="status-dot green" /> {defaultProviderId ? "账户默认已设置" : "尚未设置账户默认"}</span></div>
          <div className="provider-settings-grid">
             <div className="settings-card provider-list-card"><div className="settings-card-head"><div><h2>我的 Provider</h2><p>{providers.length ? `${providers.length} 个私有配置` : "尚未添加模型"}</p></div><button className="button button-secondary button-small" onClick={beginNew}><PlusCircle size={14} /> 新增</button></div>{providers.length ? <div className="provider-list">{providers.map((item) => { const isAccountDefault = defaultProviderId ? item.id === defaultProviderId : Boolean(item.is_default); return <div className="provider-list-row" key={item.id}><button className={`provider-list-item ${item.id === selectedId && !isNew ? "is-selected" : ""}`} onClick={() => { setIsNew(false); setSelectedId(item.id || ""); }}><span className="provider-list-icon"><ServerCog size={15} /></span><span><strong>{item.name}</strong><small>{item.protocol === "anthropic_messages" ? "Anthropic Messages" : item.protocol === "responses" ? "Responses API" : "Chat Completions"}</small></span>{isAccountDefault && <em>账户默认</em>}<ChevronRight size={14} /></button>{item.id && <button className={`provider-default-action ${isAccountDefault ? "is-default" : ""}`} onClick={() => void setAccountDefault(item.id || "")} disabled={busy || isAccountDefault}>{isAccountDefault ? <><CheckCircle2 size={13} /> 当前账户默认</> : <><CheckCircle2 size={13} /> 设为账户默认</>}</button>}</div>; })}</div> : <div className="provider-empty"><div className="provider-empty-seal"><ServerCog size={20} /></div><strong>尚未添加模型</strong><p>先添加一个你自己的 Provider，章回不会自动创建内置配置或共享密钥。</p><button className="button button-primary" onClick={beginNew}><Plus size={14} /> 添加第一个 Provider</button></div>}</div>
            {draft ? <div className="settings-card provider-editor-card"><div className="settings-card-head"><div><p className="eyebrow">{isNew ? "NEW PROFILE" : "EDIT PROFILE"}</p><h2>{isNew ? "新增 Provider" : draft.name || "编辑 Provider"}</h2><p>Provider 内默认模型只属于这套连接；账户默认 Provider 需单独点击“设为账户默认”。生成抽屉可以临时切换。</p></div>{!isNew && draft.id === defaultProviderId && <span className="connection-state"><span className="status-dot green" /> 账户默认使用中</span>}</div><div className="form-grid"><label className="field"><span>显示名称</span><input value={draft.name} onChange={(event) => update("name", event.target.value)} placeholder="例如：我的 Claude" /></label><label className="field"><span>协议模式</span><select value={draft.protocol || "chat_completions"} onChange={(event) => switchProtocol(event.target.value as ProviderProfile["protocol"])}><option value="chat_completions">OpenAI Chat Completions</option><option value="responses">OpenAI Responses</option><option value="anthropic_messages">Anthropic Messages</option></select></label><label className="field field-wide"><span>Base URL</span><input value={draft.base_url} onChange={(event) => update("base_url", event.target.value)} placeholder={draft.protocol === "anthropic_messages" ? "https://api.anthropic.com/v1" : "https://api.openai.com/v1"} /></label>{draft.protocol === "anthropic_messages" && <label className="field"><span>Anthropic API 版本</span><input value={draft.api_version || "2023-06-01"} onChange={(event) => update("api_version", event.target.value)} placeholder="2023-06-01" /></label>}<label className="field"><span>Provider 默认模型 <small>此连接的内部回退</small></span><input value={draft.default_model || ""} onChange={(event) => update("default_model", event.target.value)} placeholder={draft.protocol === "anthropic_messages" ? "claude-sonnet-4-5" : "local-storyteller"} /></label><label className="field"><span>上下文长度</span><input type="number" min="1024" value={draft.context_length || 32768} onChange={(event) => update("context_length", Number(event.target.value))} /></label><label className="field"><span>最大输出 tokens</span><input type="number" min="256" value={draft.max_output_tokens || 4096} onChange={(event) => update("max_output_tokens", Number(event.target.value))} /></label><label className="field"><span>请求超时（毫秒）</span><input type="number" min="1000" value={draft.timeout_ms || 60000} onChange={(event) => update("timeout_ms", Number(event.target.value))} /></label><label className="field field-wide"><span>API Key <small>{draft.api_key_set ? "系统凭据库中已有密钥；留空即保留" : "只在需要时填写"}</small></span><input type="password" autoComplete="new-password" value={draft.api_key || ""} onChange={(event) => update("api_key", event.target.value)} placeholder={draft.api_key_set ? "••••••••（已安全保存）" : "粘贴你的 API Key"} /></label>{draft.protocol === "anthropic_messages" && <label className="field field-wide"><span>Anthropic Workspace ID <small>可选</small></span><input value={draft.anthropic_workspace_id || ""} onChange={(event) => update("anthropic_workspace_id", event.target.value)} placeholder="workspace_…" /></label>}</div><div className="form-actions"><button className="button button-secondary" onClick={test} disabled={busy || testState === "testing"}><RefreshCw size={14} className={testState === "testing" ? "spin" : ""} /> {testState === "testing" ? "测试中…" : "测试连接"}</button><button className="button button-primary" onClick={save} disabled={busy}><Save size={14} /> {busy ? "保存中…" : "保存配置"}</button>{!isNew && draft.id && <button className="button button-quiet-danger" onClick={remove} disabled={busy}><Trash2 size={14} /> 删除</button>}</div>{notice && <p className={`settings-notice ${testState === "error" ? "is-error" : ""}`} role="status">{notice}</p>}{!isNew && <div className="provider-editor-footer"><button className="text-button" onClick={() => void setAccountDefault(draft.id || "")} disabled={busy || draft.id === defaultProviderId}><CheckCircle2 size={14} /> {draft.id === defaultProviderId ? "当前账户默认 Provider" : "设为账户默认"}</button>{draft.api_key_set && <button className="text-button text-danger" onClick={clearKey} disabled={busy}><Trash2 size={14} /> 删除已保存密钥</button>}</div>}</div> : <div className="settings-card provider-editor-empty"><p className="eyebrow">SELECT A PROFILE</p><h2>选择一个 Provider 开始</h2><p>左侧列表会显示本账号的模型配置。没有默认 Provider 时，生成按钮会明确引导你完成设置。</p><button className="button button-secondary" onClick={beginNew}><Plus size={14} /> 新增 Provider</button></div>}
          </div>
          {draft && <div className="settings-card"><div className="settings-card-head"><div><h2>Provider 内角色模型映射</h2><p>六个角色可以共用本 Provider 的默认模型，也可以分别指定；它们不改变账户默认 Provider。</p></div><span className="role-count">6 个角色</span></div><div className="role-grid">{roles.map(([key, label]) => <label className="role-field" key={key}><span>{label}</span><input value={draft.model_roles?.[key] || ""} placeholder={draft.default_model || "沿用 Provider 内默认模型"} onChange={(event) => setDraft({ ...draft, model_roles: { ...(draft.model_roles || {}), [key]: event.target.value } })} /><small>{key}</small></label>)}</div></div>}
          <div className="settings-card backup-card"><div className="backup-icon"><FileArchive size={18} /></div><div><h2>完整备份</h2><p>导出正文、正典、修订、原始导入文件和 schema 版本。密钥永远不会包含在内。</p></div><button className="button button-secondary" onClick={onExport}><Download size={14} /> 导出项目 ZIP</button></div>
        </section>
      </div>
    </div>
  );
}

function NewProjectModal({
  form,
  setForm,
  onClose,
  onSubmit,
  busy,
}: {
  form: { title: string; logline: string; genre: string; tone: string };
  setForm: (form: {
    title: string;
    logline: string;
    genre: string;
    tone: string;
  }) => void;
  onClose: () => void;
  onSubmit: () => void;
  busy: boolean;
}) {
  return (
    <Modal
      title="新建小说"
      kicker="START WITH A CANON"
      onClose={onClose}
      size="small"
    >
      <form
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit();
        }}
      >
        <p className="modal-lead">
          先写下故事的坐标。创建后可以继续补充人物、规则和第一卷大纲。
        </p>
        <label className="field">
          <span>小说名称</span>
          <input
            autoFocus
            value={form.title}
            onChange={(event) =>
              setForm({ ...form, title: event.target.value })
            }
            placeholder="例如：雾中灯塔"
            required
          />
        </label>
        <label className="field">
          <span>一句话梗概</span>
          <textarea
            rows={3}
            value={form.logline}
            onChange={(event) =>
              setForm({ ...form, logline: event.target.value })
            }
            placeholder="谁在什么地方，为了什么不得不行动？"
          />
        </label>
        <div className="form-grid form-grid-two">
          <label className="field">
            <span>类型</span>
            <input
              value={form.genre}
              onChange={(event) =>
                setForm({ ...form, genre: event.target.value })
              }
            />
          </label>
          <label className="field">
            <span>文风</span>
            <input
              value={form.tone}
              onChange={(event) =>
                setForm({ ...form, tone: event.target.value })
              }
            />
          </label>
        </div>
        <div className="modal-actions">
          <button
            type="button"
            className="button button-secondary"
            onClick={onClose}
          >
            取消
          </button>
          <button
            type="submit"
            className="button button-primary"
            disabled={busy}
          >
            {busy ? <Loader2 size={14} className="spin" /> : <Plus size={14} />}{" "}
            {busy ? "创建中" : "创建项目"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function ImportModal({
  preview,
  fileInputRef,
  onFile,
  onClose,
  onCommit,
  onChange,
}: {
  preview: ImportPreview | null;
  fileInputRef: RefObject<HTMLInputElement>;
  onFile: (file: File) => void;
  onClose: () => void;
  onCommit: () => void;
  onChange: (preview: ImportPreview) => void;
}) {
  const renumber = (chapters: ImportChapterPreview[]) =>
    chapters.map((chapter, index) => ({ ...chapter, number: index + 1 }));

  const splitChapter = (index: number) => {
    if (!preview) return;
    const chapter = preview.chapters[index];
    const middle = Math.floor(chapter.content.length / 2);
    const after = chapter.content.indexOf("\n\n", middle);
    const before = chapter.content.lastIndexOf("\n\n", middle);
    const splitAt = after >= 0 ? after + 2 : before >= 0 ? before + 2 : middle;
    if (splitAt <= 0 || splitAt >= chapter.content.length) return;
    const tail = chapter.content.slice(splitAt);
    const leadingWhitespace = tail.length - tail.trimStart().length;
    const first = {
      ...chapter,
      title: `${chapter.title}（上）`,
      content: chapter.content.slice(0, splitAt).trimEnd(),
      source_end: (chapter.source_start || 0) + splitAt,
    };
    const second = {
      ...chapter,
      key: `${chapter.key}-split-${Date.now()}`,
      title: `${chapter.title}（下）`,
      content: tail.trimStart(),
      source_start: (chapter.source_start || 0) + splitAt + leadingWhitespace,
    };
    onChange({
      ...preview,
      chapters: renumber([
        ...preview.chapters.slice(0, index),
        first,
        second,
        ...preview.chapters.slice(index + 1),
      ]),
    });
  };

  const mergeWithPrevious = (index: number) => {
    if (!preview || index <= 0) return;
    const previous = preview.chapters[index - 1];
    const current = preview.chapters[index];
    const merged = {
      ...previous,
      content: `${previous.content.trimEnd()}\n\n${current.content.trimStart()}`,
      selected: previous.selected || current.selected,
      source_end: current.source_end,
    };
    onChange({
      ...preview,
      chapters: renumber([
        ...preview.chapters.slice(0, index - 1),
        merged,
        ...preview.chapters.slice(index + 1),
      ]),
    });
  };

  return (
    <Modal
      title="导入旧稿"
      kicker="IMPORT / PREVIEW FIRST"
      onClose={onClose}
      size="large"
    >
      <input
        ref={fileInputRef}
        type="file"
        accept=".txt,.md,.markdown,text/plain,text/markdown"
        className="visually-hidden"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) onFile(file);
        }}
      />
      {!preview ? (
        <button
          className="dropzone"
          onClick={() => fileInputRef.current?.click()}
        >
          <Upload size={23} />
          <strong>选择 TXT 或 Markdown</strong>
          <span>
            支持 UTF-8、BOM、GB18030；会按“第 X 章 / 序章 / 番外”尝试拆章。
          </span>
          <small>
            拆章提交会作为你确认的既有正文；模型新提取的设定仍需审核。
          </small>
        </button>
      ) : (
        <div className="import-preview">
          <div className="import-file-head">
            <div className="file-badge">
              <FileText size={17} />
            </div>
            <div>
              <strong>{preview.file_name}</strong>
              <span>
                {preview.encoding || "UTF-8"} · {preview.chapters.length}{" "}
                个章节候选
              </span>
            </div>
            <button
              className="text-button"
              onClick={() => fileInputRef.current?.click()}
            >
              <RefreshCw size={13} /> 更换文件
            </button>
          </div>
          <div className="import-notice">
            <CircleHelp size={15} />
            <span>
              拆章结果只是预览。你可以取消选择、改名、拆分或合并；提交即确认这些原文为续写基稿。
            </span>
          </div>
          <div className="import-chapter-list">
            {preview.chapters.map((chapter, index) => (
              <div
                className={`import-chapter-row ${chapter.selected ? "" : "is-muted"}`}
                key={chapter.key}
              >
                <button
                  className={`check-box ${chapter.selected ? "is-checked" : ""}`}
                  onClick={() =>
                    onChange({
                      ...preview,
                      chapters: preview.chapters.map((item, itemIndex) =>
                        itemIndex === index
                          ? { ...item, selected: !item.selected }
                          : item,
                      ),
                    })
                  }
                  aria-label={chapter.selected ? "取消选择章节" : "选择章节"}
                >
                  {chapter.selected && <Check size={13} />}
                </button>
                <span className="import-chapter-number">
                  {String(chapter.number).padStart(2, "0")}
                </span>
                <input
                  value={chapter.title}
                  onChange={(event) =>
                    onChange({
                      ...preview,
                      chapters: preview.chapters.map((item, itemIndex) =>
                        itemIndex === index
                          ? { ...item, title: event.target.value }
                          : item,
                      ),
                    })
                  }
                />
                <span className="import-word-count">
                  {formatWords(chapter.content.length)} 字
                </span>
                {index > 0 && (
                  <button
                    className="quiet-icon"
                    aria-label="与上一章合并"
                    title="与上一章合并"
                    onClick={() => mergeWithPrevious(index)}
                  >
                    <Layers3 size={14} />
                  </button>
                )}
                <button
                  className="quiet-icon"
                  aria-label="拆分章节"
                  title="在中间段落处拆分"
                  onClick={() => splitChapter(index)}
                >
                  <SplitSquareHorizontal size={14} />
                </button>
              </div>
            ))}
          </div>
          <div className="import-footer">
            <span>
              <CheckCircle2 size={13} /> 已选择{" "}
              {preview.chapters.filter((chapter) => chapter.selected).length} /{" "}
              {preview.chapters.length}
            </span>
            <div>
              <button className="button button-secondary" onClick={onClose}>
                取消
              </button>
              <button
                className="button button-primary"
                disabled={!preview.chapters.some((chapter) => chapter.selected)}
                onClick={onCommit}
              >
                <Import size={14} /> 确认并导入
              </button>
            </div>
          </div>
        </div>
      )}
    </Modal>
  );
}

function GenerationDrawer({
  form,
  setForm,
  provider,
  providers,
  selectedProviderId,
  onProviderChange,
  canonVersion,
  onClose,
  onStart,
}: {
  form: GenerationFormState;
  setForm: (form: GenerationFormState) => void;
  provider: ProviderProfile | null;
  providers: ProviderProfile[];
  selectedProviderId: string;
  onProviderChange: (id: string) => void;
  chapter: Chapter | null;
  canonVersion: number;
  onClose: () => void;
  onStart: () => void;
}) {
  const dialogRef = useDialogFocus<HTMLElement>();
  return (
    <div className="drawer-layer">
      <button
        className="drawer-scrim"
        onClick={onClose}
        aria-label="关闭生成抽屉"
      />
      <aside
        className="generation-drawer"
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label="生成下一章"
        tabIndex={-1}
      >
        <div className="drawer-head">
          <div>
            <p className="eyebrow">GENERATION RUN / NEW</p>
            <h2>生成下一章</h2>
          </div>
          <button className="quiet-icon" onClick={onClose} aria-label="关闭">
            <X size={17} />
          </button>
        </div>
        <div className="drawer-body">
          <div className="drawer-callout">
            <WandSparkles size={16} />
            <div>
              <strong>正典冻结点</strong>
              <span>
                将使用 v{canonVersion} 上下文；本次生成不会修改已确认记忆。
              </span>
            </div>
          </div>
          <label className="field">
            <span>
              本次使用的 Provider <small>账户默认只是起点，可临时切换</small>
            </span>
            {providers.length ? (
              <select value={selectedProviderId} onChange={(event) => onProviderChange(event.target.value)} required>
                <option value="">选择一个 Provider</option>
                {providers.map((item) => <option key={item.id} value={item.id}>{item.name}{item.is_default ? " · 账户默认" : ""}{item.id === selectedProviderId ? " · 本次选择" : ""}</option>)}
              </select>
            ) : (
              <div className="drawer-provider-empty"><ServerCog size={14} /><span>尚未添加模型。请先到“模型与生成”添加 Provider。</span></div>
            )}
          </label>
          <label className="field">
            <span>生成目标</span>
            <select
              value={form.mode}
              onChange={(event) => {
                const mode = event.target.value;
                setForm({
                  ...form,
                  mode,
                  chapter_count: mode === "next_chapter" ? form.chapter_count : 1,
                });
              }}
            >
              <option value="next_chapter">下一章正文</option>
              <option value="outline">先生成章节规划</option>
              <option value="scene">补写当前场景</option>
              <option value="rewrite">按审核意见定向修订</option>
            </select>
          </label>
          <label className="field">
            <span>
              连续生成章数 <output>{form.chapter_count} 章</output>
            </span>
            <select
              value={form.mode === "next_chapter" ? form.chapter_count : 1}
              onChange={(event) =>
                setForm({
                  ...form,
                  chapter_count: Number(event.target.value),
                })
              }
              disabled={form.mode !== "next_chapter"}
              aria-describedby="generation-batch-note"
            >
              {Array.from({ length: 10 }, (_, index) => index + 1).map((count) => (
                <option key={count} value={count}>
                  {count} 章
                </option>
              ))}
            </select>
          </label>
          <div className="generation-batch-panel" id="generation-batch-note">
            <div className="generation-batch-copy">
              <span className="generation-batch-icon"><Layers3 size={14} /></span>
              <div>
                <strong>
                  {form.mode === "next_chapter"
                    ? `本批次将逐章生成 ${form.chapter_count} 章`
                    : "当前模式按单章执行"}
                </strong>
                <p>
                  每章都要先通过审核并入典，系统才会继续排队下一章；任一章拒绝或需重试，后续会暂停。
                </p>
              </div>
            </div>
            <div className="generation-batch-tickets" role="list" aria-label="本批次章节序列">
              {Array.from(
                { length: form.mode === "next_chapter" ? form.chapter_count : 1 },
                (_, index) => (
                  <span className={`generation-batch-ticket ${index === 0 ? "is-current" : ""}`} key={index} role="listitem">
                    <small>章</small>{String(index + 1).padStart(2, "0")}
                  </span>
                ),
              )}
            </div>
          </div>
          <label className="field">
            <span>
              目标字数 <output>{formatWords(form.word_target)} 字</output>
            </span>
            <input
              type="range"
              min="800"
              max="8000"
              step="100"
              value={form.word_target}
              onChange={(event) =>
                setForm({ ...form, word_target: Number(event.target.value) })
              }
            />
          </label>
          <div className="range-scale">
            <span>短场景</span>
            <span>标准章节</span>
            <span>长章节</span>
          </div>
          <label className="field">
            <span>
              必须发生 <small>可选 · 自动保存到当前小说，刷新后仍保留；手动清空才移除</small>
            </span>
            <textarea
              rows={3}
              value={form.must}
              onChange={(event) =>
                setForm({ ...form, must: event.target.value })
              }
              placeholder="例如：第二轮月出现；林澈不能离开灯塔"
            />
          </label>
          <label className="field">
            <span>
              禁止发生 <small>可选 · 自动保存到当前小说，刷新后仍保留；手动清空才移除</small>
            </span>
            <textarea
              rows={3}
              value={form.must_not}
              onChange={(event) =>
                setForm({ ...form, must_not: event.target.value })
              }
              placeholder="例如：不要揭示出生年份真相；不要让旧灯守死亡"
            />
          </label>
          <div className="drawer-section-heading">
            <span>质量优先流程</span>
            <span className="role-count">
              最多 {form.revision_rounds} 轮修订
            </span>
          </div>
          <div className="generation-pipeline">
            {jobPhases.slice(0, 6).map((phase, index) => (
              <div className="pipeline-step" key={phase.key}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <strong>{phase.label}</strong>
              </div>
            ))}
          </div>
          {provider ? <div className="provider-note"><span className="status-dot green" /><div><strong>{provider.name}</strong><small>{provider.default_model || "未配置模型"} · {provider.protocol === "anthropic_messages" ? "Anthropic Messages" : provider.protocol === "responses" ? "Responses API" : "Chat Completions"}</small></div></div> : <div className="provider-note provider-note-warning"><CircleAlert size={15} /><div><strong>尚未添加模型</strong><small>生成任务需要你自己的 Provider，不会自动回退到其他模型。</small></div></div>}
        </div>
        <div className="drawer-footer">
          <div>
            <span className="context-budget">
              <Gauge size={13} /> 预计上下文 18.4k /{" "}
              {formatWords(provider?.context_length || 32768)} tokens
            </span>
            <span className="context-hint">硬约束和当前状态不会被裁剪</span>
          </div>
          <button
            className="button button-primary button-wide"
            onClick={onStart}
            disabled={!provider}
          >
            <Play size={15} fill="currentColor" /> 开始生成
          </button>
        </div>
      </aside>
    </div>
  );
}

function JobStrip({
  job,
  onOpen,
  onReview,
  onRetry,
}: {
  job: GenerationJob;
  onOpen: () => void;
  onReview: () => void;
  onRetry: () => void;
}) {
  const complete =
    job.status === "awaiting_review" || job.status === "completed";
  const stalled = job.status === "needs_retry" || job.status === "failed";
  const batchTotal = Math.max(1, job.batch_total ?? job.chapter_count ?? 1);
  const batchIndex = Math.min(
    batchTotal,
    Math.max(1, job.batch_index ?? job.chapter_index ?? 1),
  );
  const batchRemaining = Math.max(
    0,
    job.batch_remaining ?? Math.max(0, batchTotal - batchIndex),
  );
  const hasBatch = batchTotal > 1;
  return (
    <div
      className={`job-strip ${complete ? "job-ready" : stalled ? "job-stalled" : "job-running"}`}
    >
      <div className="job-strip-main">
        <span className={`job-icon ${complete ? "ready" : ""}`}>
          {complete ? (
            <CheckCircle2 size={15} />
          ) : stalled ? (
            <CircleAlert size={15} />
          ) : (
            <Loader2 size={15} className="spin" />
          )}
        </span>
        <div>
          <strong>
            {complete
              ? "审核包已准备好"
              : stalled
                ? "任务需要人工重试"
                : `正在${job.phase_label || "生成"}`}
          </strong>
          <span>
            {complete
              ? "正文仍是草稿，正典未改变"
              : stalled
                ? job.error || "远程结果不确定，系统没有自动提交"
                : `${job.provider_name || "Provider"} · 可安全关闭窗口，进度会持久化`}
          </span>
          {hasBatch && (
            <small className="job-batch-progress">
              第 {batchIndex}/{batchTotal} 章 · 审核通过后继续
              {batchRemaining ? ` · 余 ${batchRemaining} 章` : ""}
            </small>
          )}
        </div>
      </div>
      <div className="job-strip-progress">
        <div className="job-progress-label">
          <span>{Math.round(job.progress || 0)}%</span>
          <span>
            {job.phase_label || statusLabels[job.status] || job.status}
          </span>
        </div>
        <div
          className="job-progress-track"
          role="progressbar"
          aria-label={job.phase_label || "小说生成进度"}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(job.progress || 0)}
        >
          <span style={{ width: `${job.progress || 0}%` }} />
        </div>
      </div>
      <div className="job-strip-actions">
        {!stalled && (
          <button className="text-button" onClick={onOpen}>
            {complete ? "查看任务" : "生成设置"}
          </button>
        )}
        {stalled && (
          <button
            className="button button-secondary button-small"
            onClick={onRetry}
          >
            <RefreshCw size={13} /> 从断点重试
          </button>
        )}
        {complete && (
          <button
            className="button button-primary button-small"
            onClick={onReview}
          >
            <ShieldCheck size={13} /> 打开审核包
          </button>
        )}
      </div>
    </div>
  );
}

function ReviewModal({
  review,
  chapter,
  canonVersion,
  focusedSection,
  setFocusedSection,
  showDiff,
  setShowDiff,
  forceAcceptOpen,
  setForceAcceptOpen,
  forceReason,
  setForceReason,
  onClose,
  onAction,
}: {
  review: ReviewBundle;
  chapter: Chapter | null;
  canonVersion: number;
  focusedSection: "issues" | "canon" | "sources";
  setFocusedSection: (section: "issues" | "canon" | "sources") => void;
  showDiff: boolean;
  setShowDiff: (show: boolean) => void;
  forceAcceptOpen: boolean;
  setForceAcceptOpen: (open: boolean) => void;
  forceReason: string;
  setForceReason: (reason: string) => void;
  onClose: () => void;
  onAction: (
    action: "accept" | "reject" | "reaudit",
    extra?: Record<string, unknown>,
  ) => void;
}) {
  const blocking = review.issues.filter(
    (issue) => issue.severity === "critical",
  );
  const canAccept =
    review.status === "awaiting_review" && blocking.length === 0;
  return (
    <Modal
      title="章节与正典审核包"
      kicker="REVIEW BUNDLE / ATOMIC COMMIT"
      onClose={onClose}
      size="xl"
    >
      <div className="review-modal-head">
        <div>
          <p className="modal-lead">
            第 {chapter?.number || "—"} 章 · {chapter?.title || "未命名章节"} ·{" "}
            {review.generated_at || "刚刚生成"}
          </p>
          <div className="review-integrity">
            <LockKeyhole size={13} /> 当前正典 v{canonVersion} 冻结中 ·
            拒绝不会改变任何记忆
          </div>
        </div>
        <div className="review-head-actions">
          <button
            className={`button button-secondary button-compact ${showDiff ? "is-pressed" : ""}`}
            onClick={() => setShowDiff(!showDiff)}
          >
            <GitCompare size={14} /> {showDiff ? "关闭差异" : "查看差异"}
          </button>
          <button className="quiet-icon" aria-label="复制审核包编号">
            <Copy size={15} />
          </button>
        </div>
      </div>
      <div className="review-grid">
        <nav className="review-nav">
          <button
            className={focusedSection === "issues" ? "is-active" : ""}
            onClick={() => setFocusedSection("issues")}
          >
            <CircleAlert size={15} />
            <span>审查问题</span>
            <b className="nav-count warning">{review.issues.length}</b>
          </button>
          <button
            className={focusedSection === "canon" ? "is-active" : ""}
            onClick={() => setFocusedSection("canon")}
          >
            <LockKeyhole size={15} />
            <span>正典变化</span>
            <b className="nav-count">{review.canon_changes.length}</b>
          </button>
          <button
            className={focusedSection === "sources" ? "is-active" : ""}
            onClick={() => setFocusedSection("sources")}
          >
            <FileText size={15} />
            <span>来源上下文</span>
            <b className="nav-count">{review.source_context?.length || 0}</b>
          </button>
          <div className="review-nav-note">
            <ShieldCheck size={15} />
            <span>审查结果基于当前修订。编辑正文后，旧结果会自动失效。</span>
          </div>
        </nav>
        <section className="review-main">
          {focusedSection === "issues" && (
            <ReviewIssues issues={review.issues} />
          )}
          {focusedSection === "canon" && (
            <ReviewCanonChanges changes={review.canon_changes} />
          )}
          {focusedSection === "sources" && (
            <ReviewSources sources={review.source_context || []} />
          )}
          {showDiff && (
            <div className="diff-drawer">
              <div className="diff-head">
                <strong>正文修订差异</strong>
                <button
                  className="quiet-icon"
                  onClick={() => setShowDiff(false)}
                  aria-label="关闭差异"
                >
                  <X size={15} />
                </button>
              </div>
              <div className="diff-line diff-removed">
                <span>−</span>
                <p>林澈记得自己生于潮历二十九年。</p>
              </div>
              <div className="diff-line diff-added">
                <span>＋</span>
                <p>林澈，生于潮历二十七年。</p>
              </div>
              <small>来源：第 3 章当前修订 · 仅用于审阅，不会单独提交</small>
            </div>
          )}
        </section>
      </div>
      <div className="review-footer">
        <div className="review-footer-status">
          {blocking.length ? (
            <>
              <CircleAlert size={15} />
              <span>
                <strong>{blocking.length} 个严重冲突</strong> ·
                普通接受已锁定，请选择修订或填写强制接受理由
              </span>
            </>
          ) : review.status === "stale" ? (
            <>
              <CircleAlert size={15} />
              <span>正文已修改，必须重新审查后才能接受。</span>
            </>
          ) : (
            <>
              <CheckCircle2 size={15} />
              <span>没有阻塞性冲突，可以原子提交章节与正典。</span>
            </>
          )}
        </div>
        <div className="review-actions">
          <button
            className="button button-quiet-danger"
            onClick={() => onAction("reject")}
          >
            <X size={14} /> 拒绝
          </button>
          <button
            className="button button-secondary"
            onClick={() => onAction("reaudit")}
          >
            <RefreshCw size={14} /> 重新审查
          </button>
          {blocking.length ? (
            <button
              className="button button-danger"
              onClick={() => setForceAcceptOpen(true)}
            >
              <ShieldCheck size={14} /> 填理由强制接受
            </button>
          ) : (
            <button
              className="button button-primary"
              disabled={!canAccept}
              onClick={() => onAction("accept")}
            >
              <Check size={14} /> 接受并入典
            </button>
          )}
        </div>
      </div>
      {forceAcceptOpen && (
        <div className="force-accept-box">
          <div className="force-accept-head">
            <CircleAlert size={16} />
            <strong>强制接受需要审计理由</strong>
            <button
              className="quiet-icon"
              onClick={() => setForceAcceptOpen(false)}
              aria-label="取消强制接受"
            >
              <X size={14} />
            </button>
          </div>
          <p>
            这会把冲突和你的理由一并写入审计日志，并提交本章节修订与正典变化。
          </p>
          <textarea
            autoFocus
            rows={3}
            value={forceReason}
            onChange={(event) => setForceReason(event.target.value)}
            placeholder="例如：出生年份矛盾是主线谜面，下一章会给出可验证线索。"
          />
          <div className="force-accept-actions">
            <button
              className="button button-secondary"
              onClick={() => setForceAcceptOpen(false)}
            >
              返回修改
            </button>
            <button
              className="button button-danger"
              disabled={!forceReason.trim()}
              onClick={() =>
                onAction("accept", { force: true, reason: forceReason.trim() })
              }
            >
              <ShieldCheck size={14} /> 确认强制接受
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}

function ReviewIssues({ issues }: { issues: AuditIssue[] }) {
  return (
    <div className="review-section">
      <div className="review-section-head">
        <div>
          <p className="eyebrow">AUDIT REPORT</p>
          <h2>问题账本</h2>
          <p>按严重度处理；每个问题都携带可回到原文的来源。</p>
        </div>
        <span className="issue-summary">
          <span className="summary-critical">
            {issues.filter((issue) => issue.severity === "critical").length}
          </span>{" "}
          严重 <i /> {issues.length} 总计
        </span>
      </div>
      <div className="issue-list">
        {issues.map((issue) => (
          <IssueCard issue={issue} key={issue.id} />
        ))}
      </div>
    </div>
  );
}

function IssueCard({ issue }: { issue: AuditIssue }) {
  return (
    <article className={`issue-card issue-${issue.severity}`}>
      <div className="issue-card-top">
        <span className="severity-badge">
          <span className="severity-dot" />
          {severityLabels[issue.severity]}
        </span>
        <span className="issue-type">{issue.type || "continuity"}</span>
        <button className="quiet-icon" aria-label="标记已处理">
          <MoreHorizontal size={15} />
        </button>
      </div>
      <h3>{issue.title}</h3>
      <p>{issue.detail}</p>
      {issue.suggestion && (
        <div className="issue-suggestion">
          <Lightbulb size={13} />
          <span>{issue.suggestion}</span>
        </div>
      )}
      {issue.source_refs?.length ? (
        <div className="issue-sources">
          {issue.source_refs.map((source, index) => (
            <SourceChip
              key={`${source.chapter_id}-${index}`}
              source={source}
              activeChapter={null}
            />
          ))}
        </div>
      ) : null}
    </article>
  );
}

function ReviewCanonChanges({ changes }: { changes: CanonChange[] }) {
  return (
    <div className="review-section">
      <div className="review-section-head">
        <div>
          <p className="eyebrow">CANON DIFF</p>
          <h2>正典变化</h2>
          <p>只有接受审核包，这些变化才会与章节修订一起生效。</p>
        </div>
        <span className="issue-summary">
          <span className="summary-teal">{changes.length}</span> 条候选变化
        </span>
      </div>
      <div className="canon-change-list">
        {changes.map((change) => (
          <article className="canon-change-card" key={change.id}>
            <div className="change-action">
              <span className={`change-icon change-${change.action}`}>
                {change.action === "review" ? (
                  <CircleAlert size={14} />
                ) : change.action === "update" ? (
                  <RefreshCw size={14} />
                ) : (
                  <Plus size={14} />
                )}
              </span>
              <span>
                {change.action === "review"
                  ? "标记待复核"
                  : change.action === "update"
                    ? "更新条目"
                    : change.action === "create"
                      ? "新增条目"
                      : "取代旧条目"}
              </span>
            </div>
            <div className="change-copy">
              <h3>
                {change.item.subject} <small>{change.item.predicate}</small>
              </h3>
              <p>{change.item.value}</p>
              <span>{change.reason}</span>
            </div>
            {change.source_ref && (
              <SourceChip source={change.source_ref} activeChapter={null} />
            )}
          </article>
        ))}
      </div>
    </div>
  );
}

function ReviewSources({ sources }: { sources: SourceRef[] }) {
  return (
    <div className="review-section">
      <div className="review-section-head">
        <div>
          <p className="eyebrow">RETRIEVAL CONTEXT</p>
          <h2>来源上下文</h2>
          <p>模型本次使用的片段均保留章节、修订和原文引用。</p>
        </div>
        <span className="source-integrity">
          <CheckCircle2 size={14} /> 可追溯
        </span>
      </div>
      <div className="source-context-list">
        {sources.map((source, index) => (
          <article
            className="source-context-card"
            key={`${source.chapter_id}-${index}`}
          >
            <div className="source-context-meta">
              <span>0{index + 1}</span>
              <strong>{source.chapter_title || "未命名章节"}</strong>
              <small>{source.revision_id || "当前修订"}</small>
            </div>
            <blockquote>{source.quote || "未提供原文摘录。"}</blockquote>
            <button className="source-jump">
              <ArrowRight size={13} /> 回到正文位置
            </button>
          </article>
        ))}
      </div>
    </div>
  );
}

function CommandPalette({
  onClose,
  onAction,
}: {
  onClose: () => void;
  onAction: (action: () => void) => void;
}) {
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const dialogRef = useDialogFocus<HTMLDivElement>();
  const actions = [
    {
      label: "生成下一章",
      hint: "从冻结正典开始新的生成任务",
      icon: WandSparkles,
      action: () => window.dispatchEvent(new CustomEvent("command-generate")),
    },
    {
      label: "打开审核包",
      hint: "检查冲突与正典变化",
      icon: ShieldCheck,
      action: () => window.dispatchEvent(new CustomEvent("command-review")),
    },
    {
      label: "保存当前草稿",
      hint: "写入当前章节修订",
      icon: Save,
      action: () => window.dispatchEvent(new CustomEvent("command-save")),
    },
    {
      label: "导入旧稿",
      hint: "选择 TXT / Markdown 文件",
      icon: Upload,
      action: () => window.dispatchEvent(new CustomEvent("command-import")),
    },
    {
      label: "打开设置",
      hint: "Provider、快捷键与备份",
      icon: Settings,
      action: () => window.dispatchEvent(new CustomEvent("command-settings")),
    },
  ];
  const visible = actions.filter((item) =>
    `${item.label}${item.hint}`.includes(query),
  );
  useEffect(() => setActiveIndex(0), [query]);
  const handleCommandKeyDown = (event: ReactKeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((index) => (visible.length ? (index + 1) % visible.length : 0));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((index) =>
        visible.length ? (index - 1 + visible.length) % visible.length : 0,
      );
    } else if (event.key === "Enter" && visible[activeIndex]) {
      event.preventDefault();
      onAction(visible[activeIndex].action);
    }
  };
  return (
    <div className="command-layer">
      <button
        className="command-scrim"
        onClick={onClose}
        aria-label="关闭命令栏"
      />
      <div
        className="command-palette"
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label="快捷命令"
        tabIndex={-1}
      >
        <div className="command-input">
          <Search size={17} />
          <input
            autoFocus
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={handleCommandKeyDown}
            placeholder="输入命令或动作…"
          />
          <kbd>ESC</kbd>
        </div>
        <div className="command-list">
          {visible.map(({ label, hint, icon: Icon, action }, index) => (
            <button
              className={index === activeIndex ? "is-active" : ""}
              key={label}
              onClick={() => onAction(action)}
              onMouseEnter={() => setActiveIndex(index)}
            >
              <span className="command-number">{index + 1}</span>
              <span className="command-icon">
                <Icon size={15} />
              </span>
              <span>
                <strong>{label}</strong>
                <small>{hint}</small>
              </span>
              <ArrowRight size={14} />
            </button>
          ))}
          {visible.length === 0 && (
            <div className="command-empty">
              <Search size={16} /> 没有匹配的动作
            </div>
          )}
        </div>
        <div className="command-footer">
          <span>
            <kbd>↑↓</kbd> 选择
          </span>
          <span>
            <kbd>↵</kbd> 执行
          </span>
          <span>
            <kbd>ESC</kbd> 关闭
          </span>
        </div>
      </div>
    </div>
  );
}

function Toast({
  tone,
  message,
  onClose,
}: {
  tone: "success" | "warning" | "error" | "info";
  message: string;
  onClose: () => void;
}) {
  const Icon =
    tone === "success"
      ? CheckCircle2
      : tone === "warning"
        ? CircleAlert
        : tone === "error"
          ? CircleAlert
          : CircleHelp;
  return (
    <div className={`toast toast-${tone}`} role="status">
      <Icon size={16} />
      <span>{message}</span>
      <button className="quiet-icon" onClick={onClose} aria-label="关闭提示">
        <X size={14} />
      </button>
    </div>
  );
}

function Modal({
  title,
  kicker,
  children,
  onClose,
  size = "medium",
}: {
  title: string;
  kicker: string;
  children: ReactNode;
  onClose: () => void;
  size?: "small" | "medium" | "large" | "xl";
}) {
  const dialogRef = useDialogFocus<HTMLElement>();
  return (
    <div className="modal-layer">
      <button className="modal-scrim" onClick={onClose} aria-label="关闭窗口" />
      <section
        className={`modal modal-${size}`}
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
      >
        <div className="modal-head">
          <div>
            <p className="eyebrow">{kicker}</p>
            <h2>{title}</h2>
          </div>
          <button className="quiet-icon" onClick={onClose} aria-label="关闭">
            <X size={17} />
          </button>
        </div>
        {children}
      </section>
    </div>
  );
}

function normalizePreview(
  value: ImportPreview,
  fileName: string,
): ImportPreview {
  const chapters = Array.isArray(value.chapters) ? value.chapters : [];
  return {
    ...value,
    file_name: value.file_name || fileName,
    encoding: value.encoding || "UTF-8",
    chapters: chapters.map((chapter, index) => ({
      ...chapter,
      key: chapter.key || `import-${index}`,
      number: chapter.number || index + 1,
      title: chapter.title || `第 ${index + 1} 章`,
      content: chapter.content || "",
      selected: chapter.selected !== false,
    })),
  };
}

function parseImport(fileName: string, text: string): ImportPreview {
  const normalized = text.replace(/^\uFEFF/, "").replace(/\r\n?/g, "\n");
  const lines = normalized.split("\n");
  const headings: Array<{ index: number; number: number; title: string }> = [];
  lines.forEach((line, index) => {
    const clean = line.trim().replace(/^#{1,6}\s*/, "");
    const match = clean.match(
      /^(?:第\s*([0-9一二三四五六七八九十百千]+)\s*章\s*[:：.．-]?\s*(.*)|序章\s*[:：.．-]?\s*(.*)|番外\s*[:：.．-]?\s*(.*))$/,
    );
    if (!match) return;
    const numeral = match[1];
    const number = numeral ? chineseNumber(numeral) : headings.length + 1;
    const title = (
      match[2] ||
      match[3] ||
      match[4] ||
      (numeral ? `第 ${number} 章` : clean)
    ).trim();
    headings.push({ index, number, title });
  });
  const chapters: ImportChapterPreview[] = headings.length
    ? headings.map((heading, index) => ({
        key: `import-${index}`,
        number: heading.number || index + 1,
        title: heading.title,
        content: lines
          .slice(heading.index + 1, headings[index + 1]?.index ?? lines.length)
          .join("\n")
          .trim(),
        selected: true,
      }))
    : [
        {
          key: "import-0",
          number: 1,
          title: fileName.replace(/\.(txt|md|markdown)$/i, "") || "导入稿",
          content: normalized.trim(),
          selected: true,
        },
      ];
  return {
    file_name: fileName,
    encoding: /[\u4e00-\u9fff]/.test(text) ? "UTF-8 / 自动识别" : "UTF-8",
    chapters,
    source_text: normalized,
  };
}

function chineseNumber(value: string) {
  if (/^\d+$/.test(value)) return Number(value);
  const digits: Record<string, number> = {
    零: 0,
    一: 1,
    二: 2,
    两: 2,
    三: 3,
    四: 4,
    五: 5,
    六: 6,
    七: 7,
    八: 8,
    九: 9,
  };
  const units: Record<string, number> = { 十: 10, 百: 100, 千: 1000 };
  let total = 0;
  let section = 0;
  let current = 0;
  for (const char of value) {
    if (char in digits) current = digits[char];
    else if (char in units) {
      const unit = units[char];
      section += (current || 1) * unit;
      current = 0;
    }
  }
  total += section + current;
  return total || 1;
}
