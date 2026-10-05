import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactElement } from "react";

// 安全网基线：这些断言在「拆分前」成立，拆分后必须仍然成立。
// 目的（L-28 前置）：1355 行单函数拆成多组件时，用这些测试锁住行为不回归。

// ── mock 掉重依赖：api client 与重子组件 ──
vi.mock("../../api/client", () => ({
  // MessagesPage 只从 client 取类型，运行时不用它发请求（走 props.api）
  PageProps: {},
}));

// mock 掉会拉真实资源/依赖复杂上下文的子组件
vi.mock("./LeadsSection", () => ({
  default: () => <div data-testid="leads-section">leads</div>,
}));
vi.mock("./message-viewer", () => ({
  ImageViewer: () => <div data-testid="image-viewer">viewer</div>,
}));
vi.mock("./message-bubble", () => ({
  MsgBubble: ({ m }: { m: { text?: string } }) => (
    <div data-testid="msg-bubble">{m?.text ?? ""}</div>
  ),
}));

import MessagesPage from "./messages-page";
import type { PageProps } from "../../api/client";

// ── 构造最小可用的 props ──
function makeProps(overrides: Partial<PageProps> = {}): PageProps {
  const api = {
    getAccounts: vi.fn().mockResolvedValue([
      { name: "acct-a", uid: "u1", level: "ok", label: "正常", loggedIn: true },
    ]),
    getConversations: vi.fn().mockResolvedValue([
      { id: "c1", name: "张三", uid: "u9", unread: 0, last: "你好", mt: "10:00" },
    ]),
    getConversation: vi.fn().mockResolvedValue({
      id: "c1",
      msgs: [
        { id: "m1", dir: "in", type: "text", text: "你好", mt: "10:00" },
      ],
    }),
  };
  return {
    push: vi.fn(),
    ready: true,
    goDm: vi.fn(),
    api,
    msgAcct: "acct-a",
    setMsgAcct: vi.fn(),
    ...overrides,
  } as unknown as PageProps;
}

function renderPage(props: PageProps) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MessagesPage {...props} />
    </QueryClientProvider> as ReactElement,
  );
}

describe("messages-page 冒烟基线（拆分安全网）", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("① 渲染出页头「私信中心」与描述", async () => {
    renderPage(makeProps());
    await waitFor(() => {
      expect(screen.getByText("私信中心")).toBeInTheDocument();
    });
    expect(
      screen.getByText(/手动回复支持 WS 守护 \/ 网页版双通道发送/),
    ).toBeInTheDocument();
  });

  it("② 默认停在「会话」子页，两个 SegmentedTabs 项均在", async () => {
    renderPage(makeProps());
    await waitFor(() => {
      expect(screen.getByText("私信中心")).toBeInTheDocument();
    });
    expect(screen.getByText("会话")).toBeInTheDocument();
    expect(screen.getByText("留资线索")).toBeInTheDocument();
  });

  it("③ 两栏布局容器存在（data-od-id=messages-panel）", async () => {
    const { container } = renderPage(makeProps());
    await waitFor(() => {
      expect(screen.getByText("私信中心")).toBeInTheDocument();
    });
    const panel = container.querySelector('[data-od-id="messages-panel"]');
    expect(panel).toBeTruthy();
    expect(panel?.className).toContain("grid-cols-[3fr_7fr]");
  });

  it("④ 稳定锚点齐全：composer / send-msg / dm-search / dm-export / refresh-conv", async () => {
    const { container } = renderPage(makeProps());
    await waitFor(() => {
      expect(screen.getByText("私信中心")).toBeInTheDocument();
    });
    for (const id of [
      "composer",
      "send-msg",
      "dm-search",
      "dm-export",
      "refresh-conv",
    ]) {
      expect(
        container.querySelector(`[data-od-id="${id}"]`),
        `缺少稳定锚点 data-od-id=${id}`,
      ).toBeTruthy();
    }
  });

  it("⑤ 无账号时不炸（空态可渲染）", async () => {
    renderPage(makeProps({ msgAcct: "" }));
    await waitFor(() => {
      expect(screen.getByText("私信中心")).toBeInTheDocument();
    });
    // 空态下不应抛出（组件仍能渲染页头即可）
    expect(screen.getByText("私信中心")).toBeInTheDocument();
  });
});
