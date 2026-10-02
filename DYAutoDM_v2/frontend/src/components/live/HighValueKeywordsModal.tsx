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

import HighValueKeywordsSection from "@/components/settings/HighValueKeywordsSection";
import { Modal, ModalHeader, ModalBody } from "@/components/ui/modal";

export default function HighValueKeywordsModal({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  // 与 RoomConfigPage / RoomManagePage 同款：关闭态直接不渲染（零 DOM 残留）。
  // 2026-10-02：并入统一 Modal（原 z-[9999] + surface-solid）。
  if (!open) return null;
  return (
    <Modal open onClose={onClose} maxWidth="760px" labelledBy="hvk-title">
      <ModalHeader
        onClose={onClose}
        title={<span id="hvk-title">高价值关键词权重</span>}
        description={<>命中即累加权重，总分 ≥ 阈值判为高价值（阈值为 <b>0</b> 时不参与）</>}
      />
      <ModalBody>
        <HighValueKeywordsSection embedded />
      </ModalBody>
    </Modal>
  );
}
