# Codex Status

一个 macOS 菜单栏状态工具，用来显示 Codex 相关状态，方便快速观察当前任务状态。

[下载最新版](https://github.com/FanMiLi666/codex-status/releases/latest)

## 功能

- 在 macOS 菜单栏显示 Codex 当前是否有任务正在运行。
- 读取本机 Codex 日志数据库 `~/.codex/logs_2.sqlite`，根据任务开始、完成、取消、异常等事件更新状态。
- 维护轻量状态文件 `~/.codex/codex-status.json`，方便菜单栏应用快速读取。
- 内置 hook 脚本，可把 Codex 生命周期事件转换成状态记录。
- 自动忽略部分自动化心跳事件，避免把后台心跳误判成正在运行的任务。
- 自带状态图像资源，用于展示不同运行状态。

## 适用场景

- 同时开多个 Codex 任务时，想快速知道当前是否还有任务在跑。
- 让 Codex 状态常驻菜单栏，不需要频繁切换回应用查看。
- 需要一个轻量的本机状态提示工具，而不是完整监控面板。
- 想把 Codex 的运行状态沉淀成本地 JSON，方便后续扩展其他小工具。

## 应用信息

- 名称：Codex Status
- 版本：1.0
- 系统要求：macOS 13.0+
- 架构：Apple Silicon arm64

## 使用方式

1. 从 [Releases](https://github.com/FanMiLi666/codex-status/releases/latest) 下载 zip。
2. 将 `Codex Status.app` 拖到 `Applications` 文件夹。
3. 双击打开应用。

## 包含内容

应用包内包含状态监控相关 Python 脚本和图像资源。

## 说明

当前仓库保存的是已打包的 `.app` 应用包及其内置资源，不是完整源码工程。它依赖本机 Codex 日志目录，因此主要适合已经在本机使用 Codex 的环境。
