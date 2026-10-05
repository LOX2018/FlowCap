# 图标目录占位

实际打包前需要放置以下文件（Tauri 默认图标集）：

- `32x32.png`
- `128x128.png`
- `128x128@2x.png`
- `icon.icns` (macOS)
- `icon.ico` (Windows)
- `Square*.png` / `StoreLogo.png` (Windows Store)

可用 `npm run tauri icon path/to/source.png` 从一张源图自动生成全部尺寸。
