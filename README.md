# Oracle Forge 神谕台

Oracle Forge 神谕台是一款面向 AzerothCore 本地学习、单机测试、源码编译、服务端管理和 AI 运维辅助场景的网页控制台。

当前公开版本：

```text
v0.1.0-beta.1
公开内测版
```

本项目只提供控制台、自动化流程、日志观察、服务控制和 AI 辅助排障能力。AzerothCore 源码、地图数据、客户端及相关资源应由用户自行合法获取和生成。

---

## 功能概览

当前版本主要提供：

- AzerothCore 源码拉取辅助
- 数据库初始化辅助
- CMake / 编译 / 安装流程辅助
- 地图数据上传 / 下载流程辅助
- C++ 模块管理
- Lua 脚本上传
- 官方 ALE Lua Engine 快捷预设
- Authserver / Worldserver 启动、停止、重启控制
- worldserver 控制台命令发送
- 统一日志观察区
- AI 会话
- AI 生成执行方案
- 方案预检
- 确认执行白名单动作
- 任务中心
- 执行进度、日志摘要、失败续跑
- 登录器生成 / realmlist 修正辅助

---

## 适用场景

Oracle Forge 适用于：

- AzerothCore 本地学习
- AzerothCore 单机测试
- 服务端源码编译练习
- 私有测试服运维辅助
- 服务端日志分析
- AI 辅助排障
- 模块、脚本、配置管理

当前版本为 beta，不建议直接用于正式生产环境。

---

## 不包含的内容

本项目不包含，也不应通过本项目分发：

- 魔兽客户端
- DBC
- maps
- vmaps
- mmaps
- 模型、贴图、音频等游戏资源
- 预编译侵权整包
- 商业化游戏服务端资源包
- Blizzard / 暴雪娱乐拥有权利的任何游戏资产

用户需要自行确认其使用方式符合所在地法律法规、AzerothCore 许可协议及相关权利方要求。

---

## 快速安装

推荐在 Ubuntu 服务器上以 root 用户执行。

```bash
git clone https://github.com/YOUR_NAME/oracle-forge.git
cd oracle-forge
bash install.sh
```

安装完成后访问：

```text
http://服务器IP:7860
```

默认服务名：

```text
oracle-forge
```

查看服务状态：

```bash
systemctl status oracle-forge --no-pager
```

查看日志：

```bash
journalctl -u oracle-forge -n 120 --no-pager
```

---

## 升级

进入新版本仓库目录后执行：

```bash
bash upgrade.sh
```

升级脚本会尽量保留：

- `.env`
- `config.yaml`
- `workspace/`
- 运行日志
- 用户上传数据
- 用户已有配置

升级前会生成备份目录。

---

## 默认路径

默认安装路径：

```text
/opt/oracle-forge
```

默认工作区：

```text
/opt/oracle-forge/workspace
```

默认日志目录：

```text
/opt/oracle-forge/workspace/logs
```

默认端口：

```text
7860
```

---

## 常用命令

重启 Oracle Forge：

```bash
systemctl restart oracle-forge
```

查看状态：

```bash
systemctl status oracle-forge --no-pager
```

查看最近日志：

```bash
journalctl -u oracle-forge -n 120 --no-pager
```

查看端口：

```bash
ss -lntp | grep 7860 || true
```

## 登录器生成说明

Oracle Forge 的登录器生成能力只生成 Windows `.bat` 辅助脚本。

`.bat` 的作用：

- 写入客户端 `realmlist.wtf`
- 启动用户本地已有的 `Wow.exe`

它不包含：

- 客户端资源
- 游戏文件
- 模型
- 贴图
- 音频
- DBC
- maps / vmaps / mmaps

---

## Beta 说明

当前版本是公开内测版，可能存在：

- 安装环境差异
- Ubuntu 发行版差异
- Python 依赖差异
- AzerothCore 编译依赖差异
- 日志路径差异
- screen / systemd 状态识别差异
- AI 模型接口差异

欢迎通过 Issues 或 QQ 群反馈问题。

---

## 品牌信息

- 官网：https://www.gswxy.com
- 开发者：GSWXY / 耳语海岸
- QQ 群：938973736

---

## 免责声明

使用前请阅读：

```text
DISCLAIMER.md
```
