/**
 * Modal —— 全站统一弹窗（遮罩 / 玻璃材质 / 圆角 / z 层级 = 单一真源）
 *
 * ## 为什么收敛（2026-10-02 用户实测反馈）
 * 用户原话：「遮罩弹窗异常丑显得很低级，和主题不适配，包括提示弹窗也是。」
 *
 * 根因（实测清点 9 处弹窗）：遮罩透明度各写一套（bg-black/45·/55·/60·/70）、
 * 材质各用一套（glass-premium / surface-solid / 完全不透光）、z-index 五档乱跳
 * （50 / 60 / 999 / 9999 / 99999）、进出场动画有无不一 —— 于是同类交互观感各不相同。
 *
 * 现收敛到本组件：**遮罩透明度、玻璃材质、圆角、z 层级、关闭行为全部一处定义**，
 * 调用点只提供内容。主题（昼 / 夜）由设计令牌自动跟随，不再硬编码颜色。
 *
 * z 层级约定（2026-10-02 收敛为令牌梯队，全站唯一真源，禁止再用 999/9999/99999）：
 *   · --z-chrome  (30)  顶栏 / 侧栏
 *   · --z-popover (50)  下拉 / 选择 / 气泡（Radix popover 层）
 *   · --z-drawer  (60)  侧滑抽屉
 *   · --z-modal   (100) 模态弹窗（本组件）
 *   · --z-view    (120) 全屏视图级接管（查阅模式 / 播放器浮层）
 *   · --z-blocker (200) 阻断式门禁（版本不一致）
 *   · --z-toast   (300) 全局 toast（必须盖在最上）
 */
import * as React from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export function Modal({
  open = true,
  onClose,
  className,
  children,
  maxWidth,
  labelledBy,
  closeOnOverlay = true,
}: {
  open?: boolean;
  onClose: () => void;
  className?: string;
  children: React.ReactNode;
  /** 内容区最大宽度（如 "560px"）。 */
  maxWidth?: string;
  /** 标题元素 id，用于 aria-labelledby。 */
  labelledBy?: string;
  closeOnOverlay?: boolean;
}) {
  // Esc 关闭：所有弹窗行为一致（此前只有部分 Radix 弹窗自带）。
  React.useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return createPortal(
    <div
      className="fixed inset-0 z-[var(--z-modal)] flex items-center justify-center modal-scrim p-5"
      onClick={closeOnOverlay ? onClose : undefined}
      role="dialog"
      aria-modal="true"
      aria-labelledby={labelledBy}
    >
      <div
        className={cn(
          "modal-surface flex max-h-[88vh] w-full flex-col overflow-hidden",
          "rounded-[var(--radius-xl)]",
          className
        )}
        style={maxWidth ? { maxWidth } : undefined}
        onClick={(e) => e.stopPropagation()}
      >
        {children}
      </div>
    </div>,
    document.body
  );
}

export function ModalHeader({
  title,
  description,
  onClose,
  icon,
  className,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  onClose: () => void;
  icon?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex shrink-0 items-center justify-between gap-3 border-b",
        "border-[var(--color-border)] px-4 py-3",
        className
      )}
    >
      <div className="flex min-w-0 items-center gap-2.5">
        {icon}
        <div className="min-w-0">
          <div className="text-[0.95rem] font-semibold text-[var(--color-text)]">
            {title}
          </div>
          {description ? (
            <div className="mt-0.5 text-[0.72rem] text-[var(--color-text-muted)]">
              {description}
            </div>
          ) : null}
        </div>
      </div>
      <button
        type="button"
        aria-label="关闭"
        onClick={onClose}
        className="shrink-0 cursor-pointer rounded-[var(--radius-sm)] p-1
                   text-[var(--color-text-muted)] transition-colors
                   hover:bg-[var(--color-surface-raised)] hover:text-[var(--color-text)]"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  );
}

export function ModalBody({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("min-h-0 flex-1 overflow-y-auto p-4", className)}>
      {children}
    </div>
  );
}

export function ModalFooter({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex shrink-0 flex-wrap items-center justify-end gap-2 border-t",
        "border-[var(--color-border)] px-4 py-3",
        className
      )}
    >
      {children}
    </div>
  );
}

/**
 * 确认弹窗 —— 取代原生 `window.confirm`（配色灰白、不跟主题、不可定制）。
 * 指令式调用不便，故采用「状态 + 渲染」：调用点用 useState 保存待确认项。
 */
export function ConfirmDialog({
  open = true,
  title = "确认操作",
  message,
  confirmText = "确定",
  cancelText = "取消",
  danger = false,
  onConfirm,
  onCancel,
}: {
  open?: boolean;
  title?: React.ReactNode;
  message: React.ReactNode;
  confirmText?: string;
  cancelText?: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <Modal open={open} onClose={onCancel} maxWidth="420px" labelledBy="cl-title">
      <div className="p-5">
        <h3 id="cl-title" className="text-[0.95rem] font-semibold text-[var(--color-text)]">
          {title}
        </h3>
        <p className="mt-2 text-[0.82rem] leading-relaxed text-[var(--color-text-secondary)]">
          {message}
        </p>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="secondary" onClick={onCancel}>{cancelText}</Button>
          <Button variant={danger ? "danger" : "default"} onClick={onConfirm}>
            {confirmText}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

/** 输入弹窗 —— 取代原生 `window.prompt`。 */
export function PromptDialog({
  open = true,
  title = "请输入",
  message,
  placeholder,
  initialValue = "",
  confirmText = "确定",
  onConfirm,
  onCancel,
}: {
  open?: boolean;
  title?: React.ReactNode;
  message?: React.ReactNode;
  placeholder?: string;
  initialValue?: string;
  confirmText?: string;
  onConfirm: (value: string) => void;
  onCancel: () => void;
}) {
  const [val, setVal] = React.useState(initialValue);
  const ref = React.useRef<HTMLInputElement>(null);
  React.useEffect(() => {
    if (open) {
      const t = window.setTimeout(() => ref.current?.focus(), 30);
      return () => window.clearTimeout(t);
    }
  }, [open]);
  return (
    <Modal open={open} onClose={onCancel} maxWidth="420px" labelledBy="pd-title">
      <div className="p-5">
        <h3 id="pd-title" className="text-[0.95rem] font-semibold text-[var(--color-text)]">
          {title}
        </h3>
        {message ? (
          <p className="mt-1.5 text-[0.8rem] text-[var(--color-text-secondary)]">{message}</p>
        ) : null}
        <Input
          ref={ref}
          className="mt-3"
          value={val}
          placeholder={placeholder}
          onChange={(e) => setVal(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") onConfirm(val);
          }}
        />
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="secondary" onClick={onCancel}>取消</Button>
          <Button onClick={() => onConfirm(val)}>{confirmText}</Button>
        </div>
      </div>
    </Modal>
  );
}

/* ═══════════════════════════════════════════════════════════════
   指令式弹窗 —— 取代原生 `window.confirm` / `window.prompt`（零 JSX 改动）
   ═══════════════════════════════════════════════════════════════
   原生 `confirm()` 是**指令式**的（同步阻塞、返回布尔）；本套件给出等价形态：
     · `confirmDialog(opts)` → Promise<boolean>
     · `promptDialog(opts)`  → Promise<string | null>
   二者把请求投递给**单例宿主** `<DialogHost/>`（在 App 根部挂载一次），
   因此调用点无需插入任何 JSX，只需改一行表达式并 `await`。
   未挂载宿主时保守返回「取消 / 空」，绝不误判为「确定」。
*/

type ConfirmOpts = {
  title?: string;
  message: string;
  confirmText?: string;
  cancelText?: string;
  danger?: boolean;
};
type PromptOpts = {
  title?: string;
  message?: string;
  placeholder?: string;
  initialValue?: string;
  confirmText?: string;
};
type ConfirmReq = ConfirmOpts & { resolve: (v: boolean) => void };
type PromptReq = PromptOpts & { resolve: (v: string | null) => void };

let _confirmListeners: ((req: ConfirmReq) => void)[] = [];
let _promptListeners: ((req: PromptReq) => void)[] = [];

export function confirmDialog(o: ConfirmOpts): Promise<boolean> {
  return new Promise<boolean>((resolve) => {
    if (_confirmListeners.length === 0) {
      resolve(false);
      return;
    }
    _confirmListeners.forEach((l) => l({ ...o, resolve }));
  });
}

export function promptDialog(o: PromptOpts): Promise<string | null> {
  return new Promise<string | null>((resolve) => {
    if (_promptListeners.length === 0) {
      resolve(null);
      return;
    }
    _promptListeners.forEach((l) => l({ ...o, resolve }));
  });
}

/** 单例宿主：在 App 根部挂载一次，承载所有指令式确认 / 输入弹窗。 */
export function DialogHost() {
  const [c, setC] = React.useState<ConfirmReq | null>(null);
  const [p, setP] = React.useState<PromptReq | null>(null);
  React.useEffect(() => {
    const lc = (req: ConfirmReq) => setC(req);
    const lp = (req: PromptReq) => setP(req);
    _confirmListeners.push(lc);
    _promptListeners.push(lp);
    return () => {
      _confirmListeners = _confirmListeners.filter((x) => x !== lc);
      _promptListeners = _promptListeners.filter((x) => x !== lp);
    };
  }, []);
  return (
    <>
      {c && (
        <ConfirmDialog
          open
          title={c.title ?? "确认操作"}
          message={c.message}
          confirmText={c.confirmText}
          cancelText={c.cancelText}
          danger={c.danger}
          onConfirm={() => { c.resolve(true); setC(null); }}
          onCancel={() => { c.resolve(false); setC(null); }}
        />
      )}
      {p && (
        <PromptDialog
          open
          title={p.title ?? "请输入"}
          message={p.message}
          placeholder={p.placeholder}
          initialValue={p.initialValue}
          confirmText={p.confirmText}
          onConfirm={(v) => { p.resolve(v); setP(null); }}
          onCancel={() => { p.resolve(null); setP(null); }}
        />
      )}
    </>
  );
}
