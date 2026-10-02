/**
 * ErrorBoundary —— 全局渲染兜底（2026-10-02 新增）
 *
 * ## 为什么必须加（用户实测「切换到页面时会出现渲染崩溃」）
 *
 * 原应用**没有任何错误边界** ⇒ 任一页面渲染抛错，React 会**卸载整棵树**，
 * 用户只看到**全白空窗**、且**前端日志无任何痕迹**（JS 错误不写 boot log）
 * ⇒ 崩溃既不可见也不可诊断，只能靠用户口述现象。
 *
 * 本组件把「整树白屏」降级为「可读错误 + 重试」：
 *   · 捕获子树渲染错误，显示错误信息与组件栈（生产可读）；
 *   · 提供「重试」（重置错误态）与「回到总览」（让用户脱困）；
 *   · 错误同时 `console.error`，便于 DevTools / 未来日志桥接采集。
 *
 * 用法：包住 `<AppShell>` 与其页面（`<ErrorBoundary key={tab}>` 使切页自动复位）。
 */
import * as React from "react";
import { Button } from "@/components/ui/button";

interface Props {
  children: React.ReactNode;
  /** 发生错误时点「回到总览」的出口（可选）。 */
  onReset?: () => void;
}

interface State {
  error: Error | null;
  info: string;
}

export class ErrorBoundary extends React.Component<Props, State> {
  state: State = { error: null, info: "" };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    // 落到控制台，便于 DevTools 排查（boot log 只记启动，不记运行时异常）。
    // eslint-disable-next-line no-console
    console.error("[ErrorBoundary] 页面渲染异常：", error, info?.componentStack);
    this.setState({ info: info?.componentStack || "" });
  }

  private reset = () => this.setState({ error: null, info: "" });

  render() {
    const { error, info } = this.state;
    if (!error) return this.props.children;

    return (
      <div className="flex min-h-full items-center justify-center p-6">
        <div className="card-surface w-full max-w-2xl rounded-[var(--radius-lg)] p-5">
          <div className="mb-2 flex items-center gap-2">
            <span className="text-xl">⚠️</span>
            <h2 className="text-[0.95rem] font-semibold text-[var(--color-danger)]">
              页面渲染异常
            </h2>
          </div>
          <p className="mb-3 text-[0.8rem] leading-relaxed text-[var(--color-text-secondary)]">
            当前页面在渲染时抛出了错误，已拦截以免整个界面白屏。可先重试；若持续出现，
            请把下方信息反馈给开发者。
          </p>

          <pre className="max-h-[220px] overflow-auto whitespace-pre-wrap break-all
                          rounded-[var(--radius-sm)] border border-[var(--color-border)]
                          bg-[var(--color-background-soft)] p-3 text-[0.72rem]
                          leading-relaxed text-[var(--color-text)]">
            {String(error?.stack || error?.message || error)}
            {info ? "\n\n组件栈：" + info : ""}
          </pre>

          <div className="mt-4 flex justify-end gap-2">
            {this.props.onReset ? (
              <Button
                variant="secondary"
                onClick={() => {
                  this.reset();
                  this.props.onReset?.();
                }}
              >
                回到总览
              </Button>
            ) : null}
            <Button onClick={this.reset}>重试</Button>
          </div>
        </div>
      </div>
    );
  }
}
