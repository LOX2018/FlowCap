/**
 * 页面重设计验证入口（开发工具）
 *
 * 用途：真实挂载**各业务页面**（用 mock props），验证重设计后的渲染与设计令牌生效。
 *   启动屏依赖后端 + 会员登录，开发态看不到主界面；本入口绕过它。
 *
 * 访问：/preview-pages.html?p=overview|platform|...
 */
import { Component, useState, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@/styles/tokens.css";

import { AppShell } from "@/components/layout/app-shell";
import type { TabId } from "@/components/layout/sidebar";
import { Badge } from "@/components/ui/badge";
import { SegmentedTabs } from "@/components/page/kit";

import OverviewPage from "@/components/overview/overview-page";
import PlatformPage from "@/components/platform/platform-page";
import CrawlPage from "@/components/crawl/crawl-page";
import SettingsPage from "@/components/settings/settings-page";
import NotifyPage from "@/components/notify/notify-page";
import TasksPage from "@/components/tasks/tasks-page";
import LogsPage from "@/components/logs/logs-page";
import MessagesPage from "@/components/messages/messages-page";
import LivePage from "@/components/live/live-page";
import AccountsPage from "@/components/accounts/accounts-page";
import KbPage from "@/components/kb/kb-page";
import type { PageProps } from "@/api/client";

/* ── mock props：模拟后端已就绪的返回值（仅用于视觉验证） ── */

const MOCK_ACCOUNTS = [
  {
    name: "主账号_A", uid: "1234567890", isCurrent: true, loggedIn: true,
    signReady: true, isMonitor: true, isSender: false,
  },
  {
    name: "发送号_B", uid: "9876543210", isCurrent: false, loggedIn: true,
    signReady: true, isMonitor: false, isSender: true,
  },
  {
    name: "备用号_C", uid: "5555555555", isCurrent: false, loggedIn: false,
    signReady: false, isMonitor: false, isSender: false,
  },
];

const MOCK_OVERVIEW = {
  running: true, paused: false, sent: 1284, limit: 2000, queue: 37,
  status: "监听直播间中", liveUrl: "https://live.douyin.com/123456",
  browserDaemon: { alive: true }, recvDaemon: { alive: true },
  accounts: MOCK_ACCOUNTS,
} as never;

const MOCK_VIDEOS = [
  { awemeId: "7301", title: "三天学会的收纳技巧，家里瞬间整齐", cmts: 328, plays: 128000, likes: 9800, nickname: "生活小妙招", uid: "u1001", cover: "" },
  { awemeId: "7302", title: "厨房收纳避坑指南（附清单）", cmts: 156, plays: 64000, likes: 4200, nickname: "收纳达人阿May", uid: "u1002", cover: "" },
  { awemeId: "7303", title: "小户型也能有的衣帽间", cmts: 892, plays: 356000, likes: 27000, nickname: "装修老王", uid: "u1003", cover: "" },
  { awemeId: "7304", title: "10 件平价收纳好物分享", cmts: 74, plays: 21000, likes: 1500, nickname: "好物研究所", uid: "u1004", cover: "" },
];

function mockApi() {
  return {
    getAccounts: async () => MOCK_ACCOUNTS,
    // 2026-09-19：直播「配置标签 / 目标直播间」子视图的 mock 数据
    listRoomConfigs: async () => ({
      ok: true,
      items: [
        { id: "992931212705", room_id: "992931212705", name: "标准-快速", max_target: 50,
          interval: 45, delay: "40,80", force_rescan: false, acct: "主账号_A",
          auto_link_mic: true, link_mic_mode: "audio",
          dm_pool: [{ text: "你好，看到你咨询工伤，方便留个电话吗", enabled: true },
                    { text: "在的哈，有什么可以帮您", enabled: false }] },
        { id: "保守-慢速", room_id: "保守-慢速", name: "保守-慢速", max_target: 8,
          interval: 300, delay: "300,600", force_rescan: true, acct: null,
          auto_link_mic: false, link_mic_mode: "audio", dm_pool: [] },
      ],
    }),
    listTargetRooms: async () => ({
      ok: true,
      items: [
        { room_id: "992931212705", name: "张老师工伤直播间", tag_id: "992931212705", enabled: true },
        { room_id: "777666555", name: "另一间（未绑定）", tag_id: null, enabled: false },
      ],
    }),
    saveTargetRoom: async () => ({ ok: true }),
    deleteTargetRoom: async () => ({ ok: true }),
    resolveTargetRoom: async () => ({ ok: true, params: {} }),
    saveRoomConfig: async () => ({ ok: true }),
    deleteRoomConfig: async () => ({ ok: true, unbound_rooms: [] }),
    applyRoomConfig: async () => ({ ok: true }),
    restartRoomConfig: async () => ({
      ok: true, applied_fields: [],
      restart: { ok: false, applied: [], not_applied: [], reason: "引擎未运行（当前 idle），请先「开始自动私信」" },
    }),
    crawlSearch: async () => ({ items: MOCK_VIDEOS, total: MOCK_VIDEOS.length }),
    crawlComments: async () => ({
      items: [
        { cid: "c1", uid: "u2001", nickname: "小张", text: "好想要这个，怎么买", digg: 12, ts: 1757832000, ip: "广东" },
        { cid: "c2", uid: "u2002", nickname: "李姐", text: "还有货吗？", digg: 3, ts: 1757831000, ip: "浙江" },
        { cid: "c3", uid: "u2003", nickname: "王哥", text: "多少钱一套", digg: 8, ts: 1757830000, ip: "北京" },
      ],
      total: 3,
    }),
    crawlDm: async () => ({ ok: true }),
    crawlBatch: async () => ({ candidates: 3, sent_ok: 2, sent_fail: 1, rate_limited: 0, results: [] }),
    getNotifyConfig: async () => ({
      config: {
        enabled: true,
        channels: [
          { id: "wecom_1", kind: "wecom", enabled: true, default_target: "@all", webhook: "••••" },
          { id: "dingtalk_1", kind: "dingtalk", enabled: false, default_target: "", webhook: "" },
        ],
        llm: {},
      },
    }),
    getNotifyStatus: async () => ({
      channels: [
        { id: "wecom_1", ready: true, missing: [] },
        { id: "dingtalk_1", ready: false, missing: ["webhook"] },
      ],
    }),
    saveNotifyConfig: async () => ({ ok: true }),
    testNotify: async (id: string) => ({ ok: true, results: { [id]: { ok: true } } }),
    getTaskHistory: async () => ({
      ok: true,
      total: 3,
      list: [
        { id: 1, acct: "主账号_A", live_id: "123456", status: "finished", result_count: 128, start_ts: "2026-09-14 10:00:00", end_ts: "2026-09-14 11:20:00" },
        { id: 2, acct: "发送号_B", live_id: "223344", status: "stopped", result_count: 64, start_ts: "2026-09-13 15:00:00", end_ts: "2026-09-13 15:40:00" },
        { id: 3, acct: "主账号_A", live_id: "998877", status: "running", result_count: 12, start_ts: "2026-09-14 14:00:00", end_ts: "" },
      ],
    }),
    clearTaskHistory: async () => ({ ok: true }),
    exportStats: async () => ({ ok: true, path: "exports/stats_20260914.xlsx" }),
    getSessions: async () => ({
      current: "run_20260914_140000.log",
      sessions: [
        { file: "run_20260914_140000.log", start: "20260914_140000", size: 24576, mtime: 1757832000 },
        { file: "run_20260913_100000.log", start: "20260913_100000", size: 1048576, mtime: 1757745600 },
      ],
    }),
    getLogs: async () => ({
      ok: true,
      file: "run_20260914_140000.log",
      lines: [
        { ts: "14:52:01", level: "INFO", text: "引擎启动 · 直播间 https://live.douyin.com/123456" },
        { ts: "14:52:03", level: "SUCCESS", text: "凭证校验通过 · uid=1234567890" },
        { ts: "14:52:10", level: "INFO", text: "捕获评论: 好想要这个 (小张)" },
        { ts: "14:52:11", level: "SUCCESS", text: "私信发送成功 → 小张" },
        { ts: "14:52:15", level: "WARNING", text: "发送频率接近阈值，已自动降速" },
        { ts: "14:52:30", level: "ERROR", text: "评论采集失败: 网络超时（将重试）" },
      ],
    }),
    deleteSessions: async () => ({ ok: true, deleted: [], skipped: [] }),
    stop: async () => ({ ok: true }),
    getConversations: async () => ({
      ok: true,
      conversations: [
        { conv_id: "conv_001", name: "小张", unread: 2, messages: [
          { role: "them", type: "text", text: "好想要这个，怎么买", time: "14:52" },
          { role: "me", type: "text", text: "详情已私信您～", time: "14:53" },
        ]},
        { conv_id: "conv_002", name: "李姐", unread: 0, messages: [
          { role: "them", type: "text", text: "还有货吗？", time: "14:40" },
        ]},
      ],
    }),
    getConversation: async () => ({
      ok: true,
      messages: [
        { role: "them", type: "text", text: "好想要这个，怎么买", time: "14:52" },
        { role: "me", type: "text", text: "详情已私信您～", time: "14:53" },
        { role: "them", type: "text", text: "你已确认聊天", time: "14:54" },
      ],
    }),
    sendDm: async () => ({ ok: true }),
    sendImage: async () => ({ ok: true }),
    requestDm: async () => ({ ok: true, msg: "已请求" }),
    refreshConversations: async () => ({ ok: true, n_conv: 2, n_msg: 5, elapsed: 1.2 }),
    addLog: async () => ({ ok: true }),
    // live
    aiStatus: async () => ({ ok: true, running: false, enabled: false, processed: 0, replied: 0, leads_total: 0, errors: 0 }),
    aiStart: async () => ({ ok: true }),
    aiStop: async () => ({ ok: true }),
    startLive: async () => ({ ok: true }),
    stopLive: async () => ({ ok: true }),
    getLiveRecords: async () => ({ ok: true, records: [] }),
    getCurrentTask: async () => ({ ok: true, has_task: false, config: {} }),
    getStream: async () => ({ ok: true, info: { online: 0, danmaku: 0, sent: 0 } }),
    getTasks: async () => ({ ok: true, list: [] }),
    sendDanmaku: async () => ({ ok: true }),
    // accounts
    checkAccount: async () => ({ ok: true }),
    listAgents: async () => ({ agents: [{ id: "a1", name: "默认 Agent" }, { id: "a2", name: "保守号" }] }),
    aiGetConfig: async () => ({ config: {} }),
    aiSaveConfig: async () => ({ ok: true }),
    aiTest: async () => ({ ok: true, msg: "连接正常" }),
    aiKbList: async () => ({ items: [] }),
    aiLeads: async () => ({ items: [] }),
    aiBlacklist: async () => ({ items: [] }),
    aiBlacklistAdd: async () => ({ ok: true }),
    aiBlacklistRemove: async () => ({ ok: true }),
    aiLeadStatus: async () => ({ ok: true }),
    aiProKbList: async () => ({ ok: true, items: [], tree: [] }),
    aiProKbMaintainStatus: async () => ({ state: null }),
    aiReplyKbList: async () => ({ items: [] }),
    deleteAccount: async () => ({ ok: true }),
    addAccount: async () => ({ ok: true }),
    saveProxy: async () => ({ ok: true }),
    pause: async () => ({ ok: true }),
    resume: async () => ({ ok: true }),
    // 配置中心（预览 harness 补齐；真实实现见 api/client.ts）
    getSettings: async () => ({
      general: { theme: "dark", accent: "#68cb6e", density: "comfortable" },
      send: { daily_limit: 50, min_interval: 30, gate_enabled: true },
      live: { poll_interval: 10, listen_enabled: false },
      capture: { history_fill: true, cache_ttl: 7, image_cache: true },
    }),
    getSettingsSchema: async () => ({ sections: [] }),
    saveSettings: async () => ({ ok: true }),
    getTags: async () => ({ tags: [], bindings: {} }),
    getScoped: async () => ({ scoped: {} }),
    getModelHub: async () => ({ providers: [], models: [], routes: {}, consumers: {} }),
    modelhubOverview: async () => ({ providers: [], models: [], routes: {}, consumers: {} }),
    getStats: async () => ({
      ok: true, total: 862, sent: 795,
      list: [
        { captureTs: "14:52:01", status: "sent", nickname: "小张", comment: "好想要这个", content: "已私信您～" },
        { captureTs: "14:51:47", status: "pending", nickname: "李姐", comment: "还有货吗" },
        { captureTs: "14:51:30", status: "sent", nickname: "王哥", comment: "多少钱", content: "详情已发您私信" },
        { captureTs: "14:51:12", status: "sent", nickname: "阿May", comment: "求链接", content: "链接已发送" },
        { captureTs: "14:50:58", status: "pending", nickname: "小林", comment: "怎么买" },
      ],
    }),
  } as never;
}

const BASE_PROPS: PageProps = {
  push: (m: string) => console.log("[push]", m),
  api: mockApi(),
  overview: MOCK_OVERVIEW,
  ready: true,
} as PageProps;

const PAGES: Record<string, { title: string; tab: TabId; el: (p: PageProps) => React.ReactNode }> = {
  overview: { title: "总览", tab: "overview", el: (p) => <OverviewPage {...p} /> },
  platform: { title: "内容浏览", tab: "platform", el: (p) => <PlatformPage {...p} /> },
  crawl: { title: "采集", tab: "crawl", el: (p) => <CrawlPage {...p} /> },
  tasks: { title: "任务", tab: "tasks", el: (p) => <TasksPage {...p} /> },
  notify: { title: "通知", tab: "notify", el: (p) => <NotifyPage {...p} /> },
  logs: { title: "日志", tab: "logs", el: (p) => <LogsPage {...p} /> },
  settings: { title: "设置", tab: "settings", el: (p) => <SettingsPage {...p} /> },
  messages: { title: "私信", tab: "msg", el: (p) => <MessagesPage {...p} /> },
  live: { title: "直播", tab: "live", el: (p) => <LivePage {...p} /> },
  accounts: { title: "账号", tab: "accounts", el: (p) => <AccountsPage {...p} /> },
  kb: { title: "知识库", tab: "kb", el: (p) => <KbPage {...p} /> },
};

function Demo() {
  const q = new URLSearchParams(location.search);
  const [key, setKey] = useState<string>(q.get("p") || "overview");
  const spec = PAGES[key] || PAGES.overview;
  const [tab, setTab] = useState<TabId>(spec.tab);

  return (
    <AppShell
      tab={tab}
      setTab={(t) => { setTab(t); if (PAGES[t]) setKey(t); }}
      title="页面重设计验证"
      connected
      memberName="预览账号"
      topRight={<Badge variant="accent">mock 数据</Badge>}
    >
      <div className="flex items-center gap-2 px-5 pt-3">
        <SegmentedTabs
          value={key}
          onChange={(k) => { setKey(k); setTab(PAGES[k].tab); }}
          items={Object.entries(PAGES).map(([k, v]) => ({ value: k, label: v.title }))}
        />
        <span className="text-[0.72rem] text-[var(--color-text-muted)]">
          （mock props · 仅验证渲染）
        </span>
      </div>
      <div key={key}>{spec.el(BASE_PROPS)}</div>
    </AppShell>
  );
}

/** 错误边界：把渲染异常显示出来（否则空白页无法诊断）。 */
class Boundary extends Component<{ children: ReactNode }, { err: Error | null }> {
  state = { err: null as Error | null };
  static getDerivedStateFromError(err: Error) {
    return { err };
  }
  render() {
    if (this.state.err) {
      return (
        <div style={{ padding: 24, fontFamily: "monospace", color: "#ff4757",
                      background: "#0e0e14", minHeight: "100vh", whiteSpace: "pre-wrap" }}>
          <b>渲染异常（预览harness）</b>{"\n\n"}
          {String(this.state.err?.stack || this.state.err)}
        </div>
      );
    }
    return this.props.children;
  }
}

const qc = new QueryClient({
  defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
});

createRoot(document.getElementById("root")!).render(
  <Boundary>
    <QueryClientProvider client={qc}>
      <Demo />
    </QueryClientProvider>
  </Boundary>
);
