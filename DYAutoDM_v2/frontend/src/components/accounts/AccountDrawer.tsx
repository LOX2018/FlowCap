import { type Dispatch, type SetStateAction } from "react";
import { X } from "lucide-react";
import { motion } from "framer-motion";
import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/input";
import { Avatar } from "../../components/ui";
import { FormField, Section } from "@/components/page/kit";
import { PanelTitle, type FmtAccount, type AcctForm } from "./accounts-shared";

export function AccountDrawer({
  form,
  setForm,
  onSave,
  onClose,
  mode = "add",
  account = null,
  onBackupLogin,
}: {
  form: AcctForm;
  setForm: Dispatch<SetStateAction<AcctForm>>;
  onSave: () => void;
  onClose: () => void;
  mode?: "add" | "edit";
  account?: FmtAccount | null;
  /** 显式备用：RPA 自动出二维码 / 短信验证码（仅编辑态提供；默认走手动）。 */
  onBackupLogin?: (name: string, mode: "qr" | "sms") => void;
}) {
  const isEdit = mode === "edit";
  return (
    <>
      <motion.div
        key={"acct-backdrop-" + mode}
        className="fixed inset-0 z-[60] bg-black/55 backdrop-blur-sm"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        transition={{ duration: 0.15 }}
        onClick={onClose}
      />
      <motion.div
        key={"acct-drawer-" + mode}
        className="fixed bottom-0 right-0 top-0 z-[61] flex w-[480px] max-w-[92vw]
                   flex-col border-l border-[var(--color-border)]
                   bg-[var(--color-background)] shadow-[var(--shadow-lg)]"
        initial={{ x: "100%" }}
        animate={{ x: 0 }}
        exit={{ x: "100%" }}
        transition={{ type: "spring", damping: 28, stiffness: 320 }}
        data-od-id={isEdit ? "acct-edit-drawer" : "acct-add-drawer"}
      >
        <div className="flex shrink-0 items-center gap-2.5 border-b border-[var(--color-border)]
                        bg-[var(--color-surface)] px-4.5 py-3.5">
          <Avatar name={isEdit ? account?.name || "+" : "+"} h={isEdit ? account?.hue || "200" : "200"} />
          <div className="flex-1">
            <div className="text-[0.88rem] font-semibold text-[var(--color-text)]">
              {isEdit ? "编辑账号信息" : "新增授权账号"}
            </div>
            <div className="font-mono text-[0.72rem] text-[var(--color-text-muted)]">
              {isEdit ? "修改账号信息并手动更新登录凭证" : "授权新抖音账号并创建指纹环境"}
            </div>
          </div>
          <Button variant="ghost" size="icon-sm" onClick={onClose} aria-label="关闭">
            <X className="h-4 w-4" />
          </Button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-4.5 pb-6 pt-4">
          <Section className="mb-3.5">
            <PanelTitle>账号信息</PanelTitle>
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              <FormField label="昵称">
                <Input
                  placeholder="如：阿强探店"
                  value={form.name}
                  onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                  autoFocus
                />
              </FormField>
              <FormField
                label={
                  <>
                    UID{" "}
                    <span className="font-normal text-[var(--color-text-muted)]">
                      (可选 · 扫码后自动获取)
                    </span>
                  </>
                }
              >
                <Input
                  className="font-mono"
                  placeholder="留空即可，扫码后自动读入"
                  value={form.uid}
                  onChange={(e) => setForm((f) => ({ ...f, uid: e.target.value }))}
                />
              </FormField>
            </div>
            {!isEdit && (
              <FormField
                className="mt-3"
                label={
                  <>
                    Cookie{" "}
                    <span className="font-normal text-[var(--color-text-muted)]">
                      (可选 · 留空则扫码授权)
                    </span>
                  </>
                }
              >
                <Textarea
                  className="font-mono"
                  rows={3}
                  placeholder="粘贴 Cookie 字符串，用于免扫码登录"
                  value={form.cookie}
                  onChange={(e) => setForm((f) => ({ ...f, cookie: e.target.value }))}
                />
              </FormField>
            )}
          </Section>
          <div
            className="rounded-[var(--radius-sm)] bg-[var(--color-surface-raised)]
                       px-3.5 py-2.5 text-[0.75rem] leading-relaxed
                       text-[var(--color-text-muted)]"
          >
            {isEdit
              ? "点击「手动更新凭证」默认打开有头指纹浏览器，由你手动完成扫码/验证码/滑块，凭证自动写回该账号。若需沿用自动化：下方「扫码备用（自动出二维码）」或「短信备用（自动发验证码）」为显式备用路径。"
              : "保存后将打开内置指纹浏览器，请在其中手动完成抖音登录，凭证自动写回该账号；之后可在账号卡片中配置代理与凭证校验。"}
          </div>
        </div>

        <div className="flex shrink-0 justify-end gap-2 border-t border-[var(--color-border)]
                        bg-[var(--color-surface)] px-4.5 py-3">
          <Button variant="ghost" onClick={onClose}>
            取消
          </Button>
          {isEdit && onBackupLogin && (
            <>
              <Button
                variant="ghost"
                size="sm"
                title="备用路径：自动抓取二维码（可经 IM 推送），适合远程扫码"
                onClick={() => form.name.trim() && onBackupLogin(form.name.trim(), "qr")}
                disabled={!form.name.trim()}
              >
                扫码备用
              </Button>
              <Button
                variant="ghost"
                size="sm"
                title="备用路径：自动发短信验证码到绑定手机"
                onClick={() => form.name.trim() && onBackupLogin(form.name.trim(), "sms")}
                disabled={!form.name.trim()}
              >
                短信备用
              </Button>
            </>
          )}
          <Button onClick={onSave} disabled={!form.name.trim()}>
            {isEdit ? "手动更新凭证" : "确认新增"}
          </Button>
        </div>
      </motion.div>
    </>
  );
}
