import { type Dispatch, type SetStateAction } from "react";
import { CheckCircle2, Lightbulb, X, XCircle } from "lucide-react";
import { motion } from "framer-motion";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Avatar } from "../../components/ui";
import { FormField, Tone, Section, Toolbar } from "@/components/page/kit";
import { PanelTitle, type FmtAccount, type ProxyForm } from "./accounts-shared";

export function ProxyDrawer({
  account,
  form,
  setForm,
  testing,
  testResult,
  onTest,
  onSave,
  onClose,
}: {
  account: FmtAccount;
  form: ProxyForm;
  setForm: Dispatch<SetStateAction<ProxyForm>>;
  testing: boolean;
  testResult: { ok: boolean; msg: string } | null;
  onTest: () => void;
  onSave: () => void;
  onClose: () => void;
}) {
  const a = account;
  // 代理模式（环境门阀）：模式由这里的选择单方面决定，与本机环境无关。
  //   system → 走系统代理；direct → 走本机 IP；socks5/http/https → 走独立节点。
  const proxyTypes: { id: string; label: string; desc: string }[] = [
    { id: "socks5", label: "SOCKS5", desc: "独立节点 · 支持UDP/TCP" },
    { id: "http", label: "HTTP", desc: "独立节点 · 兼容性好" },
    { id: "https", label: "HTTPS", desc: "独立节点 · 加密传输" },
    { id: "system", label: "系统代理", desc: "跟随本机系统代理设置" },
    { id: "direct", label: "不走代理", desc: "走本机 IP · 豁免代理端口" },
  ];
  return (
    <>
      <motion.div
        key="proxy-backdrop"
        className="fixed inset-0 z-[60] bg-black/55 backdrop-blur-sm"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        transition={{ duration: 0.15 }}
        onClick={onClose}
      />
      <motion.div
        key="proxy-drawer"
        className="fixed bottom-0 right-0 top-0 z-[61] flex w-[480px] max-w-[92vw]
                   flex-col border-l border-[var(--color-border)]
                   bg-[var(--color-background)] shadow-[var(--shadow-lg)]"
        initial={{ x: "100%" }}
        animate={{ x: 0 }}
        exit={{ x: "100%" }}
        transition={{ type: "spring", damping: 28, stiffness: 320 }}
        data-od-id="proxy-drawer"
      >
        <div className="flex shrink-0 items-center gap-2.5 border-b border-[var(--color-border)]
                        bg-[var(--color-surface)] px-4.5 py-3.5">
          <Avatar name={a.name} h={a.hue} />
          <div className="flex-1">
            <div className="text-[0.88rem] font-semibold text-[var(--color-text)]">
              代理配置 · {a.name}
            </div>
            <div className="font-mono text-[0.72rem] text-[var(--color-text-muted)]">
              UID: {a.uid} · {a.fp.name}
            </div>
          </div>
          <Tone tone={a.fp.status === "running" ? "ok" : "warn"}>
            {a.fp.status === "running" ? "运行中" : "已停止"}
          </Tone>
          <Button variant="ghost" size="icon-sm" onClick={onClose} aria-label="关闭">
            <X className="h-4 w-4" />
          </Button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-4.5 pb-6 pt-4">
          <Section className="mb-3.5">
            <PanelTitle>代理类型</PanelTitle>
            <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
              {proxyTypes.map((pt) => (
                <button
                  key={pt.id}
                  className={cn(
                    "cursor-pointer rounded-[var(--radius-sm)] px-3 py-2.5 text-left",
                    "transition-colors duration-[var(--duration-fast)]",
                    form.type === pt.id
                      ? "border-2 border-[var(--color-accent)] bg-[var(--color-accent-soft)]"
                      : "border border-[var(--color-border)] bg-[var(--color-surface)] hover:bg-[var(--color-surface-raised)]"
                  )}
                  onClick={() => setForm((f) => ({ ...f, type: pt.id }))}
                >
                  <div
                    className={cn(
                      "text-[0.8rem] font-semibold",
                      form.type === pt.id
                        ? "text-[var(--color-accent)]"
                        : "text-[var(--color-text)]"
                    )}
                  >
                    {pt.label}
                  </div>
                  <div className="mt-0.5 text-[0.68rem] text-[var(--color-text-muted)]">
                    {pt.desc}
                  </div>
                </button>
              ))}
            </div>
          </Section>

          {form.type !== "direct" && form.type !== "system" && (
            <Section className="mb-3.5">
              <PanelTitle>连接设置</PanelTitle>
              <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                <FormField label="主机地址">
                  <Input
                    className="font-mono"
                    placeholder="127.0.0.1"
                    value={form.host}
                    onChange={(e) => setForm((f) => ({ ...f, host: e.target.value }))}
                  />
                </FormField>
                <FormField label="端口">
                  <Input
                    className="font-mono"
                    placeholder="1080"
                    value={form.port}
                    onChange={(e) => setForm((f) => ({ ...f, port: e.target.value }))}
                  />
                </FormField>
                <FormField label={<>用户名 <span className="font-normal text-[var(--color-text-muted)]">(可选)</span></>}>
                  <Input
                    placeholder="留空则无认证"
                    value={form.user}
                    onChange={(e) => setForm((f) => ({ ...f, user: e.target.value }))}
                  />
                </FormField>
                <FormField label={<>密码 <span className="font-normal text-[var(--color-text-muted)]">(可选)</span></>}>
                  <Input
                    type="password"
                    placeholder="留空则无认证"
                    value={form.pass}
                    onChange={(e) => setForm((f) => ({ ...f, pass: e.target.value }))}
                  />
                </FormField>
              </div>
              <div
                className="mt-3 flex items-center gap-2 rounded-[var(--radius-sm)]
                           bg-[var(--color-surface-raised)] px-3.5 py-2.5 text-[0.75rem]
                           text-[var(--color-text-muted)]"
              >
                <Lightbulb className="h-4 w-4 shrink-0 text-[var(--color-warning)]" />
                <span>
                  完整地址：
                  <b className="font-mono font-semibold text-[var(--color-text)]">
                    {form.type}://{form.host}
                    {form.port ? ":" + form.port : ""}
                  </b>
                </span>
              </div>
            </Section>
          )}

          <Section className="mb-3.5">
            <PanelTitle>连接测试</PanelTitle>
            <div className="mb-2 text-[0.72rem] text-[var(--color-text-muted)]">
              返回真实出口 IP 与归属地
            </div>
            <Toolbar className="mb-3">
              <Input
                className="flex-1"
                placeholder="测试目标 URL（默认 https://www.douyin.com）"
                value={form.testUrl}
                onChange={(e) => setForm((f) => ({ ...f, testUrl: e.target.value }))}
              />
              <Button disabled={testing} onClick={onTest}>
                {testing ? "测试中…" : "测试连接"}
              </Button>
            </Toolbar>
            {testResult && (
              <div
                className={cn(
                  "flex items-center gap-2 rounded-[var(--radius-sm)] px-3.5 py-2.5 text-[0.8rem]",
                  testResult.ok
                    ? "bg-[var(--color-success-soft)]"
                    : "bg-[var(--color-danger-soft)]"
                )}
              >
                {testResult.ok ? (
                  <CheckCircle2 className="h-4 w-4 shrink-0 text-[var(--color-success)]" />
                ) : (
                  <XCircle className="h-4 w-4 shrink-0 text-[var(--color-danger)]" />
                )}
                <div>
                  <div
                    className={cn(
                      "font-semibold",
                      testResult.ok
                        ? "text-[var(--color-success)]"
                        : "text-[var(--color-danger)]"
                    )}
                  >
                    {testResult.ok ? "连接成功" : "连接失败"}
                  </div>
                  <div className="mt-0.5 whitespace-pre-line text-[0.75rem] leading-relaxed
                                  text-[var(--color-text-muted)]">
                    {testResult.msg}
                  </div>
                </div>
              </div>
            )}
            {(form.type === "direct" || form.type === "system") && (
              <div
                className="rounded-[var(--radius-sm)] bg-[var(--color-surface-raised)]
                           px-3.5 py-2.5 text-[0.75rem] text-[var(--color-text-muted)]"
              >
                {form.type === "direct"
                  ? "直连：以本机 IP 发出，无需测试"
                  : "跟随系统代理设置，无需填节点"}
              </div>
            )}
          </Section>

          <Section>
            <PanelTitle>当前配置预览</PanelTitle>
            <div
              className="grid grid-cols-[80px_1fr] gap-x-2.5 gap-y-1.5 text-[0.78rem]"
            >
              <span className="text-[var(--color-text-muted)]">代理类型</span>
              <span className="font-mono font-semibold">{form.type.toUpperCase()}</span>
              <span className="text-[var(--color-text-muted)]">地址</span>
              <span className="font-mono">
                {form.type === "direct" ? "直连" : form.host + (form.port ? ":" + form.port : "")}
              </span>
              <span className="text-[var(--color-text-muted)]">认证</span>
              <span className="font-mono">{form.user ? form.user + " / ••••" : "无"}</span>
              <span className="text-[var(--color-text-muted)]">影响账号</span>
              <span>
                {a.name} ({a.uid})
              </span>
            </div>
          </Section>
        </div>

        <div className="flex shrink-0 justify-end gap-2 border-t border-[var(--color-border)]
                        bg-[var(--color-surface)] px-4.5 py-3">
          <Button variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button onClick={onSave}>
            保存配置
          </Button>
        </div>
      </motion.div>
    </>
  );
}

// ===== 新增/编辑账号抽屉 =====
