import { useState, useEffect, useCallback } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Play, Square, Plus, Trash2, RefreshCw, AlertTriangle,
  Radio, Users, Layers, Activity, Clock, CheckCircle2, XCircle,
  Download, Save, Edit2, RotateCcw, Eye,
} from "lucide-react";
import { PageProps, LiveBatchTask, LiveInstance, TaskWithInstances, LiveBatchTemplate } from "../../api/client";
import { api } from "../../api/client";
import { PageContainer, PageHeader } from "@/components/layout/app-shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Modal, ModalHeader, ModalBody, ModalFooter, ConfirmDialog } from "@/components/ui/modal";
import { Section, Blank } from "@/components/page/kit";
import { cn } from "@/lib/utils";

interface GlobalStatus {
  global_enabled: boolean;
  global_max_concurrent: number;
  current_running: number;
  total_instances: number;
  error_count: number;
  tasks: {
    task_id: string;
    name: string;
    running: number;
    total: number;
  }[];
}

// ===========================================================================
// 主组件
// ===========================================================================

export default function LiveBatchPage({ push, ready }: PageProps) {
  // 注：本组件只用到 push / ready（数据全部走 api 模块单例）。
  // 作为「直播监听」的子页签嵌入时，父组件会传整份 PageProps —— 多余字段在此忽略。
  const queryClient = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [showTemplate, setShowTemplate] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);
  const [selectedTask, setSelectedTask] = useState<string | null>(null);
  const [selectedInstance, setSelectedInstance] = useState<string | null>(null);
  const [editingTask, setEditingTask] = useState<string | null>(null);

  // 全局状态轮询
  const globalQ = useQuery({
    queryKey: ["live-batch-global"],
    queryFn: () => api.getLiveBatchStatus(),
    enabled: !!ready,
    refetchInterval: 3000,
  });

  // 任务列表轮询
  const tasksQ = useQuery({
    queryKey: ["live-batch-tasks"],
    queryFn: () => api.listLiveBatchTasks(),
    enabled: !!ready,
    refetchInterval: 5000,
  });

  // 模板列表
  const templatesQ = useQuery({
    queryKey: ["live-batch-templates"],
    queryFn: () => api.listLiveBatchTemplates(),
    enabled: !!ready,
  });

  const global = globalQ.data as GlobalStatus | undefined;
  const tasks = (tasksQ.data?.tasks || []) as TaskWithInstances[];
  const templates = (templatesQ.data?.templates || []) as LiveBatchTemplate[];

  // 创建任务
  const createMut = useMutation({
    mutationFn: (data: CreateTaskData) => api.createLiveBatchTask(data),
    onSuccess: () => {
      push("任务已创建");
      setShowCreate(false);
      queryClient.invalidateQueries({ queryKey: ["live-batch-tasks"] });
    },
    onError: (e: any) => push(`创建失败: ${e.message || e}`),
  });

  // 更新任务
  const updateMut = useMutation({
    mutationFn: ({ taskId, data }: { taskId: string; data: Partial<CreateTaskData> }) =>
      api.updateLiveBatchTask(taskId, data),
    onSuccess: () => {
      push("任务已更新");
      setEditingTask(null);
      queryClient.invalidateQueries({ queryKey: ["live-batch-tasks"] });
    },
    onError: (e: any) => push(`更新失败: ${e.message || e}`),
  });

  // 启动任务
  const startMut = useMutation({
    mutationFn: (taskId: string) => api.startLiveBatchTask(taskId),
    onSuccess: (r) => {
      // 🔴 不静默吞「已在运行」：重复点启动时若只报「已启动」，
      // 用户无法判断自己到底有没有多开（假成功）。
      const started = r?.started?.length ?? 0;
      const already = r?.already?.length ?? 0;
      const failed = r?.failed?.length ?? 0;
      if (already > 0 && started === 0) {
        push(`任务已在运行（${already} 个实例已存在，未重复创建）`);
      } else {
        push(`任务已启动：新增 ${started} 个${already > 0 ? `，${already} 个已在运行` : ""}${failed > 0 ? `，${failed} 个失败` : ""}`);
      }
      queryClient.invalidateQueries({ queryKey: ["live-batch-tasks"] });
      queryClient.invalidateQueries({ queryKey: ["live-batch-global"] });
    },
    onError: (e: any) => push(`启动失败: ${e.message || e}`),
  });

  // 停止任务
  const stopMut = useMutation({
    mutationFn: (taskId: string) => api.stopLiveBatchTask(taskId),
    onSuccess: () => {
      push("任务已停止");
      queryClient.invalidateQueries({ queryKey: ["live-batch-tasks"] });
      queryClient.invalidateQueries({ queryKey: ["live-batch-global"] });
    },
    onError: (e: any) => push(`停止失败: ${e.message || e}`),
  });

  // 重启任务
  const restartMut = useMutation({
    mutationFn: (taskId: string) => api.restartLiveBatchTask(taskId),
    onSuccess: () => {
      push("任务已重启");
      queryClient.invalidateQueries({ queryKey: ["live-batch-tasks"] });
      queryClient.invalidateQueries({ queryKey: ["live-batch-global"] });
    },
    onError: (e: any) => push(`重启失败: ${e.message || e}`),
  });

  // 删除任务
  const deleteMut = useMutation({
    mutationFn: (taskId: string) => api.deleteLiveBatchTask(taskId),
    onSuccess: () => {
      push("任务已删除");
      setDeleteConfirm(null);
      queryClient.invalidateQueries({ queryKey: ["live-batch-tasks"] });
    },
    onError: (e: any) => push(`删除失败: ${e.message || e}`),
  });

  // 启动单个实例
  const startInstanceMut = useMutation({
    mutationFn: (instanceId: string) => api.startLiveBatchInstance(instanceId),
    onSuccess: () => {
      push("实例已启动");
      queryClient.invalidateQueries({ queryKey: ["live-batch-tasks"] });
    },
    onError: (e: any) => push(`启动实例失败: ${e.message || e}`),
  });

  // 停止单个实例
  const stopInstanceMut = useMutation({
    mutationFn: (instanceId: string) => api.stopLiveBatchInstance(instanceId),
    onSuccess: () => {
      push("实例已停止");
      queryClient.invalidateQueries({ queryKey: ["live-batch-tasks"] });
    },
    onError: (e: any) => push(`停止实例失败: ${e.message || e}`),
  });

  // 重启单个实例
  const restartInstanceMut = useMutation({
    mutationFn: (instanceId: string) => api.restartLiveBatchInstance(instanceId),
    onSuccess: () => {
      push("实例已重启");
      queryClient.invalidateQueries({ queryKey: ["live-batch-tasks"] });
    },
    onError: (e: any) => push(`重启实例失败: ${e.message || e}`),
  });

  // 保存模板（从某个已有任务「另存为模板」）
  const saveTemplateMut = useMutation({
    mutationFn: (data: CreateTaskData) => api.createLiveBatchTemplate(data),
    onSuccess: () => {
      push("模板已保存");
      queryClient.invalidateQueries({ queryKey: ["live-batch-templates"] });
    },
    onError: (e: any) => push(`保存模板失败: ${e.message || e}`),
  });

  // 删除模板
  const deleteTemplateMut = useMutation({
    mutationFn: (templateId: string) => api.deleteLiveBatchTemplate(templateId),
    onSuccess: () => {
      push("模板已删除");
      queryClient.invalidateQueries({ queryKey: ["live-batch-templates"] });
    },
    onError: (e: any) => push(`删除模板失败: ${e.message || e}`),
  });

  // 导出数据
  const handleExport = useCallback(async (taskId: string) => {
    try {
      const data = await api.exportLiveBatchTask(taskId);
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `live-batch-${taskId}-${Date.now()}.json`;
      a.click();
      URL.revokeObjectURL(url);
      push("数据已导出");
    } catch (e: any) {
      push(`导出失败: ${e.message || e}`);
    }
  }, [push]);

  const handleCreate = useCallback((data: CreateTaskData) => {
    createMut.mutate(data);
  }, [createMut]);

  const handleStart = useCallback((taskId: string) => {
    startMut.mutate(taskId);
  }, [startMut]);

  const handleStop = useCallback((taskId: string) => {
    stopMut.mutate(taskId);
  }, [stopMut]);

  const handleRestart = useCallback((taskId: string) => {
    restartMut.mutate(taskId);
  }, [restartMut]);

  const handleDelete = useCallback((taskId: string) => {
    deleteMut.mutate(taskId);
  }, [deleteMut]);

  /** 把一个已有任务的配置「另存为模板」（模板不带 enabled 语义） */
  const handleSaveTemplate = useCallback((task: LiveBatchTask) => {
    saveTemplateMut.mutate({
      name: task.name,
      rooms: task.rooms,
      accounts: task.accounts,
      strategy: task.strategy,
      max_concurrent: task.max_concurrent,
      enabled: true,
      dm_pool: task.dm_pool,
      delay_range: task.delay_range,
      interval: task.interval,
      max_target: task.max_target,
      tag_id: task.tag_id,
    });
  }, [saveTemplateMut]);

  const handleStartInstance = useCallback((instanceId: string) => {
    startInstanceMut.mutate(instanceId);
  }, [startInstanceMut]);

  const handleStopInstance = useCallback((instanceId: string) => {
    stopInstanceMut.mutate(instanceId);
  }, [stopInstanceMut]);

  const handleRestartInstance = useCallback((instanceId: string) => {
    restartInstanceMut.mutate(instanceId);
  }, [restartInstanceMut]);

  // 🔴 2026-10-03（用户定调）：总开关关闭**不代表页面不可访问** ——
  //   只应禁止「启动」，配置（新建/编辑/删除任务与模板）与查看必须照常可用。
  //   原实现在此整页 return 提示条 ⇒ 用户连任务都建不了、看不到已有配置，
  //   属于把「运行门」误做成「功能门」。现在改为**顶部一条提示横幅**，
  //   页面主体照常渲染，仅「启动/重启」按钮禁用。
  const batchOff = !!(global && !global.global_enabled);

  return (
    <PageContainer>
      <PageHeader title="批量采集" description="多房间 × 多账号并发监听" actions={
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={() => setShowTemplate(true)}
            disabled={!ready}
          >
            <Save className="h-4 w-4" />
            模板
          </Button>
          <Button
            size="sm"
            onClick={() => setShowCreate(true)}
            disabled={!ready}
          >
            <Plus className="h-4 w-4" />
            新建任务
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              queryClient.invalidateQueries({ queryKey: ["live-batch-tasks"] });
              queryClient.invalidateQueries({ queryKey: ["live-batch-global"] });
            }}
          >
            <RefreshCw className="h-4 w-4" />
          </Button>
        </div>
      } />

      {/* 关闭提示：横幅而非整页替换（配置与查看不受影响） */}
      {batchOff && (
        <Section className="mt-4">
          <div className="flex items-start gap-3">
            <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0 text-yellow-500" />
            <p className="text-sm opacity-80">
              批量采集总开关已关闭：可正常新建、编辑与查看任务，但无法启动监听。
              开启位置：设置 → 通用 / 启动 → 批量采集总开关。
            </p>
          </div>
        </Section>
      )}

      {/* 全局状态概览 */}
      {global && (
        <Section className="mt-4">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <StatusCard
              icon={<Activity className="h-4 w-4" />}
              label="运行中"
              value={`${global.current_running} / ${global.global_max_concurrent}`}
              tone="ok"
            />
            <StatusCard
              icon={<Layers className="h-4 w-4" />}
              label="总实例数"
              value={String(global.total_instances)}
            />
            <StatusCard
              icon={<XCircle className="h-4 w-4" />}
              label="异常数"
              value={String(global.error_count)}
              tone={global.error_count > 0 ? "error" : undefined}
            />
            <StatusCard
              icon={<Clock className="h-4 w-4" />}
              label="任务数"
              value={String(global.tasks.length)}
            />
          </div>
        </Section>
      )}

      {/* 任务列表 */}
      <Section className="mt-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-medium">任务列表</h3>
          <span className="text-xs opacity-60">{tasks.length} 个任务</span>
        </div>

        {tasks.length === 0 ? (
          <Blank>
            <div className="flex gap-2">
              <Button size="sm" onClick={() => setShowCreate(true)}>
                <Plus className="h-4 w-4" />
                创建第一个任务
              </Button>
              {templates.length > 0 && (
                <Button size="sm" variant="outline" onClick={() => setShowTemplate(true)}>
                  <Save className="h-4 w-4" />
                  从模板创建
                </Button>
              )}
            </div>
          </Blank>
        ) : (
          <div className="space-y-3">
            {tasks.map((t) => (
              <TaskCard
                key={t.task.task_id}
                task={t}
                onStart={() => handleStart(t.task.task_id)}
                onStop={() => handleStop(t.task.task_id)}
                onRestart={() => handleRestart(t.task.task_id)}
                onDelete={() => setDeleteConfirm(t.task.task_id)}
                onSelect={() => setSelectedTask(t.task.task_id)}
                selected={selectedTask === t.task.task_id}
                onEdit={() => setEditingTask(t.task.task_id)}
                onExport={() => handleExport(t.task.task_id)}
                onSaveTemplate={() => handleSaveTemplate(t.task)}
                startDisabled={batchOff}
                onStartInstance={handleStartInstance}
                onStopInstance={handleStopInstance}
                onRestartInstance={handleRestartInstance}
                selectedInstance={selectedInstance}
                onSelectInstance={setSelectedInstance}
              />
            ))}
          </div>
        )}
      </Section>

      {/* 创建任务弹窗 */}
      {showCreate && (
        <CreateTaskModal
          onClose={() => setShowCreate(false)}
          onSubmit={handleCreate}
          templates={templates}
        />
      )}

      {/* 编辑任务弹窗 */}
      {editingTask && (
        <EditTaskModal
          taskId={editingTask}
          onClose={() => setEditingTask(null)}
          onSubmit={(data) => updateMut.mutate({ taskId: editingTask, data })}
        />
      )}

      {/* 模板管理弹窗 */}
      {showTemplate && (
        <TemplateModal
          templates={templates}
          onClose={() => setShowTemplate(false)}
          onDelete={(id) => deleteTemplateMut.mutate(id)}
          onUse={(template) => {
            handleCreate({
              name: template.name,
              rooms: template.rooms,
              accounts: template.accounts,
              strategy: template.strategy,
              max_concurrent: template.max_concurrent,
              enabled: true,
              dm_pool: template.dm_pool,
              delay_range: template.delay_range,
              interval: template.interval,
              max_target: template.max_target,
              tag_id: template.tag_id,
            });
            setShowTemplate(false);
          }}
        />
      )}

      {/* 删除确认 */}
      {deleteConfirm && (
        <ConfirmDialog
          title="删除任务"
          message="确定要删除这个批量采集任务吗？所有运行中的实例都会被停止。"
          onConfirm={() => handleDelete(deleteConfirm)}
          onCancel={() => setDeleteConfirm(null)}
        />
      )}
    </PageContainer>
  );
}

// ===========================================================================
// 子组件
// ===========================================================================

function StatusCard({ icon, label, value, tone }: {
  icon: React.ReactNode;
  label: string;
  value: string;
  tone?: "ok" | "error" | "warning";
}) {
  return (
    <div className={cn(
      "rounded-lg border p-3",
      tone === "ok" && "border-green-500/30 bg-green-500/5",
      tone === "error" && "border-red-500/30 bg-red-500/5",
      tone === "warning" && "border-yellow-500/30 bg-yellow-500/5",
      !tone && "border-[var(--color-border)] bg-[var(--color-surface)]",
    )}>
      <div className="flex items-center gap-2 text-xs opacity-60">
        {icon}
        {label}
      </div>
      <div className="text-lg font-semibold mt-1">{value}</div>
    </div>
  );
}

function TaskCard({ task, onStart, onStop, onRestart, onDelete, onSelect, selected, onEdit, onExport, onSaveTemplate, startDisabled, onStartInstance, onStopInstance, onRestartInstance, selectedInstance, onSelectInstance }: {
  task: TaskWithInstances;
  onStart: () => void;
  onStop: () => void;
  onRestart: () => void;
  onDelete: () => void;
  onSelect: () => void;
  selected: boolean;
  onEdit: () => void;
  onExport: () => void;
  onSaveTemplate: () => void;
  /** 总开关关闭时禁用「启动/重启」；停止与配置不受影响 */
  startDisabled: boolean;
  onStartInstance: (instanceId: string) => void;
  onStopInstance: (instanceId: string) => void;
  onRestartInstance: (instanceId: string) => void;
  selectedInstance: string | null;
  onSelectInstance: (instanceId: string | null) => void;
}) {
  const isRunning = task.running_count > 0;
  const hasError = task.instances.some((i) => i.state === "error");

  return (
    <div
      className={cn(
        "rounded-lg border p-4 cursor-pointer transition-colors",
        selected ? "border-[var(--color-accent)] bg-[var(--color-accent-soft)]" : "border-[var(--color-border)] bg-[var(--color-surface)] hover:border-[var(--color-accent)]/50",
      )}
      onClick={onSelect}
    >
      <div className="flex items-start justify-between">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-medium truncate">{task.task.name}</span>
            {isRunning && <Badge variant="default">运行中</Badge>}
            {hasError && <Badge variant="danger">异常</Badge>}
            {!task.task.enabled && <Badge>已停用</Badge>}
          </div>
          <div className="flex items-center gap-4 mt-2 text-xs opacity-60">
            <span className="flex items-center gap-1">
              <Radio className="h-3 w-3" />
              {task.task.rooms.length} 房间
            </span>
            <span className="flex items-center gap-1">
              <Users className="h-3 w-3" />
              {task.task.accounts.length} 账号
            </span>
            <span className="flex items-center gap-1">
              <Activity className="h-3 w-3" />
              {task.running_count}/{task.total_count} 实例
            </span>
          </div>
        </div>
        <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
          {isRunning ? (
            <Button size="sm" variant="outline" onClick={onStop}>
              <Square className="h-3 w-3" />
              停止
            </Button>
          ) : (
            <Button size="sm" onClick={onStart}
              disabled={!task.task.enabled || startDisabled}
              title={startDisabled ? "批量采集总开关已关闭" : undefined}>
              <Play className="h-3 w-3" />
              启动
            </Button>
          )}
          <Button size="sm" variant="ghost" onClick={onRestart}
            disabled={startDisabled}
            title={startDisabled ? "批量采集总开关已关闭" : "重启任务"}>
            <RotateCcw className="h-3 w-3" />
          </Button>
          <Button size="sm" variant="ghost" onClick={onEdit} title="编辑任务">
            <Edit2 className="h-3 w-3" />
          </Button>
          <Button size="sm" variant="ghost" onClick={onExport} title="导出数据">
            <Download className="h-3 w-3" />
          </Button>
          <Button size="sm" variant="ghost" onClick={onSaveTemplate} title="另存为模板">
            <Save className="h-3 w-3" />
          </Button>
          <Button size="sm" variant="ghost" onClick={onDelete}>
            <Trash2 className="h-3 w-3" />
          </Button>
        </div>
      </div>

      {/* 实例列表 */}
      {selected && task.instances.length > 0 && (
        <div className="mt-3 pt-3 border-t border-[var(--color-border)]">
          <div className="space-y-2">
            {task.instances.map((inst) => (
              <InstanceRow
                key={inst.instance_id}
                instance={inst}
                selected={selectedInstance === inst.instance_id}
                onSelect={() => onSelectInstance(selectedInstance === inst.instance_id ? null : inst.instance_id)}
                onStart={() => onStartInstance(inst.instance_id)}
                onStop={() => onStopInstance(inst.instance_id)}
                onRestart={() => onRestartInstance(inst.instance_id)}
                startDisabled={startDisabled}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function InstanceRow({ instance, selected, onSelect, onStart, onStop, onRestart, startDisabled }: {
  instance: LiveInstance;
  selected: boolean;
  onSelect: () => void;
  onStart: () => void;
  onStop: () => void;
  onRestart: () => void;
  startDisabled: boolean;
}) {
  const stateConfig: Record<string, { icon: React.ReactNode; label: string; tone: string }> = {
    running: { icon: <CheckCircle2 className="h-3 w-3" />, label: "运行中", tone: "text-green-500" },
    starting: { icon: <Clock className="h-3 w-3" />, label: "启动中", tone: "text-yellow-500" },
    stopped: { icon: <Square className="h-3 w-3" />, label: "已停止", tone: "opacity-60" },
    error: { icon: <XCircle className="h-3 w-3" />, label: "异常", tone: "text-red-500" },
    idle: { icon: <Clock className="h-3 w-3" />, label: "空闲", tone: "opacity-60" },
  };
  const cfg = stateConfig[instance.state] || stateConfig.idle;
  const isRunning = instance.state === "running";

  return (
    <div
      className={cn(
        "flex items-center justify-between text-xs p-2 rounded cursor-pointer transition-colors",
        selected ? "bg-[var(--color-accent-soft)]" : "hover:bg-[var(--color-surface)]",
      )}
      onClick={onSelect}
    >
      <div className="flex items-center gap-2">
        <span className={cfg.tone}>{cfg.icon}</span>
        <span className="font-mono opacity-60">{instance.live_id}</span>
        <span className="opacity-60">·</span>
        <span>{instance.account}</span>
      </div>
      <div className="flex items-center gap-2">
        <span className={cn("px-1.5 py-0.5 rounded", cfg.tone)}>{cfg.label}</span>
        {instance.status_msg && (
          <span className="opacity-60 truncate max-w-[200px]">{instance.status_msg}</span>
        )}
        <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
          {isRunning ? (
            <Button size="sm" variant="ghost" onClick={onStop} title="停止实例">
              <Square className="h-3 w-3" />
            </Button>
          ) : (
            <Button size="sm" variant="ghost" onClick={onStart}
              disabled={startDisabled}
              title={startDisabled ? "批量采集总开关已关闭" : "启动实例"}>
              <Play className="h-3 w-3" />
            </Button>
          )}
          <Button size="sm" variant="ghost" onClick={onRestart}
            disabled={startDisabled}
            title={startDisabled ? "批量采集总开关已关闭" : "重启实例"}>
            <RotateCcw className="h-3 w-3" />
          </Button>
        </div>
      </div>
    </div>
  );
}

// ===========================================================================
// 创建任务弹窗
// ===========================================================================

interface CreateTaskData {
  name: string;
  rooms: string[];
  accounts: string[];
  strategy: string;
  max_concurrent: number;
  enabled: boolean;
  dm_pool: string[] | null;
  delay_range: number[] | null;
  interval: number;
  max_target: number;
  tag_id: string;
}

// ===========================================================================
// 编辑任务弹窗
// ===========================================================================

function EditTaskModal({ taskId, onClose, onSubmit }: {
  taskId: string;
  onClose: () => void;
  onSubmit: (data: Partial<CreateTaskData>) => void;
}) {
  const [name, setName] = useState("");
  const [rooms, setRooms] = useState("");
  const [selectedAccounts, setSelectedAccounts] = useState<string[]>([]);
  const [strategy, setStrategy] = useState("round_robin");
  const [maxConcurrent, setMaxConcurrent] = useState(3);
  const [maxTarget, setMaxTarget] = useState(3);
  // 2026-10-03（用户定调）：词库与发送参数复用「私信策略标签」。
  const [tagId, setTagId] = useState("");
  const [dmPool, setDmPool] = useState("");
  const [enabled, setEnabled] = useState(true);

  // 获取任务详情
  const taskQ = useQuery({
    queryKey: ["live-batch-task", taskId],
    queryFn: () => api.getLiveBatchTask(taskId),
  });

  const task = taskQ.data?.task;

  useEffect(() => {
    if (task) {
      setName(task.name);
      setRooms(task.rooms.join("\n"));
      setSelectedAccounts(task.accounts);
      setStrategy(task.strategy);
      setMaxConcurrent(task.max_concurrent);
      setMaxTarget(task.max_target ?? 3);
      setTagId(task.tag_id ?? "");
      setDmPool(task.dm_pool?.join("\n") || "");
      setEnabled(task.enabled);
    }
  }, [task]);

  // 获取账号列表（getAccounts 返回数组，不是 {accounts}）
  const accountsQ = useQuery({
    queryKey: ["accounts-for-batch"],
    queryFn: () => api.getAccounts() as Promise<{ name: string }[]>,
  });
  const availableAccounts = accountsQ.data || [];

  // 2026-10-03（用户定调「词库复用私信策略的标签」）：标签是词库与发送参数的真源。
  // 与直播监听页读同一份配置标签（api.listTags），两个模式口径一致。
  const tagsQ = useQuery({
    queryKey: ["live-tags"],
    queryFn: () => api.listTags(),
    staleTime: 30_000,
  });
  const configTags = (tagsQ.data?.tags || []) as { id: string; name: string }[];

  const handleSubmit = () => {
    const roomList = rooms.split("\n").map((r) => r.trim()).filter(Boolean);
    if (!name.trim() || roomList.length === 0 || selectedAccounts.length === 0) {
      return;
    }
    onSubmit({
      name: name.trim(),
      rooms: roomList,
      accounts: selectedAccounts,
      strategy,
      max_concurrent: maxConcurrent,
      enabled,
      dm_pool: dmPool.trim() ? dmPool.split("\n").map((s) => s.trim()).filter(Boolean) : null,
      delay_range: null,
      interval: 60,
      max_target: maxTarget,
      tag_id: tagId,
    });
  };

  return (
    <Modal onClose={onClose}>
      <ModalHeader title="编辑批量采集任务" onClose={onClose} />
      <ModalBody>
        <div className="space-y-4">
          <div>
            <label className="text-sm font-medium">任务名称</label>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="mt-1"
            />
          </div>

          <div>
            <label className="text-sm font-medium">直播间链接（每行一个）</label>
            <textarea
              value={rooms}
              onChange={(e) => setRooms(e.target.value)}
              className="mt-1 w-full h-24 px-3 py-2 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] text-sm resize-none"
            />
          </div>

          <div>
            <label className="text-sm font-medium">选择账号</label>
            <div className="mt-1 flex flex-wrap gap-2">
              {availableAccounts.map((a) => (
                <button
                  key={a.name}
                  onClick={() => {
                    setSelectedAccounts((prev) =>
                      prev.includes(a.name)
                        ? prev.filter((x) => x !== a.name)
                        : [...prev, a.name]
                    );
                  }}
                  className={cn(
                    "px-3 py-1 rounded-full text-sm border transition-colors",
                    selectedAccounts.includes(a.name)
                      ? "border-[var(--color-accent)] bg-[var(--color-accent-soft)] text-[var(--color-accent)]"
                      : "border-[var(--color-border)] hover:border-[var(--color-accent)]/50",
                  )}
                >
                  {a.name}
                </button>
              ))}
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-sm font-medium">分配策略</label>
              <select
                value={strategy}
                onChange={(e) => setStrategy(e.target.value)}
                className="mt-1 w-full px-3 py-2 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] text-sm"
              >
                <option value="round_robin">轮询分配</option>
                <option value="fixed">固定配对</option>
                <option value="cartesian">笛卡尔积</option>
              </select>
            </div>
            <div>
              <label className="text-sm font-medium">并发上限</label>
              <Input
                type="number"
                value={maxConcurrent}
                onChange={(e) => setMaxConcurrent(Number(e.target.value))}
                min={1}
                max={10}
                className="mt-1"
              />
            </div>
            <div>
              <label className="text-sm font-medium">每房私信上限</label>
              <Input
                type="number"
                value={maxTarget}
                onChange={(e) => setMaxTarget(Number(e.target.value))}
                min={1}
                max={999}
                className="mt-1"
              />
            </div>
          </div>

          <div>
            <label className="text-sm font-medium">私信策略标签（词库来源）</label>
            <select
              value={tagId}
              onChange={(e) => setTagId(e.target.value)}
              className="mt-1 w-full px-3 py-2 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] text-sm"
            >
              <option value="">不使用标签（用下方自带词库）</option>
              {configTags.map((t) => (
                <option key={t.id} value={t.id}>{t.name}</option>
              ))}
            </select>
            {tagId && (
              <p className="text-xs opacity-60 mt-1">
                词库、每房上限、间隔、延迟均以标签为准，下方自带项被覆盖
              </p>
            )}
          </div>

          <div>
            <label className="text-sm font-medium">私信词库（可选）</label>
            <textarea
              value={dmPool}
              onChange={(e) => setDmPool(e.target.value)}
              placeholder="每行一条，可留空"
              className="mt-1 w-full h-20 px-3 py-2 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] text-sm resize-none"
            />
          </div>

          <div className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
              className="rounded"
            />
            <label className="text-sm font-medium">启用任务</label>
          </div>
        </div>
      </ModalBody>
      <ModalFooter>
        <Button variant="outline" onClick={onClose}>取消</Button>
        <Button
          onClick={handleSubmit}
          disabled={!name.trim() || !rooms.trim() || selectedAccounts.length === 0}
        >
          保存
        </Button>
      </ModalFooter>
    </Modal>
  );
}

// ===========================================================================
// 模板管理弹窗
// ===========================================================================

function TemplateModal({ templates, onClose, onDelete, onUse }: {
  templates: LiveBatchTemplate[];
  onClose: () => void;
  onDelete: (templateId: string) => void;
  onUse: (template: LiveBatchTemplate) => void;
}) {
  return (
    <Modal onClose={onClose}>
      <ModalHeader title="任务模板" onClose={onClose} />
      <ModalBody>
        {templates.length === 0 ? (
          <Blank>
            <div className="flex flex-col items-center gap-2">
              <Save className="h-8 w-8" />
              <p>暂无模板</p>
              <p className="text-xs opacity-60">
                创建任务时保存为模板，可快速复用配置
              </p>
            </div>
          </Blank>
        ) : (
          <div className="space-y-2">
            {templates.map((t) => (
              <div
                key={t.id}
                className="flex items-center justify-between p-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]"
              >
                <div className="flex-1 min-w-0">
                  <div className="font-medium text-sm">{t.name}</div>
                  <div className="flex items-center gap-3 mt-1 text-xs opacity-60">
                    <span>{t.rooms.length} 房间</span>
                    <span>{t.accounts.length} 账号</span>
                    <span>{t.strategy}</span>
                  </div>
                </div>
                <div className="flex items-center gap-1">
                  <Button size="sm" variant="outline" onClick={() => onUse(t)}>
                    <Eye className="h-3 w-3" />
                    使用
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => onDelete(t.id)}>
                    <Trash2 className="h-3 w-3" />
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </ModalBody>
      <ModalFooter>
        <Button variant="outline" onClick={onClose}>关闭</Button>
      </ModalFooter>
    </Modal>
  );
}

function CreateTaskModal({ onClose, onSubmit, templates }: {
  onClose: () => void;
  onSubmit: (data: CreateTaskData) => void;
  templates: LiveBatchTemplate[];
}) {
  const [name, setName] = useState("");
  const [rooms, setRooms] = useState("");
  const [selectedAccounts, setSelectedAccounts] = useState<string[]>([]);
  const [strategy, setStrategy] = useState("round_robin");
  const [maxConcurrent, setMaxConcurrent] = useState(3);
  const [maxTarget, setMaxTarget] = useState(3);
  // 2026-10-03（用户定调）：词库与发送参数复用「私信策略标签」。
  const [tagId, setTagId] = useState("");
  const [dmPool, setDmPool] = useState("");

  // 获取账号列表（getAccounts 返回数组，不是 {accounts}）
  const accountsQ = useQuery({
    queryKey: ["accounts-for-batch"],
    queryFn: () => api.getAccounts() as Promise<{ name: string }[]>,
  });
  const availableAccounts = accountsQ.data || [];

  // 2026-10-03（用户定调「词库复用私信策略的标签」）：标签是词库与发送参数的真源。
  // 与直播监听页读同一份配置标签（api.listTags），两个模式口径一致。
  const tagsQ = useQuery({
    queryKey: ["live-tags"],
    queryFn: () => api.listTags(),
    staleTime: 30_000,
  });
  const configTags = (tagsQ.data?.tags || []) as { id: string; name: string }[];

  const handleSubmit = () => {
    const roomList = rooms.split("\n").map((r) => r.trim()).filter(Boolean);
    if (!name.trim() || roomList.length === 0 || selectedAccounts.length === 0) {
      return;
    }
    onSubmit({
      name: name.trim(),
      rooms: roomList,
      accounts: selectedAccounts,
      strategy,
      max_concurrent: maxConcurrent,
      enabled: true,
      dm_pool: dmPool.trim() ? dmPool.split("\n").map((s) => s.trim()).filter(Boolean) : null,
      delay_range: null,
      interval: 60,
      max_target: maxTarget,
      tag_id: tagId,
    });
  };

  const handleUseTemplate = (template: LiveBatchTemplate) => {
    setName(template.name);
    setRooms(template.rooms.join("\n"));
    setSelectedAccounts(template.accounts);
    setStrategy(template.strategy);
    setMaxConcurrent(template.max_concurrent);
    setMaxTarget(template.max_target ?? 3);
    setTagId(template.tag_id ?? "");
    setDmPool(template.dm_pool?.join("\n") || "");
  };

  return (
    <Modal onClose={onClose}>
      <ModalHeader title="创建批量采集任务" onClose={onClose} />
      <ModalBody>
        <div className="space-y-4">
          {/* 模板选择 */}
          {templates.length > 0 && (
            <div>
              <label className="text-sm font-medium">从模板创建</label>
              <div className="mt-1 flex flex-wrap gap-2">
                {templates.map((t) => (
                  <button
                    key={t.id}
                    onClick={() => handleUseTemplate(t)}
                    className="px-3 py-1 rounded-full text-sm border border-[var(--color-border)] hover:border-[var(--color-accent)]/50 transition-colors"
                  >
                    {t.name}
                  </button>
                ))}
              </div>
            </div>
          )}

          <div>
            <label className="text-sm font-medium">任务名称</label>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="例如：多房间采集"
              className="mt-1"
            />
          </div>

          <div>
            <label className="text-sm font-medium">直播间链接（每行一个）</label>
            <textarea
              value={rooms}
              onChange={(e) => setRooms(e.target.value)}
              placeholder={"https://live.douyin.com/123456\nhttps://live.douyin.com/789012"}
              className="mt-1 w-full h-24 px-3 py-2 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] text-sm resize-none"
            />
          </div>

          <div>
            <label className="text-sm font-medium">选择账号</label>
            <div className="mt-1 flex flex-wrap gap-2">
              {availableAccounts.map((a) => (
                <button
                  key={a.name}
                  onClick={() => {
                    setSelectedAccounts((prev) =>
                      prev.includes(a.name)
                        ? prev.filter((x) => x !== a.name)
                        : [...prev, a.name]
                    );
                  }}
                  className={cn(
                    "px-3 py-1 rounded-full text-sm border transition-colors",
                    selectedAccounts.includes(a.name)
                      ? "border-[var(--color-accent)] bg-[var(--color-accent-soft)] text-[var(--color-accent)]"
                      : "border-[var(--color-border)] hover:border-[var(--color-accent)]/50",
                  )}
                >
                  {a.name}
                </button>
              ))}
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-sm font-medium">分配策略</label>
              <select
                value={strategy}
                onChange={(e) => setStrategy(e.target.value)}
                className="mt-1 w-full px-3 py-2 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] text-sm"
              >
                <option value="round_robin">轮询分配</option>
                <option value="fixed">固定配对</option>
                <option value="cartesian">笛卡尔积</option>
              </select>
            </div>
            <div>
              <label className="text-sm font-medium">并发上限</label>
              <Input
                type="number"
                value={maxConcurrent}
                onChange={(e) => setMaxConcurrent(Number(e.target.value))}
                min={1}
                max={10}
                className="mt-1"
              />
            </div>
            <div>
              <label className="text-sm font-medium">每房私信上限</label>
              <Input
                type="number"
                value={maxTarget}
                onChange={(e) => setMaxTarget(Number(e.target.value))}
                min={1}
                max={999}
                className="mt-1"
              />
            </div>
          </div>

          <div>
            <label className="text-sm font-medium">私信策略标签（词库来源）</label>
            <select
              value={tagId}
              onChange={(e) => setTagId(e.target.value)}
              className="mt-1 w-full px-3 py-2 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] text-sm"
            >
              <option value="">不使用标签（用下方自带词库）</option>
              {configTags.map((t) => (
                <option key={t.id} value={t.id}>{t.name}</option>
              ))}
            </select>
            {tagId && (
              <p className="text-xs opacity-60 mt-1">
                词库、每房上限、间隔、延迟均以标签为准，下方自带项被覆盖
              </p>
            )}
          </div>

          <div>
            <label className="text-sm font-medium">私信词库（每行一条，可选）</label>
            <textarea
              value={dmPool}
              onChange={(e) => setDmPool(e.target.value)}
              placeholder={"您好，看到您的直播很感兴趣\n想了解一下相关产品"}
              className="mt-1 w-full h-20 px-3 py-2 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] text-sm resize-none"
            />
          </div>
        </div>
      </ModalBody>
      <ModalFooter>
        <Button variant="outline" onClick={onClose}>取消</Button>
        <Button
          onClick={handleSubmit}
          disabled={!name.trim() || !rooms.trim() || selectedAccounts.length === 0}
        >
          创建
        </Button>
      </ModalFooter>
    </Modal>
  );
}
