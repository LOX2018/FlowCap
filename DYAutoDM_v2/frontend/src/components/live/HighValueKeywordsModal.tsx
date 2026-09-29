/**
 * `HighValueKeywordsModal` —— 高价值关键词权重表**编辑弹窗**（2026-09-29 从设置页迁入直播页）。
 *
 * ## 为什么迁（用户 2026-09-29 指令：按钮方案融入 `live-room-configs`）
 *
 * 该权重表决定 `is_high_value` → 高价值窗口时长（`high_value_window_seconds`）
 * → 最终是否被**发送闸门**拦下，属**直播发送策略**的一部分。
 * 原先只挂在「设置 → 直播」tab 内（`settings-page.tsx`），用户在直播页调策略时
 * **看不到也改不了** ⇒ 入口与使用场景分离，用户只能记住「要改策略得跑去设置页」。
 * 现把入口放到直播页「直播间」区的「管理策略」旁，点开即编辑，与策略同场景。
 *
 * ## 设计约定（与 `HighValueKeywordsSection` 同源，不复制第二份编辑逻辑）
 *
 * 1. **复用同一组件**：内部直接渲染 `HighValueKeywordsSection`（同一 API、同一
 *    保存前校验），故两处入口在结构上不可能漂移（SSOT）。`embedded` 让该卡在
 *    弹窗里去壳（不再带 `mt-3` 外边距）。
 * 2. **不做第二套表单**：若将来权重表逻辑演进，只改 `HighValueKeywordsSection` 一处。
 * 3. **点击遮罩关闭**，与同页 `alert` 弹窗、`RoomConfigPage` 的交互一致。
 */

import { X } from "lucide-react";
import HighValueKeywordsSection from "@/components/settings/HighValueKeywordsSection";

export default function HighValueKeywordsModal({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  // 与 RoomConfigPage / RoomManagePage 同款：关闭态直接不渲染（零 DOM 残留）。
  if (!open) return null;
  return (
    <div
      data-od-id="high-value-keywords-modal"
      className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/55
                 p-5 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="flex max-h-[86vh] w-full max-w-[760px] flex-col overflow-hidden
                   rounded-[var(--radius-lg)] border border-[var(--color-border)]
                   bg-[var(--color-surface-solid)] shadow-[var(--shadow-lg)]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between gap-3 border-b
                        border-[var(--color-border)] px-4 py-3">
          <div className="min-w-0">
            <div className="text-[0.95rem] font-semibold text-[var(--color-text)]">
              高价值关键词权重
            </div>
            <div className="mt-0.5 text-[0.72rem] text-[var(--color-text-muted)]">
              弹幕 / 评论命中即累加权重，命中总分 ≥ 阈值即判为高价值（决定高价值窗口，
              进而影响是否被发送闸门拦下）。阈值（设置 → 发送 → 高价值关键词阈值）为{" "}
              <b>0</b> 时本表不参与判定。
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
        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          <HighValueKeywordsSection embedded />
        </div>
      </div>
    </div>
  );
}
