/**
 * 浮动玻璃胶囊顶栏 —— 移植自 zn0wii/satelite-proxy（src/components/TopNav.tsx）。
 *
 * 与原版的差异：
 *   · 保留本项目原有内容：品牌 + 版本号、9 个 tab、IM 网关待授权角标、
 *     「已连接」徽章、会员徽章、退出按钮；
 *   · 新增：滑动玻璃指示器（跟随选中 tab）、主题切换胶囊；
 *   · 原版右侧的「UI 模式菜单」本项目没有对应功能，不移植。
 *
 * 指示器为何用 offsetLeft 而不是 getBoundingClientRect：
 *   指示器是 .topnav-items 的子元素，随内容一起横向滚动；
 *   offsetLeft 是相对该容器（position:relative）的布局坐标，滚动时不需要重算，
 *   而 getBoundingClientRect 得到的是视口坐标，滚动后必须重新测量。
 */
import { useLayoutEffect, useRef, useState } from "react";
import { Dot, TABS } from "./ui";
import ThemeSwitch from "./ThemeSwitch";

export default function TopNav({
  tab,
  setTab,
  ready,
  memberName,
  onLogout,
  gwPending,
}: {
  tab: string;
  setTab: (t: string) => void;
  ready: boolean;
  memberName?: string;
  onLogout?: () => void;
  gwPending: number;
}) {
  const btnRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  const [ind, setInd] = useState({ left: 0, width: 0, ready: false });

  // 指示器跟随选中项。窗口尺寸、tab 列表变化、字体加载完成都要重算。
  useLayoutEffect(() => {
    const measure = () => {
      const el = btnRefs.current[tab];
      if (!el) return;
      setInd({ left: el.offsetLeft, width: el.offsetWidth, ready: true });
    };
    measure();
    // 字体（JetBrains Mono）异步加载完会让按钮宽度变化，迟一拍再校一次
    const t = window.setTimeout(measure, 150);
    window.addEventListener("resize", measure);
    return () => {
      window.clearTimeout(t);
      window.removeEventListener("resize", measure);
    };
  }, [tab]);

  return (
    <div className="topnav-wrap">
      <header className="topnav">
        <span className="topnav-glow" aria-hidden="true" />

        <div className="topnav-brand">
          <span className="topnav-mark" aria-hidden="true" />
          <h1>抖音数据控制台</h1>
          <span className="sub">Douyin Console</span>
          {/* 2026-08-31：显示版本号。排查「改了代码但界面没变」时，
              第一件事就是确认跑的是哪个版本。 */}
          <span className="appver" title="应用版本">
            v{__APP_VERSION__}
          </span>
        </div>

        <span className="topnav-divider" aria-hidden="true" />

        <nav className="topnav-items" aria-label="主导航">
          {/* 滑动玻璃指示器：宽度/位移由 state 驱动，CSS 负责缓动 */}
          <span
            className="topnav-indicator"
            aria-hidden="true"
            style={{
              transform: `translateX(${ind.left}px)`,
              width: ind.width,
              opacity: ind.ready ? 1 : 0,
            }}
          />
          {TABS.map(([id, label]) => (
            <button
              key={id}
              ref={(el) => {
                btnRefs.current[id] = el;
              }}
              className={"topnav-item" + (tab === id ? " active" : "")}
              onClick={() => setTab(id)}
            >
              {label}
              {/* IM 网关待授权角标（v0.38.5）：设置 tab 上提示有待审来源 */}
              {id === "settings" && gwPending > 0 && (
                <span
                  style={{
                    marginLeft: 5,
                    background: "var(--warn, #d89614)",
                    color: "#fff",
                    borderRadius: 999,
                    fontSize: 10.5,
                    padding: "0 6px",
                    lineHeight: "16px",
                    display: "inline-block",
                    verticalAlign: "middle",
                  }}
                  title={`${gwPending} 个来源待授权（设置 → 通知与指令）`}
                >
                  {gwPending}
                </span>
              )}
            </button>
          ))}
        </nav>

        <div className="topnav-tools">
          {/* 2026-09-10：引擎/凭证守护/私信守护/已发 徽章已删（状态只在账号管理页展示）。 */}
          {ready ? (
            <span className="topnav-status" title="后端连接状态">
              <Dot c="ok" pulse /> 已连接
            </span>
          ) : (
            <span className="topnav-status">未连接</span>
          )}
          {memberName && (
            <span className="badge-conn member-badge" title="当前会员">
              <Dot c="ok" /> {memberName}
            </span>
          )}
          <ThemeSwitch />
          {onLogout && (
            <button className="member-logout" onClick={onLogout} title="退出登录">
              退出
            </button>
          )}
        </div>
      </header>
    </div>
  );
}
