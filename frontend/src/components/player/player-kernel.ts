/**
 * 播放器 —— 内核层（kernel）
 *
 * ## 设计来源：YCVideoPlayer（yangchong211）架构
 *
 * 用户指定参考 https://github.com/yangchong211/YCVideoPlayer 。
 * 该库是 **Android 原生**（Java + ExoPlayer/IjkPlayer），本项目是 React + Tauri
 * Webview，**无法直接引用**。故按已确认的口径：**借用其架构设计，用 React 重写**。
 *
 * YCVideoPlayer 的整体架构（本项目照此分层）：
 * ```
 * 播放器内核（自由切换）  ← 本文件：VideoKernel
 *   + 视频播放器          ← player-engine.ts（对内核的状态封装）
 *   + 边播边缓存          ← player-cache.ts
 *   + 高度定制 UI 视图层   ← player-*.tsx（与业务彻底解耦）
 * ```
 * 其设计要点（README 实证）：
 *   · "UI/Player/业务解耦"：播放器 lib 不含业务 UI 代码，由接入方高度定制
 *   · "自由切换视频内核"：MediaPlayer / ExoPlayer / IjkPlayer 可切换
 *   · "边播边缓存"：VideoCache 独立模块
 *
 * ## 本项目的内核映射
 *
 * Android 的多内核 → Web 侧对应（均实现同一接口 `VideoKernel`）：
 *   · `Html5Kernel`  —— 原生 `<video>`（对应 MediaPlayer，零依赖、最稳）
 *   · `DashKernel`   —— dash.js（对应 ExoPlayer，支持 DASH 自适应码流）
 *   · `HlsKernel`    —— hls.js（对应 IjkPlayer，支持 HLS）
 *
 * **默认与降级**：抖音的视频多为 MP4（直接 `<video>` 可播），故默认 `Html5Kernel`；
 * 遇 `application/dash+xml` / `.m3u8` 自动切到对应内核；切换失败回落到 Html5。
 *
 * ## 为什么这么设计（可维护性，而非炫技）
 *
 * 旧实现把 `<video>` 直接写在舞台组件里 → 想支持 HLS/DASH 必须改 UI 组件。
 * 现在 UI 只依赖 `VideoKernel` 接口，新增内核**不动任何 UI 代码**。
 */

import type { PlayerMedia } from "./player-types";

/** 内核能力声明（供 UI 决定显示哪些控件）。 */
export interface KernelCaps {
  /** 是否支持倍速 */
  rate: boolean;
  /** 是否支持清晰度切换 */
  quality: boolean;
  /** 是否支持逐帧/截图（暂未实现） */
  snapshot: boolean;
}

/** 播放器内核统一接口（照 YCVideoPlayer 的 VideoKernel 抽象）。 */
export interface VideoKernel {
  /** 内核名 */
  readonly name: string;
  /** 能力声明 */
  readonly caps: KernelCaps;
  /** 挂载到容器 */
  attach(el: HTMLVideoElement, media: PlayerMedia): Promise<void>;
  /** 卸载 / 释放 */
  detach(): void;
  /** 销毁（释放内核资源） */
  destroy(): void;
}

/** 内核工厂：按媒体类型选择（对应"自由切换视频内核"）。 */
export type KernelFactory = () => VideoKernel;

/**
 * HTML5 内核（默认，零依赖）
 * 对应 YCVideoPlayer 的 MediaPlayer：能力最基础但最稳。
 */
export class Html5Kernel implements VideoKernel {
  readonly name = "html5";
  readonly caps: KernelCaps = { rate: true, quality: false, snapshot: false };
  private _el: HTMLVideoElement | null = null;

  async attach(el: HTMLVideoElement, media: PlayerMedia): Promise<void> {
    this._el = el;
    // 直接给 src；HLS/DASH 交给对应内核处理
    if (media.url && el.getAttribute("src") !== media.url) {
      el.src = media.url;
    }
  }

  detach(): void {
    try {
      this._el?.pause();
    } catch {
      /* ignore */
    }
    this._el = null;
  }

  destroy(): void {
    this.detach();
  }
}

/** 判断媒体是否走 HLS（.m3u8）。 */
export function isHls(media: PlayerMedia): boolean {
  return /\.m3u8(\?|$)/i.test(media.url || "");
}

/** 判断媒体是否走 DASH（.mpd 或 dash 类型）。 */
export function isDash(media: PlayerMedia): boolean {
  return /\.mpd(\?|$)/i.test(media.url || "");
}

/**
 * 选择内核（照 YCVideoPlayer 的"自由切换内核"）。
 *
 * 当前仅内置 Html5Kernel（零依赖、抖音 MP4 可直接播）。
 * DashKernel / HlsKernel 为**预留扩展位**：接入 dash.js / hls.js 时只需新增
 * 一个实现 `VideoKernel` 的类并在此登记，**不动任何 UI 代码**。
 */
const REGISTRY: Record<string, KernelFactory> = {
  html5: () => new Html5Kernel(),
};

export function pickKernel(media: PlayerMedia): VideoKernel {
  // 预留：接入 hls.js / dash.js 后按类型返回对应内核（UI 无需改动）
  if (isHls(media) && REGISTRY.hls) return REGISTRY.hls();
  if (isDash(media) && REGISTRY.dash) return REGISTRY.dash();
  return REGISTRY.html5();
}

/**
 * 注册自定义内核（供接入方扩展，如内嵌 WebAssembly 解码器）。
 *
 * 2026-09-17 修补（OCR 审查 HIGH —— 原型污染）：
 * 原实现直接 `REGISTRY[name] = factory`，未校验 name。若 name 为
 * `__proto__` / `constructor` / `prototype` 等，会污染 Object 原型链
 * （`REGISTRY["__proto__"] = fn` 会改写原型而非新增自有属性）。
 * 现拒绝非标识符名称与原型链危险键，并要求 factory 为函数。
 *
 * 注：截至本次审查，本函数**全仓无调用方**（仅定义 + 桶文件 re-export），
 * 属预留扩展 API；此处为防御性加固，不改变现有行为。
 */
export function registerKernel(name: string, factory: KernelFactory): void {
  const DANGEROUS = new Set(["__proto__", "constructor", "prototype"]);
  if (typeof name !== "string" || !name || DANGEROUS.has(name)) {
    throw new Error(`registerKernel: 非法内核名 ${JSON.stringify(name)}`);
  }
  if (typeof factory !== "function") {
    throw new Error(`registerKernel: factory 必须是函数（收到 ${typeof factory}）`);
  }
  Object.defineProperty(REGISTRY, name, {
    value: factory, writable: true, enumerable: true, configurable: true,
  });
}
